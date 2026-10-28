"""Operator save copies preserve committed runs and never overwrite originals."""
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from services.run_service import RunService
from services.engine_api import default_opponents, choose_legal_action
from services.save_tools import DatabaseLease, backup_save, default_db_path, inspect_save, restore_save


class SaveToolLeaseTests(unittest.TestCase):
    def test_default_directory_and_explicit_directory(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(GAME_ROOT / 'runtime' / 'runs.sqlite3', default_db_path())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FREEFOLDER_SAVE_DIR': tmp}):
            self.assertEqual(Path(tmp) / 'runs.sqlite3', default_db_path())

    def test_memory_lease_and_repeated_close_are_safe(self):
        lease = DatabaseLease(':memory:')
        lease.acquire()
        lease.acquire()
        lease.close()
        lease.close()

    def test_second_lease_cannot_claim_a_database_in_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'runs.sqlite3'
            first, second = DatabaseLease(target), DatabaseLease(target)
            try:
                first.acquire()
                with self.assertRaises(RuntimeError):
                    second.acquire()
                first.close()
                second.acquire()
            finally:
                first.close()
                second.close()


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for actual checkpoint saves')
class SaveToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_directory = tempfile.TemporaryDirectory()
        source = Path(cls.template_directory.name) / 'template.sqlite3'
        service = RunService(source)
        try:
            state = service.create(default_opponents(), seed='private-backup-test-seed')
            cls.run_id, cls.token = state['run_id'], state['token']
            state = service.battle_start(cls.run_id, cls.token)
            state = service.battle_action(
                cls.run_id, cls.token, choose_legal_action(state['battle']['view']['request']),
                state['battle']['revision'], 'template-action')
            if state['phase'] != 'battle':
                raise AssertionError('Save fixture must have an unfinished actual battle')
            cls.expected_view = copy.deepcopy(state['battle']['view'])
        finally:
            service.close()
        cls.template_bytes = source.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.template_directory.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.source = self.directory / 'source.sqlite3'
        self.source.write_bytes(self.template_bytes)

    def tearDown(self):
        self.tmp.cleanup()

    def mutate_state(self, mutate):
        with closing(sqlite3.connect(self.source)) as connection, connection:
            state = json.loads(connection.execute('SELECT state FROM runs').fetchone()[0])
            mutate(state)
            connection.execute('UPDATE runs SET state=?', (json.dumps(state),))

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, '-X', 'utf8', '-B', '-m', 'services.save_tools', *map(str, arguments)],
            cwd=GAME_ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20)

    def test_inspect_reports_compatibility_without_disclosing_run_data(self):
        before = self.source.read_bytes()
        report = inspect_save(self.source)
        self.assertTrue(report['valid'])
        self.assertTrue(report['compatible'])
        self.assertEqual([], report['reasons'])
        self.assertEqual(1, report['run_count'])
        self.assertEqual([1], report['schema_versions'])
        self.assertEqual(['roguelike-v1'], report['content_versions'])
        self.assertEqual(['0.11.11'], report['checkpoint_engine_versions'])
        self.assertIsInstance(report['validation_scope'], str)
        public = json.dumps(report)
        for secret in (self.run_id, self.token, hashlib.sha256(self.token.encode()).hexdigest(),
                       'private-backup-test-seed', 'Charizard', 'token_hash'):
            self.assertNotIn(secret, public)
        self.assertEqual(before, self.source.read_bytes())

    def test_schema_without_required_value_constraints_is_rejected(self):
        target = self.directory / 'wrong-constraints.sqlite3'
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, token_hash TEXT, state TEXT, updated REAL)')
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            inspect_save(target)
        with self.assertRaises(ValueError):
            restore_save(target, self.directory / 'invalid-restore.sqlite3')
        self.assertEqual(before, target.read_bytes())
        self.assertFalse((self.directory / 'invalid-restore.sqlite3').exists())

    def test_optional_run_revision_accepts_legacy_and_rejects_invalid_versions(self):
        for value in (-1, True, '1', None):
            with self.subTest(value=value):
                self.source.write_bytes(self.template_bytes)
                self.mutate_state(lambda state: state.update(run_revision=value))
                with self.assertRaises(ValueError):
                    inspect_save(self.source)
        self.source.write_bytes(self.template_bytes)
        self.mutate_state(lambda state: state.update(run_revision=2))
        self.assertTrue(inspect_save(self.source)['compatible'])

    def test_creation_metadata_validates_without_storing_raw_credentials(self):
        for metadata in (None, [], {}, {'version': True}, {'version': 0},
                         {'version': 1, 'request_hash': 'x' * 64},
                         {'version': 1, 'request_hash': 'a' * 63},
                         {'version': 1, 'request_hash': 'a' * 64, 'creation_secret': 'secret'}):
            with self.subTest(metadata=metadata):
                self.source.write_bytes(self.template_bytes)
                self.mutate_state(lambda state: state.update(creation=metadata))
                with self.assertRaises(ValueError):
                    inspect_save(self.source)
        self.source.write_bytes(self.template_bytes)
        self.mutate_state(lambda state: state.update(creation={'version': 1, 'request_hash': 'a' * 64}))
        report = inspect_save(self.source)
        self.assertTrue(report['compatible'])
        self.assertNotIn('a' * 64, json.dumps(report))

    def test_future_creation_metadata_can_be_archived_but_not_restored(self):
        self.mutate_state(lambda state: state.update(creation={'version': 2, 'future': 'format'}))
        snapshot = self.directory / 'future-creation.sqlite3'
        self.assertFalse(inspect_save(self.source)['compatible'])
        self.assertFalse(backup_save(self.source, snapshot)['compatible'])
        with self.assertRaises(ValueError):
            restore_save(snapshot, self.directory / 'future-creation-restored.sqlite3')

    def test_unknown_optional_difficulty_and_policy_are_archivable_not_restorable(self):
        for field in ('difficulty', 'ai_policy'):
            with self.subTest(field=field):
                self.source.write_bytes(self.template_bytes)
                def mutate(state):
                    if field == 'difficulty':
                        state['difficulty'] = 'future-mode'
                    else:
                        state['battle']['ai_policy'] = 'future-policy'
                self.mutate_state(mutate)
                report = inspect_save(self.source)
                self.assertFalse(report['compatible'])
                snapshot = self.directory / (field + '-archive.sqlite3')
                self.assertFalse(backup_save(self.source, snapshot)['compatible'])
                with self.assertRaises(ValueError):
                    restore_save(snapshot, self.directory / (field + '-restore.sqlite3'))

    def test_online_backup_then_restore_reopens_the_actual_checkpoint_with_same_token(self):
        service = RunService(self.source)
        snapshot = self.directory / 'snapshot.sqlite3'
        restored_path = self.directory / 'restored.sqlite3'
        try:
            current = service.resume(self.run_id, self.token)
            expected = copy.deepcopy(current['battle']['view'])
            before = self.source.read_bytes()
            report = backup_save(self.source, snapshot)
            self.assertTrue(report['compatible'])
            self.assertEqual(before, self.source.read_bytes())
            self.assertEqual(expected['own'], service.get(self.run_id, self.token)['battle']['view']['own'])
        finally:
            service.close()
        report = restore_save(snapshot, restored_path)
        self.assertTrue(report['valid'])
        restored = RunService(restored_path)
        try:
            state = restored.resume(self.run_id, self.token)
            self.assertEqual(expected['turn'], state['battle']['view']['turn'])
            self.assertEqual(expected['own'], state['battle']['view']['own'])
            self.assertEqual(current['battle']['revision'], state['battle']['revision'])
        finally:
            restored.close()

    def test_online_wal_snapshot_excludes_uncommitted_writer_changes(self):
        snapshot = self.directory / 'wal-copy.sqlite3'
        writer = sqlite3.connect(self.source)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('UPDATE runs SET state=?', ('uncommitted invalid json',))
            report = backup_save(self.source, snapshot)
            self.assertTrue(report['valid'])
            self.assertTrue(report['compatible'])
            self.assertEqual(1, report['run_count'])
            self.assertEqual('uncommitted invalid json', writer.execute('SELECT state FROM runs').fetchone()[0])
        finally:
            writer.rollback()
            writer.close()
        self.assertTrue(inspect_save(self.source)['compatible'])

    def test_empty_initialized_save_can_be_inspected_copied_and_restored(self):
        empty = self.directory / 'empty.sqlite3'
        service = RunService(empty)
        service.close()
        report = inspect_save(empty)
        self.assertTrue(report['valid'])
        self.assertTrue(report['compatible'])
        self.assertEqual(0, report['run_count'])
        snapshot, target = self.directory / 'empty-backup.sqlite3', self.directory / 'empty-restored.sqlite3'
        backup_save(empty, snapshot)
        restore_save(snapshot, target)
        self.assertEqual(0, inspect_save(target)['run_count'])

    def test_incompatible_versions_remain_archivable_but_cannot_be_restored(self):
        changes = (
            ('save', lambda s: s.update(schema_version=999)),
            ('content', lambda s: s.update(content_version='future-content')),
            ('engine', lambda s: s['battle']['checkpoint'].update(engine_version='999.0.0')),
            ('ruleset', lambda s: s['battle']['checkpoint'].update(ruleset='future-ruleset')),
            ('format', lambda s: s['battle']['checkpoint'].update(format='future-format')),
        )
        for label, mutate in changes:
            with self.subTest(label=label):
                self.source.write_bytes(self.template_bytes)
                self.mutate_state(mutate)
                before = self.source.read_bytes()
                report = inspect_save(self.source)
                self.assertTrue(report['valid'])
                self.assertFalse(report['compatible'])
                self.assertTrue(report['reasons'])
                snapshot = self.directory / (label + '-archive.sqlite3')
                self.assertFalse(backup_save(self.source, snapshot)['compatible'])
                target = self.directory / (label + '-restore.sqlite3')
                with self.assertRaises(ValueError):
                    restore_save(snapshot, target)
                self.assertFalse(target.exists())
                self.assertEqual(before, self.source.read_bytes())

    def test_existing_output_is_preserved_for_backup_and_restore(self):
        output = self.directory / 'existing.sqlite3'
        sentinel = b'operator file must not be replaced'
        output.write_bytes(sentinel)
        before = self.source.read_bytes()
        for operation in (backup_save, restore_save):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises((ValueError, FileExistsError)):
                    operation(self.source, output)
                self.assertEqual(sentinel, output.read_bytes())
                self.assertEqual(before, self.source.read_bytes())

    def test_source_cannot_be_used_as_its_own_output(self):
        before = self.source.read_bytes()
        for operation in (backup_save, restore_save):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises((ValueError, FileExistsError)):
                    operation(self.source, self.source)
                self.assertEqual(before, self.source.read_bytes())

    def test_hard_link_to_source_is_not_overwritten(self):
        alias = self.directory / 'source-alias.sqlite3'
        try:
            os.link(self.source, alias)
        except OSError as error:
            self.skipTest('Hard links unavailable: ' + str(error))
        before = self.source.read_bytes()
        for operation in (backup_save, restore_save):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises((ValueError, FileExistsError)):
                    operation(self.source, alias)
                self.assertEqual(before, self.source.read_bytes())
                self.assertEqual(before, alias.read_bytes())

    def test_dangling_output_symlink_is_preserved_without_creating_its_target(self):
        target = self.directory / 'symlink-target.sqlite3'
        alias = self.directory / 'dangling-output.sqlite3'
        try:
            alias.symlink_to(target)
        except OSError as error:
            self.skipTest('Symbolic links unavailable: ' + str(error))
        before = self.source.read_bytes()
        for operation in (backup_save, restore_save):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises((ValueError, FileExistsError)):
                    operation(self.source, alias)
                self.assertTrue(alias.is_symlink())
                self.assertFalse(target.exists())
                self.assertEqual(before, self.source.read_bytes())

    def test_restore_requires_exclusive_lease_even_if_target_does_not_exist(self):
        target = self.directory / 'leased.sqlite3'
        lease = DatabaseLease(target)
        try:
            lease.acquire()
            with self.assertRaises(RuntimeError):
                restore_save(self.source, target)
            self.assertFalse(target.exists())
        finally:
            lease.close()
        self.assertTrue(restore_save(self.source, target)['compatible'])

    def test_missing_file_is_not_created_during_inspection(self):
        missing = self.directory / 'missing.sqlite3'
        with self.assertRaises((ValueError, FileNotFoundError)):
            inspect_save(missing)
        self.assertFalse(missing.exists())

    def test_corrupt_database_and_wrong_sqlite_schema_are_rejected(self):
        corrupt = self.directory / 'corrupt.sqlite3'
        corrupt.write_bytes(b'this is not an sqlite database')
        other = self.directory / 'other.sqlite3'
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute('CREATE TABLE unrelated(value TEXT)')
        for source in (corrupt, other):
            with self.subTest(source=source.name):
                before = source.read_bytes()
                with self.assertRaises(ValueError):
                    inspect_save(source)
                target = self.directory / (source.stem + '-copy.sqlite3')
                with self.assertRaises(ValueError):
                    backup_save(source, target)
                self.assertFalse(target.exists())
                self.assertEqual(before, source.read_bytes())

    def test_invalid_json_mismatched_id_and_token_hash_are_rejected(self):
        invalid_updates = (
            ('json', 'UPDATE runs SET state=?', ('{',)),
            ('id', 'UPDATE runs SET id=?', ('different-row-id',)),
            ('hash', 'UPDATE runs SET token_hash=?', ('invalid hash',)),
        )
        for label, sql, parameters in invalid_updates:
            with self.subTest(label=label):
                self.source.write_bytes(self.template_bytes)
                with closing(sqlite3.connect(self.source)) as connection, connection:
                    connection.execute(sql, parameters)
                before = self.source.read_bytes()
                with self.assertRaises(ValueError):
                    inspect_save(self.source)
                self.assertEqual(before, self.source.read_bytes())

    def test_cli_inspect_backup_and_restore_print_only_summary_json(self):
        inspected = self.cli('inspect', self.source)
        self.assertEqual(0, inspected.returncode, inspected.stderr)
        self.assertTrue(json.loads(inspected.stdout)['compatible'])
        self.assertNotIn(self.token, inspected.stdout + inspected.stderr)
        self.assertNotIn(self.run_id, inspected.stdout + inspected.stderr)
        self.assertNotIn('token_hash', inspected.stdout + inspected.stderr)
        snapshot = self.directory / 'cli-backup.sqlite3'
        copied = self.cli('backup', self.source, snapshot)
        self.assertEqual(0, copied.returncode, copied.stderr)
        self.assertTrue(json.loads(copied.stdout)['valid'])
        target = self.directory / 'cli-restored.sqlite3'
        restored = self.cli('restore', snapshot, target)
        self.assertEqual(0, restored.returncode, restored.stderr)
        self.assertEqual(1, json.loads(restored.stdout)['run_count'])

    def test_cli_error_is_nonzero_and_never_replaces_existing_file(self):
        target = self.directory / 'protected.sqlite3'
        target.write_bytes(b'protected')
        result = self.cli('restore', self.source, target)
        self.assertEqual(2, result.returncode)
        self.assertTrue(result.stderr.strip())
        self.assertEqual('', result.stdout)
        self.assertEqual(b'protected', target.read_bytes())
        self.assertNotIn(self.token, result.stderr)


if __name__ == '__main__':
    unittest.main()
