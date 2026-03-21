"""
PokemonBattle.py - 독립 실행 클라이언트
브라우저가 아닌 네이티브 윈도우에서 게임 실행
로컬 데이터(설정, 닉네임 등) exe 옆 userdata/ 폴더에 저장

빌드: build_client.bat 실행
"""
import sys
import os
import webview

# ===== 서버 주소 =====
SERVER_URL = "https://freefolder.onrender.com"

class Api:
    def quit(self):
        """HTML에서 window.pywebview.api.quit() 호출 시 앱 종료"""
        for w in webview.windows:
            w.destroy()

def main():
    # 데이터 저장 경로 (exe 옆 userdata 폴더)
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    
    storage = os.path.join(base, 'userdata')
    os.makedirs(storage, exist_ok=True)

    api = Api()

    # 네이티브 윈도우 생성
    window = webview.create_window(
        title="Pokemon Battle AI",
        url=SERVER_URL,
        width=1200,
        height=800,
        min_size=(900, 600),
        resizable=True,
        text_select=False,
        confirm_close=True,
        js_api=api,
    )
    
    # 실행 (Edge WebView2 — Windows 10/11 기본 내장)
    webview.start(storage_path=storage, debug=False)

if __name__ == "__main__":
    main()
