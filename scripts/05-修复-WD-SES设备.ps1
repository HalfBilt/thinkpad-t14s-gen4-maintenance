<#
  05-修复-WD-SES设备.ps1
  ------------------------------------------------------------------
  作用：处理 WD 移动硬盘的 "WD SES Device" 加载失败（设备管理器黄标 / 错误码 28）。
        SES 只是硬盘盒的辅助组件（指示灯等），盘的数据读写不依赖它。

  需要：管理员权限
  说明：如果驱动缺失，脚本会先重扫让系统尝试自动安装；
        若仍失败，则移除该坏节点，让系统重新枚举（下次插盘会重新识别）。
  ------------------------------------------------------------------
#>
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

function IsAdmin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host "================ 修复 WD SES 设备 ================" -ForegroundColor Cyan
if (-not (IsAdmin)) {
    Write-Host "❌ 需要管理员权限。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

# 当前状态
$ses = Get-PnpDevice | Where-Object { $_.InstanceId -like '*VEN_WD&PROD_SES*' } | Select-Object -First 1
if (-not $ses) {
    Write-Host "当前系统里没有 WD SES 设备节点（可能盘没插，或已被清理）。" -ForegroundColor Yellow
    Write-Host "如果是盘没插：插上硬盘后再运行本脚本。" -ForegroundColor Gray
    Read-Host "按回车退出"; exit 0
}
Write-Host ("当前状态: {0} / {1}" -f $ses.Status, $ses.Problem)
Write-Host ""

Write-Host "① 重新扫描设备，让系统尝试自动安装驱动..." -ForegroundColor Gray
pnputil /scan-devices | Out-Null
Start-Sleep -Seconds 3

$ses = Get-PnpDevice | Where-Object { $_.InstanceId -like '*VEN_WD&PROD_SES*' } | Select-Object -First 1
if ($ses -and $ses.Status -eq 'OK') {
    Write-Host "✅ 已自行修复，SES 设备状态 OK。" -ForegroundColor Green
    Read-Host "按回车退出"; exit 0
}

Write-Host "② 仍异常，移除该坏节点让系统重新枚举..." -ForegroundColor Gray
if ($ses) { pnputil /remove-device "$($ses.InstanceId)" | Out-Null }
Start-Sleep -Seconds 2
pnputil /scan-devices | Out-Null
Start-Sleep -Seconds 4

$ses = Get-PnpDevice | Where-Object { $_.InstanceId -like '*VEN_WD&PROD_SES*' } | Select-Object -First 1
if (-not $ses) {
    Write-Host "SES 节点已清除（拔插硬盘后会重新枚举）。" -ForegroundColor Green
} elseif ($ses.Status -eq 'OK') {
    Write-Host "✅ 修复成功，状态 OK。" -ForegroundColor Green
} else {
    Write-Host ("仍异常：{0}。可在有网络时插着硬盘，让 Windows Update 自动补装该驱动。" -f $ses.Problem) -ForegroundColor Yellow
}
Write-Host ""
Write-Host "说明：SES 只影响硬盘盒指示灯等辅助功能，不影响硬盘的数据读写。" -ForegroundColor Gray
Read-Host "按回车退出"
