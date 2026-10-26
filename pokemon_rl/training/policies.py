"""Small reproducible baselines that use only their permitted side view."""

import random

import numpy as np

from .observation import action_to_command, legal_mask

SUPER = {
    "normal": (), "fire": ("grass", "ice", "bug", "steel"), "water": ("fire", "ground", "rock"),
    "electric": ("water", "flying"), "grass": ("water", "ground", "rock"), "ice": ("grass", "ground", "flying", "dragon"),
    "fighting": ("normal", "ice", "rock", "dark", "steel"), "poison": ("grass", "fairy"), "ground": ("fire", "electric", "poison", "rock", "steel"),
    "flying": ("grass", "fighting", "bug"), "psychic": ("fighting", "poison"), "bug": ("grass", "psychic", "dark"),
    "rock": ("fire", "ice", "flying", "bug"), "ghost": ("psychic", "ghost"), "dragon": ("dragon",), "dark": ("psychic", "ghost"),
    "steel": ("ice", "rock", "fairy"), "fairy": ("fighting", "dragon", "dark"),
}
RESIST = {
    "normal": ("rock", "steel"), "fire": ("fire", "water", "rock", "dragon"), "water": ("water", "grass", "dragon"),
    "electric": ("electric", "grass", "dragon"), "grass": ("fire", "grass", "poison", "flying", "bug", "dragon", "steel"),
    "ice": ("fire", "water", "ice", "steel"), "fighting": ("poison", "flying", "psychic", "bug", "fairy"),
    "poison": ("poison", "ground", "rock", "ghost"), "ground": ("grass", "bug"), "flying": ("electric", "rock", "steel"),
    "psychic": ("psychic", "steel"), "bug": ("fire", "fighting", "poison", "flying", "ghost", "steel", "fairy"),
    "rock": ("fighting", "ground", "steel"), "ghost": ("dark",), "dragon": ("steel",), "dark": ("fighting", "dark", "fairy"),
    "steel": ("fire", "water", "electric", "steel"), "fairy": ("fire", "poison", "steel"),
}
IMMUNE = {"normal": ("ghost",), "electric": ("ground",), "fighting": ("ghost",), "poison": ("steel",),
          "ground": ("flying",), "psychic": ("dark",), "ghost": ("normal",), "dragon": ("fairy",)}


def effectiveness(move_type, target_types):
    result = 1.0
    for target in target_types or []:
        if target in IMMUNE.get(move_type, ()):
            return 0.0
        if target in SUPER.get(move_type, ()):
            result *= 2
        if target in RESIST.get(move_type, ()):
            result *= 0.5
    return result


class BaselinePolicy:
    def __init__(self, name="heuristic", seed=0):
        if name not in ("random", "maxpower", "heuristic"):
            raise ValueError("Unknown baseline policy")
        self.name, self.rng, self.calls = name, random.Random(seed), 0

    def predict(self, request, view):
        self.calls += 1
        mask = legal_mask(request, view)
        legal = np.flatnonzero(mask).tolist()
        if not legal:
            return None
        if self.name == "random":
            return action_to_command(self.rng.choice(legal))
        moves = (request.get("active") or [{}])[0].get("moves", [])
        own = next((p for p in view.get("own", []) if p.get("active")), {})
        opponent = next((p for p in view.get("opponent", []) if p.get("active")), {})
        scores = []
        for action in legal:
            if action >= 4:
                # A forced replacement uses remaining HP; voluntary switches
                # are only attractive when all known attacks are ineffective.
                p = next(p for p in view["own"] if p["slot"] == action - 4)
                score = 1 + p["hp"] / p["maxhp"] if any(request.get("forceSwitch", [])) else -5
            else:
                move = moves[action]
                accuracy = 1 if move.get("accuracy") is True else (move.get("accuracy", 100) or 100) / 100
                score = (move.get("basePower", 0) or 0) * accuracy
                if self.name == "heuristic":
                    score *= effectiveness(move.get("type"), opponent.get("types"))
                    if move.get("type") in own.get("types", []):
                        score *= 1.5
                    if move.get("category") == "physical" and own.get("status") == "brn":
                        score *= 0.5
                    if move.get("id") in ("recover", "roost", "softboiled", "synthesis", "morningsun", "slackoff") and own.get("hp", 1) / own.get("maxhp", 1) < 0.35:
                        score = 120
            scores.append((score, -action, action))
        return action_to_command(max(scores)[2])
