# -*- coding: utf-8 -*-
"""14-EC总线实测.py —— 判定"EC 串位率"到底是真的总线错误，还是工具自己的统计口径虚高。

怎么跑：双击同目录 `14-EC总线实测.bat`（会自己弹 UAC 提权）
        或在管理员窗口里  python "14-EC总线实测.py"

背景（2026-10-19）：
  看守服务里那行 "EC 总线体检: ... 串位 66 次(52.8%)" 是把三种情况合在一个分子里：
    a) 单事务读不出来（`ec_read` 返回 None，端口事务失败）          —— 真错误
    b) 哨兵 0xC9 不等于 65（`ec_read_strict` 判废）                 —— 真错误
    c) `ec_read_ok` 连续两次读到的值不同                            —— **不一定是错误**
       温度(0x78/0x7A~0x7E)、转速(0x84/0x85)本来就该变，
       而 `ec_read_ok` 没有像 `ec_read_strict` 那样把这种情况单独记为 "retry"。
  本脚本把三种情况拆开分别测量，直接给出各自的真实发生率。

全脚本只读，不写任何寄存器。约 2~4 分钟。
"""
import importlib.util
import os
import sys
import time
import traceback
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_cpf_base():
    import glob
    cands = []
    a = os.environ.get('APPDATA')
    if a:
        cands.append(os.path.join(a, 'T14scontrol', 'cpu'))
    cands += sorted(glob.glob(r'C:\Users\*\AppData\Roaming\T14scontrol\cpu'))
    for c in cands:
        if os.path.isfile(os.path.join(c, 'CpuPLFix.py')):
            return c
    return cands[0] if cands else r'C:\Users\Ting\AppData\Roaming\T14scontrol\cpu'


def _load_cpf():
    base = _find_cpf_base()
    spec = importlib.util.spec_from_file_location('cpf', os.path.join(base, 'CpuPLFix.py'))
    m = importlib.util.module_from_spec(spec)
    sys.modules['cpf'] = m
    spec.loader.exec_module(m)
    return m, base


OUT = []
T0 = time.time()


def w(s=''):
    OUT.append(str(s))
    print(s)


def sec(t):
    w('')
    w('=' * 72)
    w('== ' + t)
    w('=' * 72)


def safe(label, fn):
    try:
        return fn()
    except Exception as e:
        w('  [%s] 异常 %s: %s' % (label, type(e).__name__, e))
        return None


def main():
    global cpf
    cpf, base = _load_cpf()

    w('EC 总线实测   %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    w('CpuPLFix 目录: %s' % base)
    w('管理员: %s' % safe('is_admin', cpf.is_admin))
    w('驱动就绪: %s' % safe('ensure_driver', lambda: cpf.ensure_driver(verbose=False)))

    # ---------- 0. 统计口径拆解：谁在贡献 "串位" ----------
    # 把 ec_bus_stat 换成带调用方记录的版本（monkey-patch，只影响本进程）
    origin = cpf.ec_bus_stat
    tally = Counter()

    def spy(hit_bad, retry=False):
        stack = traceback.extract_stack()
        # 上溯到 CpuPLFix.py 之外的第一个帧 = 真正的业务调用方
        who = '?'
        for f in reversed(stack[:-2]):
            if f.filename.replace('/', '\\').split('\\')[-1] != 'CpuPLFix.py':
                who = '%s' % (f.name,)
                break
        if hit_bad:
            tally['bad by %s' % who] += 1
        elif retry:
            tally['retry by %s' % who] += 1
        else:
            tally['ok by %s' % who] += 1
        return origin(hit_bad, retry)

    cpf.ec_bus_stat = spy

    sec('0. 当前总线状态（进程启动以来的累计值）')
    w('  ec_bus_stats() = %s' % (safe('stats', cpf.ec_bus_stats),))
    w('  含义: (读判定总数, 串位数, 自适应等待毫秒, 值变动重读数)')
    w('  当前自适应等待 settle = %.1f ms' % (cpf.ec_bus_settle() * 1000.0))

    # ---------- 1. 哨兵自身：单事务读 0xC9 ----------
    sec('1. 哨兵 0xC9(HWAT) 单事务连读 300 次 —— 它到底是不是恒 65')
    c = Counter()
    t = time.time()
    for _ in range(300):
        c[cpf.ec_read(0xC9, delay=0.003)] += 1
    dt = time.time() - t
    n_none = c.get(None, 0)
    n_ne65 = sum(v for k, v in c.items() if k is not None and k != 65)
    w('  用时 %.1f 秒, 平均每次事务 %.2f ms' % (dt, dt * 1000.0 / 300))
    w('  取值分布: %s' % dict(sorted(c.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    w('  · 读不出来(None):        %d / 300 = %.1f%%   <- 真·事务失败' % (n_none, 100.0 * n_none / 300))
    w('  · 读出来但不是 65:       %d / 300 = %.1f%%   <- 真的数据错位' % (n_ne65, 100.0 * n_ne65 / 300))
    w('  · 哨兵判废合计:          %d / 300 = %.1f%%' % (n_none + n_ne65, 100.0 * (n_none + n_ne65) / 300))

    # ---------- 2. 被判成 "串位" 的第三种来源：值变动 ----------
    sec('2. 单事务双读：每个寄存器"连续两次读到同一个值"的概率')
    w('  （这里用的是最原始的 ec_read，不重试、不加等待外的延迟）')
    w('  %-6s %-22s %-8s %s' % ('偏移', '名字', '不一致', '说明'))
    regs = [(0xC9, 'HWAT 哨兵(应恒定)'), (0x78, 'TMP0 主温度(会变)'),
            (0x7A, 'TTS1(会变)'), (0x7B, 'TTS6(会变)'),
            (0x7C, 'TTS2(会变)'), (0x7D, 'TTS3(会变)'), (0x7E, 'TTS4(会变)'),
            (0x84, 'HSPD 转速低字节(会变)'), (0x85, 'HSPD 转速高字节'),
            (0x89, 'TSL1 过热标志(常量 0)'), (0x8A, 'TSL2(常量 0)'), (0x8B, 'TSL3(常量 0)'),
            (0x2F, 'HFSP 风扇档(常量)'), (0x88, 'TSL0(常量)'),
            (0xB0, 'BSC0 充电起始(常量 80)'), (0xB1, 'BEC0 充电结束(常量 90)'),
            (0x38, 'HB0S 电池 0 状态')]
    per_reg = {}
    for off, name in regs:
        mismatch = 0
        pairs = 25
        for _ in range(pairs):
            a = cpf.ec_read(off, delay=0.003)
            b = cpf.ec_read(off, delay=0.003)
            if a != b:
                mismatch += 1
        per_reg[off] = mismatch
        w('  0x%02X   %-22s %2d/%d = %5.1f%%' % (off, name, mismatch, pairs, 100.0 * mismatch / pairs))

    # ---------- 3. 服务实际走的那条路 ----------
    sec('3. ec_read_ok() 成功率 —— 看守服务读温度/档位走的就是这条')
    for off, name, n in ((0x78, 'TMP0 主温度', 40), (0xC9, 'HWAT 哨兵', 40),
                         (0x7A, 'TTS1', 30), (0x7D, 'TTS3', 30),
                         (0x2F, 'HFSP 风扇档', 20)):
        givesup = 0
        vals = Counter()
        t = time.time()
        for _ in range(n):
            v = cpf.ec_read_ok(off)
            if v is None:
                givesup += 1
            else:
                vals[v] += 1
        w('  0x%02X %-14s 判废 %2d/%d = %5.1f%%   读到过的值=%s   用时 %.1f 秒'
          % (off, name, givesup, n, 100.0 * givesup / n,
             dict(sorted(vals.items(), key=lambda kv: kv[0])), time.time() - t))

    sec('4. ec_read_strict() 成功率 —— 固件过热标志 0x89/0x8A/0x8B 走这条')
    for off in (0x89, 0x8A, 0x8B):
        givesup = 0
        vals = Counter()
        t = time.time()
        for _ in range(20):
            v = cpf.ec_read_strict(off)
            if v is None:
                givesup += 1
            else:
                vals[v] += 1
        w('  0x%02X  判废 %2d/20 = %5.1f%%   值=%s   用时 %.1f 秒'
          % (off, givesup, 100.0 * givesup / 20, dict(vals), time.time() - t))

    # ---------- 5. 全表双读（和 13-EC诊断 dump 同一方法）----------
    sec('5. 全表 0x00~0xFF 双读一致性（与 13-EC诊断 的 dump 同方法）')
    bad_offs = []
    t = time.time()
    for off in range(0x100):
        a = cpf.ec_read(off, delay=0.003)
        b = cpf.ec_read(off, delay=0.003)
        if a != b:
            bad_offs.append((off, a, b))
        time.sleep(0.02)
    w('  用时 %.1f 秒' % (time.time() - t))
    w('  两次读不一致: %d / 256 = %.1f%%' % (len(bad_offs), 100.0 * len(bad_offs) / 256))
    w('  不一致的偏移(0x%02X, 第一次, 第二次):')
    for off, a, b in bad_offs:
        fx = lambda v: 'None' if v is None else '0x%02X' % v
        w('    0x%02X  %s  %s' % (off, fx(a), fx(b)))

    # ---------- 6. 是谁在贡献"串位" ----------
    sec('6. 本次实测里"串位"到底由哪个调用方产生')
    for k in sorted(tally):
        w('  %-28s %d 次' % (k, tally[k]))
    w('  （如果绝大多数 bad 都挂在读温度/转速的调用方上，就说明是口径问题，不是总线坏了）')

    # ---------- 7. CPU / 功耗墙现场值 ----------
    sec('7. CPU 与功耗墙现场值（MSR / MMIO）')
    def bits(v, a, b):
        return (v >> a) & ((1 << (b - a + 1)) - 1)

    m = safe('rdmsr 0x1A2', lambda: cpf.rdmsr(0x1A2))
    if m:
        w('  MSR 0x1A2 TEMPERATURE_TARGET = 0x%016X   TjMax = %d °C' % (m, bits(m, 16, 23)))
    m = safe('rdmsr 0x1AE', lambda: cpf.rdmsr(0x1AE))
    if m:
        w('  MSR 0x1AE TURBO_RATIO_LIMIT1 = 0x%016X   (9~16 核倍频 %s)'
          % (m, [bits(m, 8 * i, 8 * i + 7) for i in range(8)]))
    m = safe('rdmsr 0x1B1', lambda: cpf.rdmsr(0x1B1))
    if m:
        w('  MSR 0x1B1 PKG_THERM_STATUS = 0x%016X   DTS=%d°C  PROCHOT=%d  PROCHOT_LOG=%d  温度超阈值=%d'
          % (m, bits(m, 16, 22), bits(m, 0, 0), bits(m, 1, 1), bits(m, 2, 2)))
    m = safe('rdmsr 0x64F', lambda: cpf.rdmsr(0x64F))
    if m:
        w('  MSR 0x64F CORE_PERF_LIMIT_REASONS = 0x%016X' % m)
        w('     当前置位 bit: %s' % [i for i in range(16) if bits(m, i, i)])
        w('     历史置位 bit: %s' % [i for i in range(16) if bits(m, 16 + i, 16 + i)])
    m = safe('rdmsr 0x614', lambda: cpf.rdmsr(0x614))
    if m:
        w('  MSR 0x614 PKG_POWER_INFO = 0x%016X   标称 TDP(PBP) = %.1f W' % (m, bits(m, 0, 14) / 8.0))
    m = safe('rdmsr 0x610', lambda: cpf.rdmsr(0x610))
    if m:
        w('  MSR 0x610 PKG_POWER_LIMIT = 0x%016X' % m)
        w('     PL1=%.2fW enable=%d | PL2=%.2fW enable=%d | lock(bit63)=%d'
          % (bits(m, 0, 14) / 8.0, bits(m, 15, 15),
             bits(m, 32, 46) / 8.0, bits(m, 47, 47), bits(m, 63, 63)))
    mb = safe('mchbar', cpf.mchbar)
    if mb:
        mm = safe('rdphys', lambda: cpf.rdphys(mb + cpf.MMIO_PL_OFF, 8))
        w('  MCHBAR = 0x%X, 功耗墙镜像 +0x%X = %s'
          % (mb, cpf.MMIO_PL_OFF, ('0x%016X' % mm) if mm is not None else '读不到'))
        if mm is not None:
            w('     = MMIO_DISABLED_LOCKED(0x%016X) ? %s'
              % (cpf.MMIO_DISABLED_LOCKED, mm == cpf.MMIO_DISABLED_LOCKED))
            w('     解码 PL1=%.2fW PL2=%.2fW lock=%d'
              % (bits(mm, 0, 14) / 8.0, bits(mm, 32, 46) / 8.0, bits(mm, 63, 63)))

    # ---------- 8. 现场寄存器 ----------
    sec('8. 现场关键 EC 寄存器')
    w('  ec_temp()      = %s' % (safe('ec_temp', cpf.ec_temp),))
    w('  ec_hfsp()      = %s  (%s)' % (safe('ec_hfsp', cpf.ec_hfsp),
                                       safe('name', lambda: cpf.fan_name(cpf.ec_hfsp()))))
    w('  ec_rpm()       = %s RPM' % (safe('ec_rpm', cpf.ec_rpm),))
    w('  ec_extra_temps() = %s' % (safe('extra', cpf.ec_extra_temps),))
    w('  ec_trip_flags()  = %s' % (safe('flags', lambda: dict(cpf.ec_trip_flags())),))
    w('  battery_ec_state() = %s' % (safe('batt', cpf.battery_ec_state),))
    for off, name in ((0x31, 'HUWB/VPON/VRST'), (0x88, 'TSL0'), (0x35, 'FANE'),
                      (0x30, 'HMUT'), (0x46, 'BTWK'), (0x3B, 'KBLT/BDPM'),
                      (0x32, 'HWPM'), (0x28, 'HVGA'), (0x20, 'HDUZ')):
        v = cpf.ec_read(off, delay=0.008)
        w('  0x%02X %-16s = %s' % (off, name, '读不到' if v is None else '0x%02X' % v))

    sec('9. 风扇档位对照（写入值与回读值）')
    w('  FAN_LEVEL_RPM 表(工具内置): %s' % (safe('tbl', lambda: dict(cpf.FAN_LEVEL_RPM)),))
    w('  当前档位回读 = %s' % (safe('hfsp', cpf.ec_hfsp),))
    w('  当前转速 = %s RPM' % (safe('rpm', cpf.ec_rpm),))
    w('  注: 表里的 RPM 是"预期值"，与实测可能对不上 —— 本次的 rpm 就是实测。')

    w('')
    w('=' * 72)
    w('总耗时 %.1f 秒' % (time.time() - T0))
    w('=' * 72)

    path = os.path.join(HERE, 'EC总线实测-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT))
    print('')
    print('[报告已写出] ' + path)


if __name__ == '__main__':
    main()
