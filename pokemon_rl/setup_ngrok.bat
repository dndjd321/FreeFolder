@echo off
title ngrok setup
cd /d "%~dp0"

echo ============================================
echo   ngrok setup
echo ============================================
echo.

python --version
if errorlevel 1 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo [1/3] Installing pyngrok...
python -m pip install pyngrok

echo.
echo [2/3] Downloading ngrok binary...
python -c "from pyngrok import ngrok; ngrok.get_ngrok_process(); print('OK - ngrok binary ready')"

echo.
echo [3/3] Setting auth token...
python -c "from pyngrok import ngrok; ngrok.set_auth_token('3B1jzVOYfWT1LRM6Abf6n6u5XjX_2h3xPCo1ZAtqTj6Gdz876'); print('OK - Auth token set')"

echo.
echo ============================================
echo   Done! Run the game and try MULTIPLAYER
echo ============================================
echo.
pause
