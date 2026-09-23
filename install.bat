@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found on PATH. Install it from https://python.org first.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
)

echo Upgrading pip...
"venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

echo Installing dependencies (PyQt6)...
"venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet

echo.
echo [OK] TriggerApp installed. Run start.bat to launch it.
pause
