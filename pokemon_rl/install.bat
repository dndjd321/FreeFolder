@echo off
chcp 65001 > nul
title Pokemon Battle AI - Install
echo.
echo ============================================
echo  Pokemon Battle AI - First Time Setup
echo ============================================
echo.

REM Python 설치 확인
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install from python.org
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python found:
python --version
echo.

REM Visual C++ 재배포 패키지 (PyTorch DLL 필수)
echo [1/8] Visual C++ Redistributable 설치 중...
echo       (이미 설치된 경우 자동으로 넘어갑니다)
curl -L -o "%TEMP%c_redist.x64.exe" https://aka.ms/vs/17/release/vc_redist.x64.exe
if %errorlevel% neq 0 (
    echo [WARN] vc_redist 다운로드 실패. 수동 설치: https://aka.ms/vs/17/release/vc_redist.x64.exe
) else (
    "%TEMP%c_redist.x64.exe" /install /quiet /norestart
    echo [OK] Visual C++ Redistributable 설치 완료
)
echo.

echo [2/8] pip 업그레이드 중...
python -m pip install --upgrade pip
echo.

echo [3/8] Installing PyQt6...
python -m pip install PyQt6
if %errorlevel% neq 0 ( echo [WARN] PyQt6 install failed, continuing... )
echo.

echo [4/8] Installing PyQt6-WebEngine...
python -m pip install PyQt6-WebEngine
if %errorlevel% neq 0 ( echo [WARN] PyQt6-WebEngine install failed, continuing... )
echo.

echo [5/8] Installing FastAPI, Uvicorn...
python -m pip install fastapi uvicorn
if %errorlevel% neq 0 ( echo [WARN] fastapi/uvicorn install failed, continuing... )
echo.

echo [6/8] Installing Gymnasium...
python -m pip install gymnasium
if %errorlevel% neq 0 ( echo [WARN] gymnasium install failed, continuing... )
echo.

echo [7/8] Installing PyTorch 2.1.0 (CPU) + NumPy 1.x (호환 버전)...
echo       PyTorch 2.1.0 은 NumPy 2.x 와 충돌 -> 1.x 로 고정합니다
python -m pip uninstall torch numpy -y > nul 2>&1
python -m pip install torch==2.1.0 --index-url https://download.pytorch.org/whl/cpu
if %errorlevel% neq 0 ( echo [WARN] PyTorch install failed, continuing... )
python -m pip install "numpy<2"
if %errorlevel% neq 0 ( echo [WARN] numpy install failed, continuing... )
echo.

echo [8/8] 설치 확인 중...
python -c "import torch; print('[OK] PyTorch', torch.__version__)"
python -c "import numpy; print('[OK] Numpy', numpy.__version__)"
python -c "import fastapi; print('[OK] FastAPI', fastapi.__version__)"
python -c "import gymnasium; print('[OK] Gymnasium', gymnasium.__version__)"
python -c "import PyQt6; print('[OK] PyQt6 OK')"
echo.
echo ============================================
echo  설치 완료! run.bat 을 실행하세요.
echo ============================================
echo.
pause
