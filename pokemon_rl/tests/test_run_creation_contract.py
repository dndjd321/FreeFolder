"""Creation credentials survive retries without storing their raw secret."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.engine_api import canonical_team, default_opponents
from services.run_creation import CREATION_VERSION, creation_credentials, creation_request_hash


class CreationCredentialTests(unittest.TestCase):
    creation_id = '01' * 32
    creation_secret = 'ab' * 32

    def test_published_v1_vector_keeps_existing_pending_requests_recoverable(self):
        self.assertEqual(1, CREATION_VERSION)
        expected = {'run_id': 'c1_4bad1bb30be10e3b5930f49063b1a993afcb697f86f1edbc1460a59eb1c50eb4',
                    'token': '8n21nuPi2c3HP7NCuBHbWwIlnAMJNjKhn6StdXlKmj4',
                    'token_hash': '88a8c0e4fdeb28470b1fb5f69edc90454ebf0beb08fc3b0d3a542589f3184e3e'}
        self.assertEqual(expected, creation_credentials(self.creation_id, self.creation_secret))

    def test_repeated_credential_recovery_has_no_process_or_mutable_state(self):
        expected = creation_credentials(self.creation_id, self.creation_secret)
        creation_credentials('cd' * 32, 'ef' * 32)
        for _ in range(3):
            self.assertEqual(expected, creation_credentials(self.creation_id, self.creation_secret))
        self.assertEqual({'run_id', 'token', 'token_hash'}, set(expected))
        self.assertRegex(expected['run_id'], r'^c1_[0-9a-f]{64}$')
        self.assertRegex(expected['token'], r'^[A-Za-z0-9_-]{43}$')
        self.assertEqual(hashlib.sha256(expected['token'].encode('ascii')).hexdigest(), expected['token_hash'])
        self.assertNotIn(self.creation_secret, expected.values())

    def test_same_id_with_wrong_secret_cannot_recover_the_access_token(self):
        expected = creation_credentials(self.creation_id, self.creation_secret)
        wrong = creation_credentials(self.creation_id, 'cd' * 32)
        self.assertEqual(expected['run_id'], wrong['run_id'])
        self.assertNotEqual(expected['token'], wrong['token'])
        self.assertNotEqual(expected['token_hash'], wrong['token_hash'])

    def test_separate_ids_scope_tokens_even_if_a_secret_is_reused(self):
        first = creation_credentials(self.creation_id, self.creation_secret)
        second = creation_credentials('02' * 32, self.creation_secret)
        self.assertTrue(all(first[key] != second[key] for key in first))
        raw_id_hash = hashlib.sha256(self.creation_id.encode('ascii')).hexdigest()
        self.assertNotEqual('c1_' + raw_id_hash, first['run_id'])
        self.assertNotEqual(first['token_hash'], first['run_id'][3:])

    def test_only_complete_lowercase_hex_32_byte_keys_are_accepted(self):
        invalid = [None, '', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64,
                   '0x' + 'a' * 62, 'a' * 63 + '\n', ' ' * 64, '０' * 64,
                   0, False, b'a' * 64, ['a' * 64], {'secret': self.creation_secret}]
        for value in invalid:
            for position in (0, 1):
                with self.subTest(value_type=type(value).__name__, position=position):
                    args = [self.creation_id, self.creation_secret]
                    args[position] = value
                    with self.assertRaises(ValueError) as error:
                        creation_credentials(*args)
                    self.assertNotIn(self.creation_id, str(error.exception))
                    self.assertNotIn(self.creation_secret, str(error.exception))
        with self.assertRaises(ValueError):
            creation_credentials(None, None)


class CreationRequestHashTests(unittest.TestCase):
    def test_v1_request_vector_freezes_json_encoding_and_domain_separation(self):
        team = [{'species': 'Charizard', 'name': '첫 동료', 'moves': ['Flamethrower']},
                {'species': 'Venusaur', 'moves': ['Giga Drain']},
                {'species': 'Blastoise', 'moves': ['Surf']}]
        self.assertEqual('7b93cb783e080a69d82ce69960aeb75c55f0965e426c261acad42ca4617a3a14',
                         creation_request_hash(team, seed='contract-시드', difficulty='hard'))

    def test_mapping_order_and_canonical_team_defaults_do_not_change_the_hash(self):
        team = default_opponents()
        team[0]['evs'] = {'spa': 252, 'hp': 252, 'spe': 4}
        before = copy.deepcopy(team)
        expected = creation_request_hash(team)
        reordered = [{key: row[key] for key in reversed(row)} for row in copy.deepcopy(team)]
        reordered[0]['evs'] = {'spe': 4, 'hp': 252, 'spa': 252}
        self.assertEqual(expected, creation_request_hash(reordered, difficulty='normal'))
        self.assertEqual(expected, creation_request_hash(canonical_team(team)))
        self.assertEqual(before, team)
        self.assertRegex(expected, r'^[0-9a-f]{64}$')

    def test_all_gameplay_creation_choices_are_bound_to_the_request(self):
        team = default_opponents()
        expected = creation_request_hash(team, seed='fixed', difficulty='normal')
        alternatives = []
        for field, value in [('item', ''), ('nature', 'Bold'), ('ability', ''),
                             ('evs', {'hp': 252}), ('ivs', {'atk': 0}), ('name', '다른 이름')]:
            changed = copy.deepcopy(team)
            changed[0][field] = value
            alternatives.append(changed)
        moved = copy.deepcopy(team)
        moved[0]['moves'].reverse()
        alternatives.extend([moved, list(reversed(team))])
        for changed in alternatives:
            with self.subTest(changed=changed[0]['species']):
                self.assertNotEqual(expected, creation_request_hash(changed, seed='fixed'))
        self.assertNotEqual(expected, creation_request_hash(team, seed='other'))
        self.assertNotEqual(expected, creation_request_hash(team, seed='fixed', difficulty='hard'))

    def test_automatic_seed_sentinel_is_stable_and_distinct_from_explicit_seeds(self):
        team = default_opponents()
        automatic = creation_request_hash(team)
        self.assertEqual(automatic, creation_request_hash(team, seed=None))
        for seed in ('', 'None', 'auto', 0):
            with self.subTest(seed=seed):
                self.assertNotEqual(automatic, creation_request_hash(team, seed=seed))
        self.assertEqual(creation_request_hash(team, seed=123), creation_request_hash(team, seed='123'))

    def test_invalid_difficulty_and_seed_values_are_rejected(self):
        team = default_opponents()
        for difficulty in (None, '', 'Normal', 'unknown', True, 1, {}):
            with self.subTest(difficulty=difficulty), self.assertRaises(ValueError):
                creation_request_hash(team, difficulty=difficulty)
        for seed in (True, False, 1.0, float('nan'), [], {}, 's' * 129, int('9' * 129)):
            with self.subTest(seed_type=type(seed).__name__), self.assertRaises(ValueError):
                creation_request_hash(team, seed=seed)
        creation_request_hash(team, seed='s' * 128)

    def test_invalid_teams_use_the_existing_canonical_validation(self):
        for team in (None, [], default_opponents()[:2], [{'species': 'Venusaur'}] * 3):
            with self.subTest(team=team), self.assertRaises(ValueError):
                creation_request_hash(team)
        injected = default_opponents()
        injected[0]['hp'] = 999
        with self.assertRaises(ValueError):
            creation_request_hash(injected)


if __name__ == '__main__':
    unittest.main()
