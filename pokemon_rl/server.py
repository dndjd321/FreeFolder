"""
server.py — 학습된 PPO 모델을 웹에서 사용할 수 있게 해주는 API 서버

설치:
    pip install fastapi uvicorn

실행:
    python server.py
    python server.py --model checkpoints/final_model.pt  (모델 경로 지정)

접속:
    http://localhost:8765
"""
import argparse
import copy
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 프로젝트 경로 추가
sys.path.insert(0, str(Path(__file__).parent))

from agents.ppo_agent import PPOAgent
from env.battle_env import PokemonBattleEnv, make_sample_pokemon_pool, make_team
from env.damage_calc import get_type_multiplier
from env.weather import get_weather_move_modifier

# ══════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════
OBS_DIM   = 84   # battle_env 관측 차원
N_ACTIONS = 6    # 기술 4 + 교체 2
TEAM_SIZE = 3

app = FastAPI(title="포켓몬 배틀 AI API")

# CORS 허용 (HTML 파일에서 fetch 가능하게)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════
# 전역 상태
# ══════════════════════════════════════════════
agent: PPOAgent | None = None
active_envs: dict[str, PokemonBattleEnv] = {}   # session_id → env


# ══════════════════════════════════════════════
# 서버 시작 시 모델 로드
# ══════════════════════════════════════════════
def load_model(model_path: str):
    global agent
    agent = PPOAgent(obs_dim=OBS_DIM, n_actions=N_ACTIONS, hidden_dim=256)
    if os.path.exists(model_path):
        agent.load(model_path)
        print(f"✅ 모델 로드 완료: {model_path}")
    else:
        print(f"⚠️  모델 파일 없음: {model_path}")
        print("   → 랜덤 AI로 동작합니다.")


# ══════════════════════════════════════════════
# 요청/응답 스키마
# ══════════════════════════════════════════════
class NewBattleRequest(BaseModel):
    session_id: str = "default"
    team_size: int = 3

class ActionRequest(BaseModel):
    session_id: str = "default"
    player_action: int  # 0~3: 기술, 4~5: 교체


# ══════════════════════════════════════════════
# 환경 → JSON 직렬화
# ══════════════════════════════════════════════
def serialize_pokemon(p, is_active: bool = False):
    return {
        "id":        p.id if hasattr(p, "id") else 0,
        "name":      p.name,
        "types":     p.types,
        "hp":        max(0, p.current_hp),
        "maxHp":     p.max_hp,
        "hpRatio":   round(max(0, p.hp_ratio), 4),
        "status":    p.status,
        "ability":   getattr(p, "ability_name", p.ability),
        "fainted":   p.is_fainted,
        "moves": [
            {
                "name":      m.name,
                "type":      m.type_,
                "category":  m.category,
                "power":     m.power,
                "accuracy":  m.accuracy,
                "pp":        m.pp,
                "maxPp":     m.max_pp,
            }
            for m in p.moves
        ] if is_active else [],
        "ranks": {
            "attack":     p.rank_attack,
            "defense":    p.rank_defense,
            "sp_attack":  p.rank_sp_attack,
            "sp_defense": p.rank_sp_defense,
            "speed":      p.rank_speed,
        } if is_active else {},
    }


def serialize_env(env: PokemonBattleEnv, log_lines: list[str] = None):
    pa = env.player_active_idx
    oa = env.opponent_active_idx

    player_team = [
        {**serialize_pokemon(p, i == pa), "isActive": i == pa}
        for i, p in enumerate(env.player_team)
    ]
    opp_team = [
        {**serialize_pokemon(p, i == oa), "isActive": i == oa}
        for i, p in enumerate(env.opponent_team)
    ]

    # 플레이어 기준 타입 힌트
    opp_types = env.opponent_active.types
    move_hints = []
    for m in env.player_active.moves:
        mult = get_type_multiplier(m.type_, opp_types)
        hint = "normal"
        if mult == 0:   hint = "immune"
        elif mult >= 2: hint = "super"
        elif mult <= 0.5: hint = "resist"
        move_hints.append(hint)

    return {
        "turn":        env.turn,
        "maxTurns":    env.max_turns,
        "weather":     env.weather,
        "weatherTurns": env.weather_turns,
        "terrain":     env.terrain,
        "playerTeam":  player_team,
        "oppTeam":     opp_team,
        "playerActive": pa,
        "oppActive":   oa,
        "moveHints":   move_hints,
        "log":         log_lines or [],
        "gameOver":    False,
        "winner":      None,
    }


# ══════════════════════════════════════════════
# API 엔드포인트
# ══════════════════════════════════════════════

@app.get("/")
async def root():
    """서버 상태 확인"""
    return {
        "status": "ok",
        "model": "loaded" if (agent and agent.total_timesteps > 0) else "random",
        "sessions": len(active_envs),
    }


@app.post("/battle/new")
async def new_battle(req: NewBattleRequest):
    """새 배틀 시작 — 팀 구성 후 초기 상태 반환"""
    env = PokemonBattleEnv(team_size=req.team_size, max_turns=100)
    obs, _ = env.reset()
    active_envs[req.session_id] = env

    data = serialize_env(env, ["🎮 배틀 시작!", f"💪 {env.player_active.name} vs {env.opponent_active.name}!"])
    data["obs"] = obs.tolist()
    return JSONResponse(data)


@app.post("/battle/step")
async def battle_step(req: ActionRequest):
    """플레이어 행동 처리 + AI 행동 실행 → 다음 상태 반환"""
    env = active_envs.get(req.session_id)
    if env is None:
        return JSONResponse({"error": "세션 없음. /battle/new 먼저 호출하세요."}, status_code=404)

    action = req.player_action

    # 관측 벡터 생성
    obs = env._get_obs()

    # ── AI 행동: PPO 모델로 결정 ──
    if agent is not None:
        # 액션 마스크 (PP 없는 기술, 쓰러진 포켓몬 차단)
        mask = _make_opp_mask(env)
        ai_action = agent.predict(obs, mask)
    else:
        ai_action = _rule_based_action(env)

    # 배틀 로그 초기화 후 step 실행
    env.battle_log = []

    # env.step은 플레이어 vs 규칙기반 AI인데,
    # 여기서는 AI 행동을 PPO로 오버라이드하기 위해
    # 내부 _opponent_policy를 monkey-patch
    original_policy = env._opponent_policy
    env._opponent_policy = lambda: ai_action

    obs_next, reward, terminated, truncated, info = env.step(action)

    env._opponent_policy = original_policy  # 복원

    done = terminated or truncated
    log = env.battle_log.copy()

    data = serialize_env(env, log)
    data["obs"]     = obs_next.tolist()
    data["reward"]  = round(float(reward), 3)
    data["done"]    = done
    data["aiAction"] = ai_action

    if done:
        p_alive = sum(1 for p in env.player_team if not p.is_fainted)
        o_alive = sum(1 for p in env.opponent_team if not p.is_fainted)
        if o_alive == 0:
            data["winner"] = "player"
        elif p_alive == 0:
            data["winner"] = "ai"
        else:
            data["winner"] = "draw"
        data["gameOver"] = True
        # 세션 정리
        active_envs.pop(req.session_id, None)

    return JSONResponse(data)


@app.get("/battle/state/{session_id}")
async def get_state(session_id: str):
    """현재 배틀 상태 조회"""
    env = active_envs.get(session_id)
    if env is None:
        return JSONResponse({"error": "세션 없음"}, status_code=404)
    return JSONResponse(serialize_env(env))


@app.get("/pokemon/list")
async def pokemon_list():
    """사용 가능한 포켓몬 목록"""
    pool = make_sample_pokemon_pool()
    return JSONResponse([
        {"id": getattr(p,"id",0), "name": p.name, "types": p.types, "ability": getattr(p,"ability_name","")}
        for p in pool
    ])


# ══════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════
def _make_opp_mask(env: PokemonBattleEnv) -> np.ndarray:
    """상대(AI) 기준 액션 마스크"""
    n = env.action_space.n
    mask = np.zeros(n, dtype=bool)
    for i, m in enumerate(env.opponent_active.moves[:4]):
        if m.pp <= 0:
            mask[i] = True
    switch_opts = [
        i for i in range(env.team_size)
        if i != env.opponent_active_idx and not env.opponent_team[i].is_fainted
    ]
    for slot in range(env.team_size - 1):
        if slot >= len(switch_opts):
            mask[4 + slot] = True
    if mask.all():
        mask[:] = False
    return mask


def _rule_based_action(env: PokemonBattleEnv) -> int:
    """모델 없을 때 규칙 기반 fallback"""
    me  = env.opponent_active
    foe = env.player_active
    best, best_score = 0, -1
    for i, m in enumerate(me.moves[:4]):
        if m.pp <= 0: continue
        mult = get_type_multiplier(m.type_, foe.types)
        stab = 1.5 if m.type_ in me.types else 1.0
        score = m.power * mult * stab if m.category != "status" else 30
        if score > best_score:
            best_score = score; best = i
    return best


# ══════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str,
                        default="checkpoints/final_model.pt",
                        help="학습된 모델 경로")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    load_model(args.model)

    print(f"\n🚀 서버 시작!")
    print(f"   주소: http://localhost:{args.port}")
    print(f"   브라우저에서 pokemon_battle.html 열고 배틀 시작!\n")

    uvicorn.run(app, host=args.host, port=args.port)
