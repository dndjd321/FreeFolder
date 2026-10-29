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
from services.run_balance import DEFAULT_PROFILE, balance_profile, normal_rescue_rest, validate_profile
from services.run_creation import CREATION_VERSION, creation_credentials, creation_request_hash
from services.run_journal import snapshot, record_event, public_journal
from services.run_preparation import resource_preview
from services.save_tools import DatabaseLease, default_db_path
from services.save_format import SAVE_VERSION


ROOT = Path(__file__).resolve().parents[1]


class SaveStorageError(RuntimeError):
    code = 'SAVE_UNAVAILABLE'


class RunStaleError(ValueError):
    code = 'RUN_STALE'


class CreationConflictError(ValueError):
    code = 'CREATION_CONFLICT'

    def __init__(self):
        super().__init__('탐험 생성 요청이 일치하지 않습니다. 보관한 생성 요청을 확인하세요.')


def _copy(value):
    return copy.deepcopy(value)


class RunService:
    """One application process owns its workers; SQLite stores safe boundaries.

    Battle checkpoints are private server records. The browser may choose only
    offered IDs and never supplies HP, PP, money, a winner, or a checkpoint.
    """

    def __init__(self, db_path=None, client_factory=None, *, owns_client=True,
                 cache_capacity=16, cache_ttl=300.0, clock=None, new_run_balance_profile=DEFAULT_PROFILE):
        self.new_run_balance_profile = validate_profile(new_run_balance_profile)
        if type(cache_capacity) is not int or cache_capacity < 1:
            raise ValueError('전투 캐시 상한은 1 이상의 정수여야 합니다.')
        if (type(cache_ttl) not in (int, float) or not math.isfinite(cache_ttl) or cache_ttl <= 0
                or (clock is not None and not callable(clock))):
            raise ValueError('전투 캐시 유휴 시간과 시계를 확인하세요.')
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
        self._live_used = {}
        self.cache_capacity = cache_capacity
        self.cache_ttl = float(cache_ttl)
        self._clock = clock if clock is not None else time.monotonic
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
        if self.client is not None and self._client_closed(self.client):
            if self.owns_client:
                self.client.close()
            self.client = None
            self._clear_live()
        if self.client is None:
            if self.client_factory:
                self.client = self.client_factory()
            else:
                from battle_service.client import BattleClient
                self.client = BattleClient()
        return self.client

    @staticmethod
    def _client_closed(client):
        process = getattr(client, '_process', None)
        return bool(getattr(client, '_closed', False) or (process is not None and process.poll() is not None))

    def _clear_live(self):
        self.live.clear()
        self._live_used.clear()

    def _forget_live(self, run_id):
        self._live_used.pop(run_id, None)
        return self.live.pop(run_id, None)

    def _close_live(self, run_id):
        """Close only this run's cached battle, without changing its DB save."""
        bid = self.live.get(run_id)
        if bid is None:
            self._live_used.pop(run_id, None)
            return
        if self.client is not None and not self._client_closed(self.client):
            try:
                self.client.close_battle(bid)
            except KeyError:
                pass  # An already-removed fixture/cache entry is safe to forget.
            except RuntimeError as exc:
                if not self._client_closed(self.client) and getattr(exc, 'code', '') != 'BATTLE_NOT_FOUND':
                    raise
        if self.client is not None and self._client_closed(self.client):
            self._clear_live()
        else:
            self._forget_live(run_id)

    def _discard_live(self, run_id):
        """Do not reuse an uncommitted battle even if its close RPC fails."""
        try:
            self._close_live(run_id)
        except (RuntimeError, KeyError):
            pass
        finally:
            self._forget_live(run_id)

    def _maintain_cache(self, protected_run=None, *, reserve=False):
        """Prune idle/LRU entries during authorized activity, never by timer.

        Other runs are at durable boundaries while this service's lock is held.
        Eviction reads no worker checkpoint and writes no game state: restore
        always uses the last committed SQLite checkpoint. The cap covers live
        expedition battles, excluding temporary probes and other game modes.
        """
        if self.client is not None and self._client_closed(self.client):
            self._clear_live()
        now = self._clock()
        if protected_run in self.live:
            self._live_used[protected_run] = now
        for run_id in list(self.live):
            if run_id != protected_run and now - self._live_used.get(run_id, now) >= self.cache_ttl:
                self._close_live(run_id)
        needed = int(reserve and protected_run not in self.live)
        while len(self.live) + needed > self.cache_capacity:
            candidates = [run_id for run_id in self.live if run_id != protected_run]
            if not candidates:
                raise RuntimeError('처리 중인 탐험의 전투 캐시를 확보할 수 없습니다.')
            oldest = min(candidates, key=lambda run_id: self._live_used.get(run_id, now))
            self._close_live(oldest)

    def _prepare_cache(self, run_id):
        self._client()  # Worker replacement invalidates IDs before capacity checks.
        self._maintain_cache(run_id, reserve=True)
        return self._client()

    def _register_live(self, run_id, bid):
        self.live[run_id] = bid
        self._live_used[run_id] = self._clock()

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                if self.client and self.owns_client:
                    self.client.close()
                elif self.client:
                    error = None
                    for run_id in list(self.live):
                        try:
                            self._close_live(run_id)
                        except (RuntimeError, KeyError) as exc:
                            error = error or exc
                    if error is not None:
                        raise error
            finally:
                try:
                    self.db.close()
                finally:
                    self.save_lease.close()
                    self.closed = True
                    self._clear_live()

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
                    'battle_cache': {'active': len(self.live), 'capacity': self.cache_capacity,
                                     'idle_seconds': self.cache_ttl, 'cleanup': 'authenticated-activity'},
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
            self._discard_live(state['run_id'])
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
        balance_profile(state)
        self._maintain_cache(run_id)
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

    def _build_info(self, build):
        """Detached display data for offered builds, never battle opponents."""
        catalog = self._moves()
        moves = []
        for name in build.get('moves', []):
            key = self._id(name)
            info = catalog.get(key, {})
            moves.append({'id': key, 'name_ko': info.get('name_ko', name),
                          'name_en': info.get('name_en', name), 'type': info.get('type', ''),
                          'category': info.get('category', ''), 'power': info.get('power', 0)})
        return {'species': build['species'],
                'types': list(self.species_types.get(self._id(build['species']), [])),
                'moves': moves, 'ability': build.get('ability', ''), 'item': build.get('item', '')}

    def starter_details(self):
        """Keep creation payloads stable by returning display data separately."""
        return {key: self._build_info(build) for key, build in self.pool.items()}

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
            if state is not None and normal_rescue_rest({**state, 'node_index': node['index'], 'node': node}):
                result['preparation'] = ('부활을 선택하면 기절한 동료 모두 HP 50%로 되살리고, '
                                         '이미 살아 있는 동료도 최대 HP의 50%를 회복합니다.')
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
                      balance_profile=balance_profile(state),
                      difficulty=state.get('difficulty', 'normal'),
                      relics=[{**self.relics[key], 'stacks': count} for key, count in state['relics'].items()],
                      rerolls_left=state['rerolls_left'], shop=self._shop_offers(state) if state['phase'] == 'node' and state['node']['kind'] == 'shop' else [])
        result['node'] = self._node_preview(state['node'], state)
        result['paths'] = [self._node_preview(node, state) for node in state['paths']]
        result['journey'] = self._journey(state)
        result['journal'] = public_journal(state)
        result['rewards'] = [self._reward_view(state, reward) for reward in state['rewards']]
        for candidate in result['recruit_candidates']:
            candidate['build_info'] = self._build_info(candidate['team'])
        heal_percent = (.5 + self._effect(state, 'rest_heal_bonus')) * 100
        pp_amount = int(10 + self._effect(state, 'pp_bonus'))
        result['rest_options'] = [{'id': 'heal', 'label': f'살아 있는 파티 HP {heal_percent:g}% 회복', 'available': True},
                                  {'id': 'pp', 'label': f'모든 기술 PP {pp_amount} 회복', 'available': True},
                                  {'id': 'cure', 'label': '주요 상태이상 치료', 'available': True},
                                  {'id': 'revive', 'label': '기절한 동료 1마리 HP 50% 회복',
                                   'available': any(p['hp'] == 0 for p in state['resources'])}]
        if normal_rescue_rest(state):
            result['rest_options'][-1]['label'] = '기절한 동료 모두 HP 50% 부활 · 살아 있던 동료 HP 50% 회복'
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

    def _creation_replay(self, credentials, request_hash):
        try:
            row = self.db.execute('SELECT token_hash,state FROM runs WHERE id=?', (credentials['run_id'],)).fetchone()
        except sqlite3.Error as exc:
            raise SaveStorageError('탐험 생성 기록을 확인하지 못했습니다. 같은 요청으로 다시 시도하세요.') from exc
        if row is None:
            return None
        if not isinstance(row[0], str) or not secrets.compare_digest(row[0], credentials['token_hash']):
            raise CreationConflictError()
        state = json.loads(row[1])
        creation = state.get('creation')
        if (not isinstance(creation, dict) or set(creation) != {'version', 'request_hash'}
                or type(creation['version']) is not int or creation['version'] != CREATION_VERSION
                or not isinstance(creation['request_hash'], str)
                or not secrets.compare_digest(creation['request_hash'], request_hash)):
            raise CreationConflictError()
        # A creation retry retrieves the latest state; it does not advance AI,
        # settle an ended battle, or reconstruct a changed party's fingerprint.
        return self._public(self._load(credentials['run_id'], credentials['token']), credentials['token'])

    def create(self, team, seed=None, difficulty='normal', *, creation_id=None, creation_secret=None):
        with self.lock:
            credentials = request_hash = None
            if creation_id is not None or creation_secret is not None:
                credentials = creation_credentials(creation_id, creation_secret)
                request_hash = creation_request_hash(team, seed, difficulty)
                replay = self._creation_replay(credentials, request_hash)
                if replay is not None:
                    return replay
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
            if credentials is None:
                token, run_id = secrets.token_urlsafe(32), secrets.token_urlsafe(18)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
            else:
                token, run_id, token_hash = credentials['token'], credentials['run_id'], credentials['token_hash']
            state = {'schema_version': SAVE_VERSION, 'content_version': self.content['content_version'],
                     'run_id': run_id, 'run_revision': 0, 'seed': str(seed), 'difficulty': difficulty, 'phase': 'node', 'node_index': 0,
                     'balance_profile': self.new_run_balance_profile,
                     'team': team, 'resources': resources, 'money': self.content['starting_money'],
                     'relics': {}, 'rewards': [], 'paths': [], 'recruit_candidates': [],
                     'reward_roll': 0, 'rerolls_left': 0, 'completed_nodes': 0, 'outcome': None,
                     'battle': None, 'commands': {}, 'shop_bought': [], 'last_replay': None}
            if credentials is not None:
                state['creation'] = {'version': CREATION_VERSION, 'request_hash': request_hash}
            state['node'] = self._node(state, 0)
            record_event(state, 'created', None, {'difficulty': difficulty})
            try:
                with self.db:
                    self.db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, token_hash,
                                    json.dumps(state, ensure_ascii=False), time.time()))
                self.last_write_ok = True
            except sqlite3.IntegrityError as exc:
                if credentials is not None:
                    replay = self._creation_replay(credentials, request_hash)
                    if replay is not None:
                        self.last_write_ok = True
                        return replay
                self.last_write_ok = False
                raise SaveStorageError('새 탐험을 저장하지 못했습니다. 같은 요청으로 다시 시도하세요.') from exc
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
        run_id = state['run_id']
        self._client()
        if run_id in self.live:
            self._live_used[run_id] = self._clock()
            return self.live[run_id]
        client = self._prepare_cache(run_id)
        if run_id not in self.live:
            battle = client.restore(state['battle']['checkpoint'])
            self._register_live(run_id, battle['battle_id'])
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
            before = snapshot(state)
            client = self._prepare_cache(run_id)
            battle = client.create({'p1': state['team'], 'p2': self._enemy(state)}, seed=self._battle_seed(state),
                                            initial_state={'p1': state['resources']},
                                            names={'p1': 'Player', 'p2': state['node']['label']})
            bid = battle['battle_id']
            self._register_live(run_id, bid)
            try:
                state.update(phase='battle', commands={})
                state['battle'] = {'id': bid, 'revision': 0, 'checkpoint': client.checkpoint(bid),
                                  'ai_policy': 'heuristic-v2' if state.get('difficulty', 'normal') == 'hard'
                                  and state['node']['kind'] in ('elite', 'boss') else 'maxpower'}
                record_event(state, 'battle_start', before, {'opponent_label': state['node']['label']})
                self._save(state)
            except Exception:
                self._discard_live(run_id)
                raise
            return self._public(state, token)

    def _persist_battle(self, state):
        # Never restore an older checkpoint between choose() and persistence:
        # that would acknowledge a command whose worker action was discarded.
        client = self.client
        bid = self.live.get(state['run_id'])
        if client is None or self._client_closed(client) or bid is None:
            if client is not None and self._client_closed(client):
                self._clear_live()
            from battle_service.client import BattleServiceError
            raise BattleServiceError('전투 엔진 연결이 종료되었습니다. 저장된 탐험을 다시 불러오세요.', 'WORKER_CLOSED')
        try:
            checkpoint = client.checkpoint(bid)
        except (RuntimeError, KeyError):
            # A successful choose may not yet be durable. Do not reuse that
            # worker state with the older DB command/revision on the next call.
            self._discard_live(state['run_id'])
            raise
        state['battle']['checkpoint'] = checkpoint
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
        before = snapshot(state)
        state['resources'] = self._client().resources(bid)['p1']
        state['last_replay'] = self._client().checkpoint(bid)
        self._close_live(state['run_id'])
        state['battle'] = None
        if view.get('winner') != 'p1':
            state.update(phase='failed', outcome='lost', rewards=[], paths=[])
            record_event(state, 'battle_end', before,
                         {'winner': view.get('winner'), 'turns': view['turn'], 'post_battle_heal': 0})
            return True
        amount = self.content['victory_money'] * (1 + self._effect(state, 'money_bonus')) + self._effect(state, 'victory_money_flat')
        if state['node']['kind'] == 'elite':
            amount *= 1.5
        elif state['node']['kind'] == 'boss':
            amount *= 2
        state['money'] += math.floor(amount)
        hp_before_bonus = sum(pokemon['hp'] for pokemon in state['resources'])
        self._heal(state, self._effect(state, 'post_battle_heal'))
        healed = sum(pokemon['hp'] for pokemon in state['resources']) - hp_before_bonus
        state['completed_nodes'] = state['node_index'] + 1
        if state['node_index'] == len(self.content['nodes']) - 1:
            state.update(phase='complete', outcome='won', rewards=[], paths=[])
        else:
            state['phase'] = 'reward'
            state['rerolls_left'] = 1 + int(self._effect(state, 'reward_rerolls'))
            state['rewards'] = self._make_rewards(state)
        record_event(state, 'battle_end', before,
                     {'winner': 'p1', 'turns': view['turn'], 'post_battle_heal': healed})
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

    def _reward_recovery_amount(self, state, reward):
        effect = {'heal': 'heal_bonus', 'pp': 'pp_bonus'}.get(reward['kind'])
        if effect is None:
            raise ValueError('회복 보상의 종류를 확인하세요.')
        return reward['amount'] + self._effect(state, effect)

    def _apply_reward_recovery(self, state, reward):
        amount = self._reward_recovery_amount(state, reward)
        if reward['kind'] == 'heal':
            self._heal(state, amount)
        else:
            self._pp(state, amount)

    def _reward_label(self, state, reward):
        if reward['kind'] == 'heal':
            percent = self._reward_recovery_amount(state, reward) * 100
            return f'살아 있는 파티 HP {percent:g}% 회복'
        if reward['kind'] == 'pp':
            amount = int(self._reward_recovery_amount(state, reward))
            return f'모든 기술 PP {amount} 회복'
        return reward['label']

    def _reward_view(self, state, reward):
        result = _copy(reward)
        result['label'] = self._reward_label(state, reward)
        if reward['kind'] in ('heal', 'pp'):
            result['preview'] = resource_preview(state['resources'], lambda rows:
                                                self._apply_reward_recovery({**state, 'resources': rows}, reward))
        return result

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
            label = self._reward_label(state, reward)
            before = snapshot(state)
            state['rewards'] = []
            kind = reward['kind']
            if kind in ('heal', 'pp'):
                self._apply_reward_recovery(state, reward)
            elif kind == 'money':
                state['money'] += reward['amount']
            elif kind == 'relic':
                key = reward['relic_id']
                state['relics'][key] = state['relics'].get(key, 0) + 1
            elif kind == 'recruit':
                self._offer_recruits(state, 'reward')
            if kind != 'recruit':
                self._next_path(state)
            details = {'reward_id': reward['id'], 'reward_kind': kind, 'label': label}
            if kind == 'relic':
                details['relic_id'] = reward['relic_id']
            record_event(state, 'reward', before, details)
            self._save(state)
            return self._public(state, token)

    def reroll_rewards(self, run_id, token):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'reward' or state['rerolls_left'] <= 0:
                raise ValueError('보상을 다시 뽑을 수 없습니다.')
            before = snapshot(state)
            state['rerolls_left'] -= 1
            state['rewards'] = self._make_rewards(state)
            record_event(state, 'reroll', before, {'rerolls_left': state['rerolls_left'], 'cost': 0})
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
            before = snapshot(state)
            state.update(node=node, node_index=node['index'], paths=[], phase='node', shop_bought=[], shop_relic_id=None)
            record_event(state, 'path', before, {'path_id': node['id'], 'node_kind': node['kind'],
                                               'theme': node.get('boss') or node.get('theme', ''), 'label': node['label']})
            self._save(state)
            return self._public(state, token)

    def rest(self, run_id, token, option):
        with self.lock:
            state = self._load(run_id, token)
            if state['phase'] != 'node' or state['node']['kind'] != 'rest' or option not in ('heal', 'pp', 'cure', 'revive'):
                raise ValueError('쉼터에서 회복 방법을 선택하세요.')
            before = snapshot(state)
            if not self._apply_rest(state, option):
                raise ValueError('회복할 기절한 동료가 없습니다.')
            self._next_path(state)
            record_event(state, 'rest', before, {'option': option})
            self._save(state)
            return self._public(state, token)

    def _apply_rest(self, state, option):
        if option == 'heal':
            self._heal(state, .5 + self._effect(state, 'rest_heal_bonus'))
        elif option == 'pp':
            self._pp(state, 10 + self._effect(state, 'pp_bonus'))
        elif option == 'revive':
            if normal_rescue_rest(state):
                entry_dead = [pokemon for pokemon in state['resources'] if pokemon['hp'] == 0]
                if not entry_dead:
                    return False
                entry_living = [pokemon for pokemon in state['resources'] if pokemon['hp'] > 0]
                self._heal({**state, 'resources': entry_living}, .5)
                for pokemon in entry_dead:
                    self._revive({**state, 'resources': [pokemon]}, .5)
            elif not self._revive(state, .5):
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
            before = snapshot(state)
            state['team'] = [state['team'][old_slot] for old_slot in order]
            state['resources'] = [{**state['resources'][old_slot], 'slot': slot}
                                  for slot, old_slot in enumerate(order)]
            record_event(state, 'party_order', before, {'order': order, 'lead_species': state['team'][0]['species']})
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
            before = snapshot(state)
            if offer_id == 'leave':
                self._next_path(state)
                details = {'offer_id': 'leave', 'offer_kind': 'leave', 'price': 0,
                           'label': '상점 떠나기', 'action': 'leave'}
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
                details = {'offer_id': offer['id'], 'offer_kind': offer['kind'], 'price': offer['price'],
                           'label': offer['label'], 'action': 'buy'}
            record_event(state, 'shop', before, details)
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
            before = snapshot(state)
            details = {'candidate_id': candidate_id, 'skipped': candidate_id == 'skip'}
            if candidate_id != 'skip':
                candidate = next((p for p in state['recruit_candidates'] if p['id'] == candidate_id), None)
                if not candidate or type(replace_index) is not int or not 0 <= replace_index < 3:
                    raise ValueError('영입 후보와 교체할 파티 슬롯을 선택하세요.')
                details.update(species=candidate['team']['species'], replace_index=replace_index,
                               replaced_species=state['team'][replace_index]['species'])
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
            record_event(state, 'recruit', before, details)
            self._save(state)
            return self._public(state, token)
