@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =========================================
echo  MineAI Translator — сборка EXE
echo =========================================
echo.

echo [1/3] Зависимости...
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 (
    echo Ошибка: не удалось установить пакеты. Проверьте Python 3.10+
    pause
    exit /b 1
)

echo [2/3] PyInstaller...
:: Добавлен флаг --icon="icon.ico"
python -m PyInstaller --noconfirm --clean --onefile --noconsole --icon="icon.ico" --add-data "mineai\data;mineai\data" --name "MineAI_Translator" mineai\__main__.py
if errorlevel 1 (
    echo Ошибка сборки.
    pause
    exit /b 1
)

echo.
echo [3/3] Готово!
echo    EXE: dist\MineAI_Translator.exe
echo.
echo Рядом с EXE положите при необходимости:
echo    settings.ini, dictionary.json, cache.json, glossaries\
echo.
pause
