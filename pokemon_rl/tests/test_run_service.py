"""Roguelike lifecycle, authorization, persistence and real-engine coverage."""
import copy
import asyncio
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from services.run_service import RunService
from services.engine_api import default_opponents


class FakeBattleClient:
    """Small server-side fixture; clients still cannot set resources or wins."""
    def __init__(self):
        self.battles = {}
        self.counter = 0
        self.turns_to_win = 1
        self.next_winner = 'p1'
        self.info = {'engine': 'test-fixture', 'version': '1'}

    def _store(self, data):
        self.counter += 1
        bid = f'fixture-{self.counter}'
        self.battles[bid] = data
        return self._state(bid)

    def create(self, teams, seed=None, initial_state=None, names=None):
        resources = {}
        for side in ('p1', 'p2'):
            resources[side] = []
            for i, p in enumerate(teams[side]):
                row = {'slot': i, 'species': p['species'], 'hp': 100, 'maxhp': 100,
                       'status': '', 'item': p.get('item', ''), 'sleepTurns': None,
                       'moves': [{'id': ''.join(c.lower() for c in move if c.isalnum()), 'pp': 20, 'maxpp': 20} for move in p['moves']]}
                old = (initial_state or {}).get(side, [None] * 3)[i]
                resources[side].append(copy.deepcopy(old or row))
        return self._store({'teams': copy.deepcopy(teams), 'resources': resources, 'turn': 1,
                            'pending': False, 'ended': False, 'winner': None, 'actions': [],
                            'seed': seed, 'turns_to_win': self.turns_to_win})

    def _state(self, bid):
        d = self.battles[bid]
        return {'battle_id': bid, 'ended': d['ended'], 'winner': d['winner'],
                'requests': {s: self.view(bid, s)['request'] for s in ('p1', 'p2')}}

    def view(self, bid, side):
        d = self.battles[bid]
        party = [{'slot': p['slot'], 'active': i == 0, 'condition': f"{p['hp']}/{p['maxhp']}"}
                 for i, p in enumerate(d['resources'][side])]
        request = {'wait': True} if d['ended'] or (side == 'p1' and d['pending']) else {
            'side': {'pokemon': party}, 'active': [{'moves': [
                {'id': m['id'], 'name': m['id'], 'pp': m['pp'], 'maxpp': m['maxpp'], 'basePower': 80,
                 'accuracy': 100, 'disabled': m['pp'] == 0} for m in d['resources'][side][0]['moves']]}]}
        if side == 'p2' and not d['pending']:
            request = {'wait': True}
        return {'battle_id': bid, 'side': side, 'turn': d['turn'], 'ended': d['ended'],
                'winner': d['winner'], 'own': copy.deepcopy(d['resources'][side]),
                'opponent': [{'species': 'Unknown', 'revealed': False}], 'request': request, 'events': []}

    def choose(self, bid, side, action):
        d = self.battles[bid]
        if action['type'] != 'move' or not 0 <= action['index'] < len(d['resources'][side][0]['moves']):
            raise ValueError('Illegal action')
        if d['ended'] or (side == 'p1' and d['pending']) or (side == 'p2' and not d['pending']):
            raise ValueError('Not awaiting choice')
        d['actions'].append({'side': side, 'action': copy.deepcopy(action)})
        if side == 'p1':
            d['pending'] = True
            d['resources']['p1'][0]['moves'][action['index']]['pp'] -= 1
        else:
            d['pending'] = False
            d['resources']['p1'][0]['hp'] -= 7
            d['turn'] += 1
            if d['turn'] > d['turns_to_win']:
                d.update(ended=True, winner=self.next_winner)
                if self.next_winner != 'p1':
                    for p in d['resources']['p1']:
                        p['hp'] = 0
        return self._state(bid)

    def resources(self, bid):
        return copy.deepcopy(self.battles[bid]['resources'])

    def checkpoint(self, bid):
        return {'schema_version': 1, 'data': copy.deepcopy(self.battles[bid])}

    def restore(self, checkpoint):
        return self._store(copy.deepcopy(checkpoint['data']))

    def close_battle(self, bid):
        del self.battles[bid]

    def close(self):
        self.battles.clear()


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / 'runs.sqlite3')
        self.client = FakeBattleClient()
        self.service = RunService(self.db, client_factory=lambda: self.client)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def create(self, seed='test-seed'):
        return self.service.create(default_opponents(), seed=seed)

    def win_battle(self, run):
        state = self.service.battle_start(run['run_id'], run['token'])
        i = 0
        while state['phase'] == 'battle':
            i += 1
            state = self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0},
                                               state['battle']['revision'], f'command-{run["node_index"]}-{i}')
        return state

    def test_twenty_node_run_completes_without_duplicate_rewards(self):
        state = self.create()
        steps = 0
        while state['phase'] not in ('complete', 'failed') and steps < 100:
            steps += 1
            rid, token = state['run_id'], state['token']
            if state['phase'] == 'node':
                kind = state['node']['kind']
                if kind == 'rest':
                    state = self.service.rest(rid, token, 'heal')
                elif kind == 'shop':
                    state = self.service.shop(rid, token, 'leave')
                else:
                    state = self.win_battle(state)
            elif state['phase'] == 'reward':
                reward = next((r for r in state['rewards'] if r['kind'] != 'recruit'), state['rewards'][0])
                state = self.service.choose_reward(rid, token, reward['id'])
            elif state['phase'] == 'recruit':
                state = self.service.recruit(rid, token, 'skip')
            elif state['phase'] == 'path':
                state = self.service.choose_path(rid, token, state['paths'][0]['id'])
        self.assertEqual('complete', state['phase'])
        self.assertEqual(20, state['completed_nodes'])
        self.assertEqual('won', state['outcome'])

    def test_wrong_token_and_spoofed_stats_are_rejected(self):
        state = self.create()
        with self.assertRaises(KeyError):
            self.service.get(state['run_id'], 'wrong')
        invalid = default_opponents()
        invalid[0]['hp'] = 10**9
        with self.assertRaises(ValueError):
            self.service.create(invalid)
        saved = self.service.db.execute('SELECT token_hash,state FROM runs').fetchone()
        self.assertNotEqual(state['token'], saved[0])
        self.assertNotIn(state['token'], saved[1])

    def test_battle_finish_cannot_accept_client_winner(self):
        state = self.create()
        self.service.battle_start(state['run_id'], state['token'])
        with self.assertRaises(ValueError):
            self.service.battle_finish(state['run_id'], state['token'])
        self.client.next_winner = 'p2'
        state = self.win_battle(state)
        self.assertEqual('failed', state['phase'])
        self.assertEqual(60, state['money'])

    def test_resources_carry_into_next_battle(self):
        state = self.win_battle(self.create())
        self.assertEqual(93, state['party'][0]['resource']['hp'])
        self.assertEqual(19, state['party'][0]['resource']['moves'][0]['pp'])
        reward = next(r for r in state['rewards'] if r['kind'] not in ('heal', 'pp', 'recruit'))
        state = self.service.choose_reward(state['run_id'], state['token'], reward['id'])
        state = self.service.choose_path(state['run_id'], state['token'], state['paths'][0]['id'])
        state = self.service.battle_start(state['run_id'], state['token'])
        self.assertEqual(93, state['battle']['view']['own'][0]['hp'])
        self.assertEqual(19, state['battle']['view']['own'][0]['moves'][0]['pp'])

    def test_reward_replay_and_old_reroll_id_are_rejected(self):
        state = self.win_battle(self.create())
        rid, token = state['run_id'], state['token']
        old_id = state['rewards'][0]['id']
        state = self.service.reroll_rewards(rid, token)
        with self.assertRaises(ValueError):
            self.service.choose_reward(rid, token, old_id)
        reward_id = state['rewards'][0]['id']
        state = self.service.choose_reward(rid, token, reward_id)
        money = state['money']
        with self.assertRaises(ValueError):
            self.service.choose_reward(rid, token, reward_id)
        self.assertEqual(money, self.service.get(rid, token)['money'])

    def test_separate_random_streams_are_deterministic(self):
        first, second = self.create('same'), self.create('same')
        self.assertEqual(first['node'], second['node'])
        first, second = self.win_battle(first), self.win_battle(second)
        self.assertEqual(first['rewards'], second['rewards'])
        internal = self.service._load(first['run_id'], first['token'])
        seed = self.service._battle_seed(internal)
        self.service.reroll_rewards(first['run_id'], first['token'])
        self.assertEqual(seed, self.service._battle_seed(self.service._load(first['run_id'], first['token'])))

    def test_restart_restores_battle_checkpoint_and_same_token(self):
        self.client.turns_to_win = 3
        state = self.create()
        rid, token = state['run_id'], state['token']
        state = self.service.battle_start(rid, token)
        state = self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'before-restart')
        self.assertEqual('battle', state['phase'])
        self.service.close()
        self.client = FakeBattleClient()
        self.service = RunService(self.db, client_factory=lambda: self.client)
        restored = self.service.resume(rid, token)
        self.assertEqual(2, restored['battle']['view']['turn'])
        self.assertEqual(1, restored['battle']['revision'])
        self.assertEqual(93, restored['battle']['view']['own'][0]['hp'])

    def test_same_action_id_is_not_applied_twice(self):
        self.client.turns_to_win = 3
        state = self.create()
        rid, token = state['run_id'], state['token']
        self.service.battle_start(rid, token)
        first = self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'same')
        second = self.service.battle_action(rid, token, {'type': 'move', 'index': 0}, 0, 'same')
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            self.service.battle_action(rid, token, {'type': 'move', 'index': 1}, 1, 'same')

    def test_shop_purchase_is_server_priced_and_single_use(self):
        run = self.create()
        rid, token = run['run_id'], run['token']
        state = self.service._load(rid, token)
        state.update(node_index=6, node=self.service._node(state, 6))
        state['resources'][0]['hp'] = 1
        self.service._save(state)
        offer = self.service.get(rid, token)['shop'][0]
        result = self.service.shop(rid, token, offer['id'])
        self.assertEqual(35, result['money'])
        self.assertEqual(51, result['party'][0]['resource']['hp'])
        with self.assertRaises(ValueError):
            self.service.shop(rid, token, offer['id'])

    def test_recruitment_preserves_other_party_resources(self):
        run = self.create()
        rid, token = run['run_id'], run['token']
        state = self.service._load(rid, token)
        state['resources'][0].update(hp=27, status='brn')
        state['resources'][0]['moves'][0]['pp'] = 2
        self.service._offer_recruits(state, 'reward')
        self.service._save(state)
        candidate = state['recruit_candidates'][0]
        result = self.service.recruit(rid, token, candidate['id'], 1)
        self.assertEqual(candidate['team']['species'], result['party'][1]['species'])
        self.assertEqual(27, result['party'][0]['resource']['hp'])
        self.assertEqual('brn', result['party'][0]['resource']['status'])
        self.assertEqual(2, result['party'][0]['resource']['moves'][0]['pp'])
        self.assertEqual(100, result['party'][1]['resource']['hp'])

    def test_rest_revive_spends_the_rest_choice_and_keeps_pp(self):
        run = self.create()
        rid, token = run['run_id'], run['token']
        state = self.service._load(rid, token)
        state.update(node_index=2, node=self.service._node(state, 2))
        state['resources'][0].update(hp=0, status='')
        state['resources'][0]['moves'][0]['pp'] = 2
        self.service._save(state)
        result = self.service.rest(rid, token, 'revive')
        self.assertEqual(50, result['party'][0]['resource']['hp'])
        self.assertEqual(2, result['party'][0]['resource']['moves'][0]['pp'])
        self.assertEqual('path', result['phase'])
        with self.assertRaises(ValueError):
            self.service.rest(rid, token, 'heal')

    def test_shop_revive_is_paid_once(self):
        run = self.create()
        rid, token = run['run_id'], run['token']
        state = self.service._load(rid, token)
        state.update(node_index=6, node=self.service._node(state, 6))
        state['resources'][0]['hp'] = 0
        self.service._save(state)
        offer = next(o for o in self.service.get(rid, token)['shop'] if o['kind'] == 'revive')
        result = self.service.shop(rid, token, offer['id'])
        self.assertEqual(20, result['money'])
        self.assertEqual(50, result['party'][0]['resource']['hp'])
        with self.assertRaises(ValueError):
            self.service.shop(rid, token, offer['id'])

    def test_save_directory_environment_is_supported(self):
        directory = Path(self.tmp.name) / 'mounted-save'
        with patch.dict('os.environ', {'FREEFOLDER_SAVE_DIR': str(directory)}):
            service = RunService(client_factory=FakeBattleClient)
            try:
                self.assertEqual(str(directory / 'runs.sqlite3'), service.db_path)
                self.assertTrue(Path(service.db_path).is_file())
            finally:
                service.close()

    def test_unknown_save_version_is_preserved_and_rejected(self):
        run = self.create()
        state = self.service._load(run['run_id'], run['token'])
        state['schema_version'] = 999
        self.service._save(state)
        with self.assertRaises(ValueError):
            self.service.resume(run['run_id'], run['token'])
        self.assertIn('999', self.service.db.execute('SELECT state FROM runs').fetchone()[0])

    def test_public_view_does_not_leak_private_checkpoint(self):
        run = self.create()
        state = self.service.battle_start(run['run_id'], run['token'])
        public = json.dumps(state)
        self.assertNotIn('checkpoint', public)
        self.assertNotIn('enemy_team', public)
        self.assertNotIn('"teams"', public)

    def test_http_get_requires_bearer_and_rejects_query_token(self):
        run = self.create()
        from fastapi import FastAPI
        from services.run_api import install_run_routes
        app = FastAPI()
        install_run_routes(app, self.service)

        async def request(method, path, headers=(), body=b'', query=b''):
            events = []
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'method': method, 'path': path,
                     'raw_path': path.encode(), 'root_path': '', 'query_string': query,
                     'scheme': 'http', 'http_version': '1.1', 'headers': list(headers),
                     'client': ('127.0.0.1', 12345), 'server': ('testserver', 80)}
            async def receive():
                return {'type': 'http.request', 'body': body, 'more_body': False}
            async def send(event):
                events.append(event)
            await app(scope, receive, send)
            status = next(e['status'] for e in events if e['type'] == 'http.response.start')
            payload = b''.join(e.get('body', b'') for e in events if e['type'] == 'http.response.body')
            return status, json.loads(payload)

        path = '/api/runs/' + run['run_id']
        status, _ = asyncio.run(request('GET', path, query=('token=' + run['token']).encode()))
        self.assertEqual(404, status)
        status, state = asyncio.run(request('GET', path, headers=[(b'authorization', ('Bearer ' + run['token']).encode())]))
        self.assertEqual(200, status)
        self.assertEqual(run['run_id'], state['run_id'])
        status, _ = asyncio.run(request('POST', path + '/battle/finish', body=json.dumps({'token': run['token'], 'winner': 'p1'}).encode()))
        # A client winner does not grant a reward before a real battle exists.
        self.assertEqual(200, status)
        self.assertEqual(60, self.service.get(run['run_id'], run['token'])['money'])
        status, _ = asyncio.run(request('POST', '/api/runs', body=b'[]'))
        self.assertEqual(400, status)


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunEngineTests(unittest.TestCase):
    def test_real_twenty_node_run(self):
        from env.damage_calc import get_type_multiplier
        team = [
            {'species': 'Garchomp', 'ability': 'Rough Skin', 'item': 'Leftovers', 'nature': 'Jolly',
             'level': 50, 'evs': {'atk': 252, 'spe': 252, 'spd': 4},
             'moves': ['Earthquake', 'Dragon Claw', 'Poison Jab', 'Swords Dance']},
            {'species': 'Vaporeon', 'ability': 'Water Absorb', 'item': 'Leftovers', 'nature': 'Modest',
             'level': 50, 'evs': {'hp': 252, 'spa': 252, 'def': 4},
             'moves': ['Surf', 'Ice Beam', 'Protect', 'Wish']},
            {'species': 'Scizor', 'ability': 'Technician', 'item': 'Leftovers', 'nature': 'Adamant',
             'level': 50, 'evs': {'hp': 252, 'atk': 252, 'def': 4},
             'moves': ['Bullet Punch', 'Swords Dance', 'Knock Off', 'Roost']},
        ]

        def policy(view):
            request = view['request']
            own = view['own']
            enemy = next((p for p in view['opponent'] if p.get('active')), {})
            def damage(poke, move):
                if move.get('type') == 'ground' and enemy.get('id') == 'rotomwash':
                    return 0  # This species has only Levitate; no hidden state is read.
                stat = poke['stats'].get('atk' if move.get('category') == 'physical' else 'spa', 100)
                accuracy = 1 if move.get('accuracy') is True else (move.get('accuracy', 100) or 100) / 100
                stab = 1.5 if move.get('type') in poke.get('types', []) else 1
                technician = 1.5 if poke.get('ability') == 'technician' and move.get('basePower', 0) <= 60 else 1
                boost = poke.get('boosts', {}).get('atk' if move.get('category') == 'physical' else 'spa', 0)
                multiplier = (2 + boost) / 2 if boost >= 0 else 2 / (2 - boost)
                return move.get('basePower', 0) * get_type_multiplier(move.get('type', 'normal'), enemy.get('types', [])) * stat * accuracy * stab * technician * multiplier
            def matchup(poke):
                attacks = [damage(poke, m) for m in poke['moves'] if m.get('pp', 1) > 0 and not m.get('disabled')]
                threats = [0 if t == 'water' and poke.get('ability') == 'waterabsorb' else get_type_multiplier(t, poke['types']) for t in enemy.get('types', [])]
                threat = sum(threats) / len(threats) if threats else 1
                return max(attacks, default=0) * max(.3, poke['hp'] / poke['maxhp']) / max(.15, threat)
            if any(request.get('forceSwitch', [])):
                living = [p for p in own if p['hp'] > 0 and not p.get('active')]
                return {'type': 'switch', 'index': max(living, key=matchup)['slot']}
            active = next(p for p in own if p.get('active'))
            best = max((p for p in own if p['hp'] > 0), key=matchup)
            if not request['active'][0].get('trapped') and best['slot'] != active['slot'] and matchup(best) > matchup(active) * 2:
                return {'type': 'switch', 'index': best['slot']}
            moves = [(i, m) for i, m in enumerate(request['active'][0]['moves']) if not m.get('disabled') and m.get('pp', 1) > 0]
            last_move = next((line.split('|')[3] for line in reversed(view['events']) if line.startswith('|move|p1a: ' + active['name'] + '|')), '')
            if last_move == 'Wish' and active['hp'] / active['maxhp'] < .8:
                protect = next(((i, m) for i, m in moves if m.get('id') == 'protect'), None)
                if protect:
                    return {'type': 'move', 'index': protect[0]}
            heal = next(((i, m) for i, m in moves if m.get('id') in ('roost', 'moonlight') and active['hp'] / active['maxhp'] < .55), None)
            if heal:
                return {'type': 'move', 'index': heal[0]}
            wish = next(((i, m) for i, m in moves if m.get('id') == 'wish' and active['hp'] / active['maxhp'] < .55), None)
            if wish:
                return {'type': 'move', 'index': wish[0]}
            setup = next(((i, m) for i, m in moves if m.get('id') == 'swordsdance' and active.get('boosts', {}).get('atk', 0) < 2 and active['hp'] / active['maxhp'] > .7), None)
            if setup:
                return {'type': 'move', 'index': setup[0]}

            def score(entry):
                _, move = entry
                return damage(active, move)
            return {'type': 'move', 'index': max(moves, key=score)[0]}

        with tempfile.TemporaryDirectory() as tmp:
            service = RunService(str(Path(tmp) / 'full.sqlite3'))
            try:
                state = service.create(team, seed='real-twenty-node-v1')
                transitions = 0
                while state['phase'] not in ('complete', 'failed') and transitions < 1000:
                    transitions += 1
                    rid, token = state['run_id'], state['token']
                    if state['phase'] == 'node':
                        kind = state['node']['kind']
                        if kind == 'rest':
                            low_pp = min(m['pp'] for p in state['party'] for m in p['resource']['moves']) < 4
                            dead = any(p['resource']['hp'] == 0 for p in state['party'])
                            state = service.rest(rid, token, 'revive' if dead else 'pp' if low_pp else 'heal')
                        elif kind == 'shop':
                            hurt = any(0 < p['resource']['hp'] / p['resource']['maxhp'] < .8 for p in state['party'])
                            dead = any(p['resource']['hp'] == 0 for p in state['party'])
                            offer = next((o for o in state['shop'] if o['kind'] == 'revive' and not o['bought'] and o['price'] <= state['money'] and dead), None)
                            offer = offer or next((o for o in state['shop'] if o['kind'] == 'recruit' and not o['bought'] and o['price'] <= state['money'] and dead), None)
                            offer = offer or next((o for o in state['shop'] if o['kind'] == 'heal' and not o['bought'] and o['price'] <= state['money'] and hurt), None)
                            state = service.shop(rid, token, offer['id'] if offer else 'leave')
                        else:
                            state = service.battle_start(rid, token)
                    elif state['phase'] == 'battle':
                        try:
                            state = service.battle_action(rid, token, policy(state['battle']['view']), state['battle']['revision'], f'full-{transitions}')
                        except RuntimeError as exc:
                            private = service._load(rid, token)
                            bid = service._ensure_battle(private)
                            requests = {s: service._client().view(bid, s).get('request') for s in ('p1', 'p2')}
                            own_view = service._client().view(bid, 'p1')
                            raise AssertionError(f'node={state["node_index"]} own={own_view["own"]} events={own_view["events"][-25:]} requests={requests}') from exc
                    elif state['phase'] == 'reward':
                        hp = sum(p['resource']['hp'] / p['resource']['maxhp'] for p in state['party'])
                        dead = any(p['resource']['hp'] == 0 for p in state['party'])
                        rewards = state['rewards']
                        choice = next((r for r in rewards if r['kind'] == 'recruit' and dead), None)
                        if dead and not choice and state['rerolls_left']:
                            state = service.reroll_rewards(rid, token)
                            continue
                        choice = choice or next((r for r in rewards if r['kind'] == 'heal' and hp < 2.7), None)
                        choice = choice or next((r for r in rewards if r['kind'] == 'pp' and min(m['pp'] for p in state['party'] for m in p['resource']['moves']) < 4), None)
                        choice = choice or next((r for r in rewards if r['kind'] == 'relic'), None)
                        choice = choice or next((r for r in rewards if r['kind'] != 'recruit'), rewards[0])
                        state = service.choose_reward(rid, token, choice['id'])
                    elif state['phase'] == 'path':
                        state = service.choose_path(rid, token, state['paths'][0]['id'])
                    elif state['phase'] == 'recruit':
                        slot = min(range(3), key=lambda i: state['party'][i]['resource']['hp'] / state['party'][i]['resource']['maxhp'])
                        priority = ['Gyarados', 'Blastoise', 'Rotom-Wash', 'Magnezone', 'Togekiss', 'Goodra', 'Dragonite']
                        candidate = min(state['recruit_candidates'], key=lambda p: priority.index(p['team']['species']) if p['team']['species'] in priority else 100)
                        state = service.recruit(rid, token, candidate['id'], slot)
                self.assertEqual('complete', state['phase'], f'node={state["node_index"]}, party={state["party"]}')
                self.assertEqual(20, state['completed_nodes'])
            finally:
                service.close()

    def test_real_content_and_checkpoint_resume(self):
        from battle_service.client import BattleClient
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / 'runs.sqlite3')
            service = RunService(db)
            try:
                for enemy in service.content['themes'] + service.content['bosses']:
                    battle = service._client().create({'p1': enemy['team'], 'p2': default_opponents()}, seed=[1, 2, 3, 4])
                    service._client().close_battle(battle['battle_id'])
                state = service.create(default_opponents(), seed='real-resume')
                rid, token = state['run_id'], state['token']
                state = service.battle_start(rid, token)
                from services.engine_api import choose_legal_action
                action = choose_legal_action(state['battle']['view']['request'])
                state = service.battle_action(rid, token, action, 0, 'real-first')
                self.assertEqual('battle', state['phase'])
                view = copy.deepcopy(state['battle']['view'])
                service.close()
                service = RunService(db)
                resumed = service.resume(rid, token)
                self.assertEqual(view['turn'], resumed['battle']['view']['turn'])
                self.assertEqual(view['own'], resumed['battle']['view']['own'])
            finally:
                service.close()

    def test_worker_loss_restores_the_last_checkpoint_without_repeating_player_input(self):
        from services.engine_api import choose_legal_action
        with tempfile.TemporaryDirectory() as tmp:
            service = RunService(str(Path(tmp) / 'worker-loss.sqlite3'))
            try:
                state = service.create(default_opponents(), seed='worker-loss')
                rid, token = state['run_id'], state['token']
                state = service.battle_start(rid, token)
                state = service.battle_action(rid, token, choose_legal_action(state['battle']['view']['request']), 0, 'before-loss')
                own = copy.deepcopy(state['battle']['view']['own'])
                turn = state['battle']['view']['turn']
                service.client.close()
                restored = service.resume(rid, token)
                self.assertEqual(own, restored['battle']['view']['own'])
                self.assertEqual(turn, restored['battle']['view']['turn'])
                self.assertEqual(1, restored['battle']['revision'])
                other = service.create(default_opponents(), seed='new-after-loss')
                self.assertEqual('node', other['phase'])
            finally:
                service.close()

    def test_shared_worker_remains_owned_by_application_after_run_service_closes(self):
        from services.engine_api import EngineSessions
        engine = EngineSessions()
        service = RunService(':memory:', client_factory=engine.get_client, owns_client=False)
        try:
            service.create(default_opponents(), seed='shared-worker')
            self.assertIs(service.client, engine.client)
            service.close()
            self.assertTrue(engine.status()['available'])
        finally:
            service.close()
            engine.close()


if __name__ == '__main__':
    unittest.main()
