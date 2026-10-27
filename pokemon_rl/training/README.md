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

The baseline policies are random legal action, maximum power/accuracy, and
`public-heuristic-v2`, using public type, own attack stats/boosts, STAB, recovery
and legal switch matchups. In v2 an immune attack no longer outranks every
voluntary switch. Forced switches use original party slots and public matchup
information. Grass Knot and Gyro Ball have a neutral choice-score prior because
their literal engine power is zero; the engine still determines their actual
variable damage. `legacyheuristic` preserves the former policy for comparisons.
Scores select actions only; no hidden opponent moves, item or stats are read.

## Training and evaluation

From `pokemon_rl`:

```text
python -X utf8 -B -m training.cli train --steps 65536 --warmstart 8192 --self-play --model training/runtime/candidate.pt --report training/runtime/train-report.json
python -X utf8 -B -m training.cli evaluate --split validation --games 64 --model training/runtime/candidate.pt --report training/runtime/validation.json
python -X utf8 -B -m training.cli evaluate --split test --games 500 --model training/runtime/candidate.pt --opponents random,maxpower,heuristic,previous --previous training/models/bootstrap.pt --report training/runtime/test.json
```

Training first collects public-information heuristic demonstrations and fits
the masked actor. This behavior-cloning warmstart is reported separately from
PPO timesteps. With `--self-play`, complete training episodes cycle between a
separate opponent network, heuristic and maxpower; `--opponent-mix` can set the
explicit mix. The network opponent receives its own p2 view and its weights are
refreshed at rollout boundaries. Reports retain its actual call count, episode
count and snapshot refreshes. Without `--self-play`, the default is heuristic.
`previous.pt` is the pre-training checkpoint, `warmstart.pt` is the teacher-fit
checkpoint, and `last.pt` is the last PPO checkpoint. Resumed training uses new
episode seeds after prior recorded ranges; the optimizer starts fresh.

The default data contract `roguelike30-threeway-v1` uses all 30 curated recruit
species. Each species gets role-appropriate EVs/nature for its curated moves.
Train, validation and test use different EV allocations and held items (Light
Ball remains on Pikachu). All 4,060 unordered three-species combinations are
assigned by a stable SHA-256 bucket to train 0–13, validation 14–16, test 17–19
out of 20 buckets. Permuting party or move order does not create a new team or
build. The dataset manifest records source/build/composition hashes, and models
retain their training manifest. Evaluation rejects overlapping or unverifiable
new dataset contracts. Species, move vocabulary and battle rules are shared;
this is not an unseen-species or full-Pokedex benchmark.

Training seeds begin at 10,000, validation at 1,000,000 and final test at
2,000,000. With defaults, 64 validation games against heuristic are evaluated
after warmstart and every 16,384 PPO steps. The candidate with the highest win
plus half natural-draw score is saved to the requested model path; reaching a
turn/decision limit receives zero credit and ties retain the earlier candidate.
Final test is not used in checkpoint selection. Select the
candidate and training budget before running the 500-game-per-opponent final
test, and do not tune against those test outcomes.

The former six-species environment remains available with `--dataset legacy6`
for reproducing development checks. The shipped bootstrap was trained on that
shared six-species pool and has no 30-species dataset provenance. It can be a
legacy comparison opponent, not evidence of a new split guarantee. Its old
evaluation used seeds 1,000,000–1,000,499; the newer final test range is separate.
Resuming legacy trained weights into the new dataset is rejected because their
earlier team/build exposure cannot be verified; start the new candidate fresh.

Reports contain wins/losses/draws, truncations, invalid actions, average turns,
inference mean/p95 latency, runtime and Wilson win-rate intervals. A truncation
at 100 turns or 200 decisions is counted in draws and also reported separately.
`--games` is the number of games **per opponent**. Set `--opponents` to include
`random,maxpower,heuristic,previous` and provide `--previous` for the last one.

`training/runtime/` is ignored. `models/bootstrap.pt` preserves the original
2,048-step six-species smoke model for reproducibility. The 217,340-byte
`models/expedition-v2.pt` is the newer experimental neural candidate selected
from the 30-species run described below. Both need PyTorch for inference; the
recommended rule AI remains available without it. The model's exact hash is
checked against its committed evaluation report. Further training checkpoints
stay local until explicitly selected for release. Committed `benchmarks/`
reports record actual conditions and results; training completion alone does
not establish a generally strong AI.

## Server inference and compatibility

```python
from training.policy import PolicyAgent

agent = PolicyAgent.load("training/models/expedition-v2.pt")
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

## 30-species retraining and fixed test on 2026 October 27

The retained run collected 8,192 public-heuristic demonstrations and completed
65,536 masked PPO steps in 399.4 seconds on the recorded CPU environment.
It completed 5,146 PPO games, including 1,716 self-play games with 23,816 actual
separate p2 model calls and 128 snapshot refreshes. Training recorded zero
illegal actions and zero truncations. Teacher calls were 8,192; the other rule
opponent call counts include both BC collection and PPO.

Validation alone selected the **16,384-step PPO checkpoint after the 8,192 BC
samples**, rather than the last 65,536-step checkpoint. Its heuristic validation
win rate was 25.0% in 64 games. The later checkpoints scored lower and were not
deployed just because they had more training. An earlier exploratory run was
discarded after 17,920 logged PPO steps when the validation truncation-credit
criterion was corrected; none of those discarded weights contributed to the
fresh retained candidate, and no final test had been run at that point.

The candidate was frozen before final test. Test used seeds 2,000,000–2,000,499
and held-out EV/item builds and unordered three-species compositions. These are
fresh full-health 3v3 battles, not 20-node roguelike clear rates. Species, moves
and battle rules are shared across splits.

| Opponent (500 games each) | Experimental neural | Rule heuristic v2 |
| --- | --- | --- |
| Random legal policy | 385 wins, 77.0% (73.1–80.5%) | 468 wins, 93.6% (91.1–95.4%) |
| Maximum power policy | 271 wins, 54.2% (49.8–58.5%) | 406 wins, 81.2% (77.5–84.4%) |
| Rule heuristic v2 | 99 wins, 19.8% (16.5–23.5%) | 273 wins, 54.6% (50.2–58.9%) |
| Original six-species bootstrap | 360 wins, 72.0% (67.9–75.8%) | 462 wins, 92.4% (89.7–94.4%) |
| Legacy rule heuristic v1 | Not part of the frozen neural comparison | 282 wins, 56.4% (52.0–60.7%) |

Parentheses are descriptive 95% Wilson win-rate intervals for each comparison.
All 4,500 matches recorded zero illegal actions and zero truncations. Neural
inference mean was 0.91–1.07ms and maximum opponent-wise p95 was 1.80ms; rule
inference maximum p95 was 0.14ms. Network/HTTP latency is excluded. The neural
maximum-power interval includes 50%, and the original six-species report is a
different population, so these rows do not justify a direct old/new percentage
point improvement claim. The fixed p1 cohort also gave mirror rule play 54.6%;
do not overstate its 56.4% legacy comparison as a broad causal strength gain.

The candidate directly beats the old bootstrap but remains weaker than the
heuristic. It is an **experimental neural option**, while heuristic v2 remains
the recommended rule AI. Its file SHA-256 is
`20c634f38af9483217c005961b3d00ce53b9af780c825edb5600a5679f6e5610`.
See `benchmarks/training-20261027.json`,
`benchmarks/evaluation-candidate-20261027.json`,
`benchmarks/evaluation-heuristic-20261027.json` and
`benchmarks/comparison-20261027.json` for raw budgets, provenance and results.

An additional paired confirmation controls the observed cohort effect without
changing any model or policy. Reproduce it once with:

```text
python -X utf8 -B -m training.confirmatory --pairs 250 --seed-start 3000000 --report training/benchmarks/fairness-confirmatory-20261027.json
```

For each of 250 independent pair seeds, two physical teams and the engine seed
stay fixed. The policies exchange p1/p2 ownership in the second game, so each
policy controls each team and seat once. Three comparisons are candidate versus
bootstrap, heuristic v2 versus legacy, and a v2 mirror negative control: 1,500
additional matches. The report records both games per pair and uses a bootstrap
over independent pairs as well as the descriptive game-level Wilson interval.
It refuses to silently overwrite an existing confirmatory report. These results
are confirmation of the frozen candidate, not data for further training or
checkpoint selection.

| Independent paired confirmation | Wins of 500 | Pair-bootstrap 95% interval |
| --- | --- | --- |
| Frozen neural candidate vs bootstrap | 351, 70.2% | 66.6–73.8% |
| Heuristic v2 vs legacy heuristic | 257, 51.4% | 48.6–54.2% |
| Heuristic v2 mirror control | 250, 50.0% | 50.0–50.0% |

All 1,500 confirmatory matches also had zero illegal actions and zero
truncations. Candidate-versus-bootstrap descriptive Wilson interval was
66.0–74.0%, and both its game-level and pair-level lower bounds exceed 50%.
The v2-versus-legacy pair interval includes 50%, so the evidence supports the
specific immunity/switching fixes, not a broad statistically established rule
AI strength gain. The mirror control cancels the team and seat allocation as
intended. Across the original and confirmatory cohorts, 6,000 matches were run;
the original 4,500-match report and frozen weights were preserved.

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
