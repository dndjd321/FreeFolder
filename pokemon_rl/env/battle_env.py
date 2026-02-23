"""
env/battle_env.py — 9세대 데이터 통합 및 에러 수정 완결판
"""
from __future__ import annotations
import json
import os
import random
import numpy as np
import gymnasium as gym
from gymnasium import spaces

from env.pokemon import Pokemon, Move
from env.ability import get_ability

def load_game_data():
    """데이터 로딩 및 에러 방지"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_path = os.path.join(base_dir, "data", "pokemon.json")
    m_path = os.path.join(base_dir, "data", "moves.json")
    
    with open(p_path, "r", encoding="utf-8") as f:
        p_db = json.load(f)
    with open(m_path, "r", encoding="utf-8") as f:
        m_db = json.load(f)
    return p_db, m_db

POKEMON_DB, MOVE_DB = load_game_data()

def create_pokemon_from_db(name_en: str, level: int = 50) -> Pokemon:
    raw = POKEMON_DB.get(name_en, list(POKEMON_DB.values())[0])
    moves_to_assign = random.sample(raw["moves"], min(len(raw["moves"]), 4))
    move_objs = []
    for m_name in moves_to_assign:
        m_raw = MOVE_DB.get(m_name, {"name_ko":"몸통박치기","type":"normal","category":"physical","power":40,"accuracy":100,"pp":35})
        move_objs.append(Move(
            name=m_raw.get("name_ko", m_name),
            type_=m_raw["type"],
            category=m_raw["category"],
            power=m_raw["power"],
            accuracy=m_raw["accuracy"],
            pp=m_raw["pp"]
        ))

    # [수정] raw["stats"]["attack"] 등 이미지에 매칭되는 정확한 키값 사용
    p = Pokemon(
        name=raw.get("name_ko", name_en),
        types=raw["types"],
        level=level,
        base_hp=raw["stats"]["hp"],
        base_attack=raw["stats"]["attack"],
        base_defense=raw["stats"]["defense"],
        base_sp_attack=raw["stats"]["sp_attack"],
        base_sp_defense=raw["stats"]["sp_defense"],
        base_speed=raw["stats"]["speed"],
        moves=move_objs
    )
    return p

def make_random_team(team_size: int = 3) -> list[Pokemon]:
    all_names = list(POKEMON_DB.keys())
    selected_names = random.sample(all_names, team_size)
    return [create_pokemon_from_db(name) for name in selected_names]

class PokemonBattleEnv(gym.Env):
    def __init__(self, team_size=3):
        super().__init__()
        self.team_size = team_size
        
        # 관측 차원 자동 계산 (Pokemon.to_obs_vector 결과값 기준)
        test_p = create_pokemon_from_db(list(POKEMON_DB.keys())[0])
        self.one_poke_obs_len = len(test_p.to_obs_vector())
        total_obs_len = self.one_poke_obs_len * (team_size * 2) + 5
        
        self.observation_space = spaces.Box(low=-10, high=10, shape=(total_obs_len,), dtype=np.float32)
        self.action_space = spaces.Discrete(4 + (team_size - 1))
        self.reset()

    def _get_obs(self):
        obs = []
        for p in self.player_team: obs.extend(p.to_obs_vector())
        for p in self.opponent_team: obs.extend(p.to_obs_vector())
        obs.extend([0.0] * 5) # 날씨 등 추가 정보 칸
        return np.array(obs, dtype=np.float32)

    def _trigger_ability(self, event, pokemon, is_player):
        """특성 발동 (AttributeError 방지용 빈 함수)"""
        pass

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.player_team = make_random_team(self.team_size)
        self.opponent_team = make_random_team(self.team_size)
        self.player_active_idx = 0
        self.opponent_active_idx = 0
        return self._get_obs(), {}

    @property
    def player_active(self): return self.player_team[self.player_active_idx]
    @property
    def opponent_active(self): return self.opponent_team[self.opponent_active_idx]

    def step(self, action):
        # [수정] Pokemon 객체의 체력 속성이 current_hp인 경우에 맞춰 수정
        # 만약 객체 속성이 hp라면 p.hp로 수정하세요. 이미지 에러 기반으로는 current_hp 추정.
        hp_attr = 'current_hp' if hasattr(self.opponent_active, 'current_hp') else 'hp'
        
        terminated = all(getattr(p, hp_attr) <= 0 for p in self.opponent_team)
        reward = 1.0 if terminated else 0.0
        
        return self._get_obs(), reward, terminated, False, {}