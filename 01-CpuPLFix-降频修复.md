# 01 · CpuPLFix —— 开机 / 唤醒后被锁 0.4GHz

> 本文 = 这条线的**结果**：症状与根因 · 工具文件与配置 · 启动机制 · 怎么验证 · 怎么回滚 · 两条规矩。
> 风扇 / 过热 / EC 在 [`02`](02-风扇与过热保护.md) 与 [`03`](03-EC-寄存器与读写.md)；机器档案见 [`00`](00-电脑综述.md)。

| | |
|---|---|
| 源码与发布包 | <https://github.com/HalfBilt/CpuPLFix>（带 `runtime.zip`，`1-install.bat` 自动解压，**不依赖系统 Python**） |
| 本机安装位置 | `C:\Users\Ting\AppData\Roaming\CpuPLFix` |
| 桌面/仓库备份 | `C:\Users\Ting\Desktop\CpuPLFix`（与工具仓库逐文件一致，可分享） |

> ⚠️ **工具本体不存进本手册仓库**（这里只放文档 + `scripts\`）。本仓库里唯一和它相关的东西，
> 是 §3.1 那段本机专属的 `config.json`。

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
> 但改 55W 没效果（MMIO 锁定优先级高于 MSR）。**锁是双刃剑** → 改功耗墙**必须重启**，本电周期内不可逆。

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
| 日志 | `logs\fix.log`（只记事件、不记轮询；超 **2MB** 自动滚成 `fix.log.1`，只留一份）。**2026-10-18 起**：① `2-check` 的整屏输出**不再写进日志**，只留一行摘要；② `boot` 的触发标签改成**实测**（`开机`/`开机后复查`/`睡眠/休眠唤醒后, 睡掉 N 秒`/`定时复查`），状态正常的复查只记一行；③ 新增 `logs\boot_last.txt`（判断"到底睡没睡"的小状态文件，删了只会让下一次判成"唤醒/复查"，无副作用） |
| 命令行入口 | 同目录 4 个 `.bat`：`1-install` / `2-check` / `3-config` / `9-uninstall`（见 §5） |

`config.json` 键位：

| 键 | 含义 |
|---|---|
| `pl1_w` / `pl2_w` | **插电档**长时 / 短时功耗墙 |
| `pl1_w_batt` / `pl2_w_batt` | **电池档** |
| `mmio_mode` | `disable`（本机在用）开机把固件侧 MMIO 清零 + 锁死，之后**只写 MSR**，改配置免重启；`value`（老方案）写目标值进 MMIO + 锁，改配置要重启 |
| `poll_s` / `ec_poll_s` / `flag_poll_s` | 轮询节奏（秒）：主循环 / EC 读 / 固件标志读 —— 见 `02` §9 |
| `fan` / `guard` | 风扇曲线 / 过热保护阈值 —— 全部在 `02` |

### 3.1 本机配置（重装后唯一要补回来的东西）

`mmio_mode = disable`；插电 **PL1 40W / PL2 55W**，电池 **PL1 20W / PL2 28W**（省电优先）；
风扇自定义曲线；过热保护开着（95/98/78）；EC 节奏 5/15/30 秒。
**降频线与温控线是同一套数**：温控两档 = 插电档的 50%（20/28）与 25%（10/14），所有值都不超过 55W 这个顶。

> 工具仓库自带的 `config.json` 是**分享默认值**（`pl1_w = 0` 自动模式），**本机不要用**。

```json
{
  "pl1_w": 40.0,
  "pl2_w": 55.0,
  "pl1_w_batt": 20.0,
  "pl2_w_batt": 28.0,
  "pl1_time": 35,
  "pl2_time": 66,
  "min_pl1_w": 15.0,
  "lock_mmio": true,
  "mmio_mode": "disable",
  "poll_s": 5.0,
  "ec_poll_s": 15.0,
  "flag_poll_s": 30.0,
  "fan": {
    "mode": "curve",
    "level": 0,
    "curve": [[56, 2], [79, 6], [999, 64]],
    "hyst": 5.0
  },
  "guard": {
    "enabled": true,
    "warn_c": 95.0,
    "crit_c": 98.0,
    "escape_c": 105.0,
    "recover_c": 78.0,
    "warn_pl1_w": 20.0,
    "warn_pl2_w": 28.0,
    "crit_pl1_w": 10.0,
    "crit_pl2_w": 14.0,
    "dts_crit_c": 97.0,
    "flag_hot_c": 85.0,
    "force_fan_max": true,
    "trip_flags": true,
    "escape_hibernate": false
  },
  "comment": "本机实用配置。插电(AC): PL1=40W PL2=55W; 电池: PL1=20W PL2=28W(省电优先)。两条线的功率是一套: 温控档 = 插电档的 50%(20/28) 与 25%(10/14), 顶统一 55W(固件原厂值)。mmio_mode=disable: 开机把固件侧 MMIO 清零+锁, 之后只写 MSR, 改配置/拔插电免重启。poll_s/ec_poll_s/flag_poll_s = 主循环/EC读/固件标志读的间隔秒数(EC 是慢总线, 默认 5/15/30 秒; 开机前 5 分钟、过热保护期间、EC 温度超过 guard.flag_hot_c 时自动加密)。fan=风扇模式与温度曲线; guard=过热保护阈值(0x78 温度线 + EC 0x89/0x8A/0x8B 固件标志线, 详见 说明.txt)。"
}
```

核对一致（重装后可跑）：

```powershell
Get-Content "$env:APPDATA\CpuPLFix\config.json" -Raw      # 应与上面那段一致
(Get-Item "$env:APPDATA\CpuPLFix\TSCtl.sys").Length       # 50216
(Get-Item "$env:APPDATA\CpuPLFix\config.json").Length     # 1587（空格/换行会影响，只作参考）
(Get-Item "$env:APPDATA\CpuPLFix\CpuPLFix.py").Length     # 83772（随版本变）
(Get-Item "$env:APPDATA\CpuPLFix\CpuPLFixSvc.py").Length  # 10557（随版本变）
```

### 3.2 `mmio_mode = disable` 的后果（本机在用）

MMIO 被禁用后 CPU 取 `min(MSR, MMIO)` 就只剩 MSR，于是**改配置 1 分钟内自动生效**、
**拔插电源秒级切换档位**（服务订阅 `PBT_APMPOWERSTATUSCHANGE`），也不依赖"开机时是什么电源状态"。
⚠️ 前提是 **MSR 0x610 可写**（本机实测可写）。若换机器 MSR 被 BIOS 锁，把 `mmio_mode` 改回 `value`。

### 3.3 PL1 怎么选（55W 实测 OK，但散热只能稳定吐 ~42W）

| 想要 | `pl1_w` | 全核频率 |
|---|---|---|
| 满性能 | 55 | ~3.0GHz |
| **折中（当前）** | 40 | ~2.6GHz |
| 原厂长时限值 | 21 | 2.0~2.2GHz |
| 不操心 | 0 | 原厂怎么设就怎么用 |

> **PL2（短时爆发，35 秒）统一取 55W = 固件原厂值**（以前填过 64W：本机散热只稳定吐 ~42W，只是更快撞温度墙，不划算）。
> **电池档 20W/28W = 省电优先**：PL1 才是持续耗电/发热的闸门，20W 下全核仍有 ~2.0GHz，日常碰不到。

**闲置不费电**：PL 是**天花板**不是固定功耗（实测 6~9% 占用时封装只有 8.9~12.9W）。真正耗电的是屏幕背光、
Wi-Fi、后台程序；**睡眠(S3) 耗电与 PL 无关**。

---

## 4. 一条线怎么联动（功耗墙 ↔ 风扇 ↔ 过热保护）

同一个看守服务按固定顺序干活，节奏分两条：

```
主循环（默认 5 秒，只碰 MSR/MMIO）：过热保护定级 → 对齐功耗墙/电源档
EC 读（默认 15 秒）：EC 温度 0x78 → 风扇曲线写 HFSP(0x2F)；固件标志 0x89/0x8A/0x8B 默认 30 秒一次
```

阈值、曲线、为什么要分频 —— `02` §4 / §9；EC 本身 —— `03`。

---

## 5. 怎么用 / 怎么验证

```
1-install.bat        装 / 重启（要管理员；注册驱动服务 + 看守服务 + 计划任务，并立即修一次）
2-check.bat          看状态：电源/档位、两份功耗墙、频率、温度、固件过热标志、风扇档位/转速/EC 温度 + 曲线 + 保护档
3-config.bat         命令行改配置：ac= / batt= / fan= / guard= …（不带参数 = 看当前值）
9-uninstall.bat      卸载（删服务 + 计划任务，不删文件）
```

> `3-config.bat` **只改 `config.json`**：不碰硬件、不用管理员、跑完就退出，不常驻任何进程。
> 保存后看守服务 ≤60 秒自动重读；上一版自动存成 `config.json.bak`，改错了一条命令退回。
>
> ```bat
> 3-config.bat                     :: 看全部当前值
> 3-config.bat ac=40,55            :: 插电 PL1,PL2（0 = 自动）
> 3-config.bat batt=20,28          :: 电池档（0,0 = 跟随插电）
> 3-config.bat fan=56,79,5         :: 风扇曲线温度边界 + 回差
> 3-config.bat lv=2,6,64           :: 每段风扇档位（64 = 满速）
> 3-config.bat guard=95,98,105,78  :: 预警/严重/逃生/解除温度
> 3-config.bat pl_guard=20,28,10,14 :: 保护时的 PL1,PL2（预警 / 严重）
> 3-config.bat poll=5,15,30        :: 主循环 / EC / 固件标志（秒）
> 3-config.bat mmio=disable        :: disable（推荐）或 value
> 3-config.bat undo                :: 撤销上一次保存
> ```
>
> 每一项都有范围校验，写错只会报错、不会写盘（例如 `guard=95,90,...` 会被拒绝）。
> 完整键名随时用 `3-config.bat help` 看。

> 调试用的 6 个 bat（立即修 / 风扇状态 / 曲线 / 满速 / 交还 EC / 固定档）2026-10-18 已删 —— 平时用不到，
> 命令本身都还在：`%APPDATA%\CpuPLFix\runtime\python.exe %APPDATA%\CpuPLFix\CpuPLFix.py fan status|curve|max|auto|level N`
> 或 `fix`（要管理员）。删掉的 bat 在 `%APPDATA%\_repo`（git 历史）里能取回。

```powershell
sc.exe query CpuPLFixSvc                                    # 期望 STATE : 4  RUNNING
Get-Content "$env:APPDATA\CpuPLFix\logs\fix.log" -Tail 40
```

正常日志（`svc` = 看守服务，`boot` = 计划任务）：

```
20:52:49 svc  检测到功耗墙异常: MMIO PL1=15.00W lock=0 (目标 40.00W), 重新锁定
20:52:49 svc  修复后: MMIO PL1=40.00W PL2=55.00W lock=1  [√] 已生效并锁定
20:52:50 boot 进程启动(开机): 系统已运行 15.2 秒, 解释器启动耗时 0.31 秒
20:52:50 boot [*] 已是目标状态: MMIO 清零+锁, MSR PL1=40.00W PL2=55.00W, 倍频 43, 无需修复
18:56:38 svc  轮询节奏: 主循环 5 秒(只对功耗墙/电源档, 不碰 EC) / EC 读 15 秒 / 固件标志 30 秒
```

**`boot` 那两行现在有 4 种标的（2026-10-18 起实测判断，不再靠 uptime 瞎猜）**：

| 标签 | 什么时候 |
|---|---|
| `开机` | 真·冷启动 / 重启 |
| `开机后复查` | 开机后 30 分钟内，计划任务**每 5 分钟重复**叫的那次（没睡过） |
| `睡眠/休眠唤醒后, 睡掉 N 秒` | 中间真睡过（用 `QueryUnbiasedInterruptTime` 跟墙钟比出来的） |
| `定时复查` | 其它重复触发（没睡过） |

> `开机后复查` / `定时复查` **且状态正常**时只记一行（`[*] 定时复查: 状态正常, 无需修复`），
> 不再每次刷两行 —— 以前开机后半小时能刷十几行"睡眠/休眠唤醒后"，全是假的（系统明明没睡）。

`2-check.bat` 跑完只在日志里留**一行摘要**（不再把整屏输出灌进 `fix.log`）：

```
===== 2026-10-18 23:12:06 check =====
[*] 手动体检: AC  MSR PL1=40.00W PL2=55.00W  倍频 43  EC温度 85°C  DTS 95°C  风扇 满速 4658RPM  固件标志 0x89=0x00 0x8A=0x00 0x8B=0x00
```

唤醒后应出现这两行之一：

```
[*] 收到电源事件 0x0012 (自动唤醒), 判定刚从睡眠/休眠恢复, 立即重新检查
[*] 检测到 830 秒挂起间隔, 判定刚从睡眠/休眠恢复, 立即重新检查
```

插拔电源应看到 `[*] 电源档位[AC/高性能/disable]: MSR PL1 28.0->40.0W ...`（两档 PL2 都是 55W，变的是 PL1）。

> 验证唤醒：**合盖睡 1 分钟 → 开盖 → 等 5 秒 → 看日志最后 20 行**。
> 开机第一条记录里的 **MMIO PL1** 就是"固件刚写完、工具还没动手"的现场值。
> 反馈问题带上 `logs\fix.log`。

**怀疑被降频时**：双击 `scripts\10-性能自检.bat`（自动提权）→ 结果写 `scripts\性能自检-时间.txt`。
它查功耗墙状态 → 当前倍频/温度 → 单线程 7 秒（正常应到 4.8GHz 附近）→ 8 进程 7 秒（满载功耗/散热）→ 给结论；
能区分**"被功耗墙限住"**和**"散热到顶"**。

**散热上限**（`scripts\11-满载温度60秒.bat`）：78°C 起步单调爬升，**39 秒触 100°C**，末段稳态 99~100°C
（DTS 口径，满载失真）；到 100°C 时倍频 2.9→2.7GHz、功耗 49W→42W。即**散热只能稳定吐 ~42W**，
所以 PL1 给 40W 刚好贴顶留余量。

---

## 6. 已知限制 / 回滚

- 只管 PL1/PL2 这一条路径。**BD PROCHOT**、温度墙、电池/适配器识别问题导致的降频它管不了（要用 ThrottleStop 排查）。
  → 2026-10-18 补了观察手段：`scripts\13-EC诊断.bat` 的 **`throttle`** 子命令会把"倍频掉下来那一瞬间"连同
  功耗墙 / `PROCHOT`(封装与 core) / EC 状态位一起记下来，用来判定到底是哪条线在压 CPU。见 `05` §7。
- MMIO 偏移按 `0x59A0`（Intel 近几代通用）。换机器先跑 `2-check.bat`，读不到或数值明显不对就是偏移不同。
- 开机阶段做不到零延迟（0.4GHz 时 Windows 自己启动慢十倍），本机实测 14~24 秒。
- 开了**内存完整性（HVCI）**时可能拒绝加载这类直接访问物理内存的驱动（本机是关的）。
- **需要重启的三种情况**：① `mmio_mode` 在 disable/value 间切换；② value 模式升档超过已锁值；
  ③ 手动想改被锁死的 MMIO 寄存器。**改配置不用重启；改代码要重启服务。**
- **2026-10-18 改过 `CpuPLFix.py`**（不含在上面"三种情况"里，**不用重启服务** —— 服务不调用这几处，
  而 `boot`/`check` 每次都是新进程，改完立即生效）：
  ① `ec_rpm()` 去掉了"读转速前写 `0x31=0`"（本机 `0x31` 是 `HUWB`/`VPON`/`VRST`，不是老 X60 那代的"选风扇"；
  实测写与不写读到的转速完全一样），并把 `0x84`/`0x85` 两次读放进同一把 EC 互斥体里；
  ② `cmd_fan` 状态里 `ec_rpm()` 原来被调了两遍 → 只读一次；
  ③ `2-check.bat` 多一行 `其它 EC 温度`（TTS1/TTS6/TTS2/TTS3/TTS4/HWAT，**只看不参与逻辑**）；
  ④ **`2-check` 的整屏输出不再写进 `fix.log`**，只留一行摘要（原来 43 个 `check` 块占了日志一多半）；
  ⑤ **`boot` 的触发标签改成实测**（见 §5 那张表）：以前靠 uptime 猜，开机后 30 分钟内的 5 分钟重复
  全被记成"睡眠/休眠唤醒后"，是假记录；现在没睡过就记 `开机后复查`/`定时复查`，且状态正常只记一行。
  回滚：`%APPDATA%\CpuPLFix\CpuPLFix.py.bak-20261018-before-0x31-fix`（这一份**同时**覆盖上面 ①~⑤）
  覆盖回去即可；新增的 `logs\boot_last.txt` 可留可删。详见 `05` §3。
- 与联想/Intel 自家工具**不冲突**（Vantage、Intel IPF、Intel DTT、Windows 电源计划全走用户态 WMI/ACPI/EPP）；
  但 **ThrottleStop / XTU / QuickCPU 没装就别装**（会和它抢寄存器）。

```powershell
# 不用 9-uninstall.bat 时的手动回滚
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
   （当时只有 `1-install.bat` 恰好写对了）。现在剩下的 bat 由**同一份模板生成**，
   且保证 **纯 ASCII + CRLF + 无 BOM**。
   不弹 UAC 的验证办法：`cmd /c "set CPF_ELEV=1& "<路径>\2-check.bat" < nul"`。
2. **EC 操作必须串行**：服务、bat、临时脚本读写 EC 走同一套端口事务，两个进程同时做会互相插队、
   读到别人的应答。对策（互斥体 + 一致性校验 + 读前等待 + 标志两轮确认 + 温度突变过滤）全部在 `03` §4。
