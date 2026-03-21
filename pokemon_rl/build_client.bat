@echo off
title Pokemon Battle AI - Client EXE Builder
cd /d "%~dp0"

echo ============================================
echo   Pokemon Battle AI - Client EXE Build
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo [1/3] Installing packages...
python -m pip install pyinstaller pywebview --quiet 2>nul

echo [2/3] Building EXE...
python -m PyInstaller --noconfirm ^
    --name "PokemonBattle" ^
    --noconsole ^
    --onedir ^
    --hidden-import webview ^
    --hidden-import clr ^
    --hidden-import pythonnet ^
    --collect-all webview ^
    PokemonBattle.py

if errorlevel 1 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo [3/3] Creating userdata folder...
mkdir "dist\PokemonBattle\userdata" 2>nul

echo.
echo ============================================
echo   Build complete!
echo   Result: dist\PokemonBattle\
echo ============================================
echo.
echo   Distribute:
echo     1. dist\PokemonBattle folder -> ZIP
echo     2. Send to friends
echo     3. Friends: unzip, run PokemonBattle.exe
echo.
echo   Settings saved in: userdata\ folder
echo.

explorer "dist\PokemonBattle" 2>nul
pause
