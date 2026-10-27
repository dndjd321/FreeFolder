"""Fixed-model paired confirmation after the original test cohort.

Each independent pair has fixed teams and a fixed engine seed. Policies exchange
p1/p2 ownership in the second game, so each policy controls each team and seat
once. No model update or selection occurs here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import time

import numpy as np
import torch

from battle_service.client import BattleClient
from .cli import ensure_held_out, runtime_metadata, save_json, wilson
from .dataset import TeamDataset, ensure_disjoint_training_data, team_fingerprint
from .observation import command_to_action, legal_mask
from .policies import BaselinePolicy, POLICY_VERSION
from .policy import PolicyAgent


def paired_bootstrap_interval(pair_win_rates, *, seed=3_000_000, samples=20_000):
    values = np.asarray(pair_win_rates, dtype=np.float64)
    rng = np.random.default_rng(seed)
    means = []
    for start in range(0, samples, 1000):
        batch = min(1000, samples - start)
        means.extend(rng.choice(values, size=(batch, len(values)), replace=True).mean(axis=1).tolist())
    return [float(x) for x in np.percentile(means, [2.5, 97.5])]


def _play(client, teams, engine_seed, policy_a, policy_b, side_a, *, max_turns=100, max_decisions=200):
    policies = {'p1': policy_a if side_a == 'p1' else policy_b,
                'p2': policy_b if side_a == 'p1' else policy_a}
    state = client.create(teams, seed=engine_seed)
    bid = state['battle_id']
    decisions = {'p1': 0, 'p2': 0}
    latencies = {'a': [], 'b': []}
    invalid = 0
    truncated = False
    try:
        while not state['ended']:
            if state['turn'] >= max_turns or max(decisions.values()) >= max_decisions:
                truncated = True
                break
            commands = []
            # Derive both decisions from permitted pre-choice projections.
            for side in ('p1', 'p2'):
                view = state['views'][side]
                mask = legal_mask(view['request'], view)
                if not mask.any():
                    continue
                before = time.perf_counter()
                command = policies[side].predict(view['request'], view)
                latencies['a' if side == side_a else 'b'].append((time.perf_counter() - before) * 1000)
                action = command_to_action(command)
                if not mask[action]:
                    invalid += 1
                    raise ValueError('Paired policy proposed an illegal action')
                commands.append((side, command))
            if not commands:
                raise RuntimeError('Paired battle has no legal request and is not ended')
            for side, command in commands:
                if state['ended']:
                    break
                state = client.choose(bid, side, command)
                decisions[side] += 1
        winner = state['winner'] if state['ended'] else None
        result = {'policy_a_side': side_a, 'winner': winner,
                  'policy_a_win': winner == side_a,
                  'policy_a_loss': winner is not None and winner != side_a,
                  'natural_draw': state['ended'] and winner is None,
                  'truncated': truncated, 'turns': state['turn'],
                  'decisions_by_side': decisions, 'invalid_actions': invalid}
        return result, latencies
    finally:
        client.close_battle(bid)


def paired_comparison(client, policy_a, policy_b, *, dataset, pairs, seed_start, progress=None):
    ensure_held_out(policy_a, (seed_start, seed_start + pairs - 1))
    ensure_held_out(policy_b, (seed_start, seed_start + pairs - 1))
    ensure_disjoint_training_data(policy_a, dataset)
    ensure_disjoint_training_data(policy_b, dataset)
    records, all_latencies, turns = [], {'a': [], 'b': []}, []
    wins = losses = natural_draws = truncations = invalid = 0
    started = time.perf_counter()
    for pair in range(pairs):
        seed = seed_start + pair
        teams = dataset.teams(seed)
        rng = random.Random(seed)
        engine_seed = [rng.randrange(65536) for _ in range(4)]
        record = {'pair_index': pair, 'data_seed': seed, 'engine_seed': engine_seed,
                  'team_fingerprints': {side: team_fingerprint(team) for side, team in teams.items()}, 'games': []}
        for side_a in ('p1', 'p2'):
            game, latencies = _play(client, teams, engine_seed, policy_a, policy_b, side_a)
            record['games'].append(game)
            wins += game['policy_a_win']; losses += game['policy_a_loss']
            natural_draws += game['natural_draw']; truncations += game['truncated']; invalid += game['invalid_actions']
            turns.append(game['turns'])
            for key in all_latencies:
                all_latencies[key].extend(latencies[key])
        records.append(record)
        if progress and (pair + 1) % 25 == 0:
            progress({'pairs': pair + 1, 'games': (pair + 1) * 2, 'wins': wins})
    games = pairs * 2
    pair_win_rates = [sum(g['policy_a_win'] for g in r['games']) / 2 for r in records]
    return {'games': games, 'independent_pairs': pairs, 'wins': wins, 'losses': losses,
            'natural_draws': natural_draws, 'truncations': truncations, 'invalid_actions': invalid,
            'win_rate': wins / games, 'win_rate_95ci_wilson_games': wilson(wins, games),
            'win_rate_95ci_pair_bootstrap': paired_bootstrap_interval(pair_win_rates, seed=seed_start),
            'mean_turns': float(np.mean(turns)),
            'inference': {key: {'mean_ms': float(np.mean(values)), 'p95_ms': float(np.percentile(values, 95)), 'calls': len(values)}
                          for key, values in all_latencies.items()},
            'seconds': time.perf_counter() - started, 'pairs': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='training/models/expedition-v2.pt')
    parser.add_argument('--previous', default='training/models/bootstrap.pt')
    parser.add_argument('--pairs', type=int, default=250)
    parser.add_argument('--seed-start', type=int, default=3_000_000)
    parser.add_argument('--report', default='training/benchmarks/fairness-confirmatory-20261027.json')
    args = parser.parse_args()
    if args.pairs < 1 or args.seed_start < 3_000_000:
        parser.error('Use positive pairs and an independent seed range at or above 3000000')
    if Path(args.report).exists():
        parser.error('The confirmatory report already exists; do not silently overwrite a fixed evaluation')
    torch.set_num_threads(1)
    dataset = TeamDataset('test')
    candidate, previous = PolicyAgent.load(args.model), PolicyAgent.load(args.previous)
    comparisons = [('candidate_vs_bootstrap', candidate, previous),
                   ('heuristic_v2_vs_legacy', BaselinePolicy('heuristic'), BaselinePolicy('legacyheuristic')),
                   ('heuristic_v2_mirror', BaselinePolicy('heuristic'), BaselinePolicy('heuristic'))]
    results = {}
    with BattleClient() as client:
        for name, policy_a, policy_b in comparisons:
            results[name] = paired_comparison(client, policy_a, policy_b, dataset=dataset, pairs=args.pairs, seed_start=args.seed_start,
                                              progress=lambda value: print(json.dumps({'comparison': name, **value}), flush=True))
            print(json.dumps({'completed': name, **{k: v for k, v in results[name].items() if k != 'pairs'}}), flush=True)
    candidate_result = results['candidate_vs_bootstrap']
    report = {'kind': 'paired-seat-and-team-confirmatory-evaluation', 'runtime': runtime_metadata(),
              'seed_start': args.seed_start, 'seed_end': args.seed_start + args.pairs - 1,
              'pairs_per_comparison': args.pairs, 'games_per_comparison': args.pairs * 2,
              'total_games': args.pairs * 2 * len(comparisons), 'dataset': dataset.manifest(), 'policy_version': POLICY_VERSION,
              'model_sha256': hashlib.sha256(Path(args.model).read_bytes()).hexdigest(),
              'previous_sha256': hashlib.sha256(Path(args.previous).read_bytes()).hexdigest(),
              'pairing': 'For each independent data seed the two physical teams and four-integer engine seed stay fixed. Policy A owns p1 in game 0 and p2 in game 1; both policies therefore use each team and seat once. Both actor decisions use their own permitted view.',
              'uncertainty': 'Games within a pair are dependent. Report both descriptive 500-game Wilson intervals and a 20000-resample percentile bootstrap over the 250 independent pair win rates; use the pair-level lower bound as an additional guard.',
              'frozen_models_and_policies': True, 'model_or_budget_tuned_from_this_report': False,
              'candidate_bootstrap_lower_bounds_above_half': candidate_result['win_rate_95ci_wilson_games'][0] > .5 and candidate_result['win_rate_95ci_pair_bootstrap'][0] > .5,
              'results': results}
    save_json(args.report, report)


if __name__ == '__main__':
    main()
