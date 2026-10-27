"""A failed durable write must not leave a worker ahead of its checkpoint."""
import asyncio
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from services.engine_api import default_opponents
from services.run_api import install_run_routes
from services.run_service import RunService, SaveStorageError
from test_engine_api import http
from test_run_service import FakeBattleClient


class RunStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'runs.sqlite3'
        self.client = FakeBattleClient()
        self.client.turns_to_win = 20
        self.service = RunService(self.path, client_factory=lambda: self.client)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def battle(self):
        run = self.service.create(default_opponents(), seed='storage-regression')
        return self.service.battle_start(run['run_id'], run['token'])

    def writable(self, enabled):
        self.service.db.execute('PRAGMA query_only = ' + ('OFF' if enabled else 'ON'))

    def test_another_service_cannot_open_a_live_database(self):
        with self.assertRaises(RuntimeError):
            RunService(self.path, client_factory=FakeBattleClient)
        self.assertEqual('node', self.service.create(default_opponents())['phase'])
        self.service.close()
        self.service = RunService(self.path, client_factory=lambda: self.client)

    def test_initialization_failure_releases_the_database_lease(self):
        path = Path(self.tmp.name) / 'new.sqlite3'
        with patch('services.run_service.sqlite3.connect', side_effect=sqlite3.OperationalError('unavailable')):
            with self.assertRaises(sqlite3.OperationalError):
                RunService(path, client_factory=FakeBattleClient)
        service = RunService(path, client_factory=FakeBattleClient)
        service.close()

    def test_failed_create_is_rolled_back_and_reports_unavailable(self):
        app = FastAPI()
        install_run_routes(app, self.service)
        self.writable(False)
        status, result = asyncio.run(http(app, 'POST', '/api/runs', {'team': default_opponents()}))
        self.assertEqual(503, status)
        self.assertIn('저장', result['detail'])
        self.assertFalse(self.service.db.in_transaction)
        self.assertEqual(0, self.service.db.execute('SELECT count(*) FROM runs').fetchone()[0])
        self.assertEqual('failed', self.service.storage_status()['last_write'])
        self.writable(True)
        self.service.create(default_opponents())
        self.assertEqual('ok', self.service.storage_status()['last_write'])

    def test_player_input_failure_discards_unsaved_engine_state_before_retry(self):
        run = self.battle()
        rid, token = run['run_id'], run['token']
        bid = run['battle']['battle_id']
        self.writable(False)
        with self.assertRaises(SaveStorageError):
            self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'retry-once')
        self.assertNotIn(rid, self.service.live)
        self.assertNotIn(bid, self.client.battles)
        self.assertFalse(self.service.db.in_transaction)
        self.writable(True)
        restored = self.service.resume(rid, token)
        self.assertEqual(0, restored['battle']['revision'])
        self.assertEqual(20, restored['battle']['view']['own'][0]['moves'][0]['pp'])
        accepted = self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'retry-once')
        duplicate = self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'retry-once')
        self.assertEqual(accepted, duplicate)
        self.assertEqual(2, accepted['battle']['view']['turn'])
        self.assertEqual(19, accepted['battle']['view']['own'][0]['moves'][0]['pp'])

    def test_ai_write_failure_resumes_the_committed_player_choice_once(self):
        run = self.battle()
        rid, token = run['run_id'], run['token']
        original_choose = self.client.choose
        def interrupt_after_ai(bid, side, action):
            result = original_choose(bid, side, action)
            if side == 'p2':
                self.writable(False)
            return result
        self.client.choose = interrupt_after_ai
        with self.assertRaises(SaveStorageError):
            self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'committed-player')
        self.client.choose = original_choose
        self.writable(True)
        resumed = self.service.resume(rid, token)
        self.assertEqual(1, resumed['battle']['revision'])
        self.assertEqual(2, resumed['battle']['view']['turn'])
        self.assertEqual(19, resumed['battle']['view']['own'][0]['moves'][0]['pp'])
        actions = self.client.battles[resumed['battle']['battle_id']]['actions']
        self.assertEqual(['p1', 'p2'], [row['side'] for row in actions])

    def test_public_storage_status_contains_no_paths_or_private_records(self):
        run = self.battle()
        app = FastAPI()
        install_run_routes(app, self.service)
        status, result = asyncio.run(http(app, 'GET', '/api/runs/status'))
        self.assertEqual(200, status)
        storage = result['storage']
        self.assertTrue(storage['available'])
        self.assertEqual('ok', storage['last_write'])
        encoded = json.dumps(storage)
        for secret in (str(self.path), run['run_id'], run['token'], 'checkpoint', 'token_hash'):
            self.assertNotIn(secret, encoded)
        self.service.close()
        self.assertFalse(self.service.storage_status()['available'])


if __name__ == '__main__':
    unittest.main()
