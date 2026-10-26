"""Exercise both real ASGI websocket routes without a third-party test client."""

import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest


GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

with contextlib.redirect_stdout(io.StringIO()):
    import server_deploy
    from server_core import create_app


def team(count=3):
    return [{"id": i + 1, "name": f"Pokemon{i + 1}", "types": ["normal"],
             "hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80,
             "ability": "none", "nature": "성급", "selectedItem": "없음",
             "selectedMoves": ["몸통"], "allMoves": [], "evs": {}}
            for i in range(count)]


class ASGISocket:
    def __init__(self, app):
        self.app = app
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.task = None

    async def connect(self):
        scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.1"},
                 "path": "/ws/multi", "raw_path": b"/ws/multi", "root_path": "",
                 "scheme": "ws", "query_string": b"", "headers": [],
                 "client": ("127.0.0.1", 12345), "server": ("testserver", 80), "subprotocols": []}
        self.task = asyncio.create_task(self.app(scope, self.incoming.get, self.outgoing.put))
        await self.incoming.put({"type": "websocket.connect"})
        event = await asyncio.wait_for(self.outgoing.get(), 2)
        assert event["type"] == "websocket.accept", event
        return self

    async def send_raw(self, raw):
        await self.incoming.put({"type": "websocket.receive", "text": raw})

    async def send(self, payload):
        await self.send_raw(json.dumps(payload, ensure_ascii=False))

    async def receive(self, message_type):
        async def find():
            while True:
                event = await self.outgoing.get()
                assert event["type"] == "websocket.send", event
                payload = json.loads(event["text"])
                if payload["type"] == message_type:
                    return payload
        return await asyncio.wait_for(find(), 2)

    async def close(self):
        if self.task is not None and not self.task.done():
            await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        if self.task is not None:
            await asyncio.wait_for(self.task, 2)


class CloudRoomProtocolTests(unittest.IsolatedAsyncioTestCase):
    server_kind = "cloud"

    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.app = server_deploy.app if cls.server_kind == "cloud" else create_app("")[0]

    async def asyncSetUp(self):
        self.service = self.app.state.multiplayer
        self.assertEqual({}, self.service.clients)
        self.assertEqual({}, self.service.rooms)
        self.sockets = []

    async def asyncTearDown(self):
        for socket in reversed(self.sockets):
            await socket.close()
        self.assertEqual({}, self.service.clients)
        self.assertEqual({}, self.service.rooms)

    async def connect(self):
        socket = await ASGISocket(self.app).connect()
        self.sockets.append(socket)
        return socket

    async def create(self, socket, nickname="Host", **settings):
        payload = {"type": "create_room", "nickname": nickname, "name": "Test battle"}
        payload.update(settings)
        await socket.send(payload)
        return (await socket.receive("room_created"))["code"]

    async def join(self, socket, code, nickname="Guest", **settings):
        await socket.send({"type": "join_room", "code": code, "nickname": nickname, **settings})
        return await socket.receive("room_joined")

    async def start_battle(self, host, guest, **settings):
        code = await self.create(host, **settings)
        await self.join(guest, code)
        await host.send({"type": "team_ready", "team": team()})
        await guest.send({"type": "team_ready", "team": team()})
        first = await host.receive("battle_start")
        second = await guest.receive("battle_start")
        return code, first, second

    async def test_malformed_payload_reports_error_without_disconnect(self):
        socket = await self.connect()
        for raw in ("not-json", "null", "[]", "123", '{"type": []}', '{"type":"create_room","rules":{"turnTime":NaN}}'):
            with self.subTest(raw=raw):
                await socket.send_raw(raw)
                await socket.receive("error")
                await socket.send({"type": "list_rooms"})
                self.assertEqual([], (await socket.receive("room_list"))["rooms"])

    async def test_invalid_create_does_not_abandon_existing_room(self):
        socket = await self.connect()
        code = await self.create(socket)
        for extra in ({"name": "x" * 81}, {"nickname": []}, {"password": 12}, {"rules": []},
                      {"rules": {"turnTime": True}}, {"rules": {"weather": "<img>"}},
                      {"rules": {"noItems": "false"}}, {"rules": {"unknown": 1}}):
            with self.subTest(extra=extra):
                await socket.send({"type": "create_room", **extra})
                await socket.receive("error")
                self.assertEqual([code], list(self.service.rooms))

    async def test_repeated_room_creation_removes_old_room(self):
        socket = await self.connect()
        first = await self.create(socket)
        second = await self.create(socket)
        self.assertNotEqual(first, second)
        self.assertEqual([second], list(self.service.rooms))

    async def test_duplicate_join_keeps_one_player_and_waiting_state(self):
        host = await self.connect()
        code = await self.create(host)
        await host.send({"type": "join_room", "code": code})
        await host.receive("error")
        self.assertEqual(1, len(self.service.rooms[code]["players"]))
        self.assertEqual("waiting", self.service.rooms[code]["state"])
        guest = await self.connect()
        await self.join(guest, code)
        await guest.send({"type": "join_room", "code": code})
        await guest.receive("error")
        self.assertEqual(2, len(self.service.rooms[code]["players"]))

    async def test_abandoned_match_restarts_with_replacement_player(self):
        host, guest, replacement = await self.connect(), await self.connect(), await self.connect()
        code, _, _ = await self.start_battle(host, guest)
        await host.send({"type": "battle_action", "action": "move", "index": 0})
        await host.receive("action_received")
        await self.create(guest, nickname="Moved")
        await host.receive("opponent_left")
        room = self.service.rooms[code]
        self.assertEqual("waiting", room["state"])
        for field in ("teams", "entries", "turn_actions"):
            self.assertEqual({}, room[field])
        await self.join(replacement, code)
        await host.send({"type": "team_ready", "team": team()})
        await replacement.send({"type": "team_ready", "team": team()})
        await host.receive("battle_start")
        await replacement.receive("battle_start")
        self.assertEqual(2, len(room["teams"]))
        self.assertEqual("battle", room["state"])

    async def test_disconnect_transfers_host_and_clears_teams(self):
        host, guest = await self.connect(), await self.connect()
        code, _, _ = await self.start_battle(host, guest)
        await host.close()
        await guest.receive("opponent_left")
        room = self.service.rooms[code]
        self.assertEqual("Guest", room["host_nick"])
        self.assertEqual(room["players"][0], room["host_ws"])
        self.assertEqual({}, room["teams"])
        await guest.send({"type": "list_rooms"})
        listing = (await guest.receive("room_list"))["rooms"]
        self.assertEqual("Guest", listing[0]["host"])

    async def test_private_join_failure_preserves_current_room(self):
        host, guest = await self.connect(), await self.connect()
        private = await self.create(host, visibility="private", password="secret")
        current = await self.create(guest)
        await guest.send({"type": "join_room", "code": private, "password": "wrong"})
        await guest.receive("error")
        self.assertIn(current, self.service.rooms)
        await self.join(guest, private, password="secret")
        self.assertNotIn(current, self.service.rooms)

    async def test_invalid_team_and_actions_do_not_corrupt_match(self):
        host, guest = await self.connect(), await self.connect()
        code = await self.create(host)
        await self.join(guest, code)
        bad_stat = team()
        bad_stat[0]["hp"] = 10**9
        for bad_team in ([], ["bad"] * 3, bad_stat):
            await host.send({"type": "team_ready", "team": bad_team})
            await host.receive("error")
            self.assertEqual({}, self.service.rooms[code]["teams"])
        await host.send({"type": "team_ready", "team": team()})
        await guest.send({"type": "team_ready", "team": team()})
        first = await host.receive("battle_start")
        second = await guest.receive("battle_start")
        self.assertEqual(first["seed"], second["seed"])
        self.assertEqual("player1", first["you_are"])
        self.assertEqual("player2", second["you_are"])
        for action in ({"action": "teleport", "index": 0}, {"action": "move", "index": -1},
                       {"action": "move", "index": True}, {"action": "switch", "index": 3}):
            await host.send({"type": "battle_action", **action})
            await host.receive("error")
            self.assertEqual({}, self.service.rooms[code]["turn_actions"])

    async def test_turn_action_is_not_overwritten_and_results_match(self):
        host, guest = await self.connect(), await self.connect()
        code, _, _ = await self.start_battle(host, guest)
        await host.send({"type": "battle_action", "action": "move", "index": 0})
        await host.receive("action_received")
        await host.send({"type": "battle_action", "action": "move", "index": 1})
        await host.receive("error")
        await guest.send({"type": "battle_action", "action": "move", "index": 2})
        first, second = await host.receive("turn_result"), await guest.receive("turn_result")
        self.assertEqual(first, second)
        self.assertEqual({"action": "move", "index": 0}, first["actions"]["player1"])
        self.assertEqual(2, self.service.rooms[code]["turn"])

    async def test_pick_uses_server_entry_and_rejects_repeated_slots(self):
        host, guest = await self.connect(), await self.connect()
        code = await self.create(host, rules={"entryMode": "6pick3"})
        await self.join(guest, code)
        await host.send({"type": "entry_ready", "team": team(6)})
        await guest.send({"type": "entry_ready", "team": team(6)})
        await host.receive("pick_phase")
        await guest.receive("pick_phase")
        await host.send({"type": "pick_ready", "picks": [0, 0, 1], "team": team()})
        await host.receive("error")
        self.assertEqual({}, self.service.rooms[code]["teams"])
        await host.send({"type": "pick_ready", "picks": [0, 2, 4], "team": [{"id": 9999}]})
        await guest.send({"type": "pick_ready", "picks": [1, 3, 5]})
        first = await host.receive("battle_start")
        await guest.receive("battle_start")
        self.assertEqual([1, 3, 5], [p["id"] for p in first["your_team"]])

    async def test_no_item_rule_is_applied_on_server(self):
        host, guest = await self.connect(), await self.connect()
        code = await self.create(host, rules={"noItems": True})
        await self.join(guest, code)
        value = team()
        value[0]["selectedItem"] = "생명의구슬"
        await host.send({"type": "team_ready", "team": value})
        await guest.receive("opponent_team_ready")
        self.assertEqual("없음", next(iter(self.service.rooms[code]["teams"].values()))[0]["selectedItem"])

    async def test_chat_and_oversized_message_do_not_disconnect(self):
        socket = await self.connect()
        await self.create(socket)
        await socket.send({"type": "chat", "message": []})
        await socket.receive("error")
        await socket.send_raw("x" * 2_000_001)
        await socket.receive("error")
        await socket.send({"type": "chat", "message": "Still connected"})
        self.assertEqual("Still connected", (await socket.receive("chat"))["message"])


class DesktopRoomProtocolTests(CloudRoomProtocolTests):
    server_kind = "desktop"


if __name__ == "__main__":
    unittest.main()
