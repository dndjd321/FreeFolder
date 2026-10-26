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


async def http(app, method, path, body=None):
    events = []
    raw = json.dumps(body).encode() if body is not None else b''
    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    async def send(event):
        events.append(event)
    await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
               'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
               'headers': [(b'content-type', b'application/json')], 'server': ('test', 80), 'client': ('127.0.0.1', 1), 'root_path': ''}, receive, send)
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


class EngineHTTPTests(unittest.IsolatedAsyncioTestCase):
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
