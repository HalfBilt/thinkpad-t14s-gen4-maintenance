#Requires -Version 5.1
<#
  事实核对：把手册里那些"可复核的断言"一次跑完，输出到本目录的 事实核对-时间戳.txt
  需要管理员（部分项要 bcdedit / schtasks / EC 直读）。
  用法：右键 → 以 PowerShell 运行，或
        powershell -NoProfile -ExecutionPolicy Bypass -File 31-事实核对.ps1
  设计约定：任何一项失败都不中断，逐项写盘（$ErrorActionPreference=Continue）。
#>
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $here ("事实核对-" + $stamp + ".txt")

function W($s) { ($s | Out-String).TrimEnd() | Out-File -FilePath $out -Append -Encoding UTF8 }

W ("== fact check " + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + " ==")
$isAdmin = [bool](([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
W ("elevated = " + $isAdmin)

W ""
W "== machine =="
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
$bi  = Get-CimInstance Win32_BIOS
$cv  = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
W ("CPU      : " + $cpu.Name + " | " + $cpu.NumberOfCores + "C/" + $cpu.NumberOfLogicalProcessors + "T | L3=" + $cpu.L3CacheSize + "KB")
W ("MEM      : " + [math]::Round($cs.TotalPhysicalMemory/1GB,1) + " GB")
W ("OS       : " + $cv.DisplayVersion + " build " + $cv.CurrentBuild + "." + $cv.UBR)
W ("BIOS     : " + $bi.SMBIOSBIOSVersion + " / " + $bi.ReleaseDate)
W ("MODEL    : " + $cs.Model + " / SN=" + $bi.SerialNumber)

W ""
W "== security =="
try { W ("SecureBoot        = " + (Confirm-SecureBootUEFI)) } catch { W ("SecureBoot        : ERR " + $_.Exception.Message) }
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard
W ("VBS status        = " + $dg.VirtualizationBasedSecurityStatus + "  (0=off 1=enabled-not-running 2=running)")
W ("HiberbootEnabled  = " + (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power').HiberbootEnabled)

W ""
W "== bcdedit =="
W (bcdedit /enum '{current}')

W ""
W "== battery =="
$pb = Get-CimInstance Win32_PortableBattery | Select-Object -First 1
W ("model=" + $pb.Name + " design=" + $pb.DesignCapacity + " chemistry=" + $pb.Chemistry)
$fc = Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity
foreach ($x in $fc) { W ("FullChargedCapacity = " + $x.FullChargedCapacity) }
W ("(cycle count: run 'powercfg /batteryreport' and look for CYCLE COUNT)")

W ""
W "== services / tasks =="
foreach ($n in 'CpuPLFixSvc','T14sFanSvc','TSCtl','w32time') {
  $s = Get-Service -Name $n -ErrorAction SilentlyContinue
  if ($s) { W ("{0,-12} {1,-9} {2}" -f $s.Name, $s.Status, $s.StartType) } else { W ("{0,-12} (none)" -f $n) }
}
W (schtasks /query /tn 'CpuPLFix' /fo LIST /v)
W (schtasks /query /tn 'T14sHUDGuard' /fo LIST)

W ""
W "== status snapshots =="
foreach ($f in 'cpu.json','fan.json') {
  $p = Join-Path $env:APPDATA ("T14scontrol\status\" + $f)
  if (Test-Path $p) { W ($f + " : " + ((Get-Content $p -Raw -Encoding UTF8).Trim())) } else { W ($f + " : missing") }
}

W ""
W "== EC read-only (needs TSCtl + admin) =="
$py = Join-Path $env:APPDATA 'T14scontrol\runtime\python.exe'
if (-not $isAdmin) {
  W "SKIP: 需要管理员（CpuPLFix.ensure_driver 会在非提权时尝试提权并卡住，所以这里直接跳过）"
} elseif (-not (Test-Path $py)) {
  W "SKIP: python.exe missing"
} else {
  $code = "import sys;sys.path.insert(0,r'$env:APPDATA\T14scontrol\cpu');import CpuPLFix as C;C.ensure_driver(verbose=False);" +
          "[print('0x%02X = %s' % (o, C.ec_read_ok(o,tries=2))) for o in (0xF7,0x2F,0xB0,0xB1,0x38,0x81,0x89,0x8A,0x8B)]"
  W (& $py -c $code 2>&1)
}

W ""
W "== recent system events (13/15/88/41/6008) =="
$ev = Get-WinEvent -FilterHashtable @{LogName='System'; Id=13,15,88,41,6008} -ErrorAction SilentlyContinue
$oldest = Get-WinEvent -LogName System -Oldest -MaxEvents 1
W ("log window starts = " + $oldest.TimeCreated)
W ("count = " + @($ev).Count)
foreach ($e in ($ev | Sort-Object TimeCreated | Select-Object -Last 15)) {
  W ("{0}  [{1}]  {2}" -f $e.TimeCreated.ToString('MM-dd HH:mm:ss'), $e.Id, $e.ProviderName)
}

W ""
W "== end =="
Write-Host ("WROTE " + $out)
