"""
server_deploy.py - 클라우드 배포용 경량 서버
멀티플레이 WebSocket + HTML 게임 제공 전용
Oracle Cloud / Render / 어디서든 실행 가능

사용법:
  pip install fastapi uvicorn[standard] websockets
  python server_deploy.py
"""
import sys
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from starlette.responses import FileResponse as _FileResponse

# 경로 설정
ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", 8765))

app = FastAPI(title="Pokemon Battle AI - Multiplayer Server")

@app.on_event('startup')
async def prepare_battle_runtime():
    from services.bootstrap import ensure_node_dependencies
    await asyncio.to_thread(ensure_node_dependencies)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ── HTML 제공 ─────────────────────────────────────
def _find_html():
    for name in ["pokemon_battle_v3.html", "pokemon_battle_ai.html"]:
        p = ROOT / name
        if p.exists():
            print(f"[HTML] Found: {p}")
            return p.read_text(encoding="utf-8")
    return "<h1>pokemon_battle_v3.html not found!</h1>"

_html_cache = [None]

@app.get("/", response_class=HTMLResponse)
async def root():
    if _html_cache[0] is None:
        _html_cache[0] = _find_html()
    return _html_cache[0]

# ── BGM 파일 제공 ─────────────────────────────────
for _bgm_name in ["main_bgm.mp3", "battle_bgm.mp3", "win_bgm.mp3", "lose_bgm.mp3"]:
    _bgm_path = ROOT / _bgm_name
    if not _bgm_path.exists():
        _bgm_path = ROOT / "backup_bgm" / _bgm_name

    if _bgm_path.exists():
        _p = str(_bgm_path)
        print(f"[BGM] {_bgm_name} → {_p}")

        def _make_handler(path):
            async def handler():
                return _FileResponse(path, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
            return handler

        app.get(f"/{_bgm_name}")(_make_handler(_p))
    else:
        print(f"[BGM] {_bgm_name} → NOT FOUND")

# ── PWA 파일 제공 (manifest, service worker, 아이콘) ──
def _pwa_file(name, media_type):
    path = ROOT / name
    if not path.exists():
        path = ROOT / "backup_pwa" / name
    if path.exists():
        _p = str(path)
        async def handler():
            return _FileResponse(_p, media_type=media_type,
                headers={"Cache-Control": "no-cache"} if name in ("manifest.json", "sw.js") else {})
        return handler
    return None

for _pwa_name, _mt in [
    ("manifest.json", "application/manifest+json"),
    ("sw.js", "application/javascript"),
    ("icon-192.png", "image/png"),
    ("icon-512.png", "image/png"),
    ("apple-touch-icon.png", "image/png"),
]:
    _h = _pwa_file(_pwa_name, _mt)
    if _h:
        app.get(f"/{_pwa_name}")(_h)
        print(f"[PWA] {_pwa_name} → served")
    else:
        print(f"[PWA] {_pwa_name} → NOT FOUND")

# ── API 상태 ──────────────────────────────────────
@app.get("/api/status")
async def api_status():
    return {
        "status": "ok",
        "model_status": "Multiplayer Server (no AI)",
        "sessions": 0,
        "rooms": len(multi_rooms),
        "clients": len(multi_clients)
    }

# Shared room validation and lifecycle for desktop and cloud clients.
from services.engine_api import install_engine_routes
from services.engine_multiplayer import AuthoritativeMultiplayerService

engine_sessions = install_engine_routes(app)
multiplayer = AuthoritativeMultiplayerService(engine_sessions)
app.state.multiplayer = multiplayer
multi_rooms = multiplayer.rooms
multi_clients = multiplayer.clients

@app.websocket("/ws/multi")
async def ws_multi(ws: WebSocket):
    await multiplayer.websocket(ws)


# ── 메인 실행 ─────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Pokemon Battle AI - Multiplayer Server")
    print(f"  Port: {PORT}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
