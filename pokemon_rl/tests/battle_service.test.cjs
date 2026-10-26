'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {BattleEngine} = require('../battle_service/engine.cjs');

function team() {
  return [
    {species: 'Charizard', ability: 'Blaze', item: 'Life Orb', nature: 'Timid', moves: ['Flamethrower', 'Air Slash', 'Dragon Claw', 'Protect']},
    {species: 'Garchomp', ability: 'Rough Skin', item: 'Leftovers', nature: 'Jolly', moves: ['Earthquake', 'Dragon Claw', 'Swords Dance', 'Protect']},
    {species: 'Clefable', ability: 'Magic Guard', item: 'Leftovers', nature: 'Bold', moves: ['Moonblast', 'Soft-Boiled', 'Calm Mind', 'Protect']},
  ];
}
const payload = () => ({teams: {p1: team(), p2: team()}, seed: [21, 22, 23, 24]});
function clean(state) {
  const result = JSON.parse(JSON.stringify(state));
  delete result.battle_id;
  for (const view of Object.values(result.views)) delete view.battle_id;
  return result;
}
function cleanup(engine) { for (const id of [...engine.battles.keys()]) engine.close(id); }

test('carried fainted members do not create phantom survivors after the last KO', async () => {
  const engine = new BattleEngine();
  try {
    const initial = await engine.create(payload());
    const resources = engine.resources(initial.battle_id);
    resources.p1.forEach((p, i) => { p.hp = i === 2 ? 1 : 0; });
    const p = payload(); p.initial_state = {p1: resources.p1};
    let state = await engine.create(p);
    state = await engine.choose(state.battle_id, 'p1', {type: 'move', index: 0});
    state = await engine.choose(state.battle_id, 'p2', {type: 'move', index: 0});
    assert.equal(state.ended, true);
    assert.equal(state.winner, 'p2');
  } finally { cleanup(engine); }
});

test('canonical team validation rejects fabricated species, ability, learnset and EVs', async () => {
  const engine = new BattleEngine();
  for (const mutate of [
    p => p.teams.p1.pop(),
    p => { p.teams.p1[0].species = 'not-a-pokemon'; },
    p => { p.teams.p1[0].ability = 'Wonder Guard'; },
    p => { p.teams.p1[0].moves[0] = 'Spore'; },
    p => { p.teams.p1[0].evs = {hp: 252, atk: 252, def: 252}; },
    p => { p.teams.p1[0].level = 100; },
  ]) {
    const p = payload(); mutate(p);
    await assert.rejects(engine.create(p));
  }
  const p = payload(); p.teams.p1[0].stats = {hp: 100000000};
  const state = await engine.create(p);
  assert.equal(state.views.p1.own[0].maxhp, 153);
  assert.equal(state.views.p1.own[0].moves[0].basePower, 90);
  cleanup(engine);
});

test('same seed and accepted actions replay with identical state and private logs', async () => {
  const engine = new BattleEngine();
  let a = await engine.create(payload());
  let b = await engine.create(payload());
  assert.deepEqual(clean(a), clean(b));
  for (const [side, action] of [
    ['p1', {type: 'move', index: 2}], ['p2', {type: 'move', index: 2}],
    ['p1', {type: 'switch', index: 1}], ['p2', {type: 'move', index: 2}],
    ['p1', {type: 'move', index: 1}], ['p2', {type: 'move', index: 3}],
  ]) {
    a = await engine.choose(a.battle_id, side, action);
    b = await engine.choose(b.battle_id, side, action);
    assert.deepEqual(clean(a), clean(b));
  }
  const restored = await engine.restore(engine.checkpoint(a.battle_id));
  assert.deepEqual(clean(a), clean(restored));
  cleanup(engine);
});

test('side projections hide unrevealed bench, moves, item and exact opponent HP', async () => {
  const engine = new BattleEngine();
  const state = await engine.create(payload());
  const view = engine.view(state.battle_id, 'p1');
  assert.equal(view.opponent.filter(p => p.revealed).length, 1);
  assert.equal(view.opponent[0].maxhp, 100);
  for (const opponent of view.opponent) {
    assert.equal(Object.hasOwn(opponent, 'item'), false);
    assert.equal(Object.hasOwn(opponent, 'moves'), false);
    assert.equal(Object.hasOwn(opponent, 'stats'), false);
  }
  assert(view.events.some(line => line.includes('|switch|p2a: Charizard|Charizard, L50') && line.endsWith('|100/100')));
  assert(!view.events.some(line => line.includes('|split|')));
  assert(!view.events.some(line => line.includes('|switch|p2a:') && line.includes('153/153')));
  assert(!('canTerastallize' in view.request.active[0]));
  cleanup(engine);
});

test('pending input cannot be overwritten; illegal choices leave the battle usable', async () => {
  const engine = new BattleEngine();
  let state = await engine.create(payload());
  await assert.rejects(engine.choose(state.battle_id, 'p1', {type: 'switch', index: 0}));
  await assert.rejects(engine.choose(state.battle_id, 'p1', {type: 'eval', index: 0}));
  state = await engine.choose(state.battle_id, 'p1', {type: 'move', index: 2});
  assert.equal(state.requests.p1.wait, true);
  await assert.rejects(engine.choose(state.battle_id, 'p1', {type: 'move', index: 0}));
  const resumed = await engine.restore(engine.checkpoint(state.battle_id));
  assert.equal(resumed.requests.p1.wait, true);
  state = await engine.choose(resumed.battle_id, 'p2', {type: 'move', index: 2});
  assert.equal(state.turn, 2);
  cleanup(engine);
});

test('durable HP, PP, status and consumed item survive a new battle', async () => {
  const engine = new BattleEngine();
  let state = await engine.create(payload());
  await engine.choose(state.battle_id, 'p1', {type: 'move', index: 2});
  state = await engine.choose(state.battle_id, 'p2', {type: 'move', index: 2});
  const resources = engine.resources(state.battle_id);
  assert.equal(resources.p1[0].moves[2].pp, 23);
  resources.p1[0].hp = 42;
  resources.p1[0].item = '';
  resources.p1[0].status = 'brn';
  resources.p1[0].moves[0].pp = 0;
  resources.p1[1].status = 'slp';
  resources.p1[1].sleepTurns = 2;
  state = await engine.create({...payload(), initial_state: resources});
  const first = state.views.p1.own[0];
  assert.equal(first.hp, 42);
  assert.equal(first.status, 'brn');
  assert.equal(first.item, '');
  assert.equal(first.moves[0].pp, 0);
  assert.equal(first.moves[2].pp, 23);
  assert.equal(state.views.p1.own[1].status, 'slp');
  assert.equal(state.requests.p1.active[0].moves[0].disabled, true);
  await assert.rejects(engine.choose(state.battle_id, 'p1', {type: 'move', index: 0}));
  const restored = await engine.restore(engine.checkpoint(state.battle_id));
  assert.deepEqual(clean(state), clean(restored));
  cleanup(engine);
});

test('fainted leading party slots choose a living lead and keep stable switch indexes', async () => {
  const engine = new BattleEngine();
  const original = await engine.create(payload());
  const resources = engine.resources(original.battle_id);
  resources.p1[0].hp = 0;
  const state = await engine.create({...payload(), initial_state: resources});
  assert.equal(state.views.p1.own[0].fainted, true);
  assert.equal(state.views.p1.own[1].active, true);
  const next = await engine.choose(state.battle_id, 'p1', {type: 'switch', index: 2});
  await engine.choose(next.battle_id, 'p2', {type: 'move', index: 3});
  assert.equal(engine.view(next.battle_id, 'p1').own[2].active, true);
  cleanup(engine);
});

test('sandstorm KO requests a forced switch and the other side waits', async () => {
  const engine = new BattleEngine();
  const p = payload();
  p.teams.p2[0] = {species: 'Tyranitar', ability: 'Sand Stream', moves: ['Protect', 'Crunch', 'Rock Slide', 'Earthquake']};
  let state = await engine.create(p);
  const resources = engine.resources(state.battle_id);
  resources.p1[0].hp = 1;
  state = await engine.create({...p, initial_state: resources});
  await engine.choose(state.battle_id, 'p1', {type: 'move', index: 3});
  state = await engine.choose(state.battle_id, 'p2', {type: 'move', index: 0});
  assert.equal(state.views.p1.own[0].hp, 0);
  assert.equal(state.requests.p1.forceSwitch[0], true);
  assert.equal(state.requests.p2.wait, true);
  await assert.rejects(engine.choose(state.battle_id, 'p1', {type: 'move', index: 0}));
  state = await engine.choose(state.battle_id, 'p1', {type: 'switch', index: 1});
  assert.equal(state.views.p1.own[1].active, true);
  cleanup(engine);
});

test('boosts reset on switching while durable resources remain', async () => {
  const engine = new BattleEngine();
  let state = await engine.create(payload());
  await engine.choose(state.battle_id, 'p1', {type: 'switch', index: 1});
  state = await engine.choose(state.battle_id, 'p2', {type: 'move', index: 3});
  await engine.choose(state.battle_id, 'p1', {type: 'move', index: 2});
  state = await engine.choose(state.battle_id, 'p2', {type: 'move', index: 3});
  assert.equal(state.views.p1.own[1].boosts.atk, 2);
  const pp = state.views.p1.own[1].moves[2].pp;
  await engine.choose(state.battle_id, 'p1', {type: 'switch', index: 2});
  await engine.choose(state.battle_id, 'p2', {type: 'move', index: 3});
  await engine.choose(state.battle_id, 'p1', {type: 'switch', index: 1});
  state = await engine.choose(state.battle_id, 'p2', {type: 'move', index: 3});
  assert.equal(state.views.p1.own[1].boosts.atk, 0);
  assert.equal(state.views.p1.own[1].moves[2].pp, pp);
  cleanup(engine);
});

test('a complete 3v3 battle reports a canonical winner and can be replayed', async () => {
  const engine = new BattleEngine();
  let state = await engine.create(payload());
  for (let count = 0; count < 100 && !state.ended; count++) {
    for (const side of ['p1', 'p2']) {
      const view = engine.view(state.battle_id, side);
      if (view.ended || view.request.wait) continue;
      const choice = view.request.forceSwitch?.[0]
        ? {type: 'switch', index: view.own.find(p => !p.fainted && !p.active).slot}
        : {type: 'move', index: view.request.active[0].moves
          .filter(move => !move.disabled).sort((a, b) => b.basePower - a.basePower)[0].index};
      state = await engine.choose(state.battle_id, side, choice);
    }
  }
  assert.equal(state.ended, true);
  assert(['p1', 'p2', null].includes(state.winner));
  const restored = await engine.restore(engine.checkpoint(state.battle_id));
  assert.deepEqual(clean(state), clean(restored));
  cleanup(engine);
});

module.exports = {team, payload};
