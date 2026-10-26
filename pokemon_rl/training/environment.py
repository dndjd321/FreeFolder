"""Gymnasium wrapper over the exact same Node worker used by the game."""

from __future__ import annotations

import copy
import random

import gymnasium as gym
import numpy as np

from battle_service.client import BattleClient
from .observation import N_ACTIONS, OBS_DIM, action_to_command, encode_observation, legal_mask
from .policies import BaselinePolicy


TEAM_POOL = [
    {"species": "Charizard", "ability": "Blaze", "item": "Life Orb", "nature": "Timid", "moves": ["Flamethrower", "Air Slash", "Dragon Claw", "Protect"], "evs": {"spa": 252, "spe": 252, "def": 4}},
    {"species": "Garchomp", "ability": "Rough Skin", "item": "Leftovers", "nature": "Jolly", "moves": ["Earthquake", "Dragon Claw", "Swords Dance", "Protect"], "evs": {"atk": 252, "spe": 252, "spd": 4}},
    {"species": "Clefable", "ability": "Magic Guard", "item": "Leftovers", "nature": "Bold", "moves": ["Moonblast", "Flamethrower", "Soft-Boiled", "Calm Mind"], "evs": {"hp": 252, "def": 252, "spa": 4}},
    {"species": "Venusaur", "ability": "Overgrow", "item": "Leftovers", "nature": "Modest", "moves": ["Giga Drain", "Sludge Bomb", "Sleep Powder", "Synthesis"], "evs": {"hp": 252, "spa": 252, "spd": 4}},
    {"species": "Blastoise", "ability": "Torrent", "item": "Sitrus Berry", "nature": "Modest", "moves": ["Surf", "Ice Beam", "Dark Pulse", "Protect"], "evs": {"hp": 252, "spa": 252, "spd": 4}},
    {"species": "Arcanine", "ability": "Intimidate", "item": "Heavy-Duty Boots", "nature": "Adamant", "moves": ["Flare Blitz", "Extreme Speed", "Will-O-Wisp", "Morning Sun"], "evs": {"atk": 252, "spe": 252, "spd": 4}},
]


class ShowdownBattleEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, opponent=None, *, client=None, max_turns=100, max_steps=200):
        self.client = client or BattleClient()
        self.owns_client = client is None
        self.opponent = opponent or BaselinePolicy("heuristic")
        self.max_turns, self.max_steps = max_turns, max_steps
        self.observation_space = gym.spaces.Box(-1, 1, shape=(OBS_DIM,), dtype=np.float32)
        self.action_space = gym.spaces.Discrete(N_ACTIONS)
        self.state, self.battle_id, self.steps, self.invalid_actions = None, None, 0, 0

    def _view(self):
        return self.state["views"]["p1"]

    def action_masks(self):
        return legal_mask(self._view()["request"], self._view())

    def _info(self):
        return {"action_mask": self.action_masks(), "view": self._view(), "winner": self.state["winner"],
                "turn": self.state["turn"], "invalid_actions": self.invalid_actions}

    def _opponent_steps(self):
        for _ in range(20):
            if self.state["ended"]:
                return
            view = self.state["views"]["p2"]
            if view["request"].get("wait"):
                return
            action = self.opponent.predict(view["request"], view)
            if action is None:
                raise ValueError("Opponent policy returned no action for an active request")
            self.state = self.client.choose(self.battle_id, "p2", action)
            if not self.state["requests"]["p1"].get("wait"):
                return
        raise RuntimeError("Opponent request loop did not settle")

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if self.battle_id:
            self.client.close_battle(self.battle_id)
        rng = random.Random(seed)
        options = options or {}
        teams = options.get("teams") or {"p1": copy.deepcopy(rng.sample(TEAM_POOL, 3)), "p2": copy.deepcopy(rng.sample(TEAM_POOL, 3))}
        self.state = self.client.create(teams, seed=[rng.randrange(65536) for _ in range(4)])
        self.battle_id = self.state["battle_id"]
        self.steps, self.invalid_actions = 0, 0
        self._opponent_steps()
        return encode_observation(self._view()), self._info()

    @staticmethod
    def _health(view):
        own = sum(p["hp"] / max(p["maxhp"], 1) for p in view["own"])
        opponent = sum((p.get("hp", 0) or 0) / max(p.get("maxhp", 1) or 1, 1) if p.get("revealed") else 1 for p in view["opponent"][:3])
        return own, opponent

    def step(self, action):
        if self.state is None or self.state["ended"]:
            raise RuntimeError("Reset the environment before stepping")
        if not self.action_space.contains(action) or not self.action_masks()[int(action)]:
            self.invalid_actions += 1
            raise ValueError("Illegal action masked by the engine")
        before = self._health(self._view())
        self.state = self.client.choose(self.battle_id, "p1", action_to_command(action))
        self._opponent_steps()
        self.steps += 1
        after = self._health(self._view())
        reward = 0.15 * ((before[1] - after[1]) - (before[0] - after[0]))
        terminated = self.state["ended"]
        truncated = not terminated and (self.state["turn"] >= self.max_turns or self.steps >= self.max_steps)
        if terminated:
            reward += 1 if self.state["winner"] == "p1" else -1 if self.state["winner"] == "p2" else 0
        return encode_observation(self._view()), float(reward), terminated, truncated, self._info()

    def close(self):
        if self.owns_client:
            self.client.close()
        elif self.battle_id:
            self.client.close_battle(self.battle_id)
        self.battle_id = None
