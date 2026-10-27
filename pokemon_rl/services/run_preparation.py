"""Pure resource previews for server-owned expedition preparation choices.

The service supplies the same mutating recovery function that it uses when an
action is committed. This module measures its result; it does not implement
healing, PP limits, relic bonuses, status treatment, or revival rules again.
"""
from __future__ import annotations

from copy import deepcopy


def _integer(value, label, *, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f'{label} 값이 올바르지 않습니다.')
    return value


def _snapshot(resource):
    if not isinstance(resource, dict):
        raise ValueError('파티 자원 형식이 올바르지 않습니다.')
    maxhp = _integer(resource.get('maxhp'), '최대 HP', minimum=1)
    hp = _integer(resource.get('hp'), 'HP', maximum=maxhp)
    status = resource.get('status') or ''
    if not isinstance(status, str):
        raise ValueError('상태이상 형식이 올바르지 않습니다.')
    sleep_turns = resource.get('sleepTurns')
    if sleep_turns is not None:
        _integer(sleep_turns, '잠듦 경과')
    raw_moves = resource.get('moves')
    if not isinstance(raw_moves, list):
        raise ValueError('기술 자원 형식이 올바르지 않습니다.')
    moves = []
    for move in raw_moves:
        if not isinstance(move, dict) or not isinstance(move.get('id'), str):
            raise ValueError('기술 자원 형식이 올바르지 않습니다.')
        maxpp = _integer(move.get('maxpp'), '최대 PP')
        pp = _integer(move.get('pp'), 'PP', maximum=maxpp)
        moves.append({'id': move['id'], 'pp': pp, 'maxpp': maxpp})
    return {'hp': hp, 'maxhp': maxhp, 'pp': sum(move['pp'] for move in moves),
            'maxpp': sum(move['maxpp'] for move in moves), 'status': status,
            'sleep_turns': sleep_turns, 'moves': moves}


def _totals(party):
    return {'hp': sum(row['hp'] for row in party), 'maxhp': sum(row['maxhp'] for row in party),
            'pp': sum(row['pp'] for row in party), 'maxpp': sum(row['maxpp'] for row in party),
            'fainted': sum(row['hp'] == 0 for row in party),
            'status_count': sum(bool(row['status']) for row in party)}


def resource_preview(resources, apply_effect):
    """Apply a recovery callback to a copy and return JSON-safe before/after.

    ``apply_effect`` receives a mutable deep copy of the resource list. Its
    return value is ignored, which also supports helpers that return a revival
    success flag. Exceptions are propagated without modifying the original.

    Slots describe current resource-list positions; embedded engine slot IDs
    are not party identities. Recovery must retain the party size. ``hp_gain``
    and ``pp_gain`` are actual net changes after the callback's limits. A cure
    means a nonempty primary status became empty; a sleep-counter-only change
    is still reflected in ``available`` and the per-slot snapshots.
    """
    if not isinstance(resources, list) or not callable(apply_effect):
        raise ValueError('파티 자원과 회복 효과를 확인하세요.')
    before_party = [_snapshot(resource) for resource in resources]
    after_resources = deepcopy(resources)
    apply_effect(after_resources)
    if len(after_resources) != len(before_party):
        raise ValueError('회복 효과는 파티 크기를 바꿀 수 없습니다.')
    after_party = [_snapshot(resource) for resource in after_resources]
    party = []
    for slot, (before, after) in enumerate(zip(before_party, after_party)):
        party.append({'slot': slot, 'before': before, 'after': after,
                      'hp_gain': after['hp'] - before['hp'], 'pp_gain': after['pp'] - before['pp'],
                      'revived': before['hp'] == 0 and after['hp'] > 0,
                      'cured': bool(before['status']) and not after['status']})
    before, after = _totals(before_party), _totals(after_party)
    return {'party': party, 'before': before, 'after': after,
            'hp_gain': after['hp'] - before['hp'], 'pp_gain': after['pp'] - before['pp'],
            'revived': sum(row['revived'] for row in party), 'cured': sum(row['cured'] for row in party),
            'available': before_party != after_party}
