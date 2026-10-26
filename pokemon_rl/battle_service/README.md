# FreeFolder battle service

This service owns the only authoritative battle simulation for the new game path.
It uses the MIT-licensed `pokemon-showdown` package pinned to **0.11.11** in
`package-lock.json`. Run `npm ci --ignore-scripts --no-audit --no-fund` in this
directory before starting the Python application. Node.js and Python 3.10 or
newer are required. The application must start and stop its `BattleClient`.

The worker exposes JSONL over its parent process pipes. It opens no port and
accepts only named operations. Client actions become numeric `move` and `switch`
commands; arbitrary simulator commands, `eval`, custom formats and forced wins
are not accepted. Keep this boundary internal to the server or training process.

## Rules

The fixed format is Generation 9 National Dex AG with exactly three Pokemon,
level 50, no team preview, no Terastallization and no Dynamax. Mega Evolution,
Z-moves and Ultra Burst commands are also omitted from the adapter action
vocabulary. Teams pass strict numeric/ID checks and Showdown's separate
`TeamValidator`, including move learnsets and abilities. Submitted attack or HP
stats are not used: the engine calculates canonical stats.

The validator's pinned `debug` flag suppresses only its advisory zero-EV and
level-50 warnings. It is never enabled on the actual battle, because doing so
would reveal exact opponent HP. Existing illegal recommended moves are rejected
with validation messages instead of silently becoming legal.

Official contracts:

- [Simulator API](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIMULATOR.md)
- [Separate team validator](https://github.com/smogon/pokemon-showdown/blob/master/sim/TEAMS.md)
- [Undocumented API version pinning](https://github.com/smogon/pokemon-showdown/blob/master/sim/README.md)
- [MIT license](https://github.com/smogon/pokemon-showdown/blob/master/LICENSE)

## Python API

```python
from battle_service.client import BattleClient

with BattleClient() as client:
    state = client.create({"p1": player_sets, "p2": opponent_sets}, seed=[1, 2, 3, 4])
    battle_id = state["battle_id"]
    state = client.choose(battle_id, "p1", {"type": "move", "index": 0})
    state = client.choose(battle_id, "p2", {"type": "move", "index": 1})
    public_response = client.view(battle_id, "p1")
    checkpoint = client.checkpoint(battle_id)
    resources = client.resources(battle_id)
    restored = client.restore(checkpoint)
    next_battle = client.create({"p1": player_sets, "p2": next_opponent_sets},
                                initial_state={"p1": resources["p1"]})
```

Each set contains `species`, `ability`, `item`, `nature`, `moves`, and optional
`evs`/`ivs` using Showdown's `hp/atk/def/spa/spd/spe` keys. Action indexes are
zero based. Move indexes refer to the current request's moves. Switch indexes
refer to the original team slot and remain stable after the engine reorders its
party. Requests expose the current internal `position` separately.

`create` and `choose` return a **server-internal** object containing both sides'
requests and views. Never send that whole object to a browser. `view(id, side)`
returns that side's exact own party, public opponent observations, its request,
and side-filtered protocol events. Opponent bench, moves, item, and exact HP are
not exposed. Public opponent HP uses the engine's percentage scale. Opponent
slots are display-order identifiers, not private team indexes. `winner` is
`p1`, `p2`, or `null` for no winner/tie; `ended` distinguishes a tie from play.

The request is `{wait: true}` after that side has submitted a choice or while
only the other side must act. Forced switches arrive as `forceSwitch`. Current
move request metadata includes power, type, category, accuracy, PP and disabled
state for the shared heuristic/training adapters.

## Resource transfer and recovery

Showdown's documented API does not initialize an injured party. The versioned
`hydrateResources` function isolates the necessary internal API access and runs
before switch-in effects. It restores HP, PP, major status, sleep turns and held
item consumption; boosts and volatile combat conditions start clean. A fainted
leading slot is retained and the first living member leads the next battle.
Every resource value is checked against the canonical team and max HP/PP.

Resource transfer currently requires the same canonical moves in the same
order. Permanent moveset changes should update the canonical team and map the
remaining PP before creating the next battle. Full arbitrary engine mutation is
not a persistence API.

A checkpoint stores the exact engine/ruleset version, canonical teams, seed,
initial resource hydration and **acknowledged actions**, including a pending
single-side action. Restore/replay reconstructs the battle through the same
validator and action path; it does not deserialize executable code. It can be
used in a fresh worker after a restart. Persist checkpoints atomically at the
server boundary. A worker timeout reports an unknown action outcome and never
retries that action automatically.

## Verification

From the repository root:

```text
node --test pokemon_rl/tests/battle_service.test.cjs
python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_battle_service.py -v
```

These use the actual installed engine and actual Node subprocess. They cover
team legality, deterministic replay, private projection, pending choices,
resource transfer, stable switch slots, and process restart recovery. Hosting
provider deployment and browser integration are separate acceptance checks.
