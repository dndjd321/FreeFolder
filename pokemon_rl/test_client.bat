@echo off
title Pokemon Battle AI - Client Test
cd /d "%~dp0"

echo Installing pywebview...
python -m pip install pywebview --quiet 2>nul

echo Starting client...
python PokemonBattle.py

pause
