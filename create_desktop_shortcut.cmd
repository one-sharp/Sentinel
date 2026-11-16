@echo off
setlocal
set "SENTINEL_SHORTCUT_ROOT=%~dp0"
powershell.exe -NoProfile -Command "$r=$env:SENTINEL_SHORTCUT_ROOT; $d=$env:SENTINEL_SHORTCUT_DEST; if (!$d) {$d=[Environment]::GetFolderPath('DesktopDirectory')}; $p=Join-Path $r '.venv\Scripts\pythonw.exe'; if (!(Test-Path -LiteralPath $p)) {throw 'Existing .venv Python was not found'}; $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut((Join-Path $d 'Sentinel.lnk')); $s.TargetPath=$p; $s.Arguments=[char]34+(Join-Path $r 'sentinel_app.pyw')+[char]34; $s.WorkingDirectory=$r; $s.IconLocation=(Join-Path $r 'assets\sentinel-idle.ico')+',0'; $s.Description='Sentinel v0.3.1'; $s.Save(); Write-Host ('Created: '+(Join-Path $d 'Sentinel.lnk'))"
if errorlevel 1 (
    echo Shortcut creation failed. Keep this file in the Sentinel project folder.
    pause
    exit /b 1
)
pause
