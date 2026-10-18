<#
  03-清理孤儿驱动包.ps1
  ------------------------------------------------------------------
  作用：删除"没有被任何设备引用"的多余驱动包（旧版本残留、本机根本没装的硬件的驱动）。
        本机曾用此法把驱动包从 298 个清到 74 个，DriverStore 从 8.15GB 降到 2.8GB。

  需要：管理员权限
  安全：① 运行时重新计算"在用"清单；
        ② pnputil 自身会拒绝删除正在使用的包（尤其是 Extension 类），这是第二重保险。
  说明：删除的只是驱动包缓存，设备重插/重装时会自动重新下载安装，无实际风险。

  用法：
        只看看不删   ： powershell -ExecutionPolicy Bypass -File 03-清理孤儿驱动包.ps1 -DryRun
        实际执行     ： powershell -ExecutionPolicy Bypass -File 03-清理孤儿驱动包.ps1
  ------------------------------------------------------------------
#>
param([switch]$DryRun)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

function IsAdmin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host "================ 清理孤儿驱动包 ================" -ForegroundColor Cyan
if (-not (IsAdmin)) {
    Write-Host "❌ 需要管理员权限。请右键此脚本 →「使用 PowerShell 运行」（管理员）后再试。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

# ---------- 1. 记录删除前大小 ----------
$repo = "$env:SystemRoot\System32\DriverStore\FileRepository"
$before = (Get-ChildItem $repo -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("DriverStore 当前: {0} GB" -f [math]::Round($before / 1GB, 2))
Write-Host ""

# ---------- 2. 重新计算"在用"驱动包 ----------
Write-Host "正在计算哪些驱动包正在被设备使用..." -ForegroundColor Gray
$use = @{}
foreach ($en in (Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum')) {
    foreach ($dv in (Get-ChildItem $en.PSPath)) {
        foreach ($in in (Get-ChildItem $dv.PSPath)) {
            $drv = (Get-ItemProperty -Path $in.PSPath -Name Driver -ErrorAction SilentlyContinue).Driver
            if ($drv -match '^\{(.+)\}\\?(\d+)$') {
                $ip = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{$($Matches[1])}\$($Matches[2])" -Name InfPath -ErrorAction SilentlyContinue).InfPath
                if ($ip -match '^oem\d+\.inf$') { $use[$ip.ToLower()] = $true }
            }
        }
    }
}
Write-Host ("在用驱动包: {0} 个" -f $use.Count)

# ---------- 3. 找出候选 ----------
$allOem = Get-ChildItem "$env:SystemRoot\INF\oem*.inf" | ForEach-Object { $_.Name.ToLower() }
$cands  = @($allOem | Where-Object { -not $use.ContainsKey($_) })
Write-Host ("未被引用的候选包: {0} 个（其中部分可能仍在使用，pnputil 会自动拒绝）" -f $cands.Count)
Write-Host ""

if ($DryRun) {
    Write-Host "（-DryRun 模式，仅列出，不删除）" -ForegroundColor Yellow
    $cands | Sort-Object | ForEach-Object { Write-Host "  $_" }
    Read-Host "按回车退出"; exit 0
}

$ans = Read-Host ("确认删除这 {0} 个候选包？输入 y 继续" -f $cands.Count)
if ($ans -ne 'y') { Write-Host "已取消，未做任何修改。"; Read-Host "按回车退出"; exit 0 }

# ---------- 4. 逐个删除 ----------
$deleted = 0; $refused = 0; $i = 0
foreach ($o in $cands) {
    $i++
    pnputil /delete-driver $o 2>&1 | Out-Null
    if (-not (Test-Path "$env:SystemRoot\INF\$o")) {
        $deleted++
    } else {
        $refused++
    }
    if ($i % 40 -eq 0) { Write-Host ("  进度 {0}/{1} ..." -f $i, $cands.Count) -ForegroundColor Gray }
}

# ---------- 5. 结果 ----------
$after = (Get-ChildItem $repo -Recurse -File | Measure-Object Length -Sum).Sum
$freed = $before - $after
Write-Host ""
Write-Host "================ 结果 ================" -ForegroundColor Cyan
Write-Host ("已删除: {0} 个" -f $deleted) -ForegroundColor Green
Write-Host ("被拒绝(仍在使用/扩展包): {0} 个  ← 属正常保护" -f $refused) -ForegroundColor Yellow
Write-Host ("DriverStore: {0} GB → {1} GB   释放 {2} GB" -f `
    [math]::Round($before / 1GB, 2), [math]::Round($after / 1GB, 2), [math]::Round($freed / 1GB, 2)) -ForegroundColor Green
Write-Host ""
Write-Host "建议重启一次让状态稳定。" -ForegroundColor Gray
Read-Host "按回车退出"
