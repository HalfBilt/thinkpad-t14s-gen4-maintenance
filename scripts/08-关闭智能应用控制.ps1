# 08-关闭智能应用控制.ps1
# 用途：关闭 Windows「智能应用控制」(Smart App Control / SAC)
# 背景：SAC 在 2026-10-17 从「评估」自动转成「强制」后，把未签名的 WinSoftVol.sys 拦掉，
#       导致 MEDIA 类下 5 个音频设备全部 Code 39、彻底没声。详见手册第九节。
# 权限：管理员   生效：需重启
# 注意：SAC 关掉之后，无法再打开（除非重装系统）。

$ErrorActionPreference = 'Stop'
$KEY = 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '[X] 需要管理员权限。请右键 PowerShell -> 以管理员身份运行。' -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}

Write-Host '=== 当前状态 ===' -ForegroundColor Cyan
$before = (Get-ItemProperty -Path $KEY -Name VerifiedAndReputablePolicyState -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
switch ($before) {
    0 { Write-Host '智能应用控制 = 关闭 (0)' -ForegroundColor Green }
    1 { Write-Host '智能应用控制 = 强制 (1)  <-- 就是这个在拦 WinSoftVol' -ForegroundColor Yellow }
    2 { Write-Host '智能应用控制 = 评估 (2)' -ForegroundColor Yellow }
    default { Write-Host "智能应用控制 = 未知 ($before)" -ForegroundColor Yellow }
}

Write-Host ''
Write-Host '即将把它设为 0（关闭）。' -ForegroundColor Yellow
Write-Host '提醒：SAC 一旦关闭，无法再打开（只能重装系统恢复）。' -ForegroundColor Yellow
$ok = Read-Host '继续？输入 y 回车'
if ($ok -ne 'y') { Write-Host '已取消，未做任何修改。'; Read-Host '按回车退出'; exit 0 }

Set-ItemProperty -Path $KEY -Name VerifiedAndReputablePolicyState -Value 0 -Type DWord
$after = (Get-ItemProperty -Path $KEY -Name VerifiedAndReputablePolicyState).VerifiedAndReputablePolicyState

Write-Host ''
if ($after -eq 0) {
    Write-Host '[√] 已设为 0。' -ForegroundColor Green
    Write-Host '    请重启电脑，重启后音频设备会自动恢复，WinSoftVol 也会重新生效。' -ForegroundColor Green
} else {
    Write-Host "[X] 写入未生效，读回值 = $after" -ForegroundColor Red
}

Write-Host ''
Write-Host '=== 重启后怎么核对 ===' -ForegroundColor Cyan
Write-Host '  sc.exe query WinSoftVol                                   -> STATE : 4 RUNNING'
Write-Host '  pnputil /enum-devices /problem                            -> 应该没有 Code 39'
Write-Host '  系统音量条拖 1 -> 100，响度要有明显变化'
Write-Host ''
Read-Host '按回车退出'
