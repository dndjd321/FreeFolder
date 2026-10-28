"""Journal coverage, cumulative metrics, bounds, and public data boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.run_journal import EVENT_KINDS, MAX_EVENTS, public_journal, record_event, snapshot


PRIVATE = 'PRIVATE-CREDENTIAL-OR-CHECKPOINT'


def state():
    return {'node_index': 0, 'node': {'label': '숲', 'enemy_team': PRIVATE}, 'money': 100,
            'team': [{'species': 'One', 'moves': ['Surf'], 'private': PRIVATE},
                     {'species': 'Two'}, {'species': 'Three'}],
            'resources': [
                {'hp': 35, 'maxhp': 1000, 'status': 'brn', 'sleepTurns': None, 'item': PRIVATE,
                 'moves': [{'id': 'surf', 'pp': 5, 'maxpp': 500, 'private': PRIVATE}]},
                {'hp': 0, 'maxhp': 120, 'status': 'slp', 'sleepTurns': 3,
                 'moves': [{'id': 'roost', 'pp': 2, 'maxpp': 8}]},
                {'hp': 80, 'maxhp': 80, 'status': '',
                 'moves': [{'id': 'earthquake', 'pp': 16, 'maxpp': 16}]},
            ], 'token': PRIVATE, 'token_hash': PRIVATE, 'creation_secret': PRIVATE,
            'creation': {'secret': PRIVATE}, 'battle': {'checkpoint': PRIVATE}, 'enemy_team': PRIVATE}


class JournalSnapshotTests(unittest.TestCase):
    def test_snapshot_copies_only_minimal_resources_and_is_detached(self):
        live = state()
        before = copy.deepcopy(live)
        measured = snapshot(live)
        self.assertEqual(before, live)
        self.assertEqual((115, 1200, 23, 524, 1, 100),
                         tuple(measured[key] for key in ('hp', 'maxhp', 'pp', 'maxpp', 'fainted', 'money')))
        self.assertEqual({'species', 'hp', 'maxhp', 'pp', 'maxpp', 'status'}, set(measured['party'][0]))
        self.assertNotIn(PRIVATE, json.dumps(measured))
        live['resources'][0]['hp'] = 99
        live['team'][0]['species'] = 'Changed'
        self.assertEqual((35, 'One'), (measured['party'][0]['hp'], measured['party'][0]['species']))
        measured['party'][1]['status'] = ''
        self.assertEqual('slp', live['resources'][1]['status'])

    def test_invalid_snapshot_resources_are_rejected_without_exposing_values(self):
        mutations = [lambda s: s['resources'][0].update(hp=-1),
                     lambda s: s['resources'][0].update(hp=1001),
                     lambda s: s['resources'][0].update(maxhp=0),
                     lambda s: s['resources'][0].update(status=PRIVATE),
                     lambda s: s['resources'][0].update(status=False),
                     lambda s: s['resources'][0]['moves'][0].update(pp=True),
                     lambda s: s['resources'][0]['moves'][0].update(pp=501),
                     lambda s: s.update(money=float('nan')),
                     lambda s: s['team'].pop()]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                live = state()
                mutate(live)
                with self.assertRaises(ValueError) as error:
                    snapshot(live)
                self.assertNotIn(PRIVATE, str(error.exception))


class JournalRecordingTests(unittest.TestCase):
    def test_created_starts_complete_coverage_without_counting_initial_resources_as_income(self):
        live = state()
        self.assertTrue(record_event(live, 'created', None, {'difficulty': 'hard'}))
        journal = public_journal(live)
        self.assertTrue(journal['available'])
        self.assertTrue(journal['complete_history'])
        self.assertEqual((0, 1, 0), (journal['from_node'], journal['total_events'], journal['dropped_events']))
        self.assertEqual(1, journal['counts']['created'])
        self.assertTrue(all(total == 0 for total in journal['totals'].values()))
        self.assertEqual('hard', journal['events'][0]['details']['difficulty'])
        self.assertEqual(journal['events'][0]['before'], journal['events'][0]['after'])

    def test_legacy_first_action_starts_partial_coverage_at_its_before_node(self):
        live = state()
        live['node_index'] = 7
        before = snapshot(live)
        live['node_index'] = 8
        live['node']['label'] = '다음 관문'
        record_event(live, 'path', before, {'path_id': 'node-8-0', 'node_kind': 'battle'})
        journal = public_journal(live)
        self.assertFalse(journal['complete_history'])
        self.assertEqual(7, journal['from_node'])
        self.assertEqual(0, journal['counts']['created'])
        self.assertEqual((1, 8, '다음 관문'),
                         tuple(journal['events'][0][key] for key in ('sequence', 'node_index', 'node_label')))

    def test_recovery_aggregates_actual_capped_member_deltas_and_money(self):
        live = state()
        record_event(live, 'created', None)
        before = snapshot(live)
        live['resources'][0]['hp'] = 1000
        live['resources'][0]['moves'][0]['pp'] = 500
        live['resources'][1].update(hp=60, status='')
        live['resources'][1]['moves'][0]['pp'] = 8
        live['money'] -= 25
        record_event(live, 'shop', before, {'offer_id': 'recovery', 'offer_kind': 'revive', 'price': 25, 'action': 'buy'})
        totals = public_journal(live)['totals']
        self.assertEqual((1025, 501, 1, 1, 25),
                         tuple(totals[key] for key in ('hp_recovered', 'pp_recovered', 'revived', 'cured', 'money_spent')))
        before = snapshot(live)
        live['money'] += 35
        record_event(live, 'reward', before, {'reward_id': 'gold', 'reward_kind': 'money'})
        self.assertEqual(35, public_journal(live)['totals']['money_earned'])

    def test_battle_end_counts_outcomes_and_separate_post_battle_healing(self):
        live = state()
        record_event(live, 'created', None)
        for winner, turns in [('p1', 7), ('p2', 3), (None, 2), ('draw', 1)]:
            record_event(live, 'battle_start', snapshot(live), {'opponent_label': '보스'})
            before = snapshot(live)
            live['resources'][0]['hp'] += 10  # In-battle effects are not recovery-choice totals.
            record_event(live, 'battle_end', before, {'winner': winner, 'turns': turns, 'post_battle_heal': 2})
        totals = public_journal(live)['totals']
        self.assertEqual((4, 4, 1, 1, 2, 13, 8),
                         tuple(totals[key] for key in ('battles_started', 'battles_finished', 'wins',
                                                       'losses', 'draws', 'turns', 'hp_recovered')))
        self.assertEqual(0, totals['pp_recovered'])

    def test_party_reordering_and_new_full_health_recruits_are_not_healing(self):
        live = state()
        record_event(live, 'created', None)
        before = snapshot(live)
        for key in ('team', 'resources'):
            live[key] = [live[key][slot] for slot in [2, 0, 1]]
        order = [2, 0, 1]
        record_event(live, 'party_order', before, {'order': order, 'lead_species': 'Three'})
        order[0] = 9
        before = snapshot(live)
        live['team'][2] = {'species': 'New'}
        live['resources'][2].update(hp=120, status='')
        live['resources'][2]['moves'][0]['pp'] = 8
        record_event(live, 'recruit', before, {'candidate_id': 'new-member', 'species': 'New', 'replace_index': 2,
                                              'replaced_species': 'Two', 'skipped': False})
        record_event(live, 'recruit', snapshot(live), {'candidate_id': 'skip', 'skipped': True})
        journal = public_journal(live)
        self.assertEqual(1, journal['totals']['recruits'])
        self.assertEqual([2, 0, 1], journal['events'][1]['details']['order'])
        self.assertTrue(all(journal['totals'][key] == 0 for key in ('hp_recovered', 'pp_recovered', 'revived', 'cured')))

    def test_recent_event_bound_does_not_drop_cumulative_counts_or_coverage(self):
        live = state()
        record_event(live, 'created', None)
        for index in range(150):
            before = snapshot(live)
            live['resources'][0]['hp'] += 1
            live['resources'][0]['moves'][0]['pp'] += 1
            live['money'] += 1
            record_event(live, 'reward', before, {'reward_id': 'reward-' + str(index), 'reward_kind': 'heal'})
        journal = public_journal(live)
        self.assertEqual((MAX_EVENTS, 151, 23), (len(journal['events']), journal['total_events'], journal['dropped_events']))
        self.assertEqual((24, 151), (journal['events'][0]['sequence'], journal['events'][-1]['sequence']))
        self.assertTrue(journal['complete_history'])
        self.assertEqual(1, journal['counts']['created'])
        self.assertEqual(150, journal['counts']['reward'])
        self.assertEqual([150, 150, 150], [journal['totals'][key] for key in ('money_earned', 'hp_recovered', 'pp_recovered')])

    def test_every_event_kind_uses_a_detail_whitelist(self):
        live = state()
        choices = {'created': {'difficulty': 'normal'}, 'battle_start': {'opponent_label': '상대'},
                   'battle_end': {'winner': 'p1', 'turns': 2, 'post_battle_heal': 0},
                   'reward': {'reward_id': 'reward', 'reward_kind': 'pp', 'label': '회복'},
                   'reroll': {'rerolls_left': 0, 'cost': 0},
                   'path': {'path_id': 'next', 'node_kind': 'boss', 'theme': 'rain', 'label': '관문'},
                   'rest': {'option': 'pp'}, 'shop': {'offer_id': 'leave', 'offer_kind': 'leave', 'action': 'leave'},
                   'recruit': {'candidate_id': 'skip', 'skipped': True},
                   'party_order': {'order': [0, 1, 2], 'lead_species': 'One'}}
        for kind in EVENT_KINDS:
            details = {**choices[kind], 'token': PRIVATE, 'creation_secret': PRIVATE,
                       'enemy_team': {'species': PRIVATE}, 'checkpoint': {'actions': PRIVATE}}
            record_event(live, kind, None if kind == 'created' else snapshot(live), details)
        journal = public_journal(live)
        self.assertNotIn(PRIVATE, json.dumps(journal))
        self.assertTrue(all(count == 1 for count in journal['counts'].values()))
        for event in journal['events']:
            self.assertEqual(choices[event['kind']], event['details'])

    def test_snapshot_input_and_returned_public_history_do_not_alias_saved_journal(self):
        live = state()
        before = snapshot(live)
        live['resources'][0]['hp'] += 5
        record_event(live, 'rest', before, {'option': 'heal'})
        before['party'][0]['hp'] = 0
        public = public_journal(live)
        public['events'][0]['before']['party'][0]['hp'] = 99
        public['totals']['hp_recovered'] = 999
        self.assertEqual(35, live['journal']['events'][0]['before']['party'][0]['hp'])
        self.assertEqual(5, live['journal']['totals']['hp_recovered'])

    def test_public_view_filters_unknown_fields_even_inside_saved_v1_records(self):
        live = state()
        record_event(live, 'created', None)
        live['journal']['token'] = PRIVATE
        live['journal']['totals']['enemy_team'] = PRIVATE
        event = live['journal']['events'][0]
        event['checkpoint'] = PRIVATE
        event['details']['token'] = PRIVATE
        event['before']['party'][0]['secret'] = PRIVATE
        before = copy.deepcopy(live)
        self.assertNotIn(PRIVATE, json.dumps(public_journal(live)))
        self.assertEqual(before, live)

    def test_absent_invalid_and_future_journals_are_explicit_and_never_overwritten(self):
        live = state()
        absent = public_journal(live)
        self.assertFalse(absent['available'])
        self.assertFalse(absent['complete_history'])
        self.assertIsNone(absent['from_node'])
        self.assertEqual('not_recorded', absent['reason'])
        for saved, reason in [(None, 'invalid'), ({'version': 2, 'secret': PRIVATE}, 'unsupported_version'),
                              ({'version': 1, 'events': [], 'token': PRIVATE}, 'invalid')]:
            with self.subTest(reason=reason):
                live['journal'] = copy.deepcopy(saved)
                before = copy.deepcopy(live)
                public = public_journal(live)
                self.assertFalse(public['available'])
                self.assertEqual(reason, public['reason'])
                self.assertNotIn(PRIVATE, json.dumps(public))
                self.assertFalse(record_event(live, 'rest', snapshot(live), {'option': 'heal'}))
                self.assertEqual(before, live)

    def test_invalid_event_input_does_not_partially_update_the_journal(self):
        live = state()
        record_event(live, 'created', None)
        before = copy.deepcopy(live)
        cases = [('unknown', snapshot(live), {}), ('rest', None, {'option': 'heal'}),
                 ('rest', snapshot(live), {'option': 'invalid'}),
                 ('battle_end', snapshot(live), {'winner': 'p1'}),
                 ('battle_end', snapshot(live), {'winner': 'p1', 'turns': -1}),
                 ('recruit', snapshot(live), {'candidate_id': 'skip'}),
                 ('party_order', snapshot(live), {'order': [0, 0, 2]})]
        for kind, measured, details in cases:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                record_event(live, kind, measured, details)
            self.assertEqual(before, live)

    def test_malformed_existing_counters_and_sequences_are_preserved_as_unavailable(self):
        for corrupt in [lambda j: j['counts'].update(created=2),
                        lambda j: j.update(dropped_events=99),
                        lambda j: j['events'][0].update(sequence=2),
                        lambda j: j['events'][0]['before'].update(hp=999),
                        lambda j: j.update(version=True)]:
            with self.subTest(corrupt=corrupt):
                live = state()
                record_event(live, 'created', None)
                corrupt(live['journal'])
                before = copy.deepcopy(live['journal'])
                self.assertFalse(public_journal(live)['available'])
                self.assertFalse(record_event(live, 'rest', snapshot(live), {'option': 'heal'}))
                self.assertEqual(before, live['journal'])

    def test_text_detail_sizes_are_bounded_and_control_characters_removed(self):
        live = state()
        record_event(live, 'reward', snapshot(live), {'label': 'A\n' * 200, 'reward_id': 'i' * 200})
        details = public_journal(live)['events'][0]['details']
        self.assertEqual('A' * 100, details['label'])
        self.assertEqual('i' * 128, details['reward_id'])


if __name__ == '__main__':
    unittest.main()
