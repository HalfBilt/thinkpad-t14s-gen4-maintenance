# -*- coding: utf-8 -*-
"""风扇档位标定 —— 测出本机 HFSP(0x2F) 每个档位的**真实转速**和**散热能力**。

为什么需要这个
--------------
手册里那张档位表 `0x01=2480 0x02=2610 … 0x06=3880 0x07=3880` 里，
**只有 0x06(3880) 和 0x40(4900) 是本机实测**，1~5/7 档全是从别的项目抄来的
（抄来那张还不单调：0x05=3185 > 0x06=3170）。抄来的值只影响日志标签
（"3 档(约 2720 RPM)"），不影响控制，但**标定完就是真值**。

而"风扇 6 档 ↔ 满速 来回切"（手册 06 §3.1）要根治，光有转速还不够 ——
还要知道**每一档在同样负载下能把温度压到多少**，才能决定曲线该在哪几档之间切、
要不要加一个中间档（7 档）。

怎么做
------
1. 先把看守服务 `CpuPLFixSvc` 和计划任务 `CpuPLFix` 停掉 —— 它们每 15 秒会按曲线
   把 `0x2F` 改回自己要的档，量到的会是"别人的档"；
2. 逐档写 `0x2F = N`，等风扇爬速 12 秒，连读 5 次转速取**中位数**；
3. 每档再跑 20 秒**固定负载**（4 线程），记录 EC 温度 → 得到"该档在 4 线程负载下的平衡温度"
   （这就是决定曲线用的数）；
4. 测完**恢复原档位**，重新启动服务与计划任务；
5. 打印表格 + 存一份报告（`风扇档位-时间戳.txt`）。

安全
----
* **从快往慢测**（0x40 满速 → 0x07 → … → 0x01），机器不会因为档位太低而积热；
* 每档前后读一次 EC 温度，超过 92°C 立刻停止测剩下的档；
* `finally` 里无论如何都会**恢复原档位 + 把服务和计划任务拉起来**（哪怕中途 Ctrl+C）。

用法：双击同目录的 `16-风扇档位标定.bat`（它会自己弹 UAC 提权）。
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
AUTO = 0x80
SETTLE_S = 12.0        # 写完档位后等风扇爬稳的秒数
READS = 5              # 每档连读几次转速
LOAD_S = 20.0          # 每档的负载段秒数
LOAD_THREADS = 4       # 负载线程数（固定，才能横向比）
TEMP_ABORT = 92        # EC 温度超过它就停测剩下的档（别拿机器温度开玩笑）

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
    if ok and 'Disabled' in out:
        task_was_disabled = True
    else:
        say('  禁用计划任务 %s ...' % TASK)
        sh(['schtasks', '/change', '/tn', TASK, '/disable'])
    return svc_was_running, task_was_disabled


def start_watchers(svc_was_running, task_was_disabled):
    if not task_was_disabled:
        say('  恢复计划任务 %s' % TASK)
        sh(['schtasks', '/change', '/tn', TASK, '/enable'])
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


def burn(threads, seconds):
    """起 threads 个负载进程跑 seconds 秒，返回 (起没起来, 进程列表)。"""
    py = sys.executable
    procs = []
    for _ in range(max(1, threads)):
        try:
            procs.append(subprocess.Popen([py, BURN, str(int(seconds) + 5)],
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

    OUT = os.path.join(HERE, '风扇档位-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
    say('=== 风扇档位标定  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say('（本机 EC 固件 N3PHT02w；HFSP=0x2F；转速 = (0x85<<8)|0x84，先读 0x84）')
    say()

    orig = cpf.ec_hfsp()
    t0 = cpf.ec_temp()
    say('原档位 = %s   当前 EC 温度 = %s' % (cpf.fan_name(orig) if orig is not None else '读取失败',
                                            ('%d°C' % t0) if t0 is not None else '读取失败'))
    say()
    say('【注意】接下来 2 分钟内没有看守服务 —— 风扇档位由本脚本直接写 EC 控制。')
    say('        机器会明显变吵/变安静都是正常的（我们在逐档测）。')
    say()

    svc_was_running, task_was_disabled = stop_watchers()
    say()
    say('=== 逐档测量 ===')
    say('%6s  %-16s %8s %8s %8s  %8s  %s'
        % ('档位', '名称', '转速中位', '最小', '最大', '负载后温度', '备注'))
    rows = []
    try:
        for lv in LEVELS:
            ok, got = cpf.set_fan(lv)
            if not ok:
                say('0x%02X   写入失败(EC 无响应)，跳过' % lv)
                continue
            # EC 有自己的策略，会把 0x2F 抢回 0x80（交还自动）—— 确认一下我们写的还在
            time.sleep(1.0)
            back = cpf.ec_hfsp()
            if back != lv:
                cpf.set_fan(lv)
                time.sleep(1.0)
                back = cpf.ec_hfsp()
                if back != lv:
                    say('0x%02X   EC 把控制抢回去了(0x2F=0x%02X)，这一档没测成'
                        % (lv, back if back is not None else 0))
                    continue
            time.sleep(SETTLE_S)
            med, vals = read_rpm(cpf)
            t0 = cpf.ec_temp()
            name = cpf.fan_name(lv)
            if med is None:
                say('0x%02X   %-16s  读不到转速(%s)' % (lv, name, vals))
                continue
            good = [v for v in vals if v is not None]
            # ---- 负载段: 固定 4 线程跑 20 秒, 看这一档能把温度压到多少 ----
            t_load = None
            note = ''
            if os.path.isfile(BURN):
                ok2, procs = burn(LOAD_THREADS, LOAD_S)
                if ok2:
                    t_end = None
                    t_start = time.time()
                    while time.time() - t_start < LOAD_S:
                        time.sleep(2.0)
                        t = cpf.ec_temp()
                        if t is not None:
                            t_end = t
                    stop_burn(procs)
                    t_load = t_end
                else:
                    note = '负载起不来'
            else:
                note = '缺 _负载发生器.py'
            t = cpf.ec_temp()
            say('0x%02X   %-16s %8d %8d %8d  %8s  %s'
                % (lv, name, med, min(good), max(good),
                   ('%d°C' % t_load) if t_load is not None else '--', note))
            rows.append((lv, med, min(good), max(good), t_load, t))
            if t is not None and t >= TEMP_ABORT:
                say('  !! EC 温度 %d°C ≥ %d°C，停止测剩下的档（剩下的没测）' % (t, TEMP_ABORT))
                break
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
    for lv, med, lo, hi, tl, t in rows:
        say('    0x%02X 档   ≈ %d RPM   (%d~%d)' % (lv, med, lo, hi))
    say()
    say('  【散热表】%d 线程负载下各档的平衡温度 —— 用它决定曲线切在哪:'
        % LOAD_THREADS)
    for lv, med, lo, hi, tl, t in rows:
        say('    0x%02X 档 (%4d RPM)  负载后 %s' % (lv, med, ('%d°C' % tl) if tl else '读不到'))
    say()
    say('  【怎么用】找"6 档 ↔ 满速"那个来回切的温度带：')
    say('    如果 6 档的平衡温度 ≈ 曲线的满速阈值(现在 79°C)，那就是抖动的根因 ——')
    say('    阈值要挪到 6 档平衡温度之上(高 3~5°C)，或者在中间加一档(7 档)当过渡。')
    say()
    say('报告已存: %s' % OUT)
    try:
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG) + '\n')
    except Exception as exc:
        say('  报告写盘失败: %s' % exc)


if __name__ == '__main__':
    main()
