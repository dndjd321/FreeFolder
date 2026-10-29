"""Frozen public-information battle policy for expedition benchmarks.

``public-sustain-v1`` formalizes the sustain decisions in the twenty-node
integration fixture, without importing or changing that fixture. It uses the
training type table (including Psychic against Ghost), the shared legal-action
mask, and original-slot tie breaks, so it is a new policy rather than an exact
replay of the fixture. Scores rank choices; they are not engine damage estimates.
"""
from __future__ import annotations

from training.observation import action_to_command, legal_mask
from training.policies import effectiveness


POLICY_VERSION = "public-sustain-v1"
SUSTAIN_REASONS = frozenset({
    "not-called", "no-legal-action", "forced-switch", "matchup-switch",
    "wish-protect", "heal", "wish", "setup", "damage", "legal-fallback",
})
SUSTAIN_POLICY = {
    "version": POLICY_VERSION,
    "priority": ["forced-switch", "matchup-switch", "wish-protect", "heal", "wish", "setup", "damage"],
    "switch_ratio_strictly_above": 2,
    "wish_protect_hp_strictly_below": .8,
    "heal_hp_strictly_below": .55,
    "healing_moves": ["roost", "moonlight"],
    "wish_hp_strictly_below": .55,
    "swordsdance_hp_strictly_above": .7,
    "swordsdance_atk_boost_strictly_below": 2,
    "matchup_health_floor": .3,
    "matchup_mean_type_threat_floor": .15,
    "damage_score": "literal-basePower * effectiveness * own-offensive-stat * accuracy * STAB * Technician * own-boost",
    "accuracy": "true:1; otherwise:(accuracy-or-100)/100",
    "technician": "own canonical ability technician and literal basePower <= 60:1.5",
    "type_table": "training.policies.effectiveness; differs from legacy fixture Psychic/Ghost immunity",
    "public_species_knowledge": "public opponent id rotomwash implies Levitate against Ground",
    "own_ability_knowledge": "own waterabsorb makes incoming Water type threat zero",
    "wish_history": "last own active move must be current active's Wish; own switch/drag/replace clears it",
    "tie_break": "original switch slot ascending; request move index ascending",
    "legality": "training.observation.legal_mask and action_to_command; no action when ended/wait/no legal choice",
    "information": "own public build/resources, active revealed opponent id/types, public own move events only; no opponent moves/stats/ability/item",
    "variable_power_prior": 0,
    "burn_adjustment": False,
    "reasons": sorted(SUSTAIN_REASONS),
}


def _health(pokemon):
    return max(0, pokemon.get("hp", 0)) / max(1, pokemon.get("maxhp", 1))


def _damage_score(pokemon, move, opponent):
    # Rotom-Wash's species-level ability is public knowledge, not an inspection
    # of this opponent's private ability or its current suppression state.
    if move.get("type") == "ground" and opponent.get("id") == "rotomwash":
        return 0
    stat = "atk" if move.get("category") == "physical" else "spa"
    power = move.get("basePower", 0)
    accuracy = 1 if move.get("accuracy") is True else (move.get("accuracy", 100) or 100) / 100
    stab = 1.5 if move.get("type") in pokemon.get("types", []) else 1
    technician = 1.5 if pokemon.get("ability") == "technician" and power <= 60 else 1
    boost = pokemon.get("boosts", {}).get(stat, 0)
    boost_multiplier = (2 + boost) / 2 if boost >= 0 else 2 / (2 - boost)
    return (power * effectiveness(move.get("type", "normal"), opponent.get("types", []))
            * pokemon.get("stats", {}).get(stat, 100) * accuracy * stab * technician * boost_multiplier)


def _matchup(pokemon, opponent):
    attacks = [_damage_score(pokemon, move, opponent) for move in pokemon.get("moves", [])
               if move.get("pp", 1) > 0 and not move.get("disabled")]
    threats = [0 if move_type == "water" and pokemon.get("ability") == "waterabsorb"
               else effectiveness(move_type, pokemon.get("types", []))
               for move_type in opponent.get("types", [])]
    mean_threat = sum(threats) / len(threats) if threats else 1
    return max(attacks, default=0) * max(.3, _health(pokemon)) / max(.15, mean_threat)


def _last_own_move(view, active):
    side = view.get("side", "p1")
    actor = side + "a: " + active.get("name", "")
    prefix = side + "a: "
    for line in reversed(view.get("events", [])):
        if not isinstance(line, str):
            continue
        parts = line.split("|")
        if len(parts) < 3 or not parts[2].startswith(prefix):
            continue
        if parts[1] in ("switch", "drag", "replace"):
            return ""
        if parts[1] == "move":
            return parts[3] if len(parts) > 3 and parts[2] == actor else ""
    return ""


class SustainPolicy:
    """Deterministic benchmark-only sustain policy with safe trace reasons."""

    def __init__(self):
        self.calls = 0
        self.last_reason = "not-called"

    def _choose(self, action, reason):
        self.last_reason = reason
        return action_to_command(action)

    def predict(self, request, view):
        self.calls += 1
        legal = [index for index, allowed in enumerate(legal_mask(request, view)) if allowed]
        if not legal:
            self.last_reason = "no-legal-action"
            return None
        own = view.get("own", [])
        by_slot = {pokemon["slot"]: pokemon for pokemon in own}
        opponent = next((pokemon for pokemon in view.get("opponent", [])
                         if pokemon.get("active") and pokemon.get("revealed") is not False), {})
        switches = [action for action in legal if action >= 4]
        best_switch = max(switches, key=lambda action: (_matchup(by_slot[action - 4], opponent), -action), default=None)
        if any(request.get("forceSwitch", [])):
            return self._choose(best_switch, "forced-switch")
        active = next((pokemon for pokemon in own if pokemon.get("active")), {})
        if best_switch is not None and _matchup(by_slot[best_switch - 4], opponent) > _matchup(active, opponent) * 2:
            return self._choose(best_switch, "matchup-switch")
        request_active = (request.get("active") or [{}])[0] or {}
        request_moves = request_active.get("moves", [])
        moves = [(action, request_moves[action]) for action in legal if action < 4]
        if not moves:
            return self._choose(best_switch, "legal-fallback")
        health = _health(active)
        if health < .8 and _last_own_move(view, active) == "Wish":
            protect = next((action for action, move in moves if move.get("id") == "protect"), None)
            if protect is not None:
                return self._choose(protect, "wish-protect")
        if health < .55:
            heal = next((action for action, move in moves if move.get("id") in ("roost", "moonlight")), None)
            if heal is not None:
                return self._choose(heal, "heal")
            wish = next((action for action, move in moves if move.get("id") == "wish"), None)
            if wish is not None:
                return self._choose(wish, "wish")
        if health > .7 and active.get("boosts", {}).get("atk", 0) < 2:
            setup = next((action for action, move in moves if move.get("id") == "swordsdance"), None)
            if setup is not None:
                return self._choose(setup, "setup")
        best_move = max(moves, key=lambda entry: (_damage_score(active, entry[1], opponent), -entry[0]))[0]
        return self._choose(best_move, "damage")
