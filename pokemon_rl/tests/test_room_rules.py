"""Room restrictions use canonical engine sets, never spoofable outer UI IDs."""

import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, WebSocket
from services.engine_api import EngineSessions, default_opponents
from services.engine_multiplayer import AuthoritativeMultiplayerService, LEGENDARY_DEX_NUMBERS
from test_multiplayer import ASGISocket, team


class CanonicalRoomRuleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = EngineSessions()
        self.service = AuthoritativeMultiplayerService(self.engine)
        self.app = FastAPI()
        service = self.service
        @self.app.websocket('/ws/multi')
        async def route(ws: WebSocket):
            await service.websocket(ws)
        self.sockets, self.created = [], []
        client = self.engine.get_client()
        create = client.create
        def tracked_create(*args, **kwargs):
            result = create(*args, **kwargs)
            self.created.append(result['battle_id'])
            return result
        client.create = tracked_create

    async def asyncTearDown(self):
        for socket in reversed(self.sockets):
            await socket.close()
        await self.service.shutdown()
        self.engine.close()

    async def connect(self):
        socket = await ASGISocket(self.app).connect()
        self.sockets.append(socket)
        await socket.receive('session')
        return socket

    async def submit(self, builds, rules):
        host, guest = await self.connect(), await self.connect()
        await host.send({'type': 'create_room', 'name': 'Canonical rules', 'rules': {'turnTime': 0, **rules}})
        code = (await host.receive('room_created'))['code']
        await guest.send({'type': 'join_room', 'code': code})
        await guest.receive('room_joined')
        packet = team()
        for mon, engine_set in zip(packet, builds):
            # Outer IDs remain 1,2,3 and all selectedItem values are '없음'.
            mon['engineSet'] = copy.deepcopy(engine_set)
        await host.send({'type': 'team_ready', 'team': packet})
        await guest.send({'type': 'team_ready', 'team': copy.deepcopy(packet)})
        return host, guest, code

    async def assert_rejected_without_engine_leak(self, builds, rules, expected):
        host, guest, code = await self.submit(builds, rules)
        self.assertIn(expected, (await host.receive('error'))['message'])
        await guest.receive('error')
        self.assertEqual('team_select', self.service.rooms[code]['state'])
        self.assertNotIn('engine_battle_id', self.service.rooms[code])
        self.assertEqual({}, self.service.rooms[code]['teams'])
        self.assertTrue(self.created)
        for bid in self.created:
            with self.assertRaises(RuntimeError):
                self.engine.get_client().view(bid, 'p1')

    async def test_no_items_strips_hidden_engine_items_before_battle(self):
        builds = default_opponents()
        self.assertTrue(all(mon['item'] for mon in builds))
        host, guest, _ = await self.submit(builds, {'noItems': True, 'noDupItem': True})
        h, _ = await host.receive('engine_state'), await guest.receive('engine_state')
        self.assertEqual([3, 9, 59], [p['num'] for p in h['view']['own']])
        self.assertTrue(all(not p['item'] for p in h['view']['own']))
        canonical = self.engine.get_client().checkpoint(h['battle_id'])['teams']
        self.assertTrue(all(not p['item'] for side in canonical.values() for p in side))

    async def test_fake_distinct_outer_ids_cannot_hide_duplicate_species(self):
        venusaur = default_opponents()[0]
        await self.assert_rejected_without_engine_leak([copy.deepcopy(venusaur) for _ in range(3)], {'noDupPokemon': True}, '같은 종')

    async def test_regional_forms_count_as_the_same_species(self):
        builds = [
            {'species': 'Raichu', 'ability': 'Static', 'moves': ['Thunderbolt']},
            {'species': 'Raichu-Alola', 'ability': 'Surge Surfer', 'moves': ['Thunderbolt']},
            default_opponents()[2],
        ]
        await self.assert_rejected_without_engine_leak(builds, {'noDupPokemon': True}, '같은 종')

    async def test_fake_empty_outer_items_cannot_hide_duplicate_engine_items(self):
        builds = default_opponents()
        for mon, item in zip(builds, ['Leftovers', 'leftovers', 'Left-overs']):
            mon['item'] = item
        await self.assert_rejected_without_engine_leak(builds, {'noDupItem': True}, '같은 도구')

    async def test_switch_in_item_consumption_cannot_hide_a_duplicate_starting_item(self):
        builds = [
            {'species': 'Tapu Koko', 'ability': 'Electric Surge', 'item': 'Electric Seed', 'moves': ['Thunderbolt']},
            {'species': 'Hawlucha', 'ability': 'Unburden', 'item': 'Electric Seed', 'moves': ['Acrobatics']},
            default_opponents()[1],
        ]
        probe = self.engine.get_client().create({'p1': builds, 'p2': default_opponents()})
        self.assertEqual('', probe['views']['p1']['own'][0]['item'])
        self.engine.get_client().close_battle(probe['battle_id'])
        await self.assert_rejected_without_engine_leak(builds, {'noDupItem': True}, '같은 도구')

    async def test_fake_normal_outer_ids_cannot_hide_mythical_species(self):
        builds = default_opponents()
        builds[0] = {'species': 'Mew', 'ability': 'Synchronize', 'moves': ['Psychic']}
        await self.assert_rejected_without_engine_leak(builds, {'legendary': False}, '전설·환상')

    async def test_mythical_species_missing_from_old_ui_list_are_still_rejected(self):
        builds = default_opponents()
        builds[0] = {'species': 'Zarude', 'ability': 'Leaf Guard', 'moves': ['Power Whip']}
        await self.assert_rejected_without_engine_leak(builds, {'legendary': False}, '전설·환상')

    async def test_valid_canonical_species_and_items_are_not_rejected(self):
        host, guest, _ = await self.submit(default_opponents(), {'noDupPokemon': True, 'noDupItem': True, 'legendary': False})
        h, _ = await host.receive('engine_state'), await guest.receive('engine_state')
        self.assertEqual([3, 9, 59], [p['num'] for p in h['view']['own']])
        self.assertEqual(['leftovers', 'sitrusberry', 'heavydutyboots'], [p['item'] for p in h['view']['own']])

    async def test_legendary_numbers_match_the_pinned_engine_tags(self):
        script = "const {Dex}=require('pokemon-showdown'); const tags=new Set(['Restricted Legendary','Sub-Legendary','Mythical']);console.log(JSON.stringify([...new Set(Dex.species.all().filter(p=>p.num>0&&(p.tags||[]).some(t=>tags.has(t))).map(p=>p.num))]));"
        root = Path(__file__).resolve().parents[1] / 'battle_service'
        result = await asyncio.to_thread(subprocess.run, [self.engine.get_client().node, '-e', script], cwd=root,
                                         capture_output=True, text=True, encoding='utf-8', check=True,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(set(json.loads(result.stdout)), set(LEGENDARY_DEX_NUMBERS))


if __name__ == '__main__':
    unittest.main()
