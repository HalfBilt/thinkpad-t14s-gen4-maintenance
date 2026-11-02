# 01 · CpuPLFix —— 开机 / 唤醒后被锁 0.4GHz

> 本文 = 这条线的**结果**：症状与根因 · 工具文件与配置 · 启动机制 · 怎么验证 · 怎么回滚。
> 风扇 / 过热在 [`02`](02-风扇与过热保护.md) 与 [`03`](03-EC-寄存器与读写.md)；机器档案见 [`00`](00-电脑综述.md)。

| | |
|---|---|
| 源码 | <https://github.com/HalfBilt/T14scontrol>（`cpu/` 目录，与风扇、HUD 同仓） |
| 本机安装位置 | `C:\Users\Ting\AppData\Roaming\T14scontrol\cpu`（**部署目录就是 git 工作区**） |
| 运行时 | 上一级 `T14scontrol\runtime`，两个服务 + HUD 共用；**别删别改名**，`1-安装.bat` 不会自带 |
| ⚠️ | **工具本体不存进本手册仓库**（这里只放文档 + `scripts\`）。手册仓只管这台机器怎么配，代码仓只管实现 |

---

## 1. 症状与根因

**症状**：开机后 / 睡眠唤醒后 / 休眠恢复后，CPU 被钉在 **0.40~0.55GHz**（倍频 4~5）。风扇不转、
机器不烫，但什么都卡。重启有时能好，下次又犯。

**根因**：Intel 封装功耗墙（PL1 长时 / PL2 短时）在硬件里存**两份镜像**：

| | 位置 | 谁在用 |
|---|---|---|
| 系统侧 | `MSR 0x610`（PKG_POWER_LIMIT） | Windows / ThrottleStop |
| 固件侧 | `MCHBAR + 0x59A0`（MMIO 镜像，bit63 是锁定位） | **主板 / EC 固件** |

CPU 实际执行**固件侧那份**。固件在开机 / 唤醒时会把它改成一个小得离谱的**动态值**
（本机抓到过 21W → 全核 1.8GHz、15W → 2.6GHz、5W → 0.40GHz）→ 工具**每次读现场值再覆盖**。

原厂健康状态（出厂就这样）：两份都是 `PL1 55W / PL2 55W / 未锁`。

**几条硬结论**：

- MMIO **锁一旦置上，本电周期内不可逆**。所以本机用 `mmio_mode = disable`（清零 + 锁死固件侧，
  之后只写 MSR），改配置免重启；若换机器 MSR 被 BIOS 锁，得改回 `value`（写值进 MMIO + 锁，改配置要重启）。
- ThrottleStop 的两个现象由此解释：勾 **MMIO Lock** 能修好（写回 MMIO 再锁）；但改 55W 没效果
  （MMIO 锁定优先级高于 MSR）。
- BIOS `CPUPowerManagement=Disable` **不能**替代本工具 —— 固件仍会随机写小值（10-31 实证）。

---

## 1.1 第二条降频路径：BD PROCHOT（2026-11-02 新增）

**降频有两条互不相干的路，之前只处理了第一条。**

| 路径 | 谁在压 | 症状 | 本工具 |
|---|---|---|---|
| **A. 功耗墙** | 固件把 PL1/PL2 写进 MMIO(`+0x59A0`)/MSR `0x610` | 频率掉到 1GHz 上下，`0x198` 倍频 10~17 | 改写 PL1/PL2（主功能） |
| **B. BD PROCHOT** | EC / 充电器 / 电池 BMS 拉低 `PROCHOT#` 针脚 | 掉到 400~800MHz，**温度并不高** | **改写 PL 完全无效**，只能取证 |

只修 A 修不好 B —— 这就是"工具全开了还是卡"的一种可能解释。两者症状像（都变慢），根因不同。

**MSR 判据**（CPU 内部寄存器，读它不碰 EC、零 EC 事务代价）：

| 寄存器 | 位 | 含义 |
|---|---|---|
| `0x19C` IA32_THERM_STATUS | bit2 / bit3 | PROCHOT# 正在被外部拉低 / 本次开机后曾经触发 |
| `0x1B1` PKG_THERM_STATUS | bit2 / bit3 | 同上，封装级 |
| `0x64F` CORE_PERF_LIMIT_REASONS | bit0 / bit16 | PROCHOT 活跃 / 历史 |
| `0x1FC` MSR_POWER_CTL | bit0 | BD PROCHOT 总开关（1 = 允许外部降频，原厂状态） |
| `0x198` IA32_PERF_STATUS | bit8~15 | 当前倍频（**4 = 400MHz 锁频**） |

**本机实测（2026-11-02 23:30）**：

```
0x19C = 0x881f3808   bit3=1 (曾经触发)  bit0=0 (CPU 自身不热)
0x1B1 = 0x881e0808   bit3=1
0x64F = 0x1d010100   bit16=1
0x1FC bit0 = 1       BD PROCHOT 开着（原厂状态）
0x198 倍频 = 26      → 2600MHz
```

→ **本次开机后 PROCHOT 被外部拉过，而 CPU 自身热状态位是 0。**

**归因（2026-11-02 复核后修正）**：这条记录来自当晚的**裸扇压测**（停掉两个服务、写 `0x80`
交还固件自动、加 4 进程负载），那次 EC 被顶到 **92°C** / DTS 95°C。
同一天服务正常运行的全部时间里，EC 最高 **82°C**、**≥88°C 出现 0 次**（`fan\logs\fan_service.log` 193 条决策统计）。

机制：**EC 自己的保护阈值（约 88~92°C）比 CPU 的 TjMax（100°C）低** → EC 提前拉低 `PROCHOT#`，
CPU 只记 bit3（外部信号），bit0/bit1（自身热状态 / 热日志）都不置位 —— 这正是读到的组合。
所以**不是电池信号错，是 EC 在自己压不住温度时按设计动作**。

要它不触发，唯一手段是**让 EC 温度别摸到 ~88°C**：工具正常跑时 EC 99% 时间在 45~70°C（余量约 20°C）；
危险窗口是**工具不在**的时候 —— 关机前几十秒、唤醒瞬间、服务崩了、做压测时。

**常驻取证**：`tick()` 每拍读上面几个寄存器，跳变时追加 `cpu\logs\prochot.log`：

```
2026-11-02 23:30:40  PROCHOT 历史位已置位(本次开机后曾被外部拉低, 现已恢复)  reasons=0x1D010100
```

一次性快照（想立刻看当下状态）：`python scripts\prochot_check.py`（**需管理员**，要打开 TSCtl 设备）。

**不要关 BD PROCHOT。** `0x1FC` bit0 清零等于拆掉安全气囊；Framework 实测 EC 会立刻把它写回来，
网上也有 ThrottleStop 关掉后直接断电的案例。正确做法是**找出谁在拉线**（适配器 / 电池 / DC-IN），
不是屏蔽信号。

---

## 2. 它怎么随系统启动（覆盖开机 / 唤醒 / 休眠恢复）

两套机制互相独立，任何一套单独都能修好。

1. **常驻服务 `CpuPLFixSvc`（主用）**：`Start=2` 自动，被 SCM 拉起，不需登录、无窗口。
   开机 **14~24 秒**修复；睡眠唤醒 / 休眠恢复靠电源事件通知，**≤1 秒**；
   另有轮询（本机 2 秒）与时钟跳变检测兜底。
2. **计划任务 `CpuPLFix`（兜底）**：触发器 = 开机 +1 秒（30 分钟内每 5 分钟重复）、登录 +3 秒、
   `Power-Troubleshooter` 事件 1、`Kernel-Power` 事件 107。触发后**先预检**，正常就立刻退出；
   异常才修，然后看守几分钟防固件再补一刀。

> 计划任务**普通权限 `schtasks /query` / `Get-ScheduledTask` 看不到**，会误判成"缺失"；
> 管理员查，或看 `fix.log` 的 `boot` 行（开机/复查的记录）。
>
> **"快速启动"（`HiberbootEnabled=1`）**：点「关机」实际是内核休眠，开机是恢复而非冷启动 →
> 日志标签可能标成"唤醒后"（不影响修复）。真正要盯的是**从 S3 睡醒**（合盖再打开）——
> 固件几乎必写 PL，服务 1 秒内响应。

---

## 3. 文件与配置

| 项目 | 值 |
|---|---|
| 工具目录 | `%APPDATA%\T14scontrol\cpu` |
| 主程序 / 服务 | `CpuPLFix.py` / `CpuPLFixSvc.py` |
| 配置命令 | `CpuPLFixCfg.py`（命令行直调；只读写 `config.json`，带范围校验和 `undo`） |
| 驱动 | `TSCtl.sys`（ThrottleStop 随包的低层驱动，从它的 exe 资源里原样提取、未修改；服务名 `TSCtl`，按需启动；**放 System32**）。签名可复核：`Valid` / `CN=TechPowerUp LLC` / `3.0.0.0` |
| 计划任务 | `\CpuPLFix`（SYSTEM 身份，普通权限不可见） |
| 日志 | `logs\fix.log`（只记事件、不记轮询；超 2MB 滚一份） |
| 目录入口 | 4 个 `.bat`：`1-安装` / `2-查看状态` / `3-重启服务` / `4-卸载` |

`config.json` 主要键：

| 键 | 含义 |
|---|---|
| `pl1_w` / `pl2_w` | **插电档**长时 / 短时功耗墙 |
| `pl1_w_batt` / `pl2_w_batt` | **电池档** |
| `mmio_mode` / `lock_mmio` | `disable`（本机）：清零+锁固件侧，之后只写 MSR；`value`：写值进 MMIO+锁，改配置要重启 |
| `poll_s` / `ec_poll_s` / `flag_poll_s` / `flag_poll_slow_s` | 轮询节奏（秒）：主循环 / EC 读 / 标志 / 凉机放慢 |
| `guard.*` | 过热保护阈值（`02`「过热保护」；**风扇已拆出，本工具没有 `fan` 段**） |

### 3.1 本机配置（重装后唯一要补的东西）

配置就在 T14scontrol 仓库的 `cpu/config.json`（部署目录=工作区），改完提交留档；本手册仓库不存它。
下面是"哪些值和代码默认不同"的清单：

| 键 | 本机值 | 说明 |
|---|---|---|
| `pl1_w` / `pl2_w` | `28` / `55` | 插电；PL1 = PBP 28W，PL2 = 55W（固件原厂值；时间窗编码 35/66 → 实际 8 秒 / 5 秒） |
| `pl1_w_batt` / `pl2_w_batt` | `20` / `28` | 电池；20W = Minimum Assured Power |
| `mmio_mode` / `lock_mmio` | `disable` / `true` | |
| `poll_s` / `ec_poll_s` / `flag_poll_s` / `flag_poll_slow_s` | `2` / `15` / `30` / `120` | 见 `03`「访问节奏」 |
| `guard.warn_c/crit_c/escape_c/recover_c` | `95 / 98 / 105 / 78` | 温度线 |
| `guard.warn_pl1/warn_pl2` | `28 / 40` | 预警档只收 PL2，PL1 保持 28W |
| `guard.crit → crit2 → crit3` | `28/35 → 20/25 → 12/14` | 三级阶梯（12/14 已低于芯片 20W 保底，会掉频，是地板） |
| `guard.crit_hold_s / step_dwell_s / ramp_step_s` | `10 / 15 / 30` | 驻留 / 每级加深 / 每级回升 |
| `guard.crit_now_c / crit_floor_c` | `99 / 100` | 单次 99°C 直接二级；单次 100°C（=TjMax）直接地板 |
| `guard.dts_warn_c / dts_crit_c / dts_hot_now_c` | `90 / 99 / 100` | DTS 快通道 / 危险线（`02`「过热保护」） |
| `guard.flag_pl1 / flag_pl2` | `20 / 28` | 固件标志锁存档 |
| `guard.flag_release_c / flag_release_s` | `92 / 60` | **唯一实际生效**的标志档出口：EC ≤92°C 满 60 秒 |
| `guard.trip_hold_s` | `60` | 兼容键：仅 `flag_release_s` 缺失时生效；`hold=` 同时写两个 |
| `guard.min_hold_s / release_dwell_s` | `30 / 10` | 防保护档来回切 |
| `guard.temp_power` / `escape_hibernate` | `false / false` | 调速器与主动休眠都关 |

手工从零写回（配置文件被删时按顺序跑；每条命令都带范围校验，写错不落盘）：

```bat
cd /d "%APPDATA%\T14scontrol\cpu"
set PY=..\runtime\python.exe
%PY% CpuPLFixCfg.py ac=28,55
%PY% CpuPLFixCfg.py batt=20,28
%PY% CpuPLFixCfg.py mmio=disable
%PY% CpuPLFixCfg.py lock=on
%PY% CpuPLFixCfg.py poll=2,15,30
%PY% CpuPLFixCfg.py flag_slow=120
%PY% CpuPLFixCfg.py guard=95,98,105,78
%PY% CpuPLFixCfg.py pl_guard=28,40,28,35
%PY% CpuPLFixCfg.py crit2=20,25
%PY% CpuPLFixCfg.py crit3=12,14
%PY% CpuPLFixCfg.py crit_hold=10
%PY% CpuPLFixCfg.py step_dwell=15
%PY% CpuPLFixCfg.py ramp=30
%PY% CpuPLFixCfg.py flag_pl=20,28
%PY% CpuPLFixCfg.py hold=60
%PY% CpuPLFixCfg.py flag_rel=92,60
%PY% CpuPLFixCfg.py gmin=30
%PY% CpuPLFixCfg.py rel=10
%PY% CpuPLFixCfg.py hibernate=off
```

核对（重装后跑一遍）：

```powershell
..\runtime\python.exe CpuPLFixCfg.py                      # 打印全部当前值
Get-Content "$env:APPDATA\T14scontrol\cpu\logs\fix.log" -Tail 20  # 服务有没有起来
```

### 3.2 `mmio_mode = disable` 的后果

固件侧 MMIO 清零+锁死后，CPU 取 `min(MSR, MMIO)` 就只剩 MSR，于是**改配置 1 分钟内自动生效**、
**拔插电源秒级切换档位**，也不依赖"开机时的电源状态"。前提是 **MSR 0x610 可写**（本机可写）。

### 3.3 PL1 怎么选

| 想要 | `pl1_w` | 结果 |
|---|---|---|
| 满性能 | 55 | 散热吐不出（满速风扇稳定吸热能力只有 **~42W**），很快撞温度墙 |
| **当前（推荐）** | **28** | = PBP。12 线程满载 EC 89~95°C、只到温度墙第一级、无强制休眠 |
| 更凉 | 24 | EC 再低 2~3°C，重载性能略降 |
| 原厂长时限值 | 21 | 全核 2.0~2.2GHz |
| 不操心 | 0 | 自动（按标称 TDP 兜底） |

- **PL2 = 55W = 固件原厂值**（按 MTP 上 64W 温度代价 ≈1~2°C，不划算；要试：`ac=28,64`）。
- **电池档 20/28W = 省电优先**：PL1 才是持续发热的闸门，20W 下全核仍有 ~2.0GHz。
- **PL 是天花板不是固定功耗**（6~9% 占用实测封装只有 8.9~12.9W）；真正耗电的是背光、Wi-Fi、
  后台程序；睡眠(S3) 耗电与 PL 无关。
- 突发信用：空闲切满载先按 PL2 跑十几秒到一分多钟才落回 PL1（差额越大越短）。**凉机突然满载
  冲到 94~100°C 是正常突发行为，不是散热坏了**，兜底靠 DTS 快通道 + 三级阶梯。

---

## 4. 怎么用 / 怎么验证

```
1-安装.bat           装 / 重启（管理员；注册驱动服务 + 看守服务 + 计划任务，立即修一次）
2-查看状态.bat       只读状态：电源/档位、电池链路、版本一致性、两份功耗墙、频率、温度、
                     固件标志、EC 温度/总线健康度、保护档（风扇只显示一行"由 fan 工具独立控制"）
3-重启服务.bat       清 __pycache__ + 重启 CpuPLFixSvc + 打日志尾（改 .py 代码后用它）
4-卸载.bat           卸载（删计划任务、看守服务、驱动服务，不删文件）
```

> ⚠️ `4-卸载.bat` 会连带删除**风扇共用**的 `TSCtl` 驱动（重启后生效），风扇随之停摆，而风扇安装脚本
> 不装驱动。卸载 CPU 侧前想清楚；恢复跑 `%APPDATA%\T14scontrol\0-重启后部署.bat`。
>
> 改配置：`CpuPLFixCfg.py`（只改 config.json，不碰硬件、免管理员、免重启，保存后 ≤60 秒自动重读）。
> 常用：`ac=28,55` / `batt=20,28` / `guard=95,98,105,78` / `pl_guard=...` / `poll=2,15,30` /
> `mmio=disable` / `undo`。完整键名 `CpuPLFixCfg.py help`。立即修一次：`CpuPLFix.py fix`（管理员）。

```powershell
sc.exe query CpuPLFixSvc                                    # 期望 STATE : 4  RUNNING
Get-Content "$env:APPDATA\T14scontrol\cpu\logs\fix.log" -Tail 40
```

正常日志样式：

```
[*] 已是目标状态: MMIO 清零+锁, MSR PL1=28.00W PL2=55.00W, 倍频 43, 无需修复
[*] 电源档位[电池/平衡/disable]: MSR PL1 55.0->20.0W PL2 55.0->28.0W; ...
[*] 轮询节奏: 主循环 2 秒(只对功耗墙/电源档, 不碰 EC) / EC 读 15 秒 / 固件标志 120 秒 ...
```

> 轮询行里的"固件标志 120 秒"是**当前生效值**：凉机按 `flag_poll_slow_s` 放慢，热了自动回 30 秒。
> 开机第一条记录里的 **MMIO PL1** 就是固件刚写完、工具动手前的现场值。
>
> `2-查看状态.bat` 只在日志留一行摘要。标志读失败时该格显示 `--`，**和 `0x00` 是两回事**。

**怀疑被降频时**：`scripts\10-性能自检.bat`（自动提权）→ 区分"被功耗墙限住"（PL 小 / 倍频低）
和"散热到顶"（PL 正常但温度到墙）；单线程判据是**倍频 ≥46（4.6GHz）**，本机实测基线约 48（4.8GHz）。

---

## 5. 已知限制 / 回滚

- 只管 PL1/PL2 这条路径。**BD PROCHOT、温度墙、适配器识别问题**导致的降频它管不了；
  排查用 `scripts\13-EC诊断.bat throttle`（抓锁频瞬间的 PL / PROCHOT / EC 标志，`05`「工具」）。
- MMIO 偏移 `0x59A0` 是 Intel 近几代通用值；换机器先跑 `2-查看状态.bat` 验证。
- 开机阶段做不到零延迟（本机实测 14~24 秒；0.4GHz 时 Windows 自己启动就慢十倍）。
- **需要重启的三种情况**：① `mmio_mode` 在 disable/value 间切换；② value 模式升档超过已锁值；
  ③ 手动改被锁死的 MMIO。**改配置不用重启；改 `.py` 代码要重启服务**。
- **全家桶共存**：与 Vantage / Intel IPF / DTT / Windows 电源计划不冲突（它们走用户态）；
  与 **ThrottleStop / XTU / QuickCPU 抢寄存器**（特别是 MSR 0x610 的还原行为——"改完又变回去"
  先看 `2-查看状态.bat` 两处读数，别急着判服务失效）；**别用它们的"风扇控制"**（会跟 fan 工具抢
  `EC 0x2F`）；HWiNFO 只读没问题（服务已显式放行通用锁 `Global\Access_EC`）。

**回滚：以 git 为准**

```bash
cd %APPDATA%\T14scontrol\cpu
git log --oneline                 # 找到要回的那一版
git checkout <sha> -- CpuPLFix.py CpuPLFixCfg.py CpuPLFixSvc.py
# 必须重启服务（主程序被 import 进去了）：
# 双击 3-重启服务.bat（自动提权 + 清 __pycache__ + 重启 + 打印状态）
```

手动卸载（不用 4-卸载.bat 时，管理员）：

```powershell
schtasks /delete /tn CpuPLFix /f
sc.exe stop CpuPLFixSvc; sc.exe delete CpuPLFixSvc
sc.exe stop TSCtl;       sc.exe delete TSCtl
```

> **先停 `CpuPLFixSvc` 再停 `TSCtl`** —— 反过来会卡 `STOP_PENDING`。

**两条硬规矩**：

1. `.bat` 用统一模板生成：**UTF-8（无 BOM）+ `chcp 65001` + CRLF**；改完必须**实跑一次**验证
   （bat 对 `if ... )` 与命令挤同行这类解析极敏感，双击无效很常见）。不弹 UAC 的验证：
   `cmd /c "set CPF_ELEV=1& "<bat> < nul"`。
2. **EC 操作必须串行**：任何自己写的脚本读写 EC 都要复用 `03`「并发规则」的锁与保险，
   两个进程同时裸跑会互相插队读到别人的应答。
