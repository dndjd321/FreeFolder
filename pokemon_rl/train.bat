@echo off
title Pokemon Battle AI - Training
cd /d "%~dp0"

echo.
echo ================================================
echo  Pokemon Battle AI - League Self-play Training
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (echo [ERROR] Python not found & pause & exit /b 1)

python -c "import torch" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyTorch not installed!
    echo Run: pip install torch --index-url https://download.pytorch.org/whl/cpu
    pause & exit /b 1
)

echo [1] Choose training mode:
echo     1 = Quick test     (500,000 steps  ~10min)
echo     2 = Standard       (2,000,000 steps ~40min)
echo     3 = Deep           (5,000,000 steps ~2hr)
echo     4 = Resume from checkpoint
echo.
set /p MODE="Enter choice (1-4): "

if "%MODE%"=="1" goto QUICK
if "%MODE%"=="2" goto STANDARD
if "%MODE%"=="3" goto DEEP
if "%MODE%"=="4" goto RESUME
goto STANDARD

:QUICK
echo.
echo [Quick] 500,000 steps...
python train.py --timesteps 500000 --log-freq 5000 --save-freq 100000 --league-size 4
goto DONE

:STANDARD
echo.
echo [Standard] 2,000,000 steps...
python train.py --timesteps 2000000 --league-size 8
goto DONE

:DEEP
echo.
echo [Deep] 5,000,000 steps...
python train.py --timesteps 5000000 --league-size 10 --hidden-dim 512 --buffer-size 8192
goto DONE

:RESUME
echo.
if exist "checkpoints\final_model.pt" (
    echo Resuming from final_model.pt...
    python train.py --resume checkpoints\final_model.pt --timesteps 2000000
) else if exist "checkpoints\best_model.pt" (
    echo Resuming from best_model.pt...
    python train.py --resume checkpoints\best_model.pt --timesteps 2000000
) else (
    echo [ERROR] No checkpoint found in checkpoints\
    pause & exit /b 1
)

:DONE
echo.
echo Training finished! Model saved to checkpoints\
echo Run run.bat to play against the trained AI.
pause
