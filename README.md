# 电脑维护手册

> ThinkPad T14s Gen 4（**工程机 / ES**）｜数据为 **2026-10-18 实测**
> 仓库：<https://github.com/HalfBilt/thinkpad-t14s-gen4-maintenance>｜重装系统后按**第十一节**拉回来
>
> 只想确认"这次开机有没有事"→ **双击 `scripts\体检.bat`**，其余按下面的表查。
>
> 风扇 / 散热 / 固件热区（强制休眠、`_HOT`、EC 过热标志）那一条线**单独一份**：
> **[`风扇与过热保护.md`](风扇与过热保护.md)** —— 本手册不重复。

---

## 一、机器档案

| 项目 | 值 |
|---|---|
| 机型 | ThinkPad T14s Gen 4（**工程机 / ES**，型号串 `ICL_____`） |
| CPU | `Genuine Intel(R) 0000`，12 核 16 线程，ES 上报 1800MHz（零售对应 i7-1360P，睿频 5.0GHz） |
| BIOS | `N3PET05A (0.04)`，2022-12-02 —— 极旧，**工程机勿升** |
| 内存 / 显卡 | 32GB / Intel Iris Xe（驱动 32.0.101.7088）＋ 虚拟显示器 GameViewer |
| 无线 | Intel Wi-Fi 6E AX211 160MHz（驱动 24.40.0.4） |
| 磁盘 | KINGSTON SNVS1000G SSD 932GB（Healthy）；另有外置 WD Elements SE 2TB |
| 系统 | Windows 11 专业版 26H2，Build 26300.9711 |
| 睡眠 | 支持 S3 / 休眠 / 混合睡眠 / 快速启动；**不支持** S1 / S2 / S0 |
| 安全 | Secure Boot 关；VBS / 内存完整性关；测试签名**开**；智能应用控制(SAC)**关**（见七.2） |
| 电源 | 电源计划 = 平衡；AC 电源模式 = 最佳性能 |
| 电池 | 设计 57,000 / 满充 48,180 mWh → **健康度 84.5%，70 次循环**（正常损耗，跌破 80% 再考虑换） |
| 温度 | **别用 WMI 读热区**：ACPI 热区 `\_TZ.THM0` 是存在的，它的 `_TMP` 其实就是去读 EC 的 `0x78`（见《风扇与过热保护》），但用户态常用的那个 WMI 查询（`MSAcpi_ThermalZoneTemperature`）在本机**会把调用方永久挂死**（实测卡死一个后台进程 20 分钟，只能重启）——**别碰**；`_PSV = 0K` 只是说没有被动降温档。CPU 的 DTS 可用，但**必须按 SDM 换算**：`温度 = TjMax − ((MSR 0x1B1>>16)&0x7F)`，`TjMax` 读 `MSR 0x1A2`（本机 = 100）。⚠️ 直接把差值当摄氏度会得出"满载 0°C"的荒谬值——是公式错，不是寄存器失真。空闲可信、**满载会失真**；要绝对值用 EC `0x78` 或 Vantage / HWiNFO64 交叉验证 |

> SSD 的 SMART 寿命 Windows 读不到（OEM 盘通病，`MSStorageDriver_FailurePredictStatus` 不支持）。想看磨损率装 Kingston SSD Manager 或 CrystalDiskInfo。

## 二、三条铁律

1. **工程机 → 不升 BIOS**；驱动只用**联想官方版**（13 代平台别混用 Intel 通用版）。
2. 第三节列的警告 = 正常，**不用管**。
3. **别用联想管家"一键更新全部驱动"**（一次装几十个，大半用不到）。

## 三、日志速查

**看到不用管的（无害：需要确认，可能是别的AI分析错误）**

| ID | 来源 | 说明 |
|---|---|---|
| **37** | Kernel-Processor-Power | 开机初期"受固件限制"，每次 6 条 / 约 71 秒。**这条不可信**：工具在开机 14 秒就把功耗墙修好了，它照样报"受限 71 秒"；实测倍频能到 4.8GHz、`PROCHOT=0`。**判断降频看倍频和 MMIO（第八节），别拿它当依据** |
| **11** | FilterManager | `ClawFlt 不支持绕过 IO`。ClawFlt = **Clash Verge 服务模式**（不落 `.sys` 的临时驱动，磁盘上搜不到属正常） |
| **219** | Kernel-PnP | WUDFRd 首次加载失败（`0xC0000365`）后重试成功；4 个相关设备均已 code=0 |
| **225** | Kernel-PnP | 有进程占用导致设备移除被阻（`audiodg` / Vantage） |
| **10016 / 10010** | DistributedCOM | DCOM 权限噪音（ShellServiceHost、WaaSMedicSvc，都是微软自家组件的配置问题） |
| **6062 / 7021 / 7018** | Netwtw14 | 无线网卡正常初始化 |
| **15 / 21** | ACPI / HAL | 老固件小缺陷（EC 查电池返回多余数据、RTC 方法失败），消息本身就说"无需进一步操作" |
| **1796** | TPM-WMI | 安全启动更新失败（Secure Boot 关，预期） |
| **7000 / 7009** | Service Control Manager | 开机超时，**不是每次都有**。几乎全是第三方程序服务（SWVisualize2024、OfficePLUS、ListaryServiceV2、PC Manager…）→ 无害；只有指向**系统组件或联想驱动**时才要查 |
| **7026** | Kernel-Boot | **不是每次都有**。`dam` / `uiomap` 是无文件的**孤儿项**（服务已删只剩名字）、`EhStorClass` 系统自带未启用，都正常 |

**真问题（出现才查）**

| ID | 含义 |
|---|---|
| **41 / 6008** | 非正常关机 / 意外重启 |
| **1001** | BugCheck（蓝屏），转储看 `C:\Windows\Minidump` |
| **7031** | 服务意外终止（反复出现才算问题） |
| **WHEA-Logger 任意** | 硬件错误，认真查 |
| **CodeIntegrity 3077 / 3004** | 驱动被签名策略拦了（多半是 SAC），见七.2 |
| **`dwm.exe` / 显卡驱动崩溃** | 显卡驱动问题，见第四节 |

> 一句话：**开机后只有上面那批 = 正常。** 2026-10-18 08:43 那次开机共 18 条警告、**0 条错误级**。

## 四、症状 → 怎么办

| 症状 | 先做什么 |
|---|---|
| 开机卡一会儿之后正常 | 不用管（Defender 首扫），CPU 约 15~25 秒恢复 |
| 持续负载时卡、频率上不去 | 第五节 ⑥（CPU 频率自检） |
| **开机 / 唤醒后直接掉到 0.4GHz** | **第八节**（CpuPLFix） |
| 日志有红叉 / 黄叹号 | 对照第三节 |
| 设备管理器有黄标 | `pnputil /enum-devices /problem` |
| 睡死 / 掉电快 | `powercfg /a`、`powercfg /lastwake`；本机用 **S3，别切 S0** |
| 共享 / 远程连不上本机（防火墙） | 看网络配置文件是否 **Public**（`Get-NetConnectionProfile`），家用 WiFi 应为 Private。2026-10-18 复查：WIFI-5G 已是 **Private**（另有一个 `Mihomo` 是代理虚拟网卡，Public 属正常） |
| 蓝屏 | 看 `C:\Windows\Minidump` |
| 怀疑内存 | `mdsched.exe`（需重启且耗时） |
| 开机瞬间黑一下（`dwm.exe` 崩） | 每天一次以内不用管；**反复出现才更新联想官方显卡驱动** |
| **内置扬声器系统音量调不了** | 第七节 7.1（WinSoftVol） |
| **突然完全没声、音频设备全 Code 39** | 第七节 7.2；跑 `scripts\09-诊断音频Code39.ps1` |
| 拿不准 | **双击 `scripts\体检.bat`** |

## 五、常用命令

```powershell
# 中文乱码先跑
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime

# ① 日志：本次开机的错误/警告汇总
Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=$boot} |
  Group-Object Id,ProviderName | Sort-Object Count -Descending |
  Select-Object Count,Name | Format-Table -AutoSize
# 关机方式：1074 计划 / 6006 正常 / 6008 意外 / 41 断电
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074,6006,6008,41; StartTime=$boot.AddHours(-2)} |
  Select-Object TimeCreated,Id,ProviderName | Format-Table -AutoSize

# ② 设备与驱动
pnputil /enum-devices /problem                        # 问题设备
pnputil /enum-drivers                                 # 所有第三方包
pnputil /add-driver "完整路径\xxx.inf" /install       # [管理员] 正规装驱动
pnputil /delete-driver oemNN.inf /uninstall           # [管理员] 删包（先 enum 确认编号）
pnputil /scan-devices                                 # [管理员] 重新扫描

# ③ 服务
Get-Service '<服务名>' | Select-Object Name,Status,StartType
Set-Service -Name '<服务名>' -StartupType Manual      # [管理员]
reg export "HKLM\SYSTEM\CurrentControlSet\Services\<服务名>" "$env:USERPROFILE\Desktop\svc-backup.reg" /y   # [管理员] 删前必做
sc.exe delete "<服务名>"                              # [管理员]

# ④ 电源 / 电池 / 睡眠 / 磁盘
powercfg /a; powercfg /getactivescheme
powercfg /lastwake; powercfg /waketimers              # [管理员]
powercfg /batteryreport /output "$env:USERPROFILE\Desktop\battery.html"
Get-CimInstance Win32_Battery | Select-Object Name,EstimatedChargeRemaining   # BatteryStatus=2 = 接电源
Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,Size

# ⑤ 安全
Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard |
  Select-Object VirtualizationBasedSecurityStatus,SecurityServicesConfigured,SecurityServicesRunning   # 0未启用 1已配置未运行 2运行中
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -EA 0).VerifiedAndReputablePolicyState   # SAC 0关 1强制 2评估
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -EA 0).SystemStartOptions   # 含 TESTSIGNING = 测试签名开着

# ⑥ CPU 频率自检：16 个逻辑核各 ≈ 多少 MHz（×18，因为基准 1800MHz）
Get-Counter '\Processor Information(*)\% Processor Performance' -MaxSamples 1 |
  ForEach-Object { $_.CounterSamples } | Where-Object { $_.InstanceName -notmatch '_total' } |
  ForEach-Object { [math]::Round($_.CookedValue * 18) } | Sort-Object
#   0–7 是 P 核线程，8–15 是 E 核。空闲时少数核偏低 = 正常（核心停靠）；16 个核同时很低才是异常。
#   真卡看队列：Get-Counter '\System\Processor Queue Length'，持续 >2 才是真忙不过来。
#   找吃 CPU 的进程：Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 ProcessName,Id,CPU
#   ❌ 别用"最大处理器状态 99%"降温（关掉睿频更慢）；散热用垫高 / 清灰 / Fn+Q 切性能模式
```

**删服务 / 启动项前"三查"**（任一不通过就别删）：

```powershell
$svc='<服务名>'
# ① 有服务依赖它？
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services' | ForEach-Object {
  $d=(Get-ItemProperty $_.PSPath -Name DependOnService -EA SilentlyContinue).DependOnService
  if($d -contains $svc){ $_.PSChildName } }
# ② 有设备绑定它？
pnputil /enum-devices /services | Select-String $svc
# ③ 历史上真的启动过？
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'} -MaxEvents 500 |
  Where-Object { $_.Message -match [regex]::Escape($svc) } | Select-Object TimeCreated,Id
```

> ⚠️ **绝不手动复制 `.sys` 到 `System32\drivers`**（缺 `.cat` 会验签失败）。任何驱动都用 INF 安装。

## 六、改过什么 & 怎么回滚

| # | 项目 | 做了什么 | 现状（10-18） | 回滚 |
|---|---|---|---|---|
| 1 | **内置扬声器系统音量无效** | 装 WinSoftVol 过滤驱动（七.1） | ✅ 生效中：`.sys` 在、MEDIA `UpperFilters = ksthunk,WinSoftVol`、服务 Running | `scripts\07-卸载WinSoftVol.ps1` |
| 2 | License Manager 开机卡 45 秒（7009） | 服务「自动」→「手动」 | 重装后已回到「自动」，但不再报它 → 无需处理 | `sc.exe config "Intel(R) Platform License Manager Service" start= auto` |
| 3 | PRI-Driver 启动失败（7000） | 用 INF 正规安装 | 服务已不存在，无 7000 报错 → 无需处理 | `pnputil /delete-driver oemNN.inf /uninstall` |
| 4 | `uiomap` / `dam` 孤儿引导项（7026） | `sc delete` | 服务已删除；7026 里**有时**仍会列名（正常） | 删前备份的 `.reg` 双击导入 |
| 5 | DriverStore 冗余清理 | `scripts\03-清理孤儿驱动包.ps1` | 曾 298 包/8.15GB → 74 包；现 **154 包 / 3.2GB**（重装+日常更新，正常） | 不可逆，重装/重插会自动装回 |
| 6 | VBS / Credential Guard 告警 | 当时开 BIOS VT-x 让 VBS 跑起来 | ⚠️ **现在 VBS 是关的、hypervisor 未运行** | 想开：`EnableVirtualizationBasedSecurity=1` + HVCI `Enabled=1` 后重启 |
| 7 | WD 移动硬盘 SES 黄标 | 插上跑修复脚本 | 未插时变幽灵设备，正常 | `scripts\05-修复-WD-SES设备.ps1` |
| 8 | `dwm.exe` 崩溃（10-17 11:28） | 未处理 | Intel 显卡用户态 `igd10um64xe.DLL` 访问违例，7 天第 1 次 | 反复出现 → 更新联想官方显卡驱动 |
| 9 | **10-17 20:24 起完全没声**（5 个音频设备全 Code 39） | 关闭**智能应用控制**（SAC） | ✅ 重启验证通过：5 个设备全部 code=0、`WinSoftVol` Running、代码完整性日志 0 条拦截 | ⚠️ **不可逆**：SAC 关掉后只能重装系统才能再开 |
| 10 | **开机 / 唤醒后 CPU 被锁 0.4GHz** | 装自研工具 **CpuPLFix**（第八节） | ✅ 已装并验证：开机 **14~24 秒**修复、唤醒 1~5 秒补锁 | `9-uninstall.bat`，或按八.5 手动删 |

## 七、音频

### 7.1 内置扬声器"系统音量无效"的根治

**症状**：系统音量条 / Fn 键完全无效（1~100 同响度，只有 0% 静音）；耳机正常；逐 App 调有效；重启后整体响度还会漂移。

**根因**：Intel SST + Realtek 暴露了一个**"假硬件音量节点"**（报 `KSNODETYPE_VOLUME` 但增益不生效），系统主音量走了这个坏节点。

**根治**：装 [WinSoftVol](https://github.com/dechamps/WinSoftVol) 内核过滤驱动，让 Windows 忽略坏节点、改用**软件音量**。未签名 → 需开测试签名（本机 Secure Boot 已关，可开）。

**装 / 重装**（跑 `scripts\06-安装WinSoftVol.ps1` 即可，它把下面五步全做完）：

1. 先装**联想官方音频驱动整包**（Intel SST + Realtek）—— 这步不做后面白搭
2. 确认 Secure Boot 关：`UEFISecureBootEnabled = 0`
3. 管理员跑 `scripts\06-安装WinSoftVol.ps1`（装证书 → 放 `.sys` → 建服务 → 挂 MEDIA `UpperFilters`）
4. 确认测试签名开（`SystemStartOptions` 含 `TESTSIGNING`）→ **重启**
5. 跑 UWD（`uwd_persistent_installer.exe` → Install）去掉"测试模式"水印

核对三条：

```powershell
sc.exe query WinSoftVol                                  # 期望 STATE : 4  RUNNING
Test-Path 'C:\Windows\System32\drivers\WinSoftVol.sys'   # 期望 True
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}' -Name UpperFilters).UpperFilters   # 期望 ksthunk,WinSoftVol
```

| 实物位置 | 值 |
|---|---|
| 驱动文件 | `C:\Windows\System32\drivers\WinSoftVol.sys` |
| 服务项 | `Services\WinSoftVol`：`Type=1`、`Start=3`、`ImagePath=\SystemRoot\System32\drivers\WinSoftVol.sys` |
| 挂载点 | MEDIA 类 `Class\{4d36e96c-…}` 的 `UpperFilters = ksthunk,WinSoftVol` |
| 卸载 | `scripts\07-卸载WinSoftVol.ps1` → 重启 |

**手动排错才看的四个坑**：证书必须装进「本地计算机\受信任的根」＋「受信任的发布者」；服务 `ImagePath` 必须写 `\SystemRoot\System32\drivers\WinSoftVol.sys`（写 `C:\...` 会报 123/3）；挂 `UpperFilters` 时**必须保留 `ksthunk`**；复制后要 `Unblock-File`。

- ⚠️ 它**不是程序**：任务管理器 / 托盘 / 桌面都看不到，只能 `sc query` 查——"在运行但找不到"是正常的
- **彻底清干净**：卸 WinSoftVol + `bcdedit /set testsigning off`（水印一起消失）+ 从 `UpperFilters` 删掉它 + `sc delete WinSoftVol`

### 7.2 突然没声、全部音频设备 Code 39（SAC 拦截）

**症状**：某刻起彻底没声；MEDIA 类 5 个设备全带黄叹号 **Code 39**；`pnputil /restart-device` 报成功但仍 39；重启多少次都不好。

**根因**：Windows 的**智能应用控制（SAC）**从「评估」自动转成了「强制」。WinSoftVol 是自签名驱动，SAC 一律拦；它挂在 MEDIA 类 `UpperFilters` 上，**一个过滤器加载失败 → 整类设备连带全 39**。

> 测试签名开着也救不了它：TESTSIGNING 放行的是"测试签名证书"，SAC 拦的是"没有信誉签名的二进制"，两套机制。

**怎么确认**（或跑 `scripts\09-诊断音频Code39.ps1`）：

```powershell
Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Id=3077,3004} -MaxEvents 50 |
  Where-Object { $_.Message -match 'WinSoftVol' } |
  Select-Object TimeCreated,Id,@{n='摘要';e={($_.Message -replace "`r`n",' ').Substring(0,150)}} | Format-List
```

判读：`id=3077` + `PolicyName = VerifiedAndReputableDesktop` → **SAC 在拦**；`id=3004`（file hash could not be found）是同一件事的另一面；`Status = 0xc0e90002` = 系统完整性策略违规（不是文件损坏、不是驱动没装好）。

**两条路**：

| 选 | 做法 | 结果 |
|---|---|---|
| **A（本机已选）** | 关 SAC：`scripts\08-关闭智能应用控制.ps1` → **重启** | 声音回来，音量条也一起恢复。⚠️ **SAC 关掉无法再开**（只能重装系统） |
| B（保守） | 卸 WinSoftVol：`scripts\07-卸载WinSoftVol.ps1` → **重启** | 声音回来，但 7.1 的"音量条无效"复发 |

> 设成 **2（评估）** 只能临时放行，评估期结束会再次自动转强制 —— 不推荐。

**以后别再踩**：只要这台机器还要跑未签名内核驱动（WinSoftVol 及以后类似的），**必须保持 SAC 关闭**。装完 Windows 大版本更新后，回来查一眼第一节"安全"那行。

## 八、CPU 被锁 0.4GHz 的修复（CpuPLFix）

> 源码与发布包 <https://github.com/HalfBilt/CpuPLFix>（仓库里带 `runtime.zip`，`1-install.bat` 会自动解压，**解压即用、不依赖系统 Python**）｜本机安装在 `C:\Users\Ting\AppData\Roaming\CpuPLFix`
>
> ⚠️ **工具本体不存进本仓库**（这里只放手册和脚本）。原因：工具在 GitHub 上有唯一的家，存第二份迟早会出现"两份谁新谁旧"的漂移。本仓库里**唯一**需要保管的工具相关东西，是下面 8.3 里那份本机专属的 `config.json`。
>
> 📎 这个工具现在还带**风扇曲线 + 过热保护**（同一个看守服务、开机自启）：那一条线的全部细节
> （EC 寄存器、DSDT 反汇编、`_HOT` 的真相、实测数据、踩过的坑）在 **[`风扇与过热保护.md`](风扇与过热保护.md)**。

### 8.1 症状与根因

开机 / 睡眠唤醒 / 休眠恢复后，CPU 被钉在 **0.40~0.55GHz**（倍频 4~5）。风扇不转、机器不烫，但什么都卡；重启有时好有时更糟。

Intel 封装功耗墙（PL1 长时 / PL2 短时）在硬件里存**两份镜像**：

| | 位置 | 谁在用 |
|---|---|---|
| 系统侧 | `MSR 0x610`（PKG_POWER_LIMIT） | Windows / ThrottleStop |
| 固件侧 | `MCHBAR + 0x59A0`（MMIO 镜像） | **主板 / EC 固件**，bit63 是锁定位 |

CPU 实际执行**固件侧那份**。固件会在开机 / 唤醒时把它改成小得离谱的值，四次开机抓到三个不同的值（**21W** → 全核 1.8GHz，**15W** → 2.6GHz，**5W** → 0.40GHz）。**每次都不一样**，是 EC 按电池 / 适配器 / 温度动态决定的 → 工具**不能写死，必须每次读现场值再覆盖**。

> 这也解释了 ThrottleStop 的两个现象：勾 **MMIO Lock** 就能修好（把正常值写回 MMIO 再把 bit63 置 1，固件就改不动了）；但改 55W 没效果（MMIO 锁定优先级高于 MSR）。
> **锁是双刃剑，挡住的也包括你自己** → 改功耗墙**必须重启**，本开机电周期内不可逆。

### 8.2 它怎么随系统启动（覆盖开机 / 唤醒 / 休眠恢复）

两套机制互相独立，任何一套单独都能修好。

1. **常驻服务 `CpuPLFixSvc`（主用）**：`Start=2` 自动，由 SCM 拉起，不需登录、无窗口。开机 **14~24 秒**修复；睡眠唤醒 / 休眠恢复靠 `SERVICE_ACCEPT_POWEREVENT` 主动通知，**≤1 秒**；另有 2 秒/5 秒轮询 + 时钟跳变检测兜底。
2. **计划任务 `CpuPLFix`（兜底）**：四个触发器——开机 +1 秒（30 分钟内每 5 分钟重复）、登录 +3 秒、`Power-Troubleshooter` 事件 1、`Kernel-Power` 事件 107。触发后**先预检**，正常就立刻退出；异常才修，然后看守 5 分钟防固件再补一刀。

> **关于"快速启动"**：`HiberbootEnabled = 1` 时点「关机」其实是把内核休眠到 `hiberfil.sys`，「再开机」是恢复而非冷启动 → uptime 不重置，工具可能把这次恢复**误判成"睡眠/休眠唤醒后"**（只是日志标签不准，修复功能不受影响）；PL 状态通常也被保留，预检直接通过。真正要警惕的是**从 S3 睡醒**（合盖再打开），固件几乎一定会重置 PL——好在服务 1 秒内就响应了。
> 嫌日志标签乱，可在「电源选项 → 选择电源按钮的功能」里**关掉快速启动**，关机变成真冷启动，代价是开机慢几秒。

### 8.3 文件与配置

| 项目 | 值 |
|---|---|
| 工具目录 | `C:\Users\Ting\AppData\Roaming\CpuPLFix`（`runtime\` 是内置 Python，别删） |
| 主程序 / 服务 | `CpuPLFix.py` / `CpuPLFixSvc.py` |
| 驱动 | `TSCtl.sys`（从 ThrottleStop 提取的签名驱动，服务名 `TSCtl`，按需启动） |
| 看守服务 | `CpuPLFixSvc`（`Start=2`、`Type=16`） |
| 计划任务 | `\CpuPLFix`（SYSTEM 身份；普通权限查不到它，用日志确认它在跑） |
| 日志 | `logs\fix.log` |

`config.json` 键位：

| 键 | 含义 |
|---|---|
| `pl1_w` / `pl2_w` | **插电档**长时 / 短时功耗墙 |
| `pl1_w_batt` / `pl2_w_batt` | **电池档** |
| `mmio_mode` | `disable`（推荐）开机把固件侧 MMIO 清零+锁死，之后**只写 MSR**，改配置免重启；`value`（老方案）写目标值进 MMIO+锁，改配置要重启 |

**本机配置**（2026-10-18）：`mmio_mode = disable`；插电 **PL1=40W / PL2=55W**，电池 **PL1=28W / PL2=55W**；
风扇走**自定义曲线**（`fan.mode = curve`），过热保护**开着**（`guard.enabled = true`，预警 95°C / 严重 98°C / 解除 78°C）。
**降频线与温控线是同一套数**：温控两档 = 插电档的 50%（20W/28W）与 25%（10W/14W），所有值都不超过 55W 这个顶。

这份就是本机实际在用的 `config.json`，也是重装后唯一要补回来的东西（工具仓库里的那份是分享默认值 `pl1_w=0` 自动模式，**不要用**）：

```json
{
  "pl1_w": 40.0,
  "pl2_w": 55.0,
  "pl1_w_batt": 28.0,
  "pl2_w_batt": 55.0,
  "pl1_time": 35,
  "pl2_time": 66,
  "min_pl1_w": 15.0,
  "lock_mmio": true,
  "mmio_mode": "disable",
  "fan": {
    "mode": "curve",
    "level": 0,
    "curve": [[50, 1], [58, 2], [66, 4], [74, 6], [82, 7], [999, 64]],
    "hyst": 1.5
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
    "force_fan_max": true,
    "trip_flags": true,
    "escape_hibernate": false
  },
  "comment": "本机实用配置。插电(AC): PL1=40W PL2=55W; 电池: PL1=28W PL2=55W。两条线一套数: 温控档 = 插电档的 50%(20/28) 与 25%(10/14), 顶统一 55W(固件原厂值)。mmio_mode=disable: 开机把固件侧 MMIO 清零+锁, 之后只写 MSR, 改配置/拔插电免重启。fan=风扇模式与温度曲线; guard=过热保护阈值(0x78 温度线 + EC 0x89/0x8A/0x8B 固件标志线, 详见 说明.txt)。"
}
```

> `fan` / `guard` 这两个段的含义、阈值为什么是这些数、以及背后的实测依据，都在
> **[`风扇与过热保护.md`](风扇与过热保护.md)**。改它们**不用重启**（服务每分钟重读一次配置）。

核对一致的命令（重装后可跑）：

```powershell
# 三份关键文件应与本手册记录一致
Get-Content "$env:APPDATA\CpuPLFix\config.json" -Raw      # 应与上面那段一致
(Get-Item "$env:APPDATA\CpuPLFix\TSCtl.sys").Length       # 50216
(Get-Item "$env:APPDATA\CpuPLFix\CpuPLFix.py").Length     # 本机 2026-10-18: 78489（会随版本变，只作参考）
(Get-Item "$env:APPDATA\CpuPLFix\config.json").Length     # 本机 2026-10-18: 1335
```

> 原理：MMIO 被禁用后 CPU 取 `min(MSR, MMIO)` 就只剩 MSR，于是改配置 1 分钟内自动生效、拔插电源秒级切换档位（服务订阅 `PBT_APMPOWERSTATUSCHANGE`），也不再依赖"开机时是什么电源状态"。
> ⚠️ 前提是 **MSR 0x610 可写**（本机早前实测可写）。若换机器 MSR 被 BIOS 锁，把 `mmio_mode` 改回 `value` 退回老方案。

**PL1 怎么选**（55W 实测 OK，但散热只能稳定吐 ~42W，见 8.4）：

| 想要 | `pl1_w` | 全核频率 |
|---|---|---|
| 满性能 | 55 | ~3.0GHz |
| **折中（当前）** | 40 | ~2.6GHz |
| 原厂长时限值 | 21 | 2.0~2.2GHz |
| 不操心 | 0 | 原厂怎么设就怎么用 |

> **PL2（短时爆发，35 秒）现在统一取 55W = 固件原厂值**。以前填过 64W：本机散热只能稳定吐 ~42W，
> 64W 只是更快撞上温度墙（不划算），而且会让"两条线"的比例和温控档对不上。电池档保持 28W/55W。

**闲置不费电**：PL 是**天花板**不是固定功耗。实测 6~9% 占用时封装只有 8.9~12.9W（约上限 1/5），大部分核进 C-state。真正耗电的是屏幕背光、Wi-Fi、后台程序；**睡眠(S3) 耗电与 PL 无关**。55W 的代价只在**满载**（更热、风扇更响、纯电池掉得更快）。

### 8.4 怎么用 / 怎么验证

```
1-install.bat      装（要管理员）
2-check.bat        看状态：当前电源/档位、两份功耗墙、频率、温度、固件过热标志、风扇、值守服务
3-fix-now.bat      立即修一次（降频时手动救急）
4-风扇状态.bat      看风扇档位/转速/EC 温度/固件过热标志
5-风扇曲线.bat      回到自定义曲线（推荐；闲置安静、热了能到满速）
6-风扇满速.bat      风扇压满速（会写进配置，服务一直维持，直到跑 5 恢复）
7-风扇交还EC自动.bat 交还给 EC 自己管（原生平线曲线，不推荐）
8-风扇固定档.bat    固定到某个档位
9-uninstall.bat    卸载（删服务 + 计划任务，不删文件）
```

```powershell
sc.exe query CpuPLFixSvc                                    # 期望 STATE : 4  RUNNING
Get-Content "$env:APPDATA\CpuPLFix\logs\fix.log" -Tail 40
```

正常日志（`svc` = 看守服务，`boot` = 计划任务）：

```
20:52:49 svc  检测到功耗墙异常: MMIO PL1=15.00W lock=0 (目标 40.00W), 重新锁定
20:52:49 svc  修复后: MMIO PL1=40.00W PL2=55.00W lock=1  [√] 已生效并锁定
20:52:50 boot 进程启动(开机): 系统已运行 15.2 秒 → 已是目标状态, 无需修复
```

唤醒后应出现这两行之一（证明唤醒被检查了）：

```
[*] 收到电源事件 0x0012 (自动唤醒), 判定刚从睡眠/休眠恢复, 立即重新检查
[*] 检测到 830 秒挂起间隔, 判定刚从睡眠/休眠恢复, 立即重新检查
```

插拔电源则应看到 `[*] 电源档位[AC/高性能/disable]: MSR PL1 28.0->40.0W PL2 55.0->55.0W`（两个档的 PL2 都是 55W，所以 PL2 看着没变，变的是 PL1）。

> 验证唤醒：**合盖睡 1 分钟 → 开盖 → 等 5 秒 → 看日志最后 20 行**。
> 开机第一条记录里的 **MMIO PL1** 就是"固件刚写完、工具还没动手"的现场值——想知道这次固件压到几瓦就看它。反馈问题带上 `fix.log`。

**怀疑被降频时的一键自检**（比 `2-check.bat` 多跑一组负载实测）：双击 `scripts\10-性能自检.bat`（会自动提权）→ 结果写进 `scripts\性能自检-时间.txt`。它依次查功耗墙状态 → 当前倍频/温度 → 单线程 7 秒（正常应到 4.8GHz 附近）→ 8 进程 7 秒（满载功耗/散热）→ 末尾直接给结论。

**温度墙实测（60 秒满载）**（复现：双击 `scripts\11-满载温度60秒.bat`）：78°C 起步单调爬升，**39 秒触 100°C**，末段稳态 99~100°C；到 100°C 的同时 **Thermal Status 位置 1**，倍频 2.9→2.7GHz、功耗 49W→42W。即**这机器散热只能稳定吐 ~42W**，所以 PL1 给 40W 刚好贴顶留余量。

> ⚠️ DTS 换算的读数**只有空闲时可信**，满载会失真。判断"满载被温度墙限频"看功耗 / 倍频回落，别只看温度数。
> 想压温度：清灰 + 换硅脂（工程机拆机谨慎）、垫高机身，或直接调低 `pl1_w`——长时负载几乎无性能损失，温度和噪音大降。

### 8.5 已知限制 / 回滚

- 只管 PL1/PL2 这一条路径。**BD PROCHOT**、温度墙、电池/适配器识别问题导致的降频它管不了，得用 ThrottleStop 排查。
- MMIO 偏移按 `0x59A0`（Intel 近几代通用）。换机器先跑 `2-check.bat`，读不到或数值明显不对就是偏移不同。
- 开机阶段做不到零延迟（0.4GHz 时 Windows 自己启动就慢十倍，用户态程序只能等），本机实测 14~24 秒。
- 开了**内存完整性（HVCI）**时可能拒绝加载这类直接访问物理内存的驱动。
- **需要重启的三种情况**（其余都不需要）：① `mmio_mode` 在 disable/value 间切换；② value 模式升档超过已锁值；③ 手动想改被锁死的 MMIO 寄存器。
- 与联想/Intel 自家工具**不冲突**：Vantage、Intel IPF、Intel DTT、Windows 电源计划全部走**用户态 WMI/ACPI/EPP 路线**，没有一个能裸写 `MSR 0x610`/MCHBAR。切电源模式（Fn+T / Vantage）会让频率行为变化那是 EPP 变了，功耗墙没动，属预期。反过来 **ThrottleStop / XTU / QuickCPU 没装就别装**（会和 CpuPLFix 抢寄存器）。

```powershell
# 不用 9-uninstall.bat 时的手动回滚
schtasks /delete /tn CpuPLFix /f                      # [管理员]
sc.exe stop CpuPLFixSvc; sc.exe delete CpuPLFixSvc    # [管理员]
sc.exe stop TSCtl;       sc.exe delete TSCtl          # [管理员]
```

## 九、睡眠 / 虚拟化

| | 是什么 | 谁需要 |
|---|---|---|
| ① **VT-x**（BIOS `Intel Virtualization Technology`） | 硬件虚拟化能力 | **任何虚拟化都要开，永远别关** |
| ② 微软 hypervisor | Windows 用 VT-x 跑的虚拟化框架 | Hyper-V / WSL2 / Docker / 沙盒 |
| ③ **VBS / 内存完整性** | 占用 hypervisor 的**安全**用法 | 只有安全需要 |

> 关 VBS ≠ 关 VT-x。VBS 会顺带启动 hypervisor，占了 VT-x 后第三方虚拟机（VMware/VBox）会冲突或变慢。

- **本机现状**：VBS / 内存完整性**关**、hypervisor 未运行 → 跑 VMware/VBox 无障碍。想开见第六节 #6。
- **睡眠**：BIOS 设 **S3，推荐保持**（省电、稳；工程机 + 老 BIOS 跑现代待机风险高）。`powercfg /a` 里"S0 固件不支持"是 S3 模式下的假象；想切 S0：BIOS → Config → Power → Sleep State → "Windows"，切了之后联想 Smart Standby 才生效。
- 睡眠正常标准：睡一晚掉电 ≤ 几个百分点、开盖能唤醒、包里不发烫。

## 十、scripts 目录与其它坑

| 脚本 | 用途 | 权限 | 怎么跑 |
|---|---|---|---|
| `体检.bat` | **一键体检（最常用）**，结果存成 `体检报告-时间.txt`。查：系统/应用错误 · 设备异常 · 驱动包 · 关键服务 · 安全 · **音频（WinSoftVol / SAC / Code39）** · **降频值守（CpuPLFix / 16 核频率）** · EID37 | 普通 | **双击** |
| `health-check.ps1` | 体检的实际脚本（被 `体检.bat` 调用） | 普通 | 双击 `体检.bat` |
| `01-…服务改手动-LicenseManager.ps1` / `02-…改回自动…` | 消除 License Manager 开机超时 / 回滚 | 管理员 | 见下 |
| `03-清理孤儿驱动包.ps1` | 清无用驱动包、释放空间（`-DryRun` 只预览） | 管理员 | 见下 |
| `04-删除孤儿启动项.ps1` | 删孤儿启动项（**必须带参数**） | 管理员 | 见下 |
| `05-修复-WD-SES设备.ps1` | 修 WD 移动硬盘 SES 黄标 | 管理员 | 见下 |
| `06-安装WinSoftVol.ps1` / `07-卸载WinSoftVol.ps1` | 装 / 卸 WinSoftVol（**需重启**） | 管理员 | 见下 |
| `08-关闭智能应用控制.ps1` | 关 SAC（WinSoftVol 被拦时用，**需重启**，**不可逆**） | 管理员 | 见下 |
| `09-诊断音频Code39.ps1` | **音频 Code 39 / 突然没声**一键定位 + 结论 | 普通 | **双击** |
| `10-性能自检.bat` / `.py` | **一键判断有没有被降频**：功耗墙（MSR / MMIO / 当前档）· 温度 · 单线程睿频上限 · 8 进程满载功耗。结果存 `性能自检-时间.txt`。**会自己弹 UAC 提权**；依赖已装 CpuPLFix（借它的 TSCtl 驱动读 MSR） | 管理员 | **双击** |
| `11-满载温度60秒.bat` / `.py` | 全核满载 **60 秒**的温度 / 功耗曲线 —— 看散热到底能稳定吐多少瓦。结果存 `满载温度-时间.txt`。同样自动提权 + 依赖 CpuPLFix | 管理员 | **双击** |
| `_负载发生器.py` | 上面两个用的烧机小程序（把一个 CPU 线程压满），不单独用 | — | 不用管 |

**以管理员运行**（文件名换成要跑的那个）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Ting\Desktop\Hermes\电脑维护经验\scripts\01-服务改手动-LicenseManager.ps1"
```

`04` 必须带参数：`-ServiceName uiomap`

> ⚠️ **`scripts` 里的 `.ps1` 必须保存为「UTF-8 带 BOM」**，否则 PowerShell 5.1 按 GBK 解析会报语法错（`MissingEndCurlyBrace`）。

> 体检**不查**温度 / 功耗墙具体数值（那要读 MSR/MMIO，需提权）——要查就双击 `scripts\10-性能自检.bat`（自动提权，依赖已装 CpuPLFix）。它比 CpuPLFix 自带的 `2-check.bat` 多测两项：**单线程睿频上限**（正常应到 4.8GHz）和 **8 进程满载功耗**，能直接区分"被功耗墙限住"和"散热到顶"。

**Hermes 桌面端两个坑（2026-10-12 解决）**

1. **后端起不来 / 检查更新失败** → 后端只认真实**用户级环境变量**和机器级+用户级 `PATH`（不认 shell 临时设置），`.env` 里的 `GITHUB_TOKEN` 读不到。改完要广播 `WM_SETTINGCHANGE` ＋**完全退出重开**（关窗口没用）。换 token 跑 `桌面\填入GitHub令牌.cmd`。
2. **别用 `--set-channel` 切"稳定版"** —— 那等于同意把 main 上的新代码降级成正式版标签。要稳定体验就**不要写渠道记录**。

## 十一、重装系统后怎么恢复

```powershell
git clone https://github.com/HalfBilt/thinkpad-t14s-gen4-maintenance.git
```

拉下来后按顺序补回（前 3 件是本机专属改动，重装系统和 Windows 更新都**不会**自动带回来）：

| 顺序 | 做什么 | 依据 |
|---|---|---|
| 1 | 装**联想官方音频驱动整包**（Intel SST + Realtek）→ 跑 `scripts\06-安装WinSoftVol.ps1` → 重启（恢复音量条） | 七.1 |
| 2 | 跑 `scripts\08-关闭智能应用控制.ps1` → 重启（**不做这步**，WinSoftVol 会被 SAC 拦成 Code 39、直接没声） | 七.2 |
| 3 | 装 **CpuPLFix**：`git clone https://github.com/HalfBilt/CpuPLFix` → **把 `config.json` 换成八.3 里那段**（仓库自带的是分享默认值）→ 右键**以管理员运行** `1-install.bat` → 重启 | 八.3 |
| 4 | 核对 BIOS 仍是 **S3**、Secure Boot **关**、测试签名**开**；测试模式水印用 UWD 去掉 | 一 · 九 |
| 5 | 跑 `scripts\体检.bat` 验收：应显示音频 0 问题、降频值守 0 问题 | 十 |
| 6 | 想亲眼确认没被降频：双击 `scripts\10-性能自检.bat`，应看到单线程 **4.8GHz** 上下 + `[√] 睿频通路正常` | 十 |

> 第一节里的电池健康度、DriverStore 包数、日志条数属于"当时状态"，不是要恢复的东西，重装后会自然重新累积。

> **仓库边界**：这里只存「手册 + `scripts\`」。**CpuPLFix 工具本体不存第二份**（唯一来源是 <https://github.com/HalfBilt/CpuPLFix>，里面带 `runtime.zip`）；本仓库里唯一和它相关的东西，是 8.3 里那段本机专属的 `config.json`。性能自检那两个脚本（`10` / `11`）**已经收进 `scripts\`**，但它们要借 CpuPLFix 的 Python 运行时和 TSCtl 驱动才能跑 —— 所以必须先做完上面第 3 步。
