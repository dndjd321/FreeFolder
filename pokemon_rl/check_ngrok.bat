@echo off
title ngrok diagnostic
cd /d "%~dp0"

echo ============================================
echo   ngrok 진단 도구
echo ============================================
echo.

echo [1] Python version:
python --version
echo.

echo [2] pyngrok installed?
python -c "import pyngrok; print('pyngrok version:', pyngrok.__version__)" 2>&1
echo.

echo [3] ngrok binary path:
python -c "from pyngrok.conf import get_default; c=get_default(); print('Path:', c.ngrok_path); import os; print('Exists:', os.path.exists(c.ngrok_path))" 2>&1
echo.

echo [4] ngrok config path:
python -c "from pyngrok.conf import get_default; print(get_default().config_path)" 2>&1
echo.

echo [5] ngrok version (direct):
python -c "from pyngrok.conf import get_default; import subprocess; r=subprocess.run([get_default().ngrok_path, 'version'], capture_output=True, text=True); print(r.stdout.strip() or r.stderr.strip())" 2>&1
echo.

echo [6] Auth token check:
python -c "from pyngrok.conf import get_default; p=get_default().config_path; f=open(p,'r'); print(f.read()[:200]); f.close()" 2>&1
echo.

echo [7] Try ngrok connect:
python -c "from pyngrok import ngrok; t=ngrok.connect(8765); print('URL:', t.public_url); print('Type:', type(t)); ngrok.kill()" 2>&1
echo.

echo [8] Try direct subprocess:
python -c "
import subprocess, time, re, sys
from pyngrok.conf import get_default
ngrok_bin = get_default().ngrok_path
print('Binary:', ngrok_bin)
# kill existing
subprocess.run(['taskkill','/f','/im','ngrok.exe'], capture_output=True)
time.sleep(1)
p = subprocess.Popen([ngrok_bin,'http','8765','--log','stdout'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
start = time.time()
while time.time()-start < 8:
    line = p.stdout.readline().decode('utf-8','ignore')
    if not line:
        time.sleep(0.3)
        continue
    print(line.strip())
    m = re.search(r'url=(https?://[^\s]+)', line)
    if m:
        print('SUCCESS:', m.group(1))
        break
else:
    print('Timeout - trying API...')
    try:
        import urllib.request, json
        r = urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=3)
        d = json.loads(r.read())
        print('API response:', json.dumps(d, indent=2)[:500])
    except Exception as e:
        print('API failed:', e)
p.kill()
subprocess.run(['taskkill','/f','/im','ngrok.exe'], capture_output=True)
" 2>&1
echo.

echo ============================================
echo   진단 완료 - 위 내용을 스크린샷 찍어주세요
echo ============================================
pause
