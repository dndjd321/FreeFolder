"""
train.py — PPO 학습 메인 스크립트

Self-play 방식:
  - AI가 자기 자신(이전 버전)과 배틀하며 학습
  - 주기적으로 최선 모델 저장
  - W&B 또는 TensorBoard 로깅 지원
"""
import argparse
import os
import time
import numpy as np
from collections import deque

from env.battle_env import PokemonBattleEnv
from agents.ppo_agent import PPOAgent


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--timesteps", type=int, default=2_000_000, help="총 학습 스텝")
    p.add_argument("--team-size", type=int, default=3, help="팀 크기 (기본 3)")
    p.add_argument("--buffer-size", type=int, default=4096, help="롤아웃 버퍼 크기")
    p.add_argument("--lr", type=float, default=3e-4, help="학습률")
    p.add_argument("--hidden-dim", type=int, default=256, help="신경망 히든 크기")
    p.add_argument("--n-epochs", type=int, default=10, help="PPO 에포크")
    p.add_argument("--batch-size", type=int, default=128, help="미니배치 크기")
    p.add_argument("--save-dir", type=str, default="checkpoints", help="모델 저장 경로")
    p.add_argument("--save-freq", type=int, default=100_000, help="저장 주기 (스텝)")
    p.add_argument("--log-freq", type=int, default=10_000, help="로그 출력 주기")
    p.add_argument("--resume", type=str, default=None, help="이어서 학습할 체크포인트 경로")
    return p.parse_args()


def make_action_mask(env: PokemonBattleEnv) -> np.ndarray:
    """
    유효하지 않은 행동 마스킹
    - PP 0인 기술
    - 이미 쓰러진 포켓몬으로 교체
    """
    n_actions = env.action_space.n
    mask = np.zeros(n_actions, dtype=bool)

    # 기술 PP 체크
    for i, move in enumerate(env.player_active.moves[:4]):
        if move.pp <= 0:
            mask[i] = True

    # 교체 대상 체크
    switch_options = [i for i in range(env.team_size)
                      if i != env.player_active_idx and not env.player_team[i].is_fainted]
    for slot in range(env.team_size - 1):
        if slot >= len(switch_options):
            mask[4 + slot] = True

    # 모든 행동이 마스킹되면 해제 (발버둥)
    if mask.all():
        mask[:] = False

    return mask


def train(args):
    os.makedirs(args.save_dir, exist_ok=True)

    env = PokemonBattleEnv(team_size=args.team_size, max_turns=100)
    obs_dim = env.observation_space.shape[0]
    n_actions = env.action_space.n

    agent = PPOAgent(
        obs_dim=obs_dim,
        n_actions=n_actions,
        lr=args.lr,
        hidden_dim=args.hidden_dim,
        n_epochs=args.n_epochs,
        batch_size=args.batch_size,
        buffer_size=args.buffer_size,
    )

    if args.resume:
        agent.load(args.resume)

    print(f"\n🎮 포켓몬 배틀 강화학습 시작!")
    print(f"   관측 차원: {obs_dim}  |  행동 수: {n_actions}")
    print(f"   총 학습 스텝: {args.timesteps:,}")
    print(f"   디바이스: {agent.device}\n")

    # 학습 통계
    ep_rewards = deque(maxlen=100)
    ep_lengths = deque(maxlen=100)
    win_count = deque(maxlen=100)  # 승리 여부
    best_win_rate = 0.0

    obs, _ = env.reset()
    ep_reward = 0.0
    ep_len = 0
    start_time = time.time()
    last_log = 0
    last_save = 0

    while agent.total_timesteps < args.timesteps:
        action_mask = make_action_mask(env)
        action, log_prob, value = agent.select_action(obs, action_mask)

        next_obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

        agent.store_transition(obs, action, log_prob, reward, value, float(done))

        ep_reward += reward
        ep_len += 1
        obs = next_obs

        # 에피소드 종료
        if done:
            ep_rewards.append(ep_reward)
            ep_lengths.append(ep_len)

            # 승리 판정 (상대 팀 전멸)
            opp_alive = sum(1 for p in env.opponent_team if not p.is_fainted)
            won = opp_alive == 0
            win_count.append(float(won))

            obs, _ = env.reset()
            ep_reward = 0.0
            ep_len = 0

        # 버퍼가 가득 차면 업데이트
        if agent.buffer.full:
            with np.errstate(divide='ignore', invalid='ignore'):
                last_obs_tensor = None
                last_value = 0.0
                import torch
                last_obs_t = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(agent.device)
                with torch.no_grad():
                    _, last_value_t = agent.network.forward(last_obs_t)
                    last_value = last_value_t.item()

            metrics = agent.update(last_value)

        # 로그 출력
        ts = agent.total_timesteps
        if ts - last_log >= args.log_freq and len(ep_rewards) > 0:
            elapsed = time.time() - start_time
            win_rate = np.mean(win_count) * 100 if win_count else 0
            sps = ts / elapsed

            print(
                f"[{ts:>8,} steps] "
                f"승률: {win_rate:5.1f}%  |  "
                f"평균보상: {np.mean(ep_rewards):+6.2f}  |  "
                f"평균길이: {np.mean(ep_lengths):4.0f}턴  |  "
                f"속도: {sps:,.0f} steps/s"
            )
            last_log = ts

            # 최고 승률 체크
            if win_rate > best_win_rate and win_rate > 50:
                best_win_rate = win_rate
                agent.save(os.path.join(args.save_dir, "best_model.pt"))
                print(f"  🏆 최고 승률 갱신! {win_rate:.1f}%")

        # 주기적 저장
        if ts - last_save >= args.save_freq:
            agent.save(os.path.join(args.save_dir, f"checkpoint_{ts}.pt"))
            last_save = ts

    # 최종 저장
    agent.save(os.path.join(args.save_dir, "final_model.pt"))
    elapsed = time.time() - start_time
    print(f"\n✅ 학습 완료! 총 시간: {elapsed/60:.1f}분")
    print(f"   최고 승률: {best_win_rate:.1f}%")
    return agent


if __name__ == "__main__":
    args = parse_args()
    train(args)
