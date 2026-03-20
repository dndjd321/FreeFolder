"""
server_core.py - Embedded FastAPI server for app.py
Works with or without PyTorch (falls back to rule-based AI if torch unavailable)
"""
import random
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── PyTorch: optional ────────────────────────────────────
try:
    import torch
    import numpy as np
    HAS_TORCH = True
    print("[Server] PyTorch available:", torch.__version__)
except Exception as e:
    HAS_TORCH = False
    print("[Server] PyTorch unavailable, using rule-based AI:", e)
    import numpy as np  # numpy alone is fine

# ── Sprite ID map ────────────────────────────────────────
SPRITE_IDS = {
    "리자몽": 6,   "거북왕": 9,   "이상해꽃": 3,  "팬텀": 94,
    "괴력몬": 68,  "라프라스": 131, "한카리아스": 445,
    "루카리오": 448, "보만다": 373, "잠만보": 143,
}

OBS_DIM   = 84
N_ACTIONS = 6


def _get_search_paths():
    """PyInstaller 번들 + 실행 위치 모두 검색"""
    paths = [ROOT]
    # PyInstaller 임시 폴더
    if hasattr(sys, '_MEIPASS'):
        paths.append(Path(sys._MEIPASS))
    return paths

def _find_html():
    for base in _get_search_paths():
        for name in ["pokemon_battle_v3.html", "pokemon_battle_ai.html", "pokemon_battle.html"]:
            p = base / name
            if p.exists():
                print(f"[HTML] Found: {p}")
                return p.read_text(encoding="utf-8")
    return "<h1>HTML file not found. Put pokemon_battle_v3.html in the same folder.</h1>"


def create_app(model_path: str) -> tuple:
    # ── Import project modules (optional for lightweight builds) ───
    HAS_ENV = False
    try:
        # PyInstaller frozen: _MEIPASS에서 검색
        if hasattr(sys, '_MEIPASS'):
            sys.path.insert(0, sys._MEIPASS)
        from env.battle_env import PokemonBattleEnv
        from env.damage_calc import get_type_multiplier
        HAS_ENV = True
    except ImportError:
        print("[Server] env/ 모듈 없음 — HTML 배틀만 사용 가능 (서버 API 배틀 비활성)")
        PokemonBattleEnv = None
        get_type_multiplier = lambda a, b: 1.0

    # ── Load PPO agent (only if torch works) ────────────
    agent = None
    model_status = "Rule-based AI (PyTorch unavailable)"

    if HAS_TORCH and model_path and Path(model_path).exists():
        try:
            from agents.ppo_agent import PPOAgent
            agent = PPOAgent(obs_dim=OBS_DIM, n_actions=N_ACTIONS, hidden_dim=256)
            agent.load(model_path)
            steps = getattr(agent, "total_timesteps", 0)
            model_status = "PPO AI (" + Path(model_path).name + ", " + str(steps) + " steps)"
            print("[Server] Model loaded:", model_status)
        except Exception as e:
            agent = None
            model_status = "Rule-based AI (model load failed: " + str(e)[:60] + ")"
            print("[Server] Model load failed:", e)
    elif HAS_TORCH:
        model_status = "Rule-based AI (no model file)"

    # ── Subclass env to override opponent policy ─────────
    AIPatchedEnv = None
    if HAS_ENV and PokemonBattleEnv is not None:
        class AIPatchedEnv(PokemonBattleEnv):
            _forced_action: int = -1
            def _opponent_policy(self):
                if self._forced_action >= 0:
                    return self._forced_action
                return super()._opponent_policy()

    sessions: dict = {}
    _html = [None]

    def get_html():
        if _html[0] is None:
            _html[0] = _find_html()
        return _html[0]

    def tmult(atk_type, def_types):
        m = 1.0
        for d in def_types:
            m *= get_type_multiplier(atk_type, d)
        return m

    def rule_based_action(env):
        me  = env.opponent_active
        foe = env.player_active
        best, best_score = 0, -1.0
        for i, mv in enumerate(me.moves[:4]):
            if mv.pp <= 0:
                continue
            if mv.category == "status":
                score = 25.0
            else:
                mult = tmult(mv.type_, foe.types)
                stab = 1.5 if mv.type_ in me.types else 1.0
                score = float(mv.power) * mult * stab
            if score > best_score:
                best_score = score
                best = i
        return best

    def choose_ai_action(env):
        if agent is not None:
            try:
                obs  = env._get_obs()
                mask = np.zeros(N_ACTIONS, dtype=bool)
                for i, mv in enumerate(env.opponent_active.moves[:4]):
                    if mv.pp <= 0:
                        mask[i] = True
                sw = [i for i in range(env.team_size)
                      if i != env.opponent_active_idx
                      and not env.opponent_team[i].is_fainted]
                for slot in range(2):
                    if slot >= len(sw):
                        mask[4 + slot] = True
                if mask.all():
                    mask[:] = False
                return agent.predict(obs, mask)
            except Exception as e:
                print("[AI] predict failed, fallback:", e)
        return rule_based_action(env)

    def serial_move(m):
        return {
            "name":     m.name,
            "type":     m.type_,
            "category": m.category,
            "power":    m.power,
            "accuracy": m.accuracy,
            "pp":       m.pp,
            "maxPp":    m.max_pp,
        }

    def serial_pokemon(p, active=False):
        return {
            "id":      SPRITE_IDS.get(p.name, 1),
            "name":    p.name,
            "types":   p.types,
            "hp":      max(0, p.current_hp),
            "maxHp":   p.max_hp,
            "hpRatio": round(max(0.0, p.hp_ratio), 4),
            "status":  p.status,
            "ability": p.ability,
            "fainted": p.is_fainted,
            "moves":   [serial_move(m) for m in p.moves] if active else [],
        }

    def move_hints(env):
        result = []
        for mv in env.player_active.moves:
            x = tmult(mv.type_, env.opponent_active.types)
            if x == 0:
                result.append("immune")
            elif x >= 2:
                result.append("super")
            elif x <= 0.5:
                result.append("resist")
            else:
                result.append("normal")
        return result

    def serial_env(env, log=None):
        pa = env.player_active_idx
        oa = env.opponent_active_idx
        return {
            "turn":         env.turn,
            "maxTurns":     env.max_turns,
            "weather":      env.weather,
            "weatherTurns": env.weather_turns,
            "playerTeam":   [serial_pokemon(p, i == pa) for i, p in enumerate(env.player_team)],
            "oppTeam":      [serial_pokemon(p, i == oa) for i, p in enumerate(env.opponent_team)],
            "playerActive": pa,
            "oppActive":    oa,
            "moveHints":    move_hints(env),
            "log":          log or [],
            "done":         False,
            "winner":       None,
        }

    # ── FastAPI app ───────────────────────────────────────
    app = FastAPI(title="Pokemon Battle AI")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    # ── BGM/오디오 파일 서빙 ──────────────────────────────
    from starlette.responses import FileResponse as _FileResponse
    import os as _os

    def _find_audio(filename):
        """ROOT + PyInstaller 번들 + backup_bgm에서 오디오 파일 탐색"""
        for base in _get_search_paths():
            p = base / filename
            if p.exists():
                return str(p)
            p2 = base / "backup_bgm" / filename
            if p2.exists():
                return str(p2)
            p3 = base / "data" / filename
            if p3.exists():
                return str(p3)
        return None

    @app.get("/main_bgm.mp3")
    async def _serve_main_bgm():
        p = _find_audio("main_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control":"no-cache, no-store, must-revalidate","Pragma":"no-cache"})
        return JSONResponse({"error": "main_bgm.mp3 not found"}, status_code=404)

    @app.get("/battle_bgm.mp3")
    async def _serve_battle_bgm():
        p = _find_audio("battle_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control":"no-cache, no-store, must-revalidate","Pragma":"no-cache"})
        return JSONResponse({"error": "battle_bgm.mp3 not found"}, status_code=404)

    @app.get("/win_bgm.mp3")
    async def _serve_win_bgm():
        p = _find_audio("win_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control":"no-cache, no-store, must-revalidate","Pragma":"no-cache"})
        return JSONResponse({"error": "win_bgm.mp3 not found"}, status_code=404)

    @app.get("/lose_bgm.mp3")
    async def _serve_lose_bgm():
        p = _find_audio("lose_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control":"no-cache, no-store, must-revalidate","Pragma":"no-cache"})
        return JSONResponse({"error": "lose_bgm.mp3 not found"}, status_code=404)

    # 로그: 어느 경로에서 찾는지 출력
    for _fn in ["main_bgm.mp3", "battle_bgm.mp3", "win_bgm.mp3", "lose_bgm.mp3"]:
        _found = _find_audio(_fn)
        if _found:
            print(f"[BGM] {_fn} → {_found}")
        else:
            print(f"[BGM] {_fn} → NOT FOUND")

    class NewReq(BaseModel):
        session_id: str = "default"
        team_size:  int = 3

    class StepReq(BaseModel):
        session_id:    str = "default"
        player_action: int

    # 배틀 리플레이 버퍼 (session_id → transitions 리스트)
    replay_buffer: dict = {}
    REPLAY_PATH = Path("data/player_battles.jsonl")
    REPLAY_PATH.parent.mkdir(exist_ok=True)

    def save_replay(session_id: str, winner: str):
        """배틀 종료 시 transitions를 jsonl에 저장"""
        transitions = replay_buffer.pop(session_id, [])
        if not transitions:
            return
        record = {
            "winner":      winner,
            "reward":      1.0 if winner == "ai" else -1.0,
            "n_turns":     len(transitions),
            "transitions": transitions,
        }
        try:
            with open(REPLAY_PATH, "a", encoding="utf-8") as f:
                f.write(__import__("json").dumps(record, ensure_ascii=False) + "\n")
            # 최대 2000 에피소드만 유지
            lines = REPLAY_PATH.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2000:
                REPLAY_PATH.write_text("\n".join(lines[-2000:]) + "\n", encoding="utf-8")
        except Exception:
            pass

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return get_html()

    @app.get("/api/status")
    async def api_status():
        return {
            "status":       "ok",
            "model":        "loaded" if agent is not None else "random",
            "model_status": model_status,
            "sessions":     len(sessions),
        }

    # ── ngrok 터널 관리 ──────────────────────────────────
    _ngrok_tunnel = [None]
    _ngrok_url = [None]
    _ngrok_process = [None]

    @app.post("/api/ngrok/start")
    async def ngrok_start():
        import subprocess as _sp
        import time as _time

        # Method 1: pyngrok
        try:
            from pyngrok import ngrok, conf
            print("[ngrok] Trying pyngrok...")

            # Kill any existing
            try:
                ngrok.kill()
                _time.sleep(1)
            except Exception:
                pass

            # Set auth token directly in config
            try:
                pyngrok_conf = conf.get_default()
                print(f"[ngrok] Binary path: {pyngrok_conf.ngrok_path}")
                print(f"[ngrok] Config path: {conf.get_default().config_path}")
            except Exception as e:
                print(f"[ngrok] Config check: {e}")

            # Try connect
            tunnel = ngrok.connect(8765)
            url = getattr(tunnel, 'public_url', None) or str(tunnel)
            if url and url.startswith('http'):
                _ngrok_tunnel[0] = tunnel
                _ngrok_url[0] = url
                print(f"[ngrok] SUCCESS via pyngrok: {url}")
                return {"url": url, "status": "ok"}
            else:
                print(f"[ngrok] pyngrok returned invalid URL: {url}")
                ngrok.kill()
        except ImportError:
            print("[ngrok] pyngrok not installed")
        except Exception as e:
            print(f"[ngrok] pyngrok failed: {e}")
            try:
                from pyngrok import ngrok
                ngrok.kill()
            except Exception:
                pass

        # Method 2: Direct subprocess
        print("[ngrok] Trying direct subprocess...")
        try:
            # Find ngrok binary
            ngrok_bin = None

            # Check common paths
            import shutil
            ngrok_bin = shutil.which("ngrok")

            if not ngrok_bin:
                # Check pyngrok default path
                try:
                    from pyngrok.conf import get_default
                    ngrok_bin = get_default().ngrok_path
                    if not Path(ngrok_bin).exists():
                        ngrok_bin = None
                except Exception:
                    pass

            if not ngrok_bin:
                # Check common Windows paths
                for p in [
                    Path.home() / ".ngrok2" / "ngrok.exe",
                    Path.home() / "AppData" / "Local" / "ngrok" / "ngrok.exe",
                    Path.home() / ".pyngrok" / "ngrok" / "ngrok.exe",  # pyngrok v7+
                ]:
                    if p.exists():
                        ngrok_bin = str(p)
                        break

            if not ngrok_bin:
                return JSONResponse({
                    "error": "ngrok 바이너리를 찾을 수 없습니다.\nCMD에서 실행: python -c \"from pyngrok import ngrok; print(ngrok.get_ngrok_process())\"\n또는 https://ngrok.com/download 에서 직접 다운로드"
                }, status_code=500)

            print(f"[ngrok] Found binary: {ngrok_bin}")

            # Kill existing ngrok processes
            if sys.platform == 'win32':
                _sp.run(["taskkill", "/f", "/im", "ngrok.exe"], capture_output=True)
            else:
                _sp.run(["pkill", "-f", "ngrok"], capture_output=True)
            _time.sleep(1)

            # Start ngrok
            proc = _sp.Popen(
                [ngrok_bin, "http", "8765", "--log", "stdout"],
                stdout=_sp.PIPE, stderr=_sp.PIPE,
                creationflags=_sp.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            _ngrok_process[0] = proc

            # Wait for URL in output (max 10 seconds)
            import re
            start = _time.time()
            url = None
            while _time.time() - start < 10:
                line = proc.stdout.readline().decode('utf-8', errors='ignore')
                if not line:
                    _time.sleep(0.2)
                    continue
                print(f"[ngrok] {line.strip()}")
                # Look for url= in log
                m = re.search(r'url=(https?://[^\s"]+)', line)
                if m:
                    url = m.group(1)
                    break
                # Also check for addr= format
                m2 = re.search(r'addr=(https?://[^\s"]+)', line)
                if m2:
                    url = m2.group(1)
                    break

            if not url:
                # Try ngrok API as fallback
                _time.sleep(2)
                try:
                    import urllib.request
                    api_resp = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3)
                    import json as _json2
                    tunnels = _json2.loads(api_resp.read())
                    if tunnels.get("tunnels"):
                        url = tunnels["tunnels"][0].get("public_url")
                except Exception as e2:
                    print(f"[ngrok] API fallback failed: {e2}")

            if url and url.startswith('http'):
                _ngrok_url[0] = url
                print(f"[ngrok] SUCCESS via subprocess: {url}")
                return {"url": url, "status": "ok"}
            else:
                # Read stderr for error
                err = ""
                try:
                    err = proc.stderr.read(2000).decode('utf-8', errors='ignore')
                except Exception:
                    pass
                return JSONResponse({
                    "error": f"ngrok URL 생성 실패.\n{err[:200]}\nCMD에서 직접 테스트: ngrok http 8765"
                }, status_code=500)

        except Exception as e:
            return JSONResponse({"error": f"ngrok 실행 오류: {str(e)}"}, status_code=500)

    @app.post("/api/ngrok/stop")
    async def ngrok_stop():
        import subprocess as _sp
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass
        try:
            if _ngrok_process[0]:
                _ngrok_process[0].kill()
                _ngrok_process[0] = None
        except Exception:
            pass
        if sys.platform == 'win32':
            _sp.run(["taskkill", "/f", "/im", "ngrok.exe"], capture_output=True)
        _ngrok_url[0] = None
        _ngrok_tunnel[0] = None
        print("[ngrok] Stopped")
        return {"status": "stopped"}

    @app.post("/battle/new")
    async def new_battle(req: NewReq):
        if AIPatchedEnv is None:
            return JSONResponse({"error": "Server battle engine not available (env modules missing)"}, status_code=501)
        env = AIPatchedEnv(team_size=req.team_size, max_turns=100)
        obs, _ = env.reset()
        sessions[req.session_id] = env
        replay_buffer[req.session_id] = []          # 리플레이 초기화
        data = serial_env(env, [
            "Battle Start!",
            env.player_active.name + " vs " + env.opponent_active.name + "!",
        ])
        data["obs"] = obs.tolist()
        return JSONResponse(data)

    @app.post("/battle/step")
    async def battle_step(req: StepReq):
        env = sessions.get(req.session_id)
        if env is None:
            return JSONResponse({"error": "session not found"}, status_code=404)

        obs_before = env._get_obs()                 # 행동 전 관측
        ai_act = choose_ai_action(env)
        env._forced_action = ai_act
        env.battle_log = []

        obs_next, reward, terminated, truncated, _ = env.step(req.player_action)
        env._forced_action = -1

        # 리플레이 저장 (플레이어 관점 transition)
        replay_buffer.setdefault(req.session_id, []).append({
            "obs":    obs_before.tolist(),
            "action": req.player_action,
            "reward": float(reward),
        })

        done = terminated or truncated
        log  = list(env.battle_log)
        data = serial_env(env, log)
        data.update({
            "obs":      obs_next.tolist(),
            "reward":   float(reward),
            "done":     done,
            "aiAction": ai_act,
        })

        if done:
            p_alive = sum(1 for p in env.player_team if not p.is_fainted)
            o_alive = sum(1 for p in env.opponent_team if not p.is_fainted)
            if o_alive == 0 and p_alive > 0:
                data["winner"] = "player"
            elif p_alive == 0 and o_alive > 0:
                data["winner"] = "ai"
            else:
                data["winner"] = "draw"
            # 리플레이 저장
            save_replay(req.session_id, data["winner"])
            sessions.pop(req.session_id, None)

        return JSONResponse(data)

    @app.delete("/battle/{session_id}")
    async def end_battle(session_id: str):
        sessions.pop(session_id, None)
        return {"ok": True}

    # ══════════════════════════════════════════════
    # MULTIPLAYER - WebSocket Room System
    # ══════════════════════════════════════════════
    from fastapi import WebSocket, WebSocketDisconnect
    import string
    import asyncio
    import json as _json

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
        print(f"[Multi] Client connected: {ws_id}")

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = _json.loads(raw)
                except Exception:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "list_rooms":
                    rooms_list = []
                    for code, room in multi_rooms.items():
                        if room["visibility"] == "public" or room.get("password") == "":
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
                        "state": "waiting",  # waiting → team_select → battle → done
                        "teams": {},
                        "battle_log": [],
                    }
                    multi_rooms[code] = room
                    multi_clients[ws_id]["room_code"] = code
                    multi_clients[ws_id]["nickname"] = data.get("nickname", "Host")
                    await ws.send_json({"type": "room_created", "code": code, "room_name": room["name"]})
                    print(f"[Multi] Room created: {code} by {room['host_nick']}")

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
                    print(f"[Multi] {nick} joined room {code}")

                    # Notify host
                    host_ws_id = room["host_ws"]
                    if host_ws_id in multi_clients:
                        try:
                            await multi_clients[host_ws_id]["ws"].send_json({
                                "type": "opponent_joined",
                                "nickname": nick
                            })
                        except Exception:
                            pass

                    # Both players present → start team select
                    if len(room["players"]) == 2:
                        room["state"] = "team_select"
                        for pid in room["players"]:
                            if pid in multi_clients:
                                try:
                                    await multi_clients[pid]["ws"].send_json({
                                        "type": "team_select",
                                        "message": "상대가 입장했습니다! 팀을 선택하세요!"
                                    })
                                except Exception:
                                    pass

                elif msg_type == "team_ready":
                    # Player submitted their team
                    code = multi_clients[ws_id].get("room_code")
                    room = multi_rooms.get(code) if code else None
                    if not room:
                        continue
                    room["teams"][ws_id] = data.get("team", [])

                    # Both teams ready → start battle
                    if len(room["teams"]) == 2:
                        room["state"] = "battle"
                        player_ids = room["players"]
                        for i, pid in enumerate(player_ids):
                            opp_id = player_ids[1 - i]
                            opp_nick = multi_clients.get(opp_id, {}).get("nickname", "상대")
                            if pid in multi_clients:
                                try:
                                    await multi_clients[pid]["ws"].send_json({
                                        "type": "battle_start",
                                        "your_team": room["teams"][pid],
                                        "opp_nickname": opp_nick,
                                        "you_are": "player1" if i == 0 else "player2",
                                        "format": room["format"]
                                    })
                                except Exception:
                                    pass

                elif msg_type == "battle_action":
                    # Forward action to opponent
                    code = multi_clients[ws_id].get("room_code")
                    room = multi_rooms.get(code) if code else None
                    if not room:
                        continue
                    for pid in room["players"]:
                        if pid != ws_id and pid in multi_clients:
                            try:
                                await multi_clients[pid]["ws"].send_json({
                                    "type": "opponent_action",
                                    "action": data.get("action"),
                                    "action_data": data.get("action_data")
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
                                    "type": "chat",
                                    "nickname": nick,
                                    "message": data.get("message", "")[:200]
                                })
                            except Exception:
                                pass

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[Multi] WS error: {e}")
        finally:
            # Cleanup
            code = multi_clients.get(ws_id, {}).get("room_code")
            if code and code in multi_rooms:
                room = multi_rooms[code]
                if ws_id in room["players"]:
                    room["players"].remove(ws_id)
                # Notify remaining player
                for pid in room["players"]:
                    if pid in multi_clients:
                        try:
                            await multi_clients[pid]["ws"].send_json({
                                "type": "opponent_left",
                                "message": "상대가 나갔습니다."
                            })
                        except Exception:
                            pass
                # Remove empty rooms
                if not room["players"]:
                    del multi_rooms[code]
                    print(f"[Multi] Room {code} removed (empty)")
            multi_clients.pop(ws_id, None)
            print(f"[Multi] Client disconnected: {ws_id}")

    return app, model_status
