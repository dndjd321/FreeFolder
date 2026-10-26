"""Real ASGI sockets and real engine coverage for resume and server deadlines."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from services.engine_api import EngineSessions, choose_legal_action, default_opponents
from services.engine_multiplayer import AuthoritativeMultiplayerService
from test_multiplayer import ASGISocket, team


class EngineReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = EngineSessions()
        self.service = AuthoritativeMultiplayerService(self.engine)
        self.app = FastAPI()
        service = self.service

        @self.app.websocket('/ws/multi')
        async def websocket(ws: WebSocket):
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
        token = (await socket.receive('session'))['resume_token']
        return socket, token

    async def start(self, *, turn_time=0):
        host, host_token = await self.connect()
        guest, guest_token = await self.connect()
        await host.send({'type': 'create_room', 'name': 'Reconnect test', 'rules': {'turnTime': turn_time}})
        code = (await host.receive('room_created'))['code']
        await guest.send({'type': 'join_room', 'code': code})
        await guest.receive('room_joined')
        packet = team()
        for mon, build in zip(packet, default_opponents()):
            mon['engineSet'] = build
        await host.send({'type': 'team_ready', 'team': packet})
        await guest.send({'type': 'team_ready', 'team': copy.deepcopy(packet)})
        h, g = await host.receive('engine_state'), await guest.receive('engine_state')
        return host, guest, host_token, guest_token, code, h, g

    async def choose(self, socket, state, command_id='manual'):
        action = choose_legal_action(state['view']['request'])
        await socket.send({'type': 'battle_action', 'action': action['type'], 'index': action['index'],
                           'request_token': state['request_token'], 'command_id': command_id})

    async def at_turn(self, socket, turn):
        while True:
            state = await socket.receive('engine_state')
            if state['view']['turn'] >= turn:
                return state

    async def test_midturn_resume_preserves_identity_role_token_and_pending_choice(self):
        host, guest, token, _, code, h, _ = await self.start()
        player = self.service.resume_tokens[token]
        await self.choose(host, h)
        pending_h = await host.receive('engine_state')
        pending_g = await guest.receive('engine_state')
        self.assertTrue(pending_h['view']['request']['wait'])
        await host.close()
        await guest.receive('opponent_disconnected')
        await self.choose(guest, pending_g, 'paused-choice')
        self.assertIn('재접속', (await guest.receive('error'))['message'])

        resumed, temporary_token = await self.connect()
        await resumed.send({'type': 'resume_session', 'resume_token': token})
        self.assertEqual(token, (await resumed.receive('session_resumed'))['resume_token'])
        restored_h = await resumed.receive('engine_state')
        restored_g = await guest.receive('engine_state')
        await guest.receive('opponent_reconnected')
        self.assertEqual(player, self.service.resume_tokens[token])
        self.assertNotIn(temporary_token, self.service.resume_tokens)
        self.assertEqual('p1', restored_h['role'])
        self.assertEqual(pending_h['view'], restored_h['view'])
        self.assertEqual(pending_h['request_token'], restored_h['request_token'])
        self.assertEqual(pending_g['request_token'], restored_g['request_token'])
        self.assertEqual(2, len(self.service.rooms[code]['players']))
        await self.choose(guest, restored_g, 'continue')
        h2 = await self.at_turn(resumed, 2)
        g2 = await self.at_turn(guest, 2)
        self.assertEqual(h2['view']['turn'], g2['view']['turn'])
        await resumed.send({'type': 'battle_end', 'winner': 'p1'})
        self.assertIn('서버', (await resumed.receive('error'))['message'])
        self.assertEqual('battle', self.service.rooms[code]['state'])

    async def test_active_session_cannot_be_attached_to_a_second_socket(self):
        host, guest, token, _, _, h, _ = await self.start()
        duplicate, _ = await self.connect()
        await duplicate.send({'type': 'resume_session', 'resume_token': token})
        error = await duplicate.receive('error')
        self.assertEqual('resume_busy', error['code'])
        self.assertIn('재접속', error['message'])
        owner = self.service.resume_tokens[token]
        self.assertIsNotNone(self.service.clients[owner]['ws'])
        self.assertFalse(host.task.done())
        # A reload can open its replacement before the original close is
        # processed. The same replacement and original token remain usable.
        await host.close()
        await guest.receive('opponent_disconnected')
        await duplicate.send({'type': 'resume_session', 'resume_token': token})
        await duplicate.receive('session_resumed')
        restored = await duplicate.receive('engine_state')
        self.assertEqual('p1', restored['role'])
        self.assertEqual(h['view'], restored['view'])
        self.assertEqual(h['request_token'], restored['request_token'])

    async def test_prebattle_disconnect_removes_nonresumable_lobby_and_room_tokens(self):
        for phase in ('lobby', 'waiting', 'team_select'):
            with self.subTest(phase=phase):
                host, token = await self.connect()
                guest = None
                code = None
                if phase != 'lobby':
                    await host.send({'type': 'create_room', 'name': 'Prebattle reload'})
                    code = (await host.receive('room_created'))['code']
                if phase == 'team_select':
                    guest, _ = await self.connect()
                    await guest.send({'type': 'join_room', 'code': code})
                    await guest.receive('team_select')
                await host.close()
                self.assertNotIn(token, self.service.resume_tokens)
                self.assertEqual({}, self.service.expirations)
                replacement, replacement_token = await self.connect()
                await replacement.send({'type': 'resume_session', 'resume_token': token})
                self.assertEqual('resume_unavailable', (await replacement.receive('error'))['code'])
                self.assertIn(replacement_token, self.service.resume_tokens)
                await replacement.close()
                if guest:
                    await guest.receive('opponent_left')
                    self.assertEqual('waiting', self.service.rooms[code]['state'])
                    await guest.close()
                self.assertEqual({}, self.service.rooms)
                self.assertEqual({}, self.service.clients)
                self.assertEqual({}, self.service.resume_tokens)

    async def test_disconnect_after_resume_ack_before_state_can_resume_again(self):
        host, guest, token, _, _, h, _ = await self.start()
        await host.close()
        await guest.receive('opponent_disconnected')
        owner = self.service.resume_tokens[token]
        original_send = self.service._send
        drop_state = True

        async def disconnect_before_state(player, payload):
            nonlocal drop_state
            if drop_state and player == owner and payload['type'] == 'engine_state':
                drop_state = False
                raise WebSocketDisconnect(code=1006)
            await original_send(player, payload)

        self.service._send = disconnect_before_state
        interrupted, temporary = await self.connect()
        await interrupted.send({'type': 'resume_session', 'resume_token': token})
        await interrupted.receive('session_resumed')
        await asyncio.wait_for(interrupted.task, 2)
        self.assertNotIn(temporary, self.service.resume_tokens)
        self.assertIsNone(self.service.clients[owner]['ws'])
        self.assertIn(owner, self.service.expirations)
        self.service._send = original_send
        resumed, _ = await self.connect()
        await resumed.send({'type': 'resume_session', 'resume_token': token})
        await resumed.receive('session_resumed')
        restored = await resumed.receive('engine_state')
        self.assertEqual('p1', restored['role'])
        self.assertEqual(h['view'], restored['view'])
        self.assertEqual(h['request_token'], restored['request_token'])

    async def test_two_simultaneous_resumes_claim_identity_only_once(self):
        host, guest, token, _, _, _, _ = await self.start()
        await host.close()
        await guest.receive('opponent_disconnected')
        first, _ = await self.connect()
        second, _ = await self.connect()
        await first.send({'type': 'resume_session', 'resume_token': token})
        await second.send({'type': 'resume_session', 'resume_token': token})
        async def outcome(socket):
            while True:
                event = await asyncio.wait_for(socket.outgoing.get(), 10)
                payload = json.loads(event['text'])
                if payload['type'] in ('error', 'session_resumed'):
                    return payload['type']
        self.assertEqual({'error', 'session_resumed'}, set(await asyncio.gather(outcome(first), outcome(second))))

    async def test_disconnect_expiry_sends_server_forfeit_and_cleans_match(self):
        self.service.reconnect_seconds = 0.03
        host, guest, _, guest_token, code, h, _ = await self.start()
        await guest.close()
        await host.receive('opponent_disconnected')
        terminal = await host.receive('engine_state')
        self.assertTrue(terminal['view']['ended'])
        self.assertEqual('p1', terminal['view']['winner'])
        self.assertEqual('disconnect_timeout', terminal['view']['end_reason'])
        await host.receive('opponent_left')
        self.assertNotIn(guest_token, self.service.resume_tokens)
        self.assertEqual('waiting', self.service.rooms[code]['state'])
        self.assertEqual({}, self.service.rooms[code]['teams'])
        self.assertNotIn('engine_battle_id', self.service.rooms[code])
        with self.assertRaises(RuntimeError):
            self.engine.get_client().view(h['battle_id'], 'p1')
        replacement, _ = await self.connect()
        await replacement.send({'type': 'resume_session', 'resume_token': guest_token})
        self.assertEqual('resume_unavailable', (await replacement.receive('error'))['code'])

    async def test_server_deadline_automatically_submits_legal_moves(self):
        self.service.turn_timeout_override = 0.03
        host, guest, _, _, code, h, g = await self.start(turn_time=30)
        self.assertIsInstance(h['deadline_ms'], int)
        h2 = await self.at_turn(host, 2)
        g2 = await self.at_turn(guest, 2)
        self.assertEqual(h2['view']['turn'], g2['view']['turn'])
        self.assertNotEqual(h['request_token'], h2['request_token'])
        actions = self.engine.get_client().checkpoint(h['battle_id'])['actions']
        self.assertGreaterEqual(len(actions), 2)
        self.assertTrue(all(entry['action']['type'] in ('move', 'switch') for entry in actions))
        self.assertTrue(any(command.startswith('timeout:') for _, command in self.service.rooms[code]['commands']))

    async def test_unlimited_time_has_no_timer_even_with_test_clock(self):
        self.service.turn_timeout_override = 0.03
        _, _, _, _, code, h, _ = await self.start(turn_time=0)
        self.assertIsNone(h['deadline_ms'])
        await asyncio.sleep(0.08)
        self.assertEqual({}, self.service.turn_tasks)
        self.assertEqual({}, self.service.rooms[code]['commands'])
        self.assertEqual(1, self.engine.get_client().view(h['battle_id'], 'p1')['turn'])

    async def test_disconnect_pauses_deadline_then_resume_restarts_remaining_time(self):
        self.service.turn_timeout_override = 0.3
        host, guest, token, _, code, h, _ = await self.start(turn_time=30)
        await host.close()
        await guest.receive('opponent_disconnected')
        self.assertEqual({}, self.service.turn_tasks)
        await asyncio.sleep(0.4)
        self.assertEqual({}, self.service.rooms[code]['commands'])
        self.assertEqual(1, self.engine.get_client().view(h['battle_id'], 'p1')['turn'])
        resumed, _ = await self.connect()
        await resumed.send({'type': 'resume_session', 'resume_token': token})
        await resumed.receive('session_resumed')
        restored = await resumed.receive('engine_state')
        self.assertFalse(restored['paused'])
        self.assertIsInstance(restored['deadline_ms'], int)
        self.assertEqual(2, len(self.service.turn_tasks))
        self.assertEqual(h['request_token'], restored['request_token'])

    async def test_manual_choice_cancels_only_its_previous_request_timer(self):
        self.service.turn_timeout_override = 0.08
        host, guest, token, _, code, h, _ = await self.start(turn_time=30)
        player = self.service.resume_tokens[token]
        await self.choose(host, h)
        await host.receive('engine_state')
        await self.at_turn(guest, 2)
        self.assertNotIn((player, 'timeout:' + h['request_token']), self.service.rooms[code]['commands'])
        self.assertIn((player, 'manual'), self.service.rooms[code]['commands'])

    async def test_deadline_auto_selects_a_living_forced_replacement(self):
        self.service.turn_timeout_override = 0.03
        host, _, _, _, code, _, _ = await self.start(turn_time=0)
        room = self.service.rooms[code]
        self.engine.get_client().close_battle(room['engine_battle_id'])
        builds = {'p1': default_opponents(), 'p2': default_opponents()}
        builds['p1'][0]['moves'] = ['Protect']
        builds['p2'][0] = {'species': 'Tyranitar', 'ability': 'Sand Stream', 'moves': ['Protect']}
        result = self.engine.get_client().create(builds, seed=[1, 2, 3, 4], initial_state={'p1': [{'hp': 1}, None, None]})
        room.update(engine_battle_id=result['battle_id'], request_tokens={}, request_fingerprints={}, commands={})
        room['rules']['turnTime'] = 30
        await self.service._send_views(room, result)
        while True:
            state = await host.receive('engine_state')
            if any(p.get('active') and p['slot'] != 0 for p in state['view']['own']):
                break
        self.assertEqual(0, state['view']['own'][0]['hp'])
        actions = self.engine.get_client().checkpoint(result['battle_id'])['actions']
        self.assertTrue(any(row['side'] == 'p1' and row['action']['type'] == 'switch' and row['action']['index'] != 0 for row in actions))

    async def test_disconnect_does_not_cancel_an_already_submitted_engine_action(self):
        self.service.turn_timeout_override = 0.03
        host, guest, _, _, code, _, _ = await self.start(turn_time=0)
        room = self.service.rooms[code]
        client = self.engine.get_client()
        choose = client.choose
        entered, release = threading.Event(), threading.Event()
        def delayed_choose(*args):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('Test action gate timed out')
            return choose(*args)
        client.choose = delayed_choose
        room['rules']['turnTime'] = 30
        views = {side: client.view(room['engine_battle_id'], side) for side in ('p1', 'p2')}
        await self.service._send_views(room, {'battle_id': room['engine_battle_id'], 'views': views, 'ended': False})
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        try:
            await guest.close()
            await host.receive('opponent_disconnected')
        finally:
            release.set()
        while True:
            state = await host.receive('engine_state')
            if state['paused']:
                break
        self.assertTrue(state['view']['request']['wait'])
        self.assertEqual(1, len(client.checkpoint(room['engine_battle_id'])['actions']))
        self.assertEqual(1, len(room['commands']))

    async def test_shutdown_cancels_grace_and_turn_tasks_and_closes_engine_battles(self):
        self.service.turn_timeout_override = 2
        host, guest, _, _, _, h, _ = await self.start(turn_time=30)
        await host.close()
        await guest.close()
        self.assertEqual(2, len(self.service.expirations))
        await self.service.shutdown()
        self.assertEqual({}, self.service.expirations)
        self.assertEqual({}, self.service.turn_tasks)
        self.assertEqual({}, self.service.clients)
        self.assertEqual({}, self.service.rooms)
        with self.assertRaises(RuntimeError):
            self.engine.get_client().view(h['battle_id'], 'p1')


if __name__ == '__main__':
    unittest.main()
