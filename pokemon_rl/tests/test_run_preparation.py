"""Measure the committed recovery function without changing live resources."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.run_preparation import resource_preview


def resources():
    return [
        {'slot': 2, 'species': 'One', 'hp': 35, 'maxhp': 100, 'status': 'brn', 'sleepTurns': None,
         'item': 'leftovers', 'moves': [{'id': 'surf', 'pp': 4, 'maxpp': 10}, {'id': 'icebeam', 'pp': 0, 'maxpp': 8}]},
        {'slot': 0, 'species': 'Two', 'hp': 0, 'maxhp': 120, 'status': 'slp', 'sleepTurns': 3,
         'item': '', 'moves': [{'id': 'roost', 'pp': 2, 'maxpp': 8}]},
        {'slot': 1, 'species': 'Three', 'hp': 80, 'maxhp': 80, 'status': '', 'sleepTurns': None,
         'item': 'lumberry', 'moves': [{'id': 'earthquake', 'pp': 16, 'maxpp': 16}]},
    ]


class ResourcePreviewTests(unittest.TestCase):
    def test_actual_capped_hp_result_is_measured_without_mutating_resources(self):
        original = resources(); before = copy.deepcopy(original)
        def capped_recovery(rows):
            rows[0]['hp'] = rows[0]['maxhp']
            rows[2]['hp'] = rows[2]['maxhp']
        preview = resource_preview(original, capped_recovery)
        self.assertEqual(before, original)
        self.assertEqual(65, preview['hp_gain'])
        self.assertEqual(115, preview['before']['hp'])
        self.assertEqual(180, preview['after']['hp'])
        self.assertEqual(1, preview['after']['fainted'])
        self.assertEqual(0, preview['revived'])
        self.assertTrue(preview['available'])
        self.assertEqual([0, 1, 2], [row['slot'] for row in preview['party']])

    def test_actual_per_move_pp_caps_and_totals_are_preserved(self):
        original = resources()
        def refill(rows):
            rows[0]['moves'][0]['pp'] = 10
            rows[0]['moves'][1]['pp'] = 8
            rows[1]['moves'][0]['pp'] = 8
        preview = resource_preview(original, refill)
        self.assertEqual(20, preview['pp_gain'])
        self.assertEqual(22, preview['before']['pp'])
        self.assertEqual(42, preview['after']['pp'])
        self.assertEqual(42, preview['after']['maxpp'])
        self.assertEqual({'id': 'surf', 'pp': 10, 'maxpp': 10}, preview['party'][0]['after']['moves'][0])
        self.assertEqual(4, original[0]['moves'][0]['pp'])

    def test_revival_and_sleep_cure_are_reported_from_actual_mutation(self):
        def revive(rows):
            rows[1].update(hp=60, status='', sleepTurns=None)
        preview = resource_preview(resources(), revive)
        self.assertEqual(60, preview['hp_gain'])
        self.assertEqual(1, preview['revived'])
        self.assertEqual(1, preview['cured'])
        self.assertEqual(2, preview['before']['status_count'])
        self.assertEqual(1, preview['after']['status_count'])
        self.assertEqual(3, preview['party'][1]['before']['sleep_turns'])
        self.assertIsNone(preview['party'][1]['after']['sleep_turns'])
        self.assertEqual(0, preview['after']['fainted'])

    def test_combined_effects_can_revive_and_cure_multiple_members(self):
        original = resources(); original[0]['hp'] = 0
        def combined(rows):
            rows[0].update(hp=50, status='', sleepTurns=None)
            rows[1].update(hp=30, status='', sleepTurns=None)
        preview = resource_preview(original, combined)
        self.assertEqual(80, preview['hp_gain'])
        self.assertEqual(2, preview['revived'])
        self.assertEqual(2, preview['cured'])
        self.assertEqual(0, preview['after']['fainted'])

    def test_sleep_counter_only_change_is_not_discarded_as_a_noop(self):
        preview = resource_preview(resources(), lambda rows: rows[1].update(sleepTurns=0))
        self.assertTrue(preview['available'])
        self.assertEqual(0, preview['hp_gain'])
        self.assertEqual(0, preview['cured'])
        self.assertEqual('slp', preview['party'][1]['after']['status'])

    def test_unchanged_effect_is_unavailable_and_returns_json_safe_totals(self):
        preview = resource_preview(resources(), lambda rows: None)
        self.assertFalse(preview['available'])
        self.assertEqual(preview['before'], preview['after'])
        self.assertEqual(0, preview['hp_gain'])
        self.assertEqual(0, preview['pp_gain'])
        self.assertEqual(0, preview['revived'])
        self.assertEqual(0, preview['cured'])
        self.assertIsInstance(json.dumps(preview, allow_nan=False), str)

    def test_callback_error_propagates_after_mutation_and_leaves_original_intact(self):
        original = resources(); before = copy.deepcopy(original)
        error = ValueError('현재는 회복할 수 없습니다.')
        def failure(rows):
            rows[0]['hp'] = 90
            rows[0]['moves'][0]['pp'] = 9
            raise error
        with self.assertRaises(ValueError) as result:
            resource_preview(original, failure)
        self.assertIs(error, result.exception)
        self.assertEqual(before, original)

    def test_callback_return_value_does_not_replace_mutated_resources(self):
        def effect(rows):
            rows[1].update(hp=30, status='', sleepTurns=None)
            return True
        preview = resource_preview(resources(), effect)
        self.assertEqual(1, preview['revived'])
        self.assertEqual(30, preview['hp_gain'])

    def test_empty_party_has_zero_totals_and_no_effect(self):
        preview = resource_preview([], lambda rows: None)
        self.assertEqual([], preview['party'])
        self.assertFalse(preview['available'])
        self.assertTrue(all(value == 0 for value in preview['before'].values()))

    def test_recovery_cannot_change_party_size(self):
        with self.assertRaises(ValueError):
            resource_preview(resources(), lambda rows: rows.pop())

    def test_invalid_initial_resource_does_not_reach_callback(self):
        mutations = [lambda r: r[0].update(hp=-1), lambda r: r[0].update(hp=101),
                     lambda r: r[0].update(hp=True), lambda r: r[0].update(maxhp=0),
                     lambda r: r[0]['moves'][0].update(pp=11),
                     lambda r: r[0]['moves'][0].update(maxpp=-1),
                     lambda r: r[0].update(sleepTurns=-1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                original = resources(); mutate(original)
                def should_not_run(rows):
                    self.fail('Invalid resources reached the recovery effect')
                with self.assertRaises(ValueError):
                    resource_preview(original, should_not_run)

    def test_helper_does_not_silently_clamp_an_invalid_callback_result(self):
        original = resources(); before = copy.deepcopy(original)
        with self.assertRaises(ValueError):
            resource_preview(original, lambda rows: rows[0].update(hp=999))
        self.assertEqual(before, original)

    def test_returned_snapshots_do_not_alias_the_original_nested_moves(self):
        original = resources()
        preview = resource_preview(original, lambda rows: None)
        preview['party'][0]['before']['moves'][0]['pp'] = 0
        preview['party'][0]['after']['moves'][0]['pp'] = 1
        self.assertEqual(4, original[0]['moves'][0]['pp'])
        self.assertNotEqual(preview['party'][0]['before'], preview['party'][0]['after'])


if __name__ == '__main__':
    unittest.main()
