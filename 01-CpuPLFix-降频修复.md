# 01 · CpuPLFix —— 开机 / 唤醒后被锁 0.4GHz

> 本文 = 这条线的**结果**：症状与根因 · 工具文件与配置 · 启动机制 · 怎么验证 · 怎么回滚 · 两条规矩。
> 风扇 / 过热 / EC 在 [`02`](02-风扇与过热保护.md) 与 [`03`](03-EC-寄存器与读写.md)；机器档案见 [`00`](00-电脑综述.md)。
> 工具内部的修改历史不在这里，在 <https://github.com/HalfBilt/CpuPLFix> 的 git 记录里。

| | |
|---|---|
| 源码与发布包 | <https://github.com/HalfBilt/CpuPLFix>（带 `runtime.zip`，`1-install.bat` 自动解压，**不依赖系统 Python**） |
| 本机安装位置 | `C:\Users\Ting\AppData\Roaming\CpuPLFix` |
| 工具目录（**源码 + 运行一体**） | `C:\Users\Ting\AppData\Roaming\CpuPLFix` —— 2026-10-20 起 `_repo` 已合并进来：改代码就改这里，`git` 也在这里 |

> ⚠️ **工具本体不存进本手册仓库**（这里只放文档 + `scripts\`）。

---

## 1. 症状与根因

**症状**：开机后 / 睡眠唤醒后 / 休眠恢复后，CPU 被钉在 **0.40~0.55GHz**（倍频 4~5）。风扇不转、
机器不烫，但什么都卡。重启有时能好，下次又犯。

**根因**：Intel 封装功耗墙（PL1 长时 / PL2 短时）在硬件里存**两份镜像**：

| | 位置 | 谁在用 |
|---|---|---|
| 系统侧 | `MSR 0x610`（PKG_POWER_LIMIT） | Windows / ThrottleStop |
| 固件侧 | `MCHBAR + 0x59A0`（MMIO 镜像） | **主板 / EC 固件**，bit63 是锁定位 |

CPU 实际执行**固件侧那份**。固件在开机 / 唤醒时会把它改成一个小得离谱的值，而且**每次都不一样**
（本机抓到过 **21W** → 全核 1.8GHz、**15W** → 2.6GHz、**5W** → 0.40GHz）—— 是 EC 按电池 / 适配器 /
温度动态决定的 → 工具**不能写死值，必须每次读现场值再覆盖**。

原厂健康状态（出厂就这样）：两份都是 `PL1 55W / PL2 55W / 未锁`。

> ThrottleStop 的两个现象由此解释：勾 **MMIO Lock** 能修好（把正常值写回 MMIO 再把 bit63 置 1）；
> 但改 55W 没效果（MMIO 锁定优先级高于 MSR）。
>
> ⚠️ **锁一旦置上是本电周期内不可逆的** —— 所以"写目标值进 MMIO + 锁"（`mmio_mode = value`）这种
> 老方案改功耗墙**必须重启**。本机改用 `mmio_mode = disable`（清零 + 锁死固件侧，之后只写 MSR），
> 就没有这个问题，见 §3.2。

---

## 2. 它怎么随系统启动（覆盖开机 / 唤醒 / 休眠恢复）

两套机制互相独立，任何一套单独都能修好。

1. **常驻服务 `CpuPLFixSvc`（主用）**：`Start=2` 自动，由 SCM 拉起，不需登录、无窗口。
   开机 **14~24 秒**修复；睡眠唤醒 / 休眠恢复靠 `SERVICE_ACCEPT_POWEREVENT` 主动通知，**≤1 秒**；
   另有轮询（主循环 5 秒）与时钟跳变检测兜底。
2. **计划任务 `CpuPLFix`（兜底）**：四个触发器 —— 开机 +1 秒（30 分钟内每 5 分钟重复）、登录 +3 秒、
   `Power-Troubleshooter` 事件 1、`Kernel-Power` 事件 107。触发后**先预检**，正常就立刻退出；
   异常才修，然后看守几分钟防固件再补一刀。

> **"快速启动"（`HiberbootEnabled = 1`）**：点「关机」实际是内核休眠到 `hiberfil.sys`，开机是恢复而非冷启动
> → 工具可能把这次恢复**误判成"睡眠/休眠唤醒后"**（只是日志标签不准，修复不受影响）。
> 真正要警惕的是**从 S3 睡醒**（合盖再打开）—— 固件几乎一定会重置 PL，服务 1 秒内就响应了。

---

## 3. 文件与配置

| 项目 | 值 |
|---|---|
| 工具目录 | `C:\Users\Ting\AppData\Roaming\CpuPLFix`（`runtime\` 是内置 Python，**别删**） |
| 主程序 / 服务 | `CpuPLFix.py` / `CpuPLFixSvc.py` |
| 配置命令 | `CpuPLFixCfg.py`（入口 `3-config.bat`；只读写 `config.json`，带范围校验和 `undo`） |
| 驱动 | `TSCtl.sys`（从 ThrottleStop 提取的签名驱动，服务名 `TSCtl`，按需启动） |
| 看守服务 | `CpuPLFixSvc`（`Start=2`、`Type=16`） |
| 计划任务 | `\CpuPLFix`（SYSTEM 身份；普通权限查不到，用日志确认它在跑） |
| 日志 | `logs\fix.log`（只记事件、不记轮询；超 **2MB** 自动滚成 `fix.log.1`，只留一份）。`2-check` 只留一行摘要；`boot` 的触发标签是实测的；`logs\boot_last.txt` 是判断"到底睡没睡"的小状态文件（删了无副作用） |
| 命令行入口 | 仓库里有 11 个 `.bat`；**部署目录放 4 个**：`1-install` / `2-check` / `3-config` / `9-uninstall`（见 §5）。⚠️ 2026-10-19 之前部署目录里**没有** `3-config.bat`（只有 3 个），已补上；仓库里那份原来因为找不到 `%~dp0runtime\python.exe` 也跑不起来（仓库只有 `runtime.zip`），已改成**回退到部署目录的 runtime** |

`config.json` 主要键：

| 键 | 含义 |
|---|---|
| `pl1_w` / `pl2_w` | **插电档**长时 / 短时功耗墙 |
| `pl1_w_batt` / `pl2_w_batt` | **电池档** |
| `mmio_mode` | `disable`（本机在用）开机把固件侧 MMIO 清零 + 锁死，之后**只写 MSR**，改配置免重启；`value`（老方案）写目标值进 MMIO + 锁，改配置要重启 |
| `poll_s` / `ec_poll_s` / `flag_poll_s` | 轮询节奏（秒）：主循环 / EC 读 / 固件标志读 —— 见 `02` §7 |
| `flag_poll_slow_s` | 凉机时标志读放慢到这么多秒（默认 120） |
| `guard` | 过热保护阈值 —— 见 `02` §4（**风扇已拆出，本工具没有 `fan` 段**） |

### 3.1 本机配置（重装后唯一要补回来的东西）

**仓库里那份 `config.json` 就是本机实用值**（每次改完都同步提交），重装时直接拿仓库的即可。
（2026-10-20 拆分后：实机 `%APPDATA%\T14scontrol\cpu\config.json` 与仓库那份**逐字节一致**。
仓库那份是分享默认（`pl1_w=0`），本机是 28/55 —— 这是唯一有意的差异。）
下面是"哪些值和默认不一样"的清单，用来核对：

| 键 | 本机值 | 说明 |
|---|---|---|
| `pl1_w` / `pl2_w` | `28` / `55` | 插电；PL1 = i7-1360P 的 **PBP 28W**，PL2 = **55W**（固件原厂值；MTP 64W 见 §3.3 为什么不用；时间窗 `pl1_time=35`→8 秒 / `pl2_time=66`→5 秒） |
| `pl1_w_batt` / `pl2_w_batt` | `20` / `28` | 电池；20W = Minimum Assured Power |
| `mmio_mode` / `lock_mmio` | `disable` / `true` | 固件侧清零 + 锁，之后只写 MSR |
| `poll_s` / `ec_poll_s` / `flag_poll_s` / `flag_poll_slow_s` | `5` / `15` / `30` / `120` | 见 `02` §7 |
| ~~`fan.*`~~ | — | **2026-10-20 已整段删除**（风扇交给 `T14scontrol\fan`） |
| `guard.warn_c` / `crit_c` / `escape_c` / `recover_c` | `95` / `98` / `105` / `78` | 温度线 |
| `guard.warn_pl1_w` / `warn_pl2_w` | `0` / `40` | 预警档只收 PL2 |
| `guard.crit_*` | `28/35` → `20/25` → `12/14` | 严重档三级阶梯 |
| `guard.crit_hold_s` / `step_dwell_s` / `ramp_step_s` | `10` / `15` / `30` | 驻留 / 每级加深 / 每级回升 |
| `guard.crit_now_c` | `100` | 单次到它就立即进严重档（= TjMax） |
| `guard.flag_pl1_w` / `flag_pl2_w` | `20` / `28` | 固件标志锁存档 |
| `guard.trip_hold_s` | `45` | **兼容键**：旧版代码拿它当锁存判定秒数；现版实际读 `flag_release_s`（`3-config.bat hold=` 会同时写这两个键）。0 = 关掉这条出口 |
| `guard.temp_power` | `false` | **温度→功耗调节器**（"风扇优先"模型）：开 = 日常功耗跟着温度走、顶档受 `pl1_w` 约束；关 = 固定功耗墙。`3-config.bat tpower=on/off`（`02` §4.5） |
| `guard.flag_release_c` / `flag_release_s` | `97` / `60` | 温度连续 ≤97°C 满 60 秒 → 判锁存、放开（97 低于 `crit_c`=98，高于满载稳态 89~95） |
| `guard.min_hold_s` / `release_dwell_s` | `30` / `10` | 防保护档来回切 |
| `guard.cool_tail_s` | `30` | 退出保护后继续满速 |
| `guard.escape_hibernate` | `false` | **不做**主动休眠 |

要**手工从零写回**（比如配置文件被删了），按顺序跑这一串（每条都会校验范围，写错不落盘）：

```bat
3-config.bat ac=28,55
3-config.bat batt=20,28
3-config.bat mmio=disable
3-config.bat lock=on
3-config.bat poll=5,15,30
3-config.bat flag_slow=120
3-config.bat guard=95,98,105,78
3-config.bat pl_guard=0,40,28,35
3-config.bat crit2=20,25
3-config.bat crit3=12,14
3-config.bat crit_hold=10
3-config.bat step_dwell=15
3-config.bat ramp=30
3-config.bat flag_pl=20,28
3-config.bat hold=60
3-config.bat flag_rel=97,60
3-config.bat gmin=30
3-config.bat rel=10
3-config.bat cool_tail=30
3-config.bat hibernate=off
```

核对（重装后跑一遍）：

```powershell
3-config.bat                                              # 打印全部当前值
(Get-Item "$env:APPDATA\CpuPLFix\TSCtl.sys").Length       # 50216（固定）
Get-Content "$env:APPDATA\CpuPLFix\logs\fix.log" -Tail 20 # 看服务有没有起来
```

### 3.2 `mmio_mode = disable` 的后果（本机在用）

MMIO 被清零 + 锁死后，CPU 取 `min(MSR, MMIO)` 就只剩 MSR，于是**改配置 1 分钟内自动生效**、
**拔插电源秒级切换档位**（服务订阅 `PBT_APMPOWERSTATUSCHANGE`），也不依赖"开机时是什么电源状态"。

⚠️ 前提是 **MSR 0x610 可写**（本机实测可写）。若换机器 MSR 被 BIOS 锁，把 `mmio_mode` 改回 `value`
（那就回到"改功耗墙要重启"的老规矩）。

### 3.3 PL1 怎么选

| 想要 | `pl1_w` | 结果 |
|---|---|---|
| 满性能 | 55 | 全核 ~3.0GHz，但**散热吐不出**（见下），很快撞温度墙 |
| **当前（推荐）** | **28** | = PBP。12 线程满载实测 EC **89~95°C**、只用到温度墙第一级、无强制休眠 |
| 更凉 | 24 | EC 再低 2~3°C，重载性能略降 |
| 原厂长时限值 | 21 | 全核 2.0~2.2GHz |
| 不操心 | 0 | 自动（按 CPU 标称 TDP 兜底） |

> **为什么不是 40W**：`scripts\11-满载温度60秒.bat` 实测——78°C 起步单调爬升，**39 秒触 100°C**，
> 末段稳态 99~100°C，此时倍频 2.9→2.7GHz、功耗 49→42W。也就是说**散热只能稳定吐 ~42W**，
> 而且那是"风扇已经满速"的前提下。40W 长期档实测撑不住（满速风扇下 ~30 秒~2 分钟就撞 98~99°C），
> 温度墙被迫反复出手 → 40W↔保护档来回振荡，平均吞吐反而更低。28W 才不会跟温度墙打架。
>
> **PL2（短时爆发）取 55W = 固件原厂值。**
> 2026-10-20 试过按 i7-1360P 的规格改成 **MTP 64W**（差 45 J 突发能量，对温度影响 ≈1~2°C），
> 但为了"不折腾"最后**退回 55W**。真要换成 64W：`3-config.bat ac=28,64`（60 秒内自动生效）。
> 另一个方向（把"风扇优先"的模型做成调节器）见 `02` §4.5 —— 机制已实现，**默认关**。
> **电池档 20W/28W = 省电优先**：PL1 才是持续耗电/发热的闸门，20W 下全核仍有 ~2.0GHz。

**闲置不费电**：PL 是**天花板**不是固定功耗（实测 6~9% 占用时封装只有 8.9~12.9W）。真正耗电的是屏幕背光、
Wi-Fi、后台程序；**睡眠(S3) 耗电与 PL 无关**。

---

## 4. 一条线怎么联动（功耗墙 ↔ 风扇 ↔ 过热保护）

同一个看守服务按固定顺序干活，节奏分两条：

```
主循环（默认 5 秒，只碰 MSR/MMIO）：过热保护定级 → 对齐功耗墙/电源档
EC 读（默认 15 秒）：EC 温度 0x78（**只读**，供过热保护定级）；固件标志 0x89/0x8A/0x8B 默认 30 秒一次
```

阈值、曲线、为什么要分频 —— `02` §4 / §7；EC 本身 —— `03`。

---

## 5. 怎么用 / 怎么验证

```
1-install.bat        装 / 重启（要管理员；注册驱动服务 + 看守服务 + 计划任务，并立即修一次）
2-check.bat          看状态：电源/档位、电池链路、版本一致性、两份功耗墙、频率、温度、
                     固件过热标志、EC 温度/总线健康度、保护档（**不再显示风扇**）
3-config.bat         命令行改配置：ac= / batt= / guard= / hold= / crit_hold= …（不带参数 = 看当前值）
9-uninstall.bat      卸载（删服务 + 计划任务，不删文件）
```

> `3-config.bat` **只改 `config.json`**：不碰硬件、不用管理员、跑完就退出，不常驻任何进程。
> 保存后看守服务 ≤60 秒自动重读；上一版自动存成 `config.json.bak`，改错了一条命令退回。
>
> ```bat
> 3-config.bat                     :: 看全部当前值
> 3-config.bat ac=28,55            :: 插电 PL1,PL2（0 = 自动）
> 3-config.bat batt=20,28          :: 电池档（0,0 = 跟随插电）

> 3-config.bat guard=95,98,105,78  :: 预警/严重/逃生/解除温度
> 3-config.bat pl_guard=0,40,28,35 :: 预警 PL1,PL2 / 严重 PL1,PL2（0 = 不压）
> 3-config.bat poll=5,15,30        :: 主循环 / EC / 固件标志（秒）
> 3-config.bat mmio=disable        :: disable（推荐）或 value
> 3-config.bat undo                :: 撤销上一次保存
> ```
>
> 每一项都有范围校验，写错只会报错、不会写盘（例如 `guard=95,90,...` 会被拒绝）。
> 完整键名随时用 `3-config.bat help` 看。

> **2026-10-20 拆分**：`4-风扇状态` / `5-风扇曲线` / `6-风扇满速` / `7-风扇交还EC自动` /
> `8-风扇固定档` 五个 bat 和 `CpuPLFix.py fan` 命令**已删除** —— 风扇现在用
> `%APPDATA%\T14scontrol\fan\1-启动风扇控制.bat`（托盘菜单切模式）。
> `3-fix-now.bat` 只做功耗墙 + 过热保护，要在管理员 cmd 里跑。

```powershell
sc.exe query CpuPLFixSvc                                    # 期望 STATE : 4  RUNNING
Get-Content "$env:APPDATA\T14scontrol\cpu\logs\fix.log" -Tail 40
```

正常日志（`svc` = 看守服务，`boot` = 计划任务）：

```
[*] 已是目标状态: MMIO 清零+锁, MSR PL1=28.00W PL2=55.00W, 倍频 43, 无需修复
[*] 电源档位[电池/平衡/disable]: MSR PL1 55.0->20.0W PL2 55.0->28.0W; MMIO 清零+锁(固件侧已禁用, MSR 全权)
[*] 电源档位[AC/高性能/disable]: MSR PL1 20.0->28.0W PL2 28.0->55.0W
[*] 轮询节奏: 主循环 5 秒(只对功耗墙/电源档, 不碰 EC) / EC 读 15 秒 / 固件标志 120 秒 —— 开机前 5 分钟和过热保护期间自动加密
```

> 最后一行里的 `固件标志 120 秒` 是**当前生效值**：凉机时按 `flag_poll_slow_s` 放慢；热了会自动回到 30 秒。

**`boot` 那两行有 4 种标的（按实测判断，不靠 uptime 瞎猜）**：

| 标签 | 什么时候 |
|---|---|
| `开机` | 真·冷启动 / 重启 |
| `开机后复查` | 开机后 30 分钟内，计划任务**每 5 分钟重复**叫的那次（没睡过） |
| `睡眠/休眠唤醒后, 睡掉 N 秒` | 中间真睡过（用 `QueryUnbiasedInterruptTime` 跟墙钟比出来的） |
| `定时复查` | 其它重复触发（没睡过） |

> `开机后复查` / `定时复查` **且状态正常**时只记一行（`[*] 定时复查: 状态正常, 无需修复`）。

`2-check.bat` 跑完只在日志里留**一行摘要**：

```
[*] 手动体检: AC  MSR PL1=28.00W PL2=55.00W  倍频 41  EC温度 63°C  DTS 75°C  风扇 6 档(约 3880 RPM) 3875RPM  固件标志 0x89=0x00 0x8A=0x00 0x8B=0x00
```

> 标志读失败时那一格显示 `--`（**不是** `0x00`）—— 这两件事必须分清，见 `02` §3.1。

唤醒后应出现这两行之一：

```
[*] 收到电源事件 0x0012 (自动唤醒), 判定刚从睡眠/休眠恢复, 立即重新检查
[*] 检测到 830 秒挂起间隔, 判定刚从睡眠/休眠恢复, 立即重新检查
```

> 验证唤醒：**合盖睡 1 分钟 → 开盖 → 等 5 秒 → 看日志最后 20 行**。
> 开机第一条记录里的 **MMIO PL1** 就是"固件刚写完、工具还没动手"的现场值。
> 反馈问题带上 `logs\fix.log`。

**怀疑被降频时**：双击 `scripts\10-性能自检.bat`（自动提权）→ 结果写 `scripts\性能自检-时间.txt`。
它查功耗墙状态 → 当前倍频/温度 → 单线程 7 秒（正常应到 4.8GHz 附近）→ 8 进程 7 秒（满载功耗/散热）→ 给结论；
能区分**"被功耗墙限住"**和**"散热到顶"**。

---

## 6. 已知限制 / 回滚

- 只管 PL1/PL2 这一条路径。**BD PROCHOT**、温度墙、电池/适配器识别问题导致的降频它管不了（要用 ThrottleStop 排查）。
  → 观察手段：`scripts\13-EC诊断.bat` 的 **`throttle`** 子命令会把"倍频掉下来那一瞬间"连同
  功耗墙 / `PROCHOT`(封装与 core) / EC 状态位一起记下来，用来判定到底是哪条线在压 CPU。见 `05` §7。
- MMIO 偏移按 `0x59A0`（Intel 近几代通用）。换机器先跑 `2-check.bat`，读不到或数值明显不对就是偏移不同。
- 开机阶段做不到零延迟（0.4GHz 时 Windows 自己启动慢十倍），本机实测 14~24 秒。
- 开了**内存完整性（HVCI）**时可能拒绝加载这类直接访问物理内存的驱动（本机是关的）。
- **需要重启的三种情况**：① `mmio_mode` 在 disable/value 间切换；② value 模式升档超过已锁值；
  ③ 手动想改被锁死的 MMIO 寄存器。**改配置不用重启；改 `.py` 代码要重启服务**（服务把主程序 import 进去了）。
- 与联想/Intel 自家工具**不冲突**（Vantage、Intel IPF、Intel DTT、Windows 电源计划全走用户态 WMI/ACPI/EPP）；
  但 **ThrottleStop / XTU / QuickCPU 会和它抢寄存器**，本机**已经装了 ThrottleStop 9.7 且常用**，
  另外还装了 **HWiNFO**（2026-10-19 确认）。都能用，只要避开这几种叠法：
  - **HWiNFO 会周期性读 EC 端口**（打开 EC 支持时），它正是 `03` §4 里"别的主控插进来"的一个来源。
    现在本服务每个事务会**同时拿 `Global\Access_EC`**（HWiNFO / AIDA64 / LibreHardwareMonitor
    用的那把行业通用锁），所以装着 HWiNFO 也不影响读数正确性，只是每次事务要等它让一下（毫秒级）。
    > ⚠️ **2026-10-19 复核：上面这句当时是"想当然"** —— 命名互斥体默认继承**创建者**的 DACL，
    > 而服务以 SYSTEM 身份创建 → HWiNFO（用户身份）**根本开不了那把锁**，"同行锁"等于没有，
    > HWiNFO 也可能因此开不了自己的锁。**已修**（显式给 Everyone 的 DACL）。
    > 但"HWiNFO 到底用不用 `Global\Access_EC` 这个名字"**本机仍未实测**（HWiNFO 一次没开过），
    > 所以这条只能当"机制上成立、待实测"，别当既成事实。`06` §4 第 7 项就是这条。
  - **别用 ThrottleStop / HWiNFO 自带的"风扇控制"** —— 它们会直接和我们写 `EC 0x2F` 打架，
    表现为档位来回跳。
  - **ThrottleStop 改 MSR 时别和 `mmio_lock` / PL 写入叠着用**：它退出或重开时可能把
    `MSR 0x610` 还原（取决于它的设置），我们下一轮会再写回去 —— 这类"改完又变回去"先在
    `2-check` 里看 `MSR 0x610` 和日志两处，不要急着判成服务失效。
  - **两边用的是同一个 `TSCtl.sys` 驱动**（本工具就是从 ThrottleStop 里提取的）。如果 ThrottleStop
    启动后本服务突然开始报端口类错误，先看 `TSCtl` 服务状态（`sc query TSCtl`）—— 这条还没实测过，
    只作为排查方向。

**回滚：以 git 为准**

```bash
cd %APPDATA%\T14scontrol\cpu
git log --oneline                 # 找到要回的那一版
git checkout <sha> -- CpuPLFix.py CpuPLFixCfg.py CpuPLFixSvc.py
sc.exe stop CpuPLFixSvc & sc.exe start CpuPLFixSvc     # 必须重启服务（被 import）
```

> **一键版（2026-10-20 起）**：在 `%APPDATA%\T14scontrol\cpu` 里双击 **`部署并重启.bat`** —— 它自己提权，
> 删 `__pycache__`、`net stop/start CpuPLFixSvc`，最后打 `sc query` + 日志尾 14 行。
> **不再复制文件**（源码和运行目录已合并成同一个），也不碰 `config.json`。

> ⚠️ **`%APPDATA%\T14scontrol\cpu\*.bak-*` 这类手工快照不保险**：2026-10-19 早上另一批 `*.bak-20261019-1030`
> 就在几十分钟内被别的进程清掉了（当时 WorkBuddyAI 在跑）。要留档就进 git。
> 回滚前后都用 `2-check.bat` 的 **版本一致性** 那一行确认"跑着的就是仓库里那份"。

```powershell
# 不用 9-uninstall.bat 时的手动卸载
schtasks /delete /tn CpuPLFix /f                      # [管理员]
sc.exe stop CpuPLFixSvc; sc.exe delete CpuPLFixSvc    # [管理员]
sc.exe stop TSCtl;       sc.exe delete TSCtl          # [管理员]
```

> 顺序有讲究：**先停 `CpuPLFixSvc` 再停 `TSCtl`** —— 反过来 `TSCtl` 会卡在 `STOP_PENDING`。

---

## 7. 两条硬规矩

1. **`.bat` 改完必须实跑一次**：`CpuPLFix.py` 带的 bat 曾全部双击无效 —— 因为
   `if not exist runtime\python.exe ( … exit /b 1 )` 这个块收尾的 `)` 和后面的命令挤在同一行，
   cmd 在**解析阶段**就报 `"C:\Windows\System32\cmd.exe" was unexpected at this time.` 并直接结束
   （当时只有 `1-install.bat` 恰好写对了）。现在的 bat 由**同一份模板生成**，
   且保证 **纯 ASCII + CRLF + 无 BOM**。
   不弹 UAC 的验证办法：`cmd /c "set CPF_ELEV=1& "<路径>\2-check.bat" < nul"`。
2. **EC 操作必须串行**：服务、bat、临时脚本读写 EC 走同一套端口事务，两个进程同时做会互相插队、
   读到别人的应答。对策（互斥体 + 哨兵 + 一致性校验 + 读前等待 + 标志两轮确认 + 温度突变过滤）全部在 `03` §4。
