"""Server-owned roguelike runs with deterministic content and SQLite saves."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import sqlite3
import threading
import time

from services.engine_api import canonical_team, choose_legal_action


ROOT = Path(__file__).resolve().parents[1]
SAVE_VERSION = 1


def _copy(value):
    return copy.deepcopy(value)


class RunService:
    """One application process owns its workers; SQLite stores safe boundaries.

    Battle checkpoints are private server records. The browser may choose only
    offered IDs and never supplies HP, PP, money, a winner, or a checkpoint.
    """

    def __init__(self, db_path=None, client_factory=None):
        self.content = json.loads((ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))
        save_directory = Path(os.environ.get('FREEFOLDER_SAVE_DIR', ROOT / 'runtime'))
        self.db_path = str(db_path or save_directory / 'runs.sqlite3')
        if self.db_path != ':memory:':
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL)')
        self.db.commit()
        self.lock = threading.RLock()
        self.client_factory = client_factory
        self.client = None
        self.live = {}
        self.closed = False
        self.relics = {r['id']: r for r in self.content['relics']}
        self.pool = {self._id(p['species']): p for p in self.content['recruit_pool']}
        dex = json.loads((ROOT / 'data/pokemon.json').read_text(encoding='utf-8'))
        self.species_ids = {self._id(p['name_en']): p['id'] for p in dex.values()}
        self.species_ids['rotomwash'] = self.species_ids.get('rotom', 479)

    @staticmethod
    def _id(value):
        return ''.join(c.lower() for c in str(value) if c.isascii() and c.isalnum())

    def _client(self):
        if self.closed:
            raise RuntimeError('런 서비스가 종료되었습니다.')
        if self.client is None:
            if self.client_factory:
                self.client = self.client_factory()
            else:
                from battle_service.client import BattleClient
                self.client = BattleClient()
        return self.client

    def close(self):
        with self.lock:
            if self.closed:
                return
            if self.client:
                self.client.close()
            self.db.close()
            self.closed = True
            self.live.clear()

    def _rng(self, state, stream, suffix=''):
        return random.Random(hashlib.sha256(f"{state['seed']}:{stream}:{suffix}".encode()).digest())

    def _battle_seed(self, state):
        rng = self._rng(state, 'battle', state['node_index'])
        return [rng.randrange(65536) for _ in range(4)]

    def _save(self, state):
        self.db.execute('UPDATE runs SET state=?, updated=? WHERE id=?',
                        (json.dumps(state, ensure_ascii=False, allow_nan=False), time.time(), state['run_id']))
        self.db.commit()

    def _load(self, run_id, token):
        if not isinstance(run_id, str) or not isinstance(token, str):
            raise KeyError('런을 찾을 수 없습니다.')
        row = self.db.execute('SELECT token_hash,state FROM runs WHERE id=?', (run_id,)).fetchone()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not row or not secrets.compare_digest(row[0], digest):
            raise KeyError('런을 찾을 수 없습니다.')
        state = json.loads(row[1])
        if state.get('schema_version') != SAVE_VERSION or state.get('content_version') != self.content['content_version']:
            raise ValueError('저장 버전이 현재 콘텐츠와 다릅니다. 기존 저장은 보존되었습니다.')
        return state

    def _effect(self, state, effect):
        return sum(self.relics[key]['value'] * count for key, count in state['relics'].items()
                   if self.relics[key]['effect'] == effect)

    def _party_view(self, state):
        return [{**_copy(build), 'slot': i, 'resource': _copy(state['resources'][i])}
                for i, build in enumerate(state['team'])]

    def _public(self, state, token):
        result = {key: _copy(state[key]) for key in
                  ('run_id', 'seed', 'schema_version', 'content_version', 'phase', 'node_index',
                   'node', 'money', 'rewards', 'paths', 'recruit_candidates', 'completed_nodes', 'outcome')}
        result.update(token=token, total_nodes=len(self.content['nodes']), party=self._party_view(state),
                      relics=[{**self.relics[key], 'stacks': count} for key, count in state['relics'].items()],
                      rerolls_left=state['rerolls_left'], shop=self._shop_offers(state) if state['phase'] == 'node' and state['node']['kind'] == 'shop' else [])
        result['rest_options'] = [{'id': 'heal', 'label': '살아 있는 파티 HP 50% 회복', 'available': True},
                                  {'id': 'pp', 'label': '모든 기술 PP 10 회복', 'available': True},
                                  {'id': 'cure', 'label': '주요 상태이상 치료', 'available': True},
                                  {'id': 'revive', 'label': '기절한 동료 1마리 HP 50% 회복',
                                   'available': any(p['hp'] == 0 for p in state['resources'])}]
        if state['phase'] == 'battle':
            bid = self._ensure_battle(state)
            result['battle'] = {'battle_id': bid, 'revision': state['battle']['revision'],
                                'view': self._client().view(bid, 'p1')}
        return result

    def _node(self, state, index, variant=0):
        kind = self.content['nodes'][index]
        rng = self._rng(state, 'nodes', index)
        theme = rng.sample(self.content['themes'], len(self.content['themes']))[variant % len(self.content['themes'])]
        boss = self.content['bosses'][[4, 9, 19].index(index)] if kind == 'boss' else None
        return {'id': f'node-{index}-{variant}', 'index': index, 'kind': kind,
                'theme': theme['id'], 'boss': boss['id'] if boss else None,
                'label': boss['name'] if boss else {'rest': '쉼터', 'shop': '떠돌이 상점'}.get(kind, theme['name']),
                'danger': '보스' if kind == 'boss' else '정예' if kind == 'elite' else '일반'}

    def _enemy(self, state):
        node = state['node']
        source = self.content['bosses'] if node['kind'] == 'boss' else self.content['themes']
        key = node['boss'] if node['kind'] == 'boss' else node['theme']
        team = _copy(next(t['team'] for t in source if t['id'] == key))
        if node['kind'] in ('elite', 'boss'):
            for poke in team:
                poke['evs'] = {'hp': 252, 'atk': 4, 'def': 0, 'spa': 252, 'spd': 0, 'spe': 0}
        return team

    def _probe(self, team, resources=None):
        client = self._client()
        battle = client.create({'p1': team, 'p2': self.content['themes'][0]['team']}, seed=[1, 2, 3, 4],
                               initial_state={'p1': resources} if resources is not None else None,
                               names={'p1': 'Player', 'p2': 'Probe'})
        try:
            return client.resources(battle['battle_id'])['p1']
        finally:
            client.close_battle(battle['battle_id'])

    def create(self, team, seed=None):
        with self.lock:
            team = canonical_team(team)
            if any(self._id(p['species']) not in self.pool for p in team):
                raise ValueError('로그라이크는 검증된 30종 파티 풀을 사용합니다.')
            if seed is None:
                seed = secrets.token_hex(8)
            if not isinstance(seed, (str, int)) or isinstance(seed, bool) or len(str(seed)) > 128:
                raise ValueError('시드 형식을 확인하세요.')
            resources = self._probe(team)
            token, run_id = secrets.token_urlsafe(32), secrets.token_urlsafe(18)
            state = {'schema_version': SAVE_VERSION, 'content_version': self.content['content_version'],
                     'run_id': run_id, 'seed': str(seed), 'phase': 'node', 'node_index': 0,
                     'team': team, 'resources': resources, 'money': self.content['starting_money'],
                     'relics': {}, 'rewards': [], 'paths': [], 'recruit_candidates': [],
                     'reward_roll': 0, 'rerolls_left': 0, 'completed_nodes': 0, 'outcome': None,
                     'battle': None, 'commands': {}, 'shop_bought': [], 'last_replay': None}
            state['node'] = self._node(state, 0)
            self.db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, hashlib.sha256(token.encode()).hexdigest(),
                            json.dumps(state, ensure_ascii=False), time.time()))
            self.db.commit()
            return self._public(state, token)

    def get(self, run_id, token):
        with self.lock:
            state = self._load(run_id, token)
            return self._public(state, token)

    def resume(self, run_id, token):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] == 'battle':
                self._ensure_battle(state)
                self._advance_ai(state)
                self._finish_if_ended(state)
                self._save(state)
            return self._public(state, token)

    def _ensure_battle(self, state):
        run_id = state['run_id']
        if run_id not in self.live:
            battle = self._client().restore(state['battle']['checkpoint'])
            self.live[run_id] = battle['battle_id']
            state['battle']['id'] = battle['battle_id']
            self._save(state)
        return self.live[run_id]

    def battle_start(self, run_id, token):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] == 'battle':
                return self._public(state, token)
            if state['phase'] != 'node' or state['node']['kind'] not in ('battle', 'elite', 'boss'):
                raise ValueError('현재 노드에서는 전투를 시작할 수 없습니다.')
            if len(self.live) >= 128:
                raise ValueError('열린 런 배틀이 많습니다. 기존 배틀을 마치세요.')
            battle = self._client().create({'p1': state['team'], 'p2': self._enemy(state)}, seed=self._battle_seed(state),
                                            initial_state={'p1': state['resources']},
                                            names={'p1': 'Player', 'p2': state['node']['label']})
            bid = battle['battle_id']
            self.live[run_id] = bid
            state.update(phase='battle', commands={})
            state['battle'] = {'id': bid, 'revision': 0, 'checkpoint': self._client().checkpoint(bid)}
            self._save(state)
            return self._public(state, token)

    def _persist_battle(self, state):
        bid = self._ensure_battle(state)
        state['battle']['checkpoint'] = self._client().checkpoint(bid)
        self._save(state)

    def _advance_ai(self, state):
        bid = self._ensure_battle(state)
        for _ in range(16):
            view = self._client().view(bid, 'p2')
            if view['ended']:
                return
            action = choose_legal_action(view.get('request'))
            if action is None:
                return
            self._client().choose(bid, 'p2', action)
            self._persist_battle(state)
            own = self._client().view(bid, 'p1')
            if own.get('request') and not own['request'].get('wait'):
                return
        raise RuntimeError('AI 행동 대기가 길어졌습니다. 런을 다시 불러오세요.')

    def battle_action(self, run_id, token, action, revision, command_id):
        with self.lock:
            state = self._load(run_id, token)
            if not isinstance(command_id, str) or not 1 <= len(command_id) <= 80:
                raise ValueError('요청 ID를 확인하세요.')
            encoded = json.dumps(action, sort_keys=True)
            previous = state['commands'].get(command_id)
            if previous:
                if previous != encoded:
                    raise ValueError('같은 요청 ID에 다른 행동을 보낼 수 없습니다.')
                if state['phase'] == 'battle':
                    self._advance_ai(state)
                    self._finish_if_ended(state)
                    self._save(state)
                return self._public(state, token)
            if state['phase'] != 'battle' or type(revision) is not int or revision != state['battle']['revision']:
                raise ValueError('화면이 최신 런 상태와 다릅니다. 다시 불러오세요.')
            if not isinstance(action, dict) or set(action) != {'type', 'index'} or action['type'] not in ('move', 'switch') or type(action['index']) is not int:
                raise ValueError('행동 형식을 확인하세요.')
            bid = self._ensure_battle(state)
            self._client().choose(bid, 'p1', action)
            state['commands'][command_id] = encoded
            state['battle']['revision'] += 1
            self._persist_battle(state)
            self._advance_ai(state)
            self._finish_if_ended(state)
            self._save(state)
            return self._public(state, token)

    def battle_finish(self, run_id, token):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] == 'battle' and not self._finish_if_ended(state):
                raise ValueError('전투가 아직 끝나지 않았습니다.')
            self._save(state)
            return self._public(state, token)

    def _finish_if_ended(self, state):
        bid = self._ensure_battle(state)
        view = self._client().view(bid, 'p1')
        if not view['ended']:
            return False
        state['resources'] = self._client().resources(bid)['p1']
        state['last_replay'] = self._client().checkpoint(bid)
        self._client().close_battle(bid)
        self.live.pop(state['run_id'], None)
        state['battle'] = None
        if view.get('winner') != 'p1':
            state.update(phase='failed', outcome='lost', rewards=[], paths=[])
            return True
        amount = self.content['victory_money'] * (1 + self._effect(state, 'money_bonus')) + self._effect(state, 'victory_money_flat')
        if state['node']['kind'] == 'elite':
            amount *= 1.5
        elif state['node']['kind'] == 'boss':
            amount *= 2
        state['money'] += math.floor(amount)
        self._heal(state, self._effect(state, 'post_battle_heal'))
        state['completed_nodes'] = state['node_index'] + 1
        if state['node_index'] == len(self.content['nodes']) - 1:
            state.update(phase='complete', outcome='won', rewards=[], paths=[])
        else:
            state['phase'] = 'reward'
            state['rerolls_left'] = 1 + int(self._effect(state, 'reward_rerolls'))
            state['rewards'] = self._make_rewards(state)
        return True

    def _make_rewards(self, state):
        rng = self._rng(state, 'rewards', f"{state['node_index']}:{state['reward_roll']}")
        state['reward_roll'] += 1
        candidates = [{'kind': 'heal', 'label': '파티 HP 35% 회복', 'amount': .35},
                      {'kind': 'pp', 'label': '모든 기술 PP 5 회복', 'amount': 5},
                      {'kind': 'money', 'label': '재화 35 획득', 'amount': 35},
                      {'kind': 'recruit', 'label': '새 동료 영입'}]
        available = [r for r in self.content['relics'] if state['relics'].get(r['id'], 0) < r['max_stacks']]
        rng.shuffle(candidates)
        if available and rng.random() < .65 + self._effect(state, 'rarity_bonus'):
            relic = rng.choice(available)
            candidates.insert(0, {'kind': 'relic', 'label': relic['name'], 'description': relic['description'], 'relic_id': relic['id']})
        return [{**reward, 'id': f"reward-{state['node_index']}-{state['reward_roll']}-{i}"} for i, reward in enumerate(candidates[:3])]

    def _heal(self, state, fraction, *, revive=False):
        if fraction <= 0:
            return
        for p in state['resources']:
            if p['hp'] > 0 or revive:
                p['hp'] = min(p['maxhp'], p['hp'] + max(1, math.floor(p['maxhp'] * fraction)))

    def _pp(self, state, amount):
        for p in state['resources']:
            for move in p['moves']:
                move['pp'] = min(move['maxpp'], move['pp'] + int(amount))

    def _revive(self, state, fraction):
        fainted = next((p for p in state['resources'] if p['hp'] == 0), None)
        if fainted:
            fainted.update(hp=max(1, math.floor(fainted['maxhp'] * fraction)), status='', sleepTurns=None)
        return fainted is not None

    def _next_path(self, state):
        state['completed_nodes'] = state['node_index'] + 1
        if state['node_index'] >= len(self.content['nodes']) - 1:
            state.update(phase='complete', outcome='won', paths=[])
            return
        state['phase'] = 'path'
        index = state['node_index'] + 1
        count = 1 if self.content['nodes'][index] in ('boss', 'rest', 'shop') else 2
        state['paths'] = [self._node(state, index, i) for i in range(count)]

    def choose_reward(self, run_id, token, reward_id):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'reward':
                raise ValueError('현재 받을 수 있는 전투 보상이 없습니다.')
            reward = next((r for r in state['rewards'] if r['id'] == reward_id), None)
            if not reward:
                raise ValueError('제시된 보상을 선택하세요.')
            state['rewards'] = []
            kind = reward['kind']
            if kind == 'heal':
                self._heal(state, reward['amount'] + self._effect(state, 'heal_bonus'))
            elif kind == 'pp':
                self._pp(state, reward['amount'] + self._effect(state, 'pp_bonus'))
            elif kind == 'money':
                state['money'] += reward['amount']
            elif kind == 'relic':
                key = reward['relic_id']
                state['relics'][key] = state['relics'].get(key, 0) + 1
            elif kind == 'recruit':
                self._offer_recruits(state, 'reward')
            if kind != 'recruit':
                self._next_path(state)
            self._save(state)
            return self._public(state, token)

    def reroll_rewards(self, run_id, token):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'reward' or state['rerolls_left'] <= 0:
                raise ValueError('보상을 다시 뽑을 수 없습니다.')
            state['rerolls_left'] -= 1
            state['rewards'] = self._make_rewards(state)
            self._save(state)
            return self._public(state, token)

    def choose_path(self, run_id, token, path_id):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'path':
                raise ValueError('현재는 다음 길을 선택할 수 없습니다.')
            node = next((p for p in state['paths'] if p['id'] == path_id), None)
            if not node:
                raise ValueError('제시된 길을 선택하세요.')
            state.update(node=node, node_index=node['index'], paths=[], phase='node', shop_bought=[], shop_relic_id=None)
            self._save(state)
            return self._public(state, token)

    def rest(self, run_id, token, option):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'node' or state['node']['kind'] != 'rest' or option not in ('heal', 'pp', 'cure', 'revive'):
                raise ValueError('쉼터에서 회복 방법을 선택하세요.')
            if option == 'heal':
                self._heal(state, .5 + self._effect(state, 'rest_heal_bonus'))
            elif option == 'pp':
                self._pp(state, 10 + self._effect(state, 'pp_bonus'))
            elif option == 'revive' and not self._revive(state, .5):
                raise ValueError('회복할 기절한 동료가 없습니다.')
            if option == 'cure' or self._effect(state, 'rest_cure'):
                for p in state['resources']:
                    p['status'] = ''
                    p['sleepTurns'] = None
            if self._effect(state, 'rest_revive'):
                self._revive(state, .25)
            self._next_path(state)
            self._save(state)
            return self._public(state, token)

    def _shop_offers(self, state):
        rng = self._rng(state, 'shop', state['node_index'])
        offers = [{'kind': 'heal', 'label': '파티 HP 50% 회복', 'price': 25},
                  {'kind': 'pp', 'label': '모든 기술 PP 10 회복', 'price': 25},
                  {'kind': 'recruit', 'label': '동료 영입', 'price': 35}]
        if any(p['hp'] == 0 for p in state['resources']):
            offers.append({'kind': 'revive', 'label': '기절한 동료 1마리 HP 50% 회복', 'price': 40})
        available = [r for r in self.content['relics'] if state['relics'].get(r['id'], 0) < r['max_stacks']]
        key = state.get('shop_relic_id')
        if key or available:
            relic = self.relics[key] if key else rng.choice(available)
            state['shop_relic_id'] = relic['id']
            offers.append({'kind': 'relic', 'label': relic['name'], 'description': relic['description'], 'relic_id': relic['id'], 'price': 45})
        discount = max(.2, 1 - self._effect(state, 'shop_discount'))
        return [{**offer, 'id': f"shop-{state['node_index']}-{offer['kind']}",
                 'price': max(1, math.floor(offer['price'] * discount)),
                 'bought': f"shop-{state['node_index']}-{offer['kind']}" in state['shop_bought']} for offer in offers]

    def shop(self, run_id, token, offer_id):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'node' or state['node']['kind'] != 'shop':
                raise ValueError('현재 노드는 상점이 아닙니다.')
            if offer_id == 'leave':
                self._next_path(state)
            else:
                offer = next((o for o in self._shop_offers(state) if o['id'] == offer_id), None)
                if not offer or offer['bought']:
                    raise ValueError('구매할 수 있는 상품을 선택하세요.')
                if state['money'] < offer['price']:
                    raise ValueError('재화가 부족합니다.')
                state['money'] -= offer['price']
                state['shop_bought'].append(offer_id)
                if offer['kind'] == 'heal':
                    self._heal(state, .5 + self._effect(state, 'heal_bonus'))
                elif offer['kind'] == 'pp':
                    self._pp(state, 10 + self._effect(state, 'pp_bonus'))
                elif offer['kind'] == 'relic':
                    key = offer['relic_id']
                    state['relics'][key] = state['relics'].get(key, 0) + 1
                elif offer['kind'] == 'revive':
                    self._revive(state, .5)
                else:
                    self._offer_recruits(state, 'shop')
            self._save(state)
            return self._public(state, token)

    def _offer_recruits(self, state, origin):
        rng = self._rng(state, 'recruit', f"{state['node_index']}:{origin}")
        pool = [p for p in self.content['recruit_pool'] if self._id(p['species']) not in {self._id(t['species']) for t in state['team']}]
        count = min(len(pool), 3 + int(self._effect(state, 'recruit_options')))
        state['recruit_candidates'] = [{'id': f"recruit-{state['node_index']}-{i}", 'team': _copy(p)} for i, p in enumerate(rng.sample(pool, count))]
        state.update(phase='recruit', recruit_origin=origin)

    def recruit(self, run_id, token, candidate_id, replace_index=0):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'recruit':
                raise ValueError('현재 영입할 동료가 없습니다.')
            if candidate_id != 'skip':
                candidate = next((p for p in state['recruit_candidates'] if p['id'] == candidate_id), None)
                if not candidate or type(replace_index) is not int or not 0 <= replace_index < 3:
                    raise ValueError('영입 후보와 교체할 파티 슬롯을 선택하세요.')
                new_team = _copy(state['team'])
                new_team[replace_index] = _copy(candidate['team'])
                initial = _copy(state['resources'])
                initial[replace_index] = None
                resources = self._probe(new_team, initial)
                state.update(team=new_team, resources=resources)
            state['recruit_candidates'] = []
            if state.pop('recruit_origin') == 'shop':
                state['phase'] = 'node'
            else:
                self._next_path(state)
            self._save(state)
            return self._public(state, token)
