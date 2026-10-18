# install-winsoftvol.ps1
# 一键安装 WinSoftVol：让 Windows 改用"软件音量"（修复"内置扬声器系统音量无效"）
# 用法：以【管理员】身份运行 PowerShell，执行本脚本；完成后重启。
# 参考：https://github.com/dechamps/WinSoftVol

#Requires -RunAsAdministrator
$ErrorActionPreference = 'Continue'

$work    = 'C:\Program Files\WinSoftVol'
$sysName = 'WinSoftVol.sys'
$sysDst  = Join-Path $env:WINDIR "System32\drivers\$sysName"
$url     = 'https://github.com/dechamps/WinSoftVol/releases/download/WinSoftVol-0.1/WinSoftVol_x64_SelfSigned.sys'
$mediaCls = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}'

New-Item -ItemType Directory -Force -Path $work | Out-Null
$dl = Join-Path $work 'WinSoftVol_x64_SelfSigned.sys'

Write-Host "[1/6] 下载 WinSoftVol ..."
if (-not (Test-Path $dl)) {
    Invoke-WebRequest -Uri $url -OutFile $dl -UseBasicParsing -Headers @{'User-Agent'='Mozilla/5.0'}
}
Unblock-File $dl -ErrorAction SilentlyContinue

Write-Host "[2/6] 安装自签名证书到 受信任的根 + 受信任的发布者 ..."
$cert = (Get-AuthenticodeSignature $dl).SignerCertificate
foreach ($n in @('Root','TrustedPublisher')) {
    $st = New-Object System.Security.Cryptography.X509Certificates.X509Store($n,'LocalMachine')
    $st.Open('ReadWrite'); $st.Add($cert); $st.Close()
}

Write-Host "[3/6] 部署驱动到 $sysDst ..."
Copy-Item $dl $sysDst -Force
Unblock-File $sysDst -ErrorAction SilentlyContinue

Write-Host "[4/6] 创建/修复内核服务（ImagePath 必须用 \SystemRoot\...）..."
sc.exe delete WinSoftVol > $null 2>&1
sc.exe create WinSoftVol "binPath= \SystemRoot\System32\drivers\$sysName" type= kernel error= normal | Out-Null
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\WinSoftVol' -Name ImagePath -Value "\SystemRoot\System32\drivers\$sysName"
$q = & sc.exe start WinSoftVol 2>&1 | Out-String
Write-Host $q

Write-Host "[5/6] 把 WinSoftVol 挂到 MEDIA 类（备份注册表）..."
$bk = Join-Path $work 'media-class-backup.reg'
reg.exe export 'HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}' "$bk" /y | Out-Null
$cur = (Get-ItemProperty -Path $mediaCls -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
$new = @($cur | Where-Object { $_ -ne 'WinSoftVol' }); if ($new -notcontains 'WinSoftVol') { $new += 'WinSoftVol' }
Set-ItemProperty -Path $mediaCls -Name UpperFilters -Value $new -Type MultiString

Write-Host "[6/6] 开启测试签名 ..."
bcdedit /set testsigning on | Out-Null

Write-Host ""
Write-Host "完成！请【重启电脑】。重启后系统音量条应变为原生软件音量。"
Write-Host "（会显示“测试模式”水印，可用 UWD 去除。）"
