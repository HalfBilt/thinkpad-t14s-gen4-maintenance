# 09-诊断音频Code39.ps1
# 用途：音频设备报 Code 39 / 突然没声时，一条命令定位原因
# 权限：普通用户即可（代码完整性日志需要读权限，一般够）
# 用法：双击跑，或  powershell -NoProfile -ExecutionPolicy Bypass -File "本文件路径"

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

function Line($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }

Line '================ 1. 有问题的设备 ================' 'Cyan'
$bad = Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 }
if (-not $bad) {
    Line '  [√] 没有异常设备' 'Green'
} else {
    foreach ($d in $bad) {
        $c = if ($d.ConfigManagerErrorCode -eq 39) { 'Yellow' } else { 'Gray' }
        Line ("  code={0,-3} {1,-10} {2}" -f $d.ConfigManagerErrorCode, $d.PNPClass, $d.Name) $c
    }
}

Line ''
Line '================ 2. WinSoftVol 现状 ================' 'Cyan'
$svc = Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\WinSoftVol'
Line ("  服务项存在        : {0}" -f $svc)
Line ("  驱动文件存在      : {0}" -f (Test-Path 'C:\Windows\System32\drivers\WinSoftVol.sys'))
$uf = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}' -Name UpperFilters).UpperFilters
Line ("  MEDIA UpperFilters: {0}" -f ($uf -join ','))
$q = & sc.exe query WinSoftVol 2>$null | Select-String 'STATE'
Line ("  sc query          : {0}" -f ($q -replace '\s+', ' '))

Line ''
Line '================ 3. 安全开关 ================' 'Cyan'
$sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -Name VerifiedAndReputablePolicyState).VerifiedAndReputablePolicyState
$sacTxt = switch ($sac) { 0 { '关闭' } 1 { '强制  <-- 会拦未签名驱动' } 2 { '评估' } default { "未知($sac)" } }
Line ("  智能应用控制 (SAC): {0}" -f $sacTxt) $(if ($sac -eq 1) { 'Red' } else { 'Gray' })
$sso = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name SystemStartOptions).SystemStartOptions
Line ("  测试签名 TESTSIGNING: {0}" -f $(if ($sso -match 'TESTSIGNING') { '开' } else { '关  <-- WinSoftVol 需要它' })) $(if ($sso -match 'TESTSIGNING') { 'Gray' } else { 'Red' })
$sb = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State' -Name UEFISecureBootEnabled).UEFISecureBootEnabled
Line ("  Secure Boot        : {0}" -f $(if ($sb -eq 0) { '关' } else { '开  <-- 开着就跑不了未签名驱动' })) $(if ($sb -eq 0) { 'Gray' } else { 'Red' })

Line ''
Line '================ 4. 代码完整性拦截记录（最近 20 条） ================' 'Cyan'
$ci = Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-CodeIntegrity/Operational'; Id = 3077, 3004 } -MaxEvents 200
if (-not $ci) {
    Line '  没有拦截记录' 'Green'
} else {
    $ci | Select-Object -First 20 | ForEach-Object {
        $m = $_.Message -replace "`r`n", ' '
        if ($m.Length -gt 160) { $m = $m.Substring(0, 160) }
        Line ("  [{0:MM-dd HH:mm:ss}] id={1} {2}" -f $_.TimeCreated, $_.Id, $m) 'Yellow'
    }
}

Line ''
Line '================ 结论 ================' 'Cyan'
$hasWinSoftVol = $svc -and ($uf -contains 'WinSoftVol')
$ciHit = $ci | Where-Object { $_.Message -like '*WinSoftVol*' }
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$newest = if ($ciHit) { ($ciHit | Select-Object -First 1).TimeCreated } else { $null }
$blockedThisBoot = $ciHit -and $newest -ge $boot

if ($hasWinSoftVol -and $blockedThisBoot) {
    Line '  本次开机 WinSoftVol 仍被代码完整性拦着 -> 所有音频设备都会 Code 39。' 'Red'
    if ($sac -eq 1) {
        Line '  SAC 是「强制」，就是它拦的：跑 scripts\08-关闭智能应用控制.ps1，然后重启。'
    } elseif ($sac -eq 0) {
        Line '  SAC 已经是「关闭」，但策略要重启才生效 —— 先重启一次再跑本脚本。'
    } else {
        Line '  SAC 在「评估」模式，随时可能转强制 —— 建议直接关掉（scripts\08），重启。'
    }
    if ($sso -notmatch 'TESTSIGNING') { Line '  另外：测试签名是关的，WinSoftVol 也需要它 -> bcdedit /set testsigning on' 'Yellow' }
    if ($sb -ne 0) { Line '  另外：Secure Boot 是开的，开了就跑不了未签名驱动 -> BIOS 里关掉' 'Yellow' }
} elseif ($hasWinSoftVol -and -not $blockedThisBoot) {
    Line ("  本次开机（{0:MM-dd HH:mm} 起）没有新的拦截记录，WinSoftVol 应该已经能加载。" -f $boot) 'Green'
    Line ("  最后一次拦截是 {0:MM-dd HH:mm:ss}（重启前）。" -f $newest)
    if ($bad) { Line '  但设备仍有异常 -> 再重启一次；还不行就按手册第五节重装联想官方音频驱动整包。' 'Yellow' }
    else { Line '  [√] 音频正常' 'Green' }
} elseif ($bad) {
    Line '  没挂 WinSoftVol，但设备仍有问题 -> 按手册第五节查（可能需要重装联想官方音频驱动整包）。' 'Yellow'
} else {
    Line '  [√] 一切正常' 'Green'
}
Line ''
Read-Host '按回车退出'
