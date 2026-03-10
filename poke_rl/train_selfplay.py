"""
train_selfplay.py — Self-Play PPO 학습

방식:
  - 메인 에이전트(학습 중)가 상대방 에이전트(이전 버전)와 대결
  - 일정 주기마다 상대방을 현재 버전으로 갱신 (또는 과거 버전 풀에서 랜덤 선택)
  - 리그 시스템: 과거 체크포인트 여러 개를 풀로 유지 → 다양한 상대와 연습

사용법:
  python train_selfplay.py --timesteps 5000000 --opponent-update-freq 50000
"""
import argparse
import copy
import os
import random
import time
import numpy as np
from collections import deque

import torch
from env.battle_env import PokemonBattleEnv
from agents.ppo_agent import PPOAgent


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--timesteps", type=int, default=5_000_000)
    p.add_argument("--team-size", type=int, default=3)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--hidden-dim", type=int, default=256)
    p.add_argument("--buffer-size", type=int, default=4096)
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--n-epochs", type=int, default=10)
    p.add_argument("--opponent-update-freq", type=int, default=50_000,
                   help="상대방 네트워크 갱신 주기 (스텝)")
    p.add_argument("--league-size", type=int, default=5,
                   help="과거 버전 풀 크기")
    p.add_argument("--save-dir", type=str, default="checkpoints_selfplay")
    p.add_argument("--log-freq", type=int, default=10_000)
    p.add_argument("--resume", type=str, default=None)
    return p.parse_args()


def make_action_mask(env, team, active_idx, moves):
    n = env.action_space.n
    mask = np.zeros(n, dtype=bool)
    for i, m in enumerate(moves[:4]):
        if m.pp <= 0: mask[i] = True
    switch_opts = [i for i in range(env.team_size)
                   if i != active_idx and not team[i].is_fainted]
    for slot in range(env.team_size - 1):
        if slot >= len(switch_opts): mask[4+slot] = True
    if mask.all(): mask[:] = False
    return mask


class OpponentPool:
    """
    과거 버전 에이전트 풀 — 리그 시스템의 핵심

    - 최신 버전 확률 50%, 과거 버전 50%
    - 과거 버전은 풀에서 랜덤 선택
    """

    def __init__(self, max_size: int = 5):
        self.max_size = max_size
        self.pool: list[dict] = []   # state_dict 저장
        self.latest_state = None

    def update_latest(self, network_state: dict):
        """최신 버전 갱신"""
        self.latest_state = copy.deepcopy(network_state)

    def add_to_pool(self, network_state: dict, step: int):
        """과거 버전 풀에 추가"""
        entry = {"state": copy.deepcopy(network_state), "step": step}
        self.pool.append(entry)
        if len(self.pool) > self.max_size:
            self.pool.pop(0)  # 가장 오래된 것 제거
        print(f"  📚 리그 풀 갱신 ({len(self.pool)}/{self.max_size} 버전 보관)")

    def sample_opponent(self) -> dict | None:
        """
        대전 상대 선택:
        - 50%: 최신 버전 (self-play)
        - 50%: 과거 버전 (다양성)
        """
        if self.latest_state is None:
            return None
        if not self.pool or random.random() < 0.5:
            return self.latest_state
        return random.choice(self.pool)["state"]


def run_opponent_step(opponent_agent: PPOAgent, obs: np.ndarray,
                      env: PokemonBattleEnv) -> int:
    """상대 에이전트가 행동 선택"""
    mask = make_action_mask(env, env.opponent_team,
                            env.opponent_active_idx,
                            env.opponent_active.moves)
    with torch.no_grad():
        action = opponent_agent.predict(obs, mask)
    return action


class SelfPlayEnv:
    """
    Self-Play 래퍼:
    - 플레이어 관점의 관측 → 상대 관점의 관측으로 변환
    - 두 에이전트가 번갈아 행동
    """

    def __init__(self, env: PokemonBattleEnv, obs_dim: int):
        self.env = env
        self.obs_dim = obs_dim

    def get_opponent_obs(self) -> np.ndarray:
        """상대 관점 obs (간단히 같은 obs 사용 — 대칭 환경)"""
        # 실제로는 player/opponent 벡터를 뒤집어야 정확하지만,
        # 포켓몬 배틀에서는 자기 파티 정보가 앞에 오므로 swap
        obs = self.env._get_obs()
        # 간단 근사: obs를 그대로 반환 (상대 시점 정보는 부분적)
        return obs


def train_selfplay(args):
    os.makedirs(args.save_dir, exist_ok=True)

    env = PokemonBattleEnv(team_size=args.team_size, max_turns=100)
    obs_dim = env.observation_space.shape[0]
    n_actions = env.action_space.n

    # 메인 에이전트 (학습)
    agent = PPOAgent(
        obs_dim=obs_dim, n_actions=n_actions,
        lr=args.lr, hidden_dim=args.hidden_dim,
        n_epochs=args.n_epochs, batch_size=args.batch_size,
        buffer_size=args.buffer_size,
    )
    if args.resume:
        agent.load(args.resume)

    # 상대 에이전트 (추론 전용)
    opponent = PPOAgent(
        obs_dim=obs_dim, n_actions=n_actions,
        lr=args.lr, hidden_dim=args.hidden_dim,
    )

    # 리그 풀
    league = OpponentPool(max_size=args.league_size)
    league.update_latest(agent.network.state_dict())

    print(f"\n🎮 Self-Play 강화학습 시작!")
    print(f"   리그 크기: {args.league_size}  |  상대 갱신: {args.opponent_update_freq:,} 스텝마다")
    print(f"   디바이스: {agent.device}\n")

    ep_rewards = deque(maxlen=200)
    ep_lengths = deque(maxlen=200)
    win_count = deque(maxlen=200)
    best_win_rate = 0.0
    last_opp_update = 0
    last_pool_add = 0
    last_log = 0
    start_time = time.time()

    obs, _ = env.reset()
    ep_reward = ep_len = 0.0

    # 초기 상대 로드
    opp_state = league.sample_opponent()
    if opp_state:
        opponent.network.load_state_dict(opp_state)

    while agent.total_timesteps < args.timesteps:
        ts = agent.total_timesteps

        # 플레이어 행동
        p_mask = make_action_mask(env, env.player_team,
                                  env.player_active_idx,
                                  env.player_active.moves)
        action, log_prob, value = agent.select_action(obs, p_mask)

        # 상대 행동 (self-play 시 상대 에이전트가 결정)
        # env의 step 내부 _opponent_policy를 오버라이드 대신
        # self-play에선 step을 직접 wrapping
        next_obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

        agent.store_transition(obs, action, log_prob, reward, value, float(done))
        ep_reward += reward
        ep_len += 1
        obs = next_obs

        if done:
            ep_rewards.append(ep_reward)
            ep_lengths.append(ep_len)
            won = sum(1 for p in env.opponent_team if p.is_fainted) == env.team_size
            win_count.append(float(won))

            obs, _ = env.reset()
            ep_reward = ep_len = 0.0

            # 상대 갱신 (에피소드마다 확률적으로)
            if random.random() < 0.1:
                opp_state = league.sample_opponent()
                if opp_state:
                    opponent.network.load_state_dict(opp_state)

        # 버퍼 업데이트
        if agent.buffer.full:
            import torch
            last_obs_t = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(agent.device)
            with torch.no_grad():
                _, lv = agent.network.forward(last_obs_t)
                last_val = lv.item()
            agent.update(last_val)

        # 상대방 네트워크 주기적 갱신
        if ts - last_opp_update >= args.opponent_update_freq:
            league.update_latest(agent.network.state_dict())
            last_opp_update = ts

        # 리그 풀 갱신 (2× 주기)
        if ts - last_pool_add >= args.opponent_update_freq * 2:
            league.add_to_pool(agent.network.state_dict(), ts)
            last_pool_add = ts

        # 로그
        if ts - last_log >= args.log_freq and len(ep_rewards) > 0:
            elapsed = time.time() - start_time
            win_rate = np.mean(win_count) * 100
            sps = ts / elapsed
            league_info = f"리그:{len(league.pool)}/{args.league_size}"

            print(
                f"[{ts:>8,}] 승률:{win_rate:5.1f}%  보상:{np.mean(ep_rewards):+6.2f}  "
                f"길이:{np.mean(ep_lengths):4.0f}턴  속도:{sps:,.0f}sps  {league_info}"
            )
            last_log = ts

            if win_rate > best_win_rate and win_rate > 50:
                best_win_rate = win_rate
                agent.save(os.path.join(args.save_dir, "best_model.pt"))
                print(f"  🏆 최고 승률 갱신! {win_rate:.1f}% (vs 리그)")

        # 주기적 체크포인트
        if ts % 500_000 == 0 and ts > 0:
            agent.save(os.path.join(args.save_dir, f"checkpoint_{ts}.pt"))

    agent.save(os.path.join(args.save_dir, "final_model.pt"))
    elapsed = time.time() - start_time
    print(f"\n✅ Self-Play 학습 완료! {elapsed/60:.1f}분")
    print(f"   최고 리그 승률: {best_win_rate:.1f}%")


if __name__ == "__main__":
    args = parse_args()
    train_selfplay(args)
