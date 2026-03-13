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

ROOT = Path(__file__).parent
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


def _find_html():
    for name in ["pokemon_battle_v3.html", "pokemon_battle_ai.html", "pokemon_battle.html"]:
        p = ROOT / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "<h1>HTML file not found. Put pokemon_battle_v3.html in the same folder.</h1>"


def create_app(model_path: str) -> tuple:
    # ── Import project modules ───────────────────────────
    from env.battle_env import PokemonBattleEnv
    from env.damage_calc import get_type_multiplier

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
        """ROOT 디렉토리와 하위 폴더에서 오디오 파일 탐색"""
        # 1) ROOT 바로 아래 (app.py, server_core.py 같은 위치)
        p = ROOT / filename
        if p.exists():
            return str(p)
        # 2) backup_bgm 폴더
        p2 = ROOT / "backup_bgm" / filename
        if p2.exists():
            return str(p2)
        # 3) data 폴더
        p3 = ROOT / "data" / filename
        if p3.exists():
            return str(p3)
        return None

    @app.get("/main_bgm.mp3")
    async def _serve_main_bgm():
        p = _find_audio("main_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg")
        return JSONResponse({"error": "main_bgm.mp3 not found"}, status_code=404)

    @app.get("/battle_bgm.mp3")
    async def _serve_battle_bgm():
        p = _find_audio("battle_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg")
        return JSONResponse({"error": "battle_bgm.mp3 not found"}, status_code=404)

    @app.get("/win_bgm.mp3")
    async def _serve_win_bgm():
        p = _find_audio("win_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg")
        return JSONResponse({"error": "win_bgm.mp3 not found"}, status_code=404)

    @app.get("/lose_bgm.mp3")
    async def _serve_lose_bgm():
        p = _find_audio("lose_bgm.mp3")
        if p: return _FileResponse(p, media_type="audio/mpeg")
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

    @app.post("/battle/new")
    async def new_battle(req: NewReq):
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

    return app, model_status
