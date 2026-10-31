@echo off
rem DR Model Swap: double-click to start. The first start sets up its own Python environment here (needs internet once).
cd /d "%~dp0"
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo DR Model Swap needs Python 3.9 or newer: https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" when installing^), then double-click this file again.
  echo Or download DRModelSwap.exe from the GitHub release page instead: it needs nothing else.
  pause
  exit /b 1
)
if not exist ".venv\ready.txt" (
  echo First start: setting up ^(a minute or two^)...
  if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
  if errorlevel 1 goto fail
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 goto fail
  echo ok> ".venv\ready.txt"
)
".venv\Scripts\python.exe" DRModelSwap.py
if errorlevel 1 pause
exit /b 0
:fail
echo Setting up failed. Check your internet connection and try again ^(or use DRModelSwap.exe from the release page^).
pause
exit /b 1
