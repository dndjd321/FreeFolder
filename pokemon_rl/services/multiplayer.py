"""Validated room lifecycle for the legacy multiplayer protocol.

Battle results are still relayed by this protocol. Authoritative battle rules
belong to the battle-engine adapter; these checks protect the room state and
message shape rather than proving a team or a result is competitively legal.
"""

import json
import math
import secrets
import string

from fastapi import WebSocketDisconnect


MAX_MESSAGE_BYTES = 2_000_000
ROOM_TEXT_LIMIT = 80
NICKNAME_LIMIT = 24
PASSWORD_LIMIT = 128
DEFAULT_RULES = {
    "entryMode": "3", "turnTime": 45, "legendary": True,
    "weather": "", "noDupPokemon": False, "noDupItem": False,
    "noItems": False,
}


class InvalidMessage(ValueError):
    """An invalid client command that must not disconnect the socket."""


def bounded_text(value, field, limit, *, default=None, allow_empty=False):
    if value is None and default is not None:
        value = default
    if not isinstance(value, str):
        raise InvalidMessage(f"{field} 값은 문자열이어야 합니다.")
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise InvalidMessage(f"{field} 값의 길이나 문자를 확인하세요.")
    value = value.strip()
    if not value and not allow_empty:
        raise InvalidMessage(f"{field} 값을 입력하세요.")
    return value


def bounded_int(value, field, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidMessage(f"{field} 값의 범위를 확인하세요.")
    return value


def validate_rules(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_RULES):
        raise InvalidMessage("방 규칙 형식이 올바르지 않습니다.")
    rules = dict(DEFAULT_RULES, **value)
    if rules["entryMode"] not in ("3", "6pick3"):
        raise InvalidMessage("지원하지 않는 엔트리 규칙입니다.")
    if type(rules["turnTime"]) is not int or rules["turnTime"] not in (0, 30, 45, 60):
        raise InvalidMessage("지원하지 않는 제한 시간입니다.")
    if rules["weather"] not in ("", "sun", "rain", "sand", "hail"):
        raise InvalidMessage("지원하지 않는 날씨입니다.")
    for field in ("legendary", "noDupPokemon", "noDupItem", "noItems"):
        if type(rules[field]) is not bool:
            raise InvalidMessage("방 규칙의 선택 값이 올바르지 않습니다.")
    return rules


def _validate_json_tree(value, depth=0):
    if depth > 8:
        raise InvalidMessage("팀 데이터 구조가 너무 깊습니다.")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, str) and len(value) <= 2048:
        return
    if isinstance(value, list) and len(value) <= 1000:
        for item in value:
            _validate_json_tree(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 80:
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise InvalidMessage("팀 데이터 항목이 올바르지 않습니다.")
            _validate_json_tree(item, depth + 1)
        return
    raise InvalidMessage("팀 데이터 형식이나 크기가 올바르지 않습니다.")


def validate_team(value, count, rules):
    if not isinstance(value, list) or len(value) != count:
        raise InvalidMessage(f"포켓몬 {count}마리를 선택하세요.")
    _validate_json_tree(value)
    team = []
    for original in value:
        if not isinstance(original, dict):
            raise InvalidMessage("포켓몬 데이터가 올바르지 않습니다.")
        poke = dict(original)
        bounded_int(poke.get("id"), "포켓몬 ID", 1, 20000)
        poke["name"] = bounded_text(poke.get("name"), "포켓몬 이름", ROOM_TEXT_LIMIT)
        for stat in ("hp", "atk", "def", "spa", "spd", "spe"):
            bounded_int(poke.get(stat), "기본 능력치", 1, 255)
        moves = poke.get("selectedMoves")
        if not isinstance(moves, list) or not 1 <= len(moves) <= 4:
            raise InvalidMessage("기술은 1개 이상 4개 이하로 선택하세요.")
        for move in moves:
            bounded_text(move, "기술 이름", ROOM_TEXT_LIMIT)
        if not isinstance(poke.get("allMoves", []), list):
            raise InvalidMessage("기술 데이터가 올바르지 않습니다.")
        types = poke.get("types")
        if not isinstance(types, list) or not 1 <= len(types) <= 2:
            raise InvalidMessage("포켓몬 타입이 올바르지 않습니다.")
        for poke_type in types:
            bounded_text(poke_type, "포켓몬 타입", 20)
        for field in ("ability", "nature", "selectedItem"):
            if field in poke:
                bounded_text(poke[field], field, ROOM_TEXT_LIMIT, allow_empty=True)
        evs = poke.get("evs", {})
        if not isinstance(evs, dict) or set(evs) - {"hp", "atk", "def", "spatk", "spdef", "spd"}:
            raise InvalidMessage("노력치 데이터가 올바르지 않습니다.")
        for ev in evs.values():
            bounded_int(ev, "노력치", 0, 252)
        if sum(evs.values()) > 510:
            raise InvalidMessage("노력치 합계는 510 이하여야 합니다.")
        if rules["noItems"]:
            poke["selectedItem"] = "없음"
        team.append(poke)
    if rules["noDupPokemon"] and len({p["id"] for p in team}) != count:
        raise InvalidMessage("같은 포켓몬을 중복해서 선택할 수 없습니다.")
    items = [p.get("selectedItem") for p in team if p.get("selectedItem") not in (None, "", "없음")]
    if rules["noDupItem"] and len(set(items)) != len(items):
        raise InvalidMessage("같은 도구를 중복해서 선택할 수 없습니다.")
    return team


class MultiplayerService:
    def __init__(self):
        self.rooms = {}
        self.clients = {}

    def _gen_code(self):
        while True:
            code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
            if code not in self.rooms:
                return code

    async def _send(self, player, payload):
        client = self.clients.get(player)
        if client:
            try:
                await client["ws"].send_json(payload)
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass

    async def _broadcast(self, room, payload, exclude=None):
        for player in list(room["players"]):
            if player != exclude:
                await self._send(player, payload)

    async def leave(self, player):
        client = self.clients.get(player)
        if not client:
            return
        code = client.get("room_code")
        client["room_code"] = None
        room = self.rooms.get(code)
        if not room:
            return
        room["players"] = [p for p in room["players"] if p != player and p in self.clients]
        # Everyone must choose again; no team/action from the abandoned match
        # may survive into a match with a replacement opponent.
        room.update(teams={}, entries={}, turn_actions={}, turn=1, state="waiting", battle_log=[])
        if not room["players"]:
            self.rooms.pop(code, None)
            return
        room["host_ws"] = room["players"][0]
        room["host_nick"] = self.clients[room["host_ws"]]["nickname"]
        await self._broadcast(room, {"type": "opponent_left", "message": "상대가 나갔습니다."})

    def _room_for(self, player, *states):
        room = self.rooms.get(self.clients[player].get("room_code"))
        if not room or player not in room["players"]:
            raise InvalidMessage("먼저 방에 참가하세요.")
        if states and room["state"] not in states:
            raise InvalidMessage("현재 단계에서는 이 행동을 할 수 없습니다.")
        return room

    async def websocket(self, ws):
        await ws.accept()
        player = id(ws)
        self.clients[player] = {"ws": ws, "nickname": "Guest", "room_code": None}
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    if len(raw) > MAX_MESSAGE_BYTES or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                        raise InvalidMessage("메시지가 너무 큽니다.")
                    try:
                        data = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                    except (ValueError, RecursionError):
                        raise InvalidMessage("올바른 JSON 메시지를 보내세요.") from None
                    if not isinstance(data, dict):
                        raise InvalidMessage("메시지는 객체여야 합니다.")
                    message_type = bounded_text(data.get("type"), "메시지 종류", 32)
                    await self.handle(player, message_type, data)
                except InvalidMessage as exc:
                    await self._send(player, {"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await self.leave(player)
            self.clients.pop(player, None)

    async def handle(self, player, message_type, data):
        if message_type == "list_rooms":
            rooms = [{"code": code, "name": room["name"], "host": room["host_nick"],
                      "players": len(room["players"]), "max_players": 2,
                      "format": room["format"], "visibility": room["visibility"], "rules": room["rules"]}
                     for code, room in self.rooms.items()
                     if room["visibility"] == "public" and room["state"] == "waiting" and len(room["players"]) < 2]
            await self._send(player, {"type": "room_list", "rooms": rooms})
        elif message_type == "create_room":
            nickname = bounded_text(data.get("nickname"), "닉네임", NICKNAME_LIMIT, default="Host")
            name = bounded_text(data.get("name"), "방 이름", ROOM_TEXT_LIMIT, default="배틀")
            password = bounded_text(data.get("password"), "비밀번호", PASSWORD_LIMIT, default="", allow_empty=True)
            visibility = data.get("visibility", "public")
            battle_format = data.get("format", "single")
            if visibility not in ("public", "private") or battle_format not in ("single", "double"):
                raise InvalidMessage("지원하지 않는 방 설정입니다.")
            rules = validate_rules(data.get("rules"))
            await self.leave(player)
            code = self._gen_code()
            self.clients[player].update(nickname=nickname, room_code=code)
            self.rooms[code] = {"name": name, "host_ws": player, "host_nick": nickname,
                                "visibility": visibility, "password": password, "format": battle_format,
                                "rules": rules, "players": [player], "state": "waiting", "teams": {},
                                "entries": {}, "turn": 1, "turn_actions": {}, "battle_log": []}
            await self._send(player, {"type": "room_created", "code": code, "room_name": name, "rules": rules})
        elif message_type == "join_room":
            code = bounded_text(data.get("code"), "방 코드", 5).upper()
            nickname = bounded_text(data.get("nickname"), "닉네임", NICKNAME_LIMIT, default="Guest")
            password = bounded_text(data.get("password"), "비밀번호", PASSWORD_LIMIT, default="", allow_empty=True)
            self._validate_join(player, code, password)
            await self.leave(player)
            # Leaving may await a slow peer. Recheck capacity after that await.
            room = self._validate_join(player, code, password)
            room["players"].append(player)
            room["state"] = "team_select"
            self.clients[player].update(nickname=nickname, room_code=code)
            await self._send(player, {"type": "room_joined", "code": code, "room_name": room["name"], "rules": room["rules"]})
            await self._send(room["host_ws"], {"type": "opponent_joined", "nickname": nickname})
            await self._broadcast(room, {"type": "team_select", "rules": room["rules"], "message": "상대가 입장! 팀을 선택하세요!"})
        elif message_type == "leave_room":
            await self.leave(player)
            await self._send(player, {"type": "room_left"})
        elif message_type in ("entry_ready", "pick_ready", "team_ready"):
            await self._submit_team(player, message_type, data)
        elif message_type == "battle_action":
            room = self._room_for(player, "battle")
            action = data.get("action")
            if action not in ("move", "switch", "forced_switch"):
                raise InvalidMessage("지원하지 않는 배틀 행동입니다.")
            index = bounded_int(data.get("index"), "행동 슬롯", 0, 3 if action == "move" else 2)
            if action == "forced_switch":
                name = bounded_text(data.get("pokemon_name"), "포켓몬 이름", ROOM_TEXT_LIMIT, default="???")
                await self._broadcast(room, {"type": "opponent_forced_switch", "pokemon_name": name, "index": index}, exclude=player)
                return
            if player in room["turn_actions"]:
                raise InvalidMessage("이번 턴 행동은 이미 접수되었습니다.")
            room["turn_actions"][player] = {"action": action, "index": index}
            if all(p in room["turn_actions"] for p in room["players"]):
                payload = {"type": "turn_result", "turn": room["turn"], "seed": secrets.randbelow(1_000_000),
                           "actions": {f"player{i + 1}": room["turn_actions"][p] for i, p in enumerate(room["players"])}}
                room["turn"] += 1
                room["turn_actions"] = {}
                await self._broadcast(room, payload)
            else:
                await self._send(player, {"type": "action_received", "message": "행동 입력 완료! 상대를 기다리는 중..."})
        elif message_type == "battle_end":
            room = self._room_for(player, "battle")
            winner = bounded_text(data.get("winner"), "승자", ROOM_TEXT_LIMIT, default="unknown")
            message = bounded_text(data.get("message"), "종료 메시지", 200, default="배틀 종료!")
            room["state"] = "done"
            await self._broadcast(room, {"type": "battle_ended", "winner": winner, "message": message})
        elif message_type == "chat":
            room = self._room_for(player)
            message = bounded_text(data.get("message"), "채팅", 200)
            await self._broadcast(room, {"type": "chat", "nickname": self.clients[player]["nickname"], "message": message})
        else:
            raise InvalidMessage("지원하지 않는 메시지입니다.")

    def _validate_join(self, player, code, password):
        room = self.rooms.get(code)
        if not room:
            raise InvalidMessage("방을 찾을 수 없습니다.")
        if player in room["players"]:
            raise InvalidMessage("이미 참가한 방입니다.")
        if room["state"] != "waiting" or len(room["players"]) >= 2:
            raise InvalidMessage("참가할 수 없는 방입니다.")
        if room["visibility"] == "private" and room["password"] and password != room["password"]:
            raise InvalidMessage("비밀번호가 틀렸습니다.")
        return room

    async def _submit_team(self, player, message_type, data):
        room = self._room_for(player, "team_select", "pick")
        picking = room["rules"]["entryMode"] == "6pick3"
        if message_type == "entry_ready" and picking and room["state"] == "team_select":
            room["entries"][player] = validate_team(data.get("team"), 6, room["rules"])
            if len(room["entries"]) == 2:
                room["state"] = "pick"
                players = list(room["players"])
                for i, pid in enumerate(players):
                    await self._send(pid, {"type": "pick_phase", "opp_team": room["entries"][players[1 - i]]})
            else:
                await self._broadcast(room, {"type": "opponent_team_ready", "nickname": self.clients[player]["nickname"]}, exclude=player)
            return
        if message_type == "pick_ready" and picking and room["state"] == "pick":
            picks = data.get("picks")
            if not isinstance(picks, list) or len(picks) != 3 or any(type(i) is not int or not 0 <= i < 6 for i in picks) or len(set(picks)) != 3:
                raise InvalidMessage("엔트리에서 서로 다른 3마리를 선택하세요.")
            room["teams"][player] = [room["entries"][player][i] for i in picks]
        elif message_type == "team_ready" and not picking and room["state"] == "team_select":
            room["teams"][player] = validate_team(data.get("team"), 3, room["rules"])
        else:
            raise InvalidMessage("현재 엔트리 단계에 맞는 팀을 제출하세요.")
        if len(room["teams"]) == 2:
            room.update(state="battle", turn=1, turn_actions={}, random_seed=secrets.randbelow(1_000_000))
            players = list(room["players"])
            for i, pid in enumerate(players):
                opponent = players[1 - i]
                await self._send(pid, {"type": "battle_start", "your_team": room["teams"][pid],
                                       "opp_team": room["teams"][opponent], "opp_nickname": self.clients[opponent]["nickname"],
                                       "you_are": f"player{i + 1}", "format": room["format"], "seed": room["random_seed"]})
        else:
            await self._broadcast(room, {"type": "opponent_team_ready", "nickname": self.clients[player]["nickname"]}, exclude=player)
