@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 性能自检

rem ===== 找一个能用的 Python：优先 CpuPLFix 自带的运行时 =====
set "PY="
if exist "%APPDATA%\CpuPLFix\runtime\python.exe" set "PY=%APPDATA%\CpuPLFix\runtime\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY (
  echo [X] 没找到 Python。请先装 CpuPLFix（自带 runtime\python.exe），见手册第八节。
  pause
  exit /b 1
)

rem ===== 需要管理员（读 MSR / 装 TSCtl 驱动），没提权就自己提权再回来 =====
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在弹 UAC 确认框 ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo 用 %PY%
echo.
"%PY%" "%~dp010-性能自检.py"
echo.
pause
