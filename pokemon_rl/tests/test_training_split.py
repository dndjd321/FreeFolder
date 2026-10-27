"""The benchmark must hold out builds and unordered team compositions."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training.dataset import TeamDataset, build_fingerprint, composition_split, ensure_disjoint_training_data, team_fingerprint
from training.environment import ShowdownBattleEnv
from training.observation import OBS_DIM, N_ACTIONS, command_to_action, legal_mask
from training.policies import BaselinePolicy
from training.policy import PolicyAgent
from training.cli import run_evaluation, train, evaluate, validation_score


class DatasetTests(unittest.TestCase):
    def test_all_three_partitions_share_species_but_never_builds_or_compositions(self):
        datasets = [TeamDataset(name) for name in ('train', 'validation', 'test')]
        self.assertEqual([30] * 3, [len(d.species) for d in datasets])
        self.assertEqual(4060, sum(len(d.compositions) for d in datasets))
        for left in range(3):
            for right in range(left + 1, 3):
                self.assertEqual(datasets[left].species, datasets[right].species)
                self.assertFalse(set(datasets[left].compositions) & set(datasets[right].compositions))
                self.assertFalse(set(datasets[left].manifest()['build_ids']) & set(datasets[right].manifest()['build_ids']))
        for dataset in datasets:
            for combo in dataset.compositions:
                self.assertEqual(dataset.split, composition_split(combo))

    def test_reset_seed_reproduces_teams_and_permutations_do_not_fake_novelty(self):
        dataset = TeamDataset('test')
        first = dataset.teams(2_000_000)
        self.assertEqual(first, dataset.teams(2_000_000))
        for team in first.values():
            self.assertEqual('test', composition_split([p['species'] for p in team]))
            self.assertEqual(team_fingerprint(team), team_fingerprint(team[::-1]))
            moved = copy.deepcopy(team[0]); moved['moves'].reverse()
            self.assertEqual(build_fingerprint(team[0]), build_fingerprint(moved))

    def test_builds_match_known_move_roles_and_keep_pikachus_held_item(self):
        for split in ('train', 'validation', 'test'):
            dataset = TeamDataset(split)
            for species in ('Mimikyu', 'Lucario'):
                self.assertEqual(252, dataset.builds[species]['evs']['atk'])
                self.assertNotIn('spa', dataset.builds[species]['evs'])
            for species in ('Kingdra', 'Lapras', 'Pikachu'):
                self.assertEqual(252, dataset.builds[species]['evs']['spa'])
                self.assertNotIn('atk', dataset.builds[species]['evs'])
            self.assertEqual('Light Ball', dataset.builds['Pikachu']['item'])

    def test_checkpoint_data_provenance_survives_round_trip_and_rejects_overlap(self):
        torch.set_num_threads(1)
        agent = PolicyAgent(seed=4)
        agent.training_data_contracts = [TeamDataset('train').manifest()]
        agent.imitation_samples = 123
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.pt'; agent.save(path)
            loaded = PolicyAgent.load(path)
        self.assertEqual(agent.training_data_contracts, loaded.training_data_contracts)
        self.assertEqual(123, loaded.imitation_samples)
        ensure_disjoint_training_data(loaded, TeamDataset('validation'))
        ensure_disjoint_training_data(loaded, TeamDataset('test'))
        with self.assertRaisesRegex(ValueError, 'held-out'):
            ensure_disjoint_training_data(loaded, TeamDataset('train'))
        loaded.training_data_contracts = [TeamDataset('test').manifest()]
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            ensure_disjoint_training_data(loaded, TeamDataset('test'))
        loaded.training_data_contracts = [TeamDataset('train').manifest()]
        loaded.training_data_contracts[0]['composition_sha256'] = 'wrong-partition'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            ensure_disjoint_training_data(loaded, TeamDataset('validation'))

    def test_all_ninety_partition_builds_are_validated_by_the_actual_engine(self):
        from battle_service.client import BattleClient
        with BattleClient() as client:
            for split in ('train', 'validation', 'test'):
                builds = list(TeamDataset(split).builds.values())
                for start in range(0, 30, 3):
                    team = builds[start:start + 3]
                    result = client.create({'p1': team, 'p2': copy.deepcopy(team)}, seed=[1, 2, 3, 4])
                    self.assertEqual(1, result['turn'])
                    client.close_battle(result['battle_id'])

    def test_legacy_trained_weights_cannot_silently_claim_new_data_holdouts_on_resume(self):
        bootstrap = Path(__file__).resolve().parents[1] / 'training/models/bootstrap.pt'
        with self.assertRaisesRegex(ValueError, 'legacy trained checkpoint'):
            train(SimpleNamespace(dataset='roguelike30', resume=str(bootstrap), seed=0))

    def test_new_contract_cannot_keep_a_false_holdout_claim_after_legacy_pool_training(self):
        agent = PolicyAgent(seed=5)
        agent.training_data_contracts = [TeamDataset('train').manifest()]
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / 'new-contract.pt'; agent.save(model)
            with self.assertRaisesRegex(ValueError, 'legacy shared pool'):
                train(SimpleNamespace(dataset='legacy6', resume=str(model), seed=0))

    def test_released_candidate_matches_its_report_and_selects_legal_test_actions(self):
        root = Path(__file__).resolve().parents[1]
        model = root / 'training/models/expedition-v2.pt'
        report = json.loads((root / 'training/benchmarks/evaluation-candidate-20261027.json').read_text(encoding='utf-8'))
        self.assertEqual(report['model_sha256'], hashlib.sha256(model.read_bytes()).hexdigest())
        agent = PolicyAgent.load(model)
        self.assertEqual(report['model_timesteps'], agent.timesteps)
        self.assertEqual(8192, agent.imitation_samples)
        ensure_disjoint_training_data(agent, TeamDataset('test'))
        environment = ShowdownBattleEnv(dataset=TeamDataset('test'), opponent=BaselinePolicy('maxpower'))
        try:
            _, info = environment.reset(seed=2_000_128)
            for _ in range(3):
                command = agent.predict(info['view']['request'], info['view'])
                action = command_to_action(command)
                self.assertTrue(info['action_mask'][action])
                _, _, ended, truncated, info = environment.step(action)
                if ended or truncated:
                    break
        finally:
            environment.close()


def immune_view():
    own = [
        {'slot': 0, 'active': True, 'hp': 100, 'maxhp': 100, 'types': ['electric'], 'moves': [], 'stats': {'spa': 100}},
        {'slot': 1, 'active': False, 'hp': 100, 'maxhp': 100, 'types': ['water'],
         'moves': [{'id': 'surf', 'type': 'water', 'category': 'special', 'basePower': 90, 'accuracy': 100, 'pp': 10}], 'stats': {'spa': 100}},
        {'slot': 2, 'active': False, 'hp': 100, 'maxhp': 100, 'types': ['normal'],
         'moves': [{'id': 'bodyslam', 'type': 'normal', 'category': 'physical', 'basePower': 85, 'accuracy': 100, 'pp': 10}], 'stats': {'atk': 100}},
    ]
    return {'side': 'p1', 'own': own, 'opponent': [{'active': True, 'revealed': True, 'types': ['ground'], 'hp': 100, 'maxhp': 100}],
            'request': {'active': [{'moves': [{'id': 'thunderbolt', 'type': 'electric', 'category': 'special', 'basePower': 90, 'accuracy': 100, 'pp': 10}]}]}}


class BaselineAndWarmstartTests(unittest.TestCase):
    def test_paired_mirror_swaps_policy_seats_while_keeping_teams_and_engine_seed_fixed(self):
        from battle_service.client import BattleClient
        from training.confirmatory import paired_comparison
        class Spy(BaselinePolicy):
            def __init__(self):
                super().__init__('heuristic'); self.sides = set()
            def predict(self, request, view):
                self.sides.add(view['side'])
                return super().predict(request, view)
        left, right = Spy(), Spy()
        with BattleClient() as client:
            result = paired_comparison(client, left, right, dataset=TeamDataset('test'), pairs=2, seed_start=3_000_123)
        self.assertEqual({'p1', 'p2'}, left.sides)
        self.assertEqual({'p1', 'p2'}, right.sides)
        self.assertEqual(0, result['invalid_actions'])
        for pair in result['pairs']:
            self.assertEqual(['p1', 'p2'], [g['policy_a_side'] for g in pair['games']])
            self.assertEqual(pair['games'][0]['winner'], pair['games'][1]['winner'])
            self.assertEqual(1, sum(g['policy_a_win'] for g in pair['games']))

    def test_validation_does_not_reward_a_policy_for_reaching_the_turn_limit(self):
        stall = {'games': 64, 'wins': 0, 'draws': 64, 'truncations': 64}
        modest_winner = {'games': 64, 'wins': 10, 'draws': 0, 'truncations': 0}
        natural_draw = {'games': 64, 'wins': 0, 'draws': 64, 'truncations': 0}
        self.assertEqual(0, validation_score(stall))
        self.assertGreater(validation_score(modest_winner), validation_score(stall))
        self.assertEqual(.5, validation_score(natural_draw))

    def test_immunity_uses_a_legal_switch_while_legacy_repeats_immune_attack(self):
        view = immune_view()
        self.assertEqual({'type': 'move', 'index': 0}, BaselinePolicy('legacyheuristic').predict(view['request'], view))
        command = BaselinePolicy('heuristic').predict(view['request'], view)
        self.assertEqual({'type': 'switch', 'index': 1}, command)
        self.assertTrue(legal_mask(view['request'], view)[command_to_action(command)])

    def test_trapping_keeps_the_immunity_policy_from_proposing_an_illegal_switch(self):
        view = immune_view(); view['request']['active'][0]['trapped'] = True
        self.assertEqual({'type': 'move', 'index': 0}, BaselinePolicy().predict(view['request'], view))

    def test_variable_power_attack_is_not_confused_with_an_immune_or_status_move(self):
        view = immune_view()
        view['request']['active'][0]['moves'].append({'id': 'grassknot', 'type': 'grass', 'category': 'special',
                                                    'basePower': 0, 'accuracy': 100, 'pp': 10})
        self.assertEqual({'type': 'move', 'index': 1}, BaselinePolicy().predict(view['request'], view))

    def test_hidden_opponent_build_fields_never_change_baseline_choice(self):
        view = immune_view()
        expected = BaselinePolicy().predict(view['request'], view)
        changed = copy.deepcopy(view)
        changed['opponent'][0].update(item='Choice Scarf', ability='SecretAbility', stats={'spa': 999999}, moves=[{'type': 'grass', 'basePower': 99999}])
        self.assertEqual(expected, BaselinePolicy().predict(changed['request'], changed))

    def test_forced_switch_scores_matchup_using_stable_original_slot(self):
        view = immune_view()
        view['own'][0].update(hp=0, fainted=True)
        view['own'] = view['own'][::-1]
        view['request'] = {'forceSwitch': [True]}
        self.assertEqual({'type': 'switch', 'index': 1}, BaselinePolicy().predict(view['request'], view))

    def test_imitation_records_masks_and_samples_separately_from_ppo(self):
        torch.set_num_threads(1)
        agent = PolicyAgent(seed=8)
        observation = np.zeros(OBS_DIM, np.float32)
        mask = np.array([False, True, False, False, True, False, False])
        demonstrations = [{'observation': observation, 'mask': mask, 'action': 1} for _ in range(4)]
        result = agent.imitate(demonstrations, epochs=2, batch_size=2)
        self.assertEqual(4, result['samples'])
        self.assertEqual(4, agent.imitation_samples)
        self.assertEqual(0, agent.timesteps)
        for _ in range(20):
            self.assertTrue(mask[agent.select(observation, mask)[0]])
        demonstrations[0]['action'] = 0
        with self.assertRaisesRegex(ValueError, 'not legal'):
            agent.imitate(demonstrations)

    def test_thirty_species_held_out_game_uses_the_same_engine_with_zero_invalid_actions(self):
        result = run_evaluation(BaselinePolicy('heuristic'), BaselinePolicy('maxpower'),
                                dataset=TeamDataset('validation'), seed_start=1_000_127, games=2)
        self.assertEqual(2, result['games'])
        self.assertEqual(0, result['invalid_actions'])
        self.assertEqual(2, result['wins'] + result['losses'] + result['draws'])


if __name__ == '__main__':
    unittest.main()
