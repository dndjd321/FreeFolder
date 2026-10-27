"""Run opponents use authored roles and only their own permitted battle view."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import asyncio

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))
from services.engine_api import choose_legal_action, default_opponents
from services.run_service import RunService
from services.run_api import install_run_routes
from fastapi import FastAPI
from test_engine_api import http
from test_run_service import FakeBattleClient


class PolicySpy:
    def __init__(self):
        self.views = []

    def predict(self, request, view):
        self.views.append(copy.deepcopy(view))
        return choose_legal_action(request)


class RunAITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'runs.sqlite3'
        self.client = FakeBattleClient()
        self.client.turns_to_win = 20
        self.service = RunService(self.path, client_factory=lambda: self.client)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def prepare(self, index, difficulty='normal'):
        run = self.service.create(default_opponents(), seed='run-ai-contract', difficulty=difficulty)
        state = self.service._load(run['run_id'], run['token'])
        state.update(node_index=index, completed_nodes=index, node=self.service._node(state, index))
        self.service._save(state)
        return self.service.battle_start(run['run_id'], run['token'])

    def test_mixed_boss_gets_physical_and_special_training_without_content_mutation(self):
        before = copy.deepcopy(self.service.content)
        team = self.service._enemy({'node': {'kind': 'boss', 'boss': 'sand'}})
        self.assertEqual(['Tyranitar', 'Excadrill', 'Rotom-Wash'], [p['species'] for p in team])
        self.assertEqual([252, 252, 0], [p['evs'].get('atk', 0) for p in team])
        self.assertEqual([0, 0, 252], [p['evs'].get('spa', 0) for p in team])
        self.assertTrue(all(sum(p['evs'].values()) == 508 for p in team))
        self.assertEqual(before, self.service.content)

    def test_explicit_legal_evs_are_preserved_and_returned_builds_are_copies(self):
        explicit = {'hp': 252, 'def': 252, 'spe': 4}
        self.service.content['bosses'][0]['team'][0]['evs'] = copy.deepcopy(explicit)
        self.service.content['bosses'][0]['team'][1]['evs'] = {}
        team = self.service._enemy({'node': {'kind': 'boss', 'boss': 'rain'}})
        self.assertEqual(explicit, team[0]['evs'])
        self.assertEqual({}, team[1]['evs'])
        team[0]['evs']['hp'] = 0
        self.assertEqual(explicit, self.service.content['bosses'][0]['team'][0]['evs'])

    def test_illegal_explicit_evs_are_not_silently_replaced(self):
        for evs in ({'atk': 253}, {'hp': 252, 'atk': 252, 'spe': 252}, {'atk': True}, {'unknown': 4}, None):
            with self.subTest(evs=evs):
                self.service.content['bosses'][0]['team'][0]['evs'] = evs
                with self.assertRaises(ValueError):
                    self.service._enemy({'node': {'kind': 'boss', 'boss': 'rain'}})

    def test_body_press_trains_defense_instead_of_attack(self):
        evs = self.service._default_enemy_evs({'moves': ['Body Press', 'Iron Defense', 'Protect', 'Rest']})
        self.assertEqual({'hp': 252, 'def': 252, 'spe': 4}, evs)
        defensive = self.service._default_enemy_evs({'moves': ['Wish', 'Protect', 'Toxic', 'Rest']})
        self.assertEqual({'hp': 252, 'def': 252, 'spe': 4}, defensive)

    def test_regular_enemy_build_and_policy_keep_the_existing_behavior(self):
        before = copy.deepcopy(self.service.content['themes'][0]['team'])
        self.assertEqual(before, self.service._enemy({'node': {'kind': 'battle', 'theme': 'water'}}))
        for difficulty in ('normal', 'hard'):
            with self.subTest(difficulty=difficulty):
                run = self.prepare(0, difficulty=difficulty)
                state = self.service._load(run['run_id'], run['token'])
                self.assertEqual('maxpower', state['battle']['ai_policy'])
                spy = PolicySpy()
                self.service._heuristic_policy = spy
                result = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'normal')
                self.assertEqual([], spy.views)
                self.assertEqual(2, result['battle']['view']['turn'])

    def test_default_normal_keeps_maxpower_on_elite_and_boss(self):
        for index in (3, 4):
            with self.subTest(index=index):
                run = self.prepare(index)
                self.assertEqual('normal', run['difficulty'])
                state = self.service._load(run['run_id'], run['token'])
                self.assertEqual('normal', state['difficulty'])
                self.assertEqual('maxpower', state['battle']['ai_policy'])
                spy = PolicySpy()
                self.service._heuristic_policy = spy
                result = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'normal-elite')
                self.assertEqual([], spy.views)
                self.assertEqual(2, result['battle']['view']['turn'])

    def test_new_elite_and_boss_policy_receive_only_the_opponents_own_projection(self):
        for index in (3, 4):
            with self.subTest(index=index):
                run = self.prepare(index, difficulty='hard')
                self.assertEqual('hard', run['difficulty'])
                state = self.service._load(run['run_id'], run['token'])
                self.assertEqual('heuristic-v2', state['battle']['ai_policy'])
                self.assertNotIn('ai_policy', run['battle'])
                spy = PolicySpy()
                self.service._heuristic_policy = spy
                result = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'own-view')
                self.assertEqual(2, result['battle']['view']['turn'])
                self.assertGreaterEqual(len(spy.views), 1)
                for view in spy.views:
                    self.assertEqual('p2', view['side'])
                    self.assertEqual([{'species': 'Unknown', 'revealed': False}], view['opponent'])
                    self.assertNotIn('teams', view)
                    self.assertNotIn('resources', view)

    def test_legacy_battle_without_policy_restarts_with_maxpower_and_keeps_checkpoint(self):
        run = self.prepare(4, difficulty='hard')
        rid, token = run['run_id'], run['token']
        state = self.service._load(rid, token)
        state['battle'].pop('ai_policy')
        legacy_evs = {'hp': 252, 'atk': 4, 'def': 0, 'spa': 252, 'spd': 0, 'spe': 0}
        for pokemon in state['battle']['checkpoint']['data']['teams']['p2']:
            pokemon['evs'] = copy.deepcopy(legacy_evs)
        checkpoint = copy.deepcopy(state['battle']['checkpoint'])
        self.service._save(state)
        self.service.close()
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client)
        def cannot_rebuild_saved_enemies(_):
            raise AssertionError('A saved checkpoint must not rebuild its enemy EVs')
        self.service._enemy = cannot_rebuild_saved_enemies
        spy = PolicySpy()
        self.service._heuristic_policy = spy
        resumed = self.service.resume(rid, token)
        state = self.service._load(rid, token)
        self.assertNotIn('ai_policy', state['battle'])
        self.assertEqual(checkpoint, state['battle']['checkpoint'])
        self.assertTrue(all(p['evs'] == legacy_evs for p in state['battle']['checkpoint']['data']['teams']['p2']))
        self.assertEqual([], spy.views)
        result = self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, resumed['battle']['revision'], 'legacy')
        self.assertEqual([], spy.views)
        self.assertEqual(2, result['battle']['view']['turn'])

    def test_old_save_without_difficulty_is_reported_as_normal(self):
        run = self.prepare(4)
        state = self.service._load(run['run_id'], run['token'])
        state.pop('difficulty')
        state['battle'].pop('ai_policy')
        checkpoint = copy.deepcopy(state['battle']['checkpoint'])
        self.service._save(state)
        resumed = self.service.resume(run['run_id'], run['token'])
        self.assertEqual('normal', resumed['difficulty'])
        private = self.service._load(run['run_id'], run['token'])
        self.assertNotIn('difficulty', private)
        self.assertNotIn('ai_policy', private['battle'])
        self.assertEqual(checkpoint, private['battle']['checkpoint'])

    def test_http_creation_selects_difficulty_and_rejects_invalid_values(self):
        app = FastAPI()
        install_run_routes(app, self.service)
        status, created = asyncio.run(http(app, 'POST', '/api/runs', {
            'team': default_opponents(), 'difficulty': 'hard'}))
        self.assertEqual(200, status)
        self.assertEqual('hard', created['difficulty'])
        self.assertEqual('hard', self.service._load(created['run_id'], created['token'])['difficulty'])
        before = self.service.db.execute('SELECT count(*) FROM runs').fetchone()[0]
        for difficulty in ('expert', '', None, True, [], {}):
            with self.subTest(difficulty=difficulty):
                status, _ = asyncio.run(http(app, 'POST', '/api/runs', {
                    'team': default_opponents(), 'difficulty': difficulty}))
                self.assertEqual(400, status)
        self.assertEqual(before, self.service.db.execute('SELECT count(*) FROM runs').fetchone()[0])
        status, catalog = asyncio.run(http(app, 'GET', '/api/runs/status'))
        self.assertEqual(200, status)
        self.assertEqual('normal', catalog['default_difficulty'])
        self.assertEqual(['normal', 'hard'], [p['id'] for p in catalog['difficulty_options']])

    def test_unknown_saved_policy_is_rejected_without_changing_the_save(self):
        run = self.prepare(4)
        state = self.service._load(run['run_id'], run['token'])
        state['battle']['ai_policy'] = 'future-policy'
        self.service._save(state)
        before = json.dumps(state, sort_keys=True)
        with self.assertRaises(ValueError):
            self.service.resume(run['run_id'], run['token'])
        self.assertEqual(before, json.dumps(self.service._load(run['run_id'], run['token']), sort_keys=True))


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunAITests(unittest.TestCase):
    def test_inferred_mixed_boss_build_is_accepted_by_the_canonical_engine(self):
        service = RunService(':memory:')
        try:
            team = service._enemy({'node': {'kind': 'boss', 'boss': 'sand'}})
            battle = service._client().create({'p1': default_opponents(), 'p2': team}, seed=[1, 2, 3, 4])
            view = service._client().view(battle['battle_id'], 'p2')
            self.assertEqual(3, len(view['own']))
            stored = service._client().checkpoint(battle['battle_id'])['teams']['p2']
            for authored, validated in zip(team, stored):
                self.assertEqual(authored['evs'], {key: value for key, value in validated['evs'].items() if value})
            service._client().close_battle(battle['battle_id'])
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main()
