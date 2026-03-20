@echo off
chcp 65001 >nul
title Pokemon Battle AI - EXE Builder
color 0A

echo ============================================
echo   Pokemon Battle AI - EXE 빌드 도구
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python이 설치되지 않았습니다!
    pause
    exit /b 1
)

:: Install required packages
echo [1/4] 필수 패키지 설치 중...
pip install pyinstaller --quiet --break-system-packages 2>nul
pip install pyinstaller --quiet 2>nul

:: Check if app.py exists
if not exist "app.py" (
    echo [ERROR] app.py 파일을 찾을 수 없습니다!
    echo 이 스크립트를 pokemon_rl 폴더 안에서 실행하세요.
    pause
    exit /b 1
)

echo [2/4] 빌드 설정 생성 중...

:: Create spec file for PyInstaller
python -c "
import sys, os

# Detect what we have
has_torch = False
try:
    import torch
    has_torch = True
except:
    pass

has_pyqt = False
try:
    from PyQt6.QtWidgets import QApplication
    has_pyqt = True
except:
    pass

print(f'PyTorch: {has_torch}')
print(f'PyQt6: {has_pyqt}')

# Collect data files
datas = []
root = os.path.dirname(os.path.abspath('app.py'))

# HTML file
for name in ['pokemon_battle_v3.html', 'pokemon_battle_ai.html']:
    p = os.path.join(root, name)
    if os.path.exists(p):
        datas.append((p, '.'))
        print(f'Found: {name}')

# BGM files
for bgm in ['main_bgm.mp3', 'battle_bgm.mp3', 'win_bgm.mp3', 'lose_bgm.mp3']:
    p = os.path.join(root, bgm)
    if os.path.exists(p):
        datas.append((p, '.'))
    else:
        # Check backup_bgm folder
        p2 = os.path.join(root, 'backup_bgm', bgm)
        if os.path.exists(p2):
            datas.append((p2, '.'))

# Model file
for model in ['final_model.pt', 'best_model.pt', 'model.pt']:
    p = os.path.join(root, model)
    if os.path.exists(p):
        datas.append((p, '.'))
        print(f'Found model: {model}')

# Server core
p = os.path.join(root, 'server_core.py')
if os.path.exists(p):
    datas.append((p, '.'))

# env folder (battle_env etc)
env_dir = os.path.join(root, 'env')
if os.path.isdir(env_dir):
    for f in os.listdir(env_dir):
        if f.endswith('.py'):
            datas.append((os.path.join(env_dir, f), 'env'))
    print('Found: env/ folder')

# agents folder
agents_dir = os.path.join(root, 'agents')
if os.path.isdir(agents_dir):
    for f in os.listdir(agents_dir):
        if f.endswith('.py'):
            datas.append((os.path.join(agents_dir, f), 'agents'))
    print('Found: agents/ folder')

# data folder
data_dir = os.path.join(root, 'data')
if os.path.isdir(data_dir):
    for f in os.listdir(data_dir):
        fp = os.path.join(data_dir, f)
        if os.path.isfile(fp) and os.path.getsize(fp) < 10*1024*1024:  # < 10MB
            datas.append((fp, 'data'))

# Write datas list for spec
with open('_build_datas.txt', 'w') as f:
    for src, dst in datas:
        f.write(f'{src}|{dst}\n')

print(f'Total data files: {len(datas)}')
"

echo [3/4] PyInstaller 빌드 시작...
echo (이 과정은 3~10분 소요됩니다)
echo.

:: Build with PyInstaller
python -c "
import subprocess, sys, os

# Read datas
datas = []
if os.path.exists('_build_datas.txt'):
    with open('_build_datas.txt') as f:
        for line in f:
            line = line.strip()
            if '|' in line:
                src, dst = line.split('|', 1)
                datas.append((src, dst))

# Build PyInstaller command
cmd = [
    sys.executable, '-m', 'PyInstaller',
    '--noconfirm',
    '--name', 'PokemonBattleAI',
    '--icon', 'NONE',
    '--console',  # Show console for debugging (change to --windowed later)
    '--collect-submodules', 'uvicorn',
    '--collect-submodules', 'fastapi',
    '--collect-submodules', 'starlette',
    '--hidden-import', 'uvicorn.logging',
    '--hidden-import', 'uvicorn.loops',
    '--hidden-import', 'uvicorn.loops.auto',
    '--hidden-import', 'uvicorn.protocols',
    '--hidden-import', 'uvicorn.protocols.http',
    '--hidden-import', 'uvicorn.protocols.http.auto',
    '--hidden-import', 'uvicorn.protocols.websockets',
    '--hidden-import', 'uvicorn.protocols.websockets.auto',
    '--hidden-import', 'uvicorn.lifespan',
    '--hidden-import', 'uvicorn.lifespan.on',
    '--hidden-import', 'uvicorn.lifespan.off',
    '--hidden-import', 'engineio',
    '--hidden-import', 'pydantic',
    '--hidden-import', 'websockets',
]

# Add data files
for src, dst in datas:
    cmd.extend(['--add-data', f'{src}{os.pathsep}{dst}'])

# Try to add PyQt6 WebEngine
try:
    import PyQt6
    cmd.extend([
        '--collect-all', 'PyQt6',
        '--collect-all', 'PyQt6.QtWebEngineWidgets',
        '--collect-all', 'PyQt6.QtWebEngineCore',
    ])
    print('[+] PyQt6 WebEngine included')
except:
    print('[-] PyQt6 not found, building without desktop wrapper')

# Try to add torch (large!)
try:
    import torch
    cmd.extend(['--collect-submodules', 'torch'])
    print('[+] PyTorch included (build will be large ~2GB)')
except:
    print('[-] PyTorch not found, AI will use rule-based fallback')

# Add main script
cmd.append('app.py')

print('Running:', ' '.join(cmd[:10]), '...')
result = subprocess.run(cmd)
sys.exit(result.returncode)
"

if errorlevel 1 (
    echo.
    echo [ERROR] 빌드 실패!
    echo PyInstaller 에러를 확인하세요.
    pause
    exit /b 1
)

:: Cleanup
del _build_datas.txt 2>nul

echo.
echo [4/4] 빌드 완료!
echo.
echo ============================================
echo   결과물: dist\PokemonBattleAI\ 폴더
echo ============================================
echo.
echo 배포 방법:
echo   1. dist\PokemonBattleAI 폴더를 통째로 압축
echo   2. 친구에게 전달
echo   3. 친구는 압축 풀고 PokemonBattleAI.exe 실행
echo.
echo 주의: PyTorch 포함 시 파일 크기가 2GB+ 될 수 있습니다.
echo       torch 없이 빌드하면 200MB 정도로 줄어듭니다.
echo.
pause
