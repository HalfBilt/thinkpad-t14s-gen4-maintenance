# -*- coding: utf-8 -*-
"""15-EC延时对照.py —— 判定"写完偏移后等多久才读"对 EC 读错率的影响（因果，不是相关）。

怎么跑：双击同目录 `15-EC延时对照.bat`（会自己弹 UAC 提权）

背景（2026-10-19 18:28 那次实测的结论）：
  "串位"读到的不是随机垃圾，而是**上一次别的 EC 事务留在输出缓冲里的字节**
  （空闲时最常出现 0x2A = 42，正是 0x7A TTS1 的读数）。
  那次实测里：settle=3.0 ms 时"读到别人的字节"占 3.7%；settle=20.0 ms 时占 33.6%。
  看起来是"等得越久，越容易撞上系统 ACPI EC 驱动的事务"。

  本脚本把这件事做成**受控对照**：
    · 强制把自适应等待钉在 1 ms（关掉"失败 -> 等待翻倍"的正反馈，只留 delay 一个变量）；
    · 对每个 delay 跑同一批寄存器；
    · 分三组数：
        A 组  读 X、再读 X      —— 若"撞车 = 上笔事务的字节"，这组应几乎不冲突（上笔就是 X）
        B 组  读 X、再读 Y      —— X 的值冒充成 Y 的值，直接数出来
        C 组  哨兵-读Y-哨兵     —— 服务实际用的 ec_read_strict 形状
  A/B 的差别本身就是机制的直接证据。

全脚本只读，不写任何寄存器。约 3 分钟。
"""
import importlib.util
import os
import sys
import time
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


OUT = []
T0 = time.time()


def w(s=''):
    OUT.append(str(s))
    print(s)


def sec(t):
    w('')
    w('=' * 78)
    w('== ' + t)
    w('=' * 78)


# 受测寄存器：常量类和动态类混着放，避免"值恰好相同"造成的假结论
OFFSETS = [
    0x00, 0x01, 0x03, 0x05, 0x07, 0x0D, 0x14, 0x21, 0x23, 0x26,
    0x28, 0x2A, 0x2B, 0x2C, 0x2F, 0x30, 0x31, 0x32, 0x35, 0x38,
    0x3A, 0x3B, 0x46, 0x47, 0x48, 0x49, 0x50, 0x51, 0x78, 0x7A,
    0x7C, 0x7D, 0x7E, 0x7F, 0x83, 0x88, 0x89, 0x8A, 0x8B, 0x8C,
    0xB0, 0xB1, 0xB4, 0xC6, 0xC8, 0xC9, 0xCB, 0xCC, 0xCD, 0xCF,
]
ROUNDS = 3
DELAYS = [0.001, 0.003, 0.006, 0.012, 0.020]


def main():
    base = _find_cpf_base()
    spec = importlib.util.spec_from_file_location('cpf', os.path.join(base, 'CpuPLFix.py'))
    cpf = importlib.util.module_from_spec(spec)
    sys.modules['cpf'] = cpf
    spec.loader.exec_module(cpf)

    w('EC 延时对照实测   %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    w('CpuPLFix 目录: %s' % base)
    w('管理员: %s' % cpf.is_admin())
    w('驱动就绪: %s' % cpf.ensure_driver(verbose=False))

    # ---- 关键：把自适应等待钉死在 1 ms，让 delay 成为唯一变量 --------------
    # ec_read 内部是 time.sleep(max(delay, ec_bus_settle()))，settle 只要 <= 1 ms
    # 就完全不起作用；每次读前重置，彻底关掉那个正反馈环。
    def raw(off, d):
        cpf._ECBUS['settle'] = 0.001
        return cpf.ec_read(off, delay=d)

    uniq = list(dict.fromkeys(OFFSETS))

    # ---- 0. 慢读建"真值"表（每寄存器 5 次取众数）----
    sec('0. 建立真值表（每寄存器慢读 5 次取众数；delay=20ms、间隔 50ms）')
    truth = {}
    for off in uniq:
        c = Counter()
        for _ in range(5):
            c[raw(off, 0.020)] += 1
            time.sleep(0.05)
        v, n = c.most_common(1)[0]
        truth[off] = v if n >= 3 else None      # 自己都不稳的剔除
    line = ['0x%02X=%s' % (o, '0x%02X' % truth[o])
            for o in uniq if truth[o] is not None]
    for i in range(0, len(line), 6):
        w('    ' + '  '.join(line[i:i + 6]))
    w('  不稳定被剔除的: %s' % (['0x%02X' % o for o in uniq if truth[o] is None] or '无'))
    w('  ※ 真值表只取一次: 温度/档位/电池这类寄存器在这 ~70 秒里本来就会变(实测 20:00 那次')
    w('    0x2F 就在 12ms 那一档被服务从 02 改成 06), 那部分会被算进"B 组其它错" ——')
    w('    所以**绝对值偏高**, 只有随 delay 的**趋势**可信。')
    usable = [o for o in uniq if truth[o] is not None]

    # ---- 1. 各 delay 下的 A/B/C 三组对照 ----
    sec('1. 各 delay 下的受控对照（同一批寄存器，settle 全程钉在 1ms）')
    w('  delay    A:同寄存器不一致    B:X的值冒充Y    C:哨兵通过   单次事务')
    w('  -------  -----------------  -------------  -----------  ---------')
    rows = []
    for d in DELAYS:
        a_bad = a_n = 0
        b_fake = b_ok = b_other = b_n = 0
        c_ok = c_n = 0
        t_cnt = 0
        t0 = time.time()
        for _ in range(ROUNDS):
            for i, off in enumerate(usable):
                y = usable[(i + 1) % len(usable)]
                # ---- A 组：同一个寄存器读两次（上笔事务就是它自己）
                x1 = raw(off, d)
                x2 = raw(off, d)
                a_n += 1
                if x1 != x2:
                    a_bad += 1
                t_cnt += 2
                # ---- B 组：紧接在 X 之后读 Y —— 看 Y 是不是拿回了"上笔事务"的字节
                #    X 的第二次读(x2)就是上笔事务的结果；若 vy == x2 且 x2 != Y 的真值，
                #    说明 Y 这次读拿到的是上笔事务留在缓冲里的字节（污染）。
                vy = raw(y, d)
                if x2 is not None and truth[y] is not None:
                    b_n += 1
                    if vy == truth[y]:
                        b_ok += 1                   # Y 读对了
                    elif vy == x2 and x2 != truth[y]:
                        b_fake += 1                 # Y 拿回了上笔事务的字节（污染）
                    else:
                        b_other += 1                # 其它形式的错
                t_cnt += 1
                # ---- C 组：哨兵 + 读 Y + 哨兵（服务 ec_read_strict 的形状）
                c1 = raw(0xC9, d)
                vy = raw(y, d)
                c2 = raw(0xC9, d)
                c_n += 1
                if c1 == 65 and c2 == 65 and vy is not None:
                    c_ok += 1
                t_cnt += 3
        dt = time.time() - t0
        ms = dt * 1000.0 / max(1, t_cnt)
        rows.append((d, a_bad, a_n, b_fake, b_ok, b_other, b_n, c_ok, c_n, ms))
        w('  %-7s  %2d/%-3d = %5.1f%%      %2d/%-3d = %5.1f%%   %3d/%-3d=%4.0f%%   %.2f ms'
          % ('%.0f ms' % (d * 1000), a_bad, a_n, 100.0 * a_bad / a_n,
             b_fake, b_n, 100.0 * b_fake / b_n, c_ok, c_n, 100.0 * c_ok / c_n, ms))

    sec('2. 汇总')
    w('  delay    A 组不一致   B 组被冒充   B 组其它错   B 组读对   C 组哨兵通过')
    best = None
    for (d, a_bad, a_n, b_fake, b_ok, b_other, b_n, c_ok, c_n, ms) in rows:
        pa = 100.0 * a_bad / max(1, a_n)
        pb = 100.0 * b_fake / max(1, b_n)
        po = 100.0 * b_other / max(1, b_n)
        pk = 100.0 * b_ok / max(1, b_n)
        pc = 100.0 * c_ok / max(1, c_n)
        w('  %-7s  %6.1f%%       %6.1f%%       %6.1f%%       %6.1f%%     %6.1f%%'
          % ('%.0f ms' % (d * 1000), pa, pb, po, pk, pc))
        # ★2026-10-19: 原来按"B 组被冒充率"取最小。可 1ms/3ms/6ms 三次都是 0.0% —— 并列时
        #   取先遇到的那个, 于是"最佳 delay ≈ 1 ms"纯粹是排序副产品(20:00 那次就是这样),
        #   而真正的错全落在"其它错"里。改成按"B 组读错(冒充+其它)"最小选。
        wrong = pb + po
        if best is None or wrong < best[1]:
            best = (d, wrong, pa, pk, pc)
    w('')
    w('  最佳 delay ≈ %.0f ms（B 组读错 %.1f%%，A 组不一致 %.1f%%，B 组读对 %.1f%%，哨兵通过 %.1f%%）'
      % (best[0] * 1000, best[1], best[2], best[3], best[4]))
    w('  判读：')
    w('   · B 组被冒充率 >> A 组不一致率  → 证实"撞车读到的是上一次事务的字节"')
    w('   · B 组读错率随 delay 上升 → 证实"等得越久越容易撞车"（自适应等待帮倒忙）')
    w('   · 若两者都不成立 → 我上一条的推断错了，得换方向重新查')
    w('   · ★相邻两档只差 ≤3 个事件 = 打平, 别据此改参数(每档 150 次只够看趋势)')

    sec('3. 本次测量里"错值"的分布（看是不是温度量级的数）')
    c = Counter()
    for off in usable:
        for _ in range(6):
            v = raw(off, 0.020)
            if v is not None and v != truth[off]:
                c[v] += 1
    w('  错值(hex = dec, 出现次数):')
    w('    ' + '  '.join('%s=%d(%d次)' % ('0x%02X' % k, k, n)
                         for k, n in sorted(c.items())) or '    （无）')
    w('  注: 41/42/47/51 这类"温度量级"的值 = 别的温度寄存器事务的残留')

    sec('4. 现状复核')
    w('  ec_bus_stats() = %s   (读判定总数, 串位数, 等待ms, 值变动重读)' % (cpf.ec_bus_stats(),))
    w('  ec_temp() = %s   ec_hfsp() = %s   ec_rpm() = %s   ec_trip_flags() = %s'
      % (cpf.ec_temp(), cpf.ec_hfsp(), cpf.ec_rpm(), cpf.ec_trip_flags()))

    w('')
    w('=' * 78)
    w('总耗时 %.1f 秒' % (time.time() - T0))
    w('=' * 78)

    path = os.path.join(HERE, 'EC延时对照-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT))
    print('')
    print('[报告已写出] ' + path)


if __name__ == '__main__':
    main()
