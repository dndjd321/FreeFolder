# Shared engine training

The new Gymnasium environment uses `battle_service.BattleClient`, the same
authoritative Node simulator as the HTTP game path. It does not calculate damage,
abilities, held items, status, or turn order in Python. Run `npm ci --ignore-scripts`
inside `battle_service` first, and install `numpy`, `torch` and `gymnasium` in the
Python environment used for training. Network play is not used for training.

## Observation and actions

`public-battle-v1` has 333 features: own party HP/stats/status/types/boosts, public
opponent observations, current move power/type/category/accuracy/PP/priority,
and public weather, terrain, screens, Tailwind and Trick Room. Field state is
derived from the side-filtered protocol events. Hidden opponent team builds,
moves, items and exact HP are never read. The opponent policy receives its own
`p2` view, not the learner's observation.

`moves4-switch-slots3-v1` has seven actions. Actions 0–3 select the current move
request slots; actions 4–6 switch to original party slots 0–2. The legal mask is
generated from the engine request. PPO stores that exact mask with each rollout
transition and reuses it in `evaluate`, so unchanged policy parameters have
probability ratio one. An illegal action raises an error instead of silently
selecting a fallback move.

The baseline policies are random legal action, maximum power/accuracy, and a
simple public-information heuristic using type, STAB and low-HP recovery. Their
scores choose actions only; the shared engine decides their actual effects.

## Training and evaluation

From `pokemon_rl`:

```text
python -X utf8 -B -m training.cli train --steps 2048 --self-play
python -X utf8 -B -m training.cli evaluate --games 500 --model training/runtime/latest.pt
python -X utf8 -B -m training.cli evaluate --games 500 --model training/runtime/latest.pt --opponents previous --previous training/runtime/previous.pt
```

Without `--self-play`, training uses the heuristic opponent. Self-play actually
calls a separate opponent network on its own view and refreshes its weights at
rollout boundaries. It is not an alias for a rule-based opponent. Training also
saves `previous.pt` before updating so the previous policy can be evaluated.

Training seeds start at 10,000; held-out evaluation seeds start at 1,000,000.
The supplied smoke run uses training seeds 10,000–10,059 and evaluation seeds
1,000,000–1,000,499. The development pool currently contains six species with
random three-member teams. Seeds are held out; the species pool is shared.
Expand and separate team/build pools before making a broader strength claim.
Checkpoint seed ranges are retained across resumed training, and evaluation
rejects a requested seed interval that overlaps either model's recorded ranges.

Reports contain wins/losses/draws, truncations, invalid actions, average turns,
inference mean/p95 latency, runtime and Wilson win-rate intervals. A truncation
at 100 turns or 200 decisions is counted in draws and also reported separately.
`--games` is the number of games **per opponent**. Set `--opponents` to include
`random,maxpower,heuristic,previous` and provide `--previous` for the last one.

`training/runtime/` is ignored. A 210 KB `models/bootstrap.pt` checkpoint is
included so the neural difficulty works immediately after checkout when PyTorch
is installed. It is the actual 2,048-step self-play smoke model, a weak initial
policy that is not recommended over the heuristic opponent. It exists to make
inference and model replacement reproducible. Its SHA-256 is in the training
report. Further training checkpoints stay local until explicitly selected for
release. The committed `benchmarks/` reports record
actual run conditions and results; short training verifies the pipeline and does
not establish a generally strong AI. No minimum win rate is claimed.

## Server inference and compatibility

```python
from training.policy import PolicyAgent

agent = PolicyAgent.load("training/models/bootstrap.pt")
command = agent.predict(view["request"], view)
```

`predict` returns a legal `{type, index}` command or `None` for a waiting side.
Loading requires matching observation/action versions, vector dimensions, engine
version, ruleset and network architecture. Legacy 84-dimensional checkpoints are
not compatible. Files are loaded with PyTorch's `weights_only=True`; training
saves atomically. The service should report model unavailability explicitly and
can provide its rule-based policy until a compatible local model is trained.

Tests from the repository root:

```text
python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_training.py -v
```

The tests exercise masked probability equality, model metadata rejection,
opponent perspective/calls, move-sensitive observations, hidden-field exclusion,
and complete legal games against the actual shared engine. The bootstrap model
has a separate metadata-and-legal-inference regression test.

## Bootstrap evaluation on 2026 October 26

The 2,048-step model was evaluated over 500 held-out seeds per opponent, for
2,000 games total. All recorded illegal-action counts were zero.

| Opponent | Wins | Win rate | 95 percent Wilson interval |
| --- | --- | --- | --- |
| Random legal policy | 194 of 500 | 38.8 percent | 34.6–43.1 percent |
| Maximum power policy | 86 of 500 | 17.2 percent | 14.1–20.8 percent |
| Public-information heuristic | 28 of 500 | 5.6 percent | 3.9–8.0 percent |
| Its initial untrained checkpoint | 341 of 500 | 68.2 percent | 64.0–72.1 percent |

The model improved relative to its initial checkpoint in this development pool
but remains weaker than the supplied baselines. Keep the heuristic as the
recommended opponent. Two games against the initial checkpoint reached the
decision/turn limit and are counted as draws/truncations. See
`benchmarks/evaluation-20261026.json` for runtime, seeds, model hash, full counts,
turns and measured inference latency. This does not establish performance on
unseen species, teams or the complete Pokemon roster.
