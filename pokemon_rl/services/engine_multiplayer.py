"""Server-authoritative room battles using the same adapter as single play."""
import asyncio
import copy
import json
import re
import secrets
import time
from fastapi import WebSocketDisconnect

from .engine_api import canonical_legacy_team, choose_legal_action
from .multiplayer import InvalidMessage, MultiplayerService, bounded_int, bounded_text, MAX_MESSAGE_BYTES


# Generated from pokemon-showdown 0.11.11 Dex tags: Restricted Legendary,
# Sub-Legendary and Mythical. Forms share their base national-dex number.
# test_room_rules.py checks this set against the installed pinned engine data.
LEGENDARY_DEX_NUMBERS = frozenset((
    144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251,
    377, 378, 379, 380, 381, 382, 383, 384, 385, 386,
    480, 481, 482, 483, 484, 485, 486, 487, 488, 489, 490, 491, 492, 493, 494,
    638, 639, 640, 641, 642, 643, 644, 645, 646, 647, 648, 649,
    716, 717, 718, 719, 720, 721, 772, 773, 785, 786, 787, 788, 789, 790,
    791, 792, 800, 801, 802, 807, 808, 809, 888, 889, 890, 891, 892, 893,
    894, 895, 896, 897, 898, 905, 1001, 1002, 1003, 1004, 1007, 1008,
    1014, 1015, 1016, 1017, 1024, 1025,
))


class ResumeRejected(InvalidMessage):
    """A resume rejection whose code lets clients retry only a live-socket race."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def validate_engine_room_rules(result, canonical_teams, rules):
    """Apply room restrictions to the actual validated engine builds.

    Legacy outer IDs and display item strings are untrusted UI metadata. Using
    the starting checkpoint's item also prevents switch-in consumption from
    hiding a duplicate held item before this validation runs.
    """
    for side in ('p1', 'p2'):
        pokemon = result['views'][side]['own']
        numbers = [mon['num'] for mon in pokemon]
        if rules.get('noDupPokemon') and len(set(numbers)) != len(numbers):
            raise InvalidMessage('실제 팀에 같은 종의 포켓몬을 중복해서 선택할 수 없습니다.')
        item_ids = [re.sub(r'[^a-z0-9]', '', mon.get('item', '').lower()) for mon in canonical_teams[side]]
        items = [item for item in item_ids if item]
        if rules.get('noItems') and items:
            raise InvalidMessage('도구를 사용할 수 없는 방입니다.')
        if rules.get('noDupItem') and len(set(items)) != len(items):
            raise InvalidMessage('실제 팀에 같은 도구를 중복해서 선택할 수 없습니다.')
        if rules.get('legendary') is False and any(number in LEGENDARY_DEX_NUMBERS for number in numbers):
            raise InvalidMessage('전설·환상 포켓몬을 사용할 수 없는 방입니다.')


class AuthoritativeMultiplayerService(MultiplayerService):
    def __init__(self, engine_sessions):
        super().__init__()
        self.engine_sessions = engine_sessions
        self.authoritative = True
        self.resume_tokens = {}
        self.expirations = {}
        self.reconnect_seconds = 60
        self.turn_tasks = {}
        self.turn_deadlines = {}
        self.turn_remaining = {}
        self.turn_tokens = {}
        self.turn_applying = {}
        self.background_tasks = set()
        self.stopping = False
        self.turn_timeout_override = None  # Internal test clock; never supplied by a client.

    async def websocket(self, ws):
        if not self.authoritative:
            return await super().websocket(ws)
        await ws.accept()
        # A disconnected socket object can be garbage-collected while its player
        # remains resumable. Object IDs can then be reused by another socket.
        player = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        self.clients[player] = {'ws': ws, 'nickname': 'Guest', 'room_code': None, 'resume_token': token}
        self.resume_tokens[token] = player
        await self._send(player, {'type': 'session', 'resume_token': token})
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    if len(raw.encode('utf-8')) > MAX_MESSAGE_BYTES:
                        raise InvalidMessage('메시지가 너무 큽니다.')
                    try:
                        data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    except (ValueError, RecursionError):
                        raise InvalidMessage('올바른 JSON 메시지를 보내세요.') from None
                    if not isinstance(data, dict):
                        raise InvalidMessage('메시지는 객체여야 합니다.')
                    message_type = bounded_text(data.get('type'), '메시지 종류', 32)
                    if message_type == 'resume_session':
                        current = player
                        # Claim synchronously so finally always owns the resumed
                        # identity, even if the ack or state send disconnects.
                        token = data.get('resume_token')
                        player = self._claim_resume(ws, token)
                        await self._resume(current, player, token)
                    else:
                        await self.handle(player, message_type, data)
                except InvalidMessage as exc:
                    payload = {'type': 'error', 'message': str(exc)}
                    if isinstance(exc, ResumeRejected):
                        payload['code'] = exc.code
                    await self._send(player, payload)
        except WebSocketDisconnect:
            pass
        finally:
            info = self.clients.get(player)
            if info and info.get('ws') is ws:
                info['ws'] = None
                room = self.rooms.get(info.get('room_code'))
                if room and room.get('engine_battle_id') and room.get('state') == 'battle':
                    self._pause_turn_timers(room)
                    await self._broadcast(room, {'type': 'opponent_disconnected', 'seconds': self.reconnect_seconds}, exclude=player)
                    self.expirations[player] = self._background(self._expire(player))
                else:
                    await self._remove_client(player)

    async def _expire(self, player):
        try:
            await asyncio.sleep(self.reconnect_seconds)
            info = self.clients.get(player)
            if info and info.get('ws') is None:
                info['expiring'] = True
                await self._remove_client(player)
        except asyncio.CancelledError:
            pass
        finally:
            if self.expirations.get(player) is asyncio.current_task():
                self.expirations.pop(player, None)

    async def _remove_client(self, player):
        await self.leave(player)
        self._stop_turn_timer(player)
        info = self.clients.pop(player, {})
        self.resume_tokens.pop(info.get('resume_token'), None)

    def _claim_resume(self, ws, token):
        if not isinstance(token, str) or not 1 <= len(token) <= 128:
            raise ResumeRejected('재접속 정보가 올바르지 않습니다.', 'resume_unavailable')
        previous = self.resume_tokens.get(token)
        info = self.clients.get(previous)
        if not info or info.get('expiring'):
            raise ResumeRejected('재접속할 배틀을 찾을 수 없습니다.', 'resume_unavailable')
        if info.get('ws') is not None:
            # The old socket may still be processing a browser reload's close.
            # Never take over an attached identity; let the client retry briefly.
            raise ResumeRejected('기존 연결 종료를 기다리는 중입니다. 잠시 후 재접속하세요.', 'resume_busy')
        task = self.expirations.pop(previous, None)
        if task:
            task.cancel()
        # Reserve the identity before awaiting cleanup of the temporary session.
        # A second simultaneous resume must see this socket as already attached.
        info['ws'] = ws
        return previous

    async def _resume(self, current, previous, token):
        await self._remove_client(current)
        info = self.clients[previous]
        await self._send(previous, {'type': 'session_resumed', 'resume_token': token, 'room_code': info.get('room_code')})
        room = self.rooms.get(info.get('room_code'))
        if room and room.get('engine_battle_id'):
            bid = room['engine_battle_id']
            def current_state():
                views = {side: self._engine().view(bid, side) for side in ('p1', 'p2')}
                return {'battle_id': bid, 'views': views, 'ended': views['p1']['ended']}
            async with room['engine_lock']:
                if room.get('engine_battle_id') == bid:
                    result = await asyncio.to_thread(current_state)
                    await self._send_views(room, result)
                    await self._broadcast(room, {'type': 'opponent_reconnected'}, exclude=previous)
        elif room:
            await self._send(previous, {'type': 'opponent_left', 'message': '이전 배틀이 종료되었습니다. 방에서 다시 시작하세요.'})

    async def shutdown(self):
        self.stopping = True
        tasks = list(self.background_tasks)
        applying_tasks = set(self.turn_applying.values())
        for task in tasks:
            if task not in applying_tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.expirations.clear()
        self.turn_tasks.clear()
        self.turn_deadlines.clear()
        self.turn_remaining.clear()
        self.turn_tokens.clear()
        self.turn_applying.clear()
        self.background_tasks.clear()
        for room in list(self.rooms.values()):
            bid = room.get('engine_battle_id')
            if bid and self.engine_sessions.client is not None:
                try:
                    await asyncio.to_thread(lambda bid=bid: self._engine().close_battle(bid))
                except RuntimeError:
                    pass
        self.clients.clear()
        self.rooms.clear()
        self.resume_tokens.clear()

    def _engine(self):
        return self.engine_sessions.get_client()

    def _background(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def leave(self, player):
        room = self.rooms.get(self.clients.get(player, {}).get('room_code'))
        if room:
            lock = room.get('engine_lock')
            if lock:
                async with lock:
                    await self._finish_forfeit(room, player)
                    self._close_room_timers(room)
                    await self._close_room_battle(room)
            else:
                self._close_room_timers(room)
                await self._close_room_battle(room)
        await super().leave(player)

    async def _close_room_battle(self, room):
        if room:
            bid = room.pop('engine_battle_id', None)
            room.pop('request_tokens', None)
            room.pop('request_fingerprints', None)
            room.pop('commands', None)
            room['generation'] = room.get('generation', 0) + 1
            if bid and self.engine_sessions.client is not None:
                try:
                    await asyncio.to_thread(lambda: self._engine().close_battle(bid))
                except RuntimeError:
                    pass

    async def _finish_forfeit(self, room, player):
        """Disconnect/leave adjudication belongs to the server match service."""
        if room.get('state') != 'battle' or not room.get('engine_battle_id') or len(room['players']) != 2:
            return
        opponent = next(pid for pid in room['players'] if pid != player)
        if self.clients.get(opponent, {}).get('ws') is None:
            return
        bid = room['engine_battle_id']
        winner = f'p{room["players"].index(opponent) + 1}'
        reason = 'disconnect_timeout' if self.clients.get(player, {}).get('ws') is None else 'player_left'
        def terminal_views():
            views = {side: self._engine().view(bid, side) for side in ('p1', 'p2')}
            for view in views.values():
                view.update(ended=True, winner=winner, end_reason=reason, request={'wait': True})
            return {'battle_id': bid, 'views': views, 'ended': True}
        result = await asyncio.to_thread(terminal_views)
        await self._send_views(room, result)

    def _turn_limit(self, room):
        configured = room.get('rules', {}).get('turnTime', 45)
        if configured == 0:
            return 0
        return configured if self.turn_timeout_override is None else self.turn_timeout_override

    def _stop_turn_timer(self, player, *, preserve=False):
        task = self.turn_tasks.pop(player, None)
        deadline = self.turn_deadlines.pop(player, None)
        if preserve and deadline is not None:
            self.turn_remaining[player] = max(0, deadline - asyncio.get_running_loop().time())
        if not preserve:
            self.turn_remaining.pop(player, None)
            self.turn_tokens.pop(player, None)
        if task and task is not asyncio.current_task() and player not in self.turn_applying:
            task.cancel()

    def _close_room_timers(self, room):
        for player in room.get('players', []):
            self._stop_turn_timer(player)

    def _paused(self, room):
        return any(self.clients.get(pid, {}).get('ws') is None for pid in room['players'])

    def _pause_turn_timers(self, room):
        for player in room['players']:
            self._stop_turn_timer(player, preserve=True)

    def _sync_turn_timers(self, room, result):
        limit, paused = self._turn_limit(room), self._paused(room)
        for index, player in enumerate(room['players']):
            view = result['views'][f'p{index+1}']
            if self.stopping or result.get('ended') or view.get('request', {}).get('wait') or not limit:
                self._stop_turn_timer(player)
                continue
            token = room['request_tokens'][player]
            if self.turn_tokens.get(player) != token:
                self._stop_turn_timer(player)
                self.turn_tokens[player] = token
                self.turn_remaining[player] = limit
            if paused:
                self._stop_turn_timer(player, preserve=True)
            elif player not in self.turn_tasks:
                duration = self.turn_remaining.pop(player, limit)
                self.turn_deadlines[player] = asyncio.get_running_loop().time() + duration
                self.turn_tasks[player] = self._background(self._turn_timeout(room, player, token, duration))

    async def _turn_timeout(self, room, player, token, duration):
        task = asyncio.current_task()
        try:
            await asyncio.sleep(duration)
            async with room['engine_lock']:
                if room.get('state') != 'battle' or self._paused(room) or room.get('request_tokens', {}).get(player) != token:
                    return
                side = f'p{room["players"].index(player)+1}'
                view = await asyncio.to_thread(lambda: self._engine().view(room['engine_battle_id'], side))
                action = choose_legal_action(view.get('request'))
                if action is None:
                    return
                await self._broadcast(room, {'type': 'turn_timeout', 'role': side, 'message': '시간 초과로 서버가 합법 행동을 선택했습니다.'})
                self.turn_applying[player] = task
                await self._action_locked(player, {'action': action['type'], 'index': action['index'],
                    'request_token': token, 'command_id': 'timeout:' + token})
        except (asyncio.CancelledError, InvalidMessage, RuntimeError, KeyError):
            pass
        finally:
            if self.turn_applying.get(player) is task:
                self.turn_applying.pop(player, None)
            if self.turn_tasks.get(player) is task:
                self.turn_tasks.pop(player, None)
                self.turn_deadlines.pop(player, None)

    async def handle(self, player, message_type, data):
        if not self.authoritative:
            return await super().handle(player, message_type, data)
        if message_type == 'create_room':
            if data.get('format', 'single') != 'single':
                raise InvalidMessage('현재 서버는 싱글 배틀을 지원합니다.')
            if isinstance(data.get('rules'), dict) and data['rules'].get('weather'):
                raise InvalidMessage('고정 날씨는 아직 지원하지 않습니다. 기본 날씨를 선택하세요.')
        if message_type == 'battle_end':
            raise InvalidMessage('승패는 서버가 판정합니다.')
        if message_type == 'battle_action':
            await self._action(player, data)
            return
        await super().handle(player, message_type, data)

    async def start_battle(self, room):
        if not self.authoritative:
            return await super().start_battle(room)
        players = tuple(room['players'])
        generation = room.get('generation', 0)
        room['state'] = 'starting'
        try:
            teams = {f'p{i+1}': canonical_legacy_team(copy.deepcopy(room['teams'][pid])) for i, pid in enumerate(players)}
            if room['rules'].get('noItems'):
                for team in teams.values():
                    for pokemon in team:
                        pokemon['item'] = ''
            def create_validated_battle():
                engine = self._engine()
                result = engine.create(teams, seed=[secrets.randbelow(65536) for _ in range(4)],
                                       names={'p1': 'Player 1', 'p2': 'Player 2'})
                try:
                    canonical_teams = engine.checkpoint(result['battle_id'])['teams']
                    validate_engine_room_rules(result, canonical_teams, room['rules'])
                except BaseException:
                    engine.close_battle(result['battle_id'])
                    raise
                return result
            result = await asyncio.to_thread(create_validated_battle)
        except (ValueError, RuntimeError) as exc:
            if tuple(room['players']) == players and room.get('generation', 0) == generation:
                room.update(state='team_select', teams={}, entries={})
                await self._broadcast(room, {'type': 'error', 'message': '팀 검증 실패: ' + str(exc)})
                await self._broadcast(room, {'type': 'team_select', 'rules': room['rules']})
            return
        if tuple(room['players']) != players or room.get('generation', 0) != generation:
            await asyncio.to_thread(lambda: self._engine().close_battle(result['battle_id']))
            return
        room.update(state='battle', engine_battle_id=result['battle_id'], commands={}, request_tokens={}, request_fingerprints={}, engine_lock=asyncio.Lock())
        await self._send_views(room, result)

    async def _send_views(self, room, result):
        players = list(room['players'])
        generation = room.get('generation', 0)
        if len(players) != 2:
            return
        for index, pid in enumerate(players):
            if room.get('generation', 0) != generation:
                return
            side = f'p{index+1}'
            view = result['views'][side]
            fingerprint = json.dumps(view.get('request'), sort_keys=True)
            if room['request_fingerprints'].get(pid) != fingerprint:
                room['request_tokens'][pid] = secrets.token_urlsafe(18)
                room['request_fingerprints'][pid] = fingerprint
        self._sync_turn_timers(room, result)
        for index, pid in enumerate(players):
            if room.get('generation', 0) != generation:
                return
            side = f'p{index+1}'
            view = result['views'][side]
            opponent = players[1-index]
            deadline = self.turn_deadlines.get(pid)
            await self._send(pid, {'type': 'engine_state', 'battle_id': result['battle_id'], 'role': side,
                'view': view, 'request_token': room['request_tokens'][pid],
                'paused': self._paused(room),
                'deadline_ms': None if deadline is None else round((time.time() + deadline - asyncio.get_running_loop().time()) * 1000),
                'opp_nickname': self.clients.get(opponent, {}).get('nickname', '상대')})
        if result.get('ended'):
            room['state'] = 'done'

    async def _action(self, player, data):
        room = self._room_for(player, 'battle')
        async with room['engine_lock']:
            await self._action_locked(player, data)

    async def _action_locked(self, player, data):
        room = self._room_for(player, 'battle')
        if self._paused(room):
            raise InvalidMessage('상대의 재접속을 기다리는 동안 배틀이 일시 정지됩니다.')
        bid = room.get('engine_battle_id')
        if not bid:
            raise InvalidMessage('서버 배틀이 준비되지 않았습니다.')
        command_id = data.get('command_id')
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 80:
            raise InvalidMessage('요청 ID를 확인하세요.')
        if (player, command_id) in room['commands']:
            await self._send(player, room['commands'][(player, command_id)])
            return
        if data.get('request_token') != room['request_tokens'].get(player):
            raise InvalidMessage('이전 턴의 입력입니다. 최신 화면에서 선택하세요.')
        action_type = data.get('action')
        if action_type not in ('move', 'switch'):
            raise InvalidMessage('행동을 확인하세요.')
        index = bounded_int(data.get('index'), '행동 슬롯', 0, 3 if action_type == 'move' else 2)
        side = f'p{room["players"].index(player)+1}'
        generation = room.get('generation', 0)
        # Invalidate this request before yielding, so concurrent duplicates cannot act twice.
        old_token = room['request_tokens'][player]
        room['request_tokens'][player] = secrets.token_urlsafe(18)
        try:
            result = await asyncio.to_thread(lambda: self._engine().choose(bid, side, {'type': action_type, 'index': index}))
        except RuntimeError as exc:
            if room.get('generation', 0) == generation:
                room['request_tokens'][player] = old_token
            raise InvalidMessage(str(exc)) from exc
        if room.get('engine_battle_id') != bid or room.get('generation', 0) != generation:
            return
        ack = {'type': 'action_received', 'command_id': command_id, 'message': '서버가 행동을 접수했습니다.'}
        room['commands'][(player, command_id)] = ack
        if len(room['commands']) > 1000:
            room['commands'].pop(next(iter(room['commands'])))
        await self._send_views(room, result)
        await self._send(player, ack)
