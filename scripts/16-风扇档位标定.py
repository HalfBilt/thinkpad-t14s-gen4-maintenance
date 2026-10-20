# -*- coding: utf-8 -*-
"""风扇档位标定 —— 测出本机 HFSP(0x2F) 每个档位的**真实转速**和**降温能力**。

2026-10-20 改版（风扇工具拆分之后）
-----------------------------------
* 旧版加载的是 `CpuPLFix.py` 里的 TSCtl 驱动；拆分后 CpuPLFix 只管 CPU，
  风扇侧换成了 PawnIO + `fan_control.py`，旧版跑起来会在 `cpf.set_fan` 处 AttributeError。
  本版改成 `import fan_control`，直接用它的 PawnIO 层读写 EC。
* **关键是先停掉 FanControl 托盘**：它每 2 秒决策一次，温度一进档就会把
  0x2F 改成它要的档 —— 不停掉的话，量到的全是"别人的档"（这就是"抢着写"）。
  测完自动把托盘拉回来（和原来一样靠 finally，Ctrl+C 也会恢复）。
* 顺带把 CPU 看守服务 `CpuPLFixSvc` 也停掉：它每 15 秒读 EC，热了还会改
  PL1/PL2，会让阶段二的升温曲线对不齐；测完同样恢复。

三个阶段
--------
1. **测转速**（不带负载）：逐档写 `0x2F = N`，等风扇爬稳，连读 5 次取中位数；
2. **测降温能力**（1 线程 12 秒）：只比 `0x40 / 0x07 / 0x06`，看升温幅度 ΔT；
3. **高温重标定**（加 `hot` 参数，可选）：带 2 线程把温度堆到 80°C 上下，
   在**热态**下再读 3/4/5/6 档的转速 —— 冷机实测 3/4/5 挤在 ~3180 分不开，
   热机到底分不分得开，只能这么测。

安全
----
* 阶段一完全不带负载，只是把风扇固定在某个档，机器不会积热；
* 阶段二只有 1 线程、12 秒，档位之间先降温 25 秒；温度超过 88°C 就**跳过**剩下的；
* 阶段三有温度护栏 88°C，超了立刻停负载并收尾；
* `finally` 里无论如何都会**恢复原档位 + 把托盘和服务拉起来**（哪怕中途 Ctrl+C）。

用法
----
    双击同目录的 `16-风扇档位标定.bat`      —— 阶段一 + 阶段二（约 4 分钟）
    `16-风扇档位标定.bat rpm-only`          —— 只测转速
    `16-风扇档位标定.bat hot`               —— 再加快温重标定（再多约 5 分钟）
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APPDATA = os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming'
FAN_DIR = os.path.join(APPDATA, 'T14scontrol', 'fan')
FAN_SRC = os.path.join(FAN_DIR, 'src', 'fan_control.py')
PYW = os.path.join(FAN_DIR, 'runtime', 'pythonw.exe')
SVC = 'CpuPLFixSvc'
BURN = os.path.join(HERE, '_负载发生器.py')

LEVELS = [0x40, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01]    # 从快到慢
COOL_LEVELS = [0x40, 0x07, 0x06]      # 阶段二只比这三档（曲线用得上的）
HOT_LEVELS = [0x03, 0x04, 0x05, 0x06]  # 阶段三：问"3/4/5 热机分不分得开"
AUTO = 0x80

# ★2026-10-20 修: 原来 12 秒 —— 而手册自己写着"风扇 2610->4700 物理上要 10~20 秒",
#   于是 0x40 只爬到 4160 就被记成"满速 4160", 直接得出"0x40 不是满速"的错误结论。
#   日志里 0x40 持续一段时间后能到 4807~4946。改成 25 秒, 给满速档足够爬升时间。
SETTLE_S = 25.0        # 写完档位后等风扇爬稳的秒数（满速档要 20s 量级）
READS = 5              # 每档连读几次转速
LOAD_S = 12.0          # 阶段二的负载秒数
LOAD_THREADS = 1       # 阶段二只用 1 个线程（多线程=功耗受限，测不出风扇差别）
COOLDOWN_S = 25.0      # 阶段二每档之间的降温时间
TEMP_SKIP = 88         # 阶段二温度超过它就跳过剩下的（不中止整个测试）

HOT_THREADS = 2        # 阶段三：把温度堆上去用
HOT_S = 45.0           # 每档负载持续秒数（含升温）
HOT_TEMP_MAX = 88      # 阶段三的温度护栏
HOT_COOLDOWN_S = 30.0  # 每档之间降温
HOT_TARGET = 76        # 温度到这儿就算"热透了"，可以采样

RPM_ONLY = 'rpm-only' in sys.argv[1:]
HOT_MODE = 'hot' in sys.argv[1:]

LOG = []
OUT = None
fc = None          # fan_control 模块
LV_NAME = {0: '停转', 1: '1 档', 2: '2 档', 3: '3 档', 4: '4 档',
           5: '5 档', 6: '6 档', 7: '7 档', 0x40: '满速', 0x80: 'EC 自动'}


def say(s=''):
    LOG.append(s)
    print(s, flush=True)


def fan_name(lv):
    if lv is None:
        return '读取失败'
    return LV_NAME.get(lv, '0x%02X' % lv)


# --------------------------------------------------------------------------
# 载入风扇工具（只借它的 EC 层，不启动托盘）
# --------------------------------------------------------------------------
def load_fan():
    if not os.path.isfile(FAN_SRC):
        sys.exit('[X] 找不到风扇工具（%s）—— 先装 T14scontrol\\fan' % FAN_SRC)
    spec = importlib.util.spec_from_file_location('fanctl', FAN_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 有 __main__ 保护，不会启动托盘
    return mod


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def sh(cmd, timeout=30):
    """跑一条命令，返回 (是否成功, 输出)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding='mbcs', errors='replace')
        return r.returncode == 0, (r.stdout or '') + (r.stderr or '')
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# 停 / 起 两个看守
# --------------------------------------------------------------------------
def find_fan_pids():
    """找出所有命令行里带 fan_control.py 的进程（托盘就是它）。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
          "| Where-Object { $_.CommandLine -like '*fan_control.py*' } "
          "| ForEach-Object { $_.ProcessId }")
    ok, out = sh(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps])
    pids = []
    if ok:
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    return pids


def stop_fan():
    """停掉 FanControl 托盘。返回它本来是否在跑。"""
    pids = find_fan_pids()
    if not pids:
        say('  风扇控制没在跑 —— 不用停')
        return False
    say('  停风扇控制托盘 PID %s ...' % ', '.join(str(p) for p in pids))
    for p in pids:
        sh(['taskkill', '/PID', str(p), '/F'])
    for _ in range(10):
        time.sleep(0.5)
        if not find_fan_pids():
            return True
    say('  !! 还有 %s 没停掉，量到的档位可能被它改回去' % find_fan_pids())
    return True


def start_fan():
    if not os.path.isfile(PYW):
        say('  !! 找不到 %s —— 请手动双击 fan\\1-启动风扇控制.bat' % PYW)
        return
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    try:
        subprocess.Popen([PYW, FAN_SRC], cwd=os.path.dirname(FAN_SRC),
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
    except Exception as exc:
        say('  !! 拉起风扇控制失败: %s —— 请手动双击 fan\\1-启动风扇控制.bat' % exc)
        return
    time.sleep(2.5)
    pids = find_fan_pids()
    say('  风扇控制已重新拉起（PID %s）' % ', '.join(str(p) for p in pids)
        if pids else '  !! 没看到风扇控制进程，请手动双击 fan\\1-启动风扇控制.bat')


def svc_running():
    ok, out = sh(['sc', 'query', SVC])
    if not ok:
        return None
    up = out.upper()
    if 'RUNNING' in up:
        return True
    if 'STOPPED' in up:
        return False
    return None


def stop_cpu_svc():
    st = svc_running()
    if st is None:
        say('  没找到 %s（没装或没权限）—— 跳过' % SVC)
        return False
    if not st:
        say('  %s 本来就是停的' % SVC)
        return False
    say('  停 CPU 看守服务 %s ...' % SVC)
    sh(['sc', 'stop', SVC])
    for _ in range(40):
        if svc_running() is False:
            return True
        time.sleep(0.5)
    say('  !! %s 没停下来（继续测，但阶段二的数据可能有功耗墙干扰）' % SVC)
    return True


def start_cpu_svc(was_running):
    if not was_running:
        return
    say('  启动 CPU 看守服务 %s ...' % SVC)
    sh(['sc', 'start', SVC])


# --------------------------------------------------------------------------
# EC 读写 / 负载
# --------------------------------------------------------------------------
def read_rpm(ec, n=READS):
    """连读 n 次转速，返回 (中位数, [全部读数])。读不到的算 None。"""
    vals = []
    for _ in range(n):
        try:
            vals.append(ec.rpm())
        except Exception:
            vals.append(None)
        time.sleep(0.4)
    good = sorted(v for v in vals if v is not None)
    if not good:
        return None, vals
    return good[len(good) // 2], vals


def ec_temp(ec):
    """EC 0x78，越界值（>127，实测偶发 132/0x80）当读不到。"""
    try:
        v = ec.ec_read(fc.EC_CPU_TMP)
    except Exception:
        return None
    return v if 0 <= v <= 127 else None


def write_level(ec, lv):
    """写档位并确认没被抢回去。返回 True=写成功且仍是这一档。"""
    try:
        ec.set_fan(lv)
    except Exception as exc:
        say('  写 0x%02X 失败: %s' % (lv, exc))
        return False
    time.sleep(1.0)
    try:
        if ec.ec_read(fc.EC_FAN) == lv:
            return True
        ec.set_fan(lv)
        time.sleep(1.0)
        return ec.ec_read(fc.EC_FAN) == lv
    except Exception:
        return False


def burn(threads, seconds):
    procs = []
    for _ in range(max(1, threads)):
        try:
            procs.append(subprocess.Popen([sys.executable, BURN, str(int(seconds) + 5)],
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL))
        except Exception:
            pass
    return bool(procs), procs


def stop_burn(procs):
    for pr in procs:
        try:
            pr.kill()
            pr.wait(timeout=3)
        except Exception:
            pass


# --------------------------------------------------------------------------
# 三个阶段
# --------------------------------------------------------------------------
def phase1(ec):
    say('=== 阶段一：逐档测转速（不带负载）===')
    say('%6s  %-10s %8s %8s %8s  %s'
        % ('档位', '名称', '转速中位', '最小', '最大', 'EC温度'))
    rows = []
    for lv in LEVELS:
        if not write_level(ec, lv):
            say('0x%02X   %-10s 写不进去 / 被抢回去了，这一档没测成' % (lv, fan_name(lv)))
            continue
        time.sleep(SETTLE_S)
        med, vals = read_rpm(ec)
        t = ec_temp(ec)
        if med is None:
            say('0x%02X   %-10s 读不到转速(%s)' % (lv, fan_name(lv), vals))
            continue
        good = [v for v in vals if v is not None]
        say('0x%02X   %-10s %8d %8d %8d  %s'
            % (lv, fan_name(lv), med, min(good), max(good),
               ('%d°C' % t) if t is not None else '--'))
        rows.append((lv, med, min(good), max(good), t))
        if t is not None and t >= 92:
            say('  !! EC 温度 %d°C，阶段一结束（不再往下测了）' % t)
            break
    return rows


def phase2(ec):
    say()
    say('=== 阶段二：轻负载下比较降温能力（1 线程 %g 秒，看升温幅度 ΔT）===' % LOAD_S)
    say('%6s  %-10s %8s %8s %8s' % ('档位', '名称', '起点', '终点', 'ΔT'))
    cools = []
    for lv in COOL_LEVELS:
        t = ec_temp(ec)
        if t is not None and t >= TEMP_SKIP:
            say('  !! EC 温度 %d°C ≥ %d°C，跳过阶段二剩下的档' % (t, TEMP_SKIP))
            break
        if not write_level(ec, lv):
            say('0x%02X   写不进去，跳过' % lv)
            continue
        time.sleep(COOLDOWN_S)          # 先降温，让每档的起点温度接近
        t_before = ec_temp(ec)
        ok2, procs = burn(LOAD_THREADS, LOAD_S)
        if not ok2:
            say('0x%02X   负载起不来，跳过' % lv)
            continue
        t_start = time.time()
        while time.time() - t_start < LOAD_S:
            time.sleep(2.0)
            tt = ec_temp(ec)
            if tt is not None and tt >= TEMP_SKIP:
                say('  !! 冲到 %d°C，提前停负载' % tt)
                break
        stop_burn(procs)
        t_after = ec_temp(ec)
        dt = ((t_after - t_before)
              if (t_after is not None and t_before is not None) else None)
        say('0x%02X   %-10s %8s %8s %8s'
            % (lv, fan_name(lv),
               ('%d°C' % t_before) if t_before is not None else '--',
               ('%d°C' % t_after) if t_after is not None else '--',
               ('%+d°C' % dt) if dt is not None else '--'))
        cools.append((lv, dt))
    return cools


def phase3(ec):
    """热态重标定：同样 2 线程下，各档在 75~85°C 时的转速。"""
    say()
    say('=== 阶段三：高温重标定（2 线程 %g 秒，热态转速）===' % HOT_S)
    say('%6s  %-10s %8s %8s  %s' % ('档位', '名称', '热态温度', '热态转速', '采样'))
    rows = []
    for lv in HOT_LEVELS:
        if not write_level(ec, lv):
            say('0x%02X   写不进去，跳过' % lv)
            continue
        ok2, procs = burn(HOT_THREADS, HOT_S)
        if not ok2:
            say('0x%02X   负载起不来，跳过' % lv)
            continue
        samples = []
        t_start = time.time()
        while time.time() - t_start < HOT_S:
            time.sleep(2.0)
            t = ec_temp(ec)
            try:
                r = ec.rpm()
            except Exception:
                r = None
            samples.append((t, r))
            if t is not None and t >= HOT_TARGET and len(samples) >= 3:
                break
            if t is not None and t >= HOT_TEMP_MAX:
                say('  !! 冲到 %d°C ≥ %d°C，停负载' % (t, HOT_TEMP_MAX))
                break
        stop_burn(procs)
        hot = [(t, r) for t, r in samples if t is not None and r is not None]
        if not hot:
            say('0x%02X   没采到有效样本' % lv)
        else:
            tail = hot[-3:]
            t_med = sorted(t for t, _ in tail)[len(tail) // 2]
            r_med = sorted(r for _, r in tail)[len(tail) // 2]
            say('0x%02X   %-10s %7d°C %8d  %s'
                % (lv, fan_name(lv), t_med, r_med,
                   ' '.join('%d°C/%d' % (t, r) for t, r in tail)))
            rows.append((lv, t_med, r_med))
        # 降温（保持当前档位继续吹）
        t0 = time.time()
        while time.time() - t0 < HOT_COOLDOWN_S:
            t = ec_temp(ec)
            if t is not None and t <= 60:
                break
            time.sleep(2.0)
    return rows


# --------------------------------------------------------------------------
def main():
    global OUT, fc
    fc = load_fan()
    if not is_admin():
        sys.exit('[X] 需要管理员权限（PawnIO 只对管理员开放 EC 端口）。\n'
                 '    请双击同目录的 16-风扇档位标定.bat —— 它会自己弹 UAC 提权再跑。')
    if not RPM_ONLY and not os.path.isfile(BURN):
        say('[!] 同目录缺 _负载发生器.py —— 只做阶段一（测转速）')
        globals()['RPM_ONLY'] = True

    try:
        ec = fc.PawnIO()
    except Exception as exc:
        sys.exit('[X] 打不开 EC 通道（%s）\n'
                 '    确认 PawnIO 已安装、fan\\src\\LpcACPIEC.bin 在、并且是管理员。' % exc)

    OUT = os.path.join(HERE, '风扇档位-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
    say('=== 风扇档位标定  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say('（EC 固件 N3PHT02w；HFSP=0x2F；转速 = (0x85<<8)|0x84，先读 0x84）')
    if RPM_ONLY:
        say('模式: 只测转速（rpm-only）')
    elif HOT_MODE:
        say('模式: 阶段一 + 阶段二 + 阶段三（热态）')
    else:
        say('模式: 阶段一 + 阶段二（1 线程 %g 秒）' % LOAD_S)
    say()

    orig = None
    try:
        orig = ec.ec_read(fc.EC_FAN)
    except Exception:
        pass
    t0 = ec_temp(ec)
    say('原档位 = %s   当前 EC 温度 = %s' % (fan_name(orig),
                                            ('%d°C' % t0) if t0 is not None else '读取失败'))
    say()
    say('【注意】接下来几分钟没有看守 —— 风扇档位由本脚本直接写 EC 控制。')
    say('        机器会忽快忽慢（风扇在逐档换）属正常；测完自动恢复。')
    say()

    fan_was_running = stop_fan()
    svc_was_running = stop_cpu_svc()
    rows = cools = hots = []
    try:
        rows = phase1(ec)
        if not RPM_ONLY and rows:
            cools = phase2(ec)
        if HOT_MODE and rows:
            hots = phase3(ec)
    except KeyboardInterrupt:
        say()
        say('[!] 手动中断（Ctrl+C），开始收尾')
    finally:
        say()
        say('=== 收尾 ===')
        if orig is not None:
            try:
                ec.set_fan(orig)
                back = ec.ec_read(fc.EC_FAN)
                say('  恢复原档位 %s（回读 %s）' % (fan_name(orig), fan_name(back)))
            except Exception as exc:
                say('  恢复原档位失败: %s' % exc)
        else:
            say('  原来档位没读到，保守起见交还 EC 自动(0x80)')
            try:
                ec.set_fan(AUTO)
            except Exception:
                pass
        try:
            ec.close()
        except Exception:
            pass
        start_cpu_svc(svc_was_running)
        if fan_was_running:
            start_fan()
        say('  收尾完成')

    say()
    say('=== 结果表 ===')
    say('  【转速表】抄进手册 02 §1（替换抄来的旧值）:')
    for lv, med, lo, hi, t in rows:
        say('    0x%02X %-6s ≈ %d RPM   (%d~%d, %s)'
            % (lv, fan_name(lv), med, lo, hi, ('%d°C' % t) if t is not None else '--'))
    if cools:
        say()
        say('  【降温能力】同样负载下升温越少 = 该档散热越强:')
        for lv, dt in cools:
            say('    0x%02X %-6s ΔT = %s'
                % (lv, fan_name(lv), ('%+d°C' % dt) if dt is not None else '读不到'))
    if hots:
        say()
        say('  【热态转速】同样 2 线程负载、温度接近时的转速（分得开才值得做中间档）:')
        for lv, t, r in hots:
            say('    0x%02X %-6s %d°C -> %d RPM' % (lv, fan_name(lv), t, r))
        if len(hots) >= 2:
            span = max(r for _, _, r in hots) - min(r for _, _, r in hots)
            say('    各档极差 = %d RPM %s'
                % (span, '（≤120 就是同一档，别拿它当中间档）' if span <= 120
                   else '（有区分度，可以当独立档位用）'))
    say()
    say('  【怎么用】')
    say('    ① 看 0x06 和 0x07 的转速差 —— 差得多就说明 7 档能当"中间档"，')
    say('       曲线可以走 2档 -> 6档 -> 7档 -> 满速，不用一步跳到满速；')
    say('    ② 看 ΔT 的差 —— 6 档的 ΔT 明显大于 7 档/满速，就证实了"6 档散热不够，')
    say('       所以温度一顶到阈值就得切满速"这个机理。')
    say()
    say('报告已存: %s' % OUT)
    try:
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG) + '\n')
    except Exception as exc:
        say('  报告写盘失败: %s' % exc)


if __name__ == '__main__':
    main()
