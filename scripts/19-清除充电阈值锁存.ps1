# Clear the Lenovo charge-threshold latch and switch the EC back to automatic mode.
# Requires administrator. After it finishes, run: shutdown /s /full
# Then boot and verify with:
#   Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus |
#     Select-Object PowerOnline,Charging,ChargeRate
# The script is ASCII-only deliberately (PS 5.1 + no-BOM + Chinese = parse error).

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $PSScriptRoot ("clear-charge-threshold-$stamp.txt")
Start-Transcript -Path $log -Force | Out-Null

try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Write-Host "Admin: $isAdmin"
    if (-not $isAdmin) { throw 'Administrator privileges required. Right-click -> Run as administrator.' }

    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class IbmPm
{
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateFileW(string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition, uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool DeviceIoControl(IntPtr hDevice, uint dwIoControlCode,
        byte[] lpInBuffer, uint nInBufferSize, byte[] lpOutBuffer, uint nOutBufferSize,
        out uint lpBytesReturned, IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);
}
'@

    $handle = [IbmPm]::CreateFileW('\\.\IBMPmDrv', [uint32]3221225472, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
    if ($handle -eq [IntPtr]::Zero -or $handle -eq [IntPtr](-1)) {
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "CreateFile(IBMPmDrv) failed, Win32 error: $err"
    }

    function Send-Ioctl {
        param([uint32]$Code, [uint32]$Value)
        $in = [BitConverter]::GetBytes($Value)
        $out = New-Object byte[] 4
        $ret = [uint32]0
        $ok = [IbmPm]::DeviceIoControl($handle, $Code, $in, 4, $out, 4, [ref]$ret, [IntPtr]::Zero)
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $res = [BitConverter]::ToUInt32($out, 0)
        Write-Host ("IOCTL 0x{0:X} value=0x{1:X} -> ok={2} err={3} result=0x{4:X}" -f $Code, $Value, $ok, $err, $res)
    }

    # 1) clear stop threshold, 2) clear start threshold, 3) select automatic mode
    Send-Ioctl -Code 0x222638 -Value 0x00000100
    Send-Ioctl -Code 0x222630 -Value 0x00000100
    Send-Ioctl -Code 0x22261C -Value 0x00000000

    [IbmPm]::CloseHandle($handle) | Out-Null

    # Clear both registry locations (ConfKeys is the one the Lenovo PM driver reads)
    $keys = @(
        'HKLM:\SOFTWARE\WOW6432Node\Lenovo\PWRMGRV\ConfKeys\Data',
        'HKLM:\SOFTWARE\WOW6432Node\Lenovo\PWRMGRV\Data'
    )
    foreach ($key in $keys) {
        if (Test-Path $key) {
            foreach ($name in 'ChargeStartControl','ChargeStopControl','ChargeStartPercentage','ChargeStopPercentage') {
                Set-ItemProperty -Path $key -Name $name -Value 0 -Type DWord
            }
            Write-Host "Registry cleared: $key"
        }
    }

    Start-Sleep -Seconds 10
    Write-Host '--- Battery status ---'
    Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus |
        Select-Object PowerOnline,Charging,Discharging,RemainingCapacity,Voltage,ChargeRate,DischargeRate |
        Format-List

    Write-Host ''
    Write-Host 'NEXT STEP: run "shutdown /s /full" (a normal restart does NOT release the EC latch), then boot and re-check Charging/ChargeRate.'
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)"
}
finally {
    Stop-Transcript | Out-Null
}
