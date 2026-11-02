"""读 PROCHOT / 限频原因的 MSR —— 判定"0.4GHz 锁频是不是 BD PROCHOT 引起的"。

需要管理员（要打开 TSCtl 驱动设备）。结果写 msr_prochot.txt，同时打印。

关键寄存器（Intel SDM / Framework issue #269 的同一组签名）：
  0x64F CORE_PERF_LIMIT_REASONS  bit0 PROCHOT 正在拉低; bit16 PROCHOT 曾经触发
  0x19C IA32_THERM_STATUS        bit2 PROCHOT 正在拉低; bit3 PROCHOT 曾经触发
  0x1B1 PKG_THERM_STATUS         同上, 封装级
  0x1FC MSR_POWER_CTL            bit0 = BD PROCHOT 总开关
  0x198 IA32_PERF_STATUS         bit8-15 = 当前倍频（4 = 400MHz）
  0x610 PKG_POWER_LIMIT          PL1/PL2
"""
import ctypes
import struct
import sys
import os

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = ctypes.c_void_p
k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
k32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                ctypes.c_void_p]

IOCTL_RDMSR = 0x80006448
OUT = []


def say(s=""):
    OUT.append(s)
    print(s)


def open_dev():
    for name in (r"\\.\TSCtl", r"\\.\TSCtl.sys"):
        h = k32.CreateFileW(name, 0xC0000000, 3, None, 3, 0, None)
        if h and h != ctypes.c_void_p(-1).value:
            return h
    return None


def rdmsr(h, reg):
    ib = ctypes.create_string_buffer(struct.pack("<I", reg), 4)
    ob = ctypes.create_string_buffer(8)
    ret = ctypes.c_uint32(0)
    ok = k32.DeviceIoControl(h, IOCTL_RDMSR, ib, 4, ob, 8, ctypes.byref(ret), None)
    return struct.unpack("<Q", ob.raw[:8])[0] if ok else None


def bits(v, upto=32):
    return [i for i in range(upto) if (v >> i) & 1]


def main():
    h = open_dev()
    if not h:
        say("打不开 TSCtl 设备 —— 需要管理员权限运行")
        return 1

    names_64f = {
        0: "PROCHOT# 正在拉低（外部信号要 CPU 降频）",
        1: "Thermal 热事件",
        2: "Power Limit PL1",
        3: "Current Limit 电流限制",
        4: "Core/ratio 限制",
        5: "VR 稳压器温度告警",
        6: "VR 管理/最大睿频",
        7: "电气设计点/其他",
        8: "PL4 限制",
        9: "3D 限制",
    }
    names_log = {16: "PROCHOT 曾经触发", 17: "Thermal 曾经触发", 18: "PL1 曾经触发",
                 19: "电流限制曾经触发", 20: "Core 限制曾经触发", 21: "VR 温度曾经触发",
                 22: "VR 管理曾经触发", 23: "其他限制曾经触发", 24: "PL4 曾经触发",
                 27: "PL2 曾经触发"}

    say("=" * 72)
    say("PROCHOT / 限频原因 MSR 快照")
    say("=" * 72)

    v = rdmsr(h, 0x64F)
    say("\n[0x64F] CORE_PERF_LIMIT_REASONS = %s" % ("None" if v is None else hex(v)))
    if v is not None:
        act = [names_64f.get(b, "bit%d" % b) for b in bits(v, 16)]
        log = [names_log.get(b, "bit%d(log)" % b) for b in bits(v >> 16, 16) if (b + 16) in names_log]
        say("   活跃中: " + ("、".join(act) if act else "（无）"))
        say("   历史日志: " + ("、".join(log) if log else "（无）"))

    v = rdmsr(h, 0x19C)
    say("\n[0x19C] IA32_THERM_STATUS = %s" % ("None" if v is None else hex(v)))
    if v is not None:
        say("   bit0 Thermal Status      = %d" % ((v >> 0) & 1))
        say("   bit1 Thermal Log         = %d" % ((v >> 1) & 1))
        say("   bit2 PROCHOT# 正在拉低   = %d   <<<< 关键" % ((v >> 2) & 1))
        say("   bit3 PROCHOT# 曾经触发   = %d   <<<< 关键" % ((v >> 3) & 1))
        say("   bit4 Critical Temp       = %d" % ((v >> 4) & 1))
        readout = (v >> 16) & 0x7F
        say("   温度读数 = %d  (相对 TjMax，越小越热)" % readout)

    v = rdmsr(h, 0x1B1)
    say("\n[0x1B1] IA32_PACKAGE_THERM_STATUS = %s" % ("None" if v is None else hex(v)))
    if v is not None:
        say("   bit0 PKG Thermal Status  = %d" % ((v >> 0) & 1))
        say("   bit1 PKG Thermal Log     = %d" % ((v >> 1) & 1))
        say("   bit2 PROCHOT# 正在拉低   = %d   <<<< 关键" % ((v >> 2) & 1))
        say("   bit3 PROCHOT# 曾经触发   = %d   <<<< 关键" % ((v >> 3) & 1))

    v = rdmsr(h, 0x1FC)
    say("\n[0x1FC] MSR_POWER_CTL = %s" % ("None" if v is None else hex(v)))
    if v is not None:
        say("   bit0 BD PROCHOT 总开关   = %d  (1 = 允许外部拉低 PROCHOT 降频)" % (v & 1))

    v = rdmsr(h, 0x198)
    say("\n[0x198] IA32_PERF_STATUS = %s" % ("None" if v is None else hex(v)))
    if v is not None:
        ratio = (v >> 8) & 0xFF
        say("   当前倍频 = %d  =>  %d MHz" % (ratio, ratio * 100))
        say("   (4 = 400MHz 锁频; 正常空载 8~20, 睿频 40+)" % ())

    v = rdmsr(h, 0x610)
    say("\n[0x610] PKG_POWER_LIMIT = %s" % ("None" if v is None else hex(v)))
    if v is not None:
        pl1 = (v & 0x7FFF) / 8.0
        pl2 = ((v >> 32) & 0x7FFF) / 8.0
        say("   PL1 = %.1f W (enable=%d)   PL2 = %.1f W (enable=%d)"
            % (pl1, (v >> 15) & 1, pl2, (v >> 47) & 1))

    say("\n" + "=" * 72)
    say("判读：bit2=1 表示此刻正被外部拉低（温度不高却在降频）→ BD PROCHOT 实锤;")
    say("      bit3=1 而 bit2=0 表示本次开机后曾经触发过（历史证据）。")
    say("=" * 72)

    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "msr_prochot.txt")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(OUT))
    print("\n-> " + dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
