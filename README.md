# 电脑维护经验 · ThinkPad T14s Gen 4（工程机 / ES）

> 快速入口。本手册只记**当前结论**：机器是什么样、现在怎么配、哪里有坑。
> 改动历史在 git 记录和 <https://github.com/HalfBilt/T14scontrol> 的仓库里，不在这里。
>
> **维护约定**：本文档只在"结论变了"时改，不记过程；参数以 [`08-参数速查表.md`](08-参数速查表.md) 为唯一来源；
> 已作废的结论直接删，不留"以前是……"的说法（要考古去看 git log）。

**先读 [`00-电脑综述.md`](00-电脑综述.md)** —— 机器档案、当前状态、关键设计决定（含回滚）、出问题先做什么。
**要调参数看 [`08-参数速查表.md`](08-参数速查表.md)** —— 全部参数当前值一页看完（**全手册唯一参数来源**）。

---

## 交接摘要（给下一个 AI，2026-11-02 12:35）

> 本机是**工程样机**，很多"标准 ThinkPad 做法"不适用；先看这一页，再动任何设置。

| 项 | 当前值 / 结论 |
|---|---|
| 机器 | T14s Gen 4 **ES**，CPU `Genuine Intel(R) 0000`（Raptor Lake-P，12C/16T，≈i7-1360P），型号串 `ICL_____`，序列号 `ING` |
| 固件 | BIOS `N3PET05A (0.04)` / EC `N3PHT02w`；**工程机无法升级 BIOS/EC**，不要刷零售 BIOS（ES CPU 无零售微码，变砖风险） |
| 系统 | Windows 11 26H2，Build 26300.9818；Fast Startup **已关**（2026-11-02）；`w32time` **Automatic + 已校时** |
| 电源 | 电池 48,140/57,000 mWh = **84.5%**，72 循环；当前 100%；充电阈值 **0/0** |
| 稳定性 | 10-31 12:35 起连续约 **48 小时**；本次开机 ACPI **13=0、15=5**（15 是冷启动固有，无害） |
| 未决 | 6008 硬断电 7 次（最后 10-31 10:32，之后约 50 小时未再断）；**唯一没做的隔离实验 = 只用电池跑 20~30 分钟**；HAL 21/RTC（w32time 兜底） |
| 不要做 | 刷 BIOS、回退 PawnIO、长期停风扇服务、同时跑多个 EC/MSR 工具（HWiNFO/ThrottleStop/TPFanCtrl2） |

---

## 数字怎么复核（给下一个 AI 或未来的自己）

**本手册的每个数字都应该能现场重算。** 怀疑过期就按这张表跑一遍，别照抄——照抄过期数字比不懂更糟。

| 断言 | 复核方法 |
|---|---|
| CPU 12C/16T、L3 18MB | `Get-CimInstance Win32_Processor \| fl Name,NumberOfCores,NumberOfLogicalProcessors,L3CacheSize` |
| 内存 32GB | `(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB` |
| Windows 26H2 / 26300.9818 | `Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' \| fl DisplayVersion,UBR` |
| BIOS `N3PET05A` | `Get-CimInstance Win32_BIOS \| fl SMBIOSBIOSVersion,ReleaseDate` |
| 电池 48140/57000、72 循环、BYD | `powercfg /batteryreport` → `DESIGN CAPACITY` / `FULL CHARGE CAPACITY` / `CYCLE COUNT` / `MANUFACTURER` |
| Secure Boot 关 | `Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State'` → `UEFISecureBootEnabled=0` |
| VBS / 内存完整性 关 | `Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard Win32_DeviceGuard` → `VirtualizationBasedSecurityStatus=0` |
| 测试签名 开 | `bcdedit /enum {current}` → `testsigning Yes`（管理员；2026-11-02 实测）。佐证：自签驱动 `WinSoftVol.sys` 在 RUNNING |
| 功耗墙 28/55W | `%APPDATA%\T14scontrol\status\cpu.json` 的 `pl1`/`pl2`，或 `cpu\2-查看状态.bat` |
| 事件统计（13/15/88/41/6008） | `Get-WinEvent -FilterHashtable @{LogName='System'; Id=13,15,88,41,6008}` —— **先确认日志窗口起点**，本机 20MB 日志约只覆盖 3 周 |
| EC 寄存器 / 充电阈值 | `scripts\13-EC诊断.bat dump`（管理员，只读） |
| EC 总线撞车率 | `%APPDATA%\T14scontrol\cpu\logs\fix.log` 里的「EC 总线体检」行 |
| 风扇档位↔转速 | `scripts\12-风扇天花板实测` 的档位实测记录（`scripts\风扇档位-*.txt`） |
| 固件会不会抢 `0x2F` | `scripts\fan_ab_test.py native`（管理员）复跑，看 `0x2F` 是否被改写 |

> **写作约定**：能复核的写数字 + 来源命令；复核不到的写「未核实」，不写「从未 / 永久 / 必然」这类无法证明的话。
> 任何一条被实测推翻，直接删掉或改写，不留「以前是……」——要考古看 git log。

---

## 快速操作

| 要做什么 | 入口 |
|---|---|
| 一键体检 | `scripts\体检.bat`（结果存 `scripts\体检报告-时间.txt`） |
| 一次核对全部事实（管理员） | `scripts\31-事实核对.ps1` → `scripts\事实核对-时间戳.txt` |
| 查系统状态（管理员） | `%APPDATA%\T14scontrol\cpu\2-查看状态.bat` |
| 查风扇 | `%APPDATA%\T14scontrol\fan\3-风扇服务状态.bat` |
| 改配置 | CPU：`CpuPLFixCfg.py`（1 分钟内自动生效）；风扇：改 `fan\config.json` 后重启服务 |
| 离线自检（改代码后必跑） | `cpu\dev\selftest.py`（84 项，不碰 EC）· `fan\src\fan_control.py --selftest` |
| 整机重启后恢复 | `%APPDATA%\T14scontrol\0-重启后部署.bat`（管理员） |
| 查充电状态（只读） | `Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus \| Select PowerOnline,Charging,ChargeRate` |
| 清充电阈值锁存（管理员） | `scripts\19-清除充电阈值锁存.ps1`，然后 `shutdown /s /full` |
| 校时 | `w32tm /resync`（`w32time` 已 Automatic；`w32tm /query /status` 看结果） |
| 备份 | 只需备份 `cpu\config.json` + `fan\config.json` 两个文件（都在 T14scontrol 仓库里） |

```
%APPDATA%\T14scontrol\        工具根目录（runtime 共用 Python 3.13 / cpu / fan / hud / status）
Desktop\Hermes\电脑维护经验   本手册 + scripts\
```

> 本仓库**只放文档和 `scripts\`**，工具本体（CpuPLFix / FanControl / HUD）在
> <https://github.com/HalfBilt/T14scontrol>：部署目录 `%APPDATA%\T14scontrol` 本身就是它的 git 工作区，
> 重装按 [`09`](09-备份与一键还原.md) 还原，不要从这里拷代码。

---

## 文档索引

| 文档 | 管什么 |
|---|---|
| [`00-电脑综述.md`](00-电脑综述.md) | **先读**：机器档案 · 现状 · 设计决定 · 出问题先做什么 |
| [`01-CpuPLFix-降频修复.md`](01-CpuPLFix-降频修复.md) | 锁 0.4~0.55GHz：根因 · 工具 · 配置 · 启动机制 · 验证 · 回滚 |
| [`02-风扇与过热保护.md`](02-风扇与过热保护.md) | 风扇寄存器与曲线 · 固件抢档 · `_HOT` 强制休眠判据 · 过热保护原理 |
| [`03-EC-寄存器与读写.md`](03-EC-寄存器与读写.md) | **EC 手册**：寄存器地图 · 读写协议 · 并发规则 · 自己读写操作手册 |
| [`04-音频-WinSoftVol与SAC.md`](04-音频-WinSoftVol与SAC.md) | 音量条无效 · 突然没声 / 全部 Code 39 |
| [`05-EC-寄存器全表与电池充电阈值.md`](05-EC-寄存器全表与电池充电阈值.md) | **EC 反汇编全表**（`0x00~0xFF`）· 充电阈值 · **§2.1 阈值锁存修复** · 固件 SMI |
| [`06-EC现状基线与未决问题.md`](06-EC现状基线与未决问题.md) | EC 总线正常基准 · 不用当故障查的现象 · **§2.1 充电阈值锁存** · 残留观察项 |
| [`07-工具部署与目录结构.md`](07-工具部署与目录结构.md) | 目录 / 服务 / 运行时 · 安装 · 改配置 · 编码约定 · 排障 |
| [`08-参数速查表.md`](08-参数速查表.md) | **一页速查**：全部参数当前值 · 温度→动作 · 档位→RPM · 正常基准 |
| [`09-备份与一键还原.md`](09-备份与一键还原.md) | 备份位置（`F:\T14s驱动全备份`）· 重装后一键还原 |
| [`10-突然断电排查-2026-10-31.md`](10-突然断电排查-2026-10-31.md) | 突发硬断电（6008，**未定性**）：7 次 / 最后一次 10-31 10:32 / 之后约 50 小时未再断 · 已排除项 · EC 复位预案 · 下一步 |
| [`scripts/README.md`](scripts/README.md) | **脚本总索引**：体检 / 实测 / 音频 / 系统维护共 19 组脚本，各管什么 · 要不要管理员 · 写哪个报告 |

> 脚本都在 `scripts\`，**全量清单见 [`scripts/README.md`](scripts/README.md)** —— 包括体检、性能自检、
> 满载温度、EC 诊断、风扇实测，以及文档正文里没提到的维护类脚本（驱动包清理、孤儿启动项、WD SES 修复等）。
> `scripts\` 下的 `*-时间戳.txt` 是历史实测记录，已被 `.gitignore` 排除、**不入库**。
