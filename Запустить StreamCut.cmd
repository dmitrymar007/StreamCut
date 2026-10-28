@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run scripts\install.ps1 using PowerShell.
  pause
  exit /b 1
)
start "StreamCut" ".venv\Scripts\pythonw.exe" -m streamcut %*
