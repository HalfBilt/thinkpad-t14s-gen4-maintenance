# -*- coding: utf-8 -*-
"""11-满载温度60秒.py —— 全核满载 60 秒的温度 / 功耗曲线（需管理员）

怎么跑：双击同目录 `11-满载温度60秒.bat`（会自己提权）
        或管理员窗口里  python "11-满载温度60秒.py" [秒数]
结果：屏幕输出 + 同目录 `满载温度-时间戳.txt`

读法（温度 = TjMax - DTS，DTS 是距 TjMax 的余量，按 Intel SDM）：
  - 温度摸到 100 = 撞 TjMax 温度墙，频率被压低是 thermal throttle 的正常表现，
    **不是** 功耗墙 —— 看"功耗W"那列有没有真到档位上限即可区分。
  - 找出散热能稳定吐多少瓦 → 决定 PL1 该给多大（见手册 8.4）。

依赖：**已装 CpuPLFix**（借它的 CpuPLFix.py 读 MSR，用它的 TSCtl 驱动）。
来源：agent 工作区的 _burn60b.py（2026-10-18 收录；TjMax 改成现场读取，
      输出文件改成相对路径，其余逻辑未动）。
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'CpuPLFix')
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

OUT = os.path.join(HERE, '满载温度-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 60
STEP = 3
L = []
if not cpf.is_admin():
    sys.exit('[X] 需要管理员权限（要读 MSR / 装 TSCtl 驱动）。\n'
             '    请双击同目录的 11-满载温度60秒.bat —— 它会自己弹 UAC 提权再跑。')

cpf.ensure_driver(verbose=False)

u = cpf.rdmsr(0x606)
E_UNIT = 1.0 / (1 << ((u >> 8) & 0x1F)) if u else 6.104e-05
NCPU = os.cpu_count() or 16

_ta = cpf.rdmsr(0x1A2)
TJMAX = ((_ta >> 16) & 0xFF) if _ta else 100
if TJMAX < 50 or TJMAX > 120:
    TJMAX = 100

k32 = ctypes.windll.kernel32


def pin(cpu):
    k32.SetThreadAffinityMask(k32.GetCurrentThread(), 1 << cpu)


def ratio():
    r = cpf.rdmsr(cpf.MSR_PERF_STATUS)
    return ((r or 0) >> 8) & 0xFF


def therm_full(cpu=0):
    pin(cpu)
    v = cpf.rdmsr(0x1B1) or 0
    dts = (v >> 16) & 0x7F
    return {'dts': dts, 'temp': TJMAX - dts, 'valid': (v >> 31) & 1,
            'thermal_status': v & 1, 'prochot': (v >> 2) & 1, 'crit': (v >> 4) & 1,
            'raw': v}


def per_core_dts():
    r = []
    for c in [0, 4, 8, 12]:
        pin(c)
        v = cpf.rdmsr(0x19C) or 0      # per-core IA32_THERM_STATUS
        d = (v >> 16) & 0x7F
        r.append(TJMAX - d)
    pin(0)
    return r


def energy():
    v = cpf.rdmsr(0x611)
    return (v & 0xFFFFFFFF) if v is not None else None


L.append('TjMax = %d °C' % TJMAX)
L.append('energy unit: %.4f uJ/count' % (E_UNIT * 1e6))
L.append('')

L.append('=== 满载前 (空闲几秒后) ===')
t0 = therm_full()
pc = per_core_dts()
L.append('DTS差值=%d → 温度=%d °C  valid=%d thermal_status=%d' % (t0['dts'], t0['temp'], t0['valid'], t0['thermal_status']))
L.append('核0/4/8/12 每核温度: %s °C' % ' / '.join(str(x) for x in pc))
L.append('')

procs = [subprocess.Popen([PY, BURN, str(DUR + 10)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
         for _ in range(NCPU)]
time.sleep(1.5)

L.append('=== %d 秒满载曲线 (每 %d 秒, 温度 = TjMax - DTS) ===' % (DUR, STEP))
L.append('%5s %8s %9s %9s %8s %6s' % ('t(s)', '频率', '温度°C', '功耗W', 'PROCHOT', 'T.Status'))
temps, ratios, watts_all, crits = [], [], [], 0
e_prev, t_prev = energy(), time.time()
t_start = time.time()
n = 0
while time.time() - t_start < DUR + 1:
    time.sleep(STEP)
    e_now = energy()
    t_now = time.time()
    w = -1
    if e_now is not None and e_prev is not None:
        w = ((e_now - e_prev) & 0xFFFFFFFF) * E_UNIT / (t_now - t_prev)
    e_prev, t_prev = e_now, t_now
    n += 1
    r = ratio()
    th = therm_full()
    if th['crit']:
        crits += 1
    temps.append(th['temp'])
    ratios.append(r)
    if w >= 0:
        watts_all.append(w)
    L.append('%5d %5.2fGHz %7d°C %8.1f %7d %7d'
             % (n * STEP, r / 10.0, th['temp'], w, th['prochot'], th['thermal_status']))

for pr in procs:
    try:
        pr.kill(); pr.wait()
    except Exception:
        pass

L.append('')
L.append('=== 汇总 ===')
L.append('温度: min=%d max=%d  末段(30s后)平均 %.1f' % (min(temps), max(temps),
    sum(temps[10:]) / max(1, len(temps[10:]))))
L.append('倍频: 起点 %d → 末端 %d (%.2f → %.2f GHz)' % (ratios[0], ratios[-1], ratios[0] / 10.0, ratios[-1] / 10.0))
if watts_all:
    L.append('功耗: 起点 %.1f W  稳态 %.1f~%.1f W' % (watts_all[0], min(watts_all[10:]), max(watts_all[10:])))
L.append('critical 温度位触发: %d 次' % crits)
L.append('')
L.append('判读: 温度 = TjMax - DTS, DTS 是距 TjMax 的余量. 温度摸到 100 = 撞 TjMax 温度墙,')
L.append('      频率被压低是 thermal throttle 的正常表现, 不是功耗墙(看功耗列有没有到档位上限)。')
open(OUT, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
print('\n'.join(L))
