import asyncio
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from services.engine_api import EngineSessions, canonical_team, choose_legal_action, default_opponents, install_engine_routes
from services.engine_multiplayer import AuthoritativeMultiplayerService
from test_multiplayer import ASGISocket


async def http(app, method, path, body=None, *, headers=None, raw_body=None):
    events = []
    raw = raw_body if raw_body is not None else json.dumps(body).encode() if body is not None else b''
    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    async def send(event):
        events.append(event)
    await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
               'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
               'headers': [(b'content-type', b'application/json'), *(headers or [])], 'server': ('test', 80), 'client': ('127.0.0.1', 1), 'root_path': ''}, receive, send)
    status = next(e['status'] for e in events if e['type'] == 'http.response.start')
    data = b''.join(e.get('body', b'') for e in events if e['type'] == 'http.response.body')
    return status, json.loads(data)


class EngineSessionTests(unittest.TestCase):
    def setUp(self):
        self.service = EngineSessions()

    def tearDown(self):
        self.service.close()

    def test_action_is_server_owned_and_duplicate_is_idempotent(self):
        r = self.service.create(default_opponents())
        action = choose_legal_action(r['view']['request'])
        first = self.service.act(r['battle_id'], r['token'], action, 0, 'one')
        duplicate = self.service.act(r['battle_id'], r['token'], action, 0, 'one')
        self.assertEqual(first, duplicate)
        self.assertEqual(2, first['view']['turn'])
        with self.assertRaises(ValueError):
            self.service.act(r['battle_id'], r['token'], action, 0, 'stale')
        with self.assertRaises(KeyError):
            self.service.act(r['battle_id'], 'wrong', action, 1, 'wrong')
        self.assertNotIn('views', first)
        for mon in first['view']['opponent']:
            self.assertNotIn('moves', mon)
            self.assertNotIn('item', mon)

    def test_client_combat_stats_and_malformed_nature_are_rejected(self):
        team = default_opponents()
        team[0]['hp'] = 100000000
        with self.assertRaises(ValueError):
            canonical_team(team)
        team = default_opponents()
        team[0]['nature'] = []
        with self.assertRaises(ValueError):
            canonical_team(team)

    def test_switch_policy_uses_stable_slot_in_reordered_party(self):
        request = {'forceSwitch': [True], 'side': {'pokemon': [
            {'slot': 2, 'active': True, 'condition': '0 fnt'},
            {'slot': 0, 'active': False, 'condition': '50/100'},
            {'slot': 1, 'active': False, 'condition': '0 fnt'}]}}
        self.assertEqual({'type': 'switch', 'index': 0}, choose_legal_action(request))

    def test_neural_mode_uses_the_shipped_policy_on_the_opponents_own_view(self):
        self.assertTrue(self.service.neural_available())
        created = self.service.create(default_opponents(), difficulty='neural')
        policy = self.service.get_policy()
        original = policy.predict
        calls = []
        def observe(request, view):
            calls.append(view['side'])
            return original(request, view)
        policy.predict = observe
        result = self.service.act(created['battle_id'], created['token'],
            choose_legal_action(created['view']['request']), 0, 'neural-first')
        self.assertEqual(2, result['view']['turn'])
        self.assertTrue(calls)
        self.assertTrue(all(side == 'p2' for side in calls))

    def test_failed_ai_prediction_recovers_without_repeating_player_choice(self):
        for recovery in ('get', 'duplicate'):
            with self.subTest(recovery=recovery):
                class FlakyPolicy:
                    calls = 0
                    def predict(self, request, view):
                        self.calls += 1
                        if self.calls == 1:
                            raise RuntimeError('temporary inference failure')
                        return choose_legal_action(request)
                self.service.policy = FlakyPolicy()
                created = self.service.create(default_opponents(), difficulty='neural')
                bid, token = created['battle_id'], created['token']
                before = created['view']['own'][0]['moves'][0]['pp']
                action = {'type': 'move', 'index': 0}
                with self.assertRaisesRegex(RuntimeError, 'temporary inference'):
                    self.service.act(bid, token, action, 0, 'recover')
                session = self.service.sessions[bid]
                self.assertEqual(1, session['revision'])
                self.assertEqual('recover', session['pending_command'])
                if recovery == 'get':
                    result = self.service.response(bid)
                else:
                    result = self.service.act(bid, token, action, 0, 'recover')
                self.assertEqual(2, result['view']['turn'])
                self.assertEqual(1, result['revision'])
                self.assertEqual(before - 1, result['view']['own'][0]['moves'][0]['pp'])
                self.assertEqual(result, self.service.act(bid, token, action, 0, 'recover'))
                self.assertIsNone(session['pending_command'])
                self.service.delete(bid, token)

    def test_permanent_ai_error_remains_explicit_and_never_repeats_player_choice(self):
        class BrokenPolicy:
            def predict(self, request, view):
                raise RuntimeError('model unavailable')
        self.service.policy = BrokenPolicy()
        created = self.service.create(default_opponents(), difficulty='neural')
        bid, token = created['battle_id'], created['token']
        choose = self.service.client.choose
        player_choices = []
        def count_choice(bid, side, action):
            if side == 'p1':
                player_choices.append(action)
            return choose(bid, side, action)
        self.service.client.choose = count_choice
        action = {'type': 'move', 'index': 0}
        for recover in (lambda: self.service.act(bid, token, action, 0, 'broken'),
                        lambda: self.service.response(bid),
                        lambda: self.service.act(bid, token, action, 0, 'broken')):
            with self.assertRaisesRegex(RuntimeError, 'model unavailable'):
                recover()
        self.assertEqual([action], player_choices)
        self.assertEqual(1, self.service.sessions[bid]['revision'])

    def test_new_battle_replaces_closed_worker_without_replaying_old_session(self):
        from battle_service.client import BattleServiceError
        first = self.service.create(default_opponents())
        old_client = self.service.client
        old_client.close()
        second = self.service.create(default_opponents())
        self.assertIsNot(old_client, self.service.client)
        self.assertEqual(1, second['view']['turn'])
        with self.assertRaises(BattleServiceError) as error:
            self.service.response(first['battle_id'])
        self.assertEqual('WORKER_CLOSED', error.exception.code)
        self.service.delete(first['battle_id'], first['token'])
        self.assertNotIn(first['battle_id'], self.service.sessions)

    def test_failed_initial_response_closes_battle_and_leaves_no_session(self):
        from battle_service.client import BattleServiceError
        client = self.service.get_client()
        create, view = client.create, client.view
        created_ids = []
        def capture_create(*args, **kwargs):
            result = create(*args, **kwargs)
            created_ids.append(result['battle_id'])
            return result
        def fail_view(*args, **kwargs):
            raise RuntimeError('initial view failure')
        client.create, client.view = capture_create, fail_view
        with self.assertRaisesRegex(RuntimeError, 'initial view failure'):
            self.service.create(default_opponents())
        self.assertEqual({}, self.service.sessions)
        client.view = view
        with self.assertRaises(BattleServiceError):
            client.view(created_ids[0], 'p1')


class EngineHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_application_lifespan_closes_the_registered_worker(self):
        app = FastAPI()
        service = install_engine_routes(app)
        async with app.router.lifespan_context(app):
            service.create(default_opponents())
            worker = service.client
            self.assertFalse(worker._closed)
        self.assertIsNone(service.client)
        self.assertTrue(worker._closed)
        self.assertEqual({}, service.sessions)

    async def test_deeply_nested_json_is_a_bad_request(self):
        app = FastAPI()
        service = install_engine_routes(app, register_shutdown=False)
        try:
            self.assertEqual([], app.router.on_shutdown)
            status, _ = await http(app, 'POST', '/api/engine/battles',
                                   raw_body=b'{"nested":' * 2000 + b'0' + b'}' * 2000)
            self.assertEqual(400, status)
        finally:
            service.close()

    async def test_routes_validate_body_and_execute_actual_battle(self):
        app = FastAPI()
        service = install_engine_routes(app)
        try:
            status, _ = await http(app, 'POST', '/api/engine/battles', [])
            self.assertEqual(400, status)
            status, created = await http(app, 'POST', '/api/engine/battles', {'team': default_opponents()})
            self.assertEqual(200, status)
            status, result = await http(app, 'POST', f'/api/engine/battles/{created["battle_id"]}/action', {
                'token': created['token'], 'revision': created['revision'], 'command_id': 'http-one',
                'action': choose_legal_action(created['view']['request'])})
            self.assertEqual(200, status)
            self.assertEqual(2, result['view']['turn'])
        finally:
            service.close()

    async def test_bearer_get_recovers_pending_ai_without_query_credentials(self):
        app = FastAPI()
        service = install_engine_routes(app)
        class FlakyPolicy:
            calls = 0
            def predict(self, request, view):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError('temporary inference failure')
                return choose_legal_action(request)
        service.policy = FlakyPolicy()
        try:
            _, created = await http(app, 'POST', '/api/engine/battles', {
                'team': default_opponents(), 'difficulty': 'neural'})
            path = f'/api/engine/battles/{created["battle_id"]}'
            status, _ = await http(app, 'POST', path + '/action', {
                'token': created['token'], 'revision': 0, 'command_id': 'recover-http',
                'action': {'type': 'move', 'index': 0}})
            self.assertEqual(400, status)
            status, _ = await http(app, 'GET', path)
            self.assertEqual(404, status)
            status, recovered = await http(app, 'GET', path, headers=[
                (b'authorization', ('Bearer ' + created['token']).encode())])
            self.assertEqual(200, status)
            self.assertEqual(1, recovered['revision'])
            self.assertEqual(2, recovered['view']['turn'])
            self.assertEqual(created['view']['own'][0]['moves'][0]['pp'] - 1,
                             recovered['view']['own'][0]['moves'][0]['pp'])
        finally:
            service.close()


class AuthoritativeRoomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = EngineSessions()
        self.service = AuthoritativeMultiplayerService(self.engine)
        self.app = FastAPI()
        service = self.service
        from fastapi import WebSocket
        @self.app.websocket('/ws/multi')
        async def route(ws: WebSocket):
            await service.websocket(ws)
        self.sockets = []

    async def asyncTearDown(self):
        for socket in reversed(self.sockets):
            await socket.close()
        await self.service.shutdown()
        self.engine.close()

    async def connect(self):
        socket = await ASGISocket(self.app).connect()
        self.sockets.append(socket)
        return socket

    async def test_both_sides_share_results_and_cannot_forge_winner(self):
        host, guest = await self.connect(), await self.connect()
        await host.send({'type': 'create_room', 'name': 'Actual engine'})
        code = (await host.receive('room_created'))['code']
        await guest.send({'type': 'join_room', 'code': code})
        await guest.receive('room_joined')
        from test_multiplayer import team
        packet = team()
        for entry, engine_set in zip(packet, default_opponents()):
            entry['engineSet'] = engine_set
        await host.send({'type': 'team_ready', 'team': packet})
        await guest.send({'type': 'team_ready', 'team': copy.deepcopy(packet)})
        h, g = await host.receive('engine_state'), await guest.receive('engine_state')
        self.assertEqual(h['battle_id'], g['battle_id'])
        for socket, state in ((host, h), (guest, g)):
            action = choose_legal_action(state['view']['request'])
            await socket.send({'type': 'battle_action', 'action': action['type'], 'index': action['index'],
                               'command_id': 'one', 'request_token': state['request_token']})
        async def turn_two(socket):
            while True:
                state = await socket.receive('engine_state')
                if state['view']['turn'] == 2:
                    return state
        h2, g2 = await turn_two(host), await turn_two(guest)
        own = next(p for p in h2['view']['own'] if p['active'])
        seen = next(p for p in g2['view']['opponent'] if p.get('active'))
        self.assertLessEqual(abs(seen['hp'] - round(own['hp'] * 100 / own['maxhp'])), 1)
        self.assertEqual(h2['view']['turn'], g2['view']['turn'])
        await host.send({'type': 'battle_end', 'winner': 'cheater'})
        self.assertIn('서버', (await host.receive('error'))['message'])
        await host.send({'type': 'battle_action', 'action': 'move', 'index': 0, 'command_id': 'stale', 'request_token': h['request_token']})
        self.assertIn('이전', (await host.receive('error'))['message'])


if __name__ == '__main__':
    unittest.main()
