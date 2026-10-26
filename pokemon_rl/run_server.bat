@echo off
setlocal
cd /d "%~dp0"
title Pokemon Battle AI - Web Server

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.11 first.
    exit /b 1
)
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found. Run setup_engine.bat first.
    exit /b 1
)
if not exist "%~dp0battle_service\node_modules\pokemon-showdown\package.json" (
    echo [ERROR] The battle engine is missing. Run setup_engine.bat first.
    exit /b 1
)
python -c "import fastapi, uvicorn, numpy" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Server packages are missing. Run setup_engine.bat first.
    exit /b 1
)

if not defined PORT set "PORT=8765"
if not defined FREEFOLDER_HOST set "FREEFOLDER_HOST=127.0.0.1"
echo Server starting: http://%FREEFOLDER_HOST%:%PORT%/
echo Press Ctrl+C to stop.
python -X utf8 -B -c "import os, uvicorn; uvicorn.run('server_deploy:app', host=os.environ.get('FREEFOLDER_HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8765')), workers=1)"
exit /b %errorlevel%
