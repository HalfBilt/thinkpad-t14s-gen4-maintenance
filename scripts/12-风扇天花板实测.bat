@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 风扇天花板实测

rem ===== 找一个能用的 Python：优先 CpuPLFix 自带的运行时 =====
set "PY="
if exist "%APPDATA%\CpuPLFix\runtime\python.exe" set "PY=%APPDATA%\CpuPLFix\runtime\python.exe"
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

echo   本脚本默认只"读"EC，不碰风扇控制寄存器。
echo   会让所有线程满载约 90 秒: 机器会明显发烫、风扇会吵, 属正常。
echo   建议: 接上电源, 垫高进风口。
echo.
echo   跑完会问一次"要不要标定真满速"(临时写 0x40 再写回 0x80), 不想就按回车。
echo.
pause

echo 用 %PY%
echo.
"%PY%" "%~dp012-风扇天花板实测.py" %*
echo.
pause
