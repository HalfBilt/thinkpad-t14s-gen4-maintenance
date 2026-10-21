# -*- coding: utf-8 -*-
"""17-风扇8档28W实测.py —— 0x08 到底是不是一个真正独立的中间档

起因：2026-10-21 扩展探测在 14 秒/点的节奏下测得 0x07=3592(仍在爬升)、
      0x08=4392。但风扇从 0x00 停下来后要 20~30 秒才爬到位，这个结果不能证明
      0x08 是独立档位。而且服务日志里 0x07 在 EC 74°C 时读到过 4392 RPM ——
      转速本身会随温度上浮，必须做**同温度、充分沉降**的 A/B 对照。

本脚本分两步：
  1) 无负载 A/B：7 档 -> 8 档 -> 7 档 -> 8 档，每段 45 秒，取后 20 秒中位数。
     固件若改写 0x2F 会立刻重写目标，并在分析里剔除受影响的样本。
  2) 只有第 1 步确认 8 档比 7 档高 >=150 RPM（真正独立），才继续跑 28W 满载：
     8 档 75 秒 + 满速 40 秒，看它能不能在 PL1=28W 下稳住温度。

怎么跑：双击同目录 `17-风扇8档28W实测.bat`（自动弹 UAC）
结果：屏幕输出 + 同目录 `风扇8档28W-时间戳.txt`（中途退出也会留下已写内容）

安全：DTS>=97°C 或 EC>=90°C 连续两拍 -> 写满速并结束负载；
      无论成功失败，收尾都写 0x80 并启动 T14sFanSvc。
前提：已装 CpuPLFix（借 TSCtl 驱动 + 加固 EC 读写）。需要管理员。
"""
import ctypes
import importlib.util
import os
import statistics
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'T14scontrol', 'cpu')
BURN = os.path.join(HERE, '_负载发生器.py')
PY = sys.executable
SVC = 'T14sFanSvc'
EC_FAN = 0x2F
EC_RPM_LO = 0x84
EC_RPM_HI = 0x85
FAN_AUTO = 0x80
FAN_MAX = 0x40
LEVEL7 = 0x07
LEVEL8 = 0x08
FAST = {0x07: '7档', 0x08: '8档', 0x40: '满速'}
AB_PHASES = [(LEVEL7, 45), (LEVEL8, 45), (LEVEL7, 45), (LEVEL8, 45)]
LOAD_PHASES = [(LEVEL8, 75), (FAN_MAX, 40)]
SAMPLE = 3.0

DTS_ABORT = 97.0
EC_ABORT = 90.0
RPM_ABORT = 5000
AB_MIN_DIFF = 150


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


def stop_svc(say):
    r = run(['sc', 'stop', SVC])
    if not r or r.returncode != 0:
        say('  [!] sc stop 失败（可能本来就没跑）')
        return False
    for _ in range(30):
        q = run(['sc', 'query', SVC])
        if q and 'STOPPED' in (q.stdout or ''):
            say('  已停服务 %s' % SVC)
            return True
        time.sleep(0.5)
    say('  [!] 服务 15 秒内未 STOPPED，放弃测试（避免它中途改档）')
    return False


def start_svc(say):
    r = run(['sc', 'start', SVC])
    say('  已启动服务 %s' % SVC if (r and r.returncode == 0)
        else '  [!] 启动服务失败，请手动 net start %s' % SVC)


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
    return cpf.ec_read_ok(EC_FAN, tries=3)


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def main():
    cpf = load_cpf()
    if not cpf.is_admin():
        sys.exit('[X] 需要管理员权限。请双击同目录的 17-风扇8档28W实测.bat。')
    if not cpf.ensure_driver(verbose=False):
        sys.exit('[X] TSCtl 驱动打不开（CpuPLFix 没装好？见手册 01 §6）')
    if not os.path.isfile(BURN):
        sys.exit('[X] 同目录缺 _负载发生器.py')

    LOG = []
    OUT = os.path.join(HERE, '风扇8档28W-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))

    def say(s=''):
        LOG.append(s)
        print(s, flush=True)
        try:
            open(OUT, 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
        except Exception:
            pass

    k32 = ctypes.windll.kernel32

    def pin(cpu=0):
        k32.SetThreadAffinityMask(k32.GetCurrentThread(), 1 << cpu)

    def dts_now():
        pin(0)
        v = cpf.rdmsr(0x1B1) or 0
        return TJMAX - ((v >> 16) & 0x7F)

    _ta = cpf.rdmsr(0x1A2)
    TJMAX = ((_ta >> 16) & 0xFF) if _ta else 100
    if not (50 <= TJMAX <= 120):
        TJMAX = 100
    _u = cpf.rdmsr(0x606)
    E_UNIT = 1.0 / (1 << ((_u >> 8) & 0x1F)) if _u else 6.104e-05
    NCPU = os.cpu_count() or 16

    def pl_limits():
        v = cpf.rdmsr(0x610)
        if v is None:
            return '-', '-'
        # 0x606 bits3:0 = 功率单位, bits12:8 = 能量单位 —— 不能混用
        pwr = 1.0 / (1 << (_u & 0x1F)) if _u is not None else 1.0 / 8
        return '%.0fW' % ((v & 0x7FFF) * pwr), '%.0fW' % (((v >> 32) & 0x7FFF) * pwr)

    say('=== 风扇 7 档 vs 8 档判定  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say('TjMax=%d°C   PL1/PL2=%s/%s   16 线程负载发生器: %s'
        % (TJMAX, pl_limits()[0], pl_limits()[1], BURN))

    t0 = cpf.ec_temp()
    say('起始 EC 温度 = %s°C' % t0)
    if t0 is None:
        say('[X] EC 读不到温度，先跑 2-check 看驱动')
        return 1

    say()
    say('=== 停风扇服务 ===')
    if not stop_svc(say):
        return 1
    time.sleep(1.0)
    say('停服务后 HFSP=%s RPM=%s'
        % ('0x%02X' % (hfsp(cpf) or 0), fan_rpm(cpf)))

    burners = []
    ab_rows = []
    load_rows = []
    aborted = False
    distinct = False
    try:
        say()
        say('=== 第 1 步: 无负载 A/B（每段 45 秒，后 20 秒统计）===')
        for pi, (lv, dur) in enumerate(AB_PHASES):
            say()
            say('--- 固定 0x%02X (%s)，%d 秒 ---' % (lv, FAST.get(lv, '?'), dur))
            say('%5s %8s %7s %7s %8s' % ('t(s)', 'HFSP', 'RPM', 'EC°C', 'DTS°C'))
            cpf.ec_write(EC_FAN, lv)
            override_t = 0.0
            t_start = time.time()
            while time.time() - t_start < dur:
                time.sleep(SAMPLE)
                hf = hfsp(cpf)
                if hf is not None and hf != lv:
                    say('  [!] 固件把 HFSP 改成 0x%02X，重写 0x%02X' % (hf, lv))
                    cpf.ec_write(EC_FAN, lv)
                    override_t = time.time()
                rp = fan_rpm(cpf)
                et = cpf.ec_temp()
                dt = dts_now()
                valid = (time.time() - override_t) > 8.0
                ab_rows.append((pi, round(time.time() - t_start, 1), lv, hf, rp, et, dt, valid))
                say('%5.1f %8s %7s %7s %8s'
                    % (time.time() - t_start,
                       '0x%02X' % hf if hf is not None else '--',
                       rp if rp is not None else '--',
                       et if et is not None else '--', dt))
                if et is not None and et >= EC_ABORT:
                    say('  [!] EC 过高，写满速并终止')
                    cpf.ec_write(EC_FAN, FAN_MAX)
                    aborted = True
                    break
                if rp is not None and rp > RPM_ABORT:
                    say('  [!] RPM 异常，写满速并终止')
                    cpf.ec_write(EC_FAN, FAN_MAX)
                    aborted = True
                    break
            if aborted:
                break

        say()
        say('=== A/B 汇总（每段取后 20 秒、且不在固件改写后 8 秒内的样本）===')
        say('%4s %6s %8s %8s %8s %8s'
            % ('段', '档位', 'RPM中位', 'RPM范围', 'EC中位', 'DTS中位'))
        stats = {}
        seen = {}
        for pi, (lv, dur) in enumerate(AB_PHASES):
            occ = seen.get(lv, 0)
            seen[lv] = occ + 1
            seg = [r for r in ab_rows if r[0] == pi and r[1] > dur - 20 and r[7]]
            if not seg:
                say('%4d %6s  无有效样本' % (pi + 1, FAST.get(lv, '?')))
                continue
            rps = [r[4] for r in seg if r[4] is not None]
            ecs = [r[5] for r in seg if r[5] is not None]
            dts = [r[6] for r in seg if r[6] is not None]
            stats[(lv, occ)] = (med(rps), med(ecs), med(dts))
            say('%4d %6s %8s %8s %8s %8s'
                % (pi + 1, FAST.get(lv, '?'),
                   '%d' % med(rps) if rps else '--',
                   '%d~%d' % (min(rps), max(rps)) if rps else '--',
                   '%.0f' % med(ecs) if ecs else '--',
                   '%.0f' % med(dts) if dts else '--'))

        r7 = stats.get((LEVEL7, 1), (None,))[0]
        r8 = stats.get((LEVEL8, 1), (None,))[0]
        if r7 is not None and r8 is not None:
            diff = r8 - r7
            say()
            say('判定: 8 档比 7 档高 %+d RPM（第二组: %d vs %d）' % (diff, r8, r7))
            distinct = diff >= AB_MIN_DIFF
            if distinct:
                say('  → 0x08 是独立档位，继续做 28W 满载测试')
            else:
                say('  → 差值不足 %d RPM，0x08 在真实温度下与 7 档没有可用差别' % AB_MIN_DIFF)
                say('     之前 14 秒探测得到的 4392 很可能只是 7 档还没爬完。')
        else:
            say()
            say('判定: 样本不足，跳过满载测试')
            aborted = True

        if distinct and not aborted:
            say()
            say('=== 第 2 步: 28W 满载（8 档 75 秒 -> 满速 40 秒）===')
            say('起测 EC=%s°C DTS=%d°C' % (cpf.ec_temp(), dts_now()))
            burners = [subprocess.Popen([PY, BURN, str(sum(d for _l, d in LOAD_PHASES) + 25)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                       for _ in range(NCPU)]
            time.sleep(1.0)
            e_prev = cpf.rdmsr(0x611)
            t_prev = time.time()
            hi_n = 0
            for lv, dur in LOAD_PHASES:
                say()
                say('--- 固定 0x%02X (%s)，%d 秒 ---' % (lv, FAST.get(lv, '?'), dur))
                say('%5s %8s %7s %7s %8s %8s'
                    % ('t(s)', 'HFSP', 'RPM', 'EC°C', 'DTS°C', 'PKG(W)'))
                if not cpf.ec_write(EC_FAN, lv):
                    say('[X] 写 0x%02X 失败，终止' % lv)
                    aborted = True
                    break
                t_start = time.time()
                while time.time() - t_start < dur:
                    time.sleep(SAMPLE)
                    e = cpf.rdmsr(0x611)
                    tn = time.time()
                    pkg = None
                    if e is not None and e_prev is not None and 0 < tn - t_prev <= 10:
                        pkg = ((e - e_prev) & 0xFFFFFFFF) * E_UNIT / (tn - t_prev)
                    e_prev, t_prev = e, tn
                    hf = hfsp(cpf)
                    if hf is not None and hf != lv:
                        say('  [!] 固件把 HFSP 改成 0x%02X，重写 0x%02X' % (hf, lv))
                        cpf.ec_write(EC_FAN, lv)
                    et = cpf.ec_temp()
                    dt = dts_now()
                    rp = fan_rpm(cpf)
                    load_rows.append((round(time.time() - t_start, 1), lv, rp, et, dt, pkg))
                    say('%5.1f %8s %7s %7s %8s %8s'
                        % (time.time() - t_start,
                           '0x%02X' % hf if hf is not None else '--',
                           rp if rp is not None else '--',
                           et if et is not None else '--', dt,
                           '%.1f' % pkg if pkg is not None else '--'))
                    if dt >= DTS_ABORT or (et is not None and et >= EC_ABORT):
                        hi_n += 1
                    else:
                        hi_n = 0
                    if hi_n >= 2:
                        say('  [!] DTS/EC 过高，写满速并结束')
                        cpf.ec_write(EC_FAN, FAN_MAX)
                        aborted = True
                        break
                if aborted:
                    break
    finally:
        for pr in burners:
            try:
                pr.kill()
                pr.wait(timeout=5)
            except Exception:
                pass
        say()
        say('=== 收尾 ===')
        try:
            cpf.ec_write(EC_FAN, FAN_AUTO)
        except Exception:
            pass
        start_svc(say)

    if load_rows:
        say()
        say('=== 28W 汇总（每段取后 30 秒）===')
        say('%6s %6s %8s %8s %8s %8s %8s'
            % ('档位', '样本', 'RPM中位', 'RPM范围', 'EC末段', 'DTS中位', 'PKG中位'))
        for lv, dur in LOAD_PHASES:
            seg = [r for r in load_rows if r[1] == lv]
            tail = seg[-10:] if len(seg) >= 10 else seg
            if not tail:
                continue
            rps = [r[2] for r in tail if r[2] is not None]
            ecs = [r[3] for r in tail if r[3] is not None]
            dts = [r[4] for r in tail if r[4] is not None]
            pk = [r[5] for r in tail if r[5] is not None]
            say('%6s %6d %8s %8s %8s %8s %8s'
                % (FAST.get(lv, '0x%02X' % lv), len(tail),
                   '%d' % med(rps) if rps else '--',
                   '%d~%d' % (min(rps), max(rps)) if rps else '--',
                   '%d' % ecs[-1] if ecs else '--',
                   '%.1f' % med(dts) if dts else '--',
                   '%.1f' % med(pk) if pk else '--'))
        seg8 = [r[4] for r in load_rows if r[1] == LEVEL8]
        if len(seg8) >= 20:
            first = statistics.median(seg8[:10])
            last = statistics.median(seg8[-10:])
            say()
            say('8 档趋势: 前 30 秒 DTS 中位 %.1f -> 后 30 秒 %.1f (%+.1f°C)'
                % (first, last, last - first))
            if last - first > 1.0:
                say('  → 仍在爬升，75 秒没稳；持续负载只会更高')
            else:
                say('  → 已基本走平，可视为稳态')

    say()
    say('报告已写入: %s' % OUT)
    return 1 if aborted else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        HERE2 = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(HERE2, '风扇8档28W-crash-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
        open(p, 'w', encoding='utf-8').write(traceback.format_exc())
        print('CRASH: %s' % p, flush=True)
        raise
