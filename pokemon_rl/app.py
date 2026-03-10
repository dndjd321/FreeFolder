"""
app.py - Pokemon Battle AI Desktop App
Run: python app.py
Requires: pip install PyQt6 PyQt6-WebEngine fastapi uvicorn
"""
import sys
import webbrowser
from pathlib import Path

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout,
        QHBoxLayout, QPushButton, QLabel, QMessageBox, QStatusBar, QDialog
    )
    from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSignal, QThread
    from PyQt6.QtGui import QFont
except ImportError as e:
    print("=" * 50)
    print("[ERROR] PyQt6 not installed!")
    print("Run: pip install PyQt6")
    print("Detail:", e)
    print("=" * 50)
    input("Press Enter to exit...")
    sys.exit(1)

HAS_WEBENGINE = False
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    HAS_WEBENGINE = True
    print("[OK] PyQt6-WebEngine found - built-in browser available")
except ImportError:
    print("[WARN] PyQt6-WebEngine not found - will use system browser")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765


# ══════════════════════════════════════════════
# Background server thread
# ══════════════════════════════════════════════
class ServerThread(QThread):
    ready  = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, model_path):
        super().__init__()
        self.model_path = model_path
        self._server = None

    def run(self):
        try:
            import uvicorn
            from server_core import create_app
            app, model_status = create_app(self.model_path)

            # ── ready 시그널을 uvicorn이 실제로 포트를 열었을 때 발생시킴 ──
            # (이전: create_app 직후 emit → uvicorn 바인딩 전이라 타이밍 미스)
            _emitted = [False]

            @app.on_event("startup")
            async def _on_uvicorn_startup():
                if not _emitted[0]:
                    _emitted[0] = True
                    self.ready.emit(model_status)

            config = uvicorn.Config(
                app,
                host=SERVER_HOST,
                port=SERVER_PORT,
                log_level="warning"
            )
            self._server = uvicorn.Server(config)
            self._server.run()
        except ImportError as e:
            msg = "Missing package: " + str(e) + "\nRun: pip install fastapi uvicorn"
            self.failed.emit(msg)
        except Exception:
            import traceback
            self.failed.emit(traceback.format_exc())

    def stop(self):
        if self._server:
            self._server.should_exit = True


# ══════════════════════════════════════════════
# Loading screen
# ══════════════════════════════════════════════
class LoadingWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pokemon Battle AI")
        self.setFixedSize(420, 160)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(
            "QWidget { background: #0a0a0f; }"
            "QLabel  { color: #94a3b8; font-size: 11px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 36, 36, 36)
        lay.setSpacing(16)

        title = QLabel("Pokemon Battle AI")
        title.setStyleSheet("color: #00e5ff; font-size: 15px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self.msg = QLabel("Starting server...")
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.msg)

        scr = QApplication.primaryScreen().geometry()
        self.move(
            (scr.width()  - self.width())  // 2,
            (scr.height() - self.height()) // 2
        )

    def set_msg(self, text):
        self.msg.setText(text)


# ══════════════════════════════════════════════
# Custom styled quit dialog
# ══════════════════════════════════════════════
class QuitDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("포켓몬 배틀 AI")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog
        )
        self.setFixedSize(340, 190)
        self.setStyleSheet("""
            QDialog {
                background: #0f0f18;
                border: 1px solid #2a2a40;
                border-radius: 14px;
            }
        """)
        self.result_confirmed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 22)
        lay.setSpacing(0)

        # 아이콘 + 타이틀
        icon_lbl = QLabel("⚡")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 28px; margin-bottom: 6px;")
        lay.addWidget(icon_lbl)

        title = QLabel("게임 종료")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 15px; font-weight: bold;"
            "color: #e2e8f0; margin-bottom: 6px;"
        )
        lay.addWidget(title)

        sub = QLabel("포켓몬 배틀 AI를 종료하시겠습니까?")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(
            "font-size: 10px; color: #64748b; margin-bottom: 20px;"
        )
        lay.addWidget(sub)

        # 버튼 행
        btn_row = QWidget()
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(10)

        cancel_btn = QPushButton("계속 플레이")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #1a1a28;
                color: #94a3b8;
                border: 1px solid #2a2a40;
                border-radius: 8px;
                font-size: 10px;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: #242436;
                color: #e2e8f0;
                border-color: #3a3a55;
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        quit_btn = QPushButton("종료")
        quit_btn.setFixedHeight(36)
        quit_btn.setStyleSheet("""
            QPushButton {
                background: rgba(239, 68, 68, 0.12);
                color: #ef4444;
                border: 1px solid rgba(239, 68, 68, 0.4);
                border-radius: 8px;
                font-size: 10px;
                font-weight: bold;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.22);
                border-color: #ef4444;
            }
        """)
        quit_btn.clicked.connect(self.accept)

        btn_lay.addWidget(cancel_btn)
        btn_lay.addWidget(quit_btn)
        lay.addWidget(btn_row)

        # 창 중앙 배치
        if parent:
            pg = parent.geometry()
            self.move(
                pg.x() + (pg.width()  - self.width())  // 2,
                pg.y() + (pg.height() - self.height()) // 2,
            )
        else:
            scr = QApplication.primaryScreen().geometry()
            self.move(
                (scr.width()  - self.width())  // 2,
                (scr.height() - self.height()) // 2,
            )


# ══════════════════════════════════════════════
# Main game window
# ══════════════════════════════════════════════
class BattleWindow(QMainWindow):
    def __init__(self, model_status):
        super().__init__()
        self.setWindowTitle("Pokemon Battle AI  |  " + model_status)
        self.resize(1100, 780)
        self.setMinimumSize(800, 560)
        self.setStyleSheet(
            "QMainWindow { background: #0a0a0f; }"
            "QStatusBar  { background: #0d0d14; color: #64748b; font-size: 9px;"
            "              border-top: 1px solid #2a2a40; }"
            "QPushButton { background: #12121a; color: #00e5ff;"
            "              border: 1px solid #2a2a40; border-radius: 6px;"
            "              padding: 5px 14px; font-size: 10px; }"
            "QPushButton:hover { background: rgba(0,229,255,30); border-color: #00e5ff; }"
            "QLabel { color: #94a3b8; font-size: 10px; }"
        )

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # toolbar
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet("background: #0d0d14; border-bottom: 1px solid #2a2a40;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)

        lbl = QLabel("  POKEMON BATTLE AI")
        lbl.setStyleSheet("color: #00e5ff; font-size: 11px; letter-spacing: 2px;")
        bl.addWidget(lbl)
        bl.addStretch()

        ml = QLabel(model_status)
        ml.setStyleSheet("color: #22c55e; font-size: 9px;")
        bl.addWidget(ml)
        bl.addSpacing(12)

        rb = QPushButton("Reload")
        rb.setFixedWidth(75)
        rb.clicked.connect(self._reload)
        bl.addWidget(rb)

        game_url = "http://" + SERVER_HOST + ":" + str(SERVER_PORT)
        eb = QPushButton("Open in Browser")
        eb.setFixedWidth(120)
        eb.clicked.connect(lambda: webbrowser.open(game_url))
        bl.addWidget(eb)

        lay.addWidget(bar)

        # web content area
        if HAS_WEBENGINE:
            from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

            # ── 영구 프로필: localStorage가 앱 재시작 후에도 유지됨 ──
            storage_path = str(ROOT / "webdata")
            self._profile = QWebEngineProfile("pokemon_battle_ai", self)
            self._profile.setPersistentStoragePath(storage_path)
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )

            self.view = QWebEngineView()
            page = QWebEnginePage(self._profile, self.view)
            self.view.setPage(page)

            s = self.view.settings()
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            lay.addWidget(self.view)
        else:
            info_text = (
                "PyQt6-WebEngine is not installed.\n\n"
                "The game has opened in your browser.\n\n"
                "URL: " + game_url + "\n\n"
                "For built-in browser: pip install PyQt6-WebEngine"
            )
            info = QLabel(info_text)
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            info.setStyleSheet("color: #e2e8f0; font-size: 12px;")
            lay.addWidget(info)

        sb = QStatusBar()
        sb.showMessage(game_url + "  |  " + model_status)
        self.setStatusBar(sb)

        scr = QApplication.primaryScreen().geometry()
        self.move(
            (scr.width()  - self.width())  // 2,
            (scr.height() - self.height()) // 2
        )
        QTimer.singleShot(300, self._load_page)

    def _load_page(self):
        url = "http://" + SERVER_HOST + ":" + str(SERVER_PORT)
        if HAS_WEBENGINE:
            self.view.load(QUrl(url))
        else:
            webbrowser.open(url)

    def _reload(self):
        if HAS_WEBENGINE:
            self.view.reload()
        else:
            self._load_page()

    def closeEvent(self, event):
        dlg = QuitDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            event.accept()
        else:
            event.ignore()


# ══════════════════════════════════════════════
# Model finder
# ══════════════════════════════════════════════
def find_model():
    candidates = [
        ROOT / "checkpoints" / "final_model.pt",
        ROOT / "checkpoints" / "best_model.pt",
    ]
    ckpt = ROOT / "checkpoints"
    if ckpt.exists():
        candidates.extend(sorted(ckpt.glob("checkpoint_*.pt"), reverse=True))
    for p in candidates:
        if p.exists():
            print("[Model] Found:", p)
            return str(p)
    print("[Model] No model found - random AI mode")
    return ""


# ══════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════
def main():
    print("=" * 50)
    print(" Pokemon Battle AI - Starting")
    print("=" * 50)
    print("Root dir:", ROOT)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    model_path = find_model()

    loading = LoadingWindow()
    if model_path:
        loading.set_msg("Loading: " + Path(model_path).name)
    else:
        loading.set_msg("No model found - random AI mode")
    loading.show()
    app.processEvents()

    server = ServerThread(model_path)
    main_win = [None]

    def on_ready(status):
        print("[App] Server ready:", status)
        loading.set_msg("Ready! Opening game...")
        app.processEvents()

        def open_main():
            loading.close()
            main_win[0] = BattleWindow(status)
            main_win[0].show()

        QTimer.singleShot(500, open_main)

    def on_failed(err):
        print("[App] Server failed:")
        print(err)
        loading.close()
        short_err = err[:600] if len(err) > 600 else err
        QMessageBox.critical(
            None, "Server Error",
            "Server failed to start:\n\n" + short_err +
            "\n\nMake sure these are installed:\n  pip install fastapi uvicorn"
        )
        app.quit()

    server.ready.connect(on_ready)
    server.failed.connect(on_failed)
    server.start()

    def cleanup():
        server.stop()
        server.quit()
        server.wait(3000)

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
