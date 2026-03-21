"""
server_deploy.py - 클라우드 배포용 경량 서버
멀티플레이 WebSocket + HTML 게임 제공 전용
Oracle Cloud / Render / 어디서든 실행 가능

사용법:
  pip install fastapi uvicorn[standard] websockets
  python server_deploy.py
"""
import random
import sys
import os
import string
import json
import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from starlette.responses import FileResponse as _FileResponse

# 경로 설정
ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", 8765))

app = FastAPI(title="Pokemon Battle AI - Multiplayer Server")
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

# ══════════════════════════════════════════════
# MULTIPLAYER - WebSocket Room System
# ══════════════════════════════════════════════
multi_rooms: dict = {}   # code → room dict
multi_clients: dict = {} # ws id → {ws, nickname, room_code}

def _gen_code(length=5):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        if code not in multi_rooms:
            return code

@app.websocket("/ws/multi")
async def ws_multi(ws: WebSocket):
    await ws.accept()
    ws_id = id(ws)
    multi_clients[ws_id] = {"ws": ws, "nickname": "Guest", "room_code": None}
    print(f"[Multi] Client connected: {ws_id} (total: {len(multi_clients)})")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            msg_type = data.get("type", "")

            if msg_type == "list_rooms":
                rooms_list = []
                for code, room in multi_rooms.items():
                    rooms_list.append({
                        "code": code,
                        "name": room["name"],
                        "host": room["host_nick"],
                        "players": len(room["players"]),
                        "max_players": 2,
                        "format": room.get("format", "single"),
                        "visibility": room["visibility"],
                    })
                await ws.send_json({"type": "room_list", "rooms": rooms_list})

            elif msg_type == "create_room":
                code = _gen_code()
                room = {
                    "name": data.get("name", "배틀"),
                    "host_ws": ws_id,
                    "host_nick": data.get("nickname", "Host"),
                    "visibility": data.get("visibility", "public"),
                    "password": data.get("password", ""),
                    "format": data.get("format", "single"),
                    "players": [ws_id],
                    "state": "waiting",
                    "teams": {},
                    "turn": 1,
                    "turn_actions": {},
                }
                multi_rooms[code] = room
                multi_clients[ws_id]["room_code"] = code
                multi_clients[ws_id]["nickname"] = data.get("nickname", "Host")
                await ws.send_json({"type": "room_created", "code": code, "room_name": room["name"]})
                print(f"[Multi] Room {code} created by {room['host_nick']}")

            elif msg_type == "join_room":
                code = data.get("code", "")
                room = multi_rooms.get(code)
                if not room:
                    await ws.send_json({"type": "error", "message": "방을 찾을 수 없습니다."})
                    continue
                if len(room["players"]) >= 2:
                    await ws.send_json({"type": "error", "message": "방이 가득 찼습니다."})
                    continue
                if room["visibility"] == "private" and room["password"]:
                    if data.get("password", "") != room["password"]:
                        await ws.send_json({"type": "error", "message": "비밀번호가 틀렸습니다."})
                        continue

                room["players"].append(ws_id)
                multi_clients[ws_id]["room_code"] = code
                multi_clients[ws_id]["nickname"] = data.get("nickname", "Guest")
                nick = data.get("nickname", "Guest")

                await ws.send_json({"type": "room_joined", "code": code, "room_name": room["name"]})

                # Notify host
                host_ws_id = room["host_ws"]
                if host_ws_id in multi_clients:
                    try:
                        await multi_clients[host_ws_id]["ws"].send_json({
                            "type": "opponent_joined", "nickname": nick
                        })
                    except Exception:
                        pass

                # Both players → team select
                if len(room["players"]) == 2:
                    room["state"] = "team_select"
                    for pid in room["players"]:
                        if pid in multi_clients:
                            try:
                                await multi_clients[pid]["ws"].send_json({
                                    "type": "team_select",
                                    "message": "상대가 입장! 팀을 선택하세요!"
                                })
                            except Exception:
                                pass

            elif msg_type == "team_ready":
                code = multi_clients[ws_id].get("room_code")
                room = multi_rooms.get(code) if code else None
                if not room:
                    continue
                room["teams"][ws_id] = data.get("team", [])

                for pid in room["players"]:
                    if pid != ws_id and pid in multi_clients:
                        try:
                            await multi_clients[pid]["ws"].send_json({
                                "type": "opponent_team_ready",
                                "nickname": multi_clients[ws_id].get("nickname", "상대")
                            })
                        except Exception:
                            pass

                if len(room["teams"]) == 2:
                    room["state"] = "battle"
                    room["turn"] = 1
                    room["turn_actions"] = {}
                    player_ids = room["players"]
                    for i, pid in enumerate(player_ids):
                        opp_id = player_ids[1 - i]
                        opp_nick = multi_clients.get(opp_id, {}).get("nickname", "상대")
                        if pid in multi_clients:
                            try:
                                await multi_clients[pid]["ws"].send_json({
                                    "type": "battle_start",
                                    "your_team": room["teams"][pid],
                                    "opp_team": room["teams"][opp_id],
                                    "opp_nickname": opp_nick,
                                    "you_are": "player1" if i == 0 else "player2",
                                    "format": room.get("format", "single"),
                                    "seed": random.randint(0, 999999)
                                })
                            except Exception:
                                pass

            elif msg_type == "battle_action":
                action_type = data.get("action", "")
                code = multi_clients[ws_id].get("room_code")
                room = multi_rooms.get(code) if code else None
                if not room or room["state"] != "battle":
                    continue

                if action_type == "forced_switch":
                    for pid in room["players"]:
                        if pid != ws_id and pid in multi_clients:
                            try:
                                await multi_clients[pid]["ws"].send_json({
                                    "type": "opponent_forced_switch",
                                    "pokemon_name": data.get("pokemon_name", "???")
                                })
                            except Exception:
                                pass
                    continue

                room.setdefault("turn_actions", {})[ws_id] = {
                    "action": data.get("action"),
                    "index": data.get("index", 0),
                }

                if len(room["turn_actions"]) == 2:
                    player_ids = room["players"]
                    seed = random.randint(0, 999999)
                    actions = {}
                    for i, pid in enumerate(player_ids):
                        role = "player1" if i == 0 else "player2"
                        actions[role] = room["turn_actions"].get(pid, {"action": "move", "index": 0})

                    for pid in player_ids:
                        if pid in multi_clients:
                            try:
                                await multi_clients[pid]["ws"].send_json({
                                    "type": "turn_result",
                                    "turn": room.get("turn", 1),
                                    "actions": actions,
                                    "seed": seed
                                })
                            except Exception:
                                pass

                    room["turn"] = room.get("turn", 1) + 1
                    room["turn_actions"] = {}
                else:
                    await ws.send_json({
                        "type": "action_received",
                        "message": "행동 입력 완료! 상대를 기다리는 중..."
                    })

            elif msg_type == "battle_end":
                code = multi_clients[ws_id].get("room_code")
                room = multi_rooms.get(code) if code else None
                if room:
                    room["state"] = "done"
                    for pid in room["players"]:
                        if pid in multi_clients:
                            try:
                                await multi_clients[pid]["ws"].send_json({
                                    "type": "battle_ended",
                                    "winner": data.get("winner", "unknown"),
                                    "message": data.get("message", "배틀 종료!")
                                })
                            except Exception:
                                pass

            elif msg_type == "chat":
                code = multi_clients[ws_id].get("room_code")
                room = multi_rooms.get(code) if code else None
                if not room:
                    continue
                nick = multi_clients[ws_id].get("nickname", "???")
                for pid in room["players"]:
                    if pid in multi_clients:
                        try:
                            await multi_clients[pid]["ws"].send_json({
                                "type": "chat", "nickname": nick,
                                "message": data.get("message", "")[:200]
                            })
                        except Exception:
                            pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Multi] WS error: {e}")
    finally:
        code = multi_clients.get(ws_id, {}).get("room_code")
        if code and code in multi_rooms:
            room = multi_rooms[code]
            if ws_id in room["players"]:
                room["players"].remove(ws_id)
            for pid in room["players"]:
                if pid in multi_clients:
                    try:
                        await multi_clients[pid]["ws"].send_json({
                            "type": "opponent_left",
                            "message": "상대가 나갔습니다."
                        })
                    except Exception:
                        pass
            if not room["players"]:
                del multi_rooms[code]
                print(f"[Multi] Room {code} removed")
        multi_clients.pop(ws_id, None)
        print(f"[Multi] Client {ws_id} disconnected (total: {len(multi_clients)})")


# ── 메인 실행 ─────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Pokemon Battle AI - Multiplayer Server")
    print(f"  Port: {PORT}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
