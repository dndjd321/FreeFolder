"""Masked PPO whose saved schemas must match the actual battle adapter."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from .observation import ACTION_VERSION, N_ACTIONS, OBS_DIM, OBSERVATION_VERSION, action_to_command, encode_observation, legal_mask

RULESET_VERSION = "freefolder-singles-v1"
ENGINE_VERSION = "0.11.11"


def model_metadata():
    return {"format_version": 1, "observation_version": OBSERVATION_VERSION,
            "action_version": ACTION_VERSION, "obs_dim": OBS_DIM, "n_actions": N_ACTIONS,
            "engine": "pokemon-showdown", "engine_version": ENGINE_VERSION,
            "ruleset": RULESET_VERSION, "hidden": [128, 64]}


class ActorCritic(nn.Module):
    def __init__(self):
        super().__init__()
        self.shared = nn.Sequential(nn.Linear(OBS_DIM, 128), nn.Tanh(), nn.Linear(128, 64), nn.Tanh())
        self.actor, self.critic = nn.Linear(64, N_ACTIONS), nn.Linear(64, 1)

    def forward(self, observations):
        hidden = self.shared(observations)
        return self.actor(hidden), self.critic(hidden).squeeze(-1)

    def distribution(self, observations, masks):
        logits, values = self(observations)
        if masks.shape != logits.shape or not torch.all(masks.any(dim=-1)):
            raise ValueError("Each policy row must have at least one legal action")
        return Categorical(logits=logits.masked_fill(~masks.bool(), torch.finfo(logits.dtype).min)), values

    def evaluate(self, observations, actions, masks):
        distribution, values = self.distribution(observations, masks)
        return distribution.log_prob(actions), distribution.entropy(), values


class PolicyAgent:
    def __init__(self, *, seed=0, learning_rate=3e-4):
        torch.manual_seed(seed)
        self.network = ActorCritic()
        self.optimizer = torch.optim.Adam(self.network.parameters(), lr=learning_rate)
        self.timesteps = 0
        self.training_seed_ranges = []

    def select(self, observation, mask, *, deterministic=False):
        with torch.no_grad():
            distribution, value = self.network.distribution(
                torch.as_tensor(observation, dtype=torch.float32).unsqueeze(0),
                torch.as_tensor(mask, dtype=torch.bool).unsqueeze(0))
            action = distribution.logits.argmax(dim=-1) if deterministic else distribution.sample()
            return int(action.item()), float(distribution.log_prob(action).item()), float(value.item())

    def predict(self, request, view):
        # The caller may pass a more recent request than the cached view.
        view = {**view, "request": request}
        mask = legal_mask(request, view)
        if not mask.any():
            return None
        action, _, _ = self.select(encode_observation(view), mask, deterministic=True)
        return action_to_command(action)

    def update(self, transitions, *, bootstrap=0.0, gamma=0.99, gae_lambda=0.95, epochs=4, batch_size=64, clip=0.2):
        """Each transition retains the exact legal mask used during collection."""
        observations = torch.as_tensor(np.asarray([row["observation"] for row in transitions]), dtype=torch.float32)
        masks = torch.as_tensor(np.asarray([row["mask"] for row in transitions]), dtype=torch.bool)
        actions = torch.as_tensor([row["action"] for row in transitions], dtype=torch.long)
        old_log_probs = torch.as_tensor([row["log_prob"] for row in transitions], dtype=torch.float32)
        advantages, returns = [], []
        gae, next_value = 0.0, float(bootstrap)
        for row in reversed(transitions):
            continuation = 0.0 if row["done"] else 1.0
            delta = row["reward"] + gamma * next_value * continuation - row["value"]
            gae = delta + gamma * gae_lambda * continuation * gae
            advantages.append(gae)
            returns.append(gae + row["value"])
            next_value = row["value"]
        advantages = torch.as_tensor(advantages[::-1], dtype=torch.float32)
        returns = torch.as_tensor(returns[::-1], dtype=torch.float32)
        if len(transitions) > 1:
            advantages = (advantages - advantages.mean()) / (advantages.std(unbiased=False) + 1e-8)
        losses = []
        for _ in range(epochs):
            indices = torch.randperm(len(transitions))
            for start in range(0, len(transitions), batch_size):
                index = indices[start:start + batch_size]
                log_probs, entropy, values = self.network.evaluate(observations[index], actions[index], masks[index])
                ratio = torch.exp(log_probs - old_log_probs[index])
                policy_loss = -torch.minimum(ratio * advantages[index], torch.clamp(ratio, 1 - clip, 1 + clip) * advantages[index]).mean()
                value_loss = 0.5 * (values - returns[index]).square().mean()
                loss = policy_loss + 0.5 * value_loss - 0.01 * entropy.mean()
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.network.parameters(), 0.5)
                self.optimizer.step()
                losses.append(float(loss.item()))
        self.timesteps += len(transitions)
        return {"loss": float(np.mean(losses)), "transitions": len(transitions)}

    def save(self, path, *, training=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        torch.save({"metadata": model_metadata(), "state_dict": self.network.state_dict(),
                    "timesteps": self.timesteps, "training": training or {},
                    "training_seed_ranges": self.training_seed_ranges}, temporary)
        temporary.replace(path)

    @classmethod
    def load(cls, path):
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        if not isinstance(checkpoint, dict) or checkpoint.get("metadata") != model_metadata():
            raise ValueError("Model observation/action/engine/ruleset metadata is incompatible")
        agent = cls()
        agent.network.load_state_dict(checkpoint["state_dict"], strict=True)
        agent.timesteps = int(checkpoint.get("timesteps", 0))
        agent.training_seed_ranges = checkpoint.get("training_seed_ranges", [])
        training = checkpoint.get("training", {})
        if not agent.training_seed_ranges and "seed_start" in training and "seed_end" in training:
            agent.training_seed_ranges = [[training["seed_start"], training["seed_end"]]]
        agent.network.eval()
        return agent
