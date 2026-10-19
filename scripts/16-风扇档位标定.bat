@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 风扇档位标定

rem ===== 找一个能用的 Python：优先 CpuPLFix 自带的运行时 =====
set "PY="
if exist "%APPDATA%\CpuPLFix\runtime\python.exe" set "PY=%APPDATA%\CpuPLFix\runtime\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY (
  echo [X] 没找到 Python。请先装 CpuPLFix（自带 runtime\python.exe），见手册 01。
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

echo.
echo   本脚本会**临时停掉**看守服务(CpuPLFixSvc)和计划任务(CpuPLFix)，
echo   逐个档位写 EC 0x2F 测真实转速，测完自动恢复原档位并把服务拉起来。
echo   期间机器会忽快忽慢（风扇在逐档换），属正常；全程约 2~3 分钟。
echo   内置温度护栏：EC 温度超过 92 度就提前收手并恢复。
echo.
echo   测完请把屏幕上"结果表"那段抄进手册 02 §1 / 03 §2.1。
echo.
pause

echo 用 %PY%
echo.
"%PY%" "%~dp016-风扇档位标定.py" %*
echo.
pause
