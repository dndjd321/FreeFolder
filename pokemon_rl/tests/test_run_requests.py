"""Run-wide request versions prevent stale tabs from spending new resources."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from services.engine_api import default_opponents
from services.run_api import install_run_routes
from services.run_service import RunService, RunStaleError, SaveStorageError
from test_engine_api import http
from test_run_service import FakeBattleClient


class RunRequestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'requests.sqlite3'
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client)
        self.app = FastAPI()
        install_run_routes(self.app, self.service)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def create(self):
        return self.service.create(default_opponents(), seed='request-versions')

    def perform(self, run, operation, *args):
        return self.service.perform(run['run_id'], run['token'], operation, run['run_revision'], *args)

    def encoded(self, run):
        return self.service.db.execute('SELECT state FROM runs WHERE id=?', (run['run_id'],)).fetchone()[0]

    def post(self, run, operation, body):
        return asyncio.run(http(self.app, 'POST', '/api/runs/' + run['run_id'] + '/' + operation,
                                body, headers=[(b'authorization', ('Bearer ' + run['token']).encode())]))

    def reward(self, *, extra_reroll=False):
        run = self.create()
        if extra_reroll:
            state = self.service._load(run['run_id'], run['token'])
            state['relics']['spare-die'] = 1
            self.service._save(state)
            run = self.service.get(run['run_id'], run['token'])
        run = self.perform(run, 'battle/start')
        return self.perform(run, 'battle/action', {'type': 'move', 'index': 0}, 0, 'first-battle')

    def until_node(self, run, index):
        sequence = 0
        while not (run['phase'] == 'node' and run['node_index'] == index):
            sequence += 1
            self.assertLess(sequence, 100)
            rid, token = run['run_id'], run['token']
            if run['phase'] == 'node':
                kind = run['node']['kind']
                run = (self.service.rest(rid, token, 'heal') if kind == 'rest' else
                       self.service.shop(rid, token, 'leave') if kind == 'shop' else
                       self.service.battle_start(rid, token))
            elif run['phase'] == 'battle':
                run = self.service.battle_action(rid, token, {'type': 'move', 'index': 0},
                                                run['battle']['revision'], 'advance-' + str(sequence))
            elif run['phase'] == 'reward':
                choice = next(r for r in run['rewards'] if r['kind'] != 'recruit')
                run = self.service.choose_reward(rid, token, choice['id'])
            elif run['phase'] == 'path':
                run = self.service.choose_path(rid, token, run['paths'][0]['id'])
            else:
                self.fail('Unexpected fixture phase: ' + run['phase'])
        return run

    def test_new_and_legacy_runs_expose_the_committed_run_revision(self):
        run = self.create()
        self.assertEqual(0, run['run_revision'])
        state = json.loads(self.encoded(run))
        self.assertEqual(0, state.pop('run_revision'))
        with self.service.db:
            self.service.db.execute('UPDATE runs SET state=? WHERE id=?', (json.dumps(state), run['run_id']))
        legacy = self.service.get(run['run_id'], run['token'])
        self.assertEqual(0, legacy['run_revision'])
        self.assertNotIn('run_revision', json.loads(self.encoded(run)))
        started = self.perform(legacy, 'battle/start')
        self.assertEqual(1, started['run_revision'])
        self.assertEqual(started['run_revision'], json.loads(self.encoded(run))['run_revision'])
        self.assertEqual(1, started['schema_version'])

    def test_every_http_mutation_requires_a_nonnegative_integer_run_revision(self):
        run = self.create()
        before = self.encoded(run)
        for operation in ('battle/start', 'battle/action', 'battle/finish', 'reward',
                          'reroll', 'path', 'rest', 'shop', 'recruit'):
            for revision in (None, True, -1, '0', 1.5, [], {}):
                with self.subTest(operation=operation, revision=revision):
                    status, result = self.post(run, operation, {'run_revision': revision})
                    self.assertEqual(409, status)
                    self.assertEqual('RUN_STALE', result['detail']['code'])
        self.assertEqual(before, self.encoded(run))
        self.assertEqual({}, self.client.battles)

    def test_http_resume_remains_available_without_a_revision(self):
        run = self.create()
        status, recovered = self.post(run, 'resume', {})
        self.assertEqual(200, status)
        self.assertEqual(run, recovered)
        status, started = self.post(run, 'battle/start', {'run_revision': run['run_revision']})
        self.assertEqual(200, status)
        self.assertGreater(started['run_revision'], run['run_revision'])

    def test_authorization_precedes_the_revision_error(self):
        run = self.create()
        status, _ = self.post({**run, 'token': 'wrong'}, 'battle/start', {})
        self.assertEqual(404, status)

    def test_a_delayed_previous_battle_command_cannot_act_in_the_next_battle(self):
        run = self.perform(self.create(), 'battle/start')
        old = copy.deepcopy(run)
        run = self.perform(run, 'battle/action', {'type': 'move', 'index': 0}, 0, 'old-tab-command')
        choice = next(r for r in run['rewards'] if r['kind'] != 'recruit')
        run = self.perform(run, 'reward', choice['id'])
        run = self.perform(run, 'path', run['paths'][0]['id'])
        run = self.perform(run, 'battle/start')
        self.assertEqual(1, run['node_index'])
        self.assertEqual(0, run['battle']['revision'])
        before = self.encoded(run)
        for command in ('old-tab-command', 'delayed-new-command'):
            status, result = self.post(run, 'battle/action', {
                'action': {'type': 'move', 'index': 0}, 'revision': 0,
                'command_id': command, 'run_revision': old['run_revision']})
            self.assertEqual(409, status)
            self.assertEqual('RUN_STALE', result['detail']['code'])
        self.assertEqual(before, self.encoded(run))
        self.assertEqual([], self.client.battles[run['battle']['battle_id']]['actions'])

    def test_an_accepted_same_battle_command_can_be_retried_once_without_spending_pp_twice(self):
        self.client.turns_to_win = 4
        run = self.perform(self.create(), 'battle/start')
        body = {'action': {'type': 'move', 'index': 0}, 'revision': 0,
                'command_id': 'lost-response', 'run_revision': run['run_revision']}
        status, first = self.post(run, 'battle/action', body)
        self.assertEqual(200, status)
        self.assertGreater(first['run_revision'], run['run_revision'])
        status, repeated = self.post(run, 'battle/action', body)
        self.assertEqual(200, status)
        self.assertEqual(first, repeated)
        self.assertEqual(19, repeated['battle']['view']['own'][0]['moves'][0]['pp'])
        status, _ = self.post(run, 'battle/action', {**body, 'action': {'type': 'move', 'index': 1}})
        self.assertEqual(409, status)
        status, _ = self.post(run, 'battle/action', {**body, 'run_revision': None})
        self.assertEqual(409, status)

    def test_pending_ai_recovery_with_the_original_command_keeps_one_player_choice(self):
        self.client.turns_to_win = 4
        run = self.perform(self.create(), 'battle/start')
        choose = self.service._choose_ai_action
        attempts = 0
        def fail_once(state, view):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError('temporary prediction failure')
            return choose(state, view)
        self.service._choose_ai_action = fail_once
        body = {'action': {'type': 'move', 'index': 0}, 'revision': 0,
                'command_id': 'pending', 'run_revision': run['run_revision']}
        status, _ = self.post(run, 'battle/action', body)
        self.assertEqual(400, status)
        status, recovered = self.post(run, 'battle/action', body)
        self.assertEqual(200, status)
        self.assertEqual(1, recovered['battle']['revision'])
        self.assertEqual(19, recovered['battle']['view']['own'][0]['moves'][0]['pp'])
        actions = self.client.battles[recovered['battle']['battle_id']]['actions']
        self.assertEqual(['p1', 'p2'], [action['side'] for action in actions])

    def test_a_retried_reroll_does_not_consume_the_second_relic_reroll(self):
        run = self.reward(extra_reroll=True)
        self.assertEqual(2, run['rerolls_left'])
        status, first = self.post(run, 'reroll', {'run_revision': run['run_revision']})
        self.assertEqual(200, status)
        self.assertEqual(1, first['rerolls_left'])
        before = self.encoded(run)
        status, _ = self.post(run, 'reroll', {'run_revision': run['run_revision']})
        self.assertEqual(409, status)
        self.assertEqual(before, self.encoded(run))

    def test_concurrent_tabs_cannot_both_spend_one_displayed_reward_revision(self):
        run = self.reward(extra_reroll=True)
        def reroll():
            try:
                self.perform(run, 'reroll')
                return 'accepted'
            except RunStaleError:
                return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reroll(), range(2)))
        self.assertCountEqual(['accepted', 'stale'], results)
        self.assertEqual(1, self.service.get(run['run_id'], run['token'])['rerolls_left'])

    def test_a_stale_rest_choice_does_not_apply_at_a_later_rest(self):
        first_rest = self.until_node(self.create(), 2)
        run = self.perform(first_rest, 'rest', 'heal')
        later_rest = self.until_node(run, 8)
        before = self.encoded(later_rest)
        status, _ = self.post(later_rest, 'rest', {'option': 'pp', 'run_revision': first_rest['run_revision']})
        self.assertEqual(409, status)
        self.assertEqual(before, self.encoded(later_rest))
        self.assertEqual('node', self.service.get(run['run_id'], run['token'])['phase'])

    def test_failed_save_does_not_publish_or_persist_a_new_revision(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        state['money'] += 100
        before = self.encoded(run)
        self.service.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(SaveStorageError):
            self.service._save(state)
        self.assertEqual(0, state['run_revision'])
        self.assertEqual(before, self.encoded(run))
        self.service.db.execute('PRAGMA query_only=OFF')
        self.service._save(state)
        self.assertEqual(1, state['run_revision'])
        self.assertEqual(160, self.service.get(run['run_id'], run['token'])['money'])

    def test_a_restored_battle_returns_the_revision_saved_during_restore(self):
        run = self.perform(self.create(), 'battle/start')
        self.service.close()
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client)
        restored = self.service.get(run['run_id'], run['token'])
        stored = json.loads(self.encoded(run))
        self.assertGreater(restored['run_revision'], run['run_revision'])
        self.assertEqual(stored['run_revision'], restored['run_revision'])
        self.assertEqual('reward', self.perform(restored, 'battle/action',
                                              {'type': 'move', 'index': 0}, 0, 'after-restore')['phase'])


if __name__ == '__main__':
    unittest.main()
