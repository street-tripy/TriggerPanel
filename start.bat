@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
  echo [ERROR] venv missing - run install.bat first.
  pause
  exit /b 1
)

start "" "venv\Scripts\pythonw.exe" "%~dp0server.py"
echo TriggerApp started - closing or minimizing the window sends it to the system tray.
echo Your shortcut base URL is shown in the window header (http://YOUR-IP:8765).
