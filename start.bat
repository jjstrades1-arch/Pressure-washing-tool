@echo off
REM PowerLeads - double-click this file to start the app (Windows).
REM It installs what it needs, then opens the app in your web browser.
cd /d "%~dp0"

echo Starting PowerLeads...

where python >nul 2>&1
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and try again.
  pause
  exit /b 1
)

python -m pip install --quiet flask >nul 2>&1
python -m pwleads serve

pause
