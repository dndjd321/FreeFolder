"""Fixed public-policy choices and full RunService measurement boundaries."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balance.cli import (DEFAULT_SEEDS, PUBLIC_POLICY, choose_operation, main, recommended_team,
                         run_benchmark, write_report)
from battle_service.client import BattleServiceError
from services.run_service import RunService
from test_run_service import FakeBattleClient


def public_run(**changes):
    run = {'phase': 'node', 'node': {'kind': 'battle'}, 'money': 100,
           'party': [{'species': species, 'resource': {'hp': 100, 'maxhp': 100, 'status': '',
                      'moves': [{'pp': 10, 'maxpp': 20}]}} for species in ('One', 'Two', 'Three')],
           'rest_options': [{'id': kind, 'available': True} for kind in ('revive', 'pp', 'cure', 'heal')],
           'shop': [], 'rewards': [], 'paths': [], 'recruit_candidates': []}
    run.update(changes)
    return run


class SharedFakeClient(FakeBattleClient):
    def __init__(self):
        super().__init__()
        self._closed = False
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self._closed = True
        super().close()


class PublicEconomyPolicyTests(unittest.TestCase):
    def choose(self, run):
        return choose_operation(run, None, 1)

    def test_frozen_default_cohort_team_and_policy_are_explicit(self):
        self.assertEqual(tuple(f'run-balance-v1-{index:03d}' for index in range(20)), DEFAULT_SEEDS)
        self.assertEqual(['Charizard', 'Garchomp', 'Clefable'], [row['species'] for row in recommended_team()])
        self.assertEqual(('public-economy-v1', .65, .35, 40),
                         tuple(PUBLIC_POLICY[key] for key in ('version', 'hp_needed_below', 'pp_needed_below', 'relic_gold_reserve')))

    def test_reward_uses_dead_recruit_then_needed_resources_then_relic_and_money(self):
        run = public_run(phase='reward', rewards=[{'id': kind, 'kind': kind} for kind in ('money', 'relic', 'pp', 'heal', 'recruit')])
        self.assertEqual(('reward', ('relic',)), self.choose(run))
        run['party'][0]['resource']['hp'] = 0
        self.assertEqual(('reward', ('recruit',)), self.choose(run))
        run['rewards'] = [row for row in run['rewards'] if row['kind'] != 'recruit']
        for row in run['party']:
            row['resource']['hp'] = 40
        self.assertEqual(('reward', ('heal',)), self.choose(run))
        for row in run['party']:
            row['resource'].update(hp=100)
            row['resource']['moves'][0]['pp'] = 1
        self.assertEqual(('reward', ('pp',)), self.choose(run))

    def test_rest_priority_is_revive_pp_cure_heal_and_checks_available_choices(self):
        run = public_run(node={'kind': 'rest'})
        run['party'][0]['resource'].update(hp=0, status='slp')
        run['party'][1]['resource'].update(status='brn')
        run['party'][1]['resource']['moves'][0]['pp'] = 0
        self.assertEqual(('rest', ('revive',)), self.choose(run))
        run['rest_options'][0]['available'] = False
        self.assertEqual(('rest', ('pp',)), self.choose(run))
        run['party'][1]['resource']['moves'][0]['pp'] = 20
        self.assertEqual(('rest', ('cure',)), self.choose(run))
        run['party'][1]['resource']['status'] = ''
        self.assertEqual(('rest', ('heal',)), self.choose(run))

    def test_shop_checks_affordability_sold_items_and_cash_reserve_then_leaves(self):
        run = public_run(node={'kind': 'shop'}, money=60,
                         shop=[{'id': kind, 'kind': kind, 'price': price} for kind, price in
                               [('revive', 40), ('recruit', 35), ('heal', 25), ('pp', 25), ('relic', 45)]])
        self.assertEqual(('shop', ('leave',)), self.choose(run))
        run['party'][0]['resource']['hp'] = 0
        self.assertEqual(('shop', ('revive',)), self.choose(run))
        run['shop'][0]['bought'] = True
        self.assertEqual(('shop', ('recruit',)), self.choose(run))
        run['party'][0]['resource']['hp'] = 100
        run['money'] = 85
        self.assertEqual(('shop', ('relic',)), self.choose(run))
        run['shop'][-1]['sold'] = True
        self.assertEqual(('shop', ('leave',)), self.choose(run))

    def test_recruit_replaces_lowest_absolute_hp_and_path_uses_only_first_offered(self):
        run = public_run(phase='recruit', recruit_candidates=[{'id': 'first'}, {'id': 'second'}])
        run['party'][0]['resource'].update(hp=50, maxhp=200)
        run['party'][1]['resource'].update(hp=40, maxhp=80)
        self.assertEqual(('recruit', ('first', 1)), self.choose(run))
        run.update(phase='path', paths=[{'id': 'first-path'}, {'id': 'second-path'}], private_enemy={'strongest': 'first-path'})
        before = copy.deepcopy(run)
        self.assertEqual(('path', ('first-path',)), self.choose(run))
        self.assertEqual(before, run)

    def test_battle_policy_receives_exactly_the_public_p1_view(self):
        class Policy:
            def predict(self, request, view):
                self.request, self.view = request, view
                return {'type': 'move', 'index': 0}
        view = {'side': 'p1', 'request': {'active': [{}]}, 'own': [], 'opponent': [{'revealed': False}]}
        run = public_run(phase='battle', battle={'revision': 4, 'view': view}, secret_enemy_team='unavailable')
        policy = Policy()
        operation, args = choose_operation(run, policy, 7)
        self.assertEqual(('battle/action', ({'type': 'move', 'index': 0}, 4, 'run-balance-v1-7')), (operation, args))
        self.assertIs(view, policy.view)
        self.assertIs(view['request'], policy.request)


class BalanceMeasurementTests(unittest.TestCase):
    def require_journal_integration(self):
        client = SharedFakeClient()
        service = RunService(':memory:', client_factory=lambda: client, owns_client=False)
        try:
            created = service.create(recommended_team(), seed='journal-contract-probe')
            self.assertIs(created.get('journal', {}).get('available'), True,
                          '9b journal service integration is required for legal full-run measurement tests')
        finally:
            service.close()
            client.close()

    def measure(self, client, **kwargs):
        return run_benchmark(seeds=['small-contract-seed'], modes=['normal'],
                             client_factory=lambda: client, **kwargs)

    def test_missing_journal_is_an_error_not_a_loss_and_stops_the_batch(self):
        class MissingJournalService(RunService):
            def create(self, *args, **kwargs):
                result = super().create(*args, **kwargs)
                result.pop('journal', None)
                return result
        client = SharedFakeClient()
        report = run_benchmark(seeds=['first', 'not-started'], modes=['normal'],
                               client_factory=lambda: client, service_factory=MissingJournalService)
        self.assertEqual((0, 0, 1, 1, 2), tuple(report['summary'][key] for key in
                                              ('valid_cases', 'failed', 'error', 'not_run', 'incomplete_cases')))
        self.assertEqual('JOURNAL_UNAVAILABLE', report['cases'][0]['error']['code'])
        self.assertEqual(1, report['exit_code'])
        self.assertEqual(1, client.close_calls)
        self.assertEqual({}, client.battles)

    def test_engine_error_keeps_diagnostics_without_leaking_error_message_or_counting_loss(self):
        class BrokenClient(SharedFakeClient):
            def create(self, *args, **kwargs):
                raise BattleServiceError('private-token-and-checkpoint', 'WORKER_CLOSED')
        client = BrokenClient()
        report = self.measure(client)
        self.assertEqual((0, 1, 0), tuple(report['summary'][key] for key in ('failed', 'error', 'valid_cases')))
        self.assertEqual({'type': 'BattleServiceError', 'code': 'WORKER_CLOSED'}, report['cases'][0]['error'])
        self.assertNotIn('private-token-and-checkpoint', json.dumps(report))
        self.assertEqual(1, report['exit_code'])
        self.assertEqual(1, client.close_calls)

    def test_public_service_full_flow_completes_both_modes_and_cleans_each_memory_service(self):
        self.require_journal_integration()
        client, services = SharedFakeClient(), []
        def factory(*args, **kwargs):
            service = RunService(*args, **kwargs)
            services.append(service)
            return service
        report = run_benchmark(seeds=['small-contract-seed'], modes=['normal', 'hard'],
                               client_factory=lambda: client, service_factory=factory)
        self.assertEqual(0, report['exit_code'])
        self.assertEqual((2, 2, 0), tuple(report['summary'][key] for key in ('complete', 'valid_cases', 'incomplete_cases')))
        self.assertTrue(all(service.closed for service in services))
        self.assertEqual(1, client.close_calls)
        self.assertEqual({}, client.battles)
        for case in report['cases']:
            self.assertEqual(20, case['completed_nodes'])
            self.assertEqual(list(range(20)), case['reached_node_indexes'])
            self.assertEqual(list(range(1, case['journal']['total_events'] + 1)), [event['sequence'] for event in case['events']])
            self.assertEqual(case['journal']['counts']['battle_end'], case['journal']['totals']['battles_finished'])
        self.assertNotIn('token', json.dumps(report['cases']))

    def test_decision_and_battle_turn_cutoffs_are_incomplete_and_never_losses(self):
        self.require_journal_integration()
        for limits, reason in [({'max_decisions': 1}, 'max_decisions'),
                               ({'max_battle_turns': 1}, 'max_battle_turns')]:
            with self.subTest(reason=reason):
                client = SharedFakeClient()
                client.turns_to_win = 50
                report = self.measure(client, **limits)
                self.assertEqual((0, 0, 1, 0), tuple(report['summary'][key] for key in ('valid_cases', 'failed', 'cutoff', 'error')))
                self.assertEqual(reason, report['cases'][0]['cutoff_reason'])
                self.assertEqual(1, report['exit_code'])
                self.assertEqual({}, client.battles)

    def test_actual_loss_is_a_valid_measured_outcome_not_an_engine_failure(self):
        self.require_journal_integration()
        client = SharedFakeClient()
        client.next_winner = 'p2'
        report = self.measure(client)
        self.assertEqual((1, 1, 0, 0), tuple(report['summary'][key] for key in ('valid_cases', 'failed', 'cutoff', 'error')))
        self.assertEqual(0, report['exit_code'])
        self.assertEqual(0, report['cases'][0]['failure_node_index'])
        self.assertEqual(1, report['cases'][0]['journal']['totals']['losses'])

    def test_same_seed_repeats_the_same_choices_resources_and_journal(self):
        self.require_journal_integration()
        first, second = self.measure(SharedFakeClient()), self.measure(SharedFakeClient())
        for key in ('events', 'nodes', 'journal', 'decisions', 'status', 'reached_node_indexes'):
            self.assertEqual(first['cases'][0][key], second['cases'][0][key], key)
        self.assertEqual(first['metadata']['policy_sha256'], second['metadata']['policy_sha256'])

    def test_report_is_exclusive_and_cli_existing_report_exits_before_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            write_report(path, {'original': True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_report(path, {'replacement': True})
            with patch('balance.cli.run_benchmark') as measure, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(['--report', str(path)])
            self.assertEqual(2, result.exception.code)
            measure.assert_not_called()
            self.assertEqual(original, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
