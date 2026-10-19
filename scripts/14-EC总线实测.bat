@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EC bus measurement

rem ===== find the python script next to this bat (wildcard: keeps this file pure ASCII) =====
set "SCRIPT="
for %%f in ("%~dp014-EC*.py") do if not defined SCRIPT set "SCRIPT=%%~ff"
if not defined SCRIPT (
  echo [X] 14-EC*.py not found next to this bat.
  pause
  exit /b 1
)

rem ===== find a usable Python: prefer the one bundled with CpuPLFix =====
set "PY="
if exist "%APPDATA%\CpuPLFix\runtime\python.exe" set "PY=%APPDATA%\CpuPLFix\runtime\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY (
  echo [X] No Python found. Install CpuPLFix first - it ships runtime\python.exe.
  pause
  exit /b 1
)

rem ===== need admin: the TSCtl device is admin-only. Re-launch elevated if not. =====
net session >nul 2>&1
if errorlevel 1 (
  echo Need administrator rights - asking for UAC confirmation ...
  if "%~1"=="" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  ) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
  )
  exit /b
)

rem ===== run =====
"%PY%" "%SCRIPT%" %*

echo.
pause
