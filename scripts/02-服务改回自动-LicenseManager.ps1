<#
  02-服务改回自动-LicenseManager.ps1
  作用：把 Intel(R) Platform License Manager Service 改回"自动"（回滚 01 的修改）。
  需要：管理员权限
#>
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$svc = 'Intel(R) Platform License Manager Service'
$reg = 'HKLM:\SYSTEM\CurrentControlSet\Services\Intel(R) Platform License Manager Service'

$before = Get-Service -Name $svc -ErrorAction SilentlyContinue
if (-not $before) { Write-Host "找不到服务 [$svc]。" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
Write-Host ("改前: {0} / {1}" -f $before.Status, $before.StartType)

try {
    Set-Service -Name $svc -StartupType Automatic -ErrorAction Stop
    Write-Host "Set-Service 成功" -ForegroundColor Green
} catch {
    sc.exe config "$svc" start= auto | Out-Null
}

$after = Get-Service -Name $svc
$start = (Get-ItemProperty $reg -Name Start -ErrorAction SilentlyContinue).Start
Write-Host ("改后: {0} / {1}   注册表 Start={2}" -f $after.Status, $after.StartType, $start)
if ($start -eq 2) { Write-Host "结果：已改回自动 ✅" -ForegroundColor Green }
else { Write-Host "结果：未生效，请检查管理员权限 ❌" -ForegroundColor Red }
Read-Host "按回车退出"
