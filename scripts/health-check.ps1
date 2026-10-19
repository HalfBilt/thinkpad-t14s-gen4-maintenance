<#
  体检.ps1 — ThinkPad 一键体检
  ------------------------------------------------------------------
  用途：随时检查"这次开机有没有问题"
  用法：双击同目录的 体检.bat，或：
        powershell -ExecutionPolicy Bypass -File "体检.ps1"
  权限：普通权限即可（个别项目会标注需提权）
  输出：屏幕打印，同时在同目录保存一份 体检报告-时间.txt
  检查：系统/应用错误 · 设备异常 · 驱动包 · 关键服务 · 安全 ·
        音频(WinSoftVol / SAC / Code39) · 降频值守(CpuPLFix / 频率) · 固件限速
  ------------------------------------------------------------------
#>

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

$now  = Get-Date
$os   = Get-CimInstance Win32_OperatingSystem
$boot = $os.LastBootUpTime

$lines = New-Object System.Collections.ArrayList
function W($s = '') { [void]$lines.Add([string]$s); Write-Host $s }

# 需要留意的事件ID（真问题）
$watch = @(7000, 7009, 7026, 41, 6008, 1001, 7031, 7034, 7001, 7011, 7023, 7024)

W "================ 电脑体检报告 ================"
W ("时间     : {0}" -f $now)
W ("开机     : {0}" -f $boot)
W ("已运行   : {0} 小时" -f [math]::Round(($now - $boot).TotalHours, 1))
W ""

# ---------- 1. 系统错误/警告 ----------
W "---- 1) 系统 错误/警告（本次开机） ----"
$sys = @(Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=$boot} -ErrorAction SilentlyContinue)
W ("总数: {0}" -f $sys.Count)
if ($sys.Count -gt 0) {
    $sys | Group-Object Id, ProviderName | Sort-Object Count -Descending | ForEach-Object {
        $idPart = ($_.Name -split ',\s*')[0]
        $mark = ''
        if ($watch -contains [int]$idPart) { $mark = '   <<<< 需要留意' }
        W ("  {0,4} 条  {1}{2}" -f $_.Count, $_.Name, $mark)
    }
}

# ---------- 2. 需要留意的具体内容 ----------
W ""
W "---- 2) 需要留意的具体内容 ----"
$hit = @($sys | Where-Object { $watch -contains $_.Id })
if ($hit.Count -eq 0) {
    W "  无"
} else {
    $hit | Sort-Object TimeCreated | ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 130) { $m = $m.Substring(0, 130) }
        W ("  [{0}] EID{1}  {2}" -f $_.TimeCreated.ToString('HH:mm:ss'), $_.Id, $m)
    }
}

# ---------- 3. 应用错误 ----------
W ""
W "---- 3) 应用 错误（本次开机） ----"
$app = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Level=1,2; StartTime=$boot} -ErrorAction SilentlyContinue)
W ("总数: {0}" -f $app.Count)
if ($app.Count -gt 0) {
    $app | Group-Object ProviderName, Id | Sort-Object Count -Descending | ForEach-Object {
        W ("  {0,4} 条  {1}" -f $_.Count, $_.Name)
    }
}

# ---------- 4. 设备异常 ----------
W ""
W "---- 4) 设备异常（Error / Disabled） ----"
$bad = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Error' -or $_.Status -eq 'Disabled' })
if ($bad.Count -eq 0) { W "  无" }
else { $bad | ForEach-Object { W ("  {0} | {1}" -f $_.Status, $_.FriendlyName) } }

# ---------- 5. 驱动包 ----------
W ""
W "---- 5) 驱动包数量 / 占用 ----"
$oemCount = @(Get-ChildItem "$env:SystemRoot\INF\oem*.inf" -ErrorAction SilentlyContinue).Count
$dsSize = (Get-ChildItem "$env:SystemRoot\System32\DriverStore\FileRepository" -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
W ("  第三方驱动包: {0} 个    DriverStore: {1} GB" -f $oemCount, [math]::Round($dsSize / 1GB, 2))

# ---------- 6. 关键服务 ----------
W ""
W "---- 6) 关键服务状态 ----"
foreach ($s in @('Intel(R) Platform License Manager Service', 'PRI-Driver', 'LenovoVantageService', 'dam')) {
    $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
    if ($svc) { W ("  {0}: {1} / {2}" -f $s, $svc.Status, $svc.StartType) }
    else      { W ("  {0}: 不存在" -f $s) }
}

# ---------- 7. 安全状态 ----------
W ""
W "---- 7) 安全（VBS / 内存完整性） ----"
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
if ($dg) {
    $st = switch ([int]$dg.VirtualizationBasedSecurityStatus) {
        0 { '未启用' }
        1 { '已配置但未运行' }
        2 { '运行中' }
        default { '未知' }
    }
    W ("  VBS 状态: {0}" -f $st)
    W ("  运行中的安全服务ID: {0}" -f ($dg.SecurityServicesRunning -join ', '))
} else {
    W "  （无法读取 DeviceGuard 信息）"
}
W ("  虚拟机监控程序存在: {0}" -f (Get-CimInstance Win32_ComputerSystem).HypervisorPresent)

# ---------- 8. 电池 / 硬盘 ----------
W ""
W "---- 8) 电池 / 硬盘 ----"
$bat = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
if ($bat) { W ("  电量: {0}%   电池状态码: {1}（2=接电源）" -f $bat.EstimatedChargeRemaining, $bat.BatteryStatus)
if (-not $bat.EstimatedChargeRemaining -or [string]::IsNullOrWhiteSpace($bat.Name)) {
    W "  [X] 电池读数无效（ACPI 返回 0xFFFFFFFF，界面会显示 -1）—— 处置: 关机 -> 拔适配器 -> 长按电源 30 秒 -> 插回开机（手册 03 §9）"
} }
Get-PhysicalDisk -ErrorAction SilentlyContinue | ForEach-Object {
    W ("  磁盘: {0} | {1} | {2}" -f $_.FriendlyName, $_.MediaType, $_.HealthStatus)
}

# ---------- 9. 音频（WinSoftVol / SAC / MEDIA 设备） ----------
W ""
W "---- 9) 音频（WinSoftVol / SAC / MEDIA 设备） ----"
$mediaIssue = 0

# 9.1 三件套：服务 / 驱动文件 / UpperFilters 挂载点
$wsvc = Get-Service WinSoftVol -ErrorAction SilentlyContinue
W ("  WinSoftVol 服务      : {0}" -f $(if ($wsvc) { "$($wsvc.Status) / $($wsvc.StartType)" } else { '不存在  <<<< 音量条可能失效' }))
if (-not $wsvc -or $wsvc.Status -ne 'Running') { $mediaIssue++ }
W ("  驱动文件             : {0}" -f (Test-Path "$env:SystemRoot\System32\drivers\WinSoftVol.sys"))
$uf = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}' -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
$ufStr = if ($uf) { ($uf -join ',') } else { '(读不到)' }
W ("  MEDIA UpperFilters   : {0}" -f $ufStr)
if ($ufStr -notmatch 'WinSoftVol') { W "      ^^ 没挂上 WinSoftVol → 系统音量条会无效（第七节 7.1）"; $mediaIssue++ }
if ($ufStr -notmatch 'ksthunk')    { W "      ^^ 少了 ksthunk，音频会坏（第七节 7.1 的坑）"; $mediaIssue++ }

# 9.2 智能应用控制（SAC 一转强制就会把 WinSoftVol 全家拦成 Code 39）
$sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
$sacTxt = switch ($sac) { 0 { '已关闭（正确）' } 1 { '强制  <<<< 会拦 WinSoftVol，随时 Code 39！' } 2 { '评估  <<<< 随时可能转强制，届时会没声' } default { '读不到' } }
W ("  智能应用控制(SAC)    : {0}" -f $sacTxt)
if ($sac -eq 1 -or $sac -eq 2) { $mediaIssue++ }

# 9.3 MEDIA 类实际设备状态（忽略没插的显示器）
$mediaAll = @(Get-PnpDevice -Class MEDIA -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'Unknown' })
$mediaBad = @($mediaAll | Where-Object { $_.Status -ne 'OK' })
W ("  音频设备             : {0} 个正常 / {1} 个异常" -f ($mediaAll.Count - $mediaBad.Count), $mediaBad.Count)
foreach ($d in $mediaBad) {
    W ("      ❌ {0} | {1} | {2}" -f $d.Status, $d.FriendlyName, $d.ProblemDescription)
    if ("$($d.ProblemDescription)" -match '39|0x27') { W "         Code 39 = 过滤器加载失败，看 9.2 的 SAC 那行（第七节 7.2）" }
    $mediaIssue++
}

# 9.4 代码完整性日志：只看"本次开机"有没有新拦截（历史遗留的不用管）
$ciNew = @(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Id=3077,3004; StartTime=$boot} -ErrorAction SilentlyContinue |
           Where-Object { $_.Message -match 'WinSoftVol' })
$ciOld = @(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Id=3077,3004; StartTime=$boot.AddDays(-7); EndTime=$boot} -ErrorAction SilentlyContinue |
           Where-Object { $_.Message -match 'WinSoftVol' }).Count
if ($ciNew.Count -gt 0) {
    W ("  ⚠️ 本次开机已发生 {0} 次 WinSoftVol 被拦（最近 {1}）→ SAC 又在拦，声音随时会没，见第七节 7.2" -f $ciNew.Count, $ciNew[0].TimeCreated.ToString('MM-dd HH:mm'))
    $mediaIssue++
} else {
    W ("  本次开机拦截记录      : 无（近 7 天历史 {0} 条，是 SAC 关闭前的遗留，不用管）" -f $ciOld)
}

# ---------- 10. CPU 功耗墙 / 降频值守 ----------
W ""
W "---- 10) CPU 功耗墙 / CpuPLFix 值守 ----"
$cpuIssue = 0

# 10.1 值守组件
$plSvc = Get-Service CpuPLFixSvc -ErrorAction SilentlyContinue
$tsCtl = Get-Service TSCtl -ErrorAction SilentlyContinue
W ("  CpuPLFixSvc 看守服务 : {0}" -f $(if ($plSvc) { "$($plSvc.Status) / $($plSvc.StartType)" } else { '不存在  <<<< 开机可能被锁 0.4GHz' }))
if (-not $plSvc -or $plSvc.Status -ne 'Running') { $cpuIssue++ }
W ("  TSCtl 驱动服务       : {0}" -f $(if ($tsCtl) { "$($tsCtl.Status) / $($tsCtl.StartType)" } else { '不存在' }))

# 10.2 当前电源档位（对得上 config.json 的 40/64 与 28/55）
$onAC = ((Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue).BatteryStatus -eq 2)
W ("  当前电源 / 档位      : {0}" -f $(if ($onAC) { '接电源 AC（应 40/55W）' } else { '电池（应 20/28W）' }))

# 10.3 日志最近一次记录（非管理员查不到计划任务，用日志确认它在跑）
$logPath = Join-Path $env:APPDATA 'CpuPLFix\logs\fix.log'
if (Test-Path $logPath) {
    $lg = @(Get-Content $logPath -Tail 80 -ErrorAction SilentlyContinue)
    $stamp = ($lg | Where-Object { $_ -match '^=+ \d{4}-\d{2}-\d{2} ' } | Select-Object -Last 1)
    $state = ($lg | Where-Object { $_ -match '已是目标状态|修复后|检测到功耗墙异常|电源档位|电源事件' } | Select-Object -Last 1)
    W ("  日志最近一次记录     : {0}" -f $(if ($stamp) { ($stamp -replace '^=+ | =+$', '') } else { '（日志为空）' }))
    if ($state) {
        $s = $state.Trim(); if ($s.Length -gt 120) { $s = $s.Substring(0, 120) + '…' }
        W ("  日志最近状态         : {0}" -f $s)
    }
    if (-not $stamp) { W "      ^^ 日志里没有任何记录，工具可能没在跑" ; $cpuIssue++ }
} else {
    W "  日志                 : 不存在（$logPath）→ CpuPLFix 未安装？"
    $cpuIssue++
}

# 10.4 现场频率（0.4GHz = 倍频 4，一眼就能看出来）
$perf = @(Get-Counter '\Processor Information(*)\% Processor Performance' -MaxSamples 1 -ErrorAction SilentlyContinue |
          ForEach-Object { $_.CounterSamples } | Where-Object { $_.InstanceName -notmatch '_total' } |
          ForEach-Object { [math]::Round($_.CookedValue * 18) } | Sort-Object)
if ($perf.Count -gt 0) {
    W ("  16 核频率(MHz)       : {0}" -f ($perf -join ' '))
    W ("  最高 / 最低          : {0} / {1}" -f $perf[-1], $perf[0])
    if ($perf[-1] -lt 700) {
        W "  ❌ 所有核都压在 0.7GHz 以下 —— 疑似被固件锁低。跑 2-check.bat 看功耗墙，救急可双击 %APPDATA%\CpuPLFix\1-install.bat（手册 01）"
        $cpuIssue++
    }
} else {
    W "  16 核频率            : 读不到"
}

# ---------- 11. 固件限速 ----------
W ""
W "---- 11) 固件限速（EID37） ----"
$e37 = @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=37; ProviderName='Microsoft-Windows-Kernel-Processor-Power'; StartTime=$boot} -ErrorAction SilentlyContinue)
W ("  本次开机 {0} 条（开机约 62 秒出现、71 秒后自愈，属已知现象；不能拿它判断降频，见手册第三节）" -f $e37.Count)

# ---------- 12. 结论 ----------
W ""
W "---- 12) 结论 ----"
$realIssue = $hit.Count + $app.Count + $bad.Count + $mediaIssue + $cpuIssue
if ($realIssue -eq 0) {
    W "  ✅ 未发现实际问题。系统错误 0、应用错误 0、设备异常 0、音频 0、降频值守 0。"
} else {
    W ("  ⚠️ 有 {0} 处需要留意：系统 {1} / 应用 {2} / 设备 {3} / 音频 {4} / 降频值守 {5}" -f `
        $realIssue, $hit.Count, $app.Count, $bad.Count, $mediaIssue, $cpuIssue)
    W "     请打开上一级的《README.md》（电脑维护手册）第三节（无害警告）与第四节（真问题）对照。"
}
W ""
W "================ 报告结束 ================"

# ---------- 保存 ----------
try {
    $dir = if ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }
    $file = Join-Path $dir ("体检报告-{0}.txt" -f $now.ToString('yyyyMMdd-HHmmss'))
    $lines | Out-File $file -Encoding utf8
    Write-Host ""
    Write-Host ("报告已保存: {0}" -f $file) -ForegroundColor Green
} catch {
    Write-Host "（报告保存失败，不影响上面结果）" -ForegroundColor DarkYellow
}

if (-not $env:HERMES_NO_PAUSE) {
    Write-Host ""
    Read-Host "按回车键退出"
}
