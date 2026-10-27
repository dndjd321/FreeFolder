"""Train/evaluate locally: python -m training.cli --help."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import torch

from .dataset import TeamDataset, ensure_disjoint_training_data
from .environment import ShowdownBattleEnv
from .observation import command_to_action
from .policies import BaselinePolicy, POLICY_VERSION
from .policy import PolicyAgent, model_metadata

TRAIN_SEED_START = 10_000
VALIDATION_SEED_START = 1_000_000
EVAL_SEED_START = 2_000_000


def runtime_metadata():
    return {"python": platform.python_version(), "torch": str(torch.__version__),
            "numpy": np.__version__, "platform": platform.system(),
            "architecture": platform.machine(), "torch_threads": torch.get_num_threads()}


def save_json(path, value):
    if path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_held_out(policy, evaluation_range):
    for start, end in getattr(policy, "training_seed_ranges", []):
        if max(start, evaluation_range[0]) <= min(end, evaluation_range[1]):
            raise ValueError("Evaluation seed range overlaps this checkpoint's training seeds")


def wilson(wins, games):
    if not games:
        return [0.0, 1.0]
    p, z = wins / games, 1.96
    denominator = 1 + z * z / games
    center = (p + z * z / (2 * games)) / denominator
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denominator
    return [center - half, center + half]


def validation_score(result):
    # A time/decision limit is an incomplete game, not evidence of a draw.
    natural_draws = max(0, result['draws'] - result['truncations'])
    return (result['wins'] + .5 * natural_draws) / result['games']


def run_evaluation(policy, opponent, *, dataset, seed_start, games, client=None, progress=None):
    ensure_held_out(policy, (seed_start, seed_start + games - 1))
    ensure_held_out(opponent, (seed_start, seed_start + games - 1))
    if dataset is not None:
        ensure_disjoint_training_data(policy, dataset)
        ensure_disjoint_training_data(opponent, dataset)
    environment = ShowdownBattleEnv(opponent=opponent, dataset=dataset, client=client)
    wins = losses = draws = invalid = truncations = 0
    turns, latencies = [], []
    started = time.perf_counter()
    try:
        for game in range(games):
            _, info = environment.reset(seed=seed_start + game)
            while True:
                start = time.perf_counter()
                command = policy.predict(info["view"]["request"], info["view"])
                latencies.append((time.perf_counter() - start) * 1000)
                _, _, ended, truncated, info = environment.step(command_to_action(command))
                if ended or truncated:
                    wins += info["winner"] == "p1"
                    losses += info["winner"] == "p2"
                    draws += info["winner"] is None
                    truncations += bool(truncated)
                    invalid += info["invalid_actions"]
                    turns.append(info["turn"])
                    break
            if progress and (game + 1) % 50 == 0:
                progress({"games": game + 1, "wins": wins})
        return {"games": games, "wins": wins, "losses": losses, "draws": draws,
                "win_rate": wins / games, "win_rate_95ci": wilson(wins, games),
                "invalid_actions": invalid, "truncations": truncations, "mean_turns": float(np.mean(turns)),
                "mean_inference_ms": float(np.mean(latencies)), "p95_inference_ms": float(np.percentile(latencies, 95)),
                "seconds": time.perf_counter() - started}
    finally:
        environment.close()


def _register_training(agent, dataset, seed_start, seed_end):
    interval = [seed_start, seed_end]
    if interval not in agent.training_seed_ranges:
        agent.training_seed_ranges.append(interval)
    if dataset is not None:
        contract = dataset.manifest()
        if contract not in agent.training_data_contracts:
            agent.training_data_contracts.append(contract)


def train(args):
    torch.set_num_threads(1)
    dataset = None if args.dataset == "legacy6" else TeamDataset("train")
    agent = PolicyAgent.load(args.resume) if args.resume else PolicyAgent(seed=args.seed)
    if dataset is None and agent.training_data_contracts:
        raise ValueError("A held-out dataset checkpoint cannot resume on the legacy shared pool")
    if dataset is not None and args.resume and not agent.training_data_contracts and (agent.timesteps > 0 or agent.imitation_samples > 0):
        raise ValueError("A legacy trained checkpoint has no held-out data provenance; start a fresh 30-species model")
    if dataset is not None and agent.training_data_contracts and any(c != dataset.manifest() for c in agent.training_data_contracts):
        raise ValueError("Resume training requires the same training dataset contract")
    seed_start = TRAIN_SEED_START + args.seed * 100_000
    if agent.training_seed_ranges:
        seed_start = max(seed_start, max(end for _, end in agent.training_seed_ranges) + 1)
    if seed_start + args.steps + args.warmstart >= VALIDATION_SEED_START:
        raise ValueError("Training budget may enter the validation seed range")
    model_path = Path(args.model)
    previous_path = model_path.with_name("previous.pt")
    agent.save(previous_path, training={"kind": "pre-training-checkpoint"})
    previous = PolicyAgent(seed=args.seed + 1)
    previous.network.load_state_dict(agent.network.state_dict())
    teacher = BaselinePolicy("heuristic", seed=args.seed)
    baseline_opponents = {name: BaselinePolicy(name, seed=args.seed) for name in ("random", "maxpower", "heuristic")}
    mix = (args.opponent_mix or ("selfplay,heuristic,maxpower" if args.self_play else "heuristic")).split(',')
    if any(name not in (*baseline_opponents, "selfplay") for name in mix):
        raise ValueError("Unknown training opponent in mix")
    if "selfplay" in mix and not args.self_play:
        raise ValueError("Self-play opponent requires --self-play")
    environment = ShowdownBattleEnv(opponent=baseline_opponents['heuristic'], dataset=dataset)
    episode_seed = seed_start
    torch.manual_seed(args.seed)
    started = time.perf_counter()
    episodes, results, validation_history = [], [], []
    invalid = 0
    warmstart_result = None
    snapshot_refreshes = 0
    selected_score, selected_step = -1.0, None
    validation_dataset = None if dataset is None else TeamDataset("validation")
    validation_seed = VALIDATION_SEED_START + args.seed * 10_000
    if validation_seed + args.validation_games > EVAL_SEED_START:
        environment.close()
        raise ValueError("Validation budget may enter the final test seed range")
    best_path = model_path.with_name("selected.pt")
    try:
        observation, info = environment.reset(seed=episode_seed)
        demonstrations = []
        for sample in range(args.warmstart):
            command = teacher.predict(info["view"]["request"], info["view"])
            action = command_to_action(command)
            demonstrations.append({"observation": observation.copy(), "mask": info["action_mask"].copy(), "action": action})
            observation, _, ended, truncated, info = environment.step(action)
            if ended or truncated:
                invalid += info["invalid_actions"]
                episode_seed += 1
                environment.opponent = baseline_opponents[("random", "maxpower", "heuristic")[episode_seed % 3]]
                observation, info = environment.reset(seed=episode_seed)
            if (sample + 1) % 1024 == 0:
                print(json.dumps({"phase": "teacher-collection", "samples": sample + 1}), flush=True)
        if demonstrations:
            warmstart_result = agent.imitate(demonstrations, epochs=args.warmstart_epochs)
            _register_training(agent, dataset, seed_start, episode_seed)
            agent.save(model_path.with_name("warmstart.pt"), training={"kind": "public-teacher-warmstart", **warmstart_result})
            print(json.dumps({"phase": "teacher-fit", **warmstart_result}), flush=True)
        del demonstrations
        invalid += info["invalid_actions"]
        episode_seed += 1
        previous.network.load_state_dict(agent.network.state_dict())

        def validate(step):
            nonlocal selected_score, selected_step
            if args.validation_games <= 0 or validation_dataset is None:
                return
            _register_training(agent, dataset, seed_start, episode_seed)
            result = run_evaluation(agent, BaselinePolicy("heuristic", seed=args.seed), dataset=validation_dataset,
                                    seed_start=validation_seed, games=args.validation_games, client=environment.client)
            validation_history.append({"ppo_steps": step, "result": result})
            score = validation_score(result)
            if score > selected_score:
                selected_score, selected_step = score, step
                agent.save(best_path, training={"kind": "selected-on-validation", "ppo_steps": step, "validation": result})
            print(json.dumps({"phase": "validation", "ppo_steps": step, "score": score, "selected_step": selected_step}), flush=True)

        validate(0)
        rollout = []
        opponent_name = mix[0]
        environment.opponent = previous if opponent_name == "selfplay" else baseline_opponents[opponent_name]
        observation, info = environment.reset(seed=episode_seed)
        for step in range(args.steps):
            mask = info["action_mask"]
            action, log_prob, value = agent.select(observation, mask)
            next_obs, reward, terminated, truncated, next_info = environment.step(action)
            rollout.append({"observation": observation.copy(), "mask": mask.copy(), "action": action,
                            "log_prob": log_prob, "value": value, "reward": reward, "done": terminated or truncated})
            observation, info = next_obs, next_info
            if terminated or truncated:
                episodes.append({"winner": info["winner"], "turns": info["turn"], "truncated": truncated, "opponent": opponent_name})
                invalid += info["invalid_actions"]
                episode_seed += 1
                opponent_name = mix[len(episodes) % len(mix)]
                environment.opponent = previous if opponent_name == "selfplay" else baseline_opponents[opponent_name]
                observation, info = environment.reset(seed=episode_seed)
            if len(rollout) >= args.rollout or step + 1 == args.steps:
                bootstrap = agent.select(observation, info["action_mask"], deterministic=True)[2]
                results.append(agent.update(rollout, bootstrap=bootstrap))
                rollout.clear()
                if args.self_play:
                    previous.network.load_state_dict(agent.network.state_dict())
                    snapshot_refreshes += 1
                completed = step + 1
                if completed % args.validation_interval == 0 or completed == args.steps:
                    validate(completed)
                    _register_training(agent, dataset, seed_start, episode_seed)
                    agent.save(model_path.with_name("last.pt"), training={"kind": "latest-ppo-checkpoint", "completed_steps": completed})
                print(json.dumps({"phase": "ppo", "steps": completed, "episodes": len(episodes), "loss": results[-1]["loss"]}), flush=True)
        invalid += info["invalid_actions"]
        _register_training(agent, dataset, seed_start, episode_seed)
        if selected_step is not None:
            selected = PolicyAgent.load(best_path)
            selected.training_seed_ranges = copy.deepcopy(agent.training_seed_ranges)
            selected.training_data_contracts = copy.deepcopy(agent.training_data_contracts)
        else:
            selected = agent
            selected_step = args.steps
        summary = {"kind": "public-teacher-plus-masked-ppo", "metadata": model_metadata(), "runtime": runtime_metadata(),
                   "requested_ppo_steps": args.steps, "completed_ppo_steps": args.steps,
                   "selected_ppo_steps": selected_step, "model_timesteps": selected.timesteps,
                   "warmstart": warmstart_result, "warmstart_teacher": POLICY_VERSION,
                   "teacher_calls": teacher.calls,
                   "seed_start": seed_start, "seed_end": episode_seed,
                   "dataset": dataset.manifest() if dataset else {"version": "legacy6"},
                   "opponent_mix": mix, "self_play": args.self_play, "snapshot_refreshes": snapshot_refreshes,
                   "opponent_calls": {**{name: value.calls for name, value in baseline_opponents.items()}, "selfplay": previous.calls},
                   "opponent_calls_scope": "Baseline counts include warmstart collection and PPO. Selfplay calls and episode count are PPO-only.",
                   "selfplay_episodes": sum(e["opponent"] == "selfplay" for e in episodes),
                   "episodes": len(episodes), "wins": sum(e["winner"] == "p1" for e in episodes),
                   "losses": sum(e["winner"] == "p2" for e in episodes), "truncations": sum(e["truncated"] for e in episodes),
                   "invalid_actions": invalid, "seconds": time.perf_counter() - started,
                   "last_loss": results[-1]["loss"], "validation": validation_history,
                   "selection": "Highest validation win plus half natural-draw score against heuristic; truncations receive zero credit and ties retain earlier checkpoint. Test partition never used for selection.",
                   "previous_checkpoint": str(previous_path), "note": "Warmstart imitation and PPO budgets are separate. Species/moves are shared across partitions; no full-Pokedex or unseen-species strength claim."}
        selected.save(model_path, training=summary)
        summary["model_sha256"] = file_hash(model_path)
        save_json(args.report, summary)
        print(json.dumps(summary), flush=True)
    finally:
        environment.close()


def evaluate(args):
    torch.set_num_threads(1)
    policy = PolicyAgent.load(args.model) if args.model else BaselinePolicy(args.policy, seed=args.seed)
    dataset = None if args.dataset == "legacy6" else TeamDataset(args.split)
    seed_start = (VALIDATION_SEED_START if args.split == "validation" else EVAL_SEED_START) + args.seed * 10_000
    if args.split == "validation" and seed_start + args.games > EVAL_SEED_START:
        raise ValueError("Validation games must stay below the final test seed range")
    results = {}
    for name in args.opponents.split(','):
        opponent = PolicyAgent.load(args.previous) if name == "previous" and args.previous else BaselinePolicy(name, seed=args.seed)
        results[name] = run_evaluation(policy, opponent, dataset=dataset, seed_start=seed_start, games=args.games,
                                      progress=lambda value: print(json.dumps({"opponent": name, **value}), flush=True))
        print(json.dumps({"phase": "opponent-finished", "opponent": name, "result": results[name]}), flush=True)
    summary = {"kind": "held-out-build-composition-and-seed-evaluation" if dataset else "held-out-seed-only-evaluation", "split": args.split,
               "policy": "ppo" if args.model else args.policy, "policy_version": POLICY_VERSION,
               "runtime": runtime_metadata(), "model_sha256": file_hash(args.model) if args.model else None,
               "model_timesteps": getattr(policy, "timesteps", None), "imitation_samples": getattr(policy, "imitation_samples", None),
               "legacy_model_provenance": bool(args.model and not policy.training_data_contracts),
               "metadata": model_metadata(), "seed_start": seed_start, "seed_end": seed_start + args.games - 1,
               "team_pool_species": 6 if dataset is None else 30,
               "dataset": dataset.manifest() if dataset else {"version": "legacy6"}, "results": results,
               "previous_model": {"sha256": file_hash(args.previous), "legacy_comparand": not bool(PolicyAgent.load(args.previous).training_data_contracts)} if args.previous else None,
               "note": ("Model selection uses validation only. Final test is a separate build/composition/seed partition. Shared species and moves; not unseen-species evaluation. A legacy bootstrap opponent has no 30-species data provenance." if dataset else
                        "Legacy six-species pool: only episode seed overlap is checked. Build and team-composition holdouts are not claimed.")}
    save_json(args.report, summary)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("train")
    p.add_argument("--steps", type=int, default=65536)
    p.add_argument("--rollout", type=int, default=512)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--self-play", action="store_true")
    p.add_argument("--opponent-mix")
    p.add_argument("--warmstart", type=int, default=8192)
    p.add_argument("--warmstart-epochs", type=int, default=12)
    p.add_argument("--validation-games", type=int, default=64)
    p.add_argument("--validation-interval", type=int, default=16384)
    p.add_argument("--dataset", choices=("roguelike30", "legacy6"), default="roguelike30")
    p.add_argument("--resume")
    p.add_argument("--model", default="training/runtime/candidate.pt")
    p.add_argument("--report", default="training/runtime/train-report.json")
    p.set_defaults(run=train)
    p = commands.add_parser("evaluate")
    p.add_argument("--games", type=int, default=500, help="Games per opponent")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--model")
    p.add_argument("--policy", choices=("random", "maxpower", "heuristic", "legacyheuristic"), default="heuristic")
    p.add_argument("--opponents", default="random,maxpower,heuristic")
    p.add_argument("--previous", help="Checkpoint path for opponent named previous")
    p.add_argument("--dataset", choices=("roguelike30", "legacy6"), default="roguelike30")
    p.add_argument("--split", choices=("validation", "test"), default="test")
    p.add_argument("--report", default="training/runtime/evaluation.json")
    p.set_defaults(run=evaluate)
    args = parser.parse_args()
    if args.seed < 0:
        parser.error("Seed offsets must be nonnegative")
    if getattr(args, "steps", 1) < 1 or getattr(args, "games", 1) < 1 or getattr(args, "rollout", 1) < 1:
        parser.error("Steps, games and rollout size must be positive")
    if getattr(args, "warmstart", 0) < 0 or getattr(args, "warmstart_epochs", 1) < 1 or getattr(args, "validation_interval", 1) < 1 or getattr(args, "validation_games", 0) < 0:
        parser.error("Warmstart and validation counts are invalid")
    if args.command == "evaluate" and "previous" in args.opponents.split(',') and not args.previous:
        parser.error("Opponent previous requires --previous CHECKPOINT")
    args.run(args)


if __name__ == "__main__":
    main()
