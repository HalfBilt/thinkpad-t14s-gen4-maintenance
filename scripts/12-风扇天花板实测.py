# -*- coding: utf-8 -*-
"""12-风扇天花板实测.py —— 测"EC 自带(自动)风扇控制"到底能给多少转速

怎么跑：双击同目录 `12-风扇天花板实测.bat`（会自己弹 UAC 提权）
结果：屏幕输出 + 同目录 `风扇天花板-时间戳.txt`

=== 2026-10-20 重写说明（旧版根本跑不出数据，两个 bug 叠加）===
  ① **写端口只发了 3 字节**。TSCtl 的 IOCTL_WRITE_PORT 要 5 字节
     `{u16 port; u16 pad; u8 value}`，旧版写的是 `pack('<H',port)+pack('<B',val)`。
     结果每一次 `pio_write` 都失败 → EC 四步事务在"写命令 0x80"那一步就抛异常
     → 屏幕上全是"读取失败: I/O 端口写 0x0066 失败"。
  ② **没有停看守服务**。就算能读，`CpuPLFixSvc` 每 5~15 秒会把 `0x2F` 改回它要的档，
     测到的根本不是"原生曲线"。
  现在改成:
     - 全程用 CpuPLFix 自己的 `ec_read_ok/ec_temp/ec_hfsp/ec_rpm`（带哨兵 + 跨进程互斥），
       不再自己实现 EC 协议；
     - 开测前自动停服务 + 禁用计划任务，测完恢复（`--keep-service` 可跳过）；
     - 开测前确认 `0x2F == 0x80`（交还 EC），不是就写回去。

做什么：
  1. 停服务 → 把 HFSP 交还 EC 自动(0x80)
  2. 空闲基线 3 次
  3. 全核满载 N 秒（默认 90），每 3 秒采一次（EC 温度 / HFSP / 转速 / 频率 / DTS / 功耗）
  4. 停载后回落 3 次
  5. 汇总：满载最高转速、温度≥80°C 时的转速、HFSP 取值集合
  6. 恢复服务

判读：
  - 自动模式 max ≈ 3861~3900 → EC 自动天花板就在 6 档附近（本机 6 档实测 3861），
    原生到不了 7 档(4388) / 满速(高负载 ~4900)。
  - 自动模式 max ≈ 4400 → 原生能到 7 档。
  - 自动模式 max ≈ 4900 → 固件自己就能拉满，问题只在触发条件。

依赖：已装 CpuPLFix（借它的 TSCtl 驱动 + EC 加固读法）。需要管理员。
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'T14scontrol', 'cpu')
BURN = os.path.join(HERE, '_负载发生器.py')
PY = sys.executable

# ★2026-10-20: 写 0x2F 的现在是风扇服务 T14sFanSvc(拆分后 CpuPLFix 不碰风扇),
#   所以要停的是它 —— 停错服务的话风扇服务每 5 秒就把档位改回去, 量到的是别人的档。
SVC = 'T14sFanSvc'
TASK = r'\CpuPLFix'
FAN_AUTO = 0x80
STEP = 3

# 本机实测标定（2026-10-20，scripts/风扇档位-20261020-073014.txt）
LEVEL_RPM = {0x01: 2463, 0x02: 2865, 0x03: 3186, 0x04: 3160,
             0x05: 3456, 0x06: 3861, 0x07: 4388, 0x40: 4900}


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
    except Exception as exc:
        return None


def stop_guard(say):
    r = run(['sc', 'stop', SVC])
    if r and r.returncode == 0:
        say('  已停服务 %s' % SVC)
    else:
        msg = ''
        if r:
            msg = ((r.stderr or '') + (r.stdout or '')).strip()[:80]
        say('  [!] 停服务失败（可能本来就没跑）%s' % ('：' + msg if msg else ''))
    r = run(['schtasks', '/change', '/tn', TASK, '/disable'])
    if r and r.returncode == 0:
        say('  已禁用计划任务 %s' % TASK)
    else:
        say('  [!] 禁用计划任务失败（可能不存在）')
    time.sleep(2.0)


def start_guard(say):
    run(['schtasks', '/change', '/tn', TASK, '/enable'])
    r = run(['sc', 'start', SVC])
    say('  已恢复服务与计划任务' if (r and r.returncode == 0) else '  [!] 启动服务失败，请手动 net start %s' % SVC)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('dur', nargs='?', type=int, default=90, help='满载秒数，默认 90')
    ap.add_argument('--keep-service', action='store_true', help='不停看守服务（会污染 HFSP，不推荐）')
    a = ap.parse_args()

    cpf = load_cpf()
    if not cpf.is_admin():
        sys.exit('[X] 需要管理员权限。请双击同目录的 12-风扇天花板实测.bat（它会自己弹 UAC）。')
    if not cpf.ensure_driver(verbose=False):
        sys.exit('[X] TSCtl 驱动打不开（CpuPLFix 没装好？见手册 01 §6）')
    if not os.path.isfile(BURN):
        sys.exit('[X] 同目录缺 _负载发生器.py')

    LOG = []
    OUT = os.path.join(HERE, '风扇天花板-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))

    def say(s=''):
        LOG.append(s)
        print(s, flush=True)

    say('=== 风扇天花板实测  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    try:
        fw = ''.join(chr(cpf.ec_read_ok(0xF0 + i) or 63) for i in range(8))
        say('EC 固件 = %s' % fw)
    except Exception:
        pass

    # ---- 端口通道自检（用加固读法，失败就早退）--------------------------
    t0 = cpf.ec_temp()
    if t0 is None:
        say('[X] EC 读不到温度 —— 驱动/端口不通，先跑 2-check 看驱动')
        open(OUT, 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
        return 1
    say('EC 温度读到 %d°C —— 通道正常' % t0)

    try:
        svc = run(['sc', 'query', SVC])
        running = bool(svc and 'RUNNING' in (svc.stdout or ''))
    except Exception:
        running = False
    if not a.keep_service:
        if running:
            say()
            say('=== 停看守服务（否则它会不停改 0x2F，测到的不是原生曲线）===')
            stop_guard(say)
        else:
            say('  （服务没在跑，直接测）')

    restored = False
    try:
        # ★2026-10-20: 停服务后 MSR 侧可能被系统/BIOS 改回默认 —— 实测 07:41 那次
        #   跑出 99°C、频率 3.0GHz 撑了 48 秒, 那是 **55W** 的表现, 不是 28W。
        #   这里按 config 重新锁一次功耗墙, 让"原生风扇在 28W 下"这个条件可控。
        try:
            cfg_now = cpf.load_cfg()
            cpf.ensure_profile(cfg_now)
            v = cpf.rdmsr(0x610)
            if v is not None:
                d = cpf.decode_pl(v)
                say('功耗墙(测试条件): MSR PL1=%.1fW PL2=%.1fW   config %.0f/%.0fW'
                    % (d['pl1_w'], d['pl2_w'],
                       float(cfg_now.get('pl1_w') or 0), float(cfg_now.get('pl2_w') or 0)))
        except Exception as exc:
            say('[!] 功耗墙对齐失败(结果可能不是 config 档): %s' % exc)

        # ---- 把风扇交还 EC 自动 -----------------------------------------
        cur = cpf.ec_hfsp()
        say()
        say('当前 HFSP = %s' % ('0x%02X' % cur if cur is not None else '读不到'))
        if cur is not None and cur != FAN_AUTO:
            if cpf.ec_write(0x2F, FAN_AUTO):
                time.sleep(2.0)
                back = cpf.ec_hfsp()
                say('已写 0x80 交还 EC 自动，回读 %s' % ('0x%02X' % back if back is not None else '失败'))
            else:
                say('[!] 写 0x80 失败 —— 下面测到的可能不是原生曲线')

        # ---- 1. 空闲基线 ------------------------------------------------
        say()
        say('=== 空闲基线 ===')
        for _ in range(3):
            t = cpf.ec_temp(); f = cpf.ec_hfsp(); r = cpf.ec_rpm()
            say('  EC温度=%s°C  HFSP=%s  RPM=%s'
                % ('%3d' % t if t is not None else ' --',
                   '0x%02X' % f if f is not None else '--',
                   '%4d' % r if r is not None else '----'))
            time.sleep(2)

        # ---- 2. 满载采样 ------------------------------------------------
        n_cpu = os.cpu_count() or 8
        say()
        say('=== 满载 %d 秒（%d 线程，全程 EC 自动、不写风扇）===' % (a.dur, n_cpu))
        say('%5s %8s %7s %7s %6s %7s' % ('t(s)', '频率', 'EC°C', 'DTS°C', 'HFSP', 'RPM'))
        procs = [subprocess.Popen([PY, BURN, str(a.dur + 15)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for _ in range(n_cpu)]
        time.sleep(1.5)

        rows = []
        t_start = time.time()
        n = 0
        try:
            while time.time() - t_start < a.dur + 1:
                time.sleep(STEP)
                n += 1
                et = cpf.ec_temp()
                f = cpf.ec_hfsp()
                rpm = cpf.ec_rpm()
                dts = None
                try:
                    tv = cpf.cpf_read_thermal()
                    dts = tv['temp'] if tv else None
                except Exception:
                    pass
                ratio = None
                try:
                    v = cpf.rdmsr(cpf.MSR_PERF_STATUS)
                    ratio = ((v or 0) >> 8) & 0xFF
                except Exception:
                    pass
                rows.append((n * STEP, ratio, et, dts, f, rpm))
                say('%5d %7s %7s %7s %6s %7s'
                    % (n * STEP,
                       '%.2fGHz' % (ratio / 10.0) if ratio else '--',
                       '%d' % et if et is not None else '--',
                       '%d' % dts if dts is not None else '--',
                       '0x%02X' % f if f is not None else '--',
                       '%d' % rpm if rpm is not None else '----'))
        finally:
            for pr in procs:
                try:
                    pr.kill(); pr.wait()
                except Exception:
                    pass

        # ---- 3. 停载回落 ------------------------------------------------
        say()
        say('=== 停载后 ===')
        for _ in range(3):
            time.sleep(2)
            t = cpf.ec_temp(); f = cpf.ec_hfsp(); r = cpf.ec_rpm()
            say('  EC温度=%s°C  HFSP=%s  RPM=%s'
                % ('%3d' % t if t is not None else ' --',
                   '0x%02X' % f if f is not None else '--',
                   '%4d' % r if r is not None else '----'))

        # ---- 4. 汇总 ----------------------------------------------------
        rpms = [r[5] for r in rows if r[5]]
        ets = [r[2] for r in rows if r[2] is not None]
        hfsp = sorted(set(r[4] for r in rows if r[4] is not None))
        say()
        say('=== 汇总（EC 自动模式）===')
        if ets:
            say('  满载 EC 最高温度 : %d°C' % max(ets))
        if hfsp:
            say('  满载 HFSP 取值   : %s' % ' '.join('0x%02X' % v for v in hfsp))
        if rpms:
            say('  满载最高转速     : %d RPM' % max(rpms))
            hot = [r[5] for r in rows if r[5] and r[2] is not None and r[2] >= 80]
            if hot:
                say('  EC≥80°C 时最高转速: %d RPM' % max(hot))
        else:
            say('  [X] 一个转速都没采到 —— EC 读取全失败，看上面的报错')

        say()
        say('=== 结论 ===')
        if not rpms:
            say('  没数据，没法判读。先跑 2-check 确认 EC 能读。')
        else:
            mx = max(rpms)
            lv, lv_rpm = min(LEVEL_RPM.items(), key=lambda kv: abs(kv[1] - mx))
            if abs(lv_rpm - mx) > 500:
                say('  原生最高 %d RPM，落在已知档位之外' % mx)
            else:
                say('  原生最高 %d RPM ≈ 档位 0x%02X（该档标定 %d RPM）' % (mx, lv, lv_rpm))
            if mx < 4000:
                say('  → 原生天花板在 6 档附近：到不了 7 档(4388)/满速(~4900)，')
                say('    高负载时只能靠降频，风扇不会更响。')
            elif mx < 4600:
                say('  → 原生能到 7 档(4388)：比 6 档多 ~530 RPM。')
            else:
                say('  → 原生能接近真满速：问题不在天花板，在触发条件。')
    finally:
        if not a.keep_service:
            say()
            say('=== 收尾 ===')
            try:
                if cpf.ec_hfsp() != FAN_AUTO:
                    cpf.ec_write(0x2F, FAN_AUTO)
            except Exception:
                pass
            start_guard(say)
            restored = True

    open(OUT, 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
    print()
    print('报告已写入: %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
