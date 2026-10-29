"""Frozen decisions and public-information boundary of public-sustain-v1."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balance.policies import POLICY_VERSION, SUSTAIN_POLICY, SUSTAIN_REASONS, SustainPolicy
from training.observation import command_to_action, legal_mask


def move(key="tackle", power=100, **changes):
    result = {"id": key, "type": "normal", "category": "physical", "basePower": power,
              "accuracy": True, "pp": 10, "maxpp": 10}
    result.update(changes)
    return result


def pokemon(slot, *, active=False, **changes):
    result = {"slot": slot, "name": "Ally " + str(slot), "id": "ally" + str(slot), "active": active,
              "hp": 100, "maxhp": 100, "fainted": False, "types": ["normal"],
              "stats": {"atk": 100, "spa": 100}, "boosts": {}, "ability": "",
              "moves": [move()]}
    result.update(changes)
    return result


def battle(moves=None, **active_changes):
    active = pokemon(0, active=True, **active_changes)
    if moves is not None:
        active["moves"] = copy.deepcopy(moves)
    request = {"active": [{"moves": copy.deepcopy(active["moves"])}]}
    view = {"side": "p1", "own": [active], "opponent": [{"active": True, "revealed": True,
            "id": "opponent", "types": ["normal"]}], "events": [], "request": request}
    return request, view


class PublicFieldGuard(dict):
    """A private-field inspection fails even if the caller would ignore its value."""
    forbidden = frozenset({"moves", "stats", "ability", "item", "team", "private_enemy", "checkpoint"})

    def get(self, key, default=None):
        if key in self.forbidden:
            raise AssertionError("Private opponent field read: " + key)
        return super().get(key, default)

    def __getitem__(self, key):
        if key in self.forbidden:
            raise AssertionError("Private opponent field read: " + key)
        return super().__getitem__(key)


class SustainPolicyTests(unittest.TestCase):
    def choose(self, request, view, expected, reason, policy=None):
        policy = policy or SustainPolicy()
        before = copy.deepcopy((request, view))
        calls = policy.calls
        actual = policy.predict(request, view)
        self.assertEqual(expected, actual)
        self.assertEqual(reason, policy.last_reason)
        self.assertIn(policy.last_reason, SUSTAIN_REASONS)
        self.assertEqual(calls + 1, policy.calls)
        self.assertEqual(before, (request, view), "Prediction must not mutate either input")
        if actual is not None:
            self.assertTrue(legal_mask(request, view)[command_to_action(actual)])
        return policy

    def test_version_configuration_and_trace_reasons_are_explicit_and_serializable(self):
        self.assertEqual("public-sustain-v1", POLICY_VERSION)
        self.assertEqual(POLICY_VERSION, SUSTAIN_POLICY["version"])
        self.assertEqual((2, .8, .55, .55, .7, 2), tuple(SUSTAIN_POLICY[key] for key in (
            "switch_ratio_strictly_above", "wish_protect_hp_strictly_below", "heal_hp_strictly_below",
            "wish_hp_strictly_below", "swordsdance_hp_strictly_above", "swordsdance_atk_boost_strictly_below")))
        self.assertEqual(["roost", "moonlight"], SUSTAIN_POLICY["healing_moves"])
        self.assertIn("Psychic/Ghost", SUSTAIN_POLICY["type_table"])
        self.assertIn("original switch slot ascending", SUSTAIN_POLICY["tie_break"])
        self.assertEqual(SUSTAIN_POLICY, json.loads(json.dumps(SUSTAIN_POLICY)))
        policy = SustainPolicy()
        self.assertEqual((0, "not-called"), (policy.calls, policy.last_reason))
        self.assertEqual(set(SUSTAIN_POLICY["reasons"]), set(SUSTAIN_REASONS))

    def test_wait_ended_missing_request_and_empty_mask_have_no_action_and_reset_reason(self):
        policy = SustainPolicy()
        request, view = battle()
        self.choose(request, view, {"type": "move", "index": 0}, "damage", policy)
        for request_override, view_override in [({"wait": True}, {}), (None, {}), ({}, {}),
                                               (request, {"ended": True}),
                                               ({"active": [{"moves": [move(pp=0)]}]}, {})]:
            with self.subTest(request=request_override, view=view_override):
                current = {**view, **view_override}
                self.choose(request_override, current, None, "no-legal-action", policy)
        self.assertEqual(6, policy.calls)

    def test_explicit_request_is_authoritative_over_an_embedded_stale_request(self):
        request, view = battle()
        view["request"] = {"wait": True}
        self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_move_ties_follow_request_index_and_ignore_a_fifth_move(self):
        request, view = battle([move("first"), move("second"), move("third"), move("fourth"), move("fifth", 999999)])
        self.choose(request, view, {"type": "move", "index": 0}, "damage")
        request["active"][0]["moves"][0]["disabled"] = True
        self.choose(request, view, {"type": "move", "index": 1}, "damage")

    def test_forced_switch_uses_original_slots_for_ties_despite_public_array_order(self):
        request, view = battle()
        request.update(forceSwitch=[True], active=[{"moves": [move("roost", 0)], "trapped": True}])
        active = view["own"][0]
        for order in ([pokemon(2), active, pokemon(1)], [pokemon(1), pokemon(2), active]):
            view["own"] = order
            self.choose(request, view, {"type": "switch", "index": 1}, "forced-switch")

    def test_forced_switch_uses_matchup_and_masks_dead_fainted_active_and_out_of_range(self):
        request, view = battle()
        request["forceSwitch"] = [True]
        view["own"] += [pokemon(1, moves=[move(power=110)]), pokemon(2, moves=[move(power=150)])]
        self.choose(request, view, {"type": "switch", "index": 2}, "forced-switch")
        view["own"][2]["fainted"] = True
        self.choose(request, view, {"type": "switch", "index": 1}, "forced-switch")
        view["own"][1]["hp"] = 0
        view["own"].append(pokemon(3, moves=[move(power=999999)]))
        self.choose(request, view, None, "no-legal-action")

    def test_voluntary_switch_requires_strictly_more_than_double_matchup(self):
        request, view = battle()
        view["own"] += [pokemon(1, moves=[move(power=200)])]
        self.choose(request, view, {"type": "move", "index": 0}, "damage")
        view["own"][1]["moves"][0]["basePower"] = 201
        self.choose(request, view, {"type": "switch", "index": 1}, "matchup-switch")
        request["active"][0]["trapped"] = True
        self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_voluntary_switch_ties_and_priority_over_healing_are_fixed(self):
        request, view = battle([move("roost", 0), move()], hp=20)
        view["own"] = [pokemon(2, moves=[move(power=300)]), view["own"][0], pokemon(1, moves=[move(power=300)])]
        self.choose(request, view, {"type": "switch", "index": 1}, "matchup-switch")
        request["active"][0]["trapped"] = True
        self.choose(request, view, {"type": "move", "index": 0}, "heal")

    def test_exhausted_and_disabled_moves_are_not_used_to_rank_replacements(self):
        request, view = battle()
        request["forceSwitch"] = [True]
        view["own"] += [pokemon(1, moves=[move(power=99999, pp=0), move(power=99999, disabled=True), move(power=10)]),
                        pokemon(2, moves=[move(power=20)])]
        self.choose(request, view, {"type": "switch", "index": 2}, "forced-switch")

    def test_hp_floor_avoids_zero_scores_for_low_health_replacements(self):
        request, view = battle()
        request["forceSwitch"] = [True]
        view["own"] += [pokemon(2, hp=29), pokemon(1, hp=1)]
        self.choose(request, view, {"type": "switch", "index": 1}, "forced-switch")

    def test_own_water_absorb_and_mean_enemy_type_threat_rank_replacements(self):
        request, view = battle()
        request["forceSwitch"] = [True]
        view["opponent"][0]["types"] = ["water"]
        view["own"] += [pokemon(1, hp=30, ability="waterabsorb"), pokemon(2)]
        self.choose(request, view, {"type": "switch", "index": 1}, "forced-switch")
        view["opponent"][0]["types"] = ["water", "grass"]
        # Water Absorb's mean threat is (0+1)/2, rather than max(0,1).
        view["own"][1]["hp"] = 60
        self.choose(request, view, {"type": "switch", "index": 1}, "forced-switch")

    def test_only_switches_legal_falls_back_to_a_legal_replacement_even_below_ratio(self):
        request, view = battle()
        request["active"][0]["moves"][0]["pp"] = 0
        view["own"].append(pokemon(2, moves=[move(power=1)]))
        self.choose(request, view, {"type": "switch", "index": 2}, "legal-fallback")
        request["active"] = [None]
        self.choose(request, view, {"type": "switch", "index": 2}, "legal-fallback")

    def test_struggle_with_zero_pp_is_legal_and_disabled_struggle_is_not(self):
        request, view = battle([move("old", 999999, pp=0), move("struggle", 50, pp=0)])
        self.choose(request, view, {"type": "move", "index": 1}, "damage")
        request["active"][0]["moves"][1]["disabled"] = True
        self.choose(request, view, None, "no-legal-action")

    def test_wish_protect_has_a_strict_eighty_percent_boundary_and_precedes_healing(self):
        request, view = battle([move("roost", 0), move("protect", 0), move()], hp=79)
        view["events"] = ["|move|p1a: Ally 0|Wish|p1a: Ally 0"]
        self.choose(request, view, {"type": "move", "index": 1}, "wish-protect")
        view["own"][0]["hp"] = 80
        self.choose(request, view, {"type": "move", "index": 2}, "damage")
        view["own"][0]["hp"] = 20
        self.choose(request, view, {"type": "move", "index": 1}, "wish-protect")

    def test_last_wish_must_belong_to_current_active_and_not_an_opponent_or_old_stint(self):
        request, view = battle([move("protect", 0), move()], hp=70)
        wish = "|move|p1a: Ally 0|Wish|p1a: Ally 0"
        for events, expected, reason in [
            ([wish, "|move|p2a: Ally 0|Tackle|p1a: Ally 0"], 0, "wish-protect"),
            (["|move|p2a: Ally 0|Wish|p2a: Ally 0"], 1, "damage"),
            ([wish, "|move|p1a: Another ally|Tackle|p2a: Foe"], 1, "damage"),
            ([wish, "|move|p1a: Ally 0|Tackle|p2a: Foe"], 1, "damage"),
            (["|move|p1a: Ally 00|Wish|p1a: Ally 00"], 1, "damage"),
            ([wish, "|switch|p1a: Ally 0|Species|70/100"], 1, "damage"),
            ([wish, "|drag|p1a: Ally 0|Species|70/100"], 1, "damage"),
            ([wish, "|replace|p1a: Ally 0|Species|70/100"], 1, "damage"),
            (["|move|p1a: Ally 0", None, "|move"], 1, "damage"),
        ]:
            with self.subTest(events=events):
                view["events"] = events
                self.choose(request, view, {"type": "move", "index": expected}, reason)
        view["side"] = "p2"
        view["events"] = ["|move|p2a: Ally 0|Wish|p2a: Ally 0"]
        self.choose(request, view, {"type": "move", "index": 0}, "wish-protect")

    def test_disabled_or_exhausted_protect_cannot_override_a_legal_heal(self):
        for changes in ({"disabled": True}, {"pp": 0}):
            request, view = battle([move("protect", 0, **changes), move("roost", 0), move()], hp=40)
            view["events"] = ["|move|p1a: Ally 0|Wish|p1a: Ally 0"]
            self.choose(request, view, {"type": "move", "index": 1}, "heal")

    def test_roost_moonlight_and_wish_have_strict_fifty_five_percent_boundaries(self):
        for healing in ("roost", "moonlight", "wish"):
            with self.subTest(healing=healing):
                request, view = battle([move(), move(healing, 0)], hp=54)
                self.choose(request, view, {"type": "move", "index": 1}, "wish" if healing == "wish" else "heal")
                view["own"][0]["hp"] = 55
                self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_heal_precedes_wish_and_same_priority_heals_follow_request_index(self):
        request, view = battle([move("wish", 0), move("moonlight", 0), move("roost", 0), move()], hp=40)
        self.choose(request, view, {"type": "move", "index": 1}, "heal")
        request["active"][0]["moves"][1]["disabled"] = True
        self.choose(request, view, {"type": "move", "index": 2}, "heal")
        request["active"][0]["moves"][2]["pp"] = 0
        self.choose(request, view, {"type": "move", "index": 0}, "wish")

    def test_swords_dance_hp_and_boost_thresholds_are_strict(self):
        for hp, boost, expected, reason in [(71, 1, 1, "setup"), (70, 1, 0, "damage"),
                                            (100, 2, 0, "damage"), (100, -1, 1, "setup")]:
            with self.subTest(hp=hp, boost=boost):
                request, view = battle([move(), move("swordsdance", 0)], hp=hp, boosts={"atk": boost})
                self.choose(request, view, {"type": "move", "index": expected}, reason)
        request, view = battle([move(), move("swordsdance", 0, disabled=True)])
        self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_literal_zero_power_does_not_get_a_variable_or_fixed_damage_prior(self):
        request, view = battle([move("grassknot", 0, type="grass", category="special"),
                                move("seismictoss", 0), move("tiny", 1)])
        self.choose(request, view, {"type": "move", "index": 2}, "damage")

    def test_training_type_table_uses_psychic_against_ghost_without_legacy_immunity(self):
        request, view = battle([move("psychic", 100, type="psychic"), move("bite", 30, type="dark")])
        view["opponent"][0]["types"] = ["ghost"]
        self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_type_immunity_and_stab_change_attack_ranking(self):
        request, view = battle([move("normal", 100), move("water", 120, type="water")])
        self.choose(request, view, {"type": "move", "index": 0}, "damage")
        view["opponent"][0]["types"] = ["ghost"]
        self.choose(request, view, {"type": "move", "index": 1}, "damage")

    def test_accuracy_uses_the_fixture_true_and_accuracy_or_hundred_convention(self):
        request, view = battle([move("risky", 100, accuracy=50), move("reliable", 60)])
        self.choose(request, view, {"type": "move", "index": 1}, "damage")
        for accuracy in (True, 0, None, False):
            request["active"][0]["moves"][0]["accuracy"] = accuracy
            self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_offensive_category_stats_and_positive_and_negative_boosts_are_public_inputs(self):
        request, view = battle([move("physical", 100), move("special", 30, category="special")],
                               stats={"atk": 50, "spa": 200})
        self.choose(request, view, {"type": "move", "index": 1}, "damage")
        view["own"][0]["boosts"]["atk"] = 2
        self.choose(request, view, {"type": "move", "index": 0}, "damage")
        view["own"][0]["boosts"]["atk"] = -2
        self.choose(request, view, {"type": "move", "index": 1}, "damage")

    def test_technician_uses_own_ability_and_the_literal_sixty_power_boundary(self):
        request, view = battle([move("small", 60), move("large", 80)], ability="technician")
        self.choose(request, view, {"type": "move", "index": 0}, "damage")
        request["active"][0]["moves"][0]["basePower"] = 61
        self.choose(request, view, {"type": "move", "index": 1}, "damage")
        request["active"][0]["moves"][0]["basePower"] = 60
        view["own"][0]["ability"] = "swarm"
        self.choose(request, view, {"type": "move", "index": 1}, "damage")

    def test_fixture_burn_behavior_is_not_silently_replaced_by_training_burn_scoring(self):
        request, view = battle([move("physical", 100), move("special", 80, category="special")], status="brn")
        self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_rotom_wash_levitate_comes_only_from_revealed_species_knowledge(self):
        request, view = battle([move("earthquake", 200, type="ground"), move("surf", 1, type="water")])
        view["opponent"][0].update(id="rotomwash", types=["electric", "water"])
        self.choose(request, view, {"type": "move", "index": 1}, "damage")
        view["opponent"][0]["id"] = "another-electric-water"
        self.choose(request, view, {"type": "move", "index": 0}, "damage")
        view["opponent"][0].update(id="rotomwash", revealed=False)
        self.choose(request, view, {"type": "move", "index": 0}, "damage")

    def test_private_opponent_fields_are_never_inspected_and_cannot_affect_decisions(self):
        request, view = battle([move("earthquake", 200, type="ground"), move("surf", 1, type="water")])
        public = {"active": True, "revealed": True, "id": "rotomwash", "types": ["electric", "water"]}
        for hidden in ({"ability": "levitate", "moves": [move(power=99999)], "stats": {"atk": 999999}},
                       {"ability": "moldbreaker", "item": "airballoon", "team": ["private"], "checkpoint": "private"}):
            view["opponent"] = [PublicFieldGuard({**public, **hidden})]
            view["private_enemy"] = PublicFieldGuard(hidden)
            with self.assertRaises(AssertionError):
                view["opponent"][0].get("ability")
            self.choose(request, view, {"type": "move", "index": 1}, "damage")
        view["own"].append(pokemon(1, moves=[move(power=400)]))
        request["forceSwitch"] = [True]
        self.choose(request, view, {"type": "switch", "index": 1}, "forced-switch")

    def test_missing_public_enemy_or_own_optional_metadata_keeps_a_legal_neutral_choice(self):
        request, view = battle([move("first", 80), move("second", 90)])
        view["opponent"] = []
        for key in ("stats", "boosts", "ability", "types"):
            view["own"][0].pop(key)
        self.choose(request, view, {"type": "move", "index": 1}, "damage")


if __name__ == "__main__":
    unittest.main()
