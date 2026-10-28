"""Bound live expedition battles without changing committed game progress."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from battle_service.client import BattleServiceError
from services.engine_api import EngineSessions, choose_legal_action, default_opponents
from services.run_service import RunService, SaveStorageError
from test_run_service import FakeBattleClient


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class CacheClient(FakeBattleClient):
    def __init__(self):
        super().__init__()
        self._closed = False
        self.closed_ids = []
        self.close_calls = 0
        self.restore_calls = 0
        self.checkpoint_calls = 0
        self.turns_to_win = 20

    def checkpoint(self, bid):
        self.checkpoint_calls += 1
        return super().checkpoint(bid)

    def restore(self, checkpoint):
        self.restore_calls += 1
        return super().restore(checkpoint)

    def close_battle(self, bid):
        self.closed_ids.append(bid)
        return super().close_battle(bid)

    def close(self):
        self.close_calls += 1
        self._closed = True
        return super().close()


class RunCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'cache.sqlite3'
        self.clock = Clock()
        self.client = CacheClient()
        self.service = RunService(self.path, client_factory=lambda: self.client,
                                  cache_capacity=2, cache_ttl=10, clock=self.clock)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def new(self):
        return self.service.create(default_opponents(), seed='cache-test')

    def start(self, run):
        return self.service.battle_start(run['run_id'], run['token'])

    def get(self, run):
        return self.service.get(run['run_id'], run['token'])

    def row(self, run):
        return self.service.db.execute('SELECT state FROM runs WHERE id=?', (run['run_id'],)).fetchone()[0]

    def assert_metadata(self):
        self.assertEqual(set(self.service.live), set(self.service._live_used))
        self.assertLessEqual(len(self.service.live), self.service.cache_capacity)

    def test_cache_configuration_is_validated_before_opening_the_database(self):
        path = Path(self.directory.name) / 'invalid.sqlite3'
        for config in ({'cache_capacity': 0}, {'cache_capacity': True}, {'cache_capacity': 1.5},
                       {'cache_ttl': 0}, {'cache_ttl': -1}, {'cache_ttl': float('inf')},
                       {'cache_ttl': float('nan')}, {'cache_ttl': True}, {'clock': 1}):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    RunService(path, **config)
                self.assertFalse(path.exists())

    def test_creation_and_restore_share_the_lru_cap_without_saving_evicted_runs(self):
        first, second, third = [self.new() for _ in range(3)]
        first = self.start(first)
        self.clock.advance(1)
        second = self.start(second)
        second_saved = self.row(second)
        checkpoints = self.client.checkpoint_calls
        self.clock.advance(1)
        self.get(first)
        self.clock.advance(1)
        third = self.start(third)
        self.assertEqual({first['run_id'], third['run_id']}, set(self.service.live))
        self.assertNotIn(second['battle']['battle_id'], self.client.battles)
        self.assertEqual(second_saved, self.row(second))
        self.assertEqual(checkpoints + 1, self.client.checkpoint_calls)
        first_saved = self.row(first)
        self.clock.advance(1)
        restored = self.get(second)
        self.assertEqual({second['run_id'], third['run_id']}, set(self.service.live))
        self.assertEqual(first_saved, self.row(first))
        self.assertEqual(json.loads(second_saved)['battle']['checkpoint'], json.loads(self.row(second))['battle']['checkpoint'])
        self.assertEqual(1, self.client.restore_calls)
        self.assertGreater(restored['run_revision'], second['run_revision'])
        self.assertEqual(json.loads(self.row(second))['run_revision'], restored['run_revision'])
        self.assert_metadata()

    def test_ttl_cleanup_is_activity_driven_and_keeps_the_current_run(self):
        first, second = [self.new() for _ in range(2)]
        first = self.start(first)
        self.clock.advance(2)
        second = self.start(second)
        first_saved = self.row(first)
        self.clock.advance(10)
        self.assertEqual(2, len(self.service.live))  # No background timer.
        self.get(second)
        self.assertEqual({second['run_id']}, set(self.service.live))
        self.assertEqual(first_saved, self.row(first))
        self.assertEqual(self.clock.now, self.service._live_used[second['run_id']])
        self.assert_metadata()

    def test_failed_authorization_does_not_prune_or_refresh_any_cache_entry(self):
        first, second = [self.start(self.new()) for _ in range(2)]
        entries = copy.deepcopy(self.service.live)
        used = copy.deepcopy(self.service._live_used)
        closes = list(self.client.closed_ids)
        self.clock.advance(50)
        for run_id, token in ((first['run_id'], 'wrong'), ('missing', first['token']), (second['run_id'], None)):
            for operation in (self.service.get, self.service.resume, self.service.battle_start):
                with self.subTest(operation=operation.__name__, run_id=run_id):
                    with self.assertRaises(KeyError):
                        operation(run_id, token)
        self.assertEqual(entries, self.service.live)
        self.assertEqual(used, self.service._live_used)
        self.assertEqual(closes, self.client.closed_ids)

    def test_status_exposes_only_safe_counts_and_does_not_trigger_cleanup_or_worker_creation(self):
        untouched = RunService(':memory:', client_factory=lambda: self.fail('status started a worker'))
        try:
            self.assertEqual({'active': 0, 'capacity': 16, 'idle_seconds': 300.0,
                              'cleanup': 'authenticated-activity'}, untouched.storage_status()['battle_cache'])
        finally:
            untouched.close()
        run = self.start(self.new())
        self.clock.advance(50)
        used = dict(self.service._live_used)
        status = self.service.storage_status()
        self.assertEqual(1, status['battle_cache']['active'])
        self.assertEqual(used, self.service._live_used)
        self.assertIn(run['run_id'], self.service.live)
        serialized = json.dumps(status)
        for secret in (run['run_id'], run['token'], run['battle']['battle_id'], str(self.path), 'checkpoint'):
            self.assertNotIn(secret, serialized)

    def test_long_active_request_does_not_evict_the_battle_between_choose_and_commit(self):
        first, second = [self.start(self.new()) for _ in range(2)]
        choose = self.client.choose
        def delayed(bid, side, action):
            result = choose(bid, side, action)
            if side == 'p1':
                self.clock.advance(20)
            return result
        self.client.choose = delayed
        result = self.service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'slow')
        self.assertEqual(2, result['battle']['view']['turn'])
        self.assertEqual(0, self.client.restore_calls)
        self.assertIn(first['run_id'], self.service.live)
        self.get(first)
        self.assertNotIn(second['run_id'], self.service.live)
        self.assert_metadata()

    def test_save_failure_discards_only_the_uncommitted_run_and_its_access_metadata(self):
        first, second = [self.start(self.new()) for _ in range(2)]
        before = self.row(first)
        self.service.db.execute('PRAGMA query_only=ON')
        try:
            with self.assertRaises(SaveStorageError):
                self.service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'retry')
        finally:
            self.service.db.execute('PRAGMA query_only=OFF')
        self.assertEqual(before, self.row(first))
        self.assertEqual({second['run_id']}, set(self.service.live))
        self.assertNotIn(first['battle']['battle_id'], self.client.battles)
        self.assert_metadata()
        restored = self.get(first)
        result = self.service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0},
                                            restored['battle']['revision'], 'retry')
        actions = self.client.battles[result['battle']['battle_id']]['actions']
        self.assertEqual(['p1', 'p2'], [action['side'] for action in actions])

    def test_initial_checkpoint_failure_leaves_the_node_and_no_cached_battle(self):
        run = self.new()
        before = self.row(run)
        checkpoint = self.client.checkpoint
        self.client.checkpoint = lambda _: (_ for _ in ()).throw(RuntimeError('checkpoint unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'checkpoint unavailable'):
            self.start(run)
        self.assertEqual(before, self.row(run))
        self.assertEqual({}, self.client.battles)
        self.assertEqual({}, self.service.live)
        self.assert_metadata()
        self.client.checkpoint = checkpoint
        self.assertEqual('battle', self.start(run)['phase'])

    def test_alive_checkpoint_rpc_failure_does_not_mix_an_uncommitted_choice_with_old_commands(self):
        first, second = [self.start(self.new()) for _ in range(2)]
        before = self.row(first)
        checkpoint = self.client.checkpoint
        self.client.checkpoint = lambda _: (_ for _ in ()).throw(RuntimeError('checkpoint unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'checkpoint unavailable'):
            self.service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'uncommitted')
        self.client.checkpoint = checkpoint
        self.assertFalse(self.client._closed)
        self.assertEqual(before, self.row(first))
        self.assertEqual({second['run_id']}, set(self.service.live))
        self.assert_metadata()
        restored = self.service.resume(first['run_id'], first['token'])
        self.assertEqual(0, restored['battle']['revision'])
        self.assertEqual([], json.loads(self.row(first))['battle']['checkpoint']['data']['actions'])
        result = self.service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'uncommitted')
        self.assertEqual(19, result['battle']['view']['own'][0]['moves'][0]['pp'])

    def test_worker_loss_after_choose_preserves_the_last_db_commit(self):
        run = self.start(self.new())
        before = self.row(run)
        old = self.client
        choose = old.choose
        def disconnect(bid, side, action):
            result = choose(bid, side, action)
            if side == 'p1':
                old.close()
            return result
        old.choose = disconnect
        with self.assertRaises(BattleServiceError):
            self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'lost-worker')
        self.assertEqual(before, self.row(run))
        self.assertEqual({}, self.service.live)
        self.assert_metadata()
        self.client = CacheClient()
        recovered = self.service.resume(run['run_id'], run['token'])
        self.assertEqual(0, recovered['battle']['revision'])
        self.assertEqual(1, recovered['battle']['view']['turn'])

    def test_committed_player_choice_survives_ai_checkpoint_failure_and_eviction(self):
        run = self.start(self.new())
        checkpoint = self.client.checkpoint
        def fail_after_ai(bid):
            if self.client.battles[bid]['actions'][-1]['side'] == 'p2':
                raise RuntimeError('AI checkpoint unavailable')
            return checkpoint(bid)
        self.client.checkpoint = fail_after_ai
        with self.assertRaisesRegex(RuntimeError, 'AI checkpoint'):
            self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'accepted')
        self.client.checkpoint = checkpoint
        saved = json.loads(self.row(run))
        self.assertEqual(1, saved['battle']['revision'])
        self.assertEqual(['p1'], [a['side'] for a in saved['battle']['checkpoint']['data']['actions']])
        self.assertEqual({}, self.service.live)
        recovered = self.service.resume(run['run_id'], run['token'])
        self.assertEqual(2, recovered['battle']['view']['turn'])
        repeated = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'accepted')
        self.assertEqual(19, repeated['battle']['view']['own'][0]['moves'][0]['pp'])
        actions = self.client.battles[recovered['battle']['battle_id']]['actions']
        self.assertEqual(['p1', 'p2'], [a['side'] for a in actions])
        self.assert_metadata()

    def test_worker_replacement_clears_all_old_access_metadata_before_restore(self):
        first, second = [self.start(self.new()) for _ in range(2)]
        second_saved = self.row(second)
        self.client.close()
        self.client = CacheClient()
        recovered = self.get(first)
        self.assertEqual({first['run_id']}, set(self.service.live))
        self.assertEqual(second_saved, self.row(second))
        self.assertEqual(1, recovered['battle']['view']['turn'])
        self.assertIs(self.client, self.service.client)
        self.assert_metadata()

    def test_shared_worker_eviction_and_close_leave_other_modes_running(self):
        self.service.close()
        self.client = CacheClient()
        self.service = RunService(self.path, client_factory=lambda: self.client, owns_client=False,
                                  cache_capacity=1, cache_ttl=10, clock=self.clock)
        external = self.client.create({'p1': default_opponents(), 'p2': default_opponents()})['battle_id']
        first, second = [self.new() for _ in range(2)]
        first = self.start(first)
        second = self.start(second)
        self.assertNotIn(first['battle']['battle_id'], self.client.battles)
        self.assertIn(external, self.client.battles)
        self.service.close()
        self.assertEqual({external}, set(self.client.battles))
        self.assertEqual(0, self.client.close_calls)
        self.assertFalse(self.client._closed)
        self.assertEqual({}, self.service.live)
        self.assert_metadata()
        self.service.close()
        self.client.close()

    def test_invalid_action_does_not_discard_a_healthy_cached_battle(self):
        run = self.start(self.new())
        before = self.row(run)
        with self.assertRaises(ValueError):
            self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 99}, 0, 'invalid')
        self.assertEqual(before, self.row(run))
        self.assertIn(run['run_id'], self.service.live)
        self.assert_metadata()

    def test_finished_battle_clears_cache_metadata_and_does_not_repeat_rewards(self):
        self.client.turns_to_win = 1
        run = self.start(self.new())
        result = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'winner')
        repeated = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'winner')
        self.assertEqual('reward', result['phase'])
        self.assertEqual(result, repeated)
        self.assertEqual(80, result['money'])
        self.assertEqual({}, self.service.live)
        self.assertEqual({}, self.client.battles)
        self.assert_metadata()

    def test_an_evicted_ended_checkpoint_applies_its_reward_only_once(self):
        self.service.cache_capacity = 1
        self.client.turns_to_win = 1
        first, second = [self.new() for _ in range(2)]
        first = self.start(first)
        finish = self.service._finish_if_ended
        self.service._finish_if_ended = lambda _: (_ for _ in ()).throw(RuntimeError('response interrupted before settlement'))
        with self.assertRaisesRegex(RuntimeError, 'before settlement'):
            self.service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'ended')
        self.service._finish_if_ended = finish
        checkpoint = json.loads(self.row(first))['battle']['checkpoint']
        self.assertTrue(checkpoint['data']['ended'])
        self.start(second)
        self.assertNotIn(first['run_id'], self.service.live)
        recovered = self.service.resume(first['run_id'], first['token'])
        self.assertEqual(('reward', 80, 1), (recovered['phase'], recovered['money'], recovered['completed_nodes']))
        self.assertEqual(checkpoint, json.loads(self.row(first))['last_replay'])
        self.assertEqual(recovered, self.service.resume(first['run_id'], first['token']))
        self.assertEqual(recovered, self.service.battle_action(first['run_id'], first['token'],
                                                              {'type': 'move', 'index': 0}, 0, 'ended'))
        self.assert_metadata()

    def test_restore_write_failure_closes_the_new_cache_entry_and_preserves_the_old_checkpoint(self):
        runs = [self.new() for _ in range(3)]
        first, second, third = [self.start(run) for run in runs]
        before = self.row(first)
        self.assertNotIn(first['run_id'], self.service.live)
        self.service.db.execute('PRAGMA query_only=ON')
        try:
            with self.assertRaises(SaveStorageError):
                self.get(first)
        finally:
            self.service.db.execute('PRAGMA query_only=OFF')
        self.assertEqual(before, self.row(first))
        self.assertNotIn(first['run_id'], self.service.live)
        self.assertEqual(set(self.service.live.values()), set(self.client.battles))
        self.assert_metadata()
        recovered = self.get(first)
        self.assertEqual(1, recovered['battle']['view']['turn'])
        self.assertEqual(json.loads(before)['battle']['checkpoint'], json.loads(self.row(first))['battle']['checkpoint'])
        self.assert_metadata()

    def test_failed_close_rpc_does_not_forget_a_healthy_cached_battle_or_exceed_capacity(self):
        first, second, third = [self.new() for _ in range(3)]
        first, second = self.start(first), self.start(second)
        close_battle = self.client.close_battle
        def fail_close(bid):
            if bid == first['battle']['battle_id']:
                raise RuntimeError('close unavailable')
            return close_battle(bid)
        self.client.close_battle = fail_close
        with self.assertRaisesRegex(RuntimeError, 'close unavailable'):
            self.start(third)
        self.assertEqual({first['run_id'], second['run_id']}, set(self.service.live))
        self.assertEqual('node', json.loads(self.row(third))['phase'])
        self.assertEqual(set(self.service.live.values()), set(self.client.battles))
        self.assert_metadata()
        self.client.close_battle = close_battle
        self.assertEqual('battle', self.start(third)['phase'])
        self.assert_metadata()


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunCacheTests(unittest.TestCase):
    def test_real_pending_battle_restores_exactly_and_shared_worker_shutdown_is_isolated(self):
        engine = EngineSessions()
        clock = Clock()
        service = RunService(':memory:', client_factory=engine.get_client, owns_client=False,
                             cache_capacity=1, cache_ttl=10, clock=clock)
        try:
            external = engine.create(default_opponents())
            first = service.create(default_opponents(), seed='real-cache-pending')
            second = service.create(default_opponents(), seed='real-cache-other')
            state = service._load(first['run_id'], first['token'])
            state['resources'][0].update(hp=state['resources'][0]['maxhp'] - 11, status='slp', sleepTurns=2)
            state['resources'][1]['item'] = ''
            for row in state['resources']:
                for move in row['moves']:
                    move['pp'] -= 2
            service._save(state)
            first = service.battle_start(first['run_id'], first['token'])
            choose_ai = service._choose_ai_action
            def pause_ai(*_):
                raise RuntimeError('pause after durable player input')
            service._choose_ai_action = pause_ai
            with self.assertRaisesRegex(RuntimeError, 'durable player'):
                service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'real-accepted')
            saved = service._load(first['run_id'], first['token'])
            checkpoint = copy.deepcopy(saved['battle']['checkpoint'])
            view = service.client.view(service.live[first['run_id']], 'p1')
            resources = service.client.resources(service.live[first['run_id']])['p1']
            self.assertTrue(view['request']['wait'])
            service.battle_start(second['run_id'], second['token'])
            self.assertNotIn(first['run_id'], service.live)
            restored = service.get(first['run_id'], first['token'])
            without_id = lambda value: {key: item for key, item in value.items() if key != 'battle_id'}
            self.assertEqual(without_id(view), without_id(restored['battle']['view']))
            self.assertEqual(resources, service.client.resources(restored['battle']['battle_id'])['p1'])
            self.assertEqual(checkpoint, service._load(first['run_id'], first['token'])['battle']['checkpoint'])
            self.assertEqual(service._load(first['run_id'], first['token'])['run_revision'], restored['run_revision'])
            service._choose_ai_action = choose_ai
            recovered = service.resume(first['run_id'], first['token'])
            repeated = service.battle_action(first['run_id'], first['token'], {'type': 'move', 'index': 0}, 0, 'real-accepted')
            self.assertEqual(recovered, repeated)
            actions = service.client.checkpoint(recovered['battle']['battle_id'])['actions']
            self.assertEqual(['p1', 'p2'], [action['side'] for action in actions])
            self.assertEqual(1, len(service.live))
            service.close()
            self.assertEqual(1, engine.response(external['battle_id'])['view']['turn'])
            self.assertFalse(engine.client._closed)
        finally:
            service.close()
            engine.close()

    def test_shared_owner_worker_pointer_replacement_restores_only_the_requested_run(self):
        engine = EngineSessions()
        service = RunService(':memory:', client_factory=engine.get_client, owns_client=False, cache_capacity=1)
        try:
            run = service.create(default_opponents(), seed='shared-worker-replacement')
            run = service.battle_start(run['run_id'], run['token'])
            old = engine.client
            old.close()
            external = engine.create(default_opponents())
            self.assertIsNot(old, engine.client)
            restored = service.get(run['run_id'], run['token'])
            self.assertIs(engine.client, service.client)
            self.assertEqual(1, restored['battle']['view']['turn'])
            self.assertEqual({run['run_id']}, set(service._live_used))
            service.close()
            result = engine.act(external['battle_id'], external['token'], choose_legal_action(external['view']['request']), 0, 'still-running')
            self.assertEqual(2, result['view']['turn'])
        finally:
            service.close()
            engine.close()


if __name__ == '__main__':
    unittest.main()
