@echo off
setlocal
cd /d "%~dp0"
title Pokemon Battle AI - Server Setup

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required. Python 3.11 is recommended.
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 'Python 3.10 or newer is required.')"
if errorlevel 1 exit /b 1
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Install Node.js 24, then reopen this terminal.
    exit /b 1
)
node -e "if(Number(process.versions.node.split('.')[0])<24){console.error('[ERROR] Node.js 24 or newer is required.');process.exit(1)}"
if errorlevel 1 exit /b 1
call npm --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found. Reinstall Node.js with npm.
    exit /b 1
)

echo [1/3] Installing server Python packages...
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 exit /b 1

echo [2/3] Installing the locked battle engine...
call npm --prefix "%~dp0battle_service" ci --ignore-scripts --no-audit --no-fund
if errorlevel 1 exit /b 1

echo [3/3] Checking the Node worker...
python -X utf8 -B -c "from battle_service.client import BattleClient; client = BattleClient(); print(client.info); client.close()"
if errorlevel 1 exit /b 1

echo Setup complete. Run run_server.bat and open http://127.0.0.1:8765/.
exit /b 0
