"""
network.py + ppo_agent.py — PPO 에이전트 (Actor-Critic)

포켓몬 배틀에 적합한 신경망 구조:
  - 듀얼 헤드 (정책 + 가치)
  - 배치 정규화
  - 마스킹 지원 (PP 없는 기술 / 살아있지 않은 포켓몬)
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torch.distributions import Categorical


# ══════════════════════════════════════════════
# 신경망 구조
# ══════════════════════════════════════════════

class PokemonBattleNet(nn.Module):
    """
    Actor-Critic 네트워크

    Input: 관측 벡터 (obs_dim,)
    Output:
      - policy_logits: (n_actions,)
      - value: (1,)
    """

    def __init__(self, obs_dim: int, n_actions: int, hidden_dim: int = 256):
        super().__init__()

        # 공유 특징 추출기 (Shared Backbone)
        self.shared = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
        )

        # 정책 헤드 (Actor)
        self.policy_head = nn.Sequential(
            nn.Linear(hidden_dim // 2, hidden_dim // 4),
            nn.ReLU(),
            nn.Linear(hidden_dim // 4, n_actions),
        )

        # 가치 헤드 (Critic)
        self.value_head = nn.Sequential(
            nn.Linear(hidden_dim // 2, hidden_dim // 4),
            nn.ReLU(),
            nn.Linear(hidden_dim // 4, 1),
        )

        # 가중치 초기화
        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight, gain=np.sqrt(2))
                nn.init.zeros_(m.bias)
        # 마지막 정책 레이어는 작게
        nn.init.orthogonal_(self.policy_head[-1].weight, gain=0.01)

    def forward(self, obs: torch.Tensor, action_mask: torch.Tensor | None = None):
        """
        obs: (batch, obs_dim)
        action_mask: (batch, n_actions) — True이면 해당 행동 불가
        """
        features = self.shared(obs)
        logits = self.policy_head(features)
        value = self.value_head(features)

        if action_mask is not None:
            logits = logits.masked_fill(action_mask, float("-inf"))

        return logits, value.squeeze(-1)

    def get_action(self, obs: torch.Tensor, action_mask: torch.Tensor | None = None):
        """추론 시 사용 — 행동 + 로그확률 + 가치 반환"""
        logits, value = self.forward(obs, action_mask)
        dist = Categorical(logits=logits)
        action = dist.sample()
        log_prob = dist.log_prob(action)
        return action, log_prob, value

    def evaluate_actions(self, obs: torch.Tensor, actions: torch.Tensor,
                         action_mask: torch.Tensor | None = None):
        """학습 시 사용 — 엔트로피 + 로그확률 + 가치 반환"""
        logits, value = self.forward(obs, action_mask)
        dist = Categorical(logits=logits)
        log_probs = dist.log_prob(actions)
        entropy = dist.entropy()
        return log_probs, entropy, value


# ══════════════════════════════════════════════
# PPO 에이전트
# ══════════════════════════════════════════════

class RolloutBuffer:
    """PPO 롤아웃 버퍼"""

    def __init__(self, buffer_size: int, obs_dim: int, n_actions: int, device: str = "cpu"):
        self.buffer_size = buffer_size
        self.device = device
        self.obs_dim = obs_dim
        self.n_actions = n_actions
        self.reset()

    def reset(self):
        self.obs = np.zeros((self.buffer_size, self.obs_dim), dtype=np.float32)
        self.actions = np.zeros(self.buffer_size, dtype=np.int64)
        self.log_probs = np.zeros(self.buffer_size, dtype=np.float32)
        self.rewards = np.zeros(self.buffer_size, dtype=np.float32)
        self.values = np.zeros(self.buffer_size, dtype=np.float32)
        self.dones = np.zeros(self.buffer_size, dtype=np.float32)
        self.ptr = 0
        self.full = False

    def add(self, obs, action, log_prob, reward, value, done):
        self.obs[self.ptr] = obs
        self.actions[self.ptr] = action
        self.log_probs[self.ptr] = log_prob
        self.rewards[self.ptr] = reward
        self.values[self.ptr] = value
        self.dones[self.ptr] = done
        self.ptr += 1
        if self.ptr >= self.buffer_size:
            self.full = True
            self.ptr = 0

    def compute_returns_and_advantages(self, last_value: float, gamma: float = 0.99,
                                       gae_lambda: float = 0.95):
        """GAE (Generalized Advantage Estimation)"""
        size = self.buffer_size
        advantages = np.zeros(size, dtype=np.float32)
        last_gae = 0.0

        for t in reversed(range(size)):
            next_done = self.dones[t]
            next_value = self.values[(t + 1) % size] if t < size - 1 else last_value
            if next_done:
                next_value = 0.0
            delta = self.rewards[t] + gamma * next_value * (1 - next_done) - self.values[t]
            last_gae = delta + gamma * gae_lambda * (1 - next_done) * last_gae
            advantages[t] = last_gae

        returns = advantages + self.values
        return (
            torch.tensor(self.obs, dtype=torch.float32).to(self.device),
            torch.tensor(self.actions, dtype=torch.long).to(self.device),
            torch.tensor(self.log_probs, dtype=torch.float32).to(self.device),
            torch.tensor(returns, dtype=torch.float32).to(self.device),
            torch.tensor(advantages, dtype=torch.float32).to(self.device),
        )


class PPOAgent:
    """
    PPO (Proximal Policy Optimization) 에이전트

    하이퍼파라미터 (포켓몬 배틀 최적화):
      - clip_eps: 0.2
      - gamma: 0.99
      - gae_lambda: 0.95
      - entropy_coef: 0.01
      - value_coef: 0.5
    """

    def __init__(
        self,
        obs_dim: int,
        n_actions: int,
        lr: float = 3e-4,
        hidden_dim: int = 256,
        clip_eps: float = 0.2,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        entropy_coef: float = 0.01,
        value_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        n_epochs: int = 10,
        batch_size: int = 64,
        buffer_size: int = 2048,
        device: str = "auto",
    ):
        if device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        print(f"🖥️  디바이스: {self.device}")

        self.network = PokemonBattleNet(obs_dim, n_actions, hidden_dim).to(self.device)
        self.optimizer = torch.optim.Adam(self.network.parameters(), lr=lr, eps=1e-5)

        self.clip_eps = clip_eps
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.entropy_coef = entropy_coef
        self.value_coef = value_coef
        self.max_grad_norm = max_grad_norm
        self.n_epochs = n_epochs
        self.batch_size = batch_size

        self.buffer = RolloutBuffer(buffer_size, obs_dim, n_actions, self.device)
        self.total_timesteps = 0
        self.n_updates = 0

    def select_action(self, obs: np.ndarray, action_mask: np.ndarray | None = None):
        """행동 선택 (학습 중)"""
        obs_tensor = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
        mask_tensor = None
        if action_mask is not None:
            mask_tensor = torch.tensor(action_mask, dtype=torch.bool).unsqueeze(0).to(self.device)

        with torch.no_grad():
            action, log_prob, value = self.network.get_action(obs_tensor, mask_tensor)

        return action.item(), log_prob.item(), value.item()

    def predict(self, obs: np.ndarray, action_mask: np.ndarray | None = None) -> int:
        """행동 선택 (추론 전용 — 그리디)"""
        obs_tensor = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
        mask_tensor = None
        if action_mask is not None:
            mask_tensor = torch.tensor(action_mask, dtype=torch.bool).unsqueeze(0).to(self.device)

        with torch.no_grad():
            logits, _ = self.network.forward(obs_tensor, mask_tensor)
            action = logits.argmax(dim=-1)

        return action.item()

    def store_transition(self, obs, action, log_prob, reward, value, done):
        self.buffer.add(obs, action, log_prob, reward, value, done)
        self.total_timesteps += 1

    def update(self, last_value: float = 0.0) -> dict:
        """PPO 업데이트"""
        obs_b, act_b, old_lp_b, ret_b, adv_b = self.buffer.compute_returns_and_advantages(
            last_value, self.gamma, self.gae_lambda
        )

        # 어드밴티지 정규화
        adv_b = (adv_b - adv_b.mean()) / (adv_b.std() + 1e-8)

        metrics = {"policy_loss": 0, "value_loss": 0, "entropy": 0, "approx_kl": 0}
        size = len(obs_b)

        for _ in range(self.n_epochs):
            indices = torch.randperm(size)

            for start in range(0, size, self.batch_size):
                idx = indices[start:start + self.batch_size]

                log_probs, entropy, values = self.network.evaluate_actions(
                    obs_b[idx], act_b[idx]
                )

                # PPO 클립 손실
                ratio = torch.exp(log_probs - old_lp_b[idx])
                surr1 = ratio * adv_b[idx]
                surr2 = torch.clamp(ratio, 1 - self.clip_eps, 1 + self.clip_eps) * adv_b[idx]
                policy_loss = -torch.min(surr1, surr2).mean()

                # 가치 손실 (클립)
                value_loss = F.mse_loss(values, ret_b[idx])

                # 전체 손실
                loss = policy_loss + self.value_coef * value_loss - self.entropy_coef * entropy.mean()

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.network.parameters(), self.max_grad_norm)
                self.optimizer.step()

                # 메트릭 누적
                with torch.no_grad():
                    approx_kl = ((ratio - 1) - (log_probs - old_lp_b[idx])).mean().item()
                metrics["policy_loss"] += policy_loss.item()
                metrics["value_loss"] += value_loss.item()
                metrics["entropy"] += entropy.mean().item()
                metrics["approx_kl"] += approx_kl

        self.buffer.reset()
        self.n_updates += 1

        n_batches = (self.n_epochs * size) // self.batch_size + 1
        for k in metrics:
            metrics[k] /= n_batches

        return metrics

    def save(self, path: str):
        torch.save({
            "network_state": self.network.state_dict(),
            "optimizer_state": self.optimizer.state_dict(),
            "total_timesteps": self.total_timesteps,
            "n_updates": self.n_updates,
        }, path)
        print(f"💾 모델 저장: {path}")

    def load(self, path: str):
        ckpt = torch.load(path, map_location=self.device)
        self.network.load_state_dict(ckpt["network_state"])
        self.optimizer.load_state_dict(ckpt["optimizer_state"])
        self.total_timesteps = ckpt.get("total_timesteps", 0)
        self.n_updates = ckpt.get("n_updates", 0)
        print(f"📂 모델 로드: {path} (스텝: {self.total_timesteps:,})")
