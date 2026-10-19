# 04 · 音频：音量条无效（WinSoftVol）· 突然没声（SAC）

> 机器档案与现状见 [`00-电脑综述.md`](00-电脑综述.md)；相关脚本：`scripts\06-安装WinSoftVol.ps1`、
> `scripts\07-卸载WinSoftVol.ps1`、`scripts\08-关闭智能应用控制.ps1`、`scripts\09-诊断音频Code39.ps1`。

---

## 1. 系统音量条无效 → 装 WinSoftVol

**症状**：系统音量条 / Fn 键完全无效（1~100 同响度，只有 0% 静音）；耳机正常；逐 App 调有效；重启后响度漂移。

**根因**：Intel SST + Realtek 暴露了一个**假硬件音量节点**（报 `KSNODETYPE_VOLUME` 但增益不生效），
系统主音量走了这个坏节点。

**做法**：装 [WinSoftVol](https://github.com/dechamps/WinSoftVol) 内核过滤驱动，让 Windows 忽略坏节点、
改用软件音量。未签名 → 需开测试签名（本机 Secure Boot 已关，可开）。

**装 / 重装**：跑 `scripts\06-安装WinSoftVol.ps1` 即可（它做完下面五步）。

| 步骤 | 内容 |
|---|---|
| 1 | 先装**联想官方音频驱动整包**（Intel SST + Realtek）—— 不做这步后面白搭 |
| 2 | 确认 Secure Boot 关：`UEFISecureBootEnabled = 0` |
| 3 | 管理员跑 `scripts\06-安装WinSoftVol.ps1`：装证书 → 放 `.sys` → 建服务 → 挂 MEDIA `UpperFilters` |
| 4 | 确认测试签名开（`SystemStartOptions` 含 `TESTSIGNING`）→ **重启** |
| 5 | 跑 UWD（`uwd_persistent_installer.exe` → Install）去掉"测试模式"水印 |

**装完核对三条**

```powershell
sc.exe query WinSoftVol                                  # 期望 STATE : 4  RUNNING
Test-Path 'C:\Windows\System32\drivers\WinSoftVol.sys'   # 期望 True
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}' -Name UpperFilters).UpperFilters   # 期望 ksthunk,WinSoftVol
```

**它在哪 / 怎么卸**

| 项 | 值 |
|---|---|
| 驱动文件 | `C:\Windows\System32\drivers\WinSoftVol.sys` |
| 服务项 | `Services\WinSoftVol`：`Type=1`、`Start=3`、`ImagePath=\SystemRoot\System32\drivers\WinSoftVol.sys` |
| 挂载点 | MEDIA 类 `Class\{4d36e96c-…}` 的 `UpperFilters = ksthunk,WinSoftVol` |
| 卸载 | `scripts\07-卸载WinSoftVol.ps1` → 重启 |

**重装时的四个要点**（不照做会失败）：证书必须装进「本地计算机\受信任的根」+「受信任的发布者」；
服务 `ImagePath` 必须写 `\SystemRoot\System32\drivers\WinSoftVol.sys`（写 `C:\…` 会报 123/3）；
挂 `UpperFilters` 时**必须保留 `ksthunk`**；复制完 `.sys` 要 `Unblock-File`。

- ⚠️ 它**不是程序**：任务管理器 / 托盘 / 桌面都看不到，只能 `sc query` 查 ——"在运行但找不到"是正常的。
- **彻底清干净** = 卸 WinSoftVol + `bcdedit /set testsigning off`（水印一起消失）+ 从 `UpperFilters` 删掉它 + `sc delete WinSoftVol`。

---

## 2. 突然没声、音频设备全 Code 39 → 关 SAC

**症状**：某刻起彻底没声；MEDIA 类 5 个设备全带黄叹号 **Code 39**；`pnputil /restart-device` 报成功但仍 39；重启多少次都不好。

**根因**：Windows **智能应用控制（SAC）**从「评估」转成「强制」，一律拦自签名驱动；WinSoftVol 挂在 MEDIA 类
`UpperFilters` 上，**一个过滤器加载失败 → 整类设备连带全 39**。（测试签名开着也救不了：TESTSIGNING 放行的是
"测试签名证书"，SAC 拦的是"没有信誉签名的二进制"，两套机制。）

**确认办法**（或跑 `scripts\09-诊断音频Code39.ps1`）

```powershell
Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Id=3077,3004} -MaxEvents 50 |
  Where-Object { $_.Message -match 'WinSoftVol' } |
  Select-Object TimeCreated,Id,@{n='摘要';e={($_.Message -replace "`r`n",' ').Substring(0,150)}} | Format-List
```

判读：`3077` + `PolicyName = VerifiedAndReputableDesktop` = SAC 在拦；`3004`（file hash could not be found）
是同一件事的另一面；`Status = 0xc0e90002` = 系统完整性策略违规（**不是文件损坏、不是没装好**）。

**两条路**

| 选 | 做法 | 结果 |
|---|---|---|
| **A（本机已选）** | `scripts\08-关闭智能应用控制.ps1` → **重启** | 声音与音量条都回来。⚠️ **SAC 关掉无法再开**（只能重装系统） |
| B | `scripts\07-卸载WinSoftVol.ps1` → **重启** | 声音回来，但 §1 的"音量条无效"复发 |

> 设成 **2（评估）** 只能临时放行，评估期结束会再次转强制 —— 不推荐。

**规矩**：只要这台机器还要跑未签名内核驱动（WinSoftVol 及以后类似的），**SAC 必须保持关闭**；
装完 Windows 大版本更新后回来查一眼（`00` 状态表 / 跑一遍体检）。

```powershell
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -EA 0).VerifiedAndReputablePolicyState
# 0 = 关（本机现状）  1 = 强制  2 = 评估
```

---

## 现状与回滚

| 项目 | 现状（2026-10-19） | 回滚 |
|---|---|---|
| WinSoftVol | ✅ `.sys` 在、服务 **RUNNING**、MEDIA `UpperFilters = ksthunk,WinSoftVol` | `scripts\07-卸载WinSoftVol.ps1` → 重启（音量条退回"无效"） |
| 智能应用控制（SAC） | ⚠️ **已关（不可逆）**；5 个音频设备 code=0、代码完整性日志 0 条拦截 | 无法回滚，只能重装系统 |
| 测试签名 / 水印 | 测试签名开（必须）；水印已用 UWD 去掉 | `bcdedit /set testsigning off`（水印回归，WinSoftVol 也不能用） |
