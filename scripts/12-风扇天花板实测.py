# -*- coding: utf-8 -*-
"""12-风扇天花板实测.py —— 满载时读 EC，确认"自带(EC 自动)风扇控制"最高能到多少转速

怎么跑：双击同目录 `12-风扇天花板实测.bat`（会自己弹 UAC 提权）
结果：屏幕输出 + 同目录 `风扇天花板-时间戳.txt`

做什么（默认全程保持 EC 自动，一个字都不写风扇控制寄存器）：
  1. 空闲基线：读 EC 的 CPU 温度 0x78 / 风扇控制寄存器 HFSP 0x2F / 转速 0x84+0x85
  2. 全核满载 90 秒，每 3 秒采一次
  3. 结束负载后再采一次
  4. 可选标定（要你按 Y）：临时写 HFSP=0x40 拉真满速读 15 秒，再写回 0x80 交还 EC

判读：
  - 自动模式 max ≈ 3900 RPM → EC 自动曲线的天花板就是档位 7（本机实测 3924 RPM），
    也就是说"原生/自带"根本到不了 0x40 的满速（4946 RPM）。
  - 自动模式 max ≈ 4900 RPM → 固件自己就能到满速，问题只在触发条件。

参考（本机历史实测，档位 → 中位转速）：
  0x00=0 RPM   0x01=2029  0x02=2362  0x03=2600  0x04=2964
  0x05=3185    0x06=3170  0x07=3924  0x40=4946(满速)  0x80=EC 自动

依赖：已装 CpuPLFix —— 借它的 TSCtl 驱动做 I/O 端口读写（**不用装 PawnIO**），
      MSR 也走它。需要管理员（驱动设备只对管理员开放）。

来源：2026-10-18 为回答"原生能不能到满速"而写。EC 协议与旧风扇工具
      ThinkPad-T14s-Gen4-FanControl/src/fan_control.py 完全一致（同一套实测值）。
"""
import importlib.util
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get('APPDATA') or r'C:\Users\Ting\AppData\Roaming', 'CpuPLFix')
BURN = os.path.join(HERE, '_负载发生器.py')
PY = sys.executable

# ---- TSCtl(ThrottleStop) 驱动的 I/O 端口 IOCTL，见 CpuPLFix README 的 IOCTL 表 ----
IOCTL_PORT_READ  = 0x80006430      # in: u16 port  -> 1/2/4 字节
IOCTL_PORT_WRITE = 0x80006434      # in: u16 port + 值

# ---- ACPI EC 协议与寄存器（与旧风扇工具一致，本机实测）-------------------
CTRL_PORT, DATA_PORT = 0x66, 0x62
FLAG_OBF, FLAG_IBF   = 0x01, 0x02
CMD_READ, CMD_WRITE  = 0x80, 0x81
EC_FAN     = 0x2F      # HFSP 风扇控制寄存器
EC_FANSW   = 0x31      # 风扇选择，读转速前写 0
EC_RPM_LO  = 0x84
EC_RPM_HI  = 0x85
EC_CPU_TMP = 0x78

LEVEL_RPM = {0x00: 0, 0x01: 2029, 0x02: 2362, 0x03: 2600, 0x04: 2964,
             0x05: 3185, 0x06: 3170, 0x07: 3924, 0x40: 4946}
FULL, AUTO = 0x40, 0x80
STEP = 3
DUR  = int(sys.argv[1]) if len(sys.argv) > 1 else 90


def load_cpf():
    mod_path = os.path.join(BASE, 'CpuPLFix.py')
    if not os.path.isfile(mod_path):
        sys.exit('[X] 找不到 CpuPLFix（%s）—— 先装它，见手册第八节' % BASE)
    sys.path.insert(0, BASE)
    spec = importlib.util.spec_from_file_location('cpf', mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EC(object):
    """ACPI EC 访问 —— 走 TSCtl 驱动的 I/O 端口(0x62/0x66)，协议同旧风扇工具"""

    def __init__(self, cpf):
        self.cpf = cpf

    def pio_read(self, port):
        ok, b = self.cpf.ioctl(IOCTL_PORT_READ, struct.pack('<H', port), 1)
        if not ok:
            raise IOError('I/O 端口读 0x%04X 失败' % port)
        return b[0] & 0xFF

    def pio_write(self, port, val):
        ok, _ = self.cpf.ioctl(IOCTL_PORT_WRITE,
                               struct.pack('<H', port) + struct.pack('<B', val & 0xFF), 0)
        if not ok:
            raise IOError('I/O 端口写 0x%04X 失败' % port)

    def _wait(self, want_set, want_clear, timeout_ms=1000):
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            st = self.pio_read(CTRL_PORT)
            if (st & want_set) == want_set and (st & want_clear) == 0:
                return True
            time.sleep(0.002)
        return False

    def _drain(self):
        for _ in range(10):
            if not (self.pio_read(CTRL_PORT) & FLAG_OBF):
                break
            self.pio_read(DATA_PORT)
            time.sleep(0.001)

    def read(self, offset):
        self._drain()
        if not self._wait(0, FLAG_IBF | FLAG_OBF):
            raise IOError('EC 状态超时(读 0x%02X)' % offset)
        self.pio_write(CTRL_PORT, CMD_READ)
        if not self._wait(0, FLAG_IBF):
            raise IOError('EC IBF 超时(读 0x%02X)' % offset)
        self.pio_write(DATA_PORT, offset)
        if not self._wait(0, FLAG_IBF):
            raise IOError('EC IBF 超时(读 0x%02X)' % offset)
        return self.pio_read(DATA_PORT)

    def write(self, offset, value):
        self._drain()
        if not self._wait(0, FLAG_IBF | FLAG_OBF):
            raise IOError('EC 状态超时(写 0x%02X)' % offset)
        self.pio_write(CTRL_PORT, CMD_WRITE)
        if not self._wait(0, FLAG_IBF):
            raise IOError('EC IBF 超时(写 0x%02X)' % offset)
        self.pio_write(DATA_PORT, offset)
        if not self._wait(0, FLAG_IBF):
            raise IOError('EC IBF 超时(写 0x%02X)' % offset)
        self.pio_write(DATA_PORT, value & 0xFF)
        if not self._wait(0, FLAG_IBF):
            raise IOError('EC IBF 超时(写 0x%02X)' % offset)

    def snapshot(self):
        """返回 (EC 温度, HFSP, 转速)。写 0x31=0 只是选主风扇，旧工具在自动模式下也这么读。"""
        self.write(EC_FANSW, 0x00)
        t = self.read(EC_CPU_TMP)
        f = self.read(EC_FAN)
        lo = self.read(EC_RPM_LO)
        hi = self.read(EC_RPM_HI)
        return t, f, (hi << 8) | lo


def main():
    cpf = load_cpf()
    if not cpf.is_admin():
        sys.exit('[X] 需要管理员权限（TSCtl 驱动设备只对管理员开放）。\n'
                 '    请双击同目录的 12-风扇天花板实测.bat —— 它会自己弹 UAC 提权再跑。')
    if not os.path.isfile(BURN):
        sys.exit('[X] 同目录缺 _负载发生器.py')
    if not cpf.ensure_driver(verbose=False):
        sys.exit('[X] TSCtl 驱动打不开（CpuPLFix 没装好？见手册第八节）')

    LOG = []
    OUT = os.path.join(HERE, '风扇天花板-%s.txt' % time.strftime('%Y%m%d-%H%M%S'))

    def say(s=''):
        LOG.append(s)
        print(s, flush=True)

    say('=== 风扇天花板实测  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    ec = EC(cpf)

    # ---- 端口通道自检 ----------------------------------------------------
    try:
        st = ec.pio_read(CTRL_PORT)
        say('EC 控制端口 0x66 读了 0x%02X —— I/O 端口通道正常' % st)
    except Exception as exc:
        say('[X] I/O 端口读写失败: %s' % exc)
        say('    可能原因: 不是管理员 / TSCtl 驱动没起来 / 驱动不允许端口 I/O')
        open(OUT, 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
        sys.exit(1)

    # ---- MSR 侧（温度/频率/功耗）----------------------------------------
    u = cpf.rdmsr(0x606)
    E_UNIT = 1.0 / (1 << ((u >> 8) & 0x1F)) if u else 6.104e-05
    ta = cpf.rdmsr(0x1A2)
    TJMAX = ((ta >> 16) & 0xFF) if ta else 100
    if not (50 <= TJMAX <= 120):
        TJMAX = 100

    def cpu_temp():
        return TJMAX - (((cpf.rdmsr(0x1B1) or 0) >> 16) & 0x7F)

    def ratio():
        return ((cpf.rdmsr(cpf.MSR_PERF_STATUS) or 0) >> 8) & 0xFF

    def energy():
        v = cpf.rdmsr(0x611)
        return (v & 0xFFFFFFFF) if v is not None else None

    say('TjMax=%d°C   energy unit=%.4f uJ/count' % (TJMAX, E_UNIT * 1e6))
    say('（转速参考：档位 7 = 3924 RPM，0x40 满速 = 4946 RPM）')
    say()

    # ---- 1. 空闲基线 -----------------------------------------------------
    say('=== 空闲基线 ===')
    for _ in range(3):
        try:
            t, f, r = ec.snapshot()
            say('  EC温度=%3d°C  HFSP=0x%02X  RPM=%4d' % (t, f, r))
        except Exception as exc:
            say('  读取失败: %s' % exc)
        time.sleep(2)

    # ---- 2. 满载采样（不动风扇）-----------------------------------------
    NCPU = os.cpu_count() or 8
    say()
    say('=== 满载 %d 秒（%d 线程，全程保持 EC 自动，不写风扇寄存器）===' % (DUR, NCPU))
    say('%5s %8s %8s %8s %8s %7s %8s' % ('t(s)', '频率', 'CPU°C', 'EC°C', 'HFSP', 'RPM', '功耗W'))
    procs = [subprocess.Popen([PY, BURN, str(DUR + 15)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for _ in range(NCPU)]
    time.sleep(1.5)

    rows = []
    e_prev, t_prev, t_start, n = energy(), time.time(), time.time(), 0
    try:
        while time.time() - t_start < DUR + 1:
            time.sleep(STEP)
            n += 1
            e_now, t_now = energy(), time.time()
            w = -1.0
            if e_now is not None and e_prev is not None:
                w = ((e_now - e_prev) & 0xFFFFFFFF) * E_UNIT / max(1e-6, t_now - t_prev)
            e_prev, t_prev = e_now, t_now
            r = ratio()
            ct = cpu_temp()
            try:
                et, f, rpm = ec.snapshot()
            except Exception as exc:
                say('%5d  （EC 读取失败: %s）' % (n * STEP, exc))
                continue
            rows.append((n * STEP, r, ct, et, f, rpm))
            say('%5d %6.2fGHz %7d°C %7d°C   0x%02X %6d %8.1f'
                % (n * STEP, r / 10.0, ct, et, f, rpm, w))
    finally:
        for pr in procs:
            try:
                pr.kill()
                pr.wait()
            except Exception:
                pass

    # ---- 3. 负载停下后的回落 --------------------------------------------
    say()
    say('=== 停载后 ===')
    for _ in range(3):
        time.sleep(2)
        try:
            et, f, rpm = ec.snapshot()
            say('  EC温度=%3d°C  HFSP=0x%02X  RPM=%4d' % (et, f, rpm))
        except Exception as exc:
            say('  读取失败: %s' % exc)

    # ---- 汇总 -----------------------------------------------------------
    auto_max = max([r[5] for r in rows], default=0)
    hot = [r for r in rows if r[2] >= 80] or rows
    hot_max = max([r[5] for r in hot], default=0)
    hfsp_seen = sorted(set(r[4] for r in rows))
    tmax = max([r[2] for r in rows], default=0)
    say()
    say('=== 汇总（EC 自动模式）===')
    say('  满载 CPU 最高温度 : %d°C' % tmax)
    say('  满载 HFSP 取值     : %s' % ' '.join('0x%02X' % v for v in hfsp_seen))
    say('  满载最高转速       : %d RPM' % auto_max)
    if hot:
        say('  温度≥80°C 时最高转速: %d RPM' % hot_max)

    # ---- 4. 可选标定：写 0x40 读真满速 -----------------------------------
    calib_max = 0
    say()
    say('=== 可选标定：临时写 HFSP=0x40 把风扇拉到真满速 15 秒，然后写回 0x80 交还 EC ===')
    try:
        ans = input('  要标定吗？（会明显变吵，约 20 秒）[y/N] ').strip().lower()
    except EOFError:
        ans = ''
    if ans == 'y':
        try:
            ec.write(EC_FAN, FULL)
            say('  已写 0x40，等风扇升速 ...')
            for i in range(6):
                time.sleep(2.5)
                et, f, rpm = ec.snapshot()
                calib_max = max(calib_max, rpm)
                say('    HFSP=0x%02X  RPM=%4d' % (f, rpm))
        except Exception as exc:
            say('  标定中出错: %s' % exc)
        finally:
            try:
                ec.write(EC_FAN, AUTO)
                say('  已写回 0x80，交还 EC 自动')
            except Exception as exc:
                say('  [!] 写回 0x80 失败: %s —— 风扇可能停在满速，重启即可恢复' % exc)
    else:
        say('  已跳过（未写任何风扇寄存器）')

    # ---- 结论 -----------------------------------------------------------
    say()
    say('=== 结论 ===')
    lv, lvrpm = min(LEVEL_RPM.items(), key=lambda kv: abs(kv[1] - auto_max))
    say('  自动模式满载最高 %d RPM ≈ 档位 0x%02X（该档实测 %d RPM）' % (auto_max, lv, lvrpm))
    if calib_max:
        say('  写 0x40 后实测最高 %d RPM（本机风扇真满速）' % calib_max)
        gap = calib_max - auto_max
        if gap <= 200:
            say('  → 固件自己就能到满速（只低 %d RPM）：问题不在天花板，在触发条件' % gap)
        else:
            say('  → 自动曲线比真满速低 %d RPM：EC 自动模式到不了满速，要满速只能直接写 EC(0x40)' % gap)
    else:
        if lv == 0x07:
            say('  → 顶到档位 7，与"自动曲线天花板就是档位 7（3924 RPM）"一致')
        elif lv == 0x40:
            say('  → 自动模式已经跑到满速档')
        say('  （没做标定，真满速参考值用历史实测 4946 RPM）')

    open(OUT, 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
    print()
    print('报告已写入: %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
