"""Frozen multi-team expedition comparison using public player information.

Run from pokemon_rl with ``python -m balance.diversity --report PATH``.
``--seeds`` selects a separate smoke cohort; it never changes game rules.
The original balance CLI, battle baselines and economy choices are reused.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import numpy as np

from balance.cli import (ROOT, PUBLIC_POLICY, _digest, _file_hash, _git_metadata,
                         _summary, recommended_team, run_case, write_report)
from balance.policies import (POLICY_VERSION as SUSTAIN_VERSION, SUSTAIN_POLICY,
                              SUSTAIN_REASONS, SustainPolicy)
from battle_service.client import BattleClient
from services.run_service import RunService
from training.observation import action_to_command, legal_mask
from training.policies import BaselinePolicy, POLICY_VERSION as BASELINE_VERSION


SUITE_ID = 'run-diversity-v1'
DEFAULT_SEEDS = tuple(f'{SUITE_ID}-{index:03d}' for index in range(10))
MODES = ('normal', 'hard')
TEAM_IDS = ('recommended-v1', 'fixture-sustain-v1', 'curated-coverage-v1')
POLICY_IDS = ('public-heuristic-v2', 'public-sustain-v1')
BALANCE_PROFILE = 'normal-rescue-v2'
MAX_DECISIONS = 1000
MAX_BATTLE_TURNS = 200
RECOVERY_MOVES = ('roost', 'moonlight', 'morningsun', 'synthesis', 'rest', 'wish')
HEAL_EVENT_COVERAGE = ('Observed public p1 heal events in returned battle views only; a lower bound. '
                       'Final battle inputs may return a non-battle phase without final events.')
OPERATIONS = ('battle/start', 'battle/action', 'battle/finish', 'resume', 'rest',
              'shop', 'reward', 'path', 'recruit')
PHASES = ('node', 'battle', 'reward', 'path', 'recruit', 'complete', 'failed')
TYPES = frozenset(('normal', 'fire', 'water', 'electric', 'grass', 'ice', 'fighting',
                   'poison', 'ground', 'flying', 'psychic', 'bug', 'rock', 'ghost',
                   'dragon', 'dark', 'steel', 'fairy'))
ERROR_CODES = frozenset(('RUN_ERROR', 'BALANCE_ERROR', 'JOURNAL_UNAVAILABLE',
    'WORKER_CLOSED', 'WORKER_PROTOCOL', 'WORKER_TIMEOUT', 'ENGINE_ERROR',
    'ENGINE_START_ERROR', 'INVALID_ACTION', 'INVALID_TEAM', 'INVALID_REQUEST',
    'BATTLE_NOT_FOUND', 'BATTLE_ENDED', 'RUN_STALE', 'SAVE_UNAVAILABLE',
    'CLEANUP_ERROR', 'TRACE_ERROR', 'ILLEGAL_POLICY_ACTION', 'BALANCE_PROFILE_UNAVAILABLE'))
ERROR_TYPES = frozenset(('RuntimeError', 'ValueError', 'TypeError', 'KeyError',
    'OSError', 'BalanceError', 'JournalUnavailable', 'BattleServiceError',
    'SaveStorageError', 'RunStaleError', 'TraceError'))
SOURCE_FILES = ('pokemon_rl/balance/diversity.py', 'pokemon_rl/balance/policies.py',
    'pokemon_rl/balance/cli.py', 'pokemon_rl/training/policies.py',
    'pokemon_rl/training/observation.py', 'pokemon_rl/services/engine_api.py',
    'pokemon_rl/services/run_service.py', 'pokemon_rl/services/run_journal.py',
    'pokemon_rl/services/run_balance.py', 'pokemon_rl/services/run_preparation.py',
    'pokemon_rl/battle_service/client.py', 'pokemon_rl/battle_service/engine.cjs',
    'pokemon_rl/battle_service/worker.cjs', 'pokemon_rl/data/pokemon.json', 'pokemon_rl/data/moves.json',
    'pokemon_rl/battle_service/package-lock.json', 'pokemon_rl/content/roguelike.json',
    'docs/RUN_DIVERSITY_PROTOCOL.md')


class TraceError(RuntimeError):
    code = 'TRACE_ERROR'


def teams():
    """Detached exact builds, including the original fixture's move order/EVs."""
    pool = {row['species']: row for row in json.loads(
        (ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))['recruit_pool']}
    fixture = [
        {'species': 'Garchomp', 'ability': 'Rough Skin', 'item': 'Leftovers', 'nature': 'Jolly',
         'level': 50, 'evs': {'atk': 252, 'spe': 252, 'spd': 4},
         'moves': ['Earthquake', 'Dragon Claw', 'Poison Jab', 'Swords Dance']},
        {'species': 'Vaporeon', 'ability': 'Water Absorb', 'item': 'Leftovers', 'nature': 'Modest',
         'level': 50, 'evs': {'hp': 252, 'spa': 252, 'def': 4},
         'moves': ['Surf', 'Ice Beam', 'Protect', 'Wish']},
        {'species': 'Scizor', 'ability': 'Technician', 'item': 'Leftovers', 'nature': 'Adamant',
         'level': 50, 'evs': {'hp': 252, 'atk': 252, 'def': 4},
         'moves': ['Bullet Punch', 'Swords Dance', 'Knock Off', 'Roost']},
    ]
    return dict(zip(TEAM_IDS, (recommended_team(), fixture,
        copy.deepcopy([pool[name] for name in ('Rotom-Wash', 'Scizor', 'Togekiss')]))))


def case_plan(seeds=DEFAULT_SEEDS):
    """A paired seed-major plan; no outcomes influence ordering or selection."""
    seeds = tuple(seeds)
    if (not seeds or any(not isinstance(seed, str) or not 1 <= len(seed) <= 128 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError('Provide unique nonempty seeds of at most 128 characters')
    return [{'case_id': f'{seed}/{team}/{policy}/{mode}', 'seed': seed,
             'team_id': team, 'policy_id': policy, 'difficulty': mode}
            for seed in seeds for team in TEAM_IDS for policy in POLICY_IDS for mode in MODES]


def _policy(policy_id):
    return BaselinePolicy('heuristic', seed=0) if policy_id == POLICY_IDS[0] else SustainPolicy()


def _error(value, fallback='RUN_ERROR'):
    """Keep controlled codes only, including errors already caught by run_case."""
    if isinstance(value, dict):
        name, code = value.get('type'), value.get('code')
    else:
        name, code = type(value).__name__, getattr(value, 'code', None)
    return {'type': name if isinstance(name, str) and name in ERROR_TYPES else 'Exception',
            'code': code if isinstance(code, str) and code in ERROR_CODES else fallback}


def _number(value, maximum=10 ** 9):
    if type(value) is not int or not 0 <= value <= maximum:
        raise TraceError('Invalid public numeric field')
    return value


def _text(value, limit=80):
    if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise TraceError('Invalid public text field')
    return value


def _command(value):
    if (not isinstance(value, dict) or set(value) != {'type', 'index'}
            or value.get('type') not in ('move', 'switch')):
        raise TraceError('Invalid policy command')
    index = _number(value.get('index'), 3 if value['type'] == 'move' else 2)
    return {'type': value['type'], 'index': index}


def _battle_view(run):
    """Reject a wrong-side response before copying any own or opponent field."""
    if run.get('phase') != 'battle':
        return None
    view = run['battle']['view']
    if not isinstance(view, dict) or view.get('side') != 'p1':
        raise TraceError('Only a public p1 view is permitted')
    return view


def _boundary(run):
    """Battle HP/PP come from current own view, not stale expedition resources."""
    view = _battle_view(run)
    source = view.get('own', []) if view is not None else run.get('party', [])
    if len(source) > 3:
        raise TraceError('Unexpected public party size')
    party = []
    for index, row in enumerate(source):
        resource = row if view is not None else row['resource']
        moves = resource.get('moves', [])
        if len(moves) > 4:
            raise TraceError('Unexpected public move count')
        party.append({'slot': _number(row.get('slot', index), 2),
                      'active': bool(row.get('active')) if view is not None else False,
                      'hp': _number(resource['hp']), 'maxhp': _number(resource['maxhp']),
                      'moves': [{'id': _text(move['id']), 'pp': _number(move['pp']),
                                 'maxpp': _number(move['maxpp'])} for move in moves]})
    return {'source': 'battle-own' if view is not None else 'run-party', 'party': party}


def _enemy(run):
    view = _battle_view(run) or {}
    enemy = next((row for row in view.get('opponent', [])
                  if row.get('active') and row.get('revealed')), None)
    if enemy is None:
        return None
    types = enemy.get('types', [])
    if len(types) > 3 or any(kind not in TYPES for kind in types):
        raise TraceError('Invalid public typing')
    return {'species': _text(enemy['species']), 'types': list(types)}


def _offered(run):
    phase = run['phase']
    if phase == 'node' and run['node']['kind'] == 'rest':
        rows = [row for row in run.get('rest_options', []) if row.get('available') is not False]
    elif phase == 'node' and run['node']['kind'] == 'shop':
        rows = [row for row in run.get('shop', []) if not row.get('bought') and not row.get('sold')
                and row['price'] <= run['money']] + [{'id': 'leave'}]
    else:
        field = {'reward': 'rewards', 'path': 'paths', 'recruit': 'recruit_candidates'}.get(phase)
        rows = list(run.get(field, [])) if field else []
        if phase == 'recruit':
            rows.append({'id': 'skip'})
    if len(rows) > 64:
        raise TraceError('Unexpected public choice count')
    return [_text(row['id'], 128) for row in rows]


class PublicTraceService:
    """Observe only public responses/commands while run_case drives the service.

    No private service/worker methods or generic argument serialization are used.
    Each service attempt records one fixed-shape row, bounded by the input limit.
    """
    def __init__(self, service, policy, *, max_rows=MAX_DECISIONS):
        self.service, self.policy, self.max_rows = service, policy, max_rows
        self.rows, self.public = [], None
        self.public_heal_events = self.recovery_move_choices = 0
        self.terminal_events_unavailable = 0
        self._event_node, self._event_count = None, 0

    def _observe_heals(self, run):
        view = _battle_view(run)
        if view is None:
            return 0
        events = view.get('events', [])
        previous = self._event_count if self._event_node == run['node_index'] else 0
        if len(events) < previous:
            raise TraceError('Public event history shortened')
        count = sum(bool(re.match(r'^\|-heal\|p1[a-z]?: ', line)) for line in events[previous:])
        self._event_node, self._event_count = run['node_index'], len(events)
        self.public_heal_events += count
        return count

    def create(self, *args, **kwargs):
        self.public = self.service.create(*args, **kwargs)
        self._observe_heals(self.public)
        return self.public

    def _record(self, operation, args, invoke):
        if operation not in OPERATIONS or len(self.rows) >= self.max_rows:
            raise TraceError('Trace operation/size limit exceeded')
        run = self.public
        phase = run['phase']
        if phase not in PHASES:
            raise TraceError('Unsupported public phase')
        view = _battle_view(run) or {}
        request = view.get('request', {})
        legal = [action_to_command(int(index)) for index in np.flatnonzero(legal_mask(request, view))]
        offered = _offered(run)
        reason = 'economy'
        row = {'decision': len(self.rows) + 1, 'node_index': _number(run['node_index']),
               'phase': phase, 'turn': _number(view.get('turn', 0)), 'operation': operation,
               'offered_id': None, 'action': None, 'legal_choices': legal, 'offered_ids': offered,
               'reason': reason, 'before': _boundary(run), 'after': None,
               'public_enemy': _enemy(run), 'new_public_p1_heal_events': 0,
               'terminal_battle_events_unavailable': False,
               'recovery_move_selected': False, 'accepted': False}
        self.rows.append(row)
        try:
            if operation == 'battle/action':
                row['action'] = _command(args[0])
                reason = getattr(self.policy, 'last_reason', 'baseline')
                row['reason'] = reason if isinstance(reason, str) and (reason in SUSTAIN_REASONS or reason == 'baseline') else 'unspecified'
                if row['action'] not in legal:
                    error = TraceError('Policy returned an illegal action')
                    error.code = 'ILLEGAL_POLICY_ACTION'
                    raise error
                if row['action']['type'] == 'move':
                    move = request['active'][0]['moves'][row['action']['index']]
                    row['recovery_move_selected'] = move.get('id') in RECOVERY_MOVES
                    self.recovery_move_choices += int(row['recovery_move_selected'])
            elif operation in ('rest', 'shop', 'reward', 'path', 'recruit'):
                selected = _text(args[0], 128)
                if selected not in offered:
                    raise TraceError('Selected choice was not publicly offered')
                row['offered_id'] = selected
                if operation == 'recruit' and selected != 'skip':
                    row['replace_slot'] = _number(args[1], 2)
            else:
                row['reason'] = {'resume': 'wait', 'battle/start': 'battle-start',
                                 'battle/finish': 'battle-finish'}[operation]
            result = invoke()
            row['accepted'] = True
            row['after'] = _boundary(result)
            row['terminal_battle_events_unavailable'] = phase == 'battle' and result['phase'] != 'battle'
            self.terminal_events_unavailable += int(row['terminal_battle_events_unavailable'])
            row['new_public_p1_heal_events'] = self._observe_heals(result)
            self.public = result
            return result
        except Exception as exc:
            row['error'] = _error(exc)
            raise

    def perform(self, run_id, token, operation, revision, *args):
        return self._record(operation, args, lambda: self.service.perform(
            run_id, token, operation, revision, *args))

    def resume(self, run_id, token):
        return self._record('resume', (), lambda: self.service.resume(run_id, token))

    def close(self):
        self.service.close()

    def summary(self):
        return {'rows': len(self.rows), 'max_rows': self.max_rows,
                'public_p1_heal_events': self.public_heal_events,
                'heal_event_coverage': HEAL_EVENT_COVERAGE,
                'terminal_events_unavailable': self.terminal_events_unavailable,
                'recovery_move_choices': self.recovery_move_choices,
                'recovery_move_ids': list(RECOVERY_MOVES),
                'metric': 'Observed public heal event count and legal selected self-recovery moves; not healing amount'}


def _freeze_metadata(hashes):
    result = _git_metadata()
    repository = ROOT.parent
    required = list(hashes)
    try:
        tracked = subprocess.run(['git', '-c', 'safe.directory=' + repository.as_posix(),
            'ls-files', '--error-unmatch', '--', *required], cwd=repository,
            check=False, capture_output=True, timeout=10)
        result['measurement_sources_tracked'] = tracked.returncode == 0
        status = subprocess.run(['git', '-c', 'safe.directory=' + repository.as_posix(),
            'status', '--porcelain', '--untracked-files=normal'], cwd=repository,
            check=True, capture_output=True, text=True, timeout=10).stdout
        result['untracked_worktree_files'] = any(line.startswith('?? ') for line in status.splitlines())
    except (OSError, subprocess.SubprocessError):
        result['measurement_sources_tracked'] = None
        result['untracked_worktree_files'] = None
    result['freeze_ready'] = bool(result.get('revision') and result.get('tracked_worktree_dirty') is False
                                  and result['measurement_sources_tracked']
                                  and result['untracked_worktree_files'] is False
                                  and all(hashes.values()))
    return result


def _node_version():
    binary = shutil.which('node')
    if not binary:
        return None
    try:
        value = subprocess.run([binary, '--version'], check=True, capture_output=True,
                               text=True, timeout=5).stdout.strip()
        return value if re.fullmatch(r'v\d+\.\d+\.\d+', value) else None
    except (OSError, subprocess.SubprocessError):
        return None


def manifest(seeds=DEFAULT_SEEDS, *, max_decisions=MAX_DECISIONS, max_battle_turns=MAX_BATTLE_TURNS):
    plan = case_plan(seeds)
    if (type(max_decisions) is not int or not 1 <= max_decisions <= MAX_DECISIONS
            or type(max_battle_turns) is not int or not 1 <= max_battle_turns <= MAX_BATTLE_TURNS):
        raise ValueError('Smoke limits must be positive and no greater than the frozen limits')
    if BASELINE_VERSION != POLICY_IDS[0] or SUSTAIN_VERSION != POLICY_IDS[1]:
        raise ValueError('The frozen battle policy versions changed')
    economy = {key: copy.deepcopy(value) for key, value in PUBLIC_POLICY.items()
               if key not in ('battle', 'battle_rng_seed')}
    configs = ({'name': 'heuristic', 'version': BASELINE_VERSION, 'seed': 0}, copy.deepcopy(SUSTAIN_POLICY))
    return {'suite_id': SUITE_ID, 'seeds': list(dict.fromkeys(row['seed'] for row in plan)),
            'team_ids': list(TEAM_IDS), 'policy_ids': list(POLICY_IDS), 'modes': list(MODES),
            'case_order': 'seed, team, policy, mode; each in listed order', 'expected_cases': len(plan),
            'balance_profile': BALANCE_PROFILE, 'default_ivs': {stat: 31 for stat in ('hp', 'atk', 'def', 'spa', 'spd', 'spe')},
            'teams': [{'id': key, 'team': value, 'team_sha256': _digest(value)} for key, value in teams().items()],
            'policies': [{'id': key, 'config': value, 'config_sha256': _digest(value)}
                         for key, value in zip(POLICY_IDS, configs)],
            'economy': {'id': PUBLIC_POLICY['version'], 'config': economy, 'config_sha256': _digest(economy),
                        'legacy_public_policy_sha256': _digest(PUBLIC_POLICY)},
            'limits': {'decisions_per_case': max_decisions, 'battle_view_turn': max_battle_turns,
                       'trace_rows_per_case': max_decisions,
                       'decision_unit': 'public service operation attempts, including explicit resume',
                       'cutoff_rule': 'stop before an input at view.turn > limit'},
            'required_journal_version': 1}


def _group_summary(cases, expected):
    result = _summary(cases, expected)
    for metric in ('public_p1_heal_events', 'recovery_move_choices', 'terminal_events_unavailable'):
        result[metric] = sum(case.get('trace_summary', {}).get(metric, 0) for case in cases)
    result['heal_event_coverage'] = HEAL_EVENT_COVERAGE
    valid = [case for case in cases if case['status'] in ('complete', 'failed')]
    result['mean_completed_nodes_among_valid'] = (sum(case['completed_nodes'] for case in valid) / len(valid)) if valid else None
    result['node_reach_counts'] = {str(index): sum(index in case.get('reached_node_indexes', []) for case in valid)
                                   for index in range(20)}
    result['failure_node_counts'] = {str(index): sum(case.get('failure_node_index') == index for case in valid)
                                    for index in range(20)}
    return result


def run_benchmark(*, seeds=DEFAULT_SEEDS, max_decisions=MAX_DECISIONS,
                  max_battle_turns=MAX_BATTLE_TURNS, client_factory=BattleClient,
                  service_factory=RunService, progress=None, clock=time.monotonic,
                  require_clean=False):
    """Fresh memory DB/policy per case, one shared engine, strict final cleanup."""
    frozen = manifest(seeds, max_decisions=max_decisions, max_battle_turns=max_battle_turns)
    plan, builds = case_plan(frozen['seeds']), {row['id']: row['team'] for row in frozen['teams']}
    hashes = {name: _file_hash(ROOT.parent / name) if (ROOT.parent / name).is_file() else None
              for name in SOURCE_FILES}
    code = _freeze_metadata(hashes)
    if require_clean and not code['freeze_ready']:
        raise ValueError('Commit the complete measurement source and protocol with a clean tracked worktree first')
    started = clock()
    report = {'schema_version': 1, 'measurement': 'diverse-full-expedition-public-policy',
        'metadata': {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'manifest': frozen,
                     'manifest_sha256': _digest(frozen), 'file_sha256': hashes, 'code': code,
                     'python_version': sys.version.split()[0], 'numpy_version': np.__version__,
                     'node_version': _node_version(),
                     'content_version': json.loads((ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))['content_version'],
                     'resource_metric': 'Journal HP/PP net changes and current own battle resources; not total damage or healing',
                     'interpretation': 'Fixed automatic policy outcomes, not human completion rates or 3v3 AI win rates; paired seeds are not independent samples'},
        'cases': []}
    client = None
    try:
        client = client_factory()
        report['metadata']['engine'] = {key: copy.deepcopy(client.info[key]) for key in
                                       ('engine', 'version', 'schema_version', 'ruleset', 'format') if key in client.info}
        for entry in plan:
            service = traced = None
            case = {**entry, 'status': 'error', 'decisions': 0, 'cutoff_reason': None, 'error': None}
            try:
                policy = _policy(entry['policy_id'])
                service = service_factory(':memory:', client_factory=lambda: client, owns_client=False,
                                          new_run_balance_profile=BALANCE_PROFILE)
                traced = PublicTraceService(service, policy, max_rows=max_decisions)
                case.update(run_case(traced, builds[entry['team_id']], entry['seed'], entry['difficulty'],
                    max_decisions=max_decisions, max_battle_turns=max_battle_turns, policy=policy, clock=clock))
                if case.get('error'):
                    case['error'] = _error(case['error'])
                if case.get('balance_profile') != BALANCE_PROFILE and case['status'] != 'error':
                    case.update(status='error', cutoff_reason=None,
                                error={'type': 'BalanceError', 'code': 'BALANCE_PROFILE_UNAVAILABLE'})
            except Exception as exc:
                case.update(status='error', cutoff_reason=None, error=_error(exc))
            finally:
                if service is not None:
                    try:
                        service.close()
                    except Exception as exc:
                        case.update(status='error', cutoff_reason=None, error=_error(exc, 'CLEANUP_ERROR'))
                if traced is not None:
                    case['decision_trace'], case['trace_summary'] = traced.rows, traced.summary()
            case['requested_balance_profile'] = BALANCE_PROFILE
            report['cases'].append(case)
            if progress:
                progress(copy.deepcopy(case))
            if case['status'] == 'error':
                break
    except Exception as exc:
        report['batch_error'] = _error(exc, 'ENGINE_START_ERROR')
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                report['batch_error'] = _error(exc, 'CLEANUP_ERROR')
    report['elapsed_seconds'] = round(max(0, clock() - started), 6)
    report['summary'] = _group_summary(report['cases'], len(plan))
    report['summary']['batch_errors'] = int(bool(report.get('batch_error')))
    report['summary']['groups'] = [{'team_id': team, 'policy_id': policy, 'difficulty': mode,
        **_group_summary([case for case in report['cases'] if (case['team_id'], case['policy_id'], case['difficulty'])
                         == (team, policy, mode)], len(frozen['seeds']))}
        for team in TEAM_IDS for policy in POLICY_IDS for mode in MODES]
    report['exit_code'] = int(bool(report.get('batch_error')) or bool(report['summary']['incomplete_cases']))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--seeds', nargs='+', default=DEFAULT_SEEDS,
                        help='Separate explicit smoke seeds; default is the frozen ten-seed cohort')
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error('Report already exists; choose a new path')
    try:
        case_plan(args.seeds)
    except ValueError:
        parser.error('Use unique seeds of 1 to 128 characters')
    def progress(case):
        print(f"{case['team_id']} {case['policy_id']} {case['difficulty']} {case['seed']}: {case['status']}",
              file=sys.stderr, flush=True)
    try:
        report = run_benchmark(seeds=args.seeds, progress=progress,
                               require_clean=tuple(args.seeds) == DEFAULT_SEEDS)
    except ValueError:
        print('Measurement manifest/freeze is not ready', file=sys.stderr)
        return 2
    try:
        write_report(args.report, report)
    except OSError:
        print('Could not exclusively create report', file=sys.stderr)
        return 2
    print(json.dumps(report['summary'], ensure_ascii=False, sort_keys=True))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
