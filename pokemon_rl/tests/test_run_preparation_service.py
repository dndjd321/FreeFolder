"""Preparation changes order only; recovery previews match committed effects."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from fastapi import FastAPI
from services.engine_api import default_opponents
from services.run_api import install_run_routes
from services.run_preparation import resource_preview
from services.run_service import RunService
from test_engine_api import http
from test_run_service import FakeBattleClient


class RunPreparationServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'prepare.sqlite3'
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client)
        self.app = FastAPI()
        install_run_routes(self.app, self.service)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def prepare(self, index=0, *, relics=None, injured=False):
        run = self.service.create(default_opponents(), seed='preparation')
        state = self.service._load(run['run_id'], run['token'])
        state.update(node_index=index, completed_nodes=index, node=self.service._node(state, index),
                     relics=copy.deepcopy(relics or {}), money=200)
        if injured:
            for slot, resource in enumerate(state['resources']):
                resource.update(hp=[35, 0, 90][slot], status=['brn', 'slp', ''][slot],
                                sleepTurns=2 if slot == 1 else None)
                for move in resource['moves']:
                    move['pp'] = 4 + slot
            state['resources'][1]['item'] = ''
        self.service._save(state)
        return self.service.get(run['run_id'], run['token'])

    def state(self, run):
        return self.service._load(run['run_id'], run['token'])

    def post(self, run, operation, body):
        return asyncio.run(http(self.app, 'POST', '/api/runs/' + run['run_id'] + '/' + operation,
                                body, headers=[(b'authorization', ('Bearer ' + run['token']).encode())]))

    def assert_preview_matches(self, preview, resources):
        actual = resource_preview(resources, lambda rows: None)
        self.assertEqual(preview['after'], actual['before'])
        self.assertEqual([row['after'] for row in preview['party']],
                         [row['before'] for row in actual['party']])

    def test_party_order_preserves_builds_and_every_resource_except_normalized_slots(self):
        run = self.prepare(injured=True)
        state = self.state(run)
        for slot, pokemon in enumerate(state['team']):
            pokemon['evs'] = {'hp': 252, 'spa': 252, 'spe': 4}
            pokemon['ivs'] = {'atk': slot}
            state['resources'][slot]['slot'] = 9 - slot
        self.service._save(state)
        before = copy.deepcopy(self.state(run))
        ordered = self.service.party_order(run['run_id'], run['token'], [2, 0, 1])
        after = self.state(run)
        self.assertEqual([before['team'][i] for i in [2, 0, 1]], after['team'])
        self.assertEqual([{**before['resources'][old], 'slot': new} for new, old in enumerate([2, 0, 1])],
                         after['resources'])
        for field in set(before) - {'team', 'resources', 'run_revision'}:
            self.assertEqual(before[field], after[field], field)
        self.assertEqual(before['run_revision'] + 1, ordered['run_revision'])
        self.assertEqual([0, 1, 2], [p['resource']['slot'] for p in ordered['party']])

    def test_invalid_orders_and_a_fainted_lead_are_rejected_without_saving(self):
        run = self.prepare(injured=True)
        before = self.state(run)
        orders = [None, {}, '201', [], [0, 1], [0, 1, 2, 3], [0, 0, 2],
                  [0, 1, 3], [-1, 1, 2], [True, 0, 2], [0.0, 1, 2], ['0', 1, 2], [1, 0, 2]]
        for order in orders:
            with self.subTest(order=order):
                status, _ = self.post(run, 'party/order', {'order': order, 'run_revision': run['run_revision']})
                self.assertEqual(400, status)
                self.assertEqual(before, self.state(run))

    def test_party_order_is_only_allowed_at_combat_preparation_nodes(self):
        for index in (0, 3, 4):
            run = self.prepare(index)
            status, changed = self.post(run, 'party/order', {'order': [1, 0, 2], 'run_revision': run['run_revision']})
            self.assertEqual(200, status)
            self.assertEqual('Blastoise', changed['party'][0]['species'])
        for index in (2, 6):
            run = self.prepare(index)
            before = self.state(run)
            status, _ = self.post(run, 'party/order', {'order': [1, 0, 2], 'run_revision': run['run_revision']})
            self.assertEqual(400, status)
            self.assertEqual(before, self.state(run))
        for phase in ('battle', 'reward', 'path', 'recruit', 'complete', 'failed'):
            with self.subTest(phase=phase):
                run = self.prepare()
                state = self.state(run)
                state['phase'] = phase
                self.service._save(state)
                before = self.state(run)
                with self.assertRaises(ValueError):
                    self.service.party_order(run['run_id'], run['token'], [1, 0, 2])
                self.assertEqual(before, self.state(run))

    def test_order_requires_authorization_and_the_current_run_revision(self):
        run = self.prepare()
        body = {'order': [1, 0, 2], 'run_revision': run['run_revision']}
        status, _ = self.post({**run, 'token': 'wrong'}, 'party/order', body)
        self.assertEqual(404, status)
        status, _ = self.post(run, 'party/order', {'order': [1, 0, 2]})
        self.assertEqual(409, status)
        status, ordered = self.post(run, 'party/order', body)
        self.assertEqual(200, status)
        before = self.state(run)
        status, result = self.post(run, 'party/order', body)
        self.assertEqual(409, status)
        self.assertEqual('RUN_STALE', result['detail']['code'])
        self.assertEqual(before, self.state(run))
        self.assertGreater(ordered['run_revision'], run['run_revision'])

    def test_legacy_saves_need_no_preparation_metadata_or_schema_change(self):
        run = self.prepare(injured=True)
        state = self.state(run)
        state.pop('run_revision')
        state.pop('difficulty')
        with self.service.db:
            self.service.db.execute('UPDATE runs SET state=? WHERE id=?', (json.dumps(state), run['run_id']))
        legacy = self.service.get(run['run_id'], run['token'])
        self.assertEqual(0, legacy['run_revision'])
        self.assertEqual('normal', legacy['difficulty'])
        self.assertTrue(all('preview' in row for row in legacy['rest_options']))
        status, ordered = self.post(legacy, 'party/order', {'order': [2, 0, 1], 'run_revision': 0})
        self.assertEqual(200, status)
        self.assertEqual(1, ordered['schema_version'])
        saved = self.state(run)
        self.assertNotIn('difficulty', saved)
        self.assertNotIn('rest_options', saved)
        self.assertNotIn('name_ko', saved['resources'][0]['moves'][0])

    def test_move_metadata_is_public_only_and_shares_the_existing_catalog_cache(self):
        run = self.prepare()
        before = self.state(run)
        cache = self.service._move_catalog
        surf = run['party'][1]['resource']['moves'][0]
        self.assertEqual('파도타기', surf['name_ko'])
        self.assertEqual('surf', surf['name_en'])
        self.assertEqual(('water', 90, 'special'), (surf['type'], surf['power'], surf['category']))
        self.assertEqual({'id', 'pp', 'maxpp'}, set(before['resources'][1]['moves'][0]))
        surf.update(pp=0, name_ko='changed')
        self.service._default_enemy_evs({'moves': ['Surf']})
        self.assertIs(cache, self.service._move_catalog)
        self.assertEqual(before, self.state(run))
        self.assertEqual('파도타기', self.service.get(run['run_id'], run['token'])['party'][1]['resource']['moves'][0]['name_ko'])

    def test_all_rest_previews_match_committed_recovery_with_and_without_relics(self):
        for relics in ({}, {'camp-kit': 2, 'ether-case': 2, 'clean-blanket': 1, 'revive-charm': 1}):
            for option in ('heal', 'pp', 'cure', 'revive'):
                with self.subTest(relics=relics, option=option):
                    run = self.prepare(2, relics=relics, injured=True)
                    before = self.state(run)
                    preview = next(row['preview'] for row in run['rest_options'] if row['id'] == option)
                    self.assertEqual(before, self.state(run))
                    result = self.service.rest(run['run_id'], run['token'], option)
                    self.assertEqual('path', result['phase'])
                    self.assert_preview_matches(preview, self.state(run)['resources'])
                    self.assertEqual(before['team'], self.state(run)['team'])

    def test_rest_and_shop_labels_include_actual_relic_bonuses(self):
        run = self.prepare(2, relics={'camp-kit': 2, 'ether-case': 2})
        labels = {row['id']: row['label'] for row in run['rest_options']}
        self.assertIn('70%', labels['heal'])
        self.assertIn('14', labels['pp'])
        run = self.prepare(6, relics={'field-medic': 2, 'ether-case': 2})
        labels = {row['kind']: row['label'] for row in run['shop']}
        self.assertIn('70%', labels['heal'])
        self.assertIn('14', labels['pp'])

    def test_noop_rest_still_advances_but_unavailable_revival_is_rejected(self):
        for option in ('heal', 'pp', 'cure'):
            run = self.prepare(2)
            options = {row['id']: row for row in run['rest_options']}
            self.assertTrue(options[option]['available'])
            self.assertFalse(options[option]['preview']['available'])
            self.assertFalse(options['revive']['available'])
            self.assertFalse(options['revive']['preview']['available'])
            before = self.state(run)
            with self.assertRaises(ValueError):
                self.service.rest(run['run_id'], run['token'], 'revive')
            self.assertEqual(before, self.state(run))
            result = self.service.rest(run['run_id'], run['token'], option)
            self.assertEqual('path', result['phase'])
            self.assertEqual(before['resources'], self.state(run)['resources'])

    def test_all_shop_recovery_previews_match_actual_purchase_and_price(self):
        for relics in ({}, {'field-medic': 2, 'ether-case': 2, 'bargain-card': 1}):
            for kind in ('heal', 'pp', 'revive'):
                with self.subTest(relics=relics, kind=kind):
                    run = self.prepare(6, relics=relics, injured=True)
                    before = self.state(run)
                    offer = next(row for row in run['shop'] if row['kind'] == kind)
                    self.assertTrue(offer['preview']['available'])
                    self.assertEqual(before, self.state(run))
                    result = self.service.shop(run['run_id'], run['token'], offer['id'])
                    self.assert_preview_matches(offer['preview'], self.state(run)['resources'])
                    self.assertEqual(before['money'] - offer['price'], result['money'])
                    self.assertEqual(before['team'], self.state(run)['team'])
                    self.assertIn(offer['id'], self.state(run)['shop_bought'])
                    remaining = next((row for row in result['shop'] if row['id'] == offer['id']), None)
                    if remaining is not None:
                        self.assertTrue(remaining['bought'])
                    self.assertTrue(all('preview' not in row for row in result['shop'] if row['kind'] in ('relic', 'recruit')))


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunPreparationTests(unittest.TestCase):
    def test_all_curated_party_types_match_the_canonical_engines_own_view(self):
        service = RunService(':memory:')
        try:
            pool = service.content['recruit_pool']
            self.assertEqual(30, len(pool))
            self.assertEqual(778, service.species_ids['mimikyu'])
            self.assertIsNot(service.species_types['mimikyu'], service.species_types['mimikyudisguised'])
            checked = set()
            for start in range(0, len(pool), 3):
                team = copy.deepcopy(pool[start:start + 3])
                run = service.create(team, seed='curated-types-' + str(start))
                saved = service._load(run['run_id'], run['token'])
                before = copy.deepcopy(saved)
                battle = service._client().create({'p1': team, 'p2': default_opponents()}, seed=[1, 2, 3, 4])
                try:
                    own = service._client().view(battle['battle_id'], 'p1')['own']
                    engine_types = {pokemon['slot']: pokemon['types'] for pokemon in own}
                    engine_ids = {pokemon['slot']: pokemon['num'] for pokemon in own}
                    for slot, displayed in enumerate(run['party']):
                        with self.subTest(species=displayed['species']):
                            self.assertEqual(engine_types[slot], displayed['types'])
                            self.assertEqual(engine_ids[slot], service.species_ids[service._id(displayed['species'])])
                            self.assertNotIn('types', saved['team'][slot])
                            self.assertNotIn('types', saved['resources'][slot])
                            if displayed['species'] == 'Rotom-Wash':
                                self.assertEqual(['electric', 'water'], displayed['types'])
                            checked.add(displayed['species'])
                    run['party'][0]['types'].append('changed')
                    self.assertEqual(engine_types[0], service._party_view(saved)[0]['types'])
                    self.assertEqual(before, service._load(run['run_id'], run['token']))
                finally:
                    service._client().close_battle(battle['battle_id'])
            self.assertEqual({pokemon['species'] for pokemon in pool}, checked)
        finally:
            service.close()

    def test_ordered_injured_party_starts_the_canonical_battle_with_exact_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'real-prepare.sqlite3'
            service = RunService(path)
            try:
                team = default_opponents()
                for pokemon in team:
                    pokemon['evs'] = {'hp': 252, 'spa': 252, 'def': 4}
                run = service.create(team, seed='real-preparation')
                state = service._load(run['run_id'], run['token'])
                for slot, resource in enumerate(state['resources']):
                    resource.update(hp=resource['maxhp'] - 17 - slot,
                                    status='slp' if slot == 1 else '', sleepTurns=2 if slot == 1 else None)
                    for move in resource['moves']:
                        move['pp'] = max(0, move['maxpp'] - 4 - slot)
                state['resources'][1]['item'] = ''
                state['resources'][2]['hp'] = 0
                before = copy.deepcopy(state)
                service._save(state)
                ordered = service.party_order(run['run_id'], run['token'], [1, 0, 2])
                expected = [{**before['resources'][old], 'slot': new} for new, old in enumerate([1, 0, 2])]
                self.assertEqual(expected, service._load(run['run_id'], run['token'])['resources'])
                service.close()
                service = RunService(path)
                restored = service.get(run['run_id'], run['token'])
                self.assertEqual([p['species'] for p in ordered['party']], [p['species'] for p in restored['party']])
                battle = service.battle_start(run['run_id'], run['token'])
                actual = service._client().resources(battle['battle']['battle_id'])['p1']
                self.assertEqual(expected, actual)
                lead = next(p for p in battle['battle']['view']['own'] if p['active'])
                self.assertEqual(('Blastoise', 0, 'slp', ''), (lead['species'], lead['slot'], lead['status'], lead['item']))
                self.assertEqual([before['team'][i] for i in [1, 0, 2]], service._load(run['run_id'], run['token'])['team'])
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
