@echo off
chcp 65001 >nul
title Pokemon Battle AI - EXE Builder
cd /d "%~dp0"

echo ============================================
echo   Pokemon Battle AI - Single EXE Builder
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python first.
    pause
    exit /b 1
)

echo [1/2] Installing packages...
python -m pip install pyinstaller pywebview --quiet

echo [2/2] Building single EXE (1-2 min)...
python -m PyInstaller --noconfirm --name "PokemonBattle" --noconsole --onefile --collect-all webview PokemonBattle.py

if errorlevel 1 (
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   DONE! Result: dist\PokemonBattle.exe
echo   Share this single file with friends.
echo   No Python needed on their PC.
echo ============================================
echo.

explorer "dist" 2>nul
pause
