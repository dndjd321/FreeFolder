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
from services.run_preparation import resource_preview
from services.save_tools import DatabaseLease, default_db_path
from services.save_format import SAVE_VERSION


ROOT = Path(__file__).resolve().parents[1]


class SaveStorageError(RuntimeError):
    code = 'SAVE_UNAVAILABLE'


class RunStaleError(ValueError):
    code = 'RUN_STALE'


def _copy(value):
    return copy.deepcopy(value)


class RunService:
    """One application process owns its workers; SQLite stores safe boundaries.

    Battle checkpoints are private server records. The browser may choose only
    offered IDs and never supplies HP, PP, money, a winner, or a checkpoint.
    """

    def __init__(self, db_path=None, client_factory=None, *, owns_client=True):
        self.content = json.loads((ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))
        self.db_path = str(db_path or default_db_path())
        if self.db_path != ':memory:':
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.save_lease = DatabaseLease(self.db_path)
        self.save_lease.acquire()
        self.db = None
        try:
            self.db = sqlite3.connect(self.db_path, check_same_thread=False)
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL)')
        except Exception:
            if self.db is not None:
                self.db.close()
            self.save_lease.close()
            raise
        self.last_write_ok = None
        self.storage_configured = db_path is None and bool(os.environ.get('FREEFOLDER_SAVE_DIR'))
        self.lock = threading.RLock()
        self.client_factory = client_factory
        self.owns_client = owns_client
        self.client = None
        self._heuristic_policy = None
        self._move_catalog = None
        self.live = {}
        self.closed = False
        self.relics = {r['id']: r for r in self.content['relics']}
        self.pool = {self._id(p['species']): p for p in self.content['recruit_pool']}
        dex = json.loads((ROOT / 'data/pokemon.json').read_text(encoding='utf-8'))
        self.species_ids = {self._id(p['name_en']): p['id'] for p in dex.values()}
        self.species_ids['rotomwash'] = self.species_ids.get('rotom', 479)
        self.species_ids['mimikyu'] = self.species_ids['mimikyudisguised']
        self.species_types = {self._id(p['name_en']): list(p['types']) for p in dex.values()}
        self.species_types['mimikyu'] = list(self.species_types['mimikyudisguised'])
        # The curated pool supports this appliance form, whose base dex entry
        # is Electric/Ghost. Never infer form typing from a shared dex number.
        self.species_types['rotomwash'] = ['electric', 'water']

    @staticmethod
    def _id(value):
        return ''.join(c.lower() for c in str(value) if c.isascii() and c.isalnum())

    def _client(self):
        if self.closed:
            raise RuntimeError('런 서비스가 종료되었습니다.')
        process = getattr(self.client, '_process', None)
        if self.client is not None and (getattr(self.client, '_closed', False) or (process is not None and process.poll() is not None)):
            if self.owns_client:
                self.client.close()
            self.client = None
            self.live.clear()
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
            try:
                if self.client and self.owns_client:
                    self.client.close()
            finally:
                try:
                    self.db.close()
                finally:
                    self.save_lease.close()
                    self.closed = True
                    self.live.clear()

    def storage_status(self):
        with self.lock:
            available = not self.closed
            if available:
                try:
                    self.db.execute('SELECT id FROM runs LIMIT 1').fetchone()
                except sqlite3.Error:
                    available = False
            return {'available': available, 'scope': 'server',
                    'backend': 'memory' if self.db_path == ':memory:' else 'sqlite',
                    'configured_directory': self.storage_configured,
                    'last_write': {True: 'ok', False: 'failed', None: 'not_attempted'}[self.last_write_ok]}

    def _rng(self, state, stream, suffix=''):
        return random.Random(hashlib.sha256(f"{state['seed']}:{stream}:{suffix}".encode()).digest())

    def _battle_seed(self, state):
        rng = self._rng(state, 'battle', state['node_index'])
        return [rng.randrange(65536) for _ in range(4)]

    def _save(self, state):
        revision = state.get('run_revision', 0) + 1
        saved = {**state, 'run_revision': revision}
        try:
            with self.db:
                self.db.execute('UPDATE runs SET state=?, updated=? WHERE id=?',
                                (json.dumps(saved, ensure_ascii=False, allow_nan=False), time.time(), state['run_id']))
            state['run_revision'] = revision
            self.last_write_ok = True
        except sqlite3.Error as exc:
            self.last_write_ok = False
            # A worker may already have accepted the move. Drop its unsaved
            # state so the next request restores the last committed checkpoint.
            bid = self.live.pop(state['run_id'], None)
            if bid and self.client is not None:
                try:
                    self.client.close_battle(bid)
                except (RuntimeError, KeyError):
                    pass
            raise SaveStorageError('탐험을 저장하지 못했습니다. 잠시 후 다시 이어하기를 시도하세요.') from exc

    def _load(self, run_id, token):
        if not isinstance(run_id, str) or not isinstance(token, str):
            raise KeyError('런을 찾을 수 없습니다.')
        try:
            row = self.db.execute('SELECT token_hash,state FROM runs WHERE id=?', (run_id,)).fetchone()
        except sqlite3.Error as exc:
            raise SaveStorageError('탐험 저장소를 읽지 못했습니다. 잠시 후 다시 시도하세요.') from exc
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
        catalog = self._moves()
        party = []
        for slot, build in enumerate(state['team']):
            resource = _copy(state['resources'][slot])
            for move in resource['moves']:
                info = catalog.get(self._id(move['id']), {})
                move.update({key: info[key] for key in ('name_ko', 'name_en', 'type', 'power', 'category') if key in info})
            party.append({**_copy(build), 'slot': slot, 'resource': resource,
                          'types': list(self.species_types.get(self._id(build['species']), []))})
        return party

    def _moves(self):
        if self._move_catalog is None:
            catalog = json.loads((ROOT / 'data/moves.json').read_text(encoding='utf-8'))
            self._move_catalog = {self._id(key): value for key, value in catalog.items()}
        return self._move_catalog

    @staticmethod
    def _kind_label(kind):
        return {'battle': '일반전', 'elite': '정예전', 'boss': '보스전',
                'rest': '쉼터', 'shop': '상점'}.get(kind, '다음 구간')

    def _boss_at(self, index):
        positions = [i for i, kind in enumerate(self.content['nodes']) if kind == 'boss']
        return self.content['bosses'][positions.index(index)]

    def _node_preview(self, node, state=None):
        """Public planning information, never the private enemy build.

        A route's theme is a hint about its encounter, not a difficulty roll.
        The saved node stays untouched so old checkpoints and choices retain
        exactly the same identifiers and generation inputs.
        """
        kind = node['kind']
        result = _copy(node)
        result.update(kind_label=self._kind_label(kind),
                      danger_level={'battle': 1, 'elite': 2, 'boss': 3}.get(kind, 0))
        themes = {
            'water': ('물 타입 중심의 상대가 기다립니다.', '물 타입을 상대할 기술과 파티 HP를 확인하세요.'),
            'fire': ('불꽃 타입 중심의 상대가 기다립니다.', '불꽃 타입에 유리한 기술과 반동 피해에 대비하세요.'),
            'grass': ('풀 타입 중심의 상대가 기다립니다.', '수면·회복을 사용하는 상대에 대비하고 PP를 확인하세요.'),
            'steel': ('강철 타입 중심의 상대가 기다립니다.', '강철 타입을 상대할 기술과 장기전용 PP를 확인하세요.'),
            'ghost': ('고스트 타입 중심의 상대가 기다립니다.', '상대 타입에 기술이 통하는지 확인하고 상태이상에 대비하세요.'),
            'dragon': ('드래곤 타입 중심의 상대가 기다립니다.', '드래곤 타입을 상대할 기술과 파티의 약점을 확인하세요.'),
        }
        bosses = {
            'rain': ('비·물 타입', '비를 활용하는 물 타입 파티가 기다립니다.',
                     '날씨 대응과 물 타입을 상대할 기술, 파티 HP·PP를 준비하세요.'),
            'sand': ('모래바람·혼합 타입', '모래바람을 활용하는 혼합 타입 파티가 기다립니다.',
                     '모래바람의 지속 피해와 빠른 상대에 대비해 회복·PP를 확보하세요.'),
            'champion': ('혼합 타입·최종 관문', '서로 다른 타입과 물리·특수 공격을 섞는 최종 관문입니다.',
                         '세 동료의 HP·PP를 회복하고 여러 타입에 대응할 기술을 준비하세요.'),
        }
        if kind == 'boss':
            boss = next((entry for entry in self.content['bosses'] if entry['id'] == node.get('boss')), None)
            if boss is None:
                boss = self._boss_at(node['index'])
            result['label'] = boss['name']
            result['theme_label'], result['description'], result['preparation'] = bosses.get(
                boss['id'], ('보스 관문', '강력한 보스가 기다립니다.', '파티 HP·PP와 상태이상을 정비하세요.'))
        elif kind in ('battle', 'elite'):
            theme = next((entry for entry in self.content['themes'] if entry['id'] == node.get('theme')), {})
            result['theme_label'] = theme.get('name', '전투 테마')
            result['description'], result['preparation'] = themes.get(
                node.get('theme'), ('테마를 가진 상대가 기다립니다.', '상대 테마와 파티 HP·PP를 확인하세요.'))
            if kind == 'elite':
                result['description'] += ' 일반전보다 훈련된 정예 파티입니다.'
        elif kind == 'rest':
            result.update(theme_label='회복', description='전투 없이 파티를 정비하는 구간입니다.',
                          preparation='HP·PP·상태이상·기절 상태를 보고 회복 방법 하나를 선택하세요.')
        elif kind == 'shop':
            result.update(theme_label='보급', description='골드를 사용해 다음 전투를 준비하는 구간입니다.',
                          preparation='남은 골드와 필요한 회복·영입을 비교한 뒤 구매하세요.')
        else:
            result.update(theme_label='', description='', preparation='파티 상태를 확인하세요.')
        if kind in ('battle', 'elite', 'boss'):
            effects = state or {'relics': {}}
            amount = self.content['victory_money'] * (1 + self._effect(effects, 'money_bonus')) + self._effect(effects, 'victory_money_flat')
            amount = math.floor(amount * {'battle': 1, 'elite': 1.5, 'boss': 2}[kind])
            result['reward'] = ('현재 특전 기준 · ' if state is not None else '기본 보상 · ') + f'승리 시 {amount}골드 · ' + (
                '탐험 완주' if node['index'] == len(self.content['nodes']) - 1 else '보상 후보 3개 중 1개 선택')
        elif kind == 'rest':
            result['reward'] = 'HP·PP·상태이상·기절한 동료 회복 중 1가지 선택'
        elif kind == 'shop':
            result['reward'] = '골드로 회복·동료 등 구매 가능'
        else:
            result['reward'] = ''
        return result

    def route_guide(self):
        """A seed-independent schedule suitable for the start screen."""
        nodes, bosses = [], []
        for index, kind in enumerate(self.content['nodes']):
            entry = {'index': index, 'kind': kind, 'kind_label': self._kind_label(kind),
                     'label': self._kind_label(kind)}
            if kind == 'boss':
                entry['label'] = self._boss_at(index)['name']
                bosses.append(self._node_preview(entry))
            nodes.append(entry)
        return {'nodes': nodes, 'bosses': bosses}

    def _journey(self, state):
        guide = self.route_guide()
        nodes = []
        for entry in guide['nodes']:
            index = entry['index']
            status = ('completed' if index < state['completed_nodes'] else
                      'current' if index == state['node_index'] else 'upcoming')
            label = state['node']['label'] if index == state['node_index'] else entry['label']
            nodes.append({**entry, 'label': label, 'status': status})
        next_boss = None
        if state['phase'] not in ('complete', 'failed'):
            index = next((entry['index'] for entry in guide['nodes']
                          if entry['kind'] == 'boss' and entry['index'] >= state['node_index']
                          and entry['index'] >= state['completed_nodes']), None)
            if index is not None:
                next_boss = self._node_preview({'index': index, 'kind': 'boss'}, state)
                next_boss['distance'] = index - state['node_index']
        branch_note = ''
        if len(state['paths']) > 1 and len({entry['kind'] for entry in state['paths']}) == 1:
            branch_note = '두 길의 위험도 분류와 기본 보상은 같고, 상대 테마가 다릅니다.'
        return {'nodes': nodes, 'next_boss': next_boss, 'branch_note': branch_note}

    def _public(self, state, token):
        result = {key: _copy(state[key]) for key in
                  ('run_id', 'seed', 'schema_version', 'content_version', 'phase', 'node_index',
                   'node', 'money', 'rewards', 'paths', 'recruit_candidates', 'completed_nodes', 'outcome')}
        result.update(token=token, total_nodes=len(self.content['nodes']), party=self._party_view(state),
                      difficulty=state.get('difficulty', 'normal'),
                      relics=[{**self.relics[key], 'stacks': count} for key, count in state['relics'].items()],
                      rerolls_left=state['rerolls_left'], shop=self._shop_offers(state) if state['phase'] == 'node' and state['node']['kind'] == 'shop' else [])
        result['node'] = self._node_preview(state['node'], state)
        result['paths'] = [self._node_preview(node, state) for node in state['paths']]
        result['journey'] = self._journey(state)
        heal_percent = (.5 + self._effect(state, 'rest_heal_bonus')) * 100
        pp_amount = int(10 + self._effect(state, 'pp_bonus'))
        result['rest_options'] = [{'id': 'heal', 'label': f'살아 있는 파티 HP {heal_percent:g}% 회복', 'available': True},
                                  {'id': 'pp', 'label': f'모든 기술 PP {pp_amount} 회복', 'available': True},
                                  {'id': 'cure', 'label': '주요 상태이상 치료', 'available': True},
                                  {'id': 'revive', 'label': '기절한 동료 1마리 HP 50% 회복',
                                   'available': any(p['hp'] == 0 for p in state['resources'])}]
        for option in result['rest_options']:
            option['preview'] = resource_preview(state['resources'], lambda rows, key=option['id']:
                                                 self._apply_rest({**state, 'resources': rows}, key))
        if state['phase'] == 'battle':
            bid = self._ensure_battle(state)
            result['battle'] = {'battle_id': bid, 'revision': state['battle']['revision'],
                                'view': self._client().view(bid, 'p1')}
        result['run_revision'] = state.get('run_revision', 0)
        return result

    def _node(self, state, index, variant=0):
        kind = self.content['nodes'][index]
        rng = self._rng(state, 'nodes', index)
        theme = rng.sample(self.content['themes'], len(self.content['themes']))[variant % len(self.content['themes'])]
        boss = self._boss_at(index) if kind == 'boss' else None
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
                if 'evs' in poke:
                    values = poke['evs']
                    if (not isinstance(values, dict) or not set(values) <= {'hp', 'atk', 'def', 'spa', 'spd', 'spe'}
                            or any(type(value) is not int or not 0 <= value <= 252 for value in values.values())
                            or sum(values.values()) > 510):
                        raise ValueError('적 파티의 노력치 설정이 올바르지 않습니다.')
                else:
                    poke['evs'] = self._default_enemy_evs(poke)
        return team

    def _default_enemy_evs(self, pokemon):
        """Train the stat used by the authored attacks, keeping explicit builds."""
        catalog = self._moves()
        scores = {'atk': 0, 'spa': 0, 'def': 0}
        for move_name in pokemon.get('moves', []):
            key = self._id(move_name)
            move = catalog.get(key, {})
            stat = 'def' if key == 'bodypress' else {'physical': 'atk', 'special': 'spa'}.get(move.get('category'))
            if stat:
                scores[stat] += max(0, move.get('power') or 0)
        # A build with no direct attack gets defensive training. Body Press is
        # physical but uses the user's Defense, so it has its own score above.
        stat = max(scores, key=scores.get) if any(scores.values()) else 'def'
        return {'hp': 252, stat: 252, 'spe': 4}

    def _choose_ai_action(self, state, view):
        policy = state['battle'].get('ai_policy', 'maxpower')
        if policy == 'maxpower':
            return choose_legal_action(view.get('request'))
        if policy != 'heuristic-v2':
            raise ValueError('저장된 AI 정책을 현재 버전에서 사용할 수 없습니다. 기존 저장은 보존되었습니다.')
        if self._heuristic_policy is None:
            from training.policies import BaselinePolicy
            self._heuristic_policy = BaselinePolicy('heuristic')
        return self._heuristic_policy.predict(view.get('request'), view)

    def _probe(self, team, resources=None):
        client = self._client()
        battle = client.create({'p1': team, 'p2': self.content['themes'][0]['team']}, seed=[1, 2, 3, 4],
                               initial_state={'p1': resources} if resources is not None else None,
                               names={'p1': 'Player', 'p2': 'Probe'})
        try:
            return client.resources(battle['battle_id'])['p1']
        finally:
            client.close_battle(battle['battle_id'])

    def create(self, team, seed=None, difficulty='normal'):
        with self.lock:
            if not isinstance(difficulty, str) or difficulty not in ('normal', 'hard'):
                raise ValueError('탐험 난이도는 기본 또는 전략 중에서 선택하세요.')
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
                     'run_id': run_id, 'run_revision': 0, 'seed': str(seed), 'difficulty': difficulty, 'phase': 'node', 'node_index': 0,
                     'team': team, 'resources': resources, 'money': self.content['starting_money'],
                     'relics': {}, 'rewards': [], 'paths': [], 'recruit_candidates': [],
                     'reward_roll': 0, 'rerolls_left': 0, 'completed_nodes': 0, 'outcome': None,
                     'battle': None, 'commands': {}, 'shop_bought': [], 'last_replay': None}
            state['node'] = self._node(state, 0)
            try:
                with self.db:
                    self.db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, hashlib.sha256(token.encode()).hexdigest(),
                                    json.dumps(state, ensure_ascii=False), time.time()))
                self.last_write_ok = True
            except sqlite3.Error as exc:
                self.last_write_ok = False
                raise SaveStorageError('새 탐험을 저장하지 못했습니다. 잠시 후 다시 시도하세요.') from exc
            return self._public(state, token)

    def perform(self, run_id, token, operation, run_revision, *args):
        """Apply an HTTP mutation only to the exact run state it displayed.

        The lock covers authorization, revision validation and the mutation.
        An accepted battle command may be retried against a newer revision,
        but commands from earlier battles are cleared at battle_start.
        Direct service methods remain usable by server-owned workflows.
        """
        operations = {'battle/start': self.battle_start, 'battle/action': self.battle_action,
                      'battle/finish': self.battle_finish, 'reward': self.choose_reward,
                      'reroll': self.reroll_rewards, 'path': self.choose_path,
                      'rest': self.rest, 'shop': self.shop, 'recruit': self.recruit,
                      'party/order': self.party_order}
        if operation not in operations:
            raise ValueError('지원하지 않는 런 작업입니다.')
        with self.lock:
            state = self._load(run_id, token)
            if type(run_revision) is not int or run_revision < 0:
                raise RunStaleError('탐험 화면이 최신 상태인지 확인할 수 없습니다. 다시 불러온 뒤 선택하세요.')
            retry = (operation == 'battle/action' and len(args) == 3
                     and isinstance(args[2], str)
                     and state['commands'].get(args[2]) == json.dumps(args[0], sort_keys=True))
            if run_revision != state.get('run_revision', 0) and not retry:
                raise RunStaleError('다른 화면에서 탐험이 진행되었습니다. 다시 불러온 뒤 선택하세요.')
            return operations[operation](run_id, token, *args)

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
        self._client()  # Detect a replaced worker before consulting live IDs.
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
            state['battle'] = {'id': bid, 'revision': 0, 'checkpoint': self._client().checkpoint(bid),
                              'ai_policy': 'heuristic-v2' if state.get('difficulty', 'normal') == 'hard'
                              and state['node']['kind'] in ('elite', 'boss') else 'maxpower'}
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
            action = self._choose_ai_action(state, view)
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
                    if self._finish_if_ended(state):
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
            if not self._apply_rest(state, option):
                raise ValueError('회복할 기절한 동료가 없습니다.')
            self._next_path(state)
            self._save(state)
            return self._public(state, token)

    def _apply_rest(self, state, option):
        if option == 'heal':
            self._heal(state, .5 + self._effect(state, 'rest_heal_bonus'))
        elif option == 'pp':
            self._pp(state, 10 + self._effect(state, 'pp_bonus'))
        elif option == 'revive':
            if not self._revive(state, .5):
                return False
        elif option != 'cure':
            raise ValueError('쉼터에서 회복 방법을 선택하세요.')
        if option == 'cure' or self._effect(state, 'rest_cure'):
            for pokemon in state['resources']:
                pokemon['status'] = ''
                pokemon['sleepTurns'] = None
        if self._effect(state, 'rest_revive'):
            self._revive(state, .25)
        return True

    def _apply_shop_recovery(self, state, kind):
        if kind == 'heal':
            self._heal(state, .5 + self._effect(state, 'heal_bonus'))
        elif kind == 'pp':
            self._pp(state, 10 + self._effect(state, 'pp_bonus'))
        elif kind == 'revive':
            return self._revive(state, .5)
        else:
            raise ValueError('상점의 회복 물품을 선택하세요.')
        return True

    def party_order(self, run_id, token, order):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'node' or state['node']['kind'] not in ('battle', 'elite', 'boss'):
                raise ValueError('전투를 시작하기 전 준비 화면에서 파티 순서를 바꾸세요.')
            if (not isinstance(order, list) or len(order) != 3 or any(type(slot) is not int for slot in order)
                    or set(order) != {0, 1, 2}):
                raise ValueError('파티 슬롯 0·1·2를 각각 한 번씩 지정하세요.')
            if state['resources'][order[0]]['hp'] == 0:
                raise ValueError('기절하지 않은 동료를 선두로 선택하세요.')
            state['team'] = [state['team'][old_slot] for old_slot in order]
            state['resources'] = [{**state['resources'][old_slot], 'slot': slot}
                                  for slot, old_slot in enumerate(order)]
            self._save(state)
            return self._public(state, token)

    def _shop_offers(self, state):
        rng = self._rng(state, 'shop', state['node_index'])
        heal_percent = (.5 + self._effect(state, 'heal_bonus')) * 100
        pp_amount = int(10 + self._effect(state, 'pp_bonus'))
        offers = [{'kind': 'heal', 'label': f'살아 있는 파티 HP {heal_percent:g}% 회복', 'price': 25},
                  {'kind': 'pp', 'label': f'모든 기술 PP {pp_amount} 회복', 'price': 25},
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
        result = []
        for offer in offers:
            offer = {**offer, 'id': f"shop-{state['node_index']}-{offer['kind']}",
                     'price': max(1, math.floor(offer['price'] * discount)),
                     'bought': f"shop-{state['node_index']}-{offer['kind']}" in state['shop_bought']}
            if offer['kind'] in ('heal', 'pp', 'revive'):
                offer['preview'] = resource_preview(state['resources'], lambda rows, key=offer['kind']:
                                                    self._apply_shop_recovery({**state, 'resources': rows}, key))
            result.append(offer)
        return result

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
                if offer['kind'] in ('heal', 'pp', 'revive'):
                    self._apply_shop_recovery(state, offer['kind'])
                elif offer['kind'] == 'relic':
                    key = offer['relic_id']
                    state['relics'][key] = state['relics'].get(key, 0) + 1
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
