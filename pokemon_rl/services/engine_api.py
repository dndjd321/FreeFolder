"""HTTP boundary for server-owned battles; the UI never submits stats or results."""
from __future__ import annotations

import asyncio
import copy
import json
import importlib.util
import os
import random
import re
import secrets
import threading
import time
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse


ROOT = Path(__file__).resolve().parents[1]
NATURES = dict(zip(
    ('노력', '천진', '성실', '수줍음', '변덕쟁이', '외로움', '용감', '고집', '개구쟁이',
     '대담', '무사태평', '장난꾸러기', '촐랑', '겁쟁이', '성급', '명랑', '천진난만',
     '조심', '의젓함', '냉정', '덜렁', '차분', '얌전', '건방짐', '신중'),
    ('Hardy', 'Docile', 'Serious', 'Bashful', 'Quirky', 'Lonely', 'Brave', 'Adamant', 'Naughty',
     'Bold', 'Relaxed', 'Impish', 'Lax', 'Timid', 'Hasty', 'Jolly', 'Naive',
     'Modest', 'Mild', 'Quiet', 'Rash', 'Calm', 'Gentle', 'Sassy', 'Careful')))


def canonical_team(team):
    """Convert legacy dex IDs, ignoring no untrusted combat-stat fields."""
    if not isinstance(team, list) or len(team) != 3:
        raise ValueError('포켓몬 3마리를 선택하세요.')
    species_data = json.loads((ROOT / 'data/pokemon.json').read_text(encoding='utf-8'))
    by_num = {p['id']: p['name_en'] for p in species_data.values()}
    allowed = {'id', 'species', 'name', 'moves', 'ability', 'item', 'nature', 'evs', 'ivs', 'level'}
    result = []
    for entry in team:
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise ValueError('팀 데이터에는 종·기술·특성·도구·노력치만 지정할 수 있습니다.')
        species = by_num.get(entry.get('id')) if type(entry.get('id')) is int else entry.get('species')
        if not isinstance(species, str) or not species:
            raise ValueError('포켓몬 종을 확인하세요.')
        moves = entry.get('moves', [])
        if not isinstance(moves, list) or not 1 <= len(moves) <= 4 or any(not isinstance(m, str) or not m or len(m) > 80 for m in moves):
            raise ValueError('기술을 1~4개 선택하세요.')
        row = {'species': species, 'moves': moves, 'level': 50}
        for key in ('ability', 'item', 'name'):
            value = entry.get(key, '')
            if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 for c in value):
                raise ValueError('팀 문자열 형식을 확인하세요.')
            row[key] = value
        nature = entry.get('nature', 'Serious')
        if not isinstance(nature, str) or len(nature) > 30:
            raise ValueError('성격을 확인하세요.')
        row['nature'] = NATURES.get(nature, nature)
        for key in ('evs', 'ivs'):
            if key in entry:
                stats = entry[key]
                if not isinstance(stats, dict) or any(k not in ('hp', 'atk', 'def', 'spa', 'spd', 'spe') or type(v) is not int for k, v in stats.items()):
                    raise ValueError('노력치·개체치 형식을 확인하세요.')
                row[key] = stats
        result.append(row)
    return result


def default_opponents():
    return [
        {'species': 'Venusaur', 'ability': 'Overgrow', 'item': 'Leftovers', 'nature': 'Modest', 'level': 50,
         'moves': ['Giga Drain', 'Sludge Bomb', 'Sleep Powder', 'Synthesis']},
        {'species': 'Blastoise', 'ability': 'Torrent', 'item': 'Sitrus Berry', 'nature': 'Modest', 'level': 50,
         'moves': ['Surf', 'Ice Beam', 'Dark Pulse', 'Protect']},
        {'species': 'Arcanine', 'ability': 'Intimidate', 'item': 'Heavy-Duty Boots', 'nature': 'Adamant', 'level': 50,
         'moves': ['Flare Blitz', 'Extreme Speed', 'Will-O-Wisp', 'Morning Sun']},
    ]


@lru_cache(maxsize=1)
def display_mappings():
    source = (ROOT / 'pokemon_battle_v3.html').read_text(encoding='utf-8')
    result = {}
    for name in ('ABILITY_EN_KO', 'ITEMS_DB'):
        block = source.split('const ' + name, 1)[1].split('\n};', 1)[0]
        if name == 'ABILITY_EN_KO':
            result['abilities'] = {ko: en for en, ko in re.findall(r'"([^"\n]+)"\s*:\s*"([^"\n]*)"', block)}
        else:
            result['items'] = {ko: en for en, ko in re.findall(r'"([^"\n]+)"\s*:\s*\{name:\s*"([^"\n]*)"', block)}
    return result


def canonical_legacy_team(team):
    """Translate display labels only; Showdown still validates every build."""
    if not isinstance(team, list):
        raise ValueError('팀 형식을 확인하세요.')
    mappings = display_mappings()
    rows = []
    for poke in team:
        if not isinstance(poke, dict):
            raise ValueError('팀 형식을 확인하세요.')
        if 'engineSet' in poke:
            rows.append(poke['engineSet'])
            continue
        moves = []
        for name in poke.get('selectedMoves', []):
            move = next((m for m in poke.get('allMoves', []) if isinstance(m, dict) and m.get('name') == name), None)
            if not move or not move.get('nameEn'):
                raise ValueError('기술의 공식 ID를 확인하세요: ' + str(name))
            moves.append(move['nameEn'])
        evs = poke.get('evs', {})
        item = poke.get('selectedItem', '')
        ability = poke.get('selectedAbility') or poke.get('ability', '')
        rows.append({'id': poke.get('id'), 'name': poke.get('name', ''), 'moves': moves,
                     'item': '' if item in ('', '없음') else mappings['items'].get(item, item),
                     'ability': mappings['abilities'].get(ability, ability), 'nature': poke.get('nature', '성실'),
                     'evs': {'hp': evs.get('hp', 0), 'atk': evs.get('atk', 0), 'def': evs.get('def', 0),
                             'spa': evs.get('spatk', 0), 'spd': evs.get('spdef', 0), 'spe': evs.get('spd', 0)}})
    return canonical_team(rows)


def choose_legal_action(request, *, random_policy=False, rng=None):
    """Baseline policy only reads its own request and legal move list."""
    if not request or request.get('wait'):
        return None
    rng = rng or random
    party = request.get('side', {}).get('pokemon', [])
    switches = [{'type': 'switch', 'index': p.get('slot', i)} for i, p in enumerate(party)
                if not p.get('active') and 'fnt' not in str(p.get('condition', '')) and not str(p.get('condition', '')).startswith('0 ')]
    if any(request.get('forceSwitch', [])):
        return switches[0] if switches else None
    active = request.get('active', [{}])[0]
    moves = [(i, m) for i, m in enumerate(active.get('moves', [])) if not m.get('disabled') and m.get('pp', 1) > 0]
    if not moves:
        return switches[0] if switches and not active.get('trapped') else None
    if random_policy:
        return {'type': 'move', 'index': rng.choice(moves)[0]}
    return {'type': 'move', 'index': max(moves, key=lambda x: (x[1].get('basePower', 0) or 0) * (1 if x[1].get('accuracy') is True else (x[1].get('accuracy', 100) or 100) / 100))[0]}


class EngineSessions:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory
        self.client = None
        self.sessions = {}
        self.lock = threading.RLock()
        self.policy = None
        self.policy_path = Path(os.environ.get('FREEFOLDER_POLICY_MODEL', str(ROOT / 'training/models/bootstrap.pt')))

    def neural_available(self):
        return self.policy_path.is_file() and importlib.util.find_spec('torch') is not None and importlib.util.find_spec('numpy') is not None

    def get_policy(self):
        if self.policy is None:
            if not self.neural_available():
                raise ValueError('이 서버에서는 신경망 AI를 사용할 수 없습니다. 다른 난이도를 선택하세요.')
            from training.policy import PolicyAgent
            import torch
            torch.set_num_threads(1)
            self.policy = PolicyAgent.load(self.policy_path)
        return self.policy

    def get_client(self):
        if self.client is None:
            if self.client_factory:
                self.client = self.client_factory()
            else:
                from battle_service.client import BattleClient
                self.client = BattleClient()
        return self.client

    def close(self):
        with self.lock:
            if self.client:
                self.client.close()
                self.client = None
            self.sessions.clear()

    def status(self):
        with self.lock:
            client = self.get_client()
            return {'available': True, 'engine': client.info, 'sessions': len(self.sessions),
                    'ai': {'neural': self.neural_available(), 'model': self.policy_path.name if self.neural_available() else None,
                           'note': '신경망은 개발용 초기 모델이며 규칙 AI보다 약할 수 있습니다.'}}

    def create(self, team, difficulty='normal', *, enemy_team=None, initial_state=None):
        with self.lock:
            if difficulty not in ('easy', 'normal', 'hard', 'expert', 'neural'):
                raise ValueError('AI 난이도를 확인하세요.')
            if difficulty == 'neural':
                self.get_policy()
            if len(self.sessions) >= 128:
                stale = [k for k, v in self.sessions.items() if time.monotonic() - v['touched'] > 3600]
                for key in stale:
                    self.delete(key, self.sessions[key]['token'])
            if len(self.sessions) >= 128:
                raise ValueError('열린 배틀이 많습니다. 기존 배틀을 종료하세요.')
            client = self.get_client()
            seed = [secrets.randbelow(65536) for _ in range(4)]
            battle = client.create({'p1': canonical_team(team), 'p2': enemy_team or default_opponents()}, seed=seed, initial_state=initial_state,
                                   names={'p1': 'Player', 'p2': 'AI'})
            bid = battle['battle_id']
            baseline = None
            if difficulty in ('hard', 'expert'):
                from training.policies import BaselinePolicy
                baseline = BaselinePolicy('heuristic', seed=seed[0])
            self.sessions[bid] = {'token': secrets.token_urlsafe(32), 'revision': 0, 'touched': time.monotonic(), 'difficulty': difficulty, 'baseline': baseline,
                                  'rng': random.Random(str(seed)), 'accepted': {}, 'battle': battle}
            return self.response(bid)

    def authorize(self, bid, token):
        session = self.sessions.get(bid)
        if not session or not isinstance(token, str) or not secrets.compare_digest(session['token'], token):
            raise KeyError('배틀을 찾을 수 없습니다.')
        session['touched'] = time.monotonic()
        return session

    def response(self, bid):
        session = self.sessions[bid]
        view = self.get_client().view(bid, 'p1')
        return {'battle_id': bid, 'token': session['token'], 'revision': session['revision'], 'view': view}

    def act(self, bid, token, action, revision, command_id):
        with self.lock:
            session = self.authorize(bid, token)
            if not isinstance(command_id, str) or not 1 <= len(command_id) <= 80:
                raise ValueError('요청 ID를 확인하세요.')
            encoded = json.dumps(action, sort_keys=True)
            previous = session['accepted'].get(command_id)
            if previous:
                if previous['action'] != encoded:
                    raise ValueError('같은 요청 ID에 다른 행동을 보낼 수 없습니다.')
                return copy.deepcopy(previous['response'])
            if type(revision) is not int or revision != session['revision']:
                raise ValueError('화면이 최신 배틀 상태와 다릅니다. 다시 불러오세요.')
            if not isinstance(action, dict) or set(action) != {'type', 'index'} or action['type'] not in ('move', 'switch') or type(action['index']) is not int:
                raise ValueError('행동 형식을 확인하세요.')
            client = self.get_client()
            battle = client.choose(bid, 'p1', action)
            for _ in range(12):
                if battle.get('ended'):
                    break
                req = battle.get('requests', {}).get('p2')
                if session['difficulty'] == 'neural':
                    ai_action = self.get_policy().predict(req, battle['views']['p2'])
                elif session['baseline'] is not None:
                    ai_action = session['baseline'].predict(req, battle['views']['p2'])
                else:
                    ai_action = choose_legal_action(req, random_policy=session['difficulty'] == 'easy', rng=session['rng'])
                if ai_action is None:
                    break
                battle = client.choose(bid, 'p2', ai_action)
                player_req = battle.get('requests', {}).get('p1')
                if player_req and not player_req.get('wait'):
                    break
            session['revision'] += 1
            session['battle'] = battle
            response = self.response(bid)
            session['accepted'][command_id] = {'action': encoded, 'response': copy.deepcopy(response)}
            if len(session['accepted']) > 1000:
                session['accepted'].pop(next(iter(session['accepted'])))
            return response

    def delete(self, bid, token):
        with self.lock:
            self.authorize(bid, token)
            self.get_client().close_battle(bid)
            del self.sessions[bid]
            return {'ok': True}


async def _json(request):
    raw = await request.body()
    if len(raw) > 100_000:
        raise HTTPException(413, '요청이 너무 큽니다.')
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, 'JSON 형식을 확인하세요.') from exc
    if not isinstance(value, dict):
        raise HTTPException(400, 'JSON 객체가 필요합니다.')
    return value


def install_engine_routes(app):
    service = EngineSessions()
    app.state.engine_sessions = service

    async def invoke(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            code = getattr(exc, 'code', '')
            status = 503 if code.startswith(('WORKER', 'NODE')) else 400
            raise HTTPException(status, str(exc)) from exc

    @app.get('/api/engine/status')
    async def engine_status():
        return await invoke(service.status)

    @app.post('/api/engine/battles')
    async def new_engine_battle(request: Request):
        body = await _json(request)
        return await invoke(service.create, body.get('team'), body.get('difficulty', 'normal'))

    @app.post('/api/engine/battles/{bid}/action')
    async def engine_action(bid: str, request: Request):
        body = await _json(request)
        return await invoke(service.act, bid, body.get('token'), body.get('action'), body.get('revision'), body.get('command_id'))

    @app.get('/api/engine/battles/{bid}')
    async def engine_view(bid: str, request: Request):
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        def get():
            with service.lock:
                service.authorize(bid, token)
                return service.response(bid)
        return await invoke(get)

    @app.delete('/api/engine/battles/{bid}')
    async def engine_delete(bid: str, request: Request):
        body = await _json(request)
        return await invoke(service.delete, bid, body.get('token'))

    @app.get('/web/engine-client.js')
    async def engine_script():
        return FileResponse(ROOT / 'web/engine-client.js', media_type='application/javascript')

    @app.on_event('shutdown')
    async def close_engine():
        await asyncio.to_thread(service.close)

    return service
