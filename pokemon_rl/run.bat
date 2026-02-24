@echo off
title Pokemon Battle AI

echo.
echo ============================================
echo  Pokemon Battle AI - Starting
echo ============================================
echo.

cd /d "%~dp0"
echo Working folder: %~dp0
echo.

python --version
if errorlevel 1 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

if not exist "%~dp0app.py" (
    echo [ERROR] app.py not found!
    pause
    exit /b 1
)

python -c "import PyQt6" 2>nul
if errorlevel 1 (
    echo [ERROR] PyQt6 not installed. Run install.bat first!
    pause
    exit /b 1
)

python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo [ERROR] fastapi not installed. Run install.bat first!
    pause
    exit /b 1
)

echo All checks passed.
echo.

REM ---- DB Update ----
if exist "%~dp0data\pokemon.json" (
    echo [DB] pokemon.json found. Updating HTML database...
    python "%~dp0data\build_html_db.py" --html "%~dp0pokemon_battle_v3.html"
    if errorlevel 1 (
        echo [WARN] DB update failed, using existing data.
    ) else (
        echo [DB] Update complete!
    )
) else (
    echo [DB] No pokemon.json - skipping DB update.
    echo      Run: python data/fetch_pokeapi.py
)
echo.

REM ---- Launch App ----
echo Launching app...
python "%~dp0app.py"

if errorlevel 1 (
    echo.
    echo [ERROR] App crashed. See messages above.
    pause
)
