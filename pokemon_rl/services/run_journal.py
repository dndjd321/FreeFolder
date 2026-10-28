"""Bounded, public-safe expedition history; persistence belongs to the caller.

Call ``snapshot`` before a committed gameplay action, then ``record_event``
after its effects. The caller commits journal and gameplay state atomically
and is responsible for recording accepted commands only once. This module has
no database, worker, clock, or random state.
"""
from __future__ import annotations


JOURNAL_VERSION = 1
MAX_EVENTS = 128
EVENT_KINDS = ('created', 'battle_start', 'battle_end', 'reward', 'reroll',
               'path', 'rest', 'shop', 'recruit', 'party_order')
_MAX_INTEGER = 2 ** 53 - 1
_STATUSES = {'', 'brn', 'burn', 'par', 'slp', 'psn', 'tox', 'frz'}
_TOTALS = ('battles_started', 'battles_finished', 'wins', 'losses', 'draws', 'turns',
           'money_earned', 'money_spent', 'hp_recovered', 'pp_recovered',
           'revived', 'cured', 'recruits')
_NODE_KINDS = ('battle', 'elite', 'boss', 'rest', 'shop')
_DETAILS = {
    'created': {'difficulty': ('enum', ('normal', 'hard'))},
    'battle_start': {'opponent_label': ('text', 80)},
    'battle_end': {'winner': ('enum', ('p1', 'p2', 'draw', None)),
                   'turns': ('int', _MAX_INTEGER), 'post_battle_heal': ('int', _MAX_INTEGER)},
    'reward': {'reward_id': ('text', 128),
               'reward_kind': ('enum', ('heal', 'pp', 'money', 'recruit', 'relic')),
               'label': ('text', 100), 'relic_id': ('text', 128)},
    'reroll': {'rerolls_left': ('int', _MAX_INTEGER), 'cost': ('int', _MAX_INTEGER)},
    'path': {'path_id': ('text', 128), 'node_kind': ('enum', _NODE_KINDS),
             'theme': ('text', 80), 'label': ('text', 100)},
    'rest': {'option': ('enum', ('heal', 'pp', 'cure', 'revive'))},
    'shop': {'offer_id': ('text', 128),
             'offer_kind': ('enum', ('heal', 'pp', 'recruit', 'revive', 'relic', 'leave')),
             'price': ('int', _MAX_INTEGER), 'label': ('text', 100),
             'action': ('enum', ('buy', 'leave'))},
    'recruit': {'candidate_id': ('text', 128), 'species': ('text', 80),
                'replace_index': ('int', 2), 'replaced_species': ('text', 80), 'skipped': ('bool',)},
    'party_order': {'order': ('order',), 'lead_species': ('text', 80)},
}


def _integer(value, maximum=_MAX_INTEGER):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('탐험 기록의 숫자 형식이 올바르지 않습니다.')
    return value


def _text(value, limit):
    if not isinstance(value, str):
        raise ValueError('탐험 기록의 문자열 형식이 올바르지 않습니다.')
    return ''.join(char for char in value if ord(char) >= 32)[:limit]


def _clean_snapshot(value):
    if not isinstance(value, dict) or not isinstance(value.get('party'), list) or len(value['party']) > 6:
        raise ValueError('탐험 자원 기록 형식이 올바르지 않습니다.')
    party = []
    for row in value['party']:
        if not isinstance(row, dict):
            raise ValueError('동료 자원 기록 형식이 올바르지 않습니다.')
        cleaned = {'species': _text(row.get('species'), 80),
                   **{key: _integer(row.get(key)) for key in ('hp', 'maxhp', 'pp', 'maxpp')}}
        status = row.get('status')
        if status is None:
            status = ''
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError('동료 상태 기록 형식이 올바르지 않습니다.')
        if cleaned['maxhp'] < 1 or cleaned['hp'] > cleaned['maxhp'] or cleaned['pp'] > cleaned['maxpp']:
            raise ValueError('동료 자원 기록 범위가 올바르지 않습니다.')
        cleaned['status'] = status
        party.append(cleaned)
    totals = {key: sum(row[key] for row in party) for key in ('hp', 'maxhp', 'pp', 'maxpp')}
    totals['fainted'] = sum(row['hp'] == 0 for row in party)
    if any(_integer(value.get(key)) != total for key, total in totals.items()):
        raise ValueError('파티 자원 기록의 합계가 일치하지 않습니다.')
    return {'node_index': _integer(value.get('node_index')), 'node_label': _text(value.get('node_label'), 80),
            **totals, 'money': _integer(value.get('money')), 'party': party}


def snapshot(state):
    """Return detached minimal HP/PP/status/money measurements, never secrets."""
    if (not isinstance(state, dict) or not isinstance(state.get('team'), list)
            or not isinstance(state.get('resources'), list)
            or len(state['team']) != len(state['resources']) or len(state['team']) > 6
            or not isinstance(state.get('node'), dict)):
        raise ValueError('탐험 상태를 기록할 수 없습니다.')
    party = []
    for build, resource in zip(state['team'], state['resources']):
        if not isinstance(build, dict) or not isinstance(resource, dict) or not isinstance(resource.get('moves'), list):
            raise ValueError('파티 자원을 기록할 수 없습니다.')
        pp = maxpp = 0
        for move in resource['moves']:
            if not isinstance(move, dict):
                raise ValueError('기술 자원을 기록할 수 없습니다.')
            current, maximum = _integer(move.get('pp')), _integer(move.get('maxpp'))
            if current > maximum:
                raise ValueError('기술 PP 기록 범위가 올바르지 않습니다.')
            pp += current
            maxpp += maximum
        party.append({'species': build.get('species'), 'hp': resource.get('hp'),
                      'maxhp': resource.get('maxhp'), 'pp': pp, 'maxpp': maxpp,
                      'status': resource.get('status')})
    values = {key: sum(_integer(row[key]) for row in party) for key in ('hp', 'maxhp', 'pp', 'maxpp')}
    values['fainted'] = sum(row['hp'] == 0 for row in party)
    return _clean_snapshot({'node_index': state.get('node_index'), 'node_label': state['node'].get('label', ''),
                            **values, 'money': state.get('money'), 'party': party})


def _details(kind, supplied):
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise ValueError('탐험 선택 기록 형식이 올바르지 않습니다.')
    result = {}
    for key, spec in _DETAILS[kind].items():
        if key not in supplied:
            continue
        value = supplied[key]
        if spec[0] == 'text':
            value = _text(value, spec[1])
        elif spec[0] == 'int':
            value = _integer(value, spec[1])
        elif spec[0] == 'enum':
            if value not in spec[1]:
                raise ValueError('탐험 선택 기록 값이 올바르지 않습니다.')
        elif spec[0] == 'bool':
            if type(value) is not bool:
                raise ValueError('탐험 선택 기록 값이 올바르지 않습니다.')
        elif spec[0] == 'order':
            if (not isinstance(value, list) or len(value) != 3
                    or any(type(slot) is not int for slot in value) or set(value) != {0, 1, 2}):
                raise ValueError('파티 순서 기록 값이 올바르지 않습니다.')
            value = list(value)
        result[key] = value
    if kind == 'battle_end' and not {'winner', 'turns'} <= result.keys():
        raise ValueError('전투 결과와 턴 수를 기록하세요.')
    if kind == 'recruit' and 'skipped' not in result:
        raise ValueError('영입 또는 건너뛰기 여부를 기록하세요.')
    return result


def _empty(from_node=0, complete=False):
    return {'version': JOURNAL_VERSION, 'from_node': from_node, 'complete_history': complete,
            'total_events': 0, 'dropped_events': 0, 'counts': {kind: 0 for kind in EVENT_KINDS},
            'totals': {key: 0 for key in _TOTALS}, 'events': []}


def _clean_journal(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != JOURNAL_VERSION:
        raise ValueError('지원하지 않는 탐험 기록 형식입니다.')
    if type(value.get('complete_history')) is not bool or not isinstance(value.get('events'), list):
        raise ValueError('탐험 기록 범위가 올바르지 않습니다.')
    result = _empty(_integer(value.get('from_node')), value['complete_history'])
    for key, names in (('counts', EVENT_KINDS), ('totals', _TOTALS)):
        if not isinstance(value.get(key), dict):
            raise ValueError('탐험 기록 집계 형식이 올바르지 않습니다.')
        result[key] = {name: _integer(value[key].get(name)) for name in names}
    result['total_events'] = _integer(value.get('total_events'))
    result['dropped_events'] = _integer(value.get('dropped_events'))
    total = result['total_events']
    if (sum(result['counts'].values()) != total or len(value['events']) != min(MAX_EVENTS, total)
            or result['dropped_events'] != total - len(value['events'])
            or result['totals']['battles_started'] != result['counts']['battle_start']
            or result['totals']['battles_finished'] != result['counts']['battle_end']
            or sum(result['totals'][key] for key in ('wins', 'losses', 'draws')) != result['counts']['battle_end']
            or result['totals']['recruits'] > result['counts']['recruit']):
        raise ValueError('탐험 기록 집계가 일치하지 않습니다.')
    for sequence, event in enumerate(value['events'], total - len(value['events']) + 1):
        if not isinstance(event, dict) or event.get('kind') not in EVENT_KINDS or event.get('sequence') != sequence:
            raise ValueError('탐험 기록 순서가 올바르지 않습니다.')
        result['events'].append({'sequence': _integer(event['sequence']), 'kind': event['kind'],
                                 'node_index': _integer(event.get('node_index')),
                                 'node_label': _text(event.get('node_label'), 80),
                                 'before': _clean_snapshot(event.get('before')),
                                 'after': _clean_snapshot(event.get('after')),
                                 'details': _details(event['kind'], event.get('details'))})
    return result


def record_event(state, kind, before_snapshot, details=None):
    """Append one accepted event and update cumulative counters in state only.

    ``created`` accepts a None before-snapshot and starts complete history.
    Legacy runs start partial history at the first measured node. Missing old
    history is never estimated. Unknown/malformed existing journals are kept
    untouched and return False so a future format is not silently overwritten.

    Recovery totals cover reward/rest/shop effects, excluding party replacement
    and reordering. ``battle_end`` requires winner and turns; post_battle_heal
    is the actual HP restored by the post-battle bonus, measured by the caller.
    ``recruit`` requires skipped. Detailed events retain only the newest 128;
    complete_history describes cumulative coverage, not timeline retention.
    """
    if not isinstance(state, dict) or kind not in EVENT_KINDS:
        raise ValueError('탐험 기록 종류를 확인하세요.')
    if 'journal' in state:
        try:
            journal = _clean_journal(state['journal'])
        except (ValueError, TypeError):
            return False
    else:
        journal = None
    after = snapshot(state)
    before = after if kind == 'created' and before_snapshot is None else _clean_snapshot(before_snapshot)
    choice = _details(kind, details)
    if journal is None:
        journal = _empty(0 if kind == 'created' else before['node_index'], kind == 'created')
    journal['total_events'] += 1
    journal['counts'][kind] += 1
    totals = journal['totals']
    if kind != 'created':
        money = after['money'] - before['money']
        totals['money_earned'] += max(0, money)
        totals['money_spent'] += max(0, -money)
    if kind in ('reward', 'rest', 'shop'):
        for old, new in zip(before['party'], after['party']):
            if old['species'] != new['species']:
                continue
            totals['hp_recovered'] += max(0, new['hp'] - old['hp'])
            totals['pp_recovered'] += max(0, new['pp'] - old['pp'])
            totals['revived'] += int(old['hp'] == 0 and new['hp'] > 0)
            totals['cured'] += int(bool(old['status']) and not new['status'])
    elif kind == 'battle_start':
        totals['battles_started'] += 1
    elif kind == 'battle_end':
        totals['battles_finished'] += 1
        totals[{'p1': 'wins', 'p2': 'losses'}.get(choice['winner'], 'draws')] += 1
        totals['turns'] += choice['turns']
        totals['hp_recovered'] += choice.get('post_battle_heal', 0)
    elif kind == 'recruit' and not choice['skipped']:
        totals['recruits'] += 1
    journal['events'].append({'sequence': journal['total_events'], 'kind': kind,
                              'node_index': after['node_index'], 'node_label': after['node_label'],
                              'before': before, 'after': after, 'details': choice})
    journal['events'] = journal['events'][-MAX_EVENTS:]
    journal['dropped_events'] = journal['total_events'] - len(journal['events'])
    state['journal'] = _clean_journal(journal)
    return True


def public_journal(state):
    """Return a detached whitelist, or explicit unavailable/partial coverage."""
    try:
        value = state['journal']
    except (KeyError, TypeError):
        return {**_empty(), 'available': False, 'from_node': None, 'reason': 'not_recorded'}
    try:
        return {**_clean_journal(value), 'available': True}
    except (ValueError, TypeError):
        reason = ('unsupported_version' if isinstance(value, dict) and value.get('version') != JOURNAL_VERSION
                  else 'invalid')
        return {**_empty(), 'available': False, 'from_node': None, 'reason': reason}
