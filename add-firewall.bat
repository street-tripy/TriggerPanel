@echo off
rem Allows inbound TCP on the TriggerApp port (default 8765).
rem Right-click -> Run as administrator, or it will fail.

net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Run this as administrator: right-click -^> Run as administrator.
  pause
  exit /b 1
)

netsh advfirewall firewall delete rule name="TriggerApp" >nul 2>&1
netsh advfirewall firewall add rule name="TriggerApp" dir=in action=allow protocol=TCP localport=8765
echo.
echo Firewall rule added for TCP port 8765.
echo If you change the port in config.json, re-run this with the new port.
pause
