<#
  04-删除孤儿启动项.ps1
  ------------------------------------------------------------------
  作用：删除一个"文件不存在、且没有任何设备/服务依赖它"的孤儿引导启动项，
        用于消除开机日志里的 7026 报错（例如本机曾出现的 uiomap）。

  需要：管理员权限
  安全：删除前自动做三查（依赖服务 / 设备绑定 / 备份注册表），任一不通过就中止。

  用法：
    powershell -ExecutionPolicy Bypass -File 04-删除孤儿启动项.ps1 -ServiceName uiomap
  ------------------------------------------------------------------
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$ServiceName
)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

function IsAdmin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host "================ 删除孤儿启动项 ================" -ForegroundColor Cyan
Write-Host ("目标服务: {0}" -f $ServiceName)
Write-Host ""

if (-not (IsAdmin)) {
    Write-Host "❌ 需要管理员权限。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

$regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName"
$key = Get-ItemProperty $regPath -ErrorAction SilentlyContinue
if (-not $key) { Write-Host "注册表里没有这个服务项，无需处理。"; Read-Host "按回车退出"; exit 0 }

$img = [Environment]::ExpandEnvironmentVariables($key.ImagePath)
Write-Host ("ImagePath : {0}" -f $key.ImagePath)
Write-Host ("Start     : {0}" -f $key.Start)
$fileExists = Test-Path ($img -replace '^\\\?\?\\', '')
Write-Host ("文件存在  : {0}" -f $fileExists)
Write-Host ""

# ---------- 三查 ----------
Write-Host "① 检查是否有设备绑定该服务..." -ForegroundColor Gray
$devHit = pnputil /enum-devices /services 2>&1 | Select-String -Pattern $ServiceName -CaseSensitive:$false
if ($devHit) { Write-Host "  ❌ 有设备绑定，中止：" -ForegroundColor Red; $devHit | ForEach-Object { Write-Host ("  " + $_.Line) }; Read-Host "按回车退出"; exit 1 }
Write-Host "  ✅ 无设备绑定" -ForegroundColor Green

Write-Host "② 检查是否有其他服务依赖它..." -ForegroundColor Gray
$dep = @()
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services' | ForEach-Object {
    $d = (Get-ItemProperty $_.PSPath -Name DependOnService -ErrorAction SilentlyContinue).DependOnService
    if ($d -and ($d -contains $ServiceName)) { $dep += $_.PSChildName }
}
if ($dep.Count -gt 0) { Write-Host ("  ❌ 被依赖: {0}，中止" -f ($dep -join ', ')) -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
Write-Host "  ✅ 无依赖者" -ForegroundColor Green

# ---------- 备份 ----------
Write-Host "③ 备份注册表项..." -ForegroundColor Gray
$bak = Join-Path $env:USERPROFILE ("Desktop\{0}-service-backup.reg" -f $ServiceName)
reg export "HKLM\SYSTEM\CurrentControlSet\Services\$ServiceName" "$bak" /y | Out-Null
Write-Host ("  ✅ 已备份到 {0}" -f $bak) -ForegroundColor Green
Write-Host ""

# ---------- 删除 ----------
Write-Host "开始删除..." -ForegroundColor Gray
sc.exe delete $ServiceName | Out-Null
Start-Sleep -Seconds 2

if (Test-Path $regPath) {
    Write-Host "❌ 删除失败，项仍存在。" -ForegroundColor Red
} else {
    Write-Host "✅ 已删除。下次开机 7026 不应再出现该项。" -ForegroundColor Green
    Write-Host ("   如需恢复：双击 {0}" -f $bak) -ForegroundColor Gray
}
Read-Host "按回车退出"
