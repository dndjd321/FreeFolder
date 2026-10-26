"""Versioned observations derived solely from one player's permitted view."""

import re

import numpy as np

OBSERVATION_VERSION = "public-battle-v1"
ACTION_VERSION = "moves4-switch-slots3-v1"
TYPES = ("normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy")
STATUSES = ("brn", "par", "psn", "tox", "slp", "frz", "fnt")
STATS = ("atk", "def", "spa", "spd", "spe")
BOOSTS = (*STATS, "accuracy", "evasion")
WEATHERS = ("sunnyday", "raindance", "sandstorm", "hail", "snow", "desolateland", "primordialsea", "deltastream")
TERRAINS = ("electricterrain", "grassyterrain", "mistyterrain", "psychicterrain")
SIDE_EFFECTS = ("reflect", "lightscreen", "tailwind")
OBS_DIM = 3 * 40 + 3 * 29 + 4 * 26 + 22
N_ACTIONS = 7


def _one_hot(value, options):
    return [float(value == item) for item in options]


def _types(values):
    return [float(item in (values or [])) for item in TYPES]


def _fraction(value, maximum):
    return float(np.clip((value or 0) / max(maximum or 1, 1), 0, 1))


def legal_mask(request, view):
    """True means legal; switch action N maps to original team slot N - 4."""
    mask = np.zeros(N_ACTIONS, dtype=np.bool_)
    if not request or request.get("wait") or view.get("ended"):
        return mask
    forced = any(request.get("forceSwitch", []))
    active = (request.get("active") or [{}])[0] or {}
    if not forced:
        for index, move in enumerate(active.get("moves", [])[:4]):
            mask[index] = not move.get("disabled") and (move.get("pp", 1) > 0 or move.get("id") == "struggle")
    if forced or not active.get("trapped"):
        for pokemon in view.get("own", []):
            slot = pokemon["slot"]
            if 0 <= slot < 3 and not pokemon.get("active") and not pokemon.get("fainted") and pokemon.get("hp", 0) > 0:
                mask[4 + slot] = True
    return mask


def action_to_command(action):
    if type(action) not in (int, np.int64, np.int32) or not 0 <= int(action) < N_ACTIONS:
        raise ValueError("Action must be an integer from 0 to 6")
    action = int(action)
    return {"type": "move" if action < 4 else "switch", "index": action if action < 4 else action - 4}


def command_to_action(command):
    return int(command["index"]) + (4 if command["type"] == "switch" else 0)


def _field_features(view):
    weather, terrain, trick_room = "", "", False
    side_effects = {"p1": set(), "p2": set()}
    normal = lambda text: re.sub(r"[^a-z0-9]", "", text.lower().replace("move:", ""))
    for line in view.get("events", []):
        parts = line.split("|")
        tag = parts[1] if len(parts) > 1 else ""
        if tag == "-weather" and len(parts) > 2 and "[upkeep]" not in parts:
            weather = normal(parts[2])
        elif tag in ("-fieldstart", "-fieldend") and len(parts) > 2:
            field = normal(parts[2])
            if field in TERRAINS:
                terrain = field if tag == "-fieldstart" else ""
            elif field == "trickroom":
                trick_room = tag == "-fieldstart"
        elif tag in ("-sidestart", "-sideend") and len(parts) > 3:
            side = parts[2][:2]
            if side in side_effects:
                effect = normal(parts[3])
                if tag == "-sidestart":
                    side_effects[side].add(effect)
                else:
                    side_effects[side].discard(effect)
    own = view.get("side", "p1")
    opponent = "p2" if own == "p1" else "p1"
    request = view.get("request", {})
    active = (request.get("active") or [{}])[0] or {}
    return [*_one_hot(weather, WEATHERS), *_one_hot(terrain, TERRAINS), float(trick_room),
            *[float(effect in side_effects[own]) for effect in SIDE_EFFECTS],
            *[float(effect in side_effects[opponent]) for effect in SIDE_EFFECTS],
            min(view.get("turn", 0), 200) / 200,
            float(any(request.get("forceSwitch", []))), float(bool(active.get("trapped")))]


def encode_observation(view):
    """333 bounded features. Opponent build, moves and exact HP are never read."""
    result = []
    own = {pokemon["slot"]: pokemon for pokemon in view.get("own", [])}
    for slot in range(3):
        pokemon = own.get(slot, {})
        status = "fnt" if pokemon.get("fainted") else pokemon.get("status", "")
        result.extend([_fraction(pokemon.get("hp"), pokemon.get("maxhp")), float(bool(pokemon.get("active"))), float(bool(pokemon.get("fainted"))),
                       *_one_hot(status, STATUSES), *_types(pokemon.get("types")),
                       *[float(np.clip(pokemon.get("stats", {}).get(stat, 0) / 500, 0, 1)) for stat in STATS],
                       *[float(np.clip(pokemon.get("boosts", {}).get(stat, 0) / 6, -1, 1)) for stat in BOOSTS]])
    public = view.get("opponent", [])
    for index in range(3):
        pokemon = public[index] if index < len(public) else {}
        status = "fnt" if pokemon.get("fainted") else pokemon.get("status", "")
        result.extend([float(bool(pokemon.get("revealed"))), _fraction(pokemon.get("hp"), pokemon.get("maxhp")),
                       float(bool(pokemon.get("active"))), float(bool(pokemon.get("fainted"))),
                       *_one_hot(status, STATUSES), *_types(pokemon.get("types"))])
    request = view.get("request", {})
    active = (request.get("active") or [{}])[0] or {}
    moves = active.get("moves", [])
    for index in range(4):
        move = moves[index] if index < len(moves) else {}
        accuracy = 1.0 if move.get("accuracy") is True else (move.get("accuracy", 0) or 0) / 100
        result.extend([min(move.get("basePower", 0) or 0, 250) / 250, accuracy,
                       _fraction(move.get("pp"), move.get("maxpp")), float(np.clip((move.get("priority", 0) or 0) / 7, -1, 1)),
                       *_one_hot(move.get("category"), ("physical", "special", "status")), *_types([move.get("type")]),
                       float(bool(move.get("disabled", True)))])
    result.extend(_field_features(view))
    observation = np.asarray(result, dtype=np.float32)
    if observation.shape != (OBS_DIM,) or not np.isfinite(observation).all():
        raise ValueError("Observation schema mismatch")
    return np.clip(observation, -1, 1)
