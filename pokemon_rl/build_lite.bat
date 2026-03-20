@echo off
chcp 65001 >nul
title Pokemon Battle AI - Lightweight EXE Builder
color 0A

echo ============================================
echo   Pokemon Battle AI - 경량 EXE 빌드
echo   (브라우저 버전 - PyQt6 불필요)
echo ============================================
echo.
echo 이 빌드는 친구에게 배포하기 좋은 경량 버전입니다.
echo PyQt6/PyTorch 없이 빌드하면 약 50~100MB 정도입니다.
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python이 설치되지 않았습니다!
    pause
    exit /b 1
)

echo [1/5] 필수 패키지 설치 중...
pip install pyinstaller fastapi uvicorn pydantic --quiet 2>nul

:: Check files
if not exist "server_core.py" (
    echo [ERROR] server_core.py를 찾을 수 없습니다!
    pause
    exit /b 1
)
if not exist "run_server.py" (
    echo [ERROR] run_server.py를 찾을 수 없습니다!
    pause
    exit /b 1
)

echo [2/5] 데이터 파일 수집 중...

:: Build add-data arguments
set "DATA_ARGS="

:: HTML
if exist "pokemon_battle_v3.html" set "DATA_ARGS=%DATA_ARGS% --add-data pokemon_battle_v3.html;."
if exist "pokemon_battle_ai.html" set "DATA_ARGS=%DATA_ARGS% --add-data pokemon_battle_ai.html;."

:: Server core
set "DATA_ARGS=%DATA_ARGS% --add-data server_core.py;."

:: BGM
if exist "main_bgm.mp3" set "DATA_ARGS=%DATA_ARGS% --add-data main_bgm.mp3;."
if exist "battle_bgm.mp3" set "DATA_ARGS=%DATA_ARGS% --add-data battle_bgm.mp3;."
if exist "win_bgm.mp3" set "DATA_ARGS=%DATA_ARGS% --add-data win_bgm.mp3;."
if exist "lose_bgm.mp3" set "DATA_ARGS=%DATA_ARGS% --add-data lose_bgm.mp3;."

:: Model (optional - makes build larger)
if exist "final_model.pt" (
    echo [!] final_model.pt 발견 - PyTorch 모델 포함 (빌드 크기 증가)
    set "DATA_ARGS=%DATA_ARGS% --add-data final_model.pt;."
)

:: env folder
if exist "env\battle_env.py" (
    set "DATA_ARGS=%DATA_ARGS% --add-data env;env"
    echo [+] env/ 폴더 포함
)

:: agents folder
if exist "agents\ppo_agent.py" (
    set "DATA_ARGS=%DATA_ARGS% --add-data agents;agents"
    echo [+] agents/ 폴더 포함
)

echo [3/5] PyInstaller 빌드 시작...
echo (약 2~5분 소요)
echo.

pyinstaller --noconfirm ^
    --name "PokemonBattleAI" ^
    --console ^
    --collect-submodules uvicorn ^
    --collect-submodules fastapi ^
    --collect-submodules starlette ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan.on ^
    --hidden-import uvicorn.lifespan.off ^
    --hidden-import pydantic ^
    --hidden-import websockets ^
    --hidden-import email.mime.multipart ^
    --hidden-import email.mime.text ^
    %DATA_ARGS% ^
    run_server.py

if errorlevel 1 (
    echo.
    echo [ERROR] 빌드 실패!
    pause
    exit /b 1
)

echo [4/5] 실행 스크립트 생성...

:: Create a simple launcher bat in dist folder
(
echo @echo off
echo title Pokemon Battle AI
echo echo ==============================
echo echo   Pokemon Battle AI
echo echo   브라우저에서 자동으로 열립니다
echo echo ==============================
echo echo.
echo cd /d "%%~dp0"
echo start PokemonBattleAI.exe
) > "dist\PokemonBattleAI\Pokemon Battle AI.bat"

echo [5/5] 완료!
echo.
echo ============================================
echo   빌드 성공!
echo ============================================
echo.
echo   결과물: dist\PokemonBattleAI\
echo.
echo   배포 방법:
echo     1. dist\PokemonBattleAI 폴더를 ZIP으로 압축
echo     2. 친구에게 전달
echo     3. 친구는 압축 풀고 "PokemonBattleAI.exe" 더블클릭
echo     4. 자동으로 브라우저가 열리고 게임 시작!
echo.
echo   멀티플레이:
echo     호스트: 게임에서 MULTIPLAYER → ngrok URL 생성 → 친구에게 공유
echo     게스트: MULTIPLAYER → 받은 URL 입력 → 접속
echo.

:: Open dist folder
explorer "dist\PokemonBattleAI" 2>nul

pause
