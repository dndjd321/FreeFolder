"""Gameplay effects and bounded public history share each durable save."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

GAME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GAME_ROOT))

from services.engine_api import choose_legal_action, default_opponents
from services.run_journal import MAX_EVENTS, snapshot
from services.run_service import RunService, SaveStorageError
from services.save_tools import backup_save, restore_save
from test_run_service import FakeBattleClient


class RunJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'journal.sqlite3'
        self.client = FakeBattleClient()
        self.service = RunService(self.path, client_factory=lambda: self.client)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def create(self, **kwargs):
        return self.service.create(default_opponents(), seed='journal', **kwargs)

    def state(self, run):
        return self.service._load(run['run_id'], run['token'])

    def row(self, run):
        return self.service.db.execute('SELECT state FROM runs WHERE id=?', (run['run_id'],)).fetchone()[0]

    def prepare(self, index=0, *, injured=False, relics=None):
        run = self.create()
        state = self.state(run)
        state.update(node_index=index, completed_nodes=index, node=self.service._node(state, index),
                     relics=copy.deepcopy(relics or {}), money=200)
        if injured:
            for slot, resource in enumerate(state['resources']):
                resource.update(hp=[35, 0, 98][slot], status=['brn', 'slp', ''][slot],
                                sleepTurns=2 if slot == 1 else None)
                for move in resource['moves']:
                    move['pp'] = 4
        self.service._save(state)
        return self.service.get(run['run_id'], run['token'])

    def finish(self, run, command='finish'):
        if run['phase'] == 'node':
            run = self.service.battle_start(run['run_id'], run['token'])
        return self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0},
                                           run['battle']['revision'], command)

    def test_created_event_is_committed_with_the_new_run_and_not_initial_income(self):
        self.service.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(SaveStorageError):
            self.create()
        self.assertEqual(0, self.service.db.execute('SELECT count(*) FROM runs').fetchone()[0])
        self.service.db.execute('PRAGMA query_only=OFF')
        run = self.create(difficulty='hard')
        journal = run['journal']
        self.assertTrue(journal['available'])
        self.assertTrue(journal['complete_history'])
        self.assertEqual((1, 1, 0), (journal['total_events'], journal['counts']['created'], journal['from_node']))
        self.assertEqual({'difficulty': 'hard'}, journal['events'][0]['details'])
        self.assertEqual(journal['events'][0]['before'], journal['events'][0]['after'])
        self.assertTrue(all(value == 0 for value in journal['totals'].values()))
        self.assertEqual(1, json.loads(self.row(run))['journal']['total_events'])

    def test_battle_end_measures_entry_resources_and_only_actual_post_battle_bonus(self):
        run = self.prepare(injured=True, relics={'forager': 3})
        before = snapshot(self.state(run))
        run = self.finish(run)
        event = run['journal']['events'][-1]
        self.assertEqual('battle_end', event['kind'])
        self.assertEqual(before, event['before'])
        # One attack loses 7 HP; the bonus restores 6 to slot 0 and caps
        # slot 2 at +2. A fainted member does not receive the bonus.
        self.assertEqual({'winner': 'p1', 'turns': 2, 'post_battle_heal': 8}, event['details'])
        self.assertEqual(before['hp'] + 1, event['after']['hp'])
        self.assertEqual(before['pp'] - 1, event['after']['pp'])
        self.assertEqual(8, run['journal']['totals']['hp_recovered'])
        self.assertEqual(20, run['journal']['totals']['money_earned'])
        self.assertEqual((1, 1, 1), tuple(run['journal']['totals'][key] for key in ('battles_started', 'battles_finished', 'wins')))
        self.assertEqual(snapshot(self.state(run)), event['after'])

    def test_loss_records_the_actual_result_without_victory_income_or_healing(self):
        run = self.prepare(injured=True, relics={'forager': 3})
        self.client.next_winner = 'p2'
        run = self.finish(run)
        self.assertEqual('failed', run['phase'])
        event = run['journal']['events'][-1]
        self.assertEqual({'winner': 'p2', 'turns': 2, 'post_battle_heal': 0}, event['details'])
        self.assertEqual(0, event['after']['hp'])
        self.assertEqual((1, 0, 0), tuple(run['journal']['totals'][key] for key in ('losses', 'money_earned', 'hp_recovered')))

    def test_recovery_event_totals_match_actual_previews_and_paid_prices(self):
        relics = {'camp-kit': 1, 'ether-case': 1, 'clean-blanket': 1, 'revive-charm': 1, 'bargain-card': 1}
        for option in ('heal', 'pp', 'cure', 'revive'):
            with self.subTest(rest=option):
                run = self.prepare(2, injured=True, relics=relics)
                preview = next(row['preview'] for row in run['rest_options'] if row['id'] == option)
                run = self.service.rest(run['run_id'], run['token'], option)
                journal = run['journal']
                self.assertEqual({'option': option}, journal['events'][-1]['details'])
                for total, expected in (('hp_recovered', 'hp_gain'), ('pp_recovered', 'pp_gain'), ('revived', 'revived'), ('cured', 'cured')):
                    self.assertEqual(preview[expected], journal['totals'][total])
        for kind in ('heal', 'pp', 'revive'):
            with self.subTest(shop=kind):
                run = self.prepare(6, injured=True, relics=relics)
                offer = next(row for row in run['shop'] if row['kind'] == kind)
                run = self.service.shop(run['run_id'], run['token'], offer['id'])
                event = run['journal']['events'][-1]
                self.assertEqual({'offer_id': offer['id'], 'offer_kind': kind, 'price': offer['price'],
                                  'label': offer['label'], 'action': 'buy'}, event['details'])
                self.assertEqual(offer['price'], run['journal']['totals']['money_spent'])
                self.assertEqual(offer['preview']['hp_gain'], run['journal']['totals']['hp_recovered'])
                self.assertEqual(offer['preview']['pp_gain'], run['journal']['totals']['pp_recovered'])
                run = self.service.shop(run['run_id'], run['token'], 'leave')
                self.assertEqual({'offer_id': 'leave', 'offer_kind': 'leave', 'price': 0,
                                  'label': '상점 떠나기', 'action': 'leave'}, run['journal']['events'][-1]['details'])
                self.assertEqual(offer['price'], run['journal']['totals']['money_spent'])

    def test_rewards_rerolls_and_chosen_boss_path_store_only_the_offered_choices(self):
        for kind in ('heal', 'pp', 'money', 'relic', 'recruit'):
            with self.subTest(reward=kind):
                run = self.prepare(injured=True)
                state = self.state(run)
                reward = {'id': 'reward-offered', 'kind': kind, 'label': '선택한 보상', 'amount': 5 if kind == 'pp' else .35}
                if kind == 'money':
                    reward['amount'] = 35
                if kind == 'relic':
                    reward['relic_id'] = 'field-medic'
                state.update(phase='reward', rewards=[reward], rerolls_left=1)
                self.service._save(state)
                run = self.service.choose_reward(run['run_id'], run['token'], reward['id'])
                details = {'reward_id': reward['id'], 'reward_kind': kind, 'label': reward['label']}
                if kind == 'relic':
                    details['relic_id'] = reward['relic_id']
                self.assertEqual(details, run['journal']['events'][-1]['details'])
                self.assertEqual(1, run['journal']['counts']['reward'])
                if kind == 'money':
                    self.assertEqual(35, run['journal']['totals']['money_earned'])
        run = self.finish(self.create())
        run = self.service.reroll_rewards(run['run_id'], run['token'])
        self.assertEqual({'rerolls_left': 0, 'cost': 0}, run['journal']['events'][-1]['details'])
        before = self.row(run)
        with self.assertRaises(ValueError):
            self.service.reroll_rewards(run['run_id'], run['token'])
        self.assertEqual(before, self.row(run))
        state = self.state(run)
        state.update(node_index=3, node=self.service._node(state, 3), phase='path')
        state['paths'] = [self.service._node(state, 4)]
        self.service._save(state)
        run = self.service.choose_path(run['run_id'], run['token'], state['paths'][0]['id'])
        event = run['journal']['events'][-1]
        self.assertEqual((3, 4), (event['before']['node_index'], event['after']['node_index']))
        self.assertEqual({'path_id': 'node-4-0', 'node_kind': 'boss', 'theme': 'rain',
                          'label': self.service.content['bosses'][0]['name']}, event['details'])

    def test_recruit_and_order_do_not_misclassify_replacement_or_reordering_as_recovery(self):
        run = self.prepare(injured=True)
        state = self.state(run)
        self.service._offer_recruits(state, 'reward')
        self.service._save(state)
        candidate = copy.deepcopy(state['recruit_candidates'][0])
        replaced = state['team'][1]['species']
        run = self.service.recruit(run['run_id'], run['token'], candidate['id'], 1)
        self.assertEqual({'candidate_id': candidate['id'], 'species': candidate['team']['species'],
                          'replace_index': 1, 'replaced_species': replaced, 'skipped': False}, run['journal']['events'][-1]['details'])
        self.assertGreater(run['journal']['events'][-1]['after']['hp'], run['journal']['events'][-1]['before']['hp'])
        self.assertEqual((1, 0, 0), tuple(run['journal']['totals'][key] for key in ('recruits', 'hp_recovered', 'pp_recovered')))
        run = self.service.choose_path(run['run_id'], run['token'], run['paths'][0]['id'])
        run = self.service.party_order(run['run_id'], run['token'], [2, 0, 1])
        self.assertEqual({'order': [2, 0, 1], 'lead_species': run['party'][0]['species']}, run['journal']['events'][-1]['details'])
        self.assertEqual((0, 0), tuple(run['journal']['totals'][key] for key in ('hp_recovered', 'pp_recovered')))
        state = self.state(run)
        self.service._offer_recruits(state, 'reward')
        self.service._save(state)
        run = self.service.recruit(run['run_id'], run['token'], 'skip')
        self.assertEqual({'candidate_id': 'skip', 'skipped': True}, run['journal']['events'][-1]['details'])
        self.assertEqual(1, run['journal']['totals']['recruits'])

    def test_read_resume_creation_retry_cache_eviction_and_individual_moves_add_no_events(self):
        self.client.turns_to_win = 4
        credentials = {'creation_id': '01' * 32, 'creation_secret': 'ab' * 32}
        run = self.create(**credentials)
        run = self.service.battle_start(run['run_id'], run['token'])
        journal = copy.deepcopy(run['journal'])
        run = self.finish(run, 'one-move')
        self.assertEqual(journal, run['journal'])
        self.assertEqual(journal, self.finish({**run, 'battle': {**run['battle'], 'revision': 0}}, 'one-move')['journal'])
        self.assertEqual(journal, self.service.resume(run['run_id'], run['token'])['journal'])
        self.assertEqual(journal, self.create(**credentials)['journal'])
        self.service.cache_capacity = 1
        other = self.create()
        self.service.battle_start(other['run_id'], other['token'])
        self.assertNotIn(run['run_id'], self.service.live)
        self.assertEqual(journal, self.create(**credentials)['journal'])
        state = self.state(run)
        self.service._save(state)
        self.assertEqual(journal, self.service.get(run['run_id'], run['token'])['journal'])

    def test_failed_recovery_and_battle_start_writes_roll_back_effects_and_events_together(self):
        run = self.prepare(2, injured=True)
        before = self.row(run)
        self.service.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(SaveStorageError):
            self.service.rest(run['run_id'], run['token'], 'heal')
        self.assertEqual(before, self.row(run))
        self.service.db.execute('PRAGMA query_only=OFF')
        run = self.service.rest(run['run_id'], run['token'], 'heal')
        self.assertEqual(1, run['journal']['counts']['rest'])
        self.assertEqual(52, run['journal']['totals']['hp_recovered'])
        run = self.create()
        before = self.row(run)
        self.service.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(SaveStorageError):
            self.service.battle_start(run['run_id'], run['token'])
        self.assertEqual(before, self.row(run))
        self.service.db.execute('PRAGMA query_only=OFF')
        run = self.service.battle_start(run['run_id'], run['token'])
        self.assertEqual(1, run['journal']['counts']['battle_start'])

    def test_failed_end_settlement_is_recovered_once_with_the_same_rewards_and_journal(self):
        run = self.create()
        run = self.service.battle_start(run['run_id'], run['token'])
        save = self.service._save
        def fail_settlement(state):
            if state['phase'] == 'reward':
                self.service.db.execute('PRAGMA query_only=ON')
            return save(state)
        self.service._save = fail_settlement
        try:
            with self.assertRaises(SaveStorageError):
                self.finish(run, 'ended')
        finally:
            self.service._save = save
            self.service.db.execute('PRAGMA query_only=OFF')
        stored = json.loads(self.row(run))
        self.assertEqual(('battle', 60, 0), (stored['phase'], stored['money'], stored['journal']['counts']['battle_end']))
        self.assertTrue(stored['battle']['checkpoint']['data']['ended'])
        recovered = self.service.resume(run['run_id'], run['token'])
        self.assertEqual(('reward', 80, 1), (recovered['phase'], recovered['money'], recovered['journal']['counts']['battle_end']))
        self.assertEqual(recovered, self.service.resume(run['run_id'], run['token']))
        self.assertEqual(recovered, self.service.battle_action(run['run_id'], run['token'], {'type': 'move', 'index': 0}, 0, 'ended'))

    def test_legacy_history_starts_at_the_first_real_action_and_preserves_unavailable_formats(self):
        run = self.prepare(8, injured=True)
        state = self.state(run)
        state.pop('journal')
        self.service._save(state)
        self.assertFalse(self.service.get(run['run_id'], run['token'])['journal']['available'])
        self.assertFalse(self.service.resume(run['run_id'], run['token'])['journal']['available'])
        run = self.service.rest(run['run_id'], run['token'], 'pp')
        self.assertEqual((True, False, 8, 1, 0), tuple(run['journal'][key] for key in ('available', 'complete_history', 'from_node', 'total_events')) + (run['journal']['counts']['created'],))
        for stored in ({'version': 2, 'private': 'PRIVATE'}, {'version': 1, 'malformed': 'PRIVATE'}):
            run = self.create()
            state = self.state(run)
            state['journal'] = copy.deepcopy(stored)
            self.service._save(state)
            run = self.service.party_order(run['run_id'], run['token'], [1, 0, 2])
            self.assertFalse(run['journal']['available'])
            self.assertNotIn('PRIVATE', json.dumps(run['journal']))
            self.assertEqual(stored, self.state(run)['journal'])

    def test_public_journal_is_detached_and_never_contains_private_builds_or_credentials(self):
        run = self.finish(self.create())
        encoded = json.dumps(run['journal'])
        for field in ('checkpoint', 'token', 'token_hash', 'creation_secret', 'moves', 'ability', 'item', 'evs', 'enemy_team'):
            self.assertNotIn('"' + field + '"', encoded)
        self.assertNotIn(run['token'], encoded)
        run['journal']['events'][0]['after']['party'][0]['hp'] = 0
        current = self.service.get(run['run_id'], run['token'])
        self.assertEqual(100, current['journal']['events'][0]['after']['party'][0]['hp'])

    def test_journal_bound_keeps_cumulative_counts_after_old_events_are_dropped(self):
        run = self.create()
        for _ in range(MAX_EVENTS + 2):
            run = self.service.party_order(run['run_id'], run['token'], [0, 1, 2])
        journal = run['journal']
        self.assertEqual(MAX_EVENTS, len(journal['events']))
        self.assertEqual((MAX_EVENTS + 3, 3, MAX_EVENTS + 2),
                         (journal['total_events'], journal['dropped_events'], journal['counts']['party_order']))
        self.assertTrue(journal['complete_history'])
        self.assertTrue(all(value == 0 for value in journal['totals'].values()))

    def test_full_twenty_node_run_has_complete_battle_and_choice_counts(self):
        run = self.create()
        decisions = 0
        while run['phase'] not in ('complete', 'failed'):
            decisions += 1
            self.assertLess(decisions, 120)
            rid, token = run['run_id'], run['token']
            if run['phase'] == 'node':
                kind = run['node']['kind']
                run = (self.service.rest(rid, token, 'heal') if kind == 'rest' else
                       self.service.shop(rid, token, 'leave') if kind == 'shop' else
                       self.service.battle_start(rid, token))
            elif run['phase'] == 'battle':
                run = self.finish(run, 'full-' + str(decisions))
            elif run['phase'] == 'reward':
                choice = next((r for r in run['rewards'] if r['kind'] == 'money'),
                              next(r for r in run['rewards'] if r['kind'] != 'recruit'))
                run = self.service.choose_reward(rid, token, choice['id'])
            elif run['phase'] == 'path':
                run = self.service.choose_path(rid, token, run['paths'][0]['id'])
        self.assertEqual('complete', run['phase'])
        counts = run['journal']['counts']
        self.assertEqual((1, 13, 13, 12, 19, 4, 3), tuple(counts[key] for key in ('created', 'battle_start', 'battle_end', 'reward', 'path', 'rest', 'shop')))
        self.assertEqual(13, run['journal']['totals']['wins'])
        self.assertEqual(run['money'] - 60, run['journal']['totals']['money_earned'] - run['journal']['totals']['money_spent'])
        self.assertEqual(65, run['journal']['total_events'])


@unittest.skipUnless(shutil.which('node') and (GAME_ROOT / 'battle_service/node_modules/pokemon-showdown').is_dir(),
                     'Install the locked battle service dependencies with npm ci')
class RealRunJournalTests(unittest.TestCase):
    def test_journal_survives_online_backup_restart_and_exact_battle_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            source, backup, restored = [Path(directory) / name for name in ('source.sqlite3', 'backup.sqlite3', 'restored.sqlite3')]
            credentials = {'creation_id': '01' * 32, 'creation_secret': 'ab' * 32}
            service = RunService(source)
            try:
                run = service.create(default_opponents(), seed='journal-real', **credentials)
                run = service.battle_start(run['run_id'], run['token'])
                run = service.battle_action(run['run_id'], run['token'], choose_legal_action(run['battle']['view']['request']), 0, 'before-backup')
                journal = copy.deepcopy(run['journal'])
                entry = snapshot(service._load(run['run_id'], run['token']))
                self.assertTrue(backup_save(source, backup)['compatible'])
                self.assertTrue(restore_save(backup, restored)['compatible'])
            finally:
                service.close()
            service = RunService(restored)
            try:
                run = service.create(default_opponents(), seed='journal-real', **credentials)
                self.assertEqual(journal, run['journal'])
                run = service.resume(run['run_id'], run['token'])
                self.assertEqual(journal, run['journal'])
                finish = service._finish_if_ended
                ended = []
                def observe(state):
                    view = service._client().view(service._ensure_battle(state), 'p1')
                    if view['ended']:
                        ended.append(view)
                    return finish(state)
                service._finish_if_ended = observe
                for decision in range(150):
                    if run['phase'] != 'battle':
                        break
                    run = service.battle_action(run['run_id'], run['token'], choose_legal_action(run['battle']['view']['request']),
                                                run['battle']['revision'], 'after-backup-' + str(decision))
                self.assertNotEqual('battle', run['phase'])
                event = run['journal']['events'][-1]
                self.assertEqual('battle_end', event['kind'])
                self.assertEqual(entry, event['before'])
                self.assertEqual((ended[-1]['winner'], ended[-1]['turn']), (event['details']['winner'], event['details']['turns']))
                self.assertEqual(1, run['journal']['counts']['battle_end'])
                self.assertEqual(run['journal'], service.resume(run['run_id'], run['token'])['journal'])
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
