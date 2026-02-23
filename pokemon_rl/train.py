"""
train.py — 통합 실행 및 버퍼 관리 수정본
"""
import os
import torch
import numpy as np
from env.battle_env import PokemonBattleEnv, POKEMON_DB
from agents.ppo_agent import PPOAgent

# 포켓몬 이름 -> ID 매핑
POKE_ID_MAP = {name: i for i, name in enumerate(POKEMON_DB.keys())}

def train():
    # 저장 폴더
    if not os.path.exists("checkpoints"): os.makedirs("checkpoints")

    env = PokemonBattleEnv(team_size=3)
    obs_dim = env.observation_space.shape[0]
    buffer_size = 4096
    
    agent = PPOAgent(obs_dim=obs_dim, n_actions=env.action_space.n, buffer_size=buffer_size)
    
    print(f"🚀 학습 시작! 관측 차원: {obs_dim}")
    
    total_steps = 0
    obs, _ = env.reset()

    try:
        while total_steps < 1000000:
            # 1. 버퍼 채우기 (4096 스텝 동안 데이터 수집)
            for _ in range(buffer_size):
                p_id = POKE_ID_MAP.get(env.player_active.name, 0)
                o_id = POKE_ID_MAP.get(env.opponent_active.name, 0)
                env_info = {'p_id': p_id, 'o_id': o_id}
                
                action, lp, v = agent.get_action(obs, env_info)
                next_obs, reward, terminated, truncated, _ = env.step(action)
                done = terminated or truncated
                
                agent.buffer.store(obs, action, reward, v, lp, done, p_id, o_id)
                
                obs = next_obs
                total_steps += 1
                
                if done:
                    obs, _ = env.reset()

            # 2. 버퍼가 다 차면 학습 진행
            loss = agent.update()
            print(f"[{total_steps:>8,} steps] 학습 업데이트... Loss: {loss:.4f}")
            agent.save("checkpoints/latest_model.pt")

    except KeyboardInterrupt:
        print("\n🛑 학습 중단 및 저장 중...")
        agent.save("checkpoints/interrupt_save.pt")

if __name__ == "__main__":
    train()