"""
PokemonBattle.py - 독립 실행 클라이언트
"""
import sys
import os
import webview

SERVER_URL = "https://freefolder.onrender.com"

class Api:
    def quit(self):
        for w in webview.windows:
            w.destroy()

def main():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    
    storage = os.path.join(base, 'userdata')
    os.makedirs(storage, exist_ok=True)

    api = Api()

    window = webview.create_window(
        title="Pokemon Battle AI",
        url=SERVER_URL,
        width=1200,
        height=800,
        min_size=(900, 600),
        resizable=True,
        text_select=False,
        js_api=api,
    )

    webview.start(
        storage_path=storage,
        private_mode=False,
        debug=False
    )

if __name__ == "__main__":
    main()
