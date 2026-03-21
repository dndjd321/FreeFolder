"""
PokemonBattle.py - 클라이언트 런처
Oracle Cloud 서버에 접속하여 게임 실행
빌드: pyinstaller --onefile --noconsole --name PokemonBattle PokemonBattle.py
"""
import webbrowser
import sys
import os
import tkinter as tk
from tkinter import messagebox

# ===== 서버 주소 설정 =====
# Oracle Cloud 서버 IP로 변경하세요
SERVER_URL = "http://YOUR_SERVER_IP:8765"

def main():
    # 간단한 스플래시
    root = tk.Tk()
    root.title("Pokemon Battle AI")
    root.geometry("400x200")
    root.configure(bg="#0a0a0f")
    root.resizable(False, False)
    
    # 중앙 배치
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - 200
    y = (root.winfo_screenheight() // 2) - 100
    root.geometry(f"+{x}+{y}")
    
    label = tk.Label(root, text="⚡ POKÉMON BATTLE ⚡", 
                     font=("Arial", 16, "bold"), fg="#00e5ff", bg="#0a0a0f")
    label.pack(pady=20)
    
    status = tk.Label(root, text="서버에 접속 중...", 
                      font=("Arial", 10), fg="#64748b", bg="#0a0a0f")
    status.pack(pady=5)
    
    def launch():
        status.config(text="브라우저를 열고 있습니다...")
        root.update()
        webbrowser.open(SERVER_URL)
        root.after(2000, root.destroy)
    
    root.after(500, launch)
    root.mainloop()

if __name__ == "__main__":
    main()
