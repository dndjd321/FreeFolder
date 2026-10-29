const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'pokemon_battle_v3.html'), 'utf8');

function sourceFunction(name) {
  const start = html.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `Missing production function ${name}`);
  const end = html.indexOf('\n}', start);
  assert.notEqual(end, -1, `Missing end of ${name}`);
  return html.slice(start, end + 2);
}

class Element {
  constructor() {
    this.children = [];
    this.style = {};
    this.classList = {add() {}, remove() {}};
    this.textContent = '';
    this.value = '';
    this._html = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; this._html = ''; }
  set innerHTML(value) { this._html = String(value); this.children = []; }
  get innerHTML() { return this._html; }
}

function pokemon(name, hp = 100, extra = {}) {
  return {
    name, id: 1, currentHp: hp, maxHp: 100, types: ['normal'], ability: '', item: '',
    rank_atk: 0, rank_def: 0, rank_spatk: 0, rank_spdef: 0, rank_spd: 0,
    moves: [{name: 'tackle', pp: 9, maxPp: 10}], status: null, statusTurns: 0,
    ...extra,
  };
}

function state(player = [pokemon('player'), pokemon('player bench')], ai = [pokemon('ai'), pokemon('ai bench')]) {
  return {
    player: {party: player, active: 0, revealed: [true, false, false]},
    ai: {party: ai, active: 0, revealed: [true, false, false]},
    phase: 'action', turn: 1, weather: null, weatherTurns: 0, field: {},
  };
}

function context() {
  const elements = new Map();
  const calls = {actions: 0, logs: [], entries: [], timers: []};
  const ctx = {
    console, Number, Object, Array, String, Math,
    document: {
      getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
      createElement() { return new Element(); },
      querySelector() { return {value: 'public'}; },
    },
    battleState: null, multiState: {active: false, rules: {}, role: 'player1'},
    multiRooms: [], multiWs: null, aiDifficulty: 'normal', TYPE_KO: {normal: '노말'},
    TYPE_CHART: {rock: {flying: 2, fire: 2}}, ABILITY_EN_KO: {},
    renderBattle() {}, renderActions() { calls.actions++; }, clearTurnTimer() {},
    switchBattleTab() {}, addLog(...args) { calls.logs.push(args); },
    applyEntryAbility(p) { calls.entries.push(p.name); },
    aiChooseBestSwitch() { return {idx: -1}; }, refreshRooms() {},
    setTimeout(fn) { calls.timers.push(fn); }, showResult() {},
  };
  vm.createContext(ctx);
  const functions = [
    'escapeHtml', 'spriteId', 'describeRules', 'handleMultiMsg', 'renderRoomList', 'pickMonHtml',
    'getActive', 'resetSwitchState', 'switchActivePokemon', 'resumeAfterForcedSwitch',
    'applyEntryHazards', 'finishEntryKO', 'playerSwitch', 'multiPlayerSwitch',
    'showSwitchPrompt', 'applyEndTurnAbility', 'applyStatusEndTurn', 'endOfTurn',
    'handleKO', 'checkBattleEnd',
  ];
  vm.runInContext(functions.map(sourceFunction).join('\n'), ctx, {filename: 'production-frontend-functions.js'});
  return {ctx, elements, calls};
}

test('network status messages escape room names, nicknames, codes, rules and errors', () => {
  const {ctx, elements} = context();
  const payload = '<img src=x onerror="globalThis.compromised=true">';
  for (const type of ['room_created', 'room_joined', 'opponent_joined', 'opponent_team_ready', 'battle_ended', 'error']) {
    ctx.handleMultiMsg({type, code: payload, room_name: payload, nickname: payload, message: payload, rules: {turnTime: payload}});
    const markup = elements.get('multiMainStatus').innerHTML;
    assert.ok(!markup.includes('<img'), `${type} injected an image element`);
    assert.ok(markup.includes('&lt;img'), `${type} omitted the escaped message`);
  }
});

test('room list treats names and codes as text and binds clicks without inline JavaScript', () => {
  const {ctx, elements} = context();
  const payload = '<img src=x onerror="compromised=true">';
  const code = "');globalThis.compromised=true;//";
  ctx.multiRooms = [{name: payload, host: payload, code, players: 1, max_players: 2, visibility: 'public', rules: {turnTime: payload}}];
  let joined;
  ctx.joinRoom = value => { joined = value; };
  ctx.renderRoomList();
  const item = elements.get('multiRoomList').children[0];
  const name = item.children[0].children[0];
  assert.equal(name.textContent, payload);
  assert.equal(name.innerHTML, '');
  assert.equal(name.children[0].textContent, `[${code}]`);
  item.onclick();
  assert.equal(joined, code);
  assert.equal(ctx.compromised, undefined);
});

test('team preview escapes remote pokemon fields and restricts sprite IDs', () => {
  const {ctx} = context();
  const payload = '<img src=x onerror="compromised=true">';
  const markup = ctx.pickMonHtml({name: payload, id: '1" onerror="attack()', types: [payload], selectedMoves: [payload]}, false, false);
  assert.ok(!markup.includes(payload));
  assert.ok(markup.includes('&lt;img'));
  assert.ok(markup.includes('pokemon/.png'));
});

test('switch resets volatile state and preserves HP, PP, primary status and held item', () => {
  const {ctx} = context();
  const p = pokemon('boosted', 37, {
    rank_atk: 6, rank_eva: -2, taunted: true, tauntTurns: 3, confused: true,
    confusedTurns: 2, yawnCounter: 1, outrageTurns: 2, outrageMove: 'outrage',
    flinched: true, protecting: true, enduring: true, recharging: true,
    status: 'burn', statusTurns: 4, item: 'leftovers', _choiceLockedMove: 'tackle', _flashFire: true,
  });
  ctx.resetSwitchState(p);
  assert.equal(p.rank_atk, 0); assert.equal(p.rank_eva, 0);
  for (const key of ['taunted', 'confused', 'flinched', 'protecting', 'enduring', 'recharging', '_flashFire']) assert.equal(p[key], false);
  assert.equal(p.outrageTurns, 0); assert.equal(p._choiceLockedMove, null);
  assert.equal(p.currentHp, 37); assert.equal(p.status, 'burn'); assert.equal(p.statusTurns, 4);
  assert.equal(p.moves[0].pp, 9); assert.equal(p.item, 'leftovers');
});

test('actual forced switch clears the outgoing and incoming boosts without spending PP', () => {
  const {ctx} = context();
  ctx.battleState = state([pokemon('old', 0, {rank_atk: 6}), pokemon('new', 70, {rank_atk: 3, taunted: true})]);
  ctx.battleState.phase = 'switch';
  ctx.playerSwitch(1);
  assert.equal(ctx.battleState.player.active, 1);
  assert.equal(ctx.battleState.player.party[0].rank_atk, 0);
  assert.equal(ctx.getActive('player').rank_atk, 0);
  assert.equal(ctx.getActive('player').taunted, false);
  assert.equal(ctx.getActive('player').currentHp, 70);
  assert.equal(ctx.getActive('player').moves[0].pp, 9);
  assert.equal(ctx.battleState.phase, 'action');
});

test('sand KO advances the completed turn and requires a living replacement', async () => {
  const {ctx, calls} = context();
  ctx.battleState = state([pokemon('fragile', 1), pokemon('bench')], [pokemon('immune', 100, {types: ['steel']})]);
  Object.assign(ctx.battleState, {weather: 'sand', weatherTurns: 3});
  await ctx.endOfTurn();
  assert.equal(ctx.getActive('player').currentHp, 0);
  assert.equal(ctx.battleState.turn, 2);
  assert.equal(ctx.battleState.phase, 'switch');
  assert.equal(calls.actions, 0);
  ctx.playerSwitch(1);
  assert.equal(ctx.battleState.phase, 'action');
  assert.equal(ctx.battleState.turn, 2);
});

test('hail KO switches the AI without applying the same residual to its new member', async () => {
  const {ctx} = context();
  ctx.battleState = state([pokemon('ice', 100, {types: ['ice']})], [pokemon('fragile', 1), pokemon('bench')]);
  Object.assign(ctx.battleState, {weather: 'hail', weatherTurns: 3});
  await ctx.endOfTurn();
  assert.equal(ctx.battleState.ai.active, 1);
  assert.equal(ctx.getActive('ai').currentHp, 100);
  assert.equal(ctx.battleState.phase, 'action');
});

test('both original active pokemon receive status residual before replacement', async () => {
  const {ctx} = context();
  ctx.battleState = state([pokemon('p', 1, {status: 'burn'}), pokemon('p bench')], [pokemon('a', 1, {status: 'burn'}), pokemon('a bench')]);
  await ctx.endOfTurn();
  assert.equal(ctx.battleState.player.party[0].currentHp, 0);
  assert.equal(ctx.battleState.ai.party[0].currentHp, 0);
  assert.equal(ctx.battleState.ai.active, 1);
  assert.equal(ctx.battleState.phase, 'switch');
});

test('entry hazards can KO successive AI replacements until a living member enters', () => {
  const {ctx, calls} = context();
  ctx.battleState = state(undefined, [pokemon('old', 0), pokemon('fragile', 1), pokemon('survivor')]);
  ctx.battleState.field.stealthRockAi = true;
  assert.equal(ctx.handleKO('ai'), true);
  assert.equal(ctx.battleState.ai.active, 2);
  assert.equal(ctx.getActive('ai').currentHp, 88);
  assert.deepEqual(calls.entries, ['survivor']);
});

test('entry hazards preserve the player replacement prompt until a living choice survives', () => {
  const {ctx, calls} = context();
  ctx.battleState = state([pokemon('old', 0), pokemon('fragile', 1), pokemon('survivor')]);
  ctx.battleState.field.stealthRockPlayer = true;
  ctx.battleState.phase = 'switch';
  ctx.playerSwitch(1);
  assert.equal(ctx.getActive('player').currentHp, 0);
  assert.equal(ctx.battleState.phase, 'switch');
  assert.equal(calls.actions, 0);
  ctx.playerSwitch(2);
  assert.equal(ctx.getActive('player').currentHp, 88);
  assert.equal(ctx.battleState.phase, 'action');
  assert.deepEqual(calls.entries, ['survivor']);
});

test('final hazard KO ends the battle instead of showing action controls', () => {
  const {ctx, calls} = context();
  ctx.battleState = state([pokemon('old', 0), pokemon('last', 1)]);
  ctx.battleState.field.stealthRockPlayer = true;
  ctx.battleState.phase = 'switch';
  ctx.playerSwitch(1);
  assert.equal(ctx.battleState._ended, true);
  assert.equal(calls.actions, 0);
});

test('multiplayer waits for the remote forced switch before enabling moves', async () => {
  const {ctx, calls} = context();
  ctx.battleState = state([pokemon('steel', 100, {types: ['steel']})], [pokemon('fragile', 1), pokemon('a bench')]);
  Object.assign(ctx.battleState, {multiMode: true, weather: 'sand', weatherTurns: 3});
  await ctx.endOfTurn();
  assert.equal(ctx.battleState.phase, 'waiting_switch');
  assert.equal(calls.actions, 0);
  ctx.handleMultiMsg({type: 'opponent_forced_switch', index: 1});
  assert.equal(ctx.battleState.phase, 'action');
  assert.equal(ctx.getActive('ai').name, 'a bench');
  assert.equal(calls.actions, 1);
});

test('inline game scripts remain valid JavaScript', () => {
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
  assert.ok(scripts.length > 0);
  scripts.forEach((script, index) => new vm.Script(script[1], {filename: `inline-${index}.js`}));
});

function recommendedContext() {
  const {ctx, elements} = context();
  ctx.RECOMMENDED_ENTRIES = [
    {name:'기본팀', team:[1,2,3].map(id => ({id, nature:'Jolly', item:'Leftovers', ability:'Pressure', evs:{hp:4}, moves:['Tackle']}))},
    {name:'다른팀', team:[4,5,6].map(id => ({id, nature:'Modest', item:'Sitrus Berry', ability:'Pressure', evs:{spa:252}, moves:['Surf']}))},
  ];
  ctx.POKE_DB = [1,2,3,4,5,6].map(id => ({id, name:'동료'+id}));
  ctx.selectedRecoName = null; ctx.selectedTeam = [];
  const alerts=[];
  ctx.alert = text => alerts.push(text);
  ctx.closeCdd = () => {};
  ctx.updatePartyUI = () => {};
  vm.runInContext(['buildCdd','initRecommendedDropdown','applyRecommended'].map(sourceFunction).join('\n'), ctx);
  ctx.document.getElementById('recoList').querySelectorAll = () => elements.get('recoList').children;
  return {ctx, elements, alerts};
}

test('the initially displayed recommended team can be applied without selecting it again', () => {
  const {ctx, elements, alerts} = recommendedContext();
  ctx.initRecommendedDropdown();
  assert.equal(elements.get('recoBtn').textContent, '기본팀');
  ctx.applyRecommended();
  assert.deepEqual(alerts, []);
  assert.deepEqual(Array.from(ctx.selectedTeam, pokemon => pokemon.id), [1,2,3]);
  assert.equal(ctx.selectedTeam[0].selectedItem, 'Leftovers');
});

test('reopening the recommended menu preserves both its visible choice and the applied team', () => {
  const {ctx, elements, alerts} = recommendedContext();
  ctx.initRecommendedDropdown();
  elements.get('recoList').children[1].onclick({stopPropagation(){}});
  ctx.initRecommendedDropdown();
  assert.equal(elements.get('recoBtn').textContent, '다른팀');
  ctx.applyRecommended();
  assert.deepEqual(alerts, []);
  assert.deepEqual(Array.from(ctx.selectedTeam, pokemon => pokemon.id), [4,5,6]);
  assert.equal(ctx.selectedTeam[0].selectedMoves[0], 'Surf');
});
