@echo off
setlocal
cd /d "%~dp0"

echo =========================================
echo  MineAI Translator - EXE build
echo =========================================
echo.

echo [1/4] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 goto :deps_error

echo [2/4] Checking Python syntax...
python -m compileall mineai/ translator.py -q
if errorlevel 1 goto :syntax_error
echo    Python sources are valid.

echo [3/4] Running PyInstaller...
python -m PyInstaller --noconfirm --clean --onefile --noconsole --icon="icon.ico" --add-data "icon.ico;." --name "MineAI_Translator" mineai\__main__.py
if errorlevel 1 goto :build_error

if not exist "dist\MineAI_Translator.exe" goto :missing_exe

echo.
echo [4/4] Done!
echo    EXE: dist\MineAI_Translator.exe
echo.
echo Optional files next to the EXE:
echo    settings.ini, dictionary.json, cache.json
echo.
if not defined CI pause
exit /b 0

:deps_error
echo ERROR: Failed to install dependencies. Check Python 3.10+.
if not defined CI pause
exit /b 1

:syntax_error
echo ERROR: Python syntax check failed. Build stopped.
if not defined CI pause
exit /b 1

:build_error
echo ERROR: PyInstaller build failed.
if not defined CI pause
exit /b 1

:missing_exe
echo ERROR: dist\MineAI_Translator.exe was not created.
if not defined CI pause
exit /b 1
