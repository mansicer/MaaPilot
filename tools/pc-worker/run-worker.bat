@echo off
chcp 65001 >nul
cd /d "%~dp0"
set MAAPILOT_ROOT=%~dp0..\..

if not exist "..\..\.venv\Scripts\python.exe" (
  echo 找不到 ..\..\.venv\Scripts\python.exe，请先按 MaaPilot 的 setup 装运行环境
  pause
  exit /b 1
)

echo 正在以当前权限启动 worker...
echo 重要：如果不是管理员启动的，请关掉这个窗口，右键本文件 -^> 以管理员身份运行
echo.
"..\..\.venv\Scripts\python.exe" worker.py
pause
