# -*- coding: utf-8 -*-
"""13-EC诊断.py —— EC(嵌入式控制器)全面体检 / 电池充电阈值养护 / 未知位监视

怎么跑：双击同目录 `13-EC诊断.bat`（会自己弹 UAC 提权）
        或在管理员窗口里  python "13-EC诊断.py" <子命令>

子命令：
  dump                全寄存器体检（0x00~0xFF 逐个读+命名+解码），结果写 `EC体检-时间戳.txt`
  temp                只看 8 个温度传感器（TMP0/GPUT/TTS1~TTS4/ATMX/HWAT），采样 10 次
  fan                 看风扇：0x2F 档位、0x84/0x85 转速；并验证"不写 0x31 能不能读到转速"
  batt                看电池充电阈值（BSC0/BEC0/BSC1/BEC1）+ ACPI 判据 + 系统电量
  batt set S E        设充电阈值：低于 S% 才充、充到 E% 停（例：batt set 75 80）
  batt off            取消充电阈值（写 0/0 = 充到 100%）
  batt autostart on|off   开机自动套用（**建议不要开**：这机器用联想自带软件管阈值，开了会互相打架）
  watch [间隔秒]      只关心"会变的/还没搞懂的"寄存器，变了才打印（Ctrl+C 结束）
  throttle [秒] [--load]  抓"被锁频的那一瞬间"：倍频 + 功耗墙 + PROCHOT + EC 标志一起记（只读）
  smi                 （只读）把固件 SMI 邮箱看一遍，用于研究，不写任何东西

为什么可信：寄存器名与位布局不是猜的 —— 是从本机 ACPI 固件反汇编出来的。
  DSDT 里 `OperationRegion (ECOR, EmbeddedControl, 0, 0x100)` 把整个 EC 空间
  定名成一个个 Field(见下表)；本脚本的表就是照抄那些名字。
  电池阈值那条线还对照了 Linux thinkpad_acpi 的 GET_START/SET_START
  = "BCTG"/"BCCS"（本机 DSDT 里同样存在，最终落到 SMI 0x14/0x05）。

依赖：已装 CpuPLFix —— 借它的 TSCtl 驱动做 I/O 端口读写 + 物理内存读写。
      需要管理员（驱动设备只对管理员开放）。

安全：只写"确认过语义"的偏移：0x2F(风扇档)、0xB0/0xB1(电池 0 阈值)。
      0x31 一律**不写**（见 fan 子命令的说明）；0x81(HIID) 绝不写（那是 OS 读电池的窗口选择器）。
"""
import importlib.util
import os
import struct
import sys
import time
from collections import Counter, deque

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_cpf_base():
    """找 CpuPLFix 目录。注意：开机计划任务以 SYSTEM 身份跑，
    那时 %APPDATA% 指向 systemprofile，不是用户的 —— 所以要有兜底。"""
    import glob
    cands = []
    a = os.environ.get('APPDATA')
    if a:
        cands.append(os.path.join(a, 'CpuPLFix'))
    cands += sorted(glob.glob(r'C:\Users\*\AppData\Roaming\CpuPLFix'))
    for c in cands:
        if os.path.isfile(os.path.join(c, 'CpuPLFix.py')):
            return c
    return cands[0] if cands else r'C:\Users\Ting\AppData\Roaming\CpuPLFix'


BASE = _find_cpf_base()
MYNAME = os.path.basename(os.path.abspath(__file__)).replace('.py', '')
THRESH_JSON = os.path.join(HERE, 'EC电池阈值.json')      # batt autostart 用
TASK_NAME = 'EcBattThreshold'

# =====================================================================
# 1. EC 寄存器地图（来源：本机 DSDT 反汇编，OperationRegion ECOR, 0x00~0xFF）
#    名字 = 固件自己给 Field 起的名字；含义 = 结合 ACPI 用法与 ThinkPad EC 惯例推断
# =====================================================================

# 位域：offset -> [(名字, 起始位, 位宽)]
BITFIELDS = {
    0x00: [('HDBM', 0), ('HFNE', 3), ('HLDM', 6)],
    0x01: [('BBLS', 0), ('BTCM', 1), ('HBPR', 5), ('BTPC', 6)],
    0x02: [('HDUE', 0), ('SNLK', 5)],
    0x03: [('HAUM', 5, 2)],
    0x05: [('HSPA', 0)],
    0x0D: [('CALM', 4)],
    0x0E: [('HFNS', 0, 2)],
    0x0F: [('NULS', 6), ('ANTT', 7)],
    0x26: [('HANA', 2, 2)],
    0x28: [('SKEM', 1), ('DIST', 2)],
    0x30: [('HMUT', 6)],
    0x31: [('HUWB', 2), ('VPON', 6), ('VRST', 7)],
    0x32: [('HWPM', 0), ('HWLB', 1), ('HWLO', 2), ('HWDK', 3),
           ('HWFN', 4), ('HWBT', 5), ('HWRI', 6), ('HWBU', 7)],
    0x33: [('HWLU', 0)],
    0x34: [('PIBS', 3), ('HPLO', 7)],
    0x35: [('FANE', 4)],
    0x38: [('HB0S', 0, 7), ('HB0A', 7)],
    0x39: [('HB1S', 0, 7), ('HB1A', 7)],
    0x3A: [('HCMU', 0), ('OVRQ', 3), ('DCBD', 4), ('DCWL', 5), ('DCWW', 6), ('HB1I', 7)],
    0x3B: [('KBLT', 1), ('BTPW', 2), ('FNKC', 3), ('HUBS', 4),
           ('BDPW', 5), ('BDDT', 6), ('HUBB', 7)],
    0x46: [('BTWK', 1), ('HPLD', 2), ('HPAC', 4), ('BTST', 5), ('PSST', 6)],
    0x47: [('HPBU', 0), ('PSS2', 1), ('HBID', 2), ('PWSE', 6), ('HPNF', 7)],
    0x48: [('GSTS', 1), ('HLBU', 4), ('DOCD', 5), ('HCBL', 6)],
    0x49: [('SLUL', 0), ('ACAT', 2), ('ELNK', 7)],
    0x51: [('HMDN', 7)],
    0x7F: [('CFWU', 0), ('QCON', 1), ('CFWL', 7)],
    0x88: [('TSL0', 0, 7), ('TSR0', 7)],
    0x89: [('TSL1', 0, 7), ('TSR1', 7)],
    0x8A: [('TSL2', 0, 7), ('TSR2', 7)],
    0x8B: [('TSL3', 0, 7), ('TSR3', 7)],
    0x8C: [('CPUI', 0, 4), ('GPUI', 4, 4)],
    0x8D: [('HDAA', 0, 3), ('HDAB', 3, 3), ('HDAC', 6, 2)],
    0xBB: [('BPL1', 0), ('BPL2', 1), ('BP1S', 2), ('BP2S', 3), ('BLPL', 4)],
    0xC4: [('CQLS', 0), ('SMCS', 1)],
    0xC6: [('DPRL', 0), ('TSN0', 7)],
    0xCF: [('ESFL', 4), ('ESLS', 5), ('ESLP', 6)],
    0xED: [('HDDD', 4)],
}

# 单字节寄存器
BYTE_NAMES = {
    0x06: 'HSUN', 0x07: 'HSRP', 0x0C: 'HLCL', 0x23: 'HANT', 0x2A: 'HATR',
    0x2B: 'HT0H', 0x2C: 'HT0L', 0x2D: 'HT1H', 0x2E: 'HT1L', 0x2F: 'HFSP',
    0x4C: 'HTMH', 0x4D: 'HTML', 0x50: 'HMPR', 0x81: 'HIID', 0x83: 'HFNI',
    0xB0: 'BSC0', 0xB1: 'BEC0', 0xB2: 'BSC1', 0xB3: 'BEC1',
    0xB8: 'HDEM', 0xB9: 'HDES', 0xBC: 'BMSB', 0xBD: 'BLSB', 0xBE: 'BTIM',
    0xC8: 'ATMX', 0xC9: 'HWAT', 0xCB: 'TTCI', 0xCC: 'PWMH', 0xCD: 'PWML',
}
HAM = {o: 'HAM%X' % (o - 0x10) for o in range(0x10, 0x20)}      # HAM0~HAMF

# 16 位
WORD_NAMES = {0x36: 'HWAC', 0x4E: 'HWAK', 0x84: 'HSPD', 0xB4: 'HDEP'}

# 温度类（值 = 摄氏度；0x80 = 固件的"无效"哨兵）
TEMP_NAMES = {0x78: 'TMP0', 0x79: 'GPUT', 0x7A: 'TTS1', 0x7B: 'TTS6',
              0x7C: 'TTS2', 0x7D: 'TTS3', 0x7E: 'TTS4', 0xC8: 'ATMX', 0xC9: 'HWAT'}
TEMPS = {
    0x78: 'TMP0  CPU 温度 (_TMP 用的就是这个)',
    0x79: 'GPUT  GPU 温度 (无独显时 = 0x80 无效)',
    0x7A: 'TTS1', 0x7B: 'TTS6', 0x7C: 'TTS2', 0x7D: 'TTS3', 0x7E: 'TTS4',
    0xC8: 'ATMX  固件内部用 (本机实测恒 0)',
    0xC9: 'HWAT  固件某个常量 (空闲/满载都恰是 65, 不是温度)',
}

# 2026-10-18 实测更正：
#   · 真正会让 \_TZ.THM0._TMP 返回 _HOT/_CRT 的只有 0x89/0x8A/0x8B —— 就是下面这三个。
#   · 0x88(TSL0/TSR0) **不**参与 _TMP 判据（只在 _WAK/_Q40 里被读来挑热表），
#     本机它常驻 0x40(bit6)，属正常，别当"过热"。
TRIP_FLAGS = (0x89, 0x8A, 0x8B)
TRIP_EXTRA = 0x88

# 0xA0~0xAF = SMBus 电池数据窗口（同一批偏移在不同上下文有不同名字，靠 HIID 选择）
SMBUS_WIN = {0xA0: 'SBRC/SBBM/SBDC/SBCH/SBMN/SBDN/VIDA/CTST/CTSC',
             0xA2: 'SBFC/SBMD/SBDV/PIDA/CTET',
             0xA4: 'SBAE/SBCC/SBOM/VIDB',
             0xA6: 'SBRS/SBSI/PIDB',
             0xA8: 'SBAC/SBDT',
             0xAA: 'SBVO/SBSN',
             0xAC: 'SBAF',
             0xAE: 'SBBS'}

# 已知语义、可以写的偏移
WRITABLE = {
    0x2F: 'HFSP 风扇档位 (0x00~0x07 / 0x40 满速 / 0x80 交还 EC)',
    0xB0: 'BSC0 电池 0 起充阈值 %',
    0xB1: 'BEC0 电池 0 停充阈值 %',
}
FORBIDDEN_WRITE = {
    0x31: 'HUWB/VPON/VRST —— 语义未确认，别写（见 fan 说明）',
    0x81: 'HIID —— OS 读电池的窗口选择器，写它会干扰系统读电池',
}

# MSR（"到底谁在压 CPU"排查用；全部只读）
MSR_PERF_STATUS = 0x198        # 当前倍频 = (值>>8)&0xFF，x0.1GHz
MSR_PKG_POWER_LIMIT = 0x610    # 功耗墙（MSR 侧）
MSR_THERM_STATUS = 0x19C       # IA32_THERM_STATUS：bit0 = PROCHOT/热越界
MSR_PKG_THERM_STATUS = 0x1B1   # IA32_PACKAGE_THERM_STATUS：bit0 同上（封装）
MSR_POWER_CTL = 0x1FC          # IA32_POWER_CTL（ThrottleStop 的 "BD PROCHOT" 在这支上；本工具只读不写）

# 固件自己会写 HFSP(0x2F) 的地方（DSDT 反汇编，见 05 §6）：
#   OPTS()  : 性能档==1 时 HFSP = 0        （本机走不到：那是 S1）
#   某个 HKEY 热模式方法: HFSP = 0x07 或 0x80（联想"全速风扇/智能散热"档位）
#   OWAK()  : If (SCRM) HFSP = 0x07         ← 每次唤醒，只要 SCRM 置位就强制 7 档

# =====================================================================
# 2. 载入 CpuPLFix
# =====================================================================

def load_cpf():
    p = os.path.join(BASE, 'CpuPLFix.py')
    if not os.path.isfile(p):
        sys.exit('[X] 找不到 CpuPLFix（%s）—— 先装它，见手册第八节' % BASE)
    spec = importlib.util.spec_from_file_location('cpf', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LOG = []


def say(s=''):
    LOG.append(str(s))
    print(s, flush=True)


def save_report(prefix):
    path = os.path.join(HERE, '%s-%s.txt' % (prefix, time.strftime('%Y%m%d-%H%M%S')))
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG) + '\n')
        print('\n报告已写入: %s' % path)
    except Exception as e:
        print('（报告保存失败: %s，不影响上面结果）' % e)
    LOG.clear()      # 清掉，否则下一个子命令的报告里会带上前面所有输出(踩过)


def open_ec(cpf):
    if not cpf.is_admin():
        sys.exit('[X] 需要管理员权限（TSCtl 驱动设备只对管理员开放）。\n'
                 '    请双击同目录的 13-EC诊断.bat —— 它会自己弹 UAC 提权再跑。')
    if not cpf.ensure_driver(verbose=False):
        sys.exit('[X] TSCtl 驱动打不开（CpuPLFix 没装好？见手册第八节）')
    st = cpf.rd_port(0x66)
    if st is None:
        sys.exit('[X] I/O 端口读失败：不是管理员 / 驱动没起来 / 驱动不允许端口 I/O')
    return st


# =====================================================================
# 3. EC 读（带一致性校验；一次拿锁批量读，减少 EC 占用）
# =====================================================================

DELAYS = (0.004, 0.012, 0.025)


def read_batch(cpf, offsets, tries=2, pace=0.0):
    """批量读。返回 {offset: (值 or None, 是否稳定)}。
    每个偏移连读 tries 次取众数；不一致时标记为不稳定（值仍给出众数）。

    pace = 每读一个偏移后额外等多少秒。**批量扫描一定要给 pace**：
    EC 是慢总线，而系统自己的 ACPI EC 驱动和看守服务都在用同一对端口(0x62/0x66)。
    不加节流地连打几百次，会去抢系统的事务 —— 轻则读到脏数据(事件 13/15)，
    重则把系统的 EC 通道搞乱。全表扫描用 0.025 秒，600 次事务摊到 ~15 秒，稳当得多。
    """
    res = {}
    locked = cpf._ec_lock(8000)
    try:
        for idx, off in enumerate(offsets):
            vals = []
            for i in range(tries):
                vals.append(cpf.ec_read(off, _locked=True, delay=DELAYS[min(i, len(DELAYS) - 1)]))
            good = [v for v in vals if v is not None]
            if not good:
                res[off] = (None, False)
            else:
                v, n = Counter(good).most_common(1)[0]
                res[off] = (v, n == tries)
            if pace and idx + 1 < len(offsets):
                time.sleep(pace)
    finally:
        cpf._ec_unlock(locked)
    return res


def read_one(cpf, off, tries=4):
    return read_batch(cpf, [off], tries=tries)[off]


def write_verify(cpf, off, val):
    """写一个 EC 寄存器并回读校验。返回 (写入是否成功, 回读值)。

    回读走"连读取众数"的 read_batch —— 单读一次可能拿到上一次事务的残留字节。
    """
    locked = cpf._ec_lock(8000)
    try:
        ok = cpf.ec_write(off, val, _locked=True)
    finally:
        cpf._ec_unlock(locked)
    time.sleep(0.03)
    got, _stable = read_batch(cpf, [off], tries=3)[off]
    return ok, got


# =====================================================================
# 4. 解码 / 渲染
# =====================================================================

def bits_str(off, val):
    spec = BITFIELDS.get(off)
    if not spec:
        return ''
    out = []
    for item in spec:
        name, start = item[0], item[1]
        width = item[2] if len(item) > 2 else 1
        if width == 1:
            if val & (1 << start):
                out.append(name + '=1')
        else:
            v = (val >> start) & ((1 << width) - 1)
            if v:
                out.append('%s=%d' % (name, v))
    return ' '.join(out)


def reg_name(off):
    if off in TEMP_NAMES:
        return TEMP_NAMES[off]
    if off == 0x85:
        return 'HSPD_HI'
    if off in WORD_NAMES:
        return WORD_NAMES[off]
    if off in BYTE_NAMES:
        return BYTE_NAMES[off]
    if off in HAM:
        return HAM[off]
    if off in SMBUS_WIN:
        return SMBUS_WIN[off].split('/')[0]
    if off in BITFIELDS:
        return BITFIELDS[off][0][0]
    return '(未命名)'


def describe(off, val):
    """给一个值加人话注解"""
    if val is None:
        return '读取失败'
    if off in TEMPS:
        if val == 0x80:
            return '0x80 = 固件的"无效"哨兵'
        if off == 0xC8:
            return '%d（固件内部用）' % val
        if off == 0xC9:
            return '%d（常量，不是温度）' % val
        return '%d°C' % val
    if off == 0x2F:
        if val == 0x80:
            return 'EC 自动（原生平线曲线）'
        if val >= 0x40:
            return '满速'
        if 1 <= val <= 7:
            return '%d 档 (标称约 %d RPM)' % (val, {1: 2480, 2: 2610, 3: 2720, 4: 3190,
                                                  5: 3185, 6: 3490, 7: 3880}[val])
        return '0 = 停转'
    if off == 0xB0:
        return '起充阈值 %d%%（低于它才开始充）' % val if val else '0 = 不限制'
    if off == 0xB1:
        return '停充阈值 %d%%' % val if val else '0 = 默认（充到 100%）'
    if off == 0x84:
        return '低字节（16 位 HSPD 的低 8 位）'
    if off == 0x85:
        return '高字节（16 位 HSPD 的高 8 位）'
    if off in (0x88, 0x89, 0x8A, 0x8B):
        tsl, tsr = val & 0x7F, (val >> 7) & 1
        s = 'TSL=%s' % bin(tsl) if tsl else 'TSL=0'
        if tsr:
            s += '  ** TSR=1 已触发 **'
        return s
    if off == 0x84:
        return '%d RPM' % val
    b = bits_str(off, val)
    return b


def flag_names(off, val):
    """只列置位的位名，用于 watch"""
    spec = BITFIELDS.get(off)
    if not spec:
        return ''
    out = []
    for item in spec:
        name, start = item[0], item[1]
        width = item[2] if len(item) > 2 else 1
        if width == 1:
            if val & (1 << start):
                out.append(name)
        else:
            v = (val >> start) & ((1 << width) - 1)
            if v:
                out.append('%s=%d' % (name, v))
    return ','.join(out)


# ---- EC 固件版本：2026-10-18 从全表里发现的 ----
# 0xF0~0xF7 是 8 个可打印 ASCII = "N3PHT02W"，和 00 文档里记的 EC 固件完全一致。
# 所以 EC 固件版本可以纯软件读出来（以前只能靠 BIOS 界面/联想工具）。
EC_VER_OFFSETS = tuple(range(0xF0, 0xF8))
EC_VER_BASELINE = 'N3PHT02W'      # 本机 2026-10-18 实测值


def ec_version_text(res):
    """从"批量读结果"里拼出 EC 固件版本；拼不出可打印 ASCII 就返回 None。"""
    s = ''
    for o in EC_VER_OFFSETS:
        v = res.get(o, (None, False))[0]
        if v is None or not (32 <= v < 127):
            return None
        s += chr(v)
    return s


# =====================================================================
# 5. 子命令
# =====================================================================

def findings(res):
    """按实测值列出"值得留意"的项（dump 末尾自动打印）"""
    out = []

    def v(o):
        return res.get(o, (None, False))[0]

    t = v(0x78)
    if t is None:
        out.append('✗ 0x78 CPU 温度读不到（EC 忙，或通信有问题）')
    elif t >= 95:
        out.append('✗ CPU 温度 %d°C ≥ 95°C —— 已经在温控线附近，散热要查' % t)
    elif t >= 85:
        out.append('! CPU 温度 %d°C 偏高' % t)

    trip = [o for o in TRIP_FLAGS if (v(o) or 0) & 0x7F]
    if trip:
        out.append('✗ 固件热触发标志 %s 非零 —— 固件已判"过热"，会强制休眠(S4)'
                   % ' '.join('0x%02X' % o for o in trip))
    tsr = [o for o in TRIP_FLAGS if (v(o) or 0) & 0x80]
    if tsr and not trip:
        out.append('! TSR 触发位（bit7）在 %s 置位但低 7 位是 0 —— 再读一轮确认（可能是垃圾字节）'
                   % ' '.join('0x%02X' % o for o in tsr))
    e88 = v(TRIP_EXTRA)
    if e88 is not None and (e88 & 0x7F):
        out.append('· 0x88 TSL0=0x%02X 有位置起来 —— **这不参与 _TMP 判据**，本机常驻 0x40(bit6) 属正常，'
                   '只有 _WAK/_Q40 会拿它挑热表' % e88)
    elif e88 is None:
        out.append('! 0x88 读不到')

    b0, b1 = v(0xB0), v(0xB1)
    if b0 is None or b1 is None:
        out.append('✗ 电池充电阈值(0xB0/0xB1)读不到')
    elif b0 == 0 and b1 == 0:
        out.append('! 电池充电阈值 = 0/0（EC 里没设）。如果你已经在联想软件里设过阈值而这里还是 0/0，'
                   '请把这份报告发回来 —— 那说明我这份寄存器映射有偏差')
    else:
        out.append('· 电池充电阈值 = %s  ←─ 拿它和联想软件里显示的数字核对：一致就证明这份映射对'
                   % batt_line(b0, b1))

    s31 = v(0x31)
    if s31:
        out.append('! 0x31 = 0x%02X（%s）非 0 —— 本工具不写它，说明是别的程序/固件写的'
                   % (s31, bits_str(0x31, s31)))

    hb0 = v(0x38)
    if hb0 is not None:
        if (hb0 & 0x7F) == 0x07:
            out.append('✗ 0x38 HB0S=0x07 → 固件认为"电池 0 不存在"')
        elif not (hb0 & 0x80):
            out.append('! 0x38 HB0A=0 → 电池 0 未激活')

    hpac = v(0x46)
    if hpac is not None and not (hpac & 0x10):
        out.append('· 0x46 HPAC=0 → 固件认为适配器未接（电池模式）')

    gp = v(0x79)
    if gp is not None and gp != 0x80:
        out.append('· 0x79 GPUT = %d°C（本来以为无独显时恒 0x80，这值得看一眼）' % gp)

    h4 = v(0x14)
    if h4 is not None and (h4 & 0x10):
        out.append('· 0x14 HAM4 bit4=1 → 电池低电量告警点(`_BTP`)已设（正常，系统设的）')

    ver = ec_version_text(res)
    if ver is None:
        out.append('! EC 固件版本(0xF0~0xF7) 读不出来 —— 这几个字节不是可打印 ASCII 了')
    elif ver != EC_VER_BASELINE:
        out.append('! EC 固件版本读到 %s，基线是 %s —— 固件被换过 / EC 被重置过？'
                   % (ver, EC_VER_BASELINE))

    if not out:
        out.append('没发现异常')
    return out


def cmd_dump(cpf, skip_smbus=True):
    say('=== EC 全寄存器体检  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say('EC 空间: 0x00~0xFF (DSDT: OperationRegion ECOR, EmbeddedControl, 0, 0x100)')
    say('协议: 命令口 0x66 / 数据口 0x62, 0x80=读 0x81=写；本工具全程持 Global\\CpuPLFix-EC 互斥体')
    say()

    offs = [o for o in range(0x100) if not (skip_smbus and 0xA0 <= o <= 0xAF)]
    say('注意: 这一遍要读 %d 个寄存器（每个 2 次 ≈ %d 次 EC 事务），中途带节流，约 15 秒。'
        % (len(offs), len(offs) * 2))
    say('      跑的时候别同时做别的事（EC 是系统共用的慢总线，见 03 §4）。')
    say()
    t0 = time.time()
    res = read_batch(cpf, offs, tries=2, pace=0.025)
    say('（%d 个寄存器，每个连读 2 次，用时 %.1f 秒）' % (len(offs), time.time() - t0))
    say()

    # 分组打印
    groups = [
        ('温度 / 热', [0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0xC8, 0xC9]),
        ('热触发标志 (固件强制休眠判据)', [0x88, 0x89, 0x8A, 0x8B]),
        ('风扇', [0x2F, 0x84, 0x85, 0x35, 0xCC, 0xCD, 0x30]),
        ('电池', [0x38, 0x39, 0xB0, 0xB1, 0xB2, 0xB3, 0xBB, 0xBC, 0xBD, 0xBE,
                  0xB4, 0xB8, 0xB9, 0xC4, 0xC6]),
        ('电源 / 适配器 / AC', [0x36, 0x3A, 0x3B, 0x46, 0x47, 0x48, 0x49, 0x32, 0x33]),
        ('键盘 / 指示灯 / 其它 H8 状态', [0x00, 0x01, 0x02, 0x03, 0x05, 0x06, 0x07,
                                          0x0C, 0x0D, 0x0E, 0x0F, 0x23, 0x26, 0x28,
                                          0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x31, 0x34,
                                          0x4C, 0x4D, 0x4E, 0x50, 0x51, 0x7F, 0x81,
                                          0x83, 0x8C, 0x8D, 0xCF, 0xED]),
    ]
    done = set()
    for title, lst in groups:
        say('---- %s ----' % title)
        for off in lst:
            if off not in res:
                continue
            done.add(off)
            val, stable = res[off]
            vs = '--' if val is None else '0x%02X' % val
            mark = '' if stable else '  [!不稳]'
            say('  0x%02X  %-5s %-22s %s%s' % (off, vs, reg_name(off), describe(off, val), mark))
        say()

    rest = [o for o in offs if o not in done]
    say('---- 其余（0x00~0xFF 里上面没列到的） ----')
    line = []
    for off in rest:
        val, stable = res[off]
        vs = '--' if val is None else '%02X' % val
        line.append('%02X:%s%s' % (off, vs, '' if stable else '*'))
    for i in range(0, len(line), 8):
        say('  ' + '  '.join(line[i:i + 8]))
    say('  （* = 两次读不一致，可能是动态值或 EC 忙）')
    say()

    # 结论
    say('---- 结论 ----')
    ver = ec_version_text(res)
    say('  EC 固件版本 (0xF0~0xF7)  : %s%s'
        % (ver or '读不到',
           '' if (ver is None or ver == EC_VER_BASELINE) else '   [!] 基线是 %s' % EC_VER_BASELINE))
    t0v = res.get(0x78, (None, False))[0]
    say('  CPU 温度 (0x78 TMP0)      : %s' % ('--' if t0v is None else '%d°C' % t0v))
    others = []
    for o in (0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0xC9):
        v = res.get(o, (None, False))[0]
        if v is not None and v != 0x80:
            others.append('0x%02X=%d°C' % (o, v))
    say('  其它温度传感器             : %s' % (' '.join(others) or '（都无效/读不到）'))
    gp = res.get(0x79, (None, False))[0]
    say('  GPU 温度 (0x79 GPUT)      : %s' % ('0x80 = 本机无独显传感器' if gp == 0x80 else (gp if gp is None else '%d°C' % gp)))
    trip = []
    for o in TRIP_FLAGS:
        v = res.get(o, (None, False))[0]
        if v is not None and (v & 0x7F):
            trip.append('0x%02X=0x%02X(%s)' % (o, v, bits_str(o, v)))
    say('  固件热触发标志(会强制休眠) : %s' % (' '.join(trip) if trip else '全 0（正常）'))
    v88 = res.get(TRIP_EXTRA, (None, False))[0]
    say('  0x88 TSL0(不参与该判据)     : %s'
        % ('0x%02X %s（本机常驻 0x40/bit6，正常）' % (v88, bits_str(0x88, v88))
           if v88 is not None else '读不到'))
    say('  风扇档位 (0x2F HFSP)      : %s' % describe(0x2F, res.get(0x2F, (None, False))[0]))
    lo, hi = res.get(0x84, (None, False))[0], res.get(0x85, (None, False))[0]
    say('  风扇转速 (0x84/0x85 HSPD) : %s'
        % ('%d RPM' % ((hi << 8) | lo) if lo is not None and hi is not None else '读不到'))
    b0 = res.get(0xB0, (None, False))[0]
    b1 = res.get(0xB1, (None, False))[0]
    say('  电池 0 充电阈值           : %s' % batt_line(b0, b1))
    say('  0x31 (HUWB/VPON/VRST)     : %s' % describe(0x31, res.get(0x31, (None, False))[0]))
    say()
    say('---- 发现 ----')
    for line in findings(res):
        say('  ' + line)
    say()


def batt_line(start, stop):
    if start is None or stop is None:
        return '读不到'
    if start == 0 and stop == 0:
        return '0 / 0 = 没设（充到 100%）'
    active = (1 <= start <= 100) and (1 <= stop <= 100) and (stop > start)
    if not active:
        return '%d / %d —— 数值不合法，ACPI 的 GBCL 判定为"没限制"' % (start, stop)
    return '%d%% 起充 / %d%% 停充  ** 充电限制已生效 **' % (start, stop)


def cmd_temp(cpf, n=10, interval=1.0):
    say('=== EC 温度传感器采样  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    offs = list(TEMPS.keys())
    samples = {}
    for i in range(n):
        res = read_batch(cpf, offs, tries=2, pace=0.02)
        for o in offs:
            v = res[o][0]
            if v is not None:
                samples.setdefault(o, []).append(v)
        time.sleep(interval)
    say()
    say('  %-6s %-42s %7s %7s %7s  %s' % ('偏移', '名字', '最低', '最高', '现在', '说明'))
    for o in offs:
        vs = samples.get(o, [])
        if not vs:
            say('  0x%02X   %-42s   ---' % (o, TEMPS[o]))
            continue
        good = [v for v in vs if v != 0x80]
        mn = '%d°C' % min(good) if good else '无效'
        mx = '%d°C' % max(good) if good else '无效'
        now = '%d°C' % vs[-1] if vs[-1] != 0x80 else '无效'
        say('  0x%02X   %-42s %7s %7s %7s  %s' % (o, TEMPS[o], mn, mx, now,
                                                  '0x80=无此传感器' if not good else ''))
    say()
    say('  提示: _TMP（固件判"过热"用它）只看 0x78；0xC9 HWAT 是固件自己算功耗/充电时的环境温度。')
    say()


def cmd_fan(cpf):
    say('=== 风扇与 0x31 那个写操作的验证  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say()

    def rpm():
        # 按 thinkpad_acpi 的固件 bug 说明：必须先读 0x84 再读 0x85
        locked = cpf._ec_lock(5000)
        try:
            lo = cpf.ec_read(0x84, _locked=True, delay=0.012)
            hi = cpf.ec_read(0x85, _locked=True, delay=0.012)
        finally:
            cpf._ec_unlock(locked)
        if lo is None or hi is None:
            return None
        return (hi << 8) | lo

    r0 = read_one(cpf, 0x2F)
    r31 = read_one(cpf, 0x31)
    say('  0x2F HFSP 风扇档位 : %s' % describe(0x2F, r0[0]))
    say('  0x31 当前值        : %s  (位: %s)' % ('0x%02X' % r31[0] if r31[0] is not None else '--',
                                                bits_str(0x31, r31[0]) or '全 0'))
    say()
    say('  --- 不写 0x31，直接读转速（连读 3 次）---')
    plain = []
    for _ in range(3):
        v = rpm()
        plain.append(v)
        say('      RPM = %s' % v)
        time.sleep(0.4)
    say()
    say('  --- 按旧做法先写 0x31=0，再读转速（连读 3 次）---')
    locked = cpf._ec_lock(5000)
    try:
        cpf.ec_write(0x31, 0x00, _locked=True)
    finally:
        cpf._ec_unlock(locked)
    with31 = []
    for _ in range(3):
        v = rpm()
        with31.append(v)
        say('      RPM = %s' % v)
        time.sleep(0.4)
    r31b = read_one(cpf, 0x31)
    say()
    say('  写后 0x31 = %s' % ('0x%02X' % r31b[0] if r31b[0] is not None else '--'))
    say()
    say('  ---- 判读 ----')
    pz = [v for v in plain if v]
    wz = [v for v in with31 if v]
    if pz and wz and abs(sum(pz) / len(pz) - sum(wz) / len(wz)) < 300:
        say('  两种读法转速一致 → 读转速**不需要**写 0x31。')
        say('  旧工具/旧脚本里那句 `写 0x31=0` 是本机 ECM 之外的型号留下的习惯；')
        say('  本机 0x31 的位被固件命名为 HUWB(bit2)/VPON(bit6)/VRST(bit7)，不是"风扇选择"。')
        say('  **建议**：把 CpuPLFix.py 的 ec_rpm() 里那句 ec_write(EC_FANSW, 0) 去掉，')
        say('            少 3 次 EC 事务/次，也不再动那三个语义未确认的位。')
    elif not pz:
        say('  不写 0x31 读不到转速 → 本机确实需要写。那也建议改成"只在读不到时才写"。')
    else:
        say('  两种读法有明显差异 → 保留写 0x31 的做法，但请记录差异值反馈。')
    say()


def cmd_batt(cpf, args):
    action = args[1] if len(args) > 1 else 'show'
    if action == 'show':
        return batt_show(cpf)
    if action == 'set':
        if len(args) < 4:
            sys.exit('用法: batt set <起充%> <停充%>   例: batt set 75 80')
        try:
            a, b = int(args[2]), int(args[3])
        except ValueError:
            sys.exit('[X] 阈值要写数字，例: batt set 75 80')
        return batt_set(cpf, a, b, yes=('--yes' in args))
    if action == 'off':
        return batt_set(cpf, 0, 0, yes=('--yes' in args))
    if action == 'autostart':
        return batt_autostart(cpf, (args[2] if len(args) > 2 else '').lower() == 'on')
    if action == 'apply':                      # 开机任务用
        return batt_apply_saved(cpf)
    sys.exit('batt 的子动作: show / set / off / autostart / apply')


def batt_show(cpf):
    say('=== 电池充电阈值（EC 寄存器）  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say()
    res = read_batch(cpf, [0xB0, 0xB1, 0xB2, 0xB3, 0x38, 0x39, 0xBB, 0xBE, 0xC4], tries=3)
    s0, e0 = res[0xB0][0], res[0xB1][0]
    s1, e1 = res[0xB2][0], res[0xB3][0]
    say('  电池 0 (BSC0/BEC0 @0xB0/0xB1) : %s' % batt_line(s0, e0))
    say('  电池 1 (BSC1/BEC1 @0xB2/0xB3) : %s' % batt_line(s1, e1))
    say()
    say('  固件 ACPI 判定充电限制是否生效（GBCL）:')
    say('      条件 = 1 <= 起充 <= 100 且 1 <= 停充 <= 100 且 停充 > 起充')
    say('      现在 = %s' % ('生效' if (s0 and e0 and 1 <= s0 <= 100 and 1 <= e0 <= 100 and e0 > s0) else '未生效（等于没限制，充到 100%）'))
    say()
    say('  电池状态位: 0x38 HB0S=0x%02X %s' % (res[0x38][0] or 0, bits_str(0x38, res[0x38][0] or 0)))
    say('              0xBB BPL1/BPL2/BP1S/BP2S/BLPL = %s' % (bits_str(0xBB, res[0xBB][0] or 0) or '全 0'))
    say('              0xC4 CQLS/SMCS = %s' % (bits_str(0xC4, res[0xC4][0] or 0) or '全 0'))
    say()
    say('  ---- 系统侧对照 ----')
    try:
        import subprocess
        o = subprocess.run(['powershell', '-NoProfile', '-Command',
                            "$b=Get-CimInstance Win32_Battery; "
                            "'电量 {0}%  状态码 {1}(2=接电源) 设计/满充 {2}' -f $b.EstimatedChargeRemaining,$b.BatteryStatus,$b.DesignCapacity; "
                            "$c=Get-CimInstance -Namespace root\\WMI -ClassName BatteryFullChargedCapacity; "
                            "$y=Get-CimInstance -Namespace root\\WMI -ClassName BatteryCycleCount; "
                            "'满充容量 {0} mWh  循环 {1} 次' -f $c.FullChargedCapacity,$y.CycleCount"],
                           capture_output=True, text=True, timeout=30)
        for ln in (o.stdout or '').splitlines():
            if ln.strip():
                say('  ' + ln.strip())
    except Exception as e:
        say('  （读系统电池信息失败: %s）' % e)
    say()
    say('  ---- 判读 ----')
    say('  · 这台机器现在是用**联想自带软件**管充电阈值的。如果你已经在那边设好了，')
    say('    上面 0xB0/0xB1 就应该读到非 0 的数字 —— 请拿它和联想软件里显示的两个百分比对照：')
    say('      **对得上** → 证明这份寄存器映射是对的（也就说明本工具读别的偏移也可信）；')
    say('      **对不上** → 请把结果发回来，我要改映射。')
    say('  · 本工具的 `batt set` 是"不装联想软件也能改"的备用手段；两条路都改同一个寄存器，')
    say('    所以**别同时用**，也不要开 `batt autostart`（会和你现在的设置打架）。')
    say()


def batt_set(cpf, start, stop, yes=False):
    say('=== 设置电池 0 充电阈值  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    if not (0 <= start <= 100 and 0 <= stop <= 100):
        sys.exit('[X] 阈值必须在 0~100')
    if (start, stop) != (0, 0):
        if start < 1 or stop > 100 or stop <= start:
            sys.exit('[X] 需要 1 <= 起充 < 停充 <= 100（取消限制用 `batt off`）')
        if stop - start < 5:
            say('  [!] 起充和停充只差 %d%%，电池会频繁在小范围内反复充放，建议至少差 5%%' % (stop - start))
    res = read_batch(cpf, [0xB0, 0xB1], tries=3)
    say('  写入前: %s' % batt_line(res[0xB0][0], res[0xB1][0]))
    say('  将要写: 0xB0 BSC0=%d   0xB1 BEC0=%d   %s'
        % (start, stop, '（不限制，充到 100%）' if (start, stop) == (0, 0) else '（低于 %d%% 才充，充到 %d%% 停）' % (start, stop)))
    say()
    say('  ⚠ 这是写 EC（改固件内部状态）。本机没有别的进程会去写这两个偏移，')
    say('     回滚只要再跑一次 `batt off` 或 `batt set <原值>`。')
    if not yes:
        try:
            a = input('  确认写入？[y/N] ').strip().lower()
        except EOFError:
            a = ''
        if a != 'y':
            say('  已取消，什么都没写。')
            return
    ok1, got1 = write_verify(cpf, 0xB0, start)
    ok2, got2 = write_verify(cpf, 0xB1, stop)
    say()
    say('  写入结果: 0xB0 → %s (%s)   0xB1 → %s (%s)'
        % ('0x%02X' % got1 if got1 is not None else '读不到', 'OK' if got1 == start else '不一致',
           '0x%02X' % got2 if got2 is not None else '读不到', 'OK' if got2 == stop else '不一致'))
    now = read_batch(cpf, [0xB0, 0xB1], tries=3)
    say('  回读    : %s' % batt_line(now[0xB0][0], now[0xB1][0]))
    say()
    if (now[0xB0][0], now[0xB1][0]) == (start, stop):
        say('  ✅ EC 寄存器已按预期保存这两个值。')
        say('     下一步验证（重要）：把电量用到 %d%% 以下再插电，看是否停在 %d%%。' % (stop, stop))
        say('     若重启/断电后自己变回 0/0 → 说明写 RAM 不持久，用 `batt autostart on` 让本工具每次开机重设。')
        try:
            import json
            with open(THRESH_JSON, 'w', encoding='utf-8') as f:
                json.dump({'start': start, 'stop': stop}, f, indent=2)
            say('     已把目标值记到 %s' % THRESH_JSON)
        except Exception as e:
            say('     （保存目标值失败: %s）' % e)
    else:
        say('  ❌ 回读和写入不一致：EC 可能不接受直接写，或者那一刻 EC 忙。')
        say('     先重跑一次本命令；还是不行的话看文档 05 里的"固件 SMI 通道"一节。')
    say()


def batt_autostart(cpf, on):
    import subprocess
    if on:
        if '--force' not in sys.argv:
            say('[!] 这台机器是用联想自带软件管充电阈值的。开这个开机任务会和它抢同一个寄存器，')
            say('    可能互相覆盖。确认非要开就再加 `--force`：')
            say('        batt autostart on --force')
            return 1
        if not os.path.isfile(THRESH_JSON):
            sys.exit('[X] 先跑一次 `batt set <起充> <停充>`，把目标值定下来。')
        py = sys.executable
        cmd = '"%s" "%s" batt apply' % (py, os.path.abspath(__file__))
        r = subprocess.run(['schtasks', '/create', '/tn', TASK_NAME, '/tr', cmd,
                            '/sc', 'onstart', '/delay', '0001:00', '/ru', 'SYSTEM',
                            '/rl', 'HIGHEST', '/f'],
                           capture_output=True, text=True)
        print((r.stdout or '') + (r.stderr or ''))
        say('[%s] 计划任务 %s 已建立（开机 1 分钟后套用 %s）'
            % ('OK' if r.returncode == 0 else 'X', TASK_NAME, THRESH_JSON))
        say('    注意: 需要"测试签名"照旧开着，TSCtl 才能被本任务加载（本机已开）。')
    else:
        r = subprocess.run(['schtasks', '/delete', '/tn', TASK_NAME, '/f'],
                           capture_output=True, text=True)
        say('[%s] 计划任务 %s 已删除' % ('OK' if r.returncode == 0 else 'X', TASK_NAME))
    return 0


def batt_apply_saved(cpf):
    import json
    if not os.path.isfile(THRESH_JSON):
        return 0
    with open(THRESH_JSON, encoding='utf-8') as f:
        d = json.load(f)
    want = (int(d.get('start', 0)), int(d.get('stop', 0)))
    now = read_batch(cpf, [0xB0, 0xB1], tries=3)
    cur = (now[0xB0][0], now[0xB1][0])
    if cur == want:
        print('已是目标值 %s，不动。' % (want,))
        return 0
    print('当前 %s -> 目标 %s，重写 ...' % (cur, want))
    write_verify(cpf, 0xB0, want[0])
    write_verify(cpf, 0xB1, want[1])
    chk = read_batch(cpf, [0xB0, 0xB1], tries=3)
    print('回读 %s' % ((chk[0xB0][0], chk[0xB1][0]),))
    return 0


WATCH_REGS = [0x2F, 0x31, 0x32, 0x33, 0x36, 0x38, 0x39, 0x3A, 0x3B,
              0x46, 0x47, 0x48, 0x49, 0x50, 0x51, 0x78, 0x79, 0x7A, 0x7B,
              0x7C, 0x7D, 0x7E, 0x7F, 0x84, 0x88, 0x89, 0x8A, 0x8B, 0x8C,
              0x8D, 0xB0, 0xB1, 0xB2, 0xB3, 0xBB, 0xBC, 0xBD, 0xBE, 0xC4,
              0xC6, 0xC8, 0xC9, 0xCC, 0xCD, 0xCF]


def cmd_watch(cpf, interval=3.0, extra=None):
    regs = list(WATCH_REGS)
    if extra:
        for x in extra:
            try:
                v = int(x, 0)
            except ValueError:
                continue
            if 0 <= v <= 0xFF and v not in regs:
                regs.append(v)
    say('=== EC 事件监视  间隔 %.1f 秒  (Ctrl+C 结束) ===' % interval)
    say('只在这个寄存器"变了"或"有位置起来了"的时候打印一行。')
    say('想看什么就制造什么事件：插拔电源 / 合盖开盖 / 按亮度键 / Fn 键 / 加负载让风扇转 /')
    say('把电池放到阈值以下再插电。监视的是这 %d 个偏移：%s'
        % (len(regs), ' '.join('0x%02X' % r for r in regs)))
    say()
    prev = {}
    n = 0
    try:
        while True:
            res = read_batch(cpf, regs, tries=2)
            for off in regs:
                v = res[off][0]
                if v is None:
                    continue
                old = prev.get(off)
                if old is None:
                    prev[off] = v
                    say('%s  0x%02X %-10s = 0x%02X  %s' % (time.strftime('%H:%M:%S'), off,
                                                          reg_name(off), v, describe(off, v)))
                elif v != old:
                    prev[off] = v
                    say('%s  0x%02X %-10s  0x%02X → 0x%02X   %s%s'
                        % (time.strftime('%H:%M:%S'), off, reg_name(off), old, v,
                           describe(off, v),
                           ('  [位: %s]' % flag_names(off, v)) if flag_names(off, v) else ''))
            n += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        say()
        say('（停了，共 %d 轮）' % n)
    return 0


def cmd_smi(cpf):
    """只读地看固件 SMI 邮箱。绝不写。"""
    MNVS = 0x63BF5018        # DSDT: OperationRegion (MNVS, SystemMemory, 0x63BF5018, 0x2000)
    CMD = MNVS + 0xFC0
    say('=== 固件 SMI 邮箱（只读）  基址 0x%X ===' % MNVS)
    say('ACPI 里 RBEC/WBEC/MBEC 和电池阈值 PSIF 全部走这里：')
    say('  往物理内存写参数 -> 往端口 0xB2 写 0xF5 触发 SMI -> 轮询 ERR，SMM 代劳访问 EC。')
    say('（这就是为什么 WMI 热区查询 MSAcpi_ThermalZoneTemperature 会永久挂死：')
    say('  _TMP 走 RBEC -> SMI 的等待环 `While (ERR == 1)` 没有超时。）')
    say()
    say('  %-10s %-12s %s' % ('字段', '物理地址', '当前值'))
    for name, off, size in (('CMD', 0xFC0, 1), ('ERR', 0xFC1, 4), ('PAR0', 0xFC5, 4),
                            ('PAR1', 0xFC9, 4), ('PAR2', 0xFCD, 4), ('PAR3', 0xFD1, 4)):
        v = cpf.rdphys(MNVS + off, size)
        say('  %-10s 0x%08X  %s' % (name, MNVS + off, '读不到' if v is None else '0x%X' % v))
    say()
    say('  这个工具**不**往这里写。要用 SMI 通道去设电池阈值，见文档 05 的接口表。')
    say()
    return 0


def cmd_throttle(cpf, seconds=60.0, interval=0.5, load=False):
    """把"被锁频的那一瞬间"连同当时所有状态一起抓下来（只读）。
    用法场景：睡前启动它 -> 合盖睡眠 -> 唤醒 -> 回来看它抓到了什么。
    """
    import subprocess
    say('=== 限频抓捕（只读）  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
    say('采样 %.1f 秒 / 每 %.1f 秒一次；MSR 每次读，EC（温度+状态位）每 4 次读一下。' % (seconds, interval))
    say('判据：倍频连续 3 次 ≤ 0.8GHz 就当成"一段锁频窗口"，把掉之前 6 个采样一起打出来。')
    say('注意：**空闲时倍频本来就低**（这是正常的省电）。要区分"空闲低"和"被锁"，')
    say('      请在做事情/刚唤醒的时候看，或者加 --load 让它一边跑负载一边采。')
    say()

    procs = []
    if load:
        burn = os.path.join(HERE, '_负载发生器.py')
        if os.path.isfile(burn):
            n = min(4, os.cpu_count() or 4)
            procs = [subprocess.Popen([sys.executable, burn, str(int(seconds) + 5)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                     for _ in range(n)]
            say('  已起 %d 个负载进程跟着采。' % n)
        else:
            say('  [!] 找不到 _负载发生器.py，--load 忽略。')
    say()

    ring = deque(maxlen=7)
    ec = {}
    low = high = 0
    in_ep = False
    episodes = 0
    minr, maxr = 999, 0
    mint, maxt = 999, 0
    t0 = time.time()
    n = 0
    try:
        while time.time() - t0 < seconds:
            n += 1
            pv = cpf.rdmsr(MSR_PERF_STATUS) or 0
            ratio = (pv >> 8) & 0xFF
            d = cpf.decode_pl(cpf.rdmsr(MSR_PKG_POWER_LIMIT) or 0)
            pkgt = cpf.rdmsr(MSR_PKG_THERM_STATUS) or 0
            coret = cpf.rdmsr(MSR_THERM_STATUS) or 0
            if n % 4 == 1:
                r = read_batch(cpf, [0x78, 0x7D, 0x7E, 0xC9, 0x3B, 0x32, 0x33, 0x2F], tries=2)
                ec = {k: x[0] for k, x in r.items()}
            snap = {
                't': time.strftime('%H:%M:%S'),
                'ratio': ratio, 'pl1': d['pl1_w'], 'pl2': d['pl2_w'], 'lock': d['lock'],
                'pkgt': pkgt & 0x0F, 'coret': coret & 0x0F, 'ec': dict(ec),
            }
            ring.append(snap)
            minr, maxr = min(minr, ratio), max(maxr, ratio)
            if ec.get(0x78) is not None:
                mint = min(mint, ec[0x78])
                maxt = max(maxt, ec[0x78])

            if ratio <= 8:
                low, high = low + 1, 0
                if low == 3 and not in_ep:
                    in_ep = True
                    episodes += 1
                    say('--- %s  抓到第 %d 段低倍频（掉之前的采样也一起打出来）---'
                        % (snap['t'], episodes))
                    for s in ring:
                        say('   %s  倍频 %4.1fGHz  PL1 %5.1fW%s  PROCHOT(封装/core)=%X/%X'
                            '  EC: 0x78=%s 0x7D=%s 0x7E=%s 0xC9=%s 0x3B=%02X 0x2F=%02X'
                            % (s['t'], s['ratio'] / 10.0, s['pl1'],
                               ' [锁定]' if s['lock'] else '       ',
                               s['pkgt'], s['coret'],
                               fmt(s['ec'].get(0x78)), fmt(s['ec'].get(0x7D)), fmt(s['ec'].get(0x7E)),
                               fmt(s['ec'].get(0xC9)),
                               s['ec'].get(0x3B) or 0, s['ec'].get(0x2F) or 0))
            else:
                high += 1
                low = 0
                if in_ep and high >= 3:
                    in_ep = False
                    say('   ^ 结束（倍频回到 %.1fGHz）' % (ratio / 10.0))
            if n % 10 == 0:
                say('   [%s] 倍频 %4.1fGHz  EC温度 %s°C  PL1 %.1fW%s'
                    % (snap['t'], ratio / 10.0, fmt(ec.get(0x78)),
                       d['pl1_w'], ' [MMIO/MSR 锁定]' if d['lock'] else ''))
            time.sleep(interval)
    except KeyboardInterrupt:
        say('（被 Ctrl+C 停了）')
    finally:
        for p in procs:
            try:
                p.kill()
                p.wait()
            except Exception:
                pass

    say()
    say('---- 汇总 ----')
    say('  倍频范围      : %.1f ~ %.1f GHz' % (minr / 10.0, maxr / 10.0))
    if mint != 999:
        say('  EC 温度(0x78) : %d ~ %d °C' % (mint, maxt))
    say('  低倍频窗口数  : %d' % episodes)
    if episodes == 0:
        say('  → 这段时间没抓到连续 3 次 ≤0.8GHz。若你还是觉得卡，把窗口拉长（`throttle 600`）。')
    else:
        say('  → 请把上面每一段贴出来（别忘了说当时在干什么 / 是不是刚唤醒）。')
        say('    如果那些窗口里 `PL1` 很小 + `锁定` → 是老问题（功耗墙被固件写小，01 已修）；')
        say('    如果 `PROCHOT(封装/core)` 的 bit0 是 1 → 有东西在拉 PROCHOT（EC 或适配器），那是 01 §6 说"管不了"的那条线；')
        say('    如果两个都正常但倍频还是低 → 那就是真的空闲，或者别的机制（BD PROCHOT / 电池识别）。')
    say()
    return 0


def fmt(v):
    return '--' if v is None else str(v)


# =====================================================================
# 6. main
# =====================================================================

USAGE = __doc__

MENU = """
========== EC 诊断工具箱 ==========
  1  全寄存器体检（先跑这个）        dump
  2  温度传感器（8 个）              temp
  3  风扇 & 0x31 那个写操作验证       fan
  4  电池充电阈值：看                batt
  5  电池充电阈值：设为 75/80        batt set 75 80
  6  电池充电阈值：取消限制          batt off
  7  监视"会变的"寄存器（Ctrl+C 停）  watch
  8  抓锁频瞬间（唤醒前启动它）       throttle
  9  看固件 SMI 邮箱（只读）          smi
  0  退出
"""


def menu(cpf):
    while True:
        print(MENU)
        try:
            c = input('选一个 [1]: ').strip() or '1'
        except EOFError:
            return 0
        if c == '0':
            return 0
        elif c == '1':
            cmd_dump(cpf, skip_smbus=True)
            save_report('EC体检')
        elif c == '2':
            cmd_temp(cpf)
            save_report('EC温度')
        elif c == '3':
            cmd_fan(cpf)
            save_report('EC风扇')
        elif c == '4':
            batt_show(cpf)
        elif c == '5':
            try:
                s = input('起充 %（例如 75）: ').strip()
                e = input('停充 %（例如 80）: ').strip()
                batt_set(cpf, int(s), int(e), yes=False)
            except (EOFError, ValueError):
                print('  （输入不是数字，取消了）')
        elif c == '6':
            batt_set(cpf, 0, 0, yes=False)
        elif c == '7':
            try:
                iv = input('间隔秒 [3]: ').strip()
                cmd_watch(cpf, float(iv) if iv else 3.0)
            except (EOFError, ValueError):
                cmd_watch(cpf, 3.0)
        elif c == '8':
            try:
                iv = input('采多久（秒）[60]: ').strip()
            except EOFError:
                iv = ''
            cmd_throttle(cpf, float(iv) if iv else 60.0)
        elif c == '9':
            cmd_smi(cpf)
        else:
            print('没有这个选项。')


def main():
    args = [a for a in sys.argv[1:] if a != '']
    if args and args[0] in ('-h', '--help', 'help'):
        print(USAGE)
        return 0
    cpf = load_cpf()
    open_ec(cpf)
    if not args:
        return menu(cpf)
    cmd = args[0].lower()
    if cmd == 'dump':
        cmd_dump(cpf, skip_smbus=('--smbus' not in args))
        save_report('EC体检')
    elif cmd == 'temp':
        cmd_temp(cpf)
        save_report('EC温度')
    elif cmd == 'fan':
        cmd_fan(cpf)
        save_report('EC风扇')
    elif cmd == 'batt':
        cmd_batt(cpf, args)
    elif cmd == 'watch':
        interval, extra = 3.0, []
        for a in args[1:]:
            try:
                interval = float(a)
            except ValueError:
                extra.append(a)
        cmd_watch(cpf, interval, extra)
    elif cmd == 'throttle':
        secs, load = 60.0, ('--load' in args)
        for a in args[1:]:
            try:
                secs = float(a)
            except ValueError:
                pass
        cmd_throttle(cpf, secs, load=load)
    elif cmd == 'smi':
        cmd_smi(cpf)
    else:
        print(USAGE)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
