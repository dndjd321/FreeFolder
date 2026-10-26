"""Behavioral contracts for shared-engine observations, policies and PPO."""

import copy
import pathlib
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from training.environment import ShowdownBattleEnv
from training.observation import N_ACTIONS, OBS_DIM, action_to_command, command_to_action, encode_observation, legal_mask
from training.policies import BaselinePolicy
from training.policy import PolicyAgent
from training.cli import ensure_held_out


class TrainingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_same_weights_and_same_rollout_mask_have_probability_ratio_one(self):
        agent = PolicyAgent(seed=1)
        observation = np.zeros(OBS_DIM, dtype=np.float32)
        mask = np.array([False, True, False, False, True, False, False])
        action, old_lp, _ = agent.select(observation, mask)
        lp, _, _ = agent.network.evaluate(torch.as_tensor(observation).unsqueeze(0), torch.tensor([action]), torch.as_tensor(mask).unsqueeze(0))
        self.assertAlmostEqual(torch.exp(lp - old_lp).item(), 1.0, places=6)
        for _ in range(50):
            self.assertTrue(mask[agent.select(observation, mask)[0]])

    def test_model_metadata_is_required_and_round_trip_preserves_policy(self):
        agent = PolicyAgent(seed=2)
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "policy.pt"
            agent.save(path)
            loaded = PolicyAgent.load(path)
            mask = np.ones(N_ACTIONS, dtype=np.bool_)
            observation = np.zeros(OBS_DIM, dtype=np.float32)
            self.assertEqual(agent.select(observation, mask, deterministic=True), loaded.select(observation, mask, deterministic=True))
            data = torch.load(path, weights_only=True)
            data["metadata"]["engine_version"] = "other-engine"
            torch.save(data, path)
            with self.assertRaisesRegex(ValueError, "incompatible"):
                PolicyAgent.load(path)

    def test_actual_opponent_policy_receives_its_own_view_and_is_called(self):
        class Spy(BaselinePolicy):
            seen = []
            def predict(self, request, view):
                self.seen.append(copy.deepcopy(view))
                return super().predict(request, view)
        spy = Spy("maxpower")
        environment = ShowdownBattleEnv(opponent=spy)
        try:
            observation, info = environment.reset(seed=123)
            self.assertEqual(observation.shape, (OBS_DIM,))
            self.assertTrue(environment.observation_space.contains(observation))
            self.assertEqual(spy.seen[0]["side"], "p2")
            self.assertNotEqual(spy.seen[0]["own"][0]["species"], info["view"]["own"][0]["species"])
            for _ in range(5):
                action = int(np.flatnonzero(info["action_mask"])[0])
                _, _, done, truncated, info = environment.step(action)
                if done or truncated:
                    break
            self.assertGreater(spy.calls, 1)
            self.assertTrue(all(view["side"] == "p2" for view in spy.seen))
        finally:
            environment.close()

    def test_observation_changes_when_move_power_changes_and_ignores_hidden_fields(self):
        environment = ShowdownBattleEnv()
        try:
            observation, info = environment.reset(seed=124)
            changed = copy.deepcopy(info["view"])
            changed["request"]["active"][0]["moves"][0]["basePower"] += 10
            self.assertFalse(np.array_equal(observation, encode_observation(changed)))
            hidden = copy.deepcopy(info["view"])
            hidden["opponent"][0]["item"] = "secret-item"
            hidden["opponent"][0]["moves"] = ["secret-move"]
            hidden["opponent"][0]["stats"] = {"atk": 999999}
            self.assertTrue(np.array_equal(observation, encode_observation(hidden)))
        finally:
            environment.close()

    def test_shared_engine_game_completes_with_zero_illegal_actions(self):
        environment = ShowdownBattleEnv(opponent=BaselinePolicy("maxpower"))
        player = BaselinePolicy("heuristic")
        try:
            _, info = environment.reset(seed=125)
            for _ in range(200):
                command = player.predict(info["view"]["request"], info["view"])
                action = command_to_action(command)
                self.assertTrue(info["action_mask"][action])
                _, _, done, truncated, info = environment.step(action)
                if done or truncated:
                    break
            self.assertTrue(done)
            self.assertFalse(truncated)
            self.assertEqual(info["invalid_actions"], 0)
        finally:
            environment.close()

    def test_illegal_action_does_not_silently_choose_another_move(self):
        environment = ShowdownBattleEnv()
        try:
            _, info = environment.reset(seed=126)
            active = next(p for p in info["view"]["own"] if p["active"])
            with self.assertRaisesRegex(ValueError, "Illegal"):
                environment.step(4 + active["slot"])
            self.assertEqual(environment.invalid_actions, 1)
            self.assertEqual(action_to_command(6), {"type": "switch", "index": 2})
        finally:
            environment.close()

    def test_shipped_bootstrap_model_loads_and_selects_legal_actions(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "training/models/bootstrap.pt"
        agent = PolicyAgent.load(path)
        self.assertEqual(agent.timesteps, 2048)
        ensure_held_out(agent, (1_000_000, 1_000_499))
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ensure_held_out(agent, (10_000, 10_010))
        environment = ShowdownBattleEnv(opponent=BaselinePolicy("maxpower"))
        try:
            _, info = environment.reset(seed=127)
            for _ in range(5):
                command = agent.predict(info["view"]["request"], info["view"])
                action = command_to_action(command)
                self.assertTrue(info["action_mask"][action])
                _, _, done, truncated, info = environment.step(action)
                if done or truncated:
                    break
        finally:
            environment.close()


if __name__ == "__main__":
    unittest.main()
