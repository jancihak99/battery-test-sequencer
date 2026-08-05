@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv — is Python installed?
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM Launch GUI without a console (pythonw). Crashes show a Qt dialog (see main.py).
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
  exit /b 0
)

".venv\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 pause
