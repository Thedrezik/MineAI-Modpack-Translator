@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo =========================================
echo  MineAI Translator — сборка EXE Beta45
echo =========================================
echo.

echo [1/4] Зависимости...
py -3 -m pip install -r requirements-qt.txt pyinstaller -q
if errorlevel 1 (
    echo Ошибка: не удалось установить пакеты. Проверьте Python 3.10+
    if not defined CI pause
    exit /b 1
)

py -3 -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo Ошибка: PyQt6 не установлен в этом Python-окружении.
    echo Установите зависимости из requirements-qt.txt и повторите сборку.
    if not defined CI pause
    exit /b 1
)

echo [2/4] Проверка синтаксиса всех файлов...
py -3 -m compileall mineai/ formatkit/ mineai_formatkit/ translator.py -q
if errorlevel 1 (
    echo.
    echo =============================================
    echo  ОШИБКА СИНТАКСИСА! Сборка остановлена.
    echo  Исправьте файлы, указанные выше.
    echo =============================================
    if not defined CI pause
    exit /b 1
)
echo    Все файлы корректны.

echo [3/4] PyInstaller...
py -3 -m PyInstaller --noconfirm --clean MineAI_Translator_Beta45.spec
if errorlevel 1 (
    echo Ошибка сборки.
    if not defined CI pause
    exit /b 1
)

if not exist "dist\MineAI_Translator_Beta45.exe" (
    echo Ошибка: dist\MineAI_Translator_Beta45.exe не создан.
    if not defined CI pause
    exit /b 1
)

echo.
echo [4/4] Готово!
echo    EXE: dist\MineAI_Translator_Beta45.exe
echo.
echo Рядом с EXE положите при необходимости:
echo    settings.ini, dictionary.json, glossary.json, cache.json
echo.
if not defined CI pause
exit /b 0
