<#
  01-服务改手动-LicenseManager.ps1
  作用：把 Intel(R) Platform License Manager Service 从"自动"改成"手动"，
        消除每次开机 45 秒的服务连接超时（EID 7009）。
  需要：管理员权限
  回滚：运行 02-服务改回自动-LicenseManager.ps1
#>
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$svc = 'Intel(R) Platform License Manager Service'
$reg = 'HKLM:\SYSTEM\CurrentControlSet\Services\Intel(R) Platform License Manager Service'

try {
    $before = Get-Service -Name $svc -ErrorAction Stop
} catch {
    Write-Host "找不到服务 [$svc]，未做修改。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

Write-Host ("改前: {0} / {1}" -f $before.Status, $before.StartType)

try {
    Set-Service -Name $svc -StartupType Manual -ErrorAction Stop
    Write-Host "Set-Service 成功" -ForegroundColor Green
} catch {
    Write-Host ("Set-Service 失败，改用 sc.exe: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    sc.exe config "$svc" start= demand | Out-Null
}

$after = Get-Service -Name $svc
$start = (Get-ItemProperty $reg -Name Start -ErrorAction SilentlyContinue).Start
Write-Host ("改后: {0} / {1}   注册表 Start={2} (2=自动 3=手动 4=禁用)" -f $after.Status, $after.StartType, $start)
if ($start -eq 3) { Write-Host "结果：已改为手动 ✅" -ForegroundColor Green }
else { Write-Host "结果：未生效，请检查是否以管理员身份运行 ❌" -ForegroundColor Red }
Read-Host "按回车退出"
