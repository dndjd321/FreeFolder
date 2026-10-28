"""Measure full expeditions without changing resources, progression, or wins.

Run from pokemon_rl: ``python -m balance.cli --report PATH``. Defaults are
20 fixed seeds in both modes. This measures this automatic public-information
policy, not human completion rates or fresh-team 3v3 AI evaluation win rates.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from battle_service.client import BattleClient
from services.run_service import RunService
from training.policies import BaselinePolicy, POLICY_VERSION


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEEDS = tuple(f'run-balance-v1-{index:03d}' for index in range(20))
DEFAULT_MODES = ('normal', 'hard')
BALANCE_PROFILES = ('balance-v1', 'normal-rescue-v2')
BALANCE_POLICY_VERSION = 'public-economy-v1'
PUBLIC_POLICY = {
    'version': BALANCE_POLICY_VERSION, 'battle': POLICY_VERSION, 'battle_rng_seed': 0,
    'hp_needed_below': .65, 'pp_needed_below': .35,
    'pp_needed_if_any_living_member_has_no_pp': True, 'relic_gold_reserve': 40,
    'reward': 'dead:recruit; needed:heal,pp; otherwise:relic,money,first-offered',
    'rest': 'dead:revive; low-pp:pp; living-status:cure; otherwise:heal',
    'shop': 'affordable dead:revive,recruit; needed:heal,pp; reserve-40:relic; leave',
    'recruit': 'first-public-candidate replaces lowest-current-absolute-HP slot, then earliest slot',
    'path': 'first-public-offered', 'reroll': 'never', 'party_order': 'unchanged',
    'information': 'run public state and p1 battle view only; no p2 view or private enemy build',
}


class BalanceError(RuntimeError):
    code = 'BALANCE_ERROR'


class JournalUnavailable(BalanceError):
    code = 'JOURNAL_UNAVAILABLE'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recommended_team():
    content = json.loads((ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))
    pool = {row['species']: row for row in content['recruit_pool']}
    return copy.deepcopy([pool[species] for species in ('Charizard', 'Garchomp', 'Clefable')])


def _needs(run):
    living = [row.get('resource', {}) for row in run.get('party', []) if row.get('resource', {}).get('hp', 0) > 0]
    hp, maxhp = sum(row.get('hp', 0) for row in living), sum(row.get('maxhp', 0) for row in living)
    moves = [move for row in living for move in row.get('moves', [])]
    pp, maxpp = sum(move.get('pp', 0) for move in moves), sum(move.get('maxpp', 0) for move in moves)
    exhausted = any(not any(move.get('pp', 0) > 0 for move in row.get('moves', [])) for row in living)
    return {'dead': any(row.get('resource', {}).get('hp', 0) == 0 for row in run.get('party', [])),
            'hp': bool(maxhp and hp / maxhp < PUBLIC_POLICY['hp_needed_below']),
            'pp': bool(maxpp and pp / maxpp < PUBLIC_POLICY['pp_needed_below']) or exhausted,
            'status': any(row.get('status') for row in living)}


def choose_operation(run, policy, decision):
    """Choose offered IDs or legal p1 battle commands, without mutating a run."""
    phase, needs = run['phase'], _needs(run)
    if phase == 'node':
        kind = run['node']['kind']
        if kind in ('battle', 'elite', 'boss'):
            return 'battle/start', ()
        if kind == 'rest':
            available = {row['id'] for row in run.get('rest_options', []) if row.get('available') is not False}
            for wanted, condition in [('revive', needs['dead']), ('pp', needs['pp']),
                                       ('cure', needs['status']), ('heal', True)]:
                if condition and wanted in available:
                    return 'rest', (wanted,)
        elif kind == 'shop':
            offers = [row for row in run.get('shop', []) if not row.get('bought') and not row.get('sold')
                      and row['price'] <= run['money']]
            for wanted, condition in [('revive', needs['dead']), ('recruit', needs['dead']),
                                       ('heal', needs['hp']), ('pp', needs['pp']), ('relic', True)]:
                offer = next((row for row in offers if row['kind'] == wanted
                              and (wanted != 'relic' or run['money'] - row['price'] >= PUBLIC_POLICY['relic_gold_reserve'])), None)
                if condition and offer:
                    return 'shop', (offer['id'],)
            return 'shop', ('leave',)
    elif phase == 'battle':
        battle = run['battle']
        view = battle['view']
        if view.get('ended'):
            return 'battle/finish', ()
        if not view.get('request') or view['request'].get('wait'):
            return 'resume', ()
        action = policy.predict(view['request'], view)
        if action is None:
            raise BalanceError('No legal p1 action was returned')
        return 'battle/action', (action, battle['revision'], f'run-balance-v1-{decision}')
    elif phase == 'reward':
        rewards = run.get('rewards', [])
        for wanted, condition in [('recruit', needs['dead']), ('heal', needs['hp']), ('pp', needs['pp']),
                                   ('relic', True), ('money', True)]:
            reward = next((row for row in rewards if row['kind'] == wanted), None)
            if condition and reward:
                return 'reward', (reward['id'],)
        if rewards:
            return 'reward', (rewards[0]['id'],)
    elif phase == 'path' and run.get('paths'):
        return 'path', (run['paths'][0]['id'],)
    elif phase == 'recruit':
        candidates, party = run.get('recruit_candidates', []), run.get('party', [])
        if candidates and party:
            slot = min(range(len(party)), key=lambda index: (party[index]['resource']['hp'], index))
            return 'recruit', (candidates[0]['id'], slot)
        return 'recruit', ('skip', 0)
    raise BalanceError('The public run state has no supported offered choice')


def _capture(run, observed):
    journal = run.get('journal')
    if not isinstance(journal, dict) or journal.get('available') is not True:
        raise JournalUnavailable('A public version-1 journal is required before measurement')
    if journal.get('version') != 1 or journal.get('complete_history') is not True or journal.get('from_node') != 0:
        raise JournalUnavailable('A newly created, fully covered journal is required')
    for event in journal.get('events', []):
        sequence = event['sequence']
        if sequence in observed and observed[sequence] != event:
            raise JournalUnavailable('Previously observed journal events changed')
        observed[sequence] = copy.deepcopy(event)
    total = journal.get('total_events')
    if type(total) is not int or total < 1 or sorted(observed) != list(range(1, total + 1)):
        raise JournalUnavailable('The measured journal contains missing events')
    return {key: copy.deepcopy(journal[key]) for key in
            ('version', 'from_node', 'complete_history', 'total_events', 'dropped_events', 'counts', 'totals')}


def _resources(measured):
    return {key: measured[key] for key in ('hp', 'maxhp', 'pp', 'maxpp', 'fainted', 'money')}


def _node_summaries(events):
    nodes = {}
    for event in events:
        index = event['node_index']
        if index not in nodes:
            measured = event['before'] if event['before']['node_index'] == index else event['after']
            nodes[index] = {'node_index': index, 'node_label': event['node_label'],
                            'first_resources': _resources(measured), 'last_resources': _resources(event['after']),
                            'events': 0, 'battle_results': []}
        row = nodes[index]
        row['events'] += 1
        row['last_resources'] = _resources(event['after'])
        if event['kind'] == 'battle_end':
            row['battle_results'].append({key: event['details'][key] for key in ('winner', 'turns')})
    for row in nodes.values():
        for resource in ('hp', 'pp', 'money'):
            row[resource + '_net_change'] = row['last_resources'][resource] - row['first_resources'][resource]
    return [nodes[index] for index in sorted(nodes)]


def run_case(service, team, seed, mode, *, max_decisions=1000, max_battle_turns=200,
             policy=None, clock=time.monotonic):
    """Execute a single fresh run through public service methods and views."""
    if type(max_decisions) is not int or max_decisions < 1 or type(max_battle_turns) is not int or max_battle_turns < 1:
        raise ValueError('Measurement limits must be positive integers')
    policy = policy or BaselinePolicy('heuristic', seed=0)
    started = clock()
    result = {'seed': seed, 'difficulty': mode, 'status': 'error', 'decisions': 0,
              'cutoff_reason': None, 'error': None, 'journal': None, 'balance_profile': None}
    run, observed = None, {}
    try:
        run = service.create(copy.deepcopy(team), seed=seed, difficulty=mode)
        result['balance_profile'] = run.get('balance_profile')
        result['journal'] = _capture(run, observed)
        while run['phase'] not in ('complete', 'failed'):
            if result['decisions'] >= max_decisions:
                result.update(status='cutoff', cutoff_reason='max_decisions')
                break
            if run['phase'] == 'battle' and not run['battle']['view'].get('ended'):
                if run['battle']['view'].get('turn', 0) > max_battle_turns:
                    result.update(status='cutoff', cutoff_reason='max_battle_turns')
                    break
            operation, args = choose_operation(run, policy, result['decisions'] + 1)
            result['decisions'] += 1
            if operation == 'resume':
                run = service.resume(run['run_id'], run['token'])
            else:
                run = service.perform(run['run_id'], run['token'], operation, run.get('run_revision', 0), *args)
            result['journal'] = _capture(run, observed)
            if run.get('balance_profile') != result['balance_profile']:
                raise BalanceError('A run changed its resolved balance profile during measurement')
        else:
            result['status'] = run['phase']
        if result['status'] == 'failed' and not any(event['kind'] == 'battle_end'
                and event['details']['winner'] != 'p1' for event in observed.values()):
            raise JournalUnavailable('A failed run must have a recorded battle result')
    except Exception as exc:
        result.update(status='error', cutoff_reason=None,
                      error={'type': type(exc).__name__, 'code': getattr(exc, 'code', 'RUN_ERROR')})
    result['elapsed_seconds'] = round(max(0, clock() - started), 6)
    result['policy_calls'] = getattr(policy, 'calls', None)
    result['last_phase'] = run.get('phase') if run else None
    result['last_node_index'] = run.get('node_index') if run else None
    result['completed_nodes'] = run.get('completed_nodes', 0) if run else 0
    result['events'] = [observed[index] for index in sorted(observed)]
    result['nodes'] = _node_summaries(result['events'])
    result['reached_node_indexes'] = [row['node_index'] for row in result['nodes']]
    losses = [event for event in result['events'] if event['kind'] == 'battle_end' and event['details']['winner'] != 'p1']
    result['failure_node_index'] = losses[-1]['node_index'] if result['status'] == 'failed' else None
    return result


def _git_metadata():
    repository = ROOT.parent
    prefix = ['git', '-c', 'safe.directory=' + repository.as_posix()]
    try:
        revision = subprocess.run(prefix + ['rev-parse', 'HEAD'], cwd=repository, check=True,
                                  capture_output=True, text=True, timeout=10).stdout.strip()
        status = subprocess.run(prefix + ['status', '--porcelain', '--untracked-files=no'], cwd=repository, check=True,
                                capture_output=True, text=True, timeout=10).stdout
        return {'revision': revision, 'tracked_worktree_dirty': bool(status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {'revision': None, 'tracked_worktree_dirty': None}


def _summary(cases, expected):
    counts = {status: sum(case['status'] == status for case in cases)
              for status in ('complete', 'failed', 'cutoff', 'error')}
    valid = counts['complete'] + counts['failed']
    return {**counts, 'expected_cases': expected, 'executed_cases': len(cases), 'valid_cases': valid,
            'not_run': expected - len(cases), 'incomplete_cases': expected - valid,
            'completion_rate_among_valid': counts['complete'] / valid if valid else None}


def run_benchmark(*, seeds=DEFAULT_SEEDS, modes=DEFAULT_MODES, team=None,
                  max_decisions=1000, max_battle_turns=200, client_factory=BattleClient,
                  service_factory=RunService, progress=None, clock=time.monotonic,
                  balance_profile=None):
    """Use one shared engine, one fresh memory DB per case, and strict cleanup.

    Engine/run errors stop the batch; remaining cases are explicitly not_run.
    Cutoffs continue but are excluded, alongside errors, from valid outcomes.
    No run is patched, forced to win, or continued with a fallback policy.
    """
    seeds, modes = tuple(seeds), tuple(modes)
    if (not seeds or any(not isinstance(seed, str) or not 1 <= len(seed) <= 128 for seed in seeds)
            or len(set(seeds)) != len(seeds) or not modes or any(mode not in DEFAULT_MODES for mode in modes)
            or len(set(modes)) != len(modes)):
        raise ValueError('Provide unique nonempty seeds and supported modes')
    if type(max_decisions) is not int or max_decisions < 1 or type(max_battle_turns) is not int or max_battle_turns < 1:
        raise ValueError('Measurement limits must be positive integers')
    if balance_profile is not None and balance_profile not in BALANCE_PROFILES:
        raise ValueError('Choose a supported explicit balance profile')
    team = recommended_team() if team is None else copy.deepcopy(team)
    started = clock()
    files = ['balance/cli.py', 'training/policies.py', 'training/observation.py',
             'services/run_service.py', 'services/run_journal.py', 'services/run_balance.py',
             'battle_service/engine.cjs', 'battle_service/package-lock.json']
    hashes = {name: _file_hash(ROOT / name) for name in files}
    content = json.loads((ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))
    report = {'schema_version': 1, 'measurement': 'full-expedition-public-policy',
              'metadata': {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'seeds': list(seeds),
                           'modes': list(modes), 'team': team, 'team_sha256': _digest(team),
                           'requested_balance_profile': balance_profile,
                           'case_order': 'seed-major, modes in listed order', 'required_journal_version': 1,
                           'python_version': sys.version.split()[0],
                           'policy': copy.deepcopy(PUBLIC_POLICY), 'policy_sha256': _digest({'policy': PUBLIC_POLICY, 'files': hashes}),
                           'file_sha256': hashes, 'content_version': content['content_version'],
                           'content_sha256': _file_hash(ROOT / 'content/roguelike.json'), 'code': _git_metadata(),
                           'limits': {'decisions_per_case': max_decisions, 'battle_view_turn': max_battle_turns,
                                      'decision_unit': 'public service operation attempts, including explicit resume',
                                      'cutoff_rule': 'stop before an input at view.turn > limit'},
                           'resource_metric': 'HP/PP net changes at journal boundaries; not total damage or in-battle healing',
                           'interpretation': 'Results describe this fixed automatic policy, not human completion rates or 3v3 AI win rates'},
              'cases': []}
    client = None
    try:
        client = client_factory()
        report['metadata']['engine'] = {key: copy.deepcopy(client.info[key]) for key in
                                       ('engine', 'version', 'schema_version', 'ruleset', 'format') if key in client.info}
        stop = False
        for seed in seeds:
            for mode in modes:
                service = None
                try:
                    options = {'client_factory': lambda: client, 'owns_client': False}
                    if balance_profile is not None:
                        options['new_run_balance_profile'] = balance_profile
                    service = service_factory(':memory:', **options)
                    case = run_case(service, team, seed, mode, max_decisions=max_decisions,
                                    max_battle_turns=max_battle_turns, clock=clock)
                except Exception as exc:
                    case = {'seed': seed, 'difficulty': mode, 'status': 'error', 'decisions': 0,
                            'error': {'type': type(exc).__name__, 'code': getattr(exc, 'code', 'RUN_ERROR')}}
                finally:
                    if service is not None:
                        try:
                            service.close()
                        except Exception as exc:
                            case.update(status='error', error={'type': type(exc).__name__, 'code': 'CLEANUP_ERROR'})
                case['requested_balance_profile'] = balance_profile
                case.setdefault('balance_profile', None)
                if balance_profile is not None and case['balance_profile'] is None and case['status'] != 'error':
                    case.update(status='error', cutoff_reason=None,
                                error={'type': 'BalanceError', 'code': 'BALANCE_PROFILE_UNAVAILABLE'})
                report['cases'].append(case)
                if progress:
                    progress(copy.deepcopy(case))
                if case['status'] == 'error':
                    stop = True
                    break
            if stop:
                break
    except Exception as exc:
        report['batch_error'] = {'type': type(exc).__name__, 'code': getattr(exc, 'code', 'ENGINE_START_ERROR')}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                report['batch_error'] = {'type': type(exc).__name__, 'code': 'CLEANUP_ERROR'}
    report['elapsed_seconds'] = round(max(0, clock() - started), 6)
    report['summary'] = _summary(report['cases'], len(seeds) * len(modes))
    report['summary']['batch_errors'] = int(bool(report.get('batch_error')))
    report['summary']['by_mode'] = {mode: _summary([case for case in report['cases'] if case['difficulty'] == mode], len(seeds)) for mode in modes}
    report['exit_code'] = int(bool(report.get('batch_error')) or bool(report['summary']['incomplete_cases']))
    return report


def write_report(path, report):
    """Create the requested artifact exclusively; never replace an old report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--seeds', nargs='+', default=DEFAULT_SEEDS, help='Explicit small seed list for checks; default is the frozen 20-seed cohort')
    parser.add_argument('--max-decisions', type=int, default=1000)
    parser.add_argument('--max-battle-turns', type=int, default=200)
    parser.add_argument('--balance-profile', choices=BALANCE_PROFILES, default=None,
                        help='Explicit per-run game rules for a controlled comparison; omitted uses service default')
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error('Report already exists; choose a new path')
    if (args.max_decisions < 1 or args.max_battle_turns < 1 or len(set(args.seeds)) != len(args.seeds)
            or any(not 1 <= len(seed) <= 128 for seed in args.seeds)):
        parser.error('Use positive limits and unique seeds of 1 to 128 characters')
    def progress(case):
        print(f"{case['difficulty']} {case['seed']}: {case['status']} ({case['decisions']} decisions)", file=sys.stderr, flush=True)
    report = run_benchmark(seeds=args.seeds, max_decisions=args.max_decisions,
                           max_battle_turns=args.max_battle_turns, progress=progress,
                           balance_profile=args.balance_profile)
    try:
        write_report(args.report, report)
    except OSError as exc:
        print('Could not create report: ' + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report['summary'], ensure_ascii=False, sort_keys=True))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
