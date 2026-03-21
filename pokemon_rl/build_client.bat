@echo off
title Pokemon Battle AI - Client EXE Builder
cd /d "%~dp0"

echo ============================================
echo   Pokemon Battle AI - Client EXE Build
echo ============================================
echo.
echo   이 EXE는 서버에 접속하는 클라이언트입니다.
echo   친구에게 이 EXE만 전달하면 게임 가능!
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo [1/2] Installing PyInstaller...
python -m pip install pyinstaller --quiet 2>nul

echo [2/2] Building EXE...
python -m PyInstaller --onefile --noconsole --name "PokemonBattle" PokemonBattle.py

if errorlevel 1 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build complete!
echo   Result: dist\PokemonBattle.exe (1 file!)
echo ============================================
echo.
echo   Before distributing:
echo   1. Open PokemonBattle.py in notepad
echo   2. Change SERVER_URL to your Oracle Cloud IP
echo   3. Rebuild with this script
echo.
echo   Then give dist\PokemonBattle.exe to friends!
echo.

explorer "dist" 2>nul
pause
