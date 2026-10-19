# -*- coding: utf-8 -*-
"""风扇档位标定 —— 测出本机 HFSP(0x2F) 每个档位的**真实转速**和**降温能力**。

为什么需要这个
--------------
手册里那张档位表 `0x01=2480 0x02=2610 … 0x06=3880 0x07=3880` 里，
**只有 0x06(3880) 和 0x40(4900) 是本机实测**，1~5/7 档全是从别的项目抄来的
（抄来那张还不单调：0x05=3185 > 0x06=3170）。抄来的值只影响日志标签
（"3 档(约 2720 RPM)"），不影响控制，但**标定完就是真值**。

"风扇 6 档 ↔ 满速 来回切"（手册 06 §3.1）要根治，光有转速还不够 ——
还要知道**每一档在同一负载下能压住多少温度**，才能决定曲线该在哪几档之间切、
要不要加一个中间档（7 档）。

⚠️ 2026-10-19 第一版脚本的教训（已修）
------------------------------------
第一版用 **4 线程满负载**测"平衡温度"，结果第一档就撞上 92°C 护栏退出了。
原因是：**4 线程 = 28W 满功耗 = 功耗受限区**，在这个区里**风扇开到满速也压不住**
（实测：满速 4815 RPM + 4 线程，20 秒就从 53°C 冲到 95°C）——
测到的不是"风扇的差别"，是"功耗墙的差别"。所以：
* 阶段一（测转速）**不带任何负载**，保证把每一档都测到；
* 阶段二（测降温能力）只用 **1 个线程 12 秒**，并且**只比较升温幅度 ΔT**，
  不去求"平衡温度"（那需要很长的时间，而功耗受限区根本达不到平衡）。

怎么做
------
1. 停掉看守服务 `CpuPLFixSvc` 和计划任务 `CpuPLFix` —— 它们每 15 秒会按曲线
   把 `0x2F` 改回自己要的档，量到的会是"别人的档"；
2. **阶段一**：逐档写 `0x2F = N`，等风扇爬速 12 秒，连读 5 次转速取**中位数**；
3. **阶段二**：对 `0x40 / 0x07 / 0x06` 三档，各跑 1 线程 12 秒，记录升温幅度 ΔT
   （档位越强，ΔT 越小）；每档之间先降温 25 秒，让起点温度接近；
4. 测完**恢复原档位**，重新启动服务与计划任务；
5. 打印表格 + 存一份报告（`风扇档位-时间戳.txt`）。

安全
----
* 阶段一**完全不带负载**，只是把风扇固定在某个档位，机器不会积热；
* 阶段二只有 1 个线程、12 秒，档位之间还有 25 秒降温；温度超过 88°C 就**跳过**
  剩下的阶段二（**不会**像第一版那样把整个测试中止掉）；
* `finally` 里无论如何都会**恢复原档位 + 把服务和计划任务拉起来**（哪怕中途 Ctrl+C）。

用法：双击同目录的 `16-风扇档位标定.bat`（它会自己弹 UAC 提权）。
想只测转速不测降温能力：命令行加 `rpm-only`。
"""
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'CpuPLFix')
SVC = 'CpuPLFixSvc'
TASK = 'CpuPLFix'
BURN = os.path.join(HERE, '_负载发生器.py')

LEVELS = [0x40, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01]    # 从快到慢
COOL_LEVELS = [0x40, 0x07, 0x06]      # 阶段二只比这三档（曲线用得上的）
AUTO = 0x80
SETTLE_S = 12.0        # 写完档位后等风扇爬稳的秒数
READS = 5              # 每档连读几次转速
LOAD_S = 12.0          # 阶段二的负载秒数
LOAD_THREADS = 1       # 阶段二只用 1 个线程（多线程=功耗受限，测不出风扇差别）
COOLDOWN_S = 25.0      # 阶段二每档之间的降温时间
TEMP_SKIP = 88         # 阶段二温度超过它就跳过剩下的（不中止整个测试）
RPM_ONLY = 'rpm-only' in sys.argv[1:]

LOG = []
OUT = None


def say(s=''):
    LOG.append(s)
    print(s, flush=True)


def load_cpf():
    p = os.path.join(BASE, 'CpuPLFix.py')
    if not os.path.isfile(p):
        sys.exit('[X] 找不到 CpuPLFix（%s）—— 先装它（1-install.bat）' % BASE)
    spec = importlib.util.spec_from_file_location('cpf', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sh(cmd):
    """跑一条命令，返回 (是否成功, 输出)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout or '') + (r.stderr or '')
    except Exception as exc:
        return False, str(exc)


def stop_watchers():
    """停掉看守服务和计划任务。返回 (服务是否本来就停着, 计划任务是否本来就禁用)。"""
    svc_was_running = True
    ok, out = sh(['sc', 'query', SVC])
    if ok and 'RUNNING' not in out.upper():
        svc_was_running = False
    else:
        say('  停服务 %s ...' % SVC)
        sh(['sc', 'stop', SVC])
        for _ in range(30):
            ok, out = sh(['sc', 'query', SVC])
            if not ok or 'STOPPED' in out.upper():
                break
            time.sleep(0.5)
    task_was_disabled = False
    ok, out = sh(['schtasks', '/query', '/tn', TASK, '/fo', 'LIST', '/v'])
    # schtasks 的输出跟着系统语言走: 英文是 Disabled, 中文是"已禁用" —— 两种都要认,
    # 否则会把用户自己禁用掉的任务又给启用回去。
    if ok and ('Disabled' in out or '已禁用' in out):
        task_was_disabled = True
    else:
        say('  禁用计划任务 %s ...' % TASK)
        ok2, out2 = sh(['schtasks', '/change', '/tn', TASK, '/disable'])
        if not ok2:
            say('  !! 禁用失败: %s' % out2.strip()[:200])
    return svc_was_running, task_was_disabled


def start_watchers(svc_was_running, task_was_disabled):
    if not task_was_disabled:
        say('  恢复计划任务 %s ...' % TASK)
        ok, out = sh(['schtasks', '/change', '/tn', TASK, '/enable'])
        if not ok:
            say('  !! 启用失败(要手动恢复!): %s' % out.strip()[:200])
        else:
            ok2, out2 = sh(['schtasks', '/query', '/tn', TASK, '/fo', 'LIST', '/v'])
            state = '?'
            for line in out2.splitlines():
                if 'State' in line or '状态' in line:
                    state = line.strip()
                    break
            say('    -> %s' % state)
    if svc_was_running:
        say('  启动服务 %s ...' % SVC)
        sh(['sc', 'start', SVC])


def read_rpm(cpf, n=READS):
    """连读 n 次转速，返回 (中位数, [全部读数])。读不到的算 None。"""
    vals = []
    for _ in range(n):
        v = cpf.ec_rpm()
        vals.append(v)
        time.sleep(0.4)
    good = sorted(v for v in vals if v is not None)
    if not good:
        return None, vals
    return good[len(good) // 2], vals


def write_level(cpf, lv):
    """写档位并确认 EC 没把控制抢回去。返回 True=写成功且仍是这一档。"""
    ok, _got = cpf.set_fan(lv)
    if not ok:
        return False
    time.sleep(1.0)
    if cpf.ec_hfsp() == lv:
        return True
    cpf.set_fan(lv)
    time.sleep(1.0)
    return cpf.ec_hfsp() == lv


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


def main():
    global OUT
    cpf = load_cpf()
    if not cpf.is_admin():
        sys.exit('[X] 需要管理员权限（TSCtl 驱动设备只对管理员开放）。\n'
                 '    请双击同目录的 16-风扇档位标定.bat —— 它会自己弹 UAC 提权再跑。')
    if not cpf.ensure_driver(verbose=False):
        sys.exit('[X] TSCtl 驱动打不开（CpuPLFix 没装好？见手册 01）')
    if not RPM_ONLY and not os.path.isfile(BURN):
        say('[!] 同目录缺 _负载发生器.py —— 只做阶段一（测转速）')
        globals()['RPM_ONLY'] = True

    OUT = os.path.join(HERE, '风扇档位-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
    say('=== 风扇档位标定  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say('（本机 EC 固件 N3PHT02w；HFSP=0x2F；转速 = (0x85<<8)|0x84，先读 0x84）')
    if RPM_ONLY:
        say('模式: 只测转速（rpm-only）')
    else:
        say('模式: 阶段一测转速 + 阶段二测降温能力（1 线程 %d 秒）' % LOAD_S)
    say()

    orig = cpf.ec_hfsp()
    t0 = cpf.ec_temp()
    say('原档位 = %s   当前 EC 温度 = %s' % (cpf.fan_name(orig) if orig is not None else '读取失败',
                                            ('%d°C' % t0) if t0 is not None else '读取失败'))
    say()
    say('【注意】接下来几分钟没有看守服务 —— 风扇档位由本脚本直接写 EC 控制。')
    say('        机器会忽快忽慢（风扇在逐档换）属正常；测完自动恢复。')
    say()

    svc_was_running, task_was_disabled = stop_watchers()
    rows = []
    try:
        # ---------------- 阶段一: 逐档测转速（无负载）----------------
        say('=== 阶段一：逐档测转速（不带负载）===')
        say('%6s  %-16s %8s %8s %8s  %s'
            % ('档位', '名称', '转速中位', '最小', '最大', 'EC温度'))
        for lv in LEVELS:
            if not write_level(cpf, lv):
                say('0x%02X   写不进去 / EC 把控制抢回去了，这一档没测成' % lv)
                continue
            time.sleep(SETTLE_S)
            med, vals = read_rpm(cpf)
            t = cpf.ec_temp()
            name = cpf.fan_name(lv)
            if med is None:
                say('0x%02X   %-16s  读不到转速(%s)' % (lv, name, vals))
                continue
            good = [v for v in vals if v is not None]
            say('0x%02X   %-16s %8d %8d %8d  %s'
                % (lv, name, med, min(good), max(good),
                   ('%d°C' % t) if t is not None else '--'))
            rows.append((lv, med, min(good), max(good)))
            if t is not None and t >= 92:
                say('  !! EC 温度 %d°C，阶段一结束（不再往下测了）' % t)
                break

        # ---------------- 阶段二: 轻负载下比较各档的降温能力 ----------------
        cools = []
        if not RPM_ONLY and rows:
            say()
            say('=== 阶段二：轻负载下比较降温能力（1 线程 %d 秒，看升温幅度 ΔT）===' % LOAD_S)
            say('%6s  %-16s %8s %8s %8s' % ('档位', '名称', '起点', '终点', 'ΔT'))
            for lv in COOL_LEVELS:
                t = cpf.ec_temp()
                if t is not None and t >= TEMP_SKIP:
                    say('  !! EC 温度 %d°C ≥ %d°C，跳过阶段二剩下的档' % (t, TEMP_SKIP))
                    break
                if not write_level(cpf, lv):
                    say('0x%02X   写不进去，跳过' % lv)
                    continue
                # 降温，让每档的起点温度接近
                time.sleep(COOLDOWN_S)
                t_before = cpf.ec_temp()
                ok2, procs = burn(LOAD_THREADS, LOAD_S)
                if not ok2:
                    say('0x%02X   负载起不来，跳过' % lv)
                    continue
                t_end = None
                t_start = time.time()
                while time.time() - t_start < LOAD_S:
                    time.sleep(2.0)
                    tt = cpf.ec_temp()
                    if tt is not None:
                        t_end = tt
                stop_burn(procs)
                t_after = cpf.ec_temp()
                dt = ((t_after - t_before)
                      if (t_after is not None and t_before is not None) else None)
                say('0x%02X   %-16s %8s %8s %8s'
                    % (lv, cpf.fan_name(lv),
                       ('%d°C' % t_before) if t_before is not None else '--',
                       ('%d°C' % t_after) if t_after is not None else '--',
                       ('%+d°C' % dt) if dt is not None else '--'))
                cools.append((lv, dt))
    finally:
        say()
        say('=== 收尾 ===')
        if orig is not None:
            ok, got = cpf.set_fan(orig)
            say('  恢复原档位 %s（回读 %s）' % (cpf.fan_name(orig),
                                               cpf.fan_name(got) if got is not None else '读取失败'))
        else:
            say('  原来档位没读到，保守起见交还 EC 自动(0x80)')
            cpf.set_fan(AUTO)
        start_watchers(svc_was_running, task_was_disabled)
        say('  服务与计划任务已恢复')

    say()
    say('=== 结果表 ===')
    say('  【转速表】抄进手册 02 §1 / 03 §2.1（替换抄来的旧值）:')
    for lv, med, lo, hi in rows:
        say('    0x%02X 档   ≈ %d RPM   (%d~%d)' % (lv, med, lo, hi))
    if cools:
        say()
        say('  【降温能力】同样负载下升温越少 = 该档散热越强:')
        for lv, dt in cools:
            say('    0x%02X 档   ΔT = %s' % (lv, ('%+d°C' % dt) if dt is not None else '读不到'))
        say()
        say('  【怎么用】:')
        say('    ① 看 0x06 和 0x07 的转速差 —— 差得多就说明 7 档能当"中间档"，')
        say('       曲线可以变成 2档 -> 6档 -> 7档 -> 满速，抖动自然就没了；')
        say('    ② 看 ΔT 的差 —— 6 档的 ΔT 明显大于 7 档/满速，就证实了"6 档散热不够，')
        say('       所以温度一顶到阈值就切满速"这个机理。')
    say()
    say('报告已存: %s' % OUT)
    try:
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG) + '\n')
    except Exception as exc:
        say('  报告写盘失败: %s' % exc)


if __name__ == '__main__':
    main()
