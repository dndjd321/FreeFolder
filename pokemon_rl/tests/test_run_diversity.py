"""Cohort, public trace boundaries and cleanup for the frozen diversity suite."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from balance import diversity
from balance.cli import PUBLIC_POLICY, _digest, run_case, write_report
from battle_service.client import BattleServiceError
from services.run_service import RunService
from test_run_balance import SharedFakeClient


def public_battle():
    own = [{'slot': index, 'active': index == 0, 'hp': 75, 'maxhp': 100,
            'moves': [{'id': 'roost' if index == 0 else 'tackle', 'pp': 10, 'maxpp': 20}]}
           for index in range(3)]
    return {'phase': 'battle', 'node_index': 0, 'token': 'PRIVATE-TOKEN', 'run_id': 'PRIVATE-RUN',
            'battle': {'revision': 9, 'battle_id': 'PRIVATE-BATTLE', 'view': {
                'side': 'p1', 'turn': 3, 'own': own, 'opponent': [{
                    'species': 'Pelipper', 'types': ['water', 'flying'], 'active': True, 'revealed': True,
                    'ability': 'PRIVATE-ENEMY', 'moves': ['PRIVATE-MOVE'], 'item': 'PRIVATE-ITEM'}],
                'events': ['|-heal|p1a: Owner|75/100|[from] item: Leftovers'],
                'request': {'active': [{'moves': [{'id': 'roost', 'pp': 10}]}]}}},
            'party': [{'resource': {'hp': 1, 'maxhp': 100, 'moves': []}}]}


class PublicService:
    def __init__(self, state):
        self.state, self.calls, self.closed = state, [], False

    def create(self, *args, **kwargs):
        return copy.deepcopy(self.state)

    def perform(self, *args):
        self.calls.append(args)
        return copy.deepcopy(self.state)

    def resume(self, *args):
        return copy.deepcopy(self.state)

    def close(self):
        self.closed = True


class EnrichedFakeClient(SharedFakeClient):
    """A legal public view for both policies, while the original fixture stays intact."""
    def view(self, bid, side):
        result = super().view(bid, side)
        for index, own in enumerate(result['own']):
            own.update(active=index == 0, fainted=own['hp'] <= 0, name=f'Owner{index}',
                       types=['normal'], stats={'atk': 100, 'spa': 100}, boosts={}, ability='')
            for move in own['moves']:
                move.update(basePower=80, category='physical', type='normal', accuracy=100)
        return result


class DiversityManifestTests(unittest.TestCase):
    def test_exact_cohort_order_builds_and_independent_hashes_are_frozen(self):
        plan = diversity.case_plan()
        self.assertEqual(120, len(plan))
        self.assertEqual(120, len({row['case_id'] for row in plan}))
        self.assertEqual(('run-diversity-v1-000', 'run-diversity-v1-009'),
                         (plan[0]['seed'], plan[-1]['seed']))
        self.assertEqual([('recommended-v1', 'public-heuristic-v2', 'normal'),
                          ('recommended-v1', 'public-heuristic-v2', 'hard'),
                          ('recommended-v1', 'public-sustain-v1', 'normal'),
                          ('recommended-v1', 'public-sustain-v1', 'hard'),
                          ('fixture-sustain-v1', 'public-heuristic-v2', 'normal')],
                         [(row['team_id'], row['policy_id'], row['difficulty']) for row in plan[:5]])
        frozen = diversity.manifest()
        builds = {row['id']: row for row in frozen['teams']}
        self.assertEqual(['Charizard', 'Garchomp', 'Clefable'],
                         [row['species'] for row in builds['recommended-v1']['team']])
        fixture = builds['fixture-sustain-v1']['team']
        self.assertEqual({'atk': 252, 'spe': 252, 'spd': 4}, fixture[0]['evs'])
        self.assertEqual(['Earthquake', 'Dragon Claw', 'Poison Jab', 'Swords Dance'], fixture[0]['moves'])
        self.assertEqual(['Surf', 'Ice Beam', 'Protect', 'Wish'], fixture[1]['moves'])
        self.assertEqual(['Bullet Punch', 'Swords Dance', 'Knock Off', 'Roost'], fixture[2]['moves'])
        self.assertEqual(['Rotom-Wash', 'Scizor', 'Togekiss'],
                         [row['species'] for row in builds['curated-coverage-v1']['team']])
        for row in builds.values():
            self.assertEqual(_digest(row['team']), row['team_sha256'])
        self.assertEqual({'hp': 31, 'atk': 31, 'def': 31, 'spa': 31, 'spd': 31, 'spe': 31}, frozen['default_ivs'])
        self.assertEqual('normal-rescue-v2', frozen['balance_profile'])
        self.assertEqual('f53a0323a3f4c66386f4629e5e64a7933f921dcb4eaf3350facb96a53884b4b7',
                         frozen['economy']['legacy_public_policy_sha256'])
        self.assertNotIn('battle', frozen['economy']['config'])
        self.assertEqual(_digest(frozen), _digest(diversity.manifest()))
        fixture[0]['moves'][0] = 'MUTATED'
        self.assertEqual('Earthquake', diversity.teams()['fixture-sustain-v1'][0]['moves'][0])

    def test_invalid_seeds_limits_and_changed_version_fail_before_engine_start(self):
        factory = Mock()
        for changes in ({'seeds': []}, {'seeds': ['same', 'same']}, {'seeds': ['']},
                        {'max_decisions': 1001}, {'max_battle_turns': 201}, {'max_decisions': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                diversity.run_benchmark(client_factory=factory, **changes)
        with patch.object(diversity, 'SUSTAIN_VERSION', 'changed'), self.assertRaises(ValueError):
            diversity.run_benchmark(client_factory=factory)
        factory.assert_not_called()


class DiversityTraceTests(unittest.TestCase):
    def test_trace_uses_current_battle_resources_allowlist_and_deduplicates_heals(self):
        original = public_battle()
        service = PublicService(original)
        policy = Mock(last_reason='heal')
        trace = diversity.PublicTraceService(service, policy, max_rows=4)
        trace.create([], seed='public-seed')
        self.assertEqual(1, trace.summary()['public_p1_heal_events'])
        result = trace.perform('PRIVATE-RUN', 'PRIVATE-TOKEN', 'battle/action', 9,
                               {'type': 'move', 'index': 0}, 42, 'PRIVATE-COMMAND')
        self.assertEqual(75, trace.rows[0]['before']['party'][0]['hp'])
        self.assertEqual('battle-own', trace.rows[0]['before']['source'])
        self.assertEqual({'type': 'move', 'index': 0}, trace.rows[0]['action'])
        self.assertEqual({'species': 'Pelipper', 'types': ['water', 'flying']}, trace.rows[0]['public_enemy'])
        self.assertEqual((0, 1), (trace.rows[0]['new_public_p1_heal_events'], trace.summary()['recovery_move_choices']))
        self.assertEqual(original, result)
        service.state['battle']['view']['events'] += [
            '|-heal|p2a: Opponent|100/100|[from] item: PRIVATE-ITEM',
            '|-heal|p1a: Owner|100/100|[from] move: Roost']
        trace.resume('PRIVATE-RUN', 'PRIVATE-TOKEN')
        trace.resume('PRIVATE-RUN', 'PRIVATE-TOKEN')
        service.state['battle']['view']['events'].append('|-heal|p1a: Owner|100/100|[from] move: Roost')
        trace.resume('PRIVATE-RUN', 'PRIVATE-TOKEN')
        self.assertEqual([0, 1, 0, 1], [row['new_public_p1_heal_events'] for row in trace.rows])
        self.assertEqual(3, trace.summary()['public_p1_heal_events'])
        encoded = json.dumps(trace.rows)
        for secret in ('PRIVATE', 'revision', 'checkpoint', 'events', 'request', 'ability'):
            # The heal-count key is intentionally an aggregate, never raw events.
            if secret == 'events':
                self.assertNotIn('"events":', encoded)
            else:
                self.assertNotIn(secret, encoded)
        with self.assertRaises(diversity.TraceError):
            trace.resume('PRIVATE-RUN', 'PRIVATE-TOKEN')

    def test_illegal_action_and_uncontrolled_reason_never_reach_service_or_report(self):
        service = PublicService(public_battle())
        trace = diversity.PublicTraceService(service, Mock(last_reason=['PRIVATE-REASON']))
        trace.create([])
        with self.assertRaises(diversity.TraceError):
            trace.perform('a', 'b', 'battle/action', 0, {'type': 'move', 'index': 3}, 'PRIVATE-ARG')
        self.assertEqual([], service.calls)
        self.assertEqual('ILLEGAL_POLICY_ACTION', trace.rows[0]['error']['code'])
        self.assertEqual('unspecified', trace.rows[0]['reason'])
        self.assertNotIn('PRIVATE', json.dumps(trace.rows))

    def test_forced_switch_original_slot_and_unrevealed_enemy_boundary(self):
        state = public_battle()
        state['battle']['view']['request'] = {'forceSwitch': [True]}
        state['battle']['view']['own'][0]['hp'] = 0
        state['battle']['view']['own'] = list(reversed(state['battle']['view']['own']))
        state['battle']['view']['opponent'][0].update(revealed=False)
        service = PublicService(state)
        trace = diversity.PublicTraceService(service, Mock(last_reason='forced-switch'))
        trace.create([])
        trace.perform('a', 'b', 'battle/action', 0, {'type': 'switch', 'index': 2})
        self.assertEqual([{'type': 'switch', 'index': 1}, {'type': 'switch', 'index': 2}], trace.rows[0]['legal_choices'])
        self.assertIsNone(trace.rows[0]['public_enemy'])

    def test_public_offered_ids_and_run_party_boundary_without_credentials(self):
        state = {'phase': 'recruit', 'node_index': 3, 'token': 'PRIVATE',
                 'party': [{'resource': {'hp': 12, 'maxhp': 100, 'moves': [{'id': 'surf', 'pp': 2, 'maxpp': 15}]}}],
                 'recruit_candidates': [{'id': 'candidate-1', 'team': 'PRIVATE-BUILD'}]}
        service = PublicService(state)
        trace = diversity.PublicTraceService(service, None)
        trace.create([])
        trace.perform('PRIVATE', 'PRIVATE', 'recruit', 0, 'candidate-1', 0)
        row = trace.rows[0]
        self.assertEqual(('candidate-1', 0, ['candidate-1', 'skip']),
                         (row['offered_id'], row['replace_slot'], row['offered_ids']))
        self.assertEqual('run-party', row['before']['source'])
        self.assertNotIn('PRIVATE', json.dumps(trace.rows))
        with self.assertRaises(diversity.TraceError):
            trace.perform('a', 'b', 'recruit', 0, 'not-offered', 0)

    def test_p2_view_and_shortened_public_event_history_are_errors(self):
        for change in ('side', 'events'):
            state = public_battle()
            service = PublicService(state)
            trace = diversity.PublicTraceService(service, None)
            trace.create([])
            service.state['battle']['view'][change] = 'p2' if change == 'side' else []
            with self.subTest(change=change), self.assertRaises(diversity.TraceError):
                trace.resume('a', 'b')

    def test_recovery_choice_metric_counts_only_the_six_frozen_self_recovery_ids(self):
        self.assertEqual({'roost', 'moonlight', 'morningsun', 'synthesis', 'rest', 'wish'}, set(diversity.RECOVERY_MOVES))
        for move_id in (*diversity.RECOVERY_MOVES, 'protect', 'gigadrain', 'leechseed'):
            state = public_battle()
            state['battle']['view']['request']['active'][0]['moves'][0]['id'] = move_id
            trace = diversity.PublicTraceService(PublicService(state), Mock(last_reason='damage'))
            trace.create([])
            trace.perform('a', 'b', 'battle/action', 0, {'type': 'move', 'index': 0})
            with self.subTest(move=move_id):
                self.assertEqual(int(move_id in diversity.RECOVERY_MOVES), trace.summary()['recovery_move_choices'])

    def test_terminal_input_marks_missing_final_events_without_inventing_healing(self):
        service = PublicService(public_battle())
        trace = diversity.PublicTraceService(service, Mock(last_reason='heal'))
        trace.create([])
        # Real RunService can finish directly into reward/failed without a final view.
        service.state = {'phase': 'reward', 'node_index': 0, 'party': [
            {'resource': {'hp': 90, 'maxhp': 100, 'moves': [{'id': 'roost', 'pp': 9, 'maxpp': 20}]}}]}
        trace.perform('a', 'b', 'battle/action', 0, {'type': 'move', 'index': 0})
        row, totals = trace.rows[0], trace.summary()
        self.assertTrue(row['terminal_battle_events_unavailable'])
        self.assertEqual(1, totals['terminal_events_unavailable'])
        self.assertIn('lower bound', totals['heal_event_coverage'])
        self.assertEqual(0, row['new_public_p1_heal_events'])
        self.assertEqual(1, totals['public_p1_heal_events'])
        self.assertEqual(1, totals['recovery_move_choices'])
        self.assertEqual(90, row['after']['party'][0]['hp'])


class DiversityRunnerTests(unittest.TestCase):
    def measure(self, client, **kwargs):
        with patch.object(diversity, '_node_version', return_value='v-test'), patch.object(
                diversity, '_freeze_metadata', return_value={'freeze_ready': True}):
            return diversity.run_benchmark(seeds=['diversity-contract-only'],
                                           client_factory=lambda: client, **kwargs)

    def test_fake_full_suite_fresh_policies_and_services_keep_shared_engine_clean(self):
        client, services, policies = EnrichedFakeClient(), [], []
        original_policy = diversity._policy
        def policy_factory(key):
            policy = original_policy(key)
            policies.append(policy)
            return policy
        def service_factory(*args, **kwargs):
            self.assertEqual(':memory:', args[0])
            self.assertEqual(False, kwargs['owns_client'])
            self.assertEqual('normal-rescue-v2', kwargs['new_run_balance_profile'])
            service = RunService(*args, **kwargs)
            services.append(service)
            return service
        original_economy = copy.deepcopy(PUBLIC_POLICY)
        with patch.object(diversity, '_policy', side_effect=policy_factory):
            report = self.measure(client, service_factory=service_factory)
        self.assertEqual((12, 12, 0), tuple(report['summary'][key] for key in ('expected_cases', 'complete', 'incomplete_cases')))
        self.assertEqual(12, len({id(policy) for policy in policies}))
        self.assertTrue(all(service.closed for service in services))
        self.assertEqual(1, client.close_calls)
        self.assertEqual({}, client.battles)
        self.assertEqual(original_economy, PUBLIC_POLICY)
        self.assertEqual(0, report['exit_code'])
        self.assertEqual(12, len(report['summary']['groups']))
        self.assertGreater(report['summary']['terminal_events_unavailable'], 0)
        self.assertIn('lower bound', report['summary']['heal_event_coverage'])
        self.assertEqual(sum(case['trace_summary']['terminal_events_unavailable'] for case in report['cases']),
                         report['summary']['terminal_events_unavailable'])
        for case in report['cases']:
            self.assertEqual(20, case['completed_nodes'])
            self.assertEqual(case['decisions'], len(case['decision_trace']))
            self.assertLessEqual(len(case['decision_trace']), 1000)
            self.assertEqual('normal-rescue-v2', case['balance_profile'])
        encoded = json.dumps(report)
        for key in ('"token"', '"checkpoint"', '"battle_id"', '"run_id"', '"request"'):
            self.assertNotIn(key, encoded)

    def test_error_stops_remaining_cases_and_sanitizes_exception_type_code_message(self):
        class BrokenClient(EnrichedFakeClient):
            def create(self, *args, **kwargs):
                raise BattleServiceError('PRIVATE-MESSAGE', 'PRIVATE-CODE')
        client = BrokenClient()
        report = self.measure(client)
        self.assertEqual((1, 11, 0), tuple(report['summary'][key] for key in ('error', 'not_run', 'failed')))
        self.assertEqual({'type': 'BattleServiceError', 'code': 'RUN_ERROR'}, report['cases'][0]['error'])
        self.assertNotIn('PRIVATE', json.dumps(report))

        class BrokenCloseClient(EnrichedFakeClient):
            def close(self):
                super().close()
                raise RuntimeError('PRIVATE-WORKER-CLOSE')
        client = BrokenCloseClient()
        report = self.measure(client, max_decisions=1)
        self.assertEqual('CLEANUP_ERROR', report['batch_error']['code'])
        self.assertEqual(1, report['summary']['batch_errors'])
        self.assertEqual(1, client.close_calls)
        self.assertEqual({}, client.battles)
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertEqual(1, client.close_calls)
        self.assertEqual('Exception', diversity._error({'type': 'PRIVATE-TYPE', 'code': []})['type'])

    def test_wrong_side_response_is_rejected_before_copying_private_own_fields(self):
        class WrongSideService(RunService):
            def perform(self, *args, **kwargs):
                result = super().perform(*args, **kwargs)
                if result['phase'] == 'battle':
                    view = result['battle']['view']
                    view['side'] = 'p2'
                    view['own'][0]['moves'][0]['id'] = 'PRIVATE-OPPONENT-MOVE'
                    view['own'][0]['hp'] = 987654321
                return result
        report = self.measure(EnrichedFakeClient(), service_factory=WrongSideService)
        self.assertEqual(1, report['summary']['error'])
        self.assertEqual('TRACE_ERROR', report['cases'][0]['error']['code'])
        self.assertIsNone(report['cases'][0]['decision_trace'][0]['after'])
        encoded = json.dumps(report)
        self.assertNotIn('PRIVATE', encoded)
        self.assertNotIn('987654321', encoded)

    def test_cutoff_continues_and_is_not_a_loss_while_real_loss_is_valid(self):
        client = EnrichedFakeClient()
        report = self.measure(client, max_decisions=1)
        self.assertEqual((12, 0, 0), tuple(report['summary'][key] for key in ('cutoff', 'failed', 'valid_cases')))
        self.assertEqual(1, report['exit_code'])
        self.assertEqual({}, client.battles)
        losing = EnrichedFakeClient()
        losing.next_winner = 'p2'
        report = self.measure(losing)
        self.assertEqual((12, 12, 0), tuple(report['summary'][key] for key in ('failed', 'valid_cases', 'error')))
        self.assertEqual(0, report['exit_code'])

    def test_cleanup_failures_and_failed_engine_start_are_reported_and_always_close_engine(self):
        class BrokenCloseService(RunService):
            def close(self):
                super().close()
                raise RuntimeError('PRIVATE-CLOSE')
        client = EnrichedFakeClient()
        report = self.measure(client, max_decisions=1, service_factory=BrokenCloseService)
        self.assertEqual('CLEANUP_ERROR', report['cases'][0]['error']['code'])
        self.assertEqual(11, report['summary']['not_run'])
        self.assertEqual(1, client.close_calls)
        self.assertEqual({}, client.battles)
        with patch.object(diversity, '_node_version', return_value=None), patch.object(
                diversity, '_freeze_metadata', return_value={'freeze_ready': True}):
            report = diversity.run_benchmark(seeds=['startup-smoke'], client_factory=Mock(side_effect=RuntimeError('PRIVATE-START')))
        self.assertEqual('ENGINE_START_ERROR', report['batch_error']['code'])
        self.assertEqual(12, report['summary']['not_run'])
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_clean_freeze_is_required_before_engine_start_when_requested(self):
        factory = Mock()
        with patch.object(diversity, '_freeze_metadata', return_value={'freeze_ready': False}), self.assertRaises(ValueError):
            diversity.run_benchmark(seeds=['smoke'], client_factory=factory, require_clean=True)
        factory.assert_not_called()

    def test_manifest_covers_behavior_sources_protocol_and_runtime_versions(self):
        report = self.measure(EnrichedFakeClient(), max_decisions=1)
        metadata = report['metadata']
        for name in ('pokemon_rl/services/engine_api.py', 'pokemon_rl/services/run_preparation.py',
                     'pokemon_rl/balance/policies.py', 'pokemon_rl/balance/diversity.py',
                     'pokemon_rl/battle_service/worker.cjs', 'pokemon_rl/data/pokemon.json', 'pokemon_rl/data/moves.json',
                     'docs/RUN_DIVERSITY_PROTOCOL.md'):
            self.assertIn(name, metadata['file_sha256'])
        self.assertRegex(metadata['file_sha256']['pokemon_rl/balance/diversity.py'], r'^[a-f0-9]{64}$')
        self.assertEqual(_digest(metadata['manifest']), metadata['manifest_sha256'])
        self.assertTrue(metadata['python_version'])
        self.assertTrue(metadata['numpy_version'])
        self.assertEqual('v-test', metadata['node_version'])

    def test_cli_smoke_and_exclusive_report_guard_preserve_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            with patch.object(diversity, 'run_benchmark', return_value={'summary': {}, 'exit_code': 0}) as measure:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, diversity.main(['--report', str(path), '--seeds', 'smoke-only']))
            self.assertEqual(['smoke-only'], measure.call_args.kwargs['seeds'])
            self.assertFalse(measure.call_args.kwargs['require_clean'])
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_report(path, {'replacement': True})
            with patch.object(diversity, 'run_benchmark') as measure, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    diversity.main(['--report', str(path)])
            measure.assert_not_called()
            self.assertEqual(before, path.read_bytes())


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install locked battle service dependencies for the separate real smoke cohort')
class DiversityRealSmokeTests(unittest.TestCase):
    def test_separate_smoke_seed_repeats_legal_choices_resources_journal_and_trace(self):
        # Twelve bounded smoke cases, repeated. This is not the frozen 120-run measurement.
        options = {'seeds': ['run-diversity-smoke-v1-contract'], 'max_decisions': 6}
        first, second = diversity.run_benchmark(**options), diversity.run_benchmark(**options)
        self.assertEqual(12, len(first['cases']))
        self.assertEqual(0, first['summary']['error'])
        self.assertEqual(0, first['summary']['not_run'])
        for a, b in zip(first['cases'], second['cases']):
            for key in ('case_id', 'status', 'decisions', 'policy_calls', 'events', 'nodes', 'journal',
                        'decision_trace', 'trace_summary', 'completed_nodes'):
                self.assertEqual(a[key], b[key], (a['case_id'], key))
            for row in a['decision_trace']:
                if row['operation'] == 'battle/action':
                    self.assertIn(row['action'], row['legal_choices'])


if __name__ == '__main__':
    unittest.main()
