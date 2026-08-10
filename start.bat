@echo off
setlocal
cd /d "%~dp0"

echo Installing dependencies...
python -m pip install -r requirements.txt -q
if errorlevel 1 goto :deps_error

echo =========================================
echo Starting MineAI Translator...
python -m mineai
if errorlevel 1 goto :run_error

if not defined CI pause
exit /b 0

:deps_error
echo ERROR: Failed to install dependencies.
if not defined CI pause
exit /b 1

:run_error
echo ERROR: MineAI Translator exited with an error.
if not defined CI pause
exit /b 1
