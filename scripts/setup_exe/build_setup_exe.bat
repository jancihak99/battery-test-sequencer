@echo off
REM Build offline BTS-Setup.exe (embeds app + Python + wheels).
cd /d "%~dp0..\.."

echo === 1/2 Pack offline payload ===
".venv\Scripts\python.exe" scripts\setup_exe\pack_payload.py
if errorlevel 1 (
  echo Payload pack failed
  pause
  exit /b 1
)

echo === 2/2 PyInstaller ===
".venv\Scripts\pyinstaller.exe" --noconfirm --distpath installer --workpath scripts\setup_exe\build scripts\setup_exe\BTS-Setup.spec
if errorlevel 1 (
  echo Build failed
  pause
  exit /b 1
)

echo.
echo OK: installer\BTS-Setup.exe
explorer /select,"%cd%\installer\BTS-Setup.exe"
