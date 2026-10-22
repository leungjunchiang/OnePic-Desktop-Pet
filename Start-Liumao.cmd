@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run scripts\setup_project_environment.ps1 first.
  pause
  exit /b 1
)
set "ONEPIC_MUSIC_LOCALAPPDATA=%LOCALAPPDATA%"
set "LOCALAPPDATA=%~dp0.local-dev-data"
set "PYTHONPATH=%~dp0src"
set "ONEPIC_DEVELOPMENT_MODE=1"
start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
