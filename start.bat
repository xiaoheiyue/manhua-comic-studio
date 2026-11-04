@echo off
rem 启动脚本（Windows，双击即可）
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" server.py
) else if exist "env\Scripts\python.exe" (
  "env\Scripts\python.exe" server.py
) else (
  python server.py
)
pause
