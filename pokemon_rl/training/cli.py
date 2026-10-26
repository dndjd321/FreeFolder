"""Train/evaluate locally: python -m training.cli --help."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import torch

from .environment import ShowdownBattleEnv
from .observation import command_to_action
from .policies import BaselinePolicy
from .policy import PolicyAgent, model_metadata

TRAIN_SEED_START = 10_000
EVAL_SEED_START = 1_000_000


def runtime_metadata():
    return {"python": platform.python_version(), "torch": str(torch.__version__),
            "numpy": np.__version__, "platform": platform.system(),
            "architecture": platform.machine(), "torch_threads": torch.get_num_threads()}


def train(args):
    torch.set_num_threads(1)
    agent = PolicyAgent.load(args.resume) if args.resume else PolicyAgent(seed=args.seed)
    previous_path = Path(args.model).with_name("previous.pt")
    agent.save(previous_path, training={"kind": "pre-training-checkpoint"})
    previous = PolicyAgent(seed=args.seed + 1)
    previous.network.load_state_dict(agent.network.state_dict())
    opponent = previous if args.self_play else BaselinePolicy("heuristic", seed=args.seed)
    environment = ShowdownBattleEnv(opponent=opponent)
    episode_seed = TRAIN_SEED_START + args.seed * 10_000
    observation, info = environment.reset(seed=episode_seed)
    rollout, results, episodes = [], [], []
    started = time.perf_counter()
    try:
        for step in range(args.steps):
            mask = info["action_mask"]
            action, log_prob, value = agent.select(observation, mask)
            next_obs, reward, terminated, truncated, next_info = environment.step(action)
            rollout.append({"observation": observation.copy(), "mask": mask.copy(), "action": action,
                            "log_prob": log_prob, "value": value, "reward": reward, "done": terminated or truncated})
            observation, info = next_obs, next_info
            if terminated or truncated:
                episodes.append({"winner": info["winner"], "turns": info["turn"], "truncated": truncated})
                episode_seed += 1
                observation, info = environment.reset(seed=episode_seed)
            if len(rollout) >= args.rollout or step + 1 == args.steps:
                bootstrap = agent.select(observation, info["action_mask"], deterministic=True)[2]
                results.append(agent.update(rollout, bootstrap=bootstrap))
                rollout.clear()
                if args.self_play:
                    previous.network.load_state_dict(agent.network.state_dict())
                print(json.dumps({"steps": step + 1, "episodes": len(episodes), "loss": results[-1]["loss"]}), flush=True)
        summary = {"kind": "training-smoke", "metadata": model_metadata(), "runtime": runtime_metadata(), "steps": args.steps,
                   "seed_start": TRAIN_SEED_START + args.seed * 10_000, "seed_end": episode_seed,
                   "self_play": args.self_play, "opponent": "previous-checkpoint" if args.self_play else "heuristic",
                   "episodes": len(episodes), "wins": sum(e["winner"] == "p1" for e in episodes),
                   "losses": sum(e["winner"] == "p2" for e in episodes), "draws_or_truncated": sum(e["winner"] is None for e in episodes),
                   "invalid_actions": environment.invalid_actions, "seconds": time.perf_counter() - started,
                   "last_loss": results[-1]["loss"], "previous_checkpoint": previous_path.name,
                   "note": "Pipeline smoke training only; no general playing-strength claim."}
        agent.training_seed_ranges.append([summary["seed_start"], summary["seed_end"]])
        agent.save(args.model, training=summary)
        save_json(args.report, summary)
        print(json.dumps(summary), flush=True)
    finally:
        environment.close()


def wilson(wins, games):
    if not games:
        return [0.0, 1.0]
    p, z = wins / games, 1.96
    denominator = 1 + z * z / games
    center = (p + z * z / (2 * games)) / denominator
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denominator
    return [center - half, center + half]


def evaluate(args):
    torch.set_num_threads(1)
    policy = PolicyAgent.load(args.model) if args.model else BaselinePolicy(args.policy, seed=args.seed)
    evaluation_range = (EVAL_SEED_START + args.seed * 10_000, EVAL_SEED_START + args.seed * 10_000 + args.games - 1)
    ensure_held_out(policy, evaluation_range)
    names = args.opponents.split(',')
    results = {}
    for name in names:
        opponent = PolicyAgent.load(args.previous) if name == "previous" and args.previous else BaselinePolicy(name, seed=args.seed)
        ensure_held_out(opponent, evaluation_range)
        environment = ShowdownBattleEnv(opponent=opponent)
        wins = losses = draws = invalid = truncations = 0
        turns, latencies = [], []
        started = time.perf_counter()
        try:
            for game in range(args.games):
                _, info = environment.reset(seed=EVAL_SEED_START + args.seed * 10_000 + game)
                while True:
                    start = time.perf_counter()
                    action = policy.predict(info["view"]["request"], info["view"])
                    latencies.append((time.perf_counter() - start) * 1000)
                    _, _, ended, truncated, info = environment.step(command_to_action(action))
                    if ended or truncated:
                        wins += info["winner"] == "p1"
                        losses += info["winner"] == "p2"
                        draws += info["winner"] is None
                        truncations += bool(truncated)
                        invalid += info["invalid_actions"]
                        turns.append(info["turn"])
                        break
                if (game + 1) % 50 == 0:
                    print(json.dumps({"opponent": name, "games": game + 1, "wins": wins}), flush=True)
            results[name] = {"games": args.games, "wins": wins, "losses": losses, "draws": draws,
                             "win_rate": wins / args.games, "win_rate_95ci": wilson(wins, args.games),
                             "invalid_actions": invalid, "truncations": truncations, "mean_turns": float(np.mean(turns)),
                             "mean_inference_ms": float(np.mean(latencies)), "p95_inference_ms": float(np.percentile(latencies, 95)),
                             "seconds": time.perf_counter() - started}
        finally:
            environment.close()
    summary = {"kind": "held-out-seed-evaluation", "policy": "ppo" if args.model else args.policy,
               "runtime": runtime_metadata(),
               "model_sha256": hashlib.sha256(Path(args.model).read_bytes()).hexdigest() if args.model else None,
               "metadata": model_metadata(), "seed_start": EVAL_SEED_START + args.seed * 10_000,
               "seed_end": EVAL_SEED_START + args.seed * 10_000 + args.games - 1,
               "team_pool_species": 6, "results": results,
               "note": "Evaluation seeds are disjoint from training. Six-species development pool; not a full-Pokedex strength benchmark."}
    save_json(args.report, summary)
    print(json.dumps(summary), flush=True)


def save_json(path, value):
    if path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_held_out(policy, evaluation_range):
    for start, end in getattr(policy, "training_seed_ranges", []):
        if max(start, evaluation_range[0]) <= min(end, evaluation_range[1]):
            raise ValueError("Evaluation seed range overlaps this checkpoint's training seeds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("train")
    p.add_argument("--steps", type=int, default=3000)
    p.add_argument("--rollout", type=int, default=256)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--self-play", action="store_true")
    p.add_argument("--resume")
    p.add_argument("--model", default="training/runtime/latest.pt")
    p.add_argument("--report", default="training/runtime/train-report.json")
    p.set_defaults(run=train)
    p = commands.add_parser("evaluate")
    p.add_argument("--games", type=int, default=500, help="Games per opponent")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--model")
    p.add_argument("--policy", choices=("random", "maxpower", "heuristic"), default="heuristic")
    p.add_argument("--opponents", default="random,maxpower,heuristic")
    p.add_argument("--previous", help="Checkpoint path for opponent named previous")
    p.add_argument("--report", default="training/runtime/evaluation.json")
    p.set_defaults(run=evaluate)
    args = parser.parse_args()
    if getattr(args, "steps", 1) < 1 or getattr(args, "games", 1) < 1 or getattr(args, "rollout", 1) < 1:
        parser.error("Steps, games and rollout size must be positive")
    if args.command == "evaluate" and "previous" in args.opponents.split(',') and not args.previous:
        parser.error("Opponent previous requires --previous CHECKPOINT")
    args.run(args)


if __name__ == "__main__":
    main()
