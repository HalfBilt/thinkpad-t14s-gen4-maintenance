# -*- coding: utf-8 -*-
"""16-风扇档位扩展探测.py —— 验证 HFSP(0x2F) 在 0x08~0x3F 是否还有真实档位

背景：手册只标定过 0x01~0x07 / 0x40 / 0x80。用户反馈 0x08~0x3F 应该也存在。
      本脚本无负载逐点写入候选值，看 EC 回读值 + 实际转速是否变化。

怎么跑：双击同目录 `16-风扇档位扩展探测.bat`（自动弹 UAC）
结果：屏幕输出 + 同目录 `风扇档位扩展-时间戳.txt`

安全措施：
  - 开测前要求 EC 温度 <= 75°C；
  - 每点写入后 14 秒内多次采样，取后 3 次中位数；
  - EC 温度 >= 82°C 或转速 > 5000 立即写 0x40 满速并终止；
  - 无论成功失败，收尾都写回 0x80(EC 自动) 并启动 T14sFanSvc。

前提：已装 CpuPLFix（借 TSCtl 驱动 + 加固 EC 读写）。需要管理员。
"""
import importlib.util
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'T14scontrol', 'cpu')
SVC = 'T14sFanSvc'
EC_FAN = 0x2F
EC_RPM_LO = 0x84
EC_RPM_HI = 0x85
FAN_AUTO = 0x80
FAN_MAX = 0x40
WAIT = 14.0
SAMPLE = 3.5
TEMP_ABORT = 82.0
RPM_ABORT = 5000

CANDIDATES = [0x00, 0x07, 0x08, 0x0A, 0x0C, 0x10, 0x14, 0x18, 0x1C,
              0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C, 0x3F, 0x40]


def load_cpf():
    p = os.path.join(BASE, 'CpuPLFix.py')
    if not os.path.isfile(p):
        sys.exit('[X] 找不到 CpuPLFix（%s）—— 先装它，见手册 01' % BASE)
    spec = importlib.util.spec_from_file_location('cpf', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None


def fan_rpm(cpf):
    vals = []
    for _ in range(3):
        lo = cpf.ec_read_ok(EC_RPM_LO, tries=3)
        hi = cpf.ec_read_ok(EC_RPM_HI, tries=3)
        if lo is not None and hi is not None:
            vals.append((hi << 8) | lo)
        time.sleep(0.15)
    return int(statistics.median(vals)) if vals else None


def hfsp(cpf):
    v = cpf.ec_read_ok(EC_FAN, tries=3)
    return v


def stop_svc(say):
    r = run(['sc', 'stop', SVC])
    if not r or r.returncode != 0:
        say('  [!] sc stop 失败（可能本来就没跑）')
        return
    for _ in range(20):
        q = run(['sc', 'query', SVC])
        if q and 'STOPPED' in (q.stdout or ''):
            say('  已停服务 %s' % SVC)
            return
        time.sleep(0.5)
    say('  [!] 服务 10 秒内未变成 STOPPED，继续测试有被改档风险')


def start_svc(say):
    r = run(['sc', 'start', SVC])
    say('  已启动服务 %s' % SVC if (r and r.returncode == 0)
        else '  [!] 启动服务失败，请手动 net start %s' % SVC)


def main():
    cpf = load_cpf()
    if not cpf.is_admin():
        sys.exit('[X] 需要管理员权限。请双击同目录的 16-风扇档位扩展探测.bat。')
    if not cpf.ensure_driver(verbose=False):
        sys.exit('[X] TSCtl 驱动打不开（CpuPLFix 没装好？见手册 01 §6）')

    LOG = []
    OUT = os.path.join(HERE, '风扇档位扩展-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))

    def say(s=''):
        LOG.append(s)
        print(s, flush=True)

    say('=== HFSP 扩展档位探测  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    try:
        fw = ''.join(chr(cpf.ec_read_ok(0xF0 + i) or 63) for i in range(8))
        say('EC 固件 = %s' % fw)
    except Exception:
        pass

    t0 = cpf.ec_temp()
    if t0 is None:
        say('[X] EC 读不到温度，先跑 2-check 看驱动')
        return 1
    say('起始 EC 温度 = %d°C' % t0)
    if t0 >= 75:
        say('[X] 起始温度过高，先让机器凉下来再测（无负载即可）')
        return 1

    say()
    say('=== 停风扇服务（否则每 5 秒会把 0x2F 改回去）===')
    stop_svc(say)
    time.sleep(1.0)

    rows = []
    aborted = False
    try:
        cur = hfsp(cpf)
        say('停服务后 HFSP = %s，RPM = %s'
            % ('0x%02X' % cur if cur is not None else '--',
               fan_rpm(cpf)))
        say()
        say('=== 逐点写入（每点等 %.0f 秒，无负载）===' % WAIT)
        say('%6s %8s %8s %8s %7s' % ('写入', '回读', 'RPM中位', 'RPM范围', 'EC°C'))

        for lv in CANDIDATES:
            okw = cpf.ec_write(EC_FAN, lv)
            time.sleep(0.3)
            back = hfsp(cpf)
            if not okw:
                say('  0x%02X   写入失败' % lv)
                rows.append((lv, None, None, None, None))
                continue

            t_end = time.time() + WAIT
            rpms = []
            temps = []
            while time.time() < t_end:
                time.sleep(SAMPLE)
                r = fan_rpm(cpf)
                t = cpf.ec_temp()
                if r is not None:
                    rpms.append(r)
                if t is not None:
                    temps.append(t)
                if t is not None and t >= TEMP_ABORT:
                    break
                if r is not None and r > RPM_ABORT:
                    break

            tail = rpms[-3:] if len(rpms) >= 3 else rpms
            med = int(statistics.median(tail)) if tail else None
            rng = '%d~%d' % (min(tail), max(tail)) if tail else '--'
            et = temps[-1] if temps else None
            rows.append((lv, back, med, rng, et))
            say('  0x%02X   %8s %8s %8s %7s'
                % (lv,
                   '0x%02X' % back if back is not None else '--',
                   '%d' % med if med is not None else '----',
                   rng,
                   '%d' % et if et is not None else '--'))

            if et is not None and et >= TEMP_ABORT:
                say('  [!] EC 温度 %d°C >= %.0f°C，写满速并终止' % (et, TEMP_ABORT))
                aborted = True
                break
            if med is not None and med > RPM_ABORT:
                say('  [!] 转速 %d > %d RPM，写满速并终止' % (med, RPM_ABORT))
                aborted = True
                break
    finally:
        say()
        say('=== 收尾 ===')
        try:
            if aborted or not cpf.ec_write(EC_FAN, FAN_AUTO):
                cpf.ec_write(EC_FAN, FAN_MAX)
                time.sleep(2)
                cpf.ec_write(EC_FAN, FAN_AUTO)
        except Exception:
            pass
        start_svc(say)

    # ---- 汇总 ----------------------------------------------------------
    say()
    say('=== 汇总 ===')
    known = {0x01: 2471, 0x02: 2858, 0x03: 3181, 0x04: 3186, 0x05: 3167,
             0x06: 3456, 0x07: 3858, 0x40: 4807}
    valid = []
    for lv, back, med, rng, et in rows:
        if med is None:
            continue
        if lv == 0x00:
            note = '停转/最低'
        elif back != lv:
            note = 'EC 改成 0x%02X（该值不被接受）' % back if back is not None else '回读失败'
        elif lv in known and abs(med - known[lv]) <= 250:
            note = '与已知档位一致（%d）' % known[lv]
        else:
            note = '★ 新行为'
            valid.append((lv, med))
        say('  0x%02X  回读=%s  RPM=%d  %s'
            % (lv, '0x%02X' % back if back is not None else '--', med, note))

    say()
    if valid:
        say('  发现 0x01~0x40 之外的新转速点：')
        for lv, med in valid:
            say('    0x%02X -> %d RPM' % (lv, med))
        say('  → 可以把其中合适的一个插进曲线（要先在 28W 负载下复测稳定性）')
    else:
        say('  0x08~0x3F 没有产生新转速：写入要么被 EC 改成别的值，')
        say('  要么转速与 0x07/0x40 相同 —— 这台上这些值不是独立档位。')

    open(OUT, 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
    print()
    print('报告已写入: %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
