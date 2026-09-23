# Creates a hidden startup shortcut so TriggerApp runs at Windows sign-in.
$startup = [Environment]::GetFolderPath('Startup')
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $startup 'TriggerApp.lnk'))
$lnk.TargetPath = Join-Path $PSScriptRoot 'venv\Scripts\pythonw.exe'
$lnk.Arguments = '"' + (Join-Path $PSScriptRoot 'server.py') + '"'
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.WindowStyle = 7  # minimized (pythonw shows no window anyway)
$lnk.Description = 'TriggerApp - phone-to-PC app launcher'
$lnk.Save()
Write-Host "Autostart enabled: $startup\TriggerApp.lnk"
