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


POLICY_VERSION = "public-heuristic-v2"


def _attack_score(move, pokemon, opponent):
    accuracy = 1 if move.get("accuracy") is True else (move.get("accuracy", 100) or 100) / 100
    # These engine moves have a zero literal basePower and a runtime callback.
    # A neutral public-information prior keeps them distinct from status moves;
    # it is a choice score, not a replacement damage calculation.
    power = move.get("basePower", 0) or {"grassknot": 60, "gyroball": 60}.get(move.get("id"), 0)
    score = power * accuracy * effectiveness(move.get("type"), opponent.get("types"))
    if move.get("type") in pokemon.get("types", []):
        score *= 1.5
    physical = move.get("category") == "physical"
    stat = "atk" if physical else "spa"
    score *= max(pokemon.get("stats", {}).get(stat, 100), 1) / 100
    stage = pokemon.get("boosts", {}).get(stat, 0)
    score *= (2 + stage) / 2 if stage >= 0 else 2 / (2 - stage)
    if physical and pokemon.get("status") == "brn":
        score *= 0.5
    return score


def _health(pokemon):
    return max(0, pokemon.get("hp", 0)) / max(1, pokemon.get("maxhp", 1))


def _incoming_type_risk(pokemon, opponent):
    # Public enemy typing is a rough STAB threat prior, never its hidden moves.
    return max((effectiveness(t, pokemon.get("types")) for t in opponent.get("types", [])), default=1.0)


class BaselinePolicy:
    def __init__(self, name="heuristic", seed=0):
        if name not in ("random", "maxpower", "heuristic", "legacyheuristic"):
            raise ValueError("Unknown baseline policy")
        self.name, self.rng, self.calls = name, random.Random(seed), 0

    def predict(self, request, view):
        self.calls += 1
        if self.name == "heuristic":
            return self._heuristic(request, view)
        return self._legacy(request, view)

    def _legacy(self, request, view):
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
                if self.name == "legacyheuristic":
                    score *= effectiveness(move.get("type"), opponent.get("types"))
                    if move.get("type") in own.get("types", []):
                        score *= 1.5
                    if move.get("category") == "physical" and own.get("status") == "brn":
                        score *= 0.5
                    if move.get("id") in ("recover", "roost", "softboiled", "synthesis", "morningsun", "slackoff") and own.get("hp", 1) / own.get("maxhp", 1) < 0.35:
                        score = 120
            scores.append((score, -action, action))
        return action_to_command(max(scores)[2])

    def _heuristic(self, request, view):
        legal = np.flatnonzero(legal_mask(request, view)).tolist()
        if not legal:
            return None
        moves = (request.get("active") or [{}])[0].get("moves", [])
        own = next((p for p in view.get("own", []) if p.get("active")), {})
        opponent = next((p for p in view.get("opponent", []) if p.get("active")), {})
        forced = any(request.get("forceSwitch", []))
        attacks = {a: _attack_score(moves[a], own, opponent) for a in legal if a < 4}
        best_attack = max(attacks.values(), default=0)
        best_choice = best_attack
        scores = []
        for action in legal:
            if action >= 4:
                continue
            move = moves[action]
            score = attacks[action]
            key = move.get("id")
            if key in ("recover", "roost", "softboiled", "synthesis", "morningsun", "slackoff", "moonlight", "wish") and _health(own) < .45:
                score = max(score, 110 * (1 - _health(own)))
            elif key == "rest" and _health(own) < .25 and own.get("status") != "slp":
                score = max(score, 75)
            elif key in ("thunderwave", "willowisp", "sleeppowder", "spore", "toxic") and not opponent.get("status"):
                score = max(score, 28 if key not in ("spore", "sleeppowder") else 42)
            elif key in ("swordsdance", "calmmind", "dragondance") and _health(own) > .8:
                boost = "atk" if key != "calmmind" else "spa"
                if own.get("boosts", {}).get(boost, 0) == 0:
                    score = max(score, min(best_attack * .65, 55))
            best_choice = max(best_choice, score)
            scores.append((score, -action, action))
        active_risk = _incoming_type_risk(own, opponent)
        for action in legal:
            if action < 4:
                continue
            candidate = next(p for p in view["own"] if p["slot"] == action - 4)
            candidate_attack = max((_attack_score(m, candidate, opponent) for m in candidate.get("moves", []) if m.get("pp", 1) > 0), default=0)
            candidate_risk = _incoming_type_risk(candidate, opponent)
            matchup = (candidate_attack + 10) * (.3 + .7 * _health(candidate)) / (.5 + candidate_risk)
            if forced:
                score = matchup
            elif best_attack <= 0 and candidate_attack > 0:
                # The old -5 score lost to an immune move's zero forever.
                score = best_choice + 1 + matchup * .1
            elif active_risk >= 2 and candidate_risk < active_risk and _health(own) < .35 and candidate_attack > 0:
                score = best_choice + matchup * .25 if _health(candidate) > .6 else -5
            else:
                score = -5
            scores.append((score, -action, action))
        return action_to_command(max(scores)[2])
