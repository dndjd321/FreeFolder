"""Choice displays measure the actual effect without changing saved choices."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from fastapi import FastAPI
from services.engine_api import default_opponents
from services.run_api import install_run_routes
from services.run_creation import creation_request_hash
from services.run_journal import record_event, snapshot
from services.run_preparation import resource_preview
from services.run_service import RunService, SaveStorageError
from test_engine_api import http
from test_run_service import FakeBattleClient


class ChoiceInformationTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeBattleClient()
        self.service = RunService(':memory:', client_factory=lambda: self.client)

    def tearDown(self):
        self.service.db.execute('PRAGMA query_only = OFF')
        self.service.close()

    def prepare(self, kind='heal', amount=None, *, stacks=0):
        run = self.service.create(default_opponents(), seed='choice-information')
        state = self.service._load(run['run_id'], run['token'])
        state.update(phase='reward', relics={('field-medic' if kind == 'heal' else 'ether-case'): stacks})
        state['rewards'] = [{'id': 'legacy-fixed-offer', 'kind': kind, 'amount':
                             (.35 if kind == 'heal' else 5) if amount is None else amount,
                             'label': '저장된 기본 보상 문구'}]
        for slot, resource in enumerate(state['resources']):
            resource.update(hp=[35, 0, 90][slot], status=['brn', 'slp', ''][slot],
                            sleepTurns=2 if slot == 1 else None, item='' if slot == 1 else resource['item'])
            for move in resource['moves']:
                move['pp'] = [2, 3, 19][slot]
        self.service._save(state)
        return run

    def state(self, run):
        return self.service._load(run['run_id'], run['token'])

    def public(self, run):
        return self.service.get(run['run_id'], run['token'])

    def assert_preview_matches(self, preview, resources):
        actual = resource_preview(resources, lambda rows: None)
        self.assertEqual(preview['after'], actual['before'])
        self.assertEqual([row['after'] for row in preview['party']],
                         [row['before'] for row in actual['party']])

    def test_catalog_metadata_is_separate_from_creation_payloads_and_starts_no_worker(self):
        app = FastAPI()
        install_run_routes(app, self.service)
        original_pool = copy.deepcopy(self.service.content['recruit_pool'])
        original_team = [copy.deepcopy(self.service.pool[key]) for key in ('charizard', 'garchomp', 'clefable')]
        fingerprint = creation_request_hash(original_team, seed='pending-before-update')
        with patch.object(self.service, '_enemy', side_effect=AssertionError('private enemy requested')):
            status, catalog = asyncio.run(http(app, 'GET', '/api/runs/status'))
        self.assertEqual(200, status)
        self.assertIsNone(self.service.client)
        self.assertEqual(0, self.client.counter)
        self.assertEqual(original_pool, catalog['starter_pool'])
        self.assertEqual(original_team, catalog['starter_team'])
        self.assertEqual(fingerprint, creation_request_hash(catalog['starter_team'], seed='pending-before-update'))
        self.assertEqual(30, len(catalog['starter_details']))
        self.assertEqual('화염방사', catalog['starter_details']['charizard']['moves'][0]['name_ko'])
        catalog['starter_details']['charizard']['types'].append('fake-type')
        self.assertEqual(original_pool, self.service.content['recruit_pool'])
        self.assertNotIn('fake-type', self.service.starter_details()['charizard']['types'])

    def test_all_offered_build_info_is_whitelisted_without_guessed_resources_or_private_fields(self):
        details = self.service.starter_details()
        self.assertEqual({'electric', 'water'}, set(details['rotomwash']['types']))
        self.assertEqual({'ghost', 'fairy'}, set(details['mimikyu']['types']))
        for key, build in details.items():
            with self.subTest(species=key):
                self.assertEqual({'species', 'types', 'moves', 'ability', 'item'}, set(build))
                self.assertTrue(build['types'])
                self.assertEqual(len(self.service.pool[key]['moves']), len(build['moves']))
                for move in build['moves']:
                    self.assertEqual({'id', 'name_ko', 'name_en', 'type', 'category', 'power'}, set(move))
                    self.assertTrue(move['name_ko'])
                    self.assertTrue(move['type'])
                    self.assertIn(move['category'], ('physical', 'special', 'status'))
        build = copy.deepcopy(self.service.pool['charizard'])
        build.update(checkpoint={'private': True}, token='private-token', private_enemy='never-public')
        before = copy.deepcopy(build)
        payload = json.dumps(self.service._build_info(build), ensure_ascii=False)
        for secret in ('checkpoint', 'private-token', 'private_enemy', 'maxpp', 'maxhp'):
            self.assertNotIn(secret, payload)
        self.assertEqual(before, build)
        self.assertIsNone(self.service.client)

    def test_recruit_info_is_detached_and_leaves_offered_ids_and_canonical_teams_unchanged(self):
        run = self.service.create(default_opponents(), seed='recruit-choice-info')
        state = self.state(run)
        self.service._offer_recruits(state, 'reward')
        self.service._save(state)
        before = copy.deepcopy(self.state(run))
        calls = self.client.counter
        with patch.object(self.service, '_enemy', side_effect=AssertionError('private enemy requested')):
            public = self.public(run)
        for stored, offered in zip(before['recruit_candidates'], public['recruit_candidates']):
            self.assertEqual(stored['id'], offered['id'])
            self.assertEqual(stored['team'], offered['team'])
            self.assertEqual(stored['team']['species'], offered['build_info']['species'])
            offered['build_info']['moves'][0]['name_ko'] = 'changed public copy'
        self.assertEqual(before, self.state(run))
        self.assertEqual(calls, self.client.counter)
        self.assertNotIn('changed public copy', json.dumps(self.public(run), ensure_ascii=False))

    def test_heal_labels_and_previews_match_committed_stacks_clamps_and_living_only_effects(self):
        for stacks, expected_hp, percent in ((0, [70, 0, 100], '35%'),
                                             # Keep the existing float+floor effect (44 HP at maxHP 100),
                                             # rather than changing balance while fixing its display.
                                             (1, [79, 0, 100], '45%'),
                                             (3, [100, 0, 100], '65%')):
            with self.subTest(stacks=stacks):
                run = self.prepare(stacks=stacks)
                before = self.state(run)
                reward = self.public(run)['rewards'][0]
                self.assertIn('살아 있는', reward['label'])
                self.assertIn(percent, reward['label'])
                self.assertEqual('legacy-fixed-offer', reward['id'])
                self.assertEqual(.35, reward['amount'])
                self.assertEqual(before, self.state(run))
                result = self.service.choose_reward(run['run_id'], run['token'], reward['id'])
                saved = self.state(run)
                self.assertEqual(expected_hp, [p['hp'] for p in saved['resources']])
                self.assert_preview_matches(reward['preview'], saved['resources'])
                self.assertEqual(['brn', 'slp', ''], [p['status'] for p in saved['resources']])
                self.assertEqual(2, saved['resources'][1]['sleepTurns'])
                self.assertEqual('', saved['resources'][1]['item'])
                self.assertEqual(reward['label'], result['journal']['events'][-1]['details']['label'])

    def test_pp_labels_and_previews_include_stacks_even_for_fainted_members_without_changing_hp(self):
        for stacks, amount in ((0, 5), (1, 7), (3, 11)):
            with self.subTest(stacks=stacks):
                run = self.prepare('pp', stacks=stacks)
                before = self.state(run)
                reward = self.public(run)['rewards'][0]
                self.assertIn(f'PP {amount} 회복', reward['label'])
                self.service.choose_reward(run['run_id'], run['token'], reward['id'])
                saved = self.state(run)
                self.assertEqual([35, 0, 90], [p['hp'] for p in saved['resources']])
                self.assertEqual([2 + amount, 3 + amount, 20], [p['moves'][0]['pp'] for p in saved['resources']])
                self.assert_preview_matches(reward['preview'], saved['resources'])
                self.assertEqual([p['status'] for p in before['resources']], [p['status'] for p in saved['resources']])

    def test_legacy_custom_amounts_use_the_saved_amount_and_preserve_older_journal_labels(self):
        for kind, base, label in (('heal', .2, '30%'), ('pp', 3, 'PP 5')):
            with self.subTest(kind=kind):
                run = self.prepare(kind, base, stacks=1)
                state = self.state(run)
                record_event(state, 'reward', snapshot(state),
                             {'reward_id': 'past', 'reward_kind': 'heal', 'label': '과거 기록의 원문'})
                self.service._save(state)
                before = self.state(run)
                reward = self.public(run)['rewards'][0]
                self.assertIn(label, reward['label'])
                self.assertEqual(base, reward['amount'])
                self.assertEqual('저장된 기본 보상 문구', before['rewards'][0]['label'])
                result = self.service.choose_reward(run['run_id'], run['token'], reward['id'])
                self.assertEqual(before['journal']['events'], result['journal']['events'][:-1])
                self.assertEqual(reward['label'], result['journal']['events'][-1]['details']['label'])
                self.assert_preview_matches(reward['preview'], self.state(run)['resources'])

    def test_repeated_public_reads_leave_raw_choices_rng_revision_resources_and_worker_unchanged(self):
        run = self.prepare(stacks=1)
        before = copy.deepcopy(self.state(run))
        future = self.service._make_rewards(copy.deepcopy(before))
        calls = self.client.counter
        first, second = self.public(run), self.public(run)
        self.assertEqual(first, second)
        self.assertEqual(before, self.state(run))
        self.assertEqual(future, self.service._make_rewards(copy.deepcopy(self.state(run))))
        self.assertEqual(calls, self.client.counter)
        first['rewards'][0]['preview']['party'][0]['after']['hp'] = 0
        self.assertEqual(second, self.public(run))

    def test_failed_reward_write_rolls_back_choice_resources_and_journal_then_retries_once(self):
        run = self.prepare(stacks=1)
        before = copy.deepcopy(self.state(run))
        reward = self.public(run)['rewards'][0]
        self.service.db.execute('PRAGMA query_only = ON')
        with self.assertRaises(SaveStorageError):
            self.service.choose_reward(run['run_id'], run['token'], reward['id'])
        self.service.db.execute('PRAGMA query_only = OFF')
        self.assertEqual(before, self.state(run))
        result = self.service.choose_reward(run['run_id'], run['token'], reward['id'])
        self.assert_preview_matches(reward['preview'], self.state(run)['resources'])
        self.assertEqual(before['journal']['counts']['reward'] + 1, result['journal']['counts']['reward'])
        with self.assertRaises(ValueError):
            self.service.choose_reward(run['run_id'], run['token'], reward['id'])


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for real reward resources')
class RealChoiceInformationTests(unittest.TestCase):
    def test_actual_engine_maxhp_and_pp_caps_match_each_reward_preview(self):
        service = RunService(':memory:')
        try:
            for kind in ('heal', 'pp'):
                with self.subTest(kind=kind):
                    run = service.create(default_opponents(), seed='real-choice-information')
                    state = service._load(run['run_id'], run['token'])
                    state.update(phase='reward', relics={'field-medic': 1, 'ether-case': 1},
                                 rewards=[{'id': 'real-saved-offer', 'kind': kind,
                                           'amount': .35 if kind == 'heal' else 5, 'label': '기본 문구'}])
                    for slot, resource in enumerate(state['resources']):
                        resource['hp'] = 1 if slot == 0 else 0 if slot == 1 else resource['maxhp'] - 1
                        for move in resource['moves']:
                            move['pp'] = 0
                    service._save(state)
                    reward = service.get(run['run_id'], run['token'])['rewards'][0]
                    if kind == 'heal':
                        self.assertEqual(155, state['resources'][0]['maxhp'])
                        self.assertEqual(69, reward['preview']['party'][0]['hp_gain'])
                    else:
                        self.assertTrue(all(move['pp'] == 7 for row in reward['preview']['party'] for move in row['after']['moves']))
                    service.choose_reward(run['run_id'], run['token'], reward['id'])
                    actual = service._load(run['run_id'], run['token'])['resources']
                    self.assertEqual(reward['preview']['after'], resource_preview(actual, lambda rows: None)['before'])
        finally:
            service.close()
