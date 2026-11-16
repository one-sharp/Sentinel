@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 build_exe.py
if errorlevel 1 exit /b 1
set "SENTINEL_ISCC=%~dp0.build_tools\inno\ISCC.exe"
if not exist "%SENTINEL_ISCC%" set "SENTINEL_ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%SENTINEL_ISCC%" (
    echo Inno Setup ISCC.exe was not found. Install Inno Setup first.
    exit /b 1
)
"%SENTINEL_ISCC%" packaging\Sentinel.iss
if errorlevel 1 exit /b 1
echo Built dist\Sentinel\Sentinel.exe and release\Sentinel-Setup.exe
