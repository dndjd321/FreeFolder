"""Recover committed expedition creation without duplicating a run or its inputs."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from fastapi import FastAPI
from services.engine_api import choose_legal_action, default_opponents
from services.run_api import install_run_routes
from services.run_creation import CREATION_VERSION, creation_credentials, creation_request_hash
from services.run_service import CreationConflictError, RunService, SaveStorageError
from services.save_tools import backup_save, restore_save
from test_engine_api import http
from test_run_service import FakeBattleClient


class ConnectionProxy:
    """Inject transaction failures without changing global SQLite behavior."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        return self.connection.__exit__(*args)

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class RunCreationTests(unittest.TestCase):
    creation_id = '01' * 32
    creation_secret = 'ab' * 32

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'creation.sqlite3'
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client)
        self.app = FastAPI()
        install_run_routes(self.app, self.service)
        self.probe_patch = patch.object(self.service, '_probe', wraps=self.service._probe)
        self.probe = self.probe_patch.start()

    def tearDown(self):
        self.probe_patch.stop()
        self.service.close()
        self.directory.cleanup()

    def body(self, **changes):
        return {'team': default_opponents(), 'creation_id': self.creation_id,
                'creation_secret': self.creation_secret, **changes}

    def create(self, *, team=None, seed=None, difficulty='normal', **credentials):
        return self.service.create(default_opponents() if team is None else team, seed, difficulty,
                                   creation_id=credentials.get('creation_id', self.creation_id),
                                   creation_secret=credentials.get('creation_secret', self.creation_secret))

    def post(self, body):
        return asyncio.run(http(self.app, 'POST', '/api/runs', body))

    def rows(self):
        return self.service.db.execute('SELECT id,token_hash,state FROM runs').fetchall()

    def test_committed_response_loss_recovers_the_same_credentials_with_one_seed_and_probe(self):
        async def lose_response():
            body = json.dumps(self.body()).encode()
            async def receive():
                return {'type': 'http.request', 'body': body, 'more_body': False}
            async def send(event):
                if event['type'] == 'http.response.body':
                    raise ConnectionError('creation response lost')
            await self.app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                            'method': 'POST', 'scheme': 'http', 'path': '/api/runs', 'raw_path': b'/api/runs',
                            'query_string': b'', 'headers': [(b'content-type', b'application/json')],
                            'server': ('test', 80), 'client': ('127.0.0.1', 1), 'root_path': ''}, receive, send)
        with patch('services.run_service.secrets.token_hex', return_value='one-generated-seed') as seed:
            with self.assertRaisesRegex(ConnectionError, 'response lost'):
                asyncio.run(lose_response())
            self.assertEqual(1, len(self.rows()))
            status, recovered = self.post(self.body())
            self.assertEqual(200, status)
            self.assertEqual(1, seed.call_count)
        credentials = creation_credentials(self.creation_id, self.creation_secret)
        self.assertEqual((credentials['run_id'], credentials['token']), (recovered['run_id'], recovered['token']))
        self.assertEqual('one-generated-seed', recovered['seed'])
        self.assertEqual(1, self.probe.call_count)
        self.assertEqual(1, len(self.rows()))

    def test_concurrent_http_posts_are_serialized_into_one_atomic_creation(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.post(self.body(seed='concurrent')), range(4)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertTrue(all(result == results[0][1] for _, result in results))
        self.assertEqual(1, len(self.rows()))
        self.assertEqual(1, self.probe.call_count)
        saved = json.loads(self.rows()[0][2])
        self.assertEqual({'version': CREATION_VERSION,
                          'request_hash': creation_request_hash(default_opponents(), 'concurrent')}, saved['creation'])

    def test_partial_null_or_malformed_http_credentials_are_rejected_before_probe(self):
        fields = [ {'creation_id': self.creation_id}, {'creation_secret': self.creation_secret},
                   {'creation_id': None, 'creation_secret': None},
                   {'creation_id': '', 'creation_secret': self.creation_secret},
                   {'creation_id': self.creation_id.upper(), 'creation_secret': self.creation_secret.upper()},
                   {'creation_id': 'g' * 64, 'creation_secret': self.creation_secret},
                   {'creation_id': self.creation_id, 'creation_secret': []} ]
        for credentials in fields:
            with self.subTest(credentials_type={key: type(value).__name__ for key, value in credentials.items()}):
                status, error = self.post({'team': default_opponents(), **credentials})
                self.assertEqual(400, status)
                self.assertNotIn(self.creation_id, json.dumps(error))
                self.assertNotIn(self.creation_secret, json.dumps(error))
        self.assertEqual([], self.rows())
        self.assertEqual(0, self.probe.call_count)
        with self.assertRaises(ValueError):
            self.service.create(default_opponents(), creation_id=self.creation_id)

    def test_valid_payload_conflicts_and_wrong_secret_have_one_nonrevealing_409_response(self):
        created = self.create(seed='original')
        before = self.rows()
        changed = default_opponents()
        changed[0]['item'] = ''
        errors = []
        for body in (self.body(seed='other'), self.body(seed='original', difficulty='hard'),
                     self.body(seed='original', team=changed),
                     self.body(seed='original', creation_secret='cd' * 32)):
            status, error = self.post(body)
            self.assertEqual(409, status)
            self.assertEqual('CREATION_CONFLICT', error['detail']['code'])
            errors.append(error)
            for secret in (created['run_id'], created['token'], self.creation_id, self.creation_secret):
                self.assertNotIn(secret, json.dumps(error))
        self.assertTrue(all(error == errors[0] for error in errors))
        self.assertEqual(before, self.rows())
        self.assertEqual(1, self.probe.call_count)

    def test_existing_token_hash_is_authenticated_before_creation_metadata_is_inspected(self):
        created = self.create()
        state = self.service._load(created['run_id'], created['token'])
        state['creation'] = {'unsupported-private-field': 'do not reveal'}
        self.service._save(state)
        compare = secrets.compare_digest
        with patch('services.run_service.secrets.compare_digest', wraps=compare) as authenticated:
            status, error = self.post(self.body(creation_secret='cd' * 32))
        self.assertEqual(409, status)
        self.assertEqual('CREATION_CONFLICT', error['detail']['code'])
        self.assertEqual(1, authenticated.call_count)
        self.assertNotIn('unsupported-private-field', json.dumps(error))

    def test_wrong_secret_does_not_prune_or_refresh_expired_live_battles(self):
        run = self.create()
        run = self.service.battle_start(run['run_id'], run['token'])
        self.service._live_used[run['run_id']] = -1000
        live, used = dict(self.service.live), dict(self.service._live_used)
        status, _ = self.post(self.body(creation_secret='cd' * 32))
        self.assertEqual(409, status)
        self.assertEqual(live, self.service.live)
        self.assertEqual(used, self.service._live_used)

    def test_reordered_keys_defaults_and_equivalent_explicit_seed_reuse_the_creation(self):
        team = default_opponents()
        team[0]['evs'] = {'hp': 252, 'spa': 252, 'spe': 4}
        created = self.create(team=team, seed=123)
        reordered = [{key: row[key] for key in reversed(row)} for row in copy.deepcopy(team)]
        reordered[0]['evs'] = {'spe': 4, 'spa': 252, 'hp': 252}
        self.assertEqual(created, self.create(team=reordered, seed='123', difficulty='normal'))
        self.assertEqual(1, self.probe.call_count)

    def test_different_creation_ids_intentionally_create_separate_runs(self):
        first = self.create()
        second = self.create(creation_id='02' * 32)
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertNotEqual(first['token'], second['token'])
        self.assertEqual(2, len(self.rows()))

    def test_creation_fingerprint_survives_party_order_recruitment_and_progress(self):
        run = self.create(seed='immutable-request')
        original = json.loads(self.rows()[0][2])['creation']
        run = self.service.party_order(run['run_id'], run['token'], [1, 0, 2])
        self.assertEqual(run, self.create(seed='immutable-request'))
        state = self.service._load(run['run_id'], run['token'])
        self.service._offer_recruits(state, 'reward')
        self.service._save(state)
        run = self.service.recruit(run['run_id'], run['token'], state['recruit_candidates'][0]['id'], 1)
        probes = self.probe.call_count
        recovered = self.create(seed='immutable-request')
        self.assertEqual(run, recovered)
        self.assertEqual('path', recovered['phase'])
        self.assertEqual(original, json.loads(self.rows()[0][2])['creation'])
        self.assertEqual(probes, self.probe.call_count)
        with self.assertRaises(CreationConflictError):
            self.create(team=json.loads(self.rows()[0][2])['team'], seed='immutable-request')

    def test_creation_retry_restores_pending_cache_without_automatically_advancing_ai(self):
        self.client.turns_to_win = 4
        run = self.create(seed='pending')
        run = self.service.battle_start(run['run_id'], run['token'])
        def should_not_advance(*_):
            raise RuntimeError('AI must remain pending')
        self.service._choose_ai_action = should_not_advance
        with self.assertRaisesRegex(RuntimeError, 'remain pending'):
            self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'pending')
        checkpoint = copy.deepcopy(json.loads(self.rows()[0][2])['battle']['checkpoint'])
        self.service.cache_capacity = 1
        other = self.create(seed='other', creation_id='02' * 32)
        self.service.battle_start(other['run_id'], other['token'])
        self.assertNotIn(run['run_id'], self.service.live)
        self.service._finish_if_ended = should_not_advance
        probes = self.probe.call_count
        recovered = self.create(seed='pending')
        self.assertTrue(recovered['battle']['view']['request']['wait'])
        self.assertEqual(checkpoint, json.loads(self.rows()[0][2])['battle']['checkpoint'])
        self.assertEqual(1, recovered['battle']['revision'])
        self.assertEqual(probes, self.probe.call_count)

    def test_creation_retry_returns_an_ended_battle_without_awarding_its_reward(self):
        run = self.create(seed='ended')
        run = self.service.battle_start(run['run_id'], run['token'])
        self.service._finish_if_ended = lambda _: (_ for _ in ()).throw(RuntimeError('settlement interrupted'))
        with self.assertRaisesRegex(RuntimeError, 'settlement interrupted'):
            self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'ended')
        before = self.rows()
        recovered = self.create(seed='ended')
        self.assertEqual(('battle', 60, 0), (recovered['phase'], recovered['money'], recovered['completed_nodes']))
        self.assertTrue(recovered['battle']['view']['ended'])
        self.assertEqual(before, self.rows())

    def test_creation_credentials_are_recovered_after_restart_without_validation_or_seed_generation(self):
        run = self.create()
        self.service.close()
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.fail('Creation retry started a validation worker'))
        with patch('services.run_service.secrets.token_hex', side_effect=AssertionError('regenerated seed')):
            self.assertEqual(run, self.create())
        self.assertEqual(1, len(self.rows()))

    def test_plain_credentials_and_creation_metadata_are_absent_from_public_state_and_database(self):
        run = self.create(seed='private')
        run_id, token_hash, encoded = self.rows()[0]
        credentials = creation_credentials(self.creation_id, self.creation_secret)
        self.assertEqual(credentials['run_id'], run_id)
        self.assertEqual(credentials['token_hash'], token_hash)
        for secret in (self.creation_id, self.creation_secret, run['token']):
            self.assertNotIn(secret, encoded)
        self.assertEqual({'version', 'request_hash'}, set(json.loads(encoded)['creation']))
        public = json.dumps(run)
        for private in ('creation', 'creation_id', 'creation_secret', 'token_hash', 'request_hash'):
            self.assertNotIn(private, run)
        self.assertNotIn(token_hash, public)
        self.assertNotIn(json.loads(encoded)['creation']['request_hash'], public)
        status, catalog = asyncio.run(http(self.app, 'GET', '/api/runs/status'))
        self.assertEqual(200, status)
        for secret in (run_id, run['token'], self.creation_id, self.creation_secret, token_hash):
            self.assertNotIn(secret, json.dumps(catalog))

    def test_legacy_creation_stays_random_without_creation_metadata(self):
        status, first = self.post({'team': default_opponents()})
        self.assertEqual(200, status)
        second = self.service.create(default_opponents(), creation_id=None, creation_secret=None)
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertNotEqual(first['token'], second['token'])
        self.assertEqual([24, 24], [len(first['run_id']), len(second['run_id'])])
        self.assertTrue(all('creation' not in json.loads(row[2]) for row in self.rows()))
        self.assertTrue(all(json.loads(row[2])['schema_version'] == 1 for row in self.rows()))

    def test_database_write_failure_can_retry_without_a_partial_creation_record(self):
        self.service.db.execute('PRAGMA query_only=ON')
        status, _ = self.post(self.body(seed='retry-write'))
        self.assertEqual(503, status)
        self.assertEqual([], self.rows())
        self.assertFalse(self.service.db.in_transaction)
        self.assertEqual({}, self.client.battles)
        self.service.db.execute('PRAGMA query_only=OFF')
        status, recovered = self.post(self.body(seed='retry-write'))
        self.assertEqual(200, status)
        self.assertEqual(creation_credentials(self.creation_id, self.creation_secret)['run_id'], recovered['run_id'])
        self.assertEqual(2, self.probe.call_count)  # The failed transaction committed no validation result.
        self.assertEqual(recovered, self.create(seed='retry-write'))
        self.assertEqual(2, self.probe.call_count)
        self.assertEqual(1, len(self.rows()))

    def test_commit_failure_rolls_back_the_run_and_its_creation_fingerprint_together(self):
        class FailedCommit(ConnectionProxy):
            def __exit__(self, exception_type, exception, traceback):
                if exception_type is None:
                    self.connection.rollback()
                    raise sqlite3.OperationalError('commit unavailable')
                return super().__exit__(exception_type, exception, traceback)
        connection = self.service.db
        self.service.db = FailedCommit(connection)
        try:
            with self.assertRaises(SaveStorageError):
                self.create(seed='failed-commit')
            self.assertEqual([], self.rows())
        finally:
            self.service.db = connection
        self.assertEqual('node', self.create(seed='failed-commit')['phase'])
        self.assertEqual(1, len(self.rows()))

    def test_creation_lookup_database_error_is_unavailable_without_running_a_probe(self):
        class FailedLookup(ConnectionProxy):
            def execute(self, sql, parameters=()):
                if sql.startswith('SELECT token_hash,state'):
                    raise sqlite3.OperationalError('database unavailable')
                return super().execute(sql, parameters)
        connection = self.service.db
        self.service.db = FailedLookup(connection)
        try:
            status, error = self.post(self.body())
            self.assertEqual(503, status)
            self.assertEqual(0, self.probe.call_count)
            self.assertEqual([], self.rows())
            for secret in (self.creation_id, self.creation_secret):
                self.assertNotIn(secret, json.dumps(error))
        finally:
            self.service.db = connection

    def test_primary_key_race_is_rechecked_and_nonduplicate_integrity_error_stays_unavailable(self):
        path = self.path
        class ConcurrentInsert(ConnectionProxy):
            def execute(self, sql, parameters=()):
                if sql.startswith('INSERT INTO runs'):
                    with closing(sqlite3.connect(path)) as winner, winner:
                        winner.execute(sql, parameters)
                    raise sqlite3.IntegrityError('UNIQUE constraint failed: runs.id')
                return super().execute(sql, parameters)
        connection = self.service.db
        self.service.db = ConcurrentInsert(connection)
        try:
            created = self.create(seed='race')
            self.assertEqual(1, len(self.rows()))
            self.assertEqual(created, self.create(seed='race'))
        finally:
            self.service.db = connection
        class FailedIntegrity(ConnectionProxy):
            def execute(self, sql, parameters=()):
                if sql.startswith('INSERT INTO runs'):
                    raise sqlite3.IntegrityError('other integrity failure')
                return super().execute(sql, parameters)
        self.service.db = FailedIntegrity(connection)
        try:
            with self.assertRaises(SaveStorageError):
                self.create(creation_id='02' * 32)
            self.assertEqual(1, len(self.rows()))
        finally:
            self.service.db = connection


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunCreationTests(unittest.TestCase):
    def test_online_backup_and_restore_recover_the_same_creation_and_current_battle(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            snapshot = Path(directory) / 'snapshot.sqlite3'
            restored = Path(directory) / 'restored.sqlite3'
            creation = {'creation_id': '01' * 32, 'creation_secret': 'ab' * 32}
            service = RunService(source)
            try:
                run = service.create(default_opponents(), seed='real-creation-backup', **creation)
                run = service.battle_start(run['run_id'], run['token'])
                run = service.battle_action(run['run_id'], run['token'],
                                            choose_legal_action(run['battle']['view']['request']), 0, 'before-backup')
                self.assertEqual('battle', run['phase'])
                fingerprint = service._load(run['run_id'], run['token'])['creation']
                self.assertTrue(backup_save(source, snapshot)['compatible'])
                self.assertTrue(restore_save(snapshot, restored)['compatible'])
            finally:
                service.close()
            service = RunService(restored)
            try:
                with patch.object(service, '_probe', side_effect=AssertionError('retry must not validate a new team')):
                    recovered = service.create(default_opponents(), seed='real-creation-backup', **creation)
                    repeated = service.create(default_opponents(), seed='real-creation-backup', **creation)
                self.assertEqual((run['run_id'], run['token']), (recovered['run_id'], recovered['token']))
                without_id = lambda value: {key: item for key, item in value.items() if key != 'battle_id'}
                self.assertEqual(without_id(run['battle']['view']), without_id(recovered['battle']['view']))
                self.assertEqual(recovered, repeated)
                self.assertEqual(fingerprint, service._load(run['run_id'], run['token'])['creation'])
                self.assertEqual(1, service.db.execute('SELECT count(*) FROM runs').fetchone()[0])
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
