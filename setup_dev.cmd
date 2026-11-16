@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Install Windows x64 Python 3.12 first, then run this file again.
        exit /b 1
    )
)
if exist ".downloads\wheelhouse\" (
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links ".downloads\wheelhouse" -r requirements-lock.txt
) else (
    echo Download and extract Sentinel-dev-wheels-py312-win64.zip to .downloads first.
    echo See DEVELOPMENT.md. The pinned SDK may not be available on your package index.
    exit /b 1
)
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 exit /b 1
if not exist ".env" copy /y ".env.example" ".env" >nul
echo Python dependencies are ready. See DEVELOPMENT.md to download and restore model files.
echo Double-click start_sentinel.cmd after restoring the models.
