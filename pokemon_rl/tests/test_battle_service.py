"""Integration tests against the actual owned Node process, no HTTP mocks."""

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from battle_service.client import BattleClient, BattleServiceError


def teams():
    team = [
        {"species": "Charizard", "ability": "Blaze", "item": "Life Orb", "nature": "Timid", "moves": ["Flamethrower", "Air Slash", "Dragon Claw", "Protect"]},
        {"species": "Garchomp", "ability": "Rough Skin", "item": "Leftovers", "nature": "Jolly", "moves": ["Earthquake", "Dragon Claw", "Swords Dance", "Protect"]},
        {"species": "Clefable", "ability": "Magic Guard", "item": "Leftovers", "nature": "Bold", "moves": ["Moonblast", "Soft-Boiled", "Calm Mind", "Protect"]},
    ]
    return {"p1": copy.deepcopy(team), "p2": copy.deepcopy(team)}


def projection(state):
    state = copy.deepcopy(state)
    state.pop("battle_id")
    for view in state["views"].values():
        view.pop("battle_id")
    return state


class BattleClientTest(unittest.TestCase):
    def test_worker_restart_recovers_acknowledged_checkpoint(self):
        with BattleClient() as client:
            self.assertEqual(client.info["version"], "0.11.11")
            state = client.create(teams(), seed=[21, 22, 23, 24])
            client.choose(state["battle_id"], "p1", {"type": "move", "index": 2})
            state = client.choose(state["battle_id"], "p2", {"type": "move", "index": 2})
            checkpoint = json.loads(json.dumps(client.checkpoint(state["battle_id"])))
            resources = client.resources(state["battle_id"])
            process = client._process
        self.assertIsNotNone(process.poll())
        with BattleClient() as restarted:
            restored = restarted.restore(checkpoint)
            self.assertEqual(projection(state), projection(restored))
            next_battle = restarted.create(teams(), initial_state=resources)
            self.assertEqual(next_battle["views"]["p1"]["own"][0]["hp"], resources["p1"][0]["hp"])
            self.assertEqual(next_battle["views"]["p1"]["own"][0]["moves"][2]["pp"], resources["p1"][0]["moves"][2]["pp"])

    def test_bad_request_does_not_kill_worker_and_closed_client_fails(self):
        client = BattleClient()
        with self.assertRaises(BattleServiceError):
            client.request("eval", script="process.exit()")
        self.assertEqual(client.request("ping")["engine"], "pokemon-showdown")
        client.close()
        client.close()
        with self.assertRaises(BattleServiceError):
            client.create(teams())

    def test_checkpoint_version_mismatch_is_rejected(self):
        with BattleClient() as client:
            state = client.create(teams())
            checkpoint = client.checkpoint(state["battle_id"])
            checkpoint["engine_version"] = "unknown"
            with self.assertRaises(BattleServiceError) as caught:
                client.restore(checkpoint)
            self.assertEqual(caught.exception.code, "VERSION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
