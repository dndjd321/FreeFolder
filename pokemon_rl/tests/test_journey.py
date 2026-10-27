"""Public route hints describe the actual saved choices without enemy leaks."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from services.engine_api import default_opponents
from services.run_api import install_run_routes
from services.run_service import RunService
from test_engine_api import http
from test_run_service import FakeBattleClient


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeBattleClient()
        self.service = RunService(':memory:', client_factory=lambda: self.client)

    def tearDown(self):
        self.service.close()

    def create(self):
        return self.service.create(default_opponents(), seed='journey-contract')

    def test_starting_journey_uses_the_real_schedule_and_first_boss(self):
        run = self.create()
        journey = run['journey']
        self.assertEqual(self.service.content['nodes'], [p['kind'] for p in journey['nodes']])
        self.assertEqual('current', journey['nodes'][0]['status'])
        self.assertTrue(all(p['status'] == 'upcoming' for p in journey['nodes'][1:]))
        boss = journey['next_boss']
        self.assertEqual((4, 4, '폭풍의 지휘관'), (boss['index'], boss['distance'], boss['label']))
        self.assertIn('비', boss['theme_label'])
        self.assertTrue(boss['preparation'])

    def test_current_and_defeated_boss_preview_advance_from_completed_nodes(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        state.update(node_index=4, completed_nodes=4, node=self.service._node(state, 4))
        before = self.service._journey(state)
        self.assertEqual(0, before['next_boss']['distance'])
        self.assertEqual('current', before['nodes'][4]['status'])
        state.update(completed_nodes=5, phase='reward')
        after = self.service._journey(state)
        self.assertEqual(9, after['next_boss']['index'])
        self.assertEqual('모래의 수호자', after['next_boss']['label'])
        self.assertEqual('completed', after['nodes'][4]['status'])

    def test_terminal_journey_does_not_suggest_another_boss(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        state.update(node_index=19, completed_nodes=20, phase='complete', node=self.service._node(state, 19))
        journey = self.service._journey(state)
        self.assertIsNone(journey['next_boss'])
        self.assertTrue(all(p['status'] == 'completed' for p in journey['nodes']))
        state.update(completed_nodes=19, phase='failed')
        self.assertIsNone(self.service._journey(state)['next_boss'])

    def test_schedule_indices_are_derived_instead_of_hard_coded(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        self.service.content['nodes'].insert(0, 'rest')
        guide = self.service.route_guide()
        self.assertEqual([5, 10, 20], [p['index'] for p in guide['bosses']])
        self.assertEqual('rain', self.service._node(state, 5)['boss'])
        self.assertEqual('폭풍의 지휘관', guide['nodes'][5]['label'])

    def test_paths_state_the_same_difficulty_and_real_theme_difference(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        self.service._next_path(state)
        self.service._save(state)
        before = copy.deepcopy(state)
        public = self.service.get(run['run_id'], run['token'])
        self.assertEqual(2, len(public['paths']))
        first, second = public['paths']
        self.assertNotEqual(first['theme_label'], second['theme_label'])
        self.assertEqual(first['danger_level'], second['danger_level'])
        self.assertEqual(first['reward'], second['reward'])
        self.assertIn('위험도 분류와 기본 보상은 같고', public['journey']['branch_note'])
        self.assertEqual(before, self.service._load(run['run_id'], run['token']))
        selected = self.service.choose_path(run['run_id'], run['token'], second['id'])
        self.assertEqual(second['description'], selected['node']['description'])
        self.assertNotIn('description', self.service._load(run['run_id'], run['token'])['node'])

    def test_preview_reward_reflects_current_relics_and_final_completion(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        state['relics'] = {'lucky-coin': 1, 'reserve-fund': 1}
        normal = self.service._node_preview(self.service._node(state, 0), state)
        elite = self.service._node_preview(self.service._node(state, 3), state)
        final = self.service._node_preview(self.service._node(state, 19), state)
        self.assertIn('27골드', normal['reward'])
        self.assertIn('40골드', elite['reward'])
        self.assertIn('54골드', final['reward'])
        self.assertIn('탐험 완주', final['reward'])
        self.assertNotIn('후보', final['reward'])

    def test_journey_and_guide_expose_no_private_enemy_build_fields(self):
        run = self.create()
        payload = {'journey': run['journey'], 'node': run['node'], 'guide': self.service.route_guide()}
        forbidden = {'team', 'teams', 'moves', 'ability', 'item', 'evs', 'checkpoint', 'token_hash', 'seed'}
        def walk(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(payload)
        serialized = json.dumps(payload)
        self.assertNotIn('Politoed', serialized)
        self.assertNotIn('Kingdra', serialized)
        self.assertNotIn(run['token'], serialized)

    def test_catalog_guide_is_available_without_starting_an_engine(self):
        app = FastAPI()
        install_run_routes(app, self.service)
        self.assertIsNone(self.service.client)
        status, catalog = asyncio.run(http(app, 'GET', '/api/runs/status'))
        self.assertEqual(200, status)
        self.assertIsNone(self.service.client)
        self.assertEqual([4, 9, 19], [p['index'] for p in catalog['route_guide']['bosses']])
        self.assertEqual(20, len(catalog['route_guide']['nodes']))


if __name__ == '__main__':
    unittest.main()
