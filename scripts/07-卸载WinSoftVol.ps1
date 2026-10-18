# uninstall-winsoftvol.ps1
# 卸载 WinSoftVol，恢复到"系统音量对扬声器无效"的原始状态
# 用法：以【管理员】身份运行

#Requires -RunAsAdministrator
$ErrorActionPreference = 'Continue'

$sysDst   = Join-Path $env:WINDIR 'System32\drivers\WinSoftVol.sys'
$mediaCls = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}'

Write-Host "[1/4] 从 MEDIA 类移除 WinSoftVol ..."
$cur = (Get-ItemProperty -Path $mediaCls -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
$new = @($cur | Where-Object { $_ -ne 'WinSoftVol' })
Set-ItemProperty -Path $mediaCls -Name UpperFilters -Value $new -Type MultiString

Write-Host "[2/4] 停止并删除服务 ..."
sc.exe stop WinSoftVol > $null 2>&1
sc.exe delete WinSoftVol > $null 2>&1

Write-Host "[3/4] 删除驱动文件 ..."
Remove-Item $sysDst -Force -ErrorAction SilentlyContinue

Write-Host "[4/4] 关闭测试签名 ..."
bcdedit /set testsigning off | Out-Null

Write-Host ""
Write-Host "完成！请【重启电脑】。"
Write-Host "（如已用 UWD 去水印，可再运行 UWD 点 Uninstall 恢复。）"
