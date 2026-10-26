"""Server-authoritative room battles using the same adapter as single play."""
import asyncio
import copy
import json
import secrets

from .engine_api import canonical_legacy_team
from .multiplayer import InvalidMessage, MultiplayerService, bounded_int


class AuthoritativeMultiplayerService(MultiplayerService):
    def __init__(self, engine_sessions):
        super().__init__()
        self.engine_sessions = engine_sessions
        self.authoritative = True

    def _engine(self):
        return self.engine_sessions.get_client()

    async def leave(self, player):
        room = self.rooms.get(self.clients.get(player, {}).get('room_code'))
        if room:
            bid = room.pop('engine_battle_id', None)
            room.pop('request_tokens', None)
            room.pop('request_fingerprints', None)
            room.pop('commands', None)
            room['generation'] = room.get('generation', 0) + 1
            if bid:
                try:
                    await asyncio.to_thread(lambda: self._engine().close_battle(bid))
                except RuntimeError:
                    pass
        await super().leave(player)

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
            result = await asyncio.to_thread(lambda: self._engine().create(teams,
                seed=[secrets.randbelow(65536) for _ in range(4)], names={'p1': 'Player 1', 'p2': 'Player 2'}))
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
            opponent = players[1-index]
            await self._send(pid, {'type': 'engine_state', 'battle_id': result['battle_id'], 'role': side,
                'view': view, 'request_token': room['request_tokens'][pid],
                'opp_nickname': self.clients.get(opponent, {}).get('nickname', '상대')})
        if result.get('ended'):
            room['state'] = 'done'

    async def _action(self, player, data):
        room = self._room_for(player, 'battle')
        async with room['engine_lock']:
            await self._action_locked(player, data)

    async def _action_locked(self, player, data):
        room = self._room_for(player, 'battle')
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
        await self._send_views(room, result)
        await self._send(player, ack)
