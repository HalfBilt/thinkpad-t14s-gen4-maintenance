@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 禁用 SWVisualize 崩溃服务

rem ===== 需要管理员 =====
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在弹 UAC 确认框 ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

rem ===== 用法: 不带参数 = 禁用；带 restore = 恢复自动启动 =====
if /i "%~1"=="restore" goto restore

echo.
echo   即将把这两个 SOLIDWORKS Visualize 服务改成"禁用"并停掉：
echo     SWVisualize2024.BoostService
echo     SWVisualize2024.Queue.Server
echo.
echo   原因: BoostService 近 3 天崩了 32 次, 每次会带崩 dwm.exe / explorer.exe
echo         (表现为"桌面点不动", 手册 00 §四 记着这个坑)。
echo   这两个服务跟降频/风扇/电池没有任何关系, 不用 SOLIDWORKS 就可以关。
echo   想恢复: 重新双击本文件, 选 restore。
echo.
pause

sc.exe config "SWVisualize2024.BoostService" start= disabled
sc.exe stop "SWVisualize2024.BoostService"
sc.exe config "SWVisualize2024.Queue.Server" start= disabled
sc.exe stop "SWVisualize2024.Queue.Server"
echo.
echo 完成。以后 explorer 挂起 / dwm 崩 的次数应该会明显减少。
echo.
pause
exit /b

:restore
echo.
echo   恢复两个服务为"自动"并启动 ...
echo.
sc.exe config "SWVisualize2024.BoostService" start= auto
sc.exe config "SWVisualize2024.Queue.Server" start= auto
sc.exe start "SWVisualize2024.BoostService"
sc.exe start "SWVisualize2024.Queue.Server"
echo.
echo 已恢复。
echo.
pause
