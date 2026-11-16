@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Sentinel: .venv\Scripts\pythonw.exe was not found.
    echo Keep this launcher in the existing Sentinel project folder.
    pause
    exit /b 1
)
if not exist "%~dp0sentinel_app.pyw" (
    echo Sentinel: sentinel_app.pyw was not found.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0sentinel_app.pyw"
