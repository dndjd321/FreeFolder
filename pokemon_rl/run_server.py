"""
run_server.py - 가벼운 서버 실행기 (PyQt6 불필요)
브라우저에서 http://127.0.0.1:8765 으로 접속하여 플레이
"""
import sys
import os
import webbrowser
import time
import threading
from pathlib import Path

# 실행 파일 기준 경로 설정
if getattr(sys, 'frozen', False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

HOST = "0.0.0.0"
PORT = 8765

def open_browser():
    """서버 시작 후 1.5초 뒤 브라우저 자동 오픈"""
    time.sleep(1.5)
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n★ 브라우저에서 접속: {url}")
    print(f"★ 같은 네트워크 친구: http://<내IP>:{PORT}")
    print(f"★ 종료: Ctrl+C\n")
    webbrowser.open(url)

def main():
    print("=" * 50)
    print("  Pokemon Battle AI - Server Mode")
    print("=" * 50)
    
    # Find model
    model_path = ""
    for name in ["final_model.pt", "best_model.pt", "model.pt"]:
        if (ROOT / name).exists():
            model_path = str(ROOT / name)
            print(f"[+] Model: {name}")
            break
    
    if not model_path:
        print("[-] No model found, using rule-based AI")
    
    try:
        import uvicorn
        from server_core import create_app
    except ImportError as e:
        print(f"\n[ERROR] 필수 패키지 미설치: {e}")
        print("\n다음 명령어로 설치하세요:")
        print("  pip install fastapi uvicorn[standard]")
        input("\nEnter를 눌러 종료...")
        return
    
    app, model_status = create_app(model_path)
    print(f"[+] {model_status}")
    
    # Auto-open browser
    threading.Thread(target=open_browser, daemon=True).start()
    
    print(f"[+] Server starting on {HOST}:{PORT}...")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

if __name__ == "__main__":
    main()
