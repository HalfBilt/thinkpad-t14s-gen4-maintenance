# -*- coding: utf-8 -*-
"""10-性能自检.py —— 一键判断 CPU 有没有被降频（需管理员）

怎么跑：双击同目录 `10-性能自检.bat`（会自己提权）
        或管理员窗口里  python "10-性能自检.py"
结果：屏幕输出 + 同目录 `性能自检-时间戳.txt`

检查项：
  1. 功耗墙状态：MSR / MMIO / 当前电源档（正常 = 生效值 >= 目标档）
  2. 当前倍频与温度（温度公式 TjMax - DTS，仅空闲可信）
  3. 单线程 7 秒 -> 最高睿频倍频（被限住上不去，正常应 >=46 = 4.6GHz）
  4. 8 进程 7 秒 -> 满载功耗/倍频（顺便看散热能吐多少瓦）
判读规则写在输出末尾。

依赖：**已装 CpuPLFix** —— 借它的 `CpuPLFix.py` 读写 MSR/MMIO，用它的 TSCtl 驱动。
      没装就先看手册第八节，或第十一节第 3 步。
来源：agent 工作区的 _perfcheck.py（2026-10-18 收录；路径改成自动定位，其余逻辑未动）。
"""
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'T14scontrol', 'cpu')
BURN = os.path.join(HERE, '_负载发生器.py')
PY = sys.executable

if not os.path.isfile(os.path.join(BASE, 'CpuPLFix.py')):
    sys.exit('[X] 找不到 CpuPLFix（%s）—— 先装它，见手册第八节' % BASE)
if not os.path.isfile(BURN):
    sys.exit('[X] 同目录缺 _负载发生器.py')

sys.path.insert(0, BASE)
_spec = importlib.util.spec_from_file_location('cpf', os.path.join(BASE, 'CpuPLFix.py'))
cpf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpf)

if not cpf.is_admin():
    sys.exit('[X] 需要管理员权限（要读 MSR / 装 TSCtl 驱动）。\n'
             '    请双击同目录的 10-性能自检.bat —— 它会自己弹 UAC 提权再跑。')

OUT = os.path.join(HERE, '性能自检-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
L = []
issues = []

u = cpf.rdmsr(0x606)
E_UNIT = 1.0 / (1 << ((u >> 8) & 0x1F)) if u else 6.104e-05


def ratio():
    r = cpf.rdmsr(cpf.MSR_PERF_STATUS)
    return ((r or 0) >> 8) & 0xFF


def therm():
    v = cpf.rdmsr(0x1A2)
    tjmax = ((v >> 16) & 0xFF) if v else 100
    if tjmax < 50 or tjmax > 120:
        tjmax = 100
    tv = cpf.rdmsr(0x1B1) or 0
    return tjmax - ((tv >> 16) & 0x7F), tjmax, tv & 1


def burst(nproc, seconds, label):
    procs = [subprocess.Popen([PY, BURN, str(seconds + 2)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for _ in range(nproc)]
    time.sleep(1.2)
    e0, t0, rs = cpf.rdmsr(0x611), time.time(), []
    end = time.time() + seconds
    hot = False
    while time.time() < end:
        rs.append(ratio())
        t, _, _ts = therm()
        if t >= 95:
            hot = True
        time.sleep(0.3)
    e1 = cpf.rdmsr(0x611)
    dt = time.time() - t0
    w = ((e1 - e0) & 0xFFFFFFFF) * E_UNIT / dt if dt > 0.5 else -1
    for p in procs:
        p.kill(); p.wait()
    L.append('%-12s %d 进程: 倍频 %d~%d (%.2f~%.2f GHz)  平均功耗 %.1fW%s'
             % (label, nproc, min(rs), max(rs), min(rs) / 10, max(rs) / 10, w,
                '  [温度触顶]' if hot else ''))
    return max(rs), w, hot


cpf.ensure_driver(verbose=False)
cfg = cpf.load_cfg()
mode = cpf.mmio_mode(cfg)
ac = cpf.on_ac()

L.append('===== CpuPLFix 性能自检 %s ====='
         % time.strftime('%Y-%m-%d %H:%M:%S'))
L.append('')

m0 = cpf.decode_pl(cpf.rdmsr(0x610))
mb = cpf.mchbar()
o0 = cpf.decode_pl(cpf.rdphys(mb + 0x59A0, 8)) if mb else None
mode_desc = 'MMIO 清零+锁(固件侧禁用, MSR 全权)' if (mb and cpf.rdphys(mb + 0x59A0, 8) == cpf.MMIO_DISABLED_LOCKED) else 'MMIO 写值+锁(老方案)'

L.append('[1] 功耗墙状态')
L.append('  电源        : %s' % ('AC 适配器' if ac else '电池'))
L.append('  档位        : [%s] PL1=%.1fW PL2=%.1fW  (mmio_mode=%s)'
         % (cpf._pick_profile(cfg)[2], m0['pl1_w'], m0['pl2_w'], mode))
L.append('  MSR 0x610   : PL1=%.1fW PL2=%.1fW lock=%d' % (m0['pl1_w'], m0['pl2_w'], m0['lock']))
if o0:
    disabled = cpf.rdphys(mb + 0x59A0, 8) == cpf.MMIO_DISABLED_LOCKED
    L.append('  MMIO 0x59A0 : PL1=%.1fW lock=%d  -> %s'
             % (o0['pl1_w'], o0['lock'], mode_desc))
    if disabled:
        # MMIO 清零+锁 = 该侧已禁用, 不参与 min; 生效墙就是 MSR 的值
        L.append('  生效         : PL1=%.1fW (MMIO 已禁用, 取 MSR 侧)' % m0['pl1_w'])
    else:
        L.append('  生效(取min) : PL1=%.1fW' % min(m0['pl1_w'], o0['pl1_w']))
    pl1_t, _pl2_t, _n = cpf._pick_profile(cfg)
    if mode == 'disable':
        if not disabled:
            issues.append('MMIO 未进入清零+锁终态 -> disable 模式未生效, 重启后由开机流程纠正')
        if pl1_t and abs(m0['pl1_w'] - pl1_t) > 0.5:
            issues.append('MSR PL1 %.1fW 与目标档 %.1fW 不符 -> 服务应已在拉回; 若持续出现看服务状态' % (m0['pl1_w'], pl1_t))
    else:
        if o0['pl1_w'] < 15:
            issues.append('MMIO PL1 只有 %.1fW -> 固件正打着坏值, 跑一次 fix 或重启' % o0['pl1_w'])
        if not o0['lock']:
            issues.append('MMIO 未锁 -> 固件随时改回去, 跑 fix')
L.append('')

t, tjmax, ts_bit = therm()
r0 = ratio()
L.append('[2] 当前状态(负载前)')
L.append('  温度 %d °C (TjMax=%d)   倍频 %.2f GHz   节流位=%d' % (t, tjmax, r0 / 10, ts_bit))
if ts_bit:
    issues.append('Thermal Status=1: CPU 已到节流点(满载/温度墙, 正常生理现象, 轻载不该出现)')
L.append('')

L.append('[3] 短时负载睿频能力 (7 秒, 判断有没有被限住)')
m1, _w1, _h1 = burst(1, 7, '单线程')
time.sleep(2)
m4, _w4, _h4 = burst(8, 7, '8进程')
time.sleep(2)
L.append('')

L.append('[4] 判读')
if m1 >= 46:
    L.append('  [√] 单线程最高 %.2f GHz —— 睿频通路正常, 没有被限住' % (m1 / 10))
elif m1 >= 40:
    L.append('  [!] 单线程最高只有 %.2f GHz —— 偏低, 但可能受温度/PL2窗口影响, 再跑一次看看' % (m1 / 10))
    issues.append('单线程最高倍频 %d 偏低(正常应 >=46)' % m1)
else:
    L.append('  [X] 单线程最高只有 %.2f GHz —— 明显被限制, 查功耗墙/温度' % (m1 / 10))
    issues.append('单线程最高倍频 %d, 严重偏低' % m1)
if _h4:
    issues.append('8 进程负载温度触顶(>=95C) -> 散热贴顶, 满载性能受温度墙限制(治本: 清灰换硅脂/降 PL1)')
if issues:
    L.append('')
    L.append('  待关注:')
    for i in issues:
        L.append('   - %s' % i)
else:
    L.append('  [√] 未发现降频迹象')
L.append('')
L.append('  参考基线(本机 2026-10-18 实测): 单线程应到 48(4.8GHz) 左右;')
L.append('  8 进程满载 40W 档: 功耗 32~42W, 倍频 2.7~2.9GHz; 100C 是 TjMax, 属设计内行为。')

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(L) + '\n')
print('\n'.join(L))
