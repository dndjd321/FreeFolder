"""
agents/ppo_agent.py — 버퍼 인덱스 에러 수정 및 PPO 로직 완결본
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torch.distributions import Categorical

class RolloutBuffer:
    def __init__(self, buffer_size, obs_dim):
        self.obs = np.zeros((buffer_size, obs_dim), dtype=np.float32)
        self.actions = np.zeros(buffer_size, dtype=np.int64)
        self.rewards = np.zeros(buffer_size, dtype=np.float32)
        self.values = np.zeros(buffer_size, dtype=np.float32)
        self.log_probs = np.zeros(buffer_size, dtype=np.float32)
        self.dones = np.zeros(buffer_size, dtype=np.float32)
        self.p_ids = np.zeros(buffer_size, dtype=np.int64)
        self.o_ids = np.zeros(buffer_size, dtype=np.int64)
        self.ptr = 0
        self.buffer_size = buffer_size

    def store(self, obs, action, reward, value, log_prob, done, p_id, o_id):
        # [수정] 인덱스 범위 초과 방지
        if self.ptr < self.buffer_size:
            self.obs[self.ptr] = obs
            self.actions[self.ptr] = action
            self.rewards[self.ptr] = reward
            self.values[self.ptr] = value
            self.log_probs[self.ptr] = log_prob
            self.dones[self.ptr] = done
            self.p_ids[self.ptr] = p_id
            self.o_ids[self.ptr] = o_id
            self.ptr += 1

    def reset(self):
        self.ptr = 0

class PokemonBattleNet(nn.Module):
    def __init__(self, obs_dim, n_actions, hidden_dim=512):
        super().__init__()
        self.poke_emb = nn.Embedding(1200, 32)
        combined_dim = obs_dim + 64 # 내 포켓몬(32) + 상대(32)
        self.fc = nn.Sequential(
            nn.Linear(combined_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU()
        )
        self.pi = nn.Linear(hidden_dim, n_actions)
        self.v = nn.Linear(hidden_dim, 1)

    def forward(self, obs, p_id, o_id):
        p_v = self.poke_emb(p_id).view(obs.size(0), -1)
        o_v = self.poke_emb(o_id).view(obs.size(0), -1)
        x = torch.cat([obs, p_v, o_v], dim=-1)
        h = self.fc(x)
        return self.pi(h), self.v(h)

class PPOAgent:
    def __init__(self, obs_dim, n_actions, lr=3e-4, buffer_size=4096):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.net = PokemonBattleNet(obs_dim, n_actions).to(self.device)
        self.optimizer = torch.optim.Adam(self.net.parameters(), lr=lr)
        self.buffer = RolloutBuffer(buffer_size, obs_dim)
        self.gamma = 0.99
        self.eps_clip = 0.2

    def get_action(self, obs, env_info):
        self.net.eval()
        with torch.no_grad():
            o = torch.FloatTensor(obs).unsqueeze(0).to(self.device)
            p = torch.LongTensor([env_info['p_id']]).to(self.device)
            r = torch.LongTensor([env_info['o_id']]).to(self.device)
            logits, val = self.net(o, p, r)
            dist = Categorical(F.softmax(logits, dim=-1))
            action = dist.sample()
            return action.item(), dist.log_prob(action).item(), val.item()

    def update(self):
        self.net.train()
        # 버퍼 데이터 추출 및 텐서 변환
        obs = torch.FloatTensor(self.buffer.obs).to(self.device)
        p_ids = torch.LongTensor(self.buffer.p_ids).to(self.device)
        o_ids = torch.LongTensor(self.buffer.o_ids).to(self.device)
        actions = torch.LongTensor(self.buffer.actions).to(self.device)
        old_lps = torch.FloatTensor(self.buffer.log_probs).to(self.device)
        
        # 리턴 계산
        returns = []
        discounted_reward = 0
        for reward, done in zip(reversed(self.buffer.rewards), reversed(self.buffer.dones)):
            if done: discounted_reward = 0
            discounted_reward = reward + (self.gamma * discounted_reward)
            returns.insert(0, discounted_reward)
        returns = torch.FloatTensor(returns).to(self.device)
        
        # 가치 함수 및 어드밴티지
        advantages = returns - torch.FloatTensor(self.buffer.values).to(self.device)
        
        # PPO 업데이트 (간략화된 버전)
        logits, values = self.net(obs, p_ids, o_ids)
        dist = Categorical(F.softmax(logits, dim=-1))
        new_lps = dist.log_prob(actions)
        
        ratio = torch.exp(new_lps - old_lps)
        surr1 = ratio * advantages
        surr2 = torch.clamp(ratio, 1-self.eps_clip, 1+self.eps_clip) * advantages
        
        loss = -torch.min(surr1, surr2).mean() + 0.5 * F.mse_loss(values.squeeze(), returns)
        
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        
        self.buffer.reset() # [중요] 업데이트 후 버퍼 비우기
        return loss.item()

    def save(self, path):
        torch.save(self.net.state_dict(), path)