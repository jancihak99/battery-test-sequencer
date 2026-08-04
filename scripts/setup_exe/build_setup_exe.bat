@echo off
REM Rebuild installer\BTS-Setup.exe (app icon). Run from anywhere.
cd /d "%~dp0..\.."
".venv\Scripts\pyinstaller.exe" --noconfirm --distpath installer --workpath scripts\setup_exe\build scripts\setup_exe\BTS-Setup.spec
if errorlevel 1 (
  echo Build failed
  pause
  exit /b 1
)
echo.
echo OK: installer\BTS-Setup.exe
explorer /select,"%cd%\installer\BTS-Setup.exe"
