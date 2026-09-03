@echo off
setlocal
chcp 65001 >nul
echo Installing dependencies...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo Failed to install dependencies.
    if not defined CI pause
    exit /b 1
)
echo =========================================
echo Starting MineAI Translator...
python -m mineai
if errorlevel 1 (
    echo MineAI Translator exited with an error.
    if not defined CI pause
    exit /b 1
)
if not defined CI pause
exit /b 0
