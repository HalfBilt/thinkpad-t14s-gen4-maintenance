@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 风扇8档28W实测

rem ===== 找一个能用的 Python：优先 CpuPLFix 自带的运行时 =====
set "PY="
if exist "%APPDATA%\T14scontrol\runtime\python.exe" set "PY=%APPDATA%\T14scontrol\runtime\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY (
  echo [X] 没找到 Python。请先装 CpuPLFix（自带 runtime\python.exe），见手册第八节。
  pause
  exit /b 1
)

rem ===== 需要管理员（TSCtl 驱动设备只对管理员开放），没提权就自己提权再回来 =====
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在弹 UAC 确认框 ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo   本脚本先停风扇服务，做 7 档/8 档无负载 A/B（约 3 分钟）；
echo   只有 8 档确实更快，才继续 28W 满载对照（8 档 75 秒 + 满速 40 秒）。
echo   DTS 超 97°C 会自动切满速并结束；测完自动恢复服务。
echo   过程中风扇很响，属正常。
echo.
pause

echo 用 %PY%
echo.
"%PY%" "%~dp017-风扇8档28W实测.py" %*
echo.
pause
