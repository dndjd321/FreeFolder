@echo off
title Pokemon Battle AI - Install
echo.
echo ============================================
echo  Pokemon Battle AI - First Time Setup
echo ============================================
echo.
python --version
if errorlevel 1 (
    echo [ERROR] Python not found. Install from python.org
    pause
    exit /b 1
)
echo.
echo [1/4] Installing PyQt6...
pip install PyQt6
echo.
echo [2/4] Installing PyQt6-WebEngine...
pip install PyQt6-WebEngine
echo.
echo [3/4] Installing FastAPI and Uvicorn...
pip install fastapi uvicorn
echo.
echo [4/4] Done!
echo.
echo ============================================
echo  Setup complete! Now run run.bat
echo ============================================
echo.
pause
