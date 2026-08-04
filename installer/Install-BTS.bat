@echo off
REM Double-click installer for Battery Test Sequencer
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-BTS.ps1"
if errorlevel 1 pause
pause
