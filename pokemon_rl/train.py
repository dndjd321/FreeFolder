"""
train.py — League Self-play + Player 배틀 학습 통합 버전

학습 방식:
  1. League Self-play  — AI vs 과거 AI 버전들 (자동 강화)
  2. vs Player         — 플레이어와의 실전 데이터로 추가 학습

사용법:
    python train.py                        # 기본 200만 스텝
    python train.py --timesteps 5000000    # 500만 스텝
    python train.py --resume checkpoints/final_model.pt
"""
import argparse, copy, json, os, random, time
from collections import deque
from pathlib import Path

import numpy as np
import torch

from env.battle_env import PokemonBattleEnv
from env.pokemon import Pokemon, Move
from agents.ppo_agent import PPOAgent


TYPE_VALID = {
    "normal","fire","water","electric","grass","ice","fighting","poison",
    "ground","flying","psychic","bug","rock","ghost","dragon","dark","steel","fairy",
}

# ══════════════════════════════════════════════════════════
# JSON 포켓몬 풀 로더
# ══════════════════════════════════════════════════════════
def load_pokemon_pool(poke_path, moves_path, max_poke=300):
    if not os.path.exists(poke_path):
        return []
    with open(poke_path, encoding="utf-8") as f:
        poke_raw = json.load(f)
    moves_db = {}
    if os.path.exists(moves_path):
        with open(moves_path, encoding="utf-8") as f:
            moves_db = json.load(f)

    EFF_MAP = {
        "burn":"burn","paralysis":"paralyze","sleep":"sleep",
        "freeze":"freeze","poison":"poison","bad-poison":"toxic",
        "confuse":"confuse","flinch":"flinch",
    }

    pool = []
    for name_en, p in list(poke_raw.items())[:max_poke]:
        stats = p.get("stats", {})
        if not all(k in stats for k in ["hp","attack","defense","sp_attack","sp_defense","speed"]):
            continue
        types = [t for t in p.get("types", []) if t in TYPE_VALID]
        if not types:
            continue

        moves = []
        for mname in p.get("moves", []):
            if mname not in moves_db:
                continue
            mv = moves_db[mname]
            if mv["power"] == 0 and not mv.get("effect"):
                continue
            eff = mv.get("effect", "")
            mapped_eff = EFF_MAP.get(eff, eff)
            target = "self" if eff.startswith("boost_") or eff == "heal_half" else "opponent"
            moves.append(Move(
                name=mv.get("name_ko") or mname,
                type_=mv["type"], category=mv["category"],
                power=mv["power"], accuracy=mv["accuracy"] or 100,
                pp=mv["pp"], effect=mapped_eff,
                effect_chance=mv.get("effect_chance", 0), target=target,
            ))
        if len(moves) < 2:
            continue

        abilities = p.get("abilities", [])
        ability = abilities[0].get("name","none") if abilities else "none"

        poke = Pokemon(
            name=p.get("name_ko") or name_en, types=types, level=50,
            base_hp=stats["hp"], base_attack=stats["attack"],
            base_defense=stats["defense"], base_sp_attack=stats["sp_attack"],
            base_sp_defense=stats["sp_defense"], base_speed=stats["speed"],
            moves=moves[:4], ability=ability,
        )
        pool.append(poke)
    return pool


# ══════════════════════════════════════════════════════════
# League Pool (과거 모델 스냅샷)
# ══════════════════════════════════════════════════════════
class LeagueAgent:
    def __init__(self, state_dict, obs_dim, n_actions, hidden_dim, device):
        self.agent = PPOAgent(obs_dim, n_actions, hidden_dim=hidden_dim)
        self.agent.network.load_state_dict(state_dict)
        self.agent.network.eval()
        self.device = device

    def act(self, obs, mask=None):
        with torch.no_grad():
            obs_t = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            mask_t = torch.tensor(mask, dtype=torch.bool).unsqueeze(0).to(self.device) if mask is not None else None
            logits, _ = self.agent.network.forward(obs_t, mask_t)
            return torch.distributions.Categorical(logits=logits).sample().item()


class LeaguePool:
    def __init__(self, max_size=10):
        self.snapshots = []
        self.max_size = max_size

    def add(self, state_dict, obs_dim, n_actions, hidden_dim, device):
        self.snapshots.append(LeagueAgent(copy.deepcopy(state_dict), obs_dim, n_actions, hidden_dim, device))
        if len(self.snapshots) > self.max_size:
            self.snapshots.pop(0)

    def sample(self):
        return random.choice(self.snapshots) if self.snapshots else None

    def __len__(self):
        return len(self.snapshots)


# ══════════════════════════════════════════════════════════
# 액션 마스크
# ══════════════════════════════════════════════════════════
def make_mask(env, team, active_idx):
    mask = np.zeros(env.action_space.n, dtype=bool)
    for i, mv in enumerate(team[active_idx].moves[:4]):
        if mv.pp <= 0:
            mask[i] = True
    sw = [i for i in range(env.team_size) if i != active_idx and not team[i].is_fainted]
    for slot in range(env.team_size - 1):
        if slot >= len(sw):
            mask[4 + slot] = True
    if mask.all():
        mask[:] = False
    return mask


# ══════════════════════════════════════════════════════════
# 환경 상대 행동 오버라이드
# ══════════════════════════════════════════════════════════
def patch_env_opponent(env, league_agent, curriculum):
    """_opponent_policy를 monkey-patch해서 리그 에이전트 or 룰기반 선택"""
    original_policy = env.__class__._opponent_policy

    def new_opp_policy(self):
        if league_agent and random.random() < curriculum:
            obs = self._get_obs()
            mask = make_mask(self, self.opponent_team, self.opponent_active_idx)
            return league_agent.act(obs, mask)
        return original_policy(self)

    import types
    env._opponent_policy = types.MethodType(new_opp_policy, env)


# ══════════════════════════════════════════════════════════
# 플레이어 배틀 데이터 학습
# ══════════════════════════════════════════════════════════
def learn_from_player_battles(agent, save_dir, batch_size=64):
    """
    server_core.py가 저장한 플레이어 배틀 리플레이로 추가 학습
    파일: data/player_battles.jsonl
    """
    replay_path = os.path.join("data", "player_battles.jsonl")
    if not os.path.exists(replay_path):
        return 0

    lines = open(replay_path, encoding="utf-8").readlines()
    if len(lines) < batch_size:
        return 0

    # 최근 N개 에피소드만 사용
    recent = lines[-500:]
    episodes = [json.loads(l) for l in recent if l.strip()]

    obs_list, act_list, ret_list = [], [], []
    for ep in episodes:
        transitions = ep.get("transitions", [])
        reward_total = ep.get("reward", 0)
        # 이긴 배틀은 가중치 높임
        weight = 1.5 if reward_total > 0 else 0.5
        for t in transitions:
            obs_list.append(t["obs"])
            act_list.append(t["action"])
            ret_list.append(t.get("reward", 0) * weight)

    if not obs_list:
        return 0

    obs_t   = torch.tensor(obs_list, dtype=torch.float32).to(agent.device)
    act_t   = torch.tensor(act_list, dtype=torch.long).to(agent.device)
    ret_t   = torch.tensor(ret_list, dtype=torch.float32).to(agent.device)

    # 간단한 지도학습 방식 (BC: Behavioral Cloning for wins)
    agent.network.train()
    optimizer = torch.optim.Adam(agent.network.parameters(), lr=1e-4)
    total_loss = 0.0
    n_batches = 0

    for start in range(0, len(obs_t), batch_size):
        end = min(start + batch_size, len(obs_t))
        o = obs_t[start:end]
        a = act_t[start:end]
        r = ret_t[start:end]

        logits, values = agent.network.forward(o)
        dist = torch.distributions.Categorical(logits=logits)
        log_prob = dist.log_prob(a)

        # 이긴 행동은 강화, 진 행동은 억제
        bc_loss = -(log_prob * r).mean()
        value_loss = ((values.squeeze() - r) ** 2).mean()
        loss = bc_loss + 0.5 * value_loss

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(agent.network.parameters(), 0.5)
        optimizer.step()
        total_loss += loss.item()
        n_batches += 1

    return len(episodes)


# ══════════════════════════════════════════════════════════
# 인자 파싱
# ══════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--timesteps",    type=int,   default=2_000_000)
    p.add_argument("--team-size",    type=int,   default=3)
    p.add_argument("--buffer-size",  type=int,   default=4096)
    p.add_argument("--lr",           type=float, default=3e-4)
    p.add_argument("--hidden-dim",   type=int,   default=256)
    p.add_argument("--n-epochs",     type=int,   default=10)
    p.add_argument("--batch-size",   type=int,   default=128)
    p.add_argument("--save-dir",     type=str,   default="checkpoints")
    p.add_argument("--save-freq",    type=int,   default=100_000)
    p.add_argument("--log-freq",     type=int,   default=10_000)
    p.add_argument("--league-size",  type=int,   default=8)
    p.add_argument("--league-freq",  type=int,   default=150_000)
    p.add_argument("--max-poke",     type=int,   default=300)
    p.add_argument("--player-learn-freq", type=int, default=200_000,
                   help="플레이어 배틀 데이터 학습 주기")
    p.add_argument("--resume",       type=str,   default=None)
    p.add_argument("--data-dir",     type=str,   default="data")
    return p.parse_args()


# ══════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════
def train(args):
    os.makedirs(args.save_dir, exist_ok=True)

    # 포켓몬 풀
    poke_path  = os.path.join(args.data_dir, "pokemon.json")
    moves_path = os.path.join(args.data_dir, "moves.json")
    json_pool = load_pokemon_pool(poke_path, moves_path, args.max_poke)

    env = PokemonBattleEnv(team_size=args.team_size, max_turns=100)
    if json_pool:
        env.pokemon_pool = json_pool
        print(f"✅ JSON 풀 로드: {len(json_pool)}마리")
    else:
        print(f"⚠️  내장 풀 사용: {len(env.pokemon_pool)}마리")

    obs_dim   = env.observation_space.shape[0]
    n_actions = env.action_space.n

    agent = PPOAgent(
        obs_dim=obs_dim, n_actions=n_actions,
        lr=args.lr, hidden_dim=args.hidden_dim,
        n_epochs=args.n_epochs, batch_size=args.batch_size,
        buffer_size=args.buffer_size,
    )
    if args.resume:
        agent.load(args.resume)
        # resume 시: 현재 스텝 + 추가 학습 스텝으로 목표 재설정
        args.timesteps = agent.total_timesteps + args.timesteps
        print(f"📂 재개: {args.resume}  (현재 {agent.total_timesteps:,}스텝 → 목표 {args.timesteps:,}스텝)")

    league = LeaguePool(max_size=args.league_size)
    league.add(copy.deepcopy(agent.network.state_dict()), obs_dim, n_actions, args.hidden_dim, agent.device)

    print(f"\n{'='*65}")
    print(f" 🎮 League Self-play 학습 시작")
    print(f"   obs:{obs_dim}  actions:{n_actions}  pool:{len(env.pokemon_pool)}마리")
    print(f"   총스텝:{args.timesteps:,}  리그크기:{args.league_size}  device:{agent.device}")
    print(f"{'='*65}\n")

    ep_rewards = deque(maxlen=200)
    ep_lengths = deque(maxlen=200)
    win_rate_q = deque(maxlen=200)
    best_win_rate = 0.0

    obs, _ = env.reset()
    ep_reward = 0.0
    ep_len    = 0
    start_time   = time.time()
    last_log     = 0
    last_save    = 0
    last_league  = 0
    last_pl      = 0
    cur_league_agent = None

    while agent.total_timesteps < args.timesteps:
        ts = agent.total_timesteps
        curriculum = min(1.0, ts / max(args.timesteps * 0.5, 1))

        # 리그 상대 업데이트
        if ts - last_league >= args.league_freq and ts > 0:
            league.add(copy.deepcopy(agent.network.state_dict()), obs_dim, n_actions, args.hidden_dim, agent.device)
            last_league = ts
            print(f"  🏟️  리그 스냅샷 추가 ({len(league)}개)")

        cur_league_agent = league.sample()
        patch_env_opponent(env, cur_league_agent, curriculum)

        action_mask = make_mask(env, env.player_team, env.player_active_idx)
        action, log_prob, value = agent.select_action(obs, action_mask)

        next_obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

        agent.store_transition(obs, action, log_prob, reward, value, float(done))
        ep_reward += reward
        ep_len    += 1
        obs = next_obs

        if done:
            ep_rewards.append(ep_reward)
            ep_lengths.append(ep_len)
            won = sum(1 for p in env.opponent_team if not p.is_fainted) == 0
            win_rate_q.append(float(won))
            obs, _ = env.reset()
            ep_reward = 0.0
            ep_len    = 0

        if agent.buffer.full:
            obs_t = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(agent.device)
            with torch.no_grad():
                _, lv = agent.network.forward(obs_t)
            agent.update(lv.item())

        # 플레이어 배틀 데이터 학습
        if ts - last_pl >= args.player_learn_freq and ts > 0:
            n = learn_from_player_battles(agent, args.save_dir)
            if n > 0:
                print(f"  👤 플레이어 배틀 데이터 {n}개 학습 완료")
            last_pl = ts

        # 로그
        if ts - last_log >= args.log_freq and len(ep_rewards) > 0:
            elapsed  = time.time() - start_time
            wr       = np.mean(win_rate_q) * 100
            sps      = ts / max(elapsed, 1)
            eta      = (args.timesteps - ts) / max(sps, 1)
            eta_str  = f"{eta/60:.0f}분" if eta < 3600 else f"{eta/3600:.1f}시간"
            bar      = "█" * int(wr/5) + "░" * (20 - int(wr/5))
            print(
                f"[{ts:>8,}] [{bar}] {wr:5.1f}%  "
                f"보상 {np.mean(ep_rewards):+5.2f}  "
                f"{np.mean(ep_lengths):3.0f}턴  "
                f"{sps:,.0f}s/s  "
                f"남은:{eta_str}  커리큘럼:{curriculum:.0%}"
            )
            last_log = ts

            if wr > best_win_rate and wr > 50:
                best_win_rate = wr
                agent.save(os.path.join(args.save_dir, "best_model.pt"))
                print(f"  🏆 최고 승률 {wr:.1f}% → best_model.pt")

        # 체크포인트
        if ts - last_save >= args.save_freq and ts > 0:
            agent.save(os.path.join(args.save_dir, f"ckpt_{ts:08d}.pt"))
            last_save = ts

    agent.save(os.path.join(args.save_dir, "final_model.pt"))
    elapsed = time.time() - start_time
    print(f"\n✅ 완료! {elapsed/60:.1f}분  최고승률: {best_win_rate:.1f}%")
    print(f"   → {args.save_dir}/final_model.pt")


if __name__ == "__main__":
    args = parse_args()
    train(args)
