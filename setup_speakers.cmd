@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m pip install scipy
if errorlevel 1 goto :done
"%~dp0.venv\Scripts\python.exe" -X utf8 setup_speaker_models.py
:done
pause
