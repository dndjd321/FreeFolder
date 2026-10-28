"""A prespecified opt-in rescue rule cannot migrate existing expeditions."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from services.engine_api import choose_legal_action, default_opponents
from services.run_balance import DEFAULT_PROFILE, LEGACY_PROFILE, RESCUE_PROFILE, SUPPORTED_PROFILES
from services.run_service import RunService, SaveStorageError
from services.save_tools import backup_save, restore_save
from test_run_service import FakeBattleClient


class RunBalanceProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'profiles.sqlite3'
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client, new_run_balance_profile=RESCUE_PROFILE)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def other_service(self, profile=LEGACY_PROFILE):
        service = RunService(':memory:', client_factory=FakeBattleClient, new_run_balance_profile=profile)
        self.addCleanup(service.close)
        return service

    def prepare(self, *, service=None, difficulty='normal', index=2, relics=None):
        service = service or self.service
        run = service.create(default_opponents(), seed='balance-profile', difficulty=difficulty)
        state = service._load(run['run_id'], run['token'])
        state.update(node_index=index, completed_nodes=index, node=service._node(state, index), relics=copy.deepcopy(relics or {}))
        for slot, resource in enumerate(state['resources']):
            resource.update(hp=[0, 0, 40][slot], status=['slp', 'brn', 'psn'][slot], sleepTurns=2 if slot == 0 else None)
            for move in resource['moves']:
                move['pp'] = slot + 3
        state['resources'][0]['item'] = ''
        service._save(state)
        return service.get(run['run_id'], run['token'])

    def state(self, run):
        return self.service._load(run['run_id'], run['token'])

    def row(self, run):
        return self.service.db.execute('SELECT state FROM runs WHERE id=?', (run['run_id'],)).fetchone()[0]

    def test_candidate_is_never_the_default_and_constructor_rejects_unknown_profiles(self):
        self.assertEqual(LEGACY_PROFILE, DEFAULT_PROFILE)
        self.assertEqual((LEGACY_PROFILE, RESCUE_PROFILE), SUPPORTED_PROFILES)
        service = RunService(':memory:', client_factory=FakeBattleClient)
        try:
            run = service.create(default_opponents())
            self.assertEqual(LEGACY_PROFILE, run['balance_profile'])
            self.assertEqual(LEGACY_PROFILE, service._load(run['run_id'], run['token'])['balance_profile'])
        finally:
            service.close()
        path = Path(self.directory.name) / 'invalid.sqlite3'
        for profile in (None, '', True, 1, [], {}, 'future-profile'):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                RunService(path, new_run_balance_profile=profile)
            self.assertFalse(path.exists())

    def test_explicit_normal_rescue_revives_each_entry_dead_once_and_heals_only_entry_living(self):
        run = self.prepare()
        before = self.state(run)
        preview = next(row['preview'] for row in run['rest_options'] if row['id'] == 'revive')
        self.assertEqual((2, 150, 0), (preview['revived'], preview['hp_gain'], preview['pp_gain']))
        result = self.service.rest(run['run_id'], run['token'], 'revive')
        after = self.state(run)
        self.assertEqual([50, 50, 90], [row['hp'] for row in after['resources']])
        self.assertEqual(['', '', 'psn'], [row['status'] for row in after['resources']])
        self.assertEqual([None, None, None], [row['sleepTurns'] for row in after['resources']])
        self.assertEqual(before['team'], after['team'])
        self.assertEqual(before['money'], after['money'])
        for old, new in zip(before['resources'], after['resources']):
            self.assertEqual(old['item'], new['item'])
            self.assertEqual(old['moves'], new['moves'])
            self.assertEqual(old['slot'], new['slot'])
        self.assertEqual('path', result['phase'])
        self.assertEqual((150, 2, 0), tuple(result['journal']['totals'][key] for key in ('hp_recovered', 'revived', 'pp_recovered')))
        self.assertEqual({'option': 'revive'}, result['journal']['events'][-1]['details'])

    def test_rescue_uses_existing_integer_rounding_and_caps_living_healing(self):
        run = self.prepare()
        state = self.state(run)
        for row in state['resources']:
            row['maxhp'] = 101
        state['resources'][2]['hp'] = 99
        self.service._save(state)
        result = self.service.rest(run['run_id'], run['token'], 'revive')
        self.assertEqual([50, 50, 101], [row['resource']['hp'] for row in result['party']])
        self.assertEqual(102, result['journal']['totals']['hp_recovered'])

    def test_rest_bonuses_run_once_without_boosting_rescue_hp_or_pp(self):
        run = self.prepare(relics={'camp-kit': 3, 'ether-case': 3, 'clean-blanket': 1, 'revive-charm': 1})
        state = self.state(run)
        moves = copy.deepcopy([row['moves'] for row in state['resources']])
        revive = self.service._revive
        fractions = []
        def observe(resources, fraction):
            fractions.append(fraction)
            return revive(resources, fraction)
        self.service._revive = observe
        self.assertTrue(self.service._apply_rest(state, 'revive'))
        self.assertEqual([.5, .5, .25], fractions)
        self.assertEqual([50, 50, 90], [row['hp'] for row in state['resources']])
        self.assertEqual(['', '', ''], [row['status'] for row in state['resources']])
        self.assertEqual([None, None, None], [row['sleepTurns'] for row in state['resources']])
        self.assertEqual(moves, [row['moves'] for row in state['resources']])

    def test_rescue_requires_an_entry_fainted_member_even_with_rest_bonuses(self):
        run = self.prepare(relics={'clean-blanket': 1, 'revive-charm': 1})
        state = self.state(run)
        for row in state['resources']:
            row['hp'] = 30
        self.service._save(state)
        run = self.service.get(run['run_id'], run['token'])
        option = next(row for row in run['rest_options'] if row['id'] == 'revive')
        self.assertFalse(option['available'])
        self.assertFalse(option['preview']['available'])
        before = self.row(run)
        with self.assertRaises(ValueError):
            self.service.rest(run['run_id'], run['token'], 'revive')
        self.assertEqual(before, self.row(run))

    def test_hard_other_nodes_and_other_options_keep_legacy_effects(self):
        legacy = self.other_service()
        relics = {'camp-kit': 1, 'ether-case': 1, 'clean-blanket': 1, 'revive-charm': 1}
        for difficulty in ('normal', 'hard'):
            for index in (2, 8, 14, 18):
                candidate = self.prepare(difficulty=difficulty, index=index, relics=relics)
                reference = self.prepare(service=legacy, difficulty=difficulty, index=index, relics=relics)
                candidate_state = self.state(candidate)
                reference_state = legacy._load(reference['run_id'], reference['token'])
                for option in ('heal', 'pp', 'cure', 'revive'):
                    if difficulty == 'normal' and index == 2 and option == 'revive':
                        continue
                    with self.subTest(difficulty=difficulty, index=index, option=option):
                        actual, expected = copy.deepcopy(candidate_state), copy.deepcopy(reference_state)
                        self.service._apply_rest(actual, option)
                        legacy._apply_rest(expected, option)
                        self.assertEqual(expected['resources'], actual['resources'])

    def test_public_labels_and_upcoming_rest_guidance_match_the_selected_profile(self):
        run = self.prepare()
        option = next(row for row in run['rest_options'] if row['id'] == 'revive')
        self.assertEqual(RESCUE_PROFILE, run['balance_profile'])
        self.assertIn('모두', option['label'])
        self.assertIn('살아 있던', option['label'])
        self.assertIn('살아 있는 동료', run['node']['preparation'])
        state = self.state(run)
        state.update(node_index=1, node=self.service._node(state, 1))
        preview = self.service._node_preview(self.service._node(state, 2), state)
        self.assertIn('모두', preview['preparation'])
        for profile, difficulty, index in ((LEGACY_PROFILE, 'normal', 2), (RESCUE_PROFILE, 'hard', 2), (RESCUE_PROFILE, 'normal', 8)):
            service = self.other_service(profile)
            run = self.prepare(service=service, difficulty=difficulty, index=index)
            option = next(row for row in run['rest_options'] if row['id'] == 'revive')
            self.assertIn('1마리', option['label'])
            self.assertNotIn('모두', run['node']['preparation'])

    def test_old_missing_profile_keeps_legacy_future_encounters_rewards_and_revival(self):
        run = self.prepare()
        state = self.state(run)
        state.pop('balance_profile')
        self.service._save(state)
        before = self.row(run)
        current = self.service.get(run['run_id'], run['token'])
        self.assertEqual(LEGACY_PROFILE, current['balance_profile'])
        self.assertEqual(before, self.row(run))
        legacy = self.other_service()
        reference = copy.deepcopy(state)
        reference['balance_profile'] = LEGACY_PROFILE
        for index, kind in enumerate(self.service.content['nodes']):
            with self.subTest(index=index):
                old_node = self.service._node(state, index)
                self.assertEqual(legacy._node(reference, index), old_node)
                actual, expected = {**state, 'node_index': index, 'node': old_node}, {**reference, 'node_index': index, 'node': old_node}
                self.assertEqual(legacy._battle_seed(expected), self.service._battle_seed(actual))
                self.assertEqual(legacy._make_rewards(copy.deepcopy(expected)), self.service._make_rewards(copy.deepcopy(actual)))
                if kind in ('battle', 'elite', 'boss'):
                    self.assertEqual(legacy._enemy(expected), self.service._enemy(actual))
        result = self.service.rest(run['run_id'], run['token'], 'revive')
        self.assertEqual([50, 0, 40], [row['resource']['hp'] for row in result['party']])
        self.assertNotIn('balance_profile', self.state(run))

    def test_unknown_explicit_profile_preserves_the_save_without_cache_activity(self):
        run = self.service.create(default_opponents())
        state = self.state(run)
        state['balance_profile'] = 'future-profile-v9'
        self.service._save(state)
        before = self.row(run)
        self.service._maintain_cache = lambda *_args, **_kwargs: self.fail('Unsupported profile touched the cache')
        for operation in (self.service.get, self.service.resume, self.service.battle_start):
            with self.assertRaises(ValueError):
                operation(run['run_id'], run['token'])
            self.assertEqual(before, self.row(run))

    def test_keyed_creation_replay_is_pinned_even_when_the_new_run_default_changes(self):
        credentials = {'creation_id': '01' * 32, 'creation_secret': 'ab' * 32}
        run = self.service.create(default_opponents(), seed='pinned', **credentials)
        self.service.close()
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client, new_run_balance_profile=LEGACY_PROFILE)
        recovered = self.service.create(default_opponents(), seed='pinned', **credentials)
        self.assertEqual(run, recovered)
        self.assertEqual(RESCUE_PROFILE, recovered['balance_profile'])
        self.assertEqual(LEGACY_PROFILE, self.service.create(default_opponents())['balance_profile'])
        state = self.state(run)
        state.pop('balance_profile')
        self.service._save(state)
        self.service.close()
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client, new_run_balance_profile=RESCUE_PROFILE)
        recovered = self.service.create(default_opponents(), seed='pinned', **credentials)
        self.assertEqual(LEGACY_PROFILE, recovered['balance_profile'])
        self.assertNotIn('balance_profile', self.state(run))

    def test_failed_rest_write_rolls_back_rescue_resources_profile_and_journal_together(self):
        run = self.prepare()
        before = self.row(run)
        self.service.db.execute('PRAGMA query_only=ON')
        try:
            with self.assertRaises(SaveStorageError):
                self.service.rest(run['run_id'], run['token'], 'revive')
        finally:
            self.service.db.execute('PRAGMA query_only=OFF')
        self.assertEqual(before, self.row(run))
        result = self.service.rest(run['run_id'], run['token'], 'revive')
        self.assertEqual((RESCUE_PROFILE, 1, 150), (result['balance_profile'], result['journal']['counts']['rest'],
                                                 result['journal']['totals']['hp_recovered']))

    def test_online_backup_and_restore_keep_the_opt_in_effect_under_a_legacy_default(self):
        run = self.prepare()
        backup, restored = [Path(self.directory.name) / name for name in ('backup.sqlite3', 'restored.sqlite3')]
        self.assertTrue(backup_save(self.path, backup)['compatible'])
        self.assertTrue(restore_save(backup, restored)['compatible'])
        service = RunService(restored, client_factory=FakeBattleClient, new_run_balance_profile=LEGACY_PROFILE)
        try:
            recovered = service.get(run['run_id'], run['token'])
            self.assertEqual(run, recovered)
            result = service.rest(run['run_id'], run['token'], 'revive')
            self.assertEqual([50, 50, 90], [row['resource']['hp'] for row in result['party']])
            self.assertEqual(RESCUE_PROFILE, result['balance_profile'])
        finally:
            service.close()


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunBalanceProfileTests(unittest.TestCase):
    def test_missing_legacy_profile_restores_the_exact_canonical_checkpoint_under_a_candidate_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.sqlite3'
            service = RunService(path)
            try:
                run = service.create(default_opponents(), seed='legacy-profile-checkpoint')
                run = service.battle_start(run['run_id'], run['token'])
                run = service.battle_action(run['run_id'], run['token'], choose_legal_action(run['battle']['view']['request']), 0, 'before-upgrade')
                state = service._load(run['run_id'], run['token'])
                state.pop('balance_profile')
                service._save(state)
                checkpoint = copy.deepcopy(state['battle']['checkpoint'])
            finally:
                service.close()
            service = RunService(path, new_run_balance_profile=RESCUE_PROFILE)
            try:
                restored = service.get(run['run_id'], run['token'])
                without_id = lambda value: {key: item for key, item in value.items() if key != 'battle_id'}
                self.assertEqual(without_id(run['battle']['view']), without_id(restored['battle']['view']))
                self.assertEqual(LEGACY_PROFILE, restored['balance_profile'])
                stored = service._load(run['run_id'], run['token'])
                self.assertEqual(checkpoint, stored['battle']['checkpoint'])
                self.assertNotIn('balance_profile', stored)
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
