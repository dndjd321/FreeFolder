const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'engine-client.js'), 'utf8');

function fixtureView(extra = {}) {
  return {
    battle_id: 'battle-1', side: 'p1', turn: 1, ended: false, winner: null,
    own: [6, 445, 36].map((num, slot) => ({
      slot, num, name: ['Charizard', 'Garchomp', 'Clefable'][slot], species: ['Charizard', 'Garchomp', 'Clefable'][slot],
      hp: 100, maxhp: 160, active: slot === 0, fainted: false, types: ['fire'],
      ability: 'blaze', item: 'lifeorb', stats: {atk: 104, def: 98, spa: 165, spd: 105, spe: 167},
      boosts: {atk: 2}, status: '', moves: [{id: 'flamethrower', name: 'Flamethrower', pp: 15, maxpp: 24, type: 'fire', basePower: 90, accuracy: 100, category: 'special'}],
    })),
    opponent: [{num: 3, slot: 0, species: 'Venusaur', name: 'Venusaur', hp: 75, maxhp: 100, active: true, revealed: true, types: ['grass', 'poison'], status: 'brn'},
      {slot: 1, name: '???', hp: null, maxhp: null, active: false, revealed: false},
      {slot: 2, name: '???', hp: null, maxhp: null, active: false, revealed: false}],
    request: {active: [{moves: [
      {id: 'flamethrower', name: 'Flamethrower', move: 'Flamethrower', pp: 15, maxpp: 24, basePower: 90, accuracy: 100},
      {id: 'airslash', name: 'Air Slash', move: 'Air Slash', pp: 0, maxpp: 24, disabled: true},
    ]}]},
    events: ['|switch|p1a: Charizard|Charizard, L50|100/160', '|switch|p2a: Venusaur|Venusaur, L50|75/100'],
    ...extra,
  };
}

function harness(fetcher) {
  const all = [], nodes = new Map(), calls = {requests: [], logs: [], legacy: [], renders: [], socketMessages: [], sleepDelays: []};
  class Element {
    constructor() { this.children = []; this.style = {}; this.attributes = {}; this.textContent = ''; this.classList = {add() {}, remove() {}}; all.push(this); }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); }
    setAttribute(key, value) { this.attributes[key] = value; }
  }
  for (const key of ['serverBadge', 'teamServerBadge', 'startBattleBtn', 'moveGrid', 'switchGrid', 'switchReason', 'battleScreen', 'resultOverlay', 'resultCard', 'campaignBanner', 'battleLog', 'aiHpNum']) {
    const node = new Element(); node.id = key; nodes.set(key, node);
    const parent = new Element(); parent.appendChild(node);
  }
  nodes.get('startBattleBtn').textContent = '▶ 배틀 시작!';
  const ctx = {
    console, setTimeout() { return 1; }, clearTimeout() {}, crypto: {randomUUID() { return 'command-unique'; }},
    document: {head: new Element(), getElementById(key) { return nodes.get(key) || all.find(n => n.id === key) || null; }, createElement() { return new Element(); }, querySelector() { return null; }},
    POKE_DB: [{id: 6, name: '리자몽'}, {id: 445, name: '한카리아스'}, {id: 36, name: '픽시'}, {id: 3, name: '이상해꽃'}],
    MOVE_EN_KO: {'flamethrower': '화염방사', 'air-slash': '에어슬래시'},
    ITEMS_DB: {'life-orb': {name: '생명의구슬'}}, ABILITY_EN_KO: {'blaze': '맹화'},
    TYPE_CHART: {fire: {grass: 2, poison: 1}}, gameSettings: {nickname: '트레이너', battleSpeed: 1, showAnimations: true, showTypeHints: true},
    location: {protocol: 'http:', host: 'localhost:18765'}, multiWs: null, multiState: {active: false},
    battleState: null, aiDifficulty: 'hard', turnNumber: 1,
    selectedTeam: [6, 445, 36].map(num => ({id: num, selectedMoves: ['화염방사'], selectedAbility: '맹화', selectedItem: '생명의구슬', nature: '겁쟁이', evs: {hp: 0, atk: 0, def: 4, spatk: 252, spdef: 0, spd: 252}, hp: 99999})),
    clearTurnTimer() {}, hideAllScreens() {}, scrollTo() {}, playBgm() {}, switchBattleTab() {},
    renderBattle() { calls.renders.push(structuredClone(ctx.battleState)); },
    addLog(message) { calls.logs.push(message); },
    startBattle() { calls.legacy.push('start'); }, playerMove() { calls.legacy.push('move'); },
    playerSwitch() { calls.legacy.push('switch'); }, renderActions() { calls.legacy.push('render'); },
    goTeamSelect() { ctx.battleState = null; calls.legacy.push('leave'); }, checkServer() { calls.legacy.push('check'); },
    handleMultiMsg(data) { calls.legacy.push(data?.type); },
    connectToWs(url) { calls.socketUrl = url; ctx.multiWs = {readyState: 1, send(raw) { calls.socketMessages.push(raw); }}; },
    sleep(ms) { calls.sleepDelays.push(ms); return Promise.resolve(); }, saveSettings() {},
    calcDamage() { throw new Error('Legacy combat calculation was called'); },
    execMove() { throw new Error('Legacy move execution was called'); },
    async fetch(url, options) {
      calls.requests.push({url, options});
      return fetcher ? fetcher(url, options) : {ok: true, json: async () => ({battle_id: 'battle-1', token: 'secret', revision: 0, view: fixtureView()})};
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(source, ctx, {filename: 'engine-client.js'});
  return {ctx, nodes, calls};
}

test('serializeTeam translates Korean selection and excludes client combat stats', () => {
  const {ctx} = harness();
  const team = ctx.EngineUI.serializeTeam(ctx.selectedTeam);
  assert.equal(team[0].moves[0], 'flamethrower');
  assert.equal(team[0].ability, 'blaze');
  assert.equal(team[0].item, 'lifeorb');
  assert.equal(team[0].evs.spa, 252); assert.equal(team[0].evs.spd, 0); assert.equal(team[0].evs.spe, 252);
  assert.equal(team[0].hp, undefined); assert.equal(team[0].atk, undefined);
  assert.equal(team[0].nature, '겁쟁이');
});

test('serializeTeam uses per-species English move identifiers and rejects unknown Korean moves', () => {
  const {ctx} = harness();
  ctx.selectedTeam[0].selectedMoves = ['새 기술'];
  assert.throws(() => ctx.EngineUI.serializeTeam(ctx.selectedTeam), /기술 이름/);
  ctx.selectedTeam[0].allMoves = [{name: '새 기술', nameEn: 'test-move'}];
  assert.equal(ctx.EngineUI.serializeTeam(ctx.selectedTeam)[0].moves[0], 'test-move');
});

test('server start maps the returned view to legacy display without simulating combat', async () => {
  const {ctx, calls, nodes} = harness();
  await ctx.startBattle();
  assert.equal(calls.requests[0].url, '/api/engine/battles');
  assert.equal(ctx.battleState.serverEngine, true);
  assert.equal(ctx.battleState.player.party[0].name, '리자몽');
  assert.equal(ctx.battleState.player.party[0].spatk, 165);
  assert.equal(ctx.battleState.ai.party[0].currentHp, 75);
  assert.equal(nodes.get('aiHpNum').textContent, '75%');
  assert.equal(ctx.battleState.ai.party[0].status, 'burn');
  assert.equal(ctx.battleState.ai.party[0].ability, '');
  assert.equal(ctx.battleState.ai.party[1].name, '미공개');
  assert.equal(nodes.get('moveGrid').children[1].disabled, true);
  assert.deepEqual(calls.legacy, []);
  assert.equal(nodes.get('startBattleBtn').disabled, false);
});

test('server start failure is visible and never silently falls back to legacy combat', async () => {
  const {ctx, nodes, calls} = harness(async () => ({ok: false, status: 503, json: async () => ({detail: 'engine unavailable'})}));
  await ctx.startBattle();
  assert.equal(ctx.battleState, null);
  assert.deepEqual(calls.legacy, []);
  assert.match(ctx.document.getElementById('engineNotice').textContent, /engine unavailable/);
  assert.equal(nodes.get('startBattleBtn').disabled, false);
});

test('move input submits token, revision, unique command and slot without applying local damage', async () => {
  const {ctx, calls} = harness(async () => ({ok: true, json: async () => ({battle_id: 'battle-1', token: 'secret', revision: 1, view: fixtureView({turn: 2})})}));
  ctx.EngineUI.render(fixtureView(), {battle_id: 'battle-1', token: 'secret', revision: 0});
  await ctx.playerMove(0);
  const body = JSON.parse(calls.requests[0].options.body);
  assert.deepEqual(body.action, {type: 'move', index: 0});
  assert.equal(body.revision, 0); assert.equal(body.token, 'secret'); assert.equal(body.command_id, 'command-unique');
  assert.equal(ctx.battleState.turn, 2);
  assert.equal(ctx.EngineUI.session.pending, false);
  assert.deepEqual(calls.legacy, []);
});

test('disabled or exhausted moves do not produce a server request', async () => {
  const {ctx, calls} = harness();
  ctx.EngineUI.render(fixtureView(), {token: 'secret', revision: 0});
  await ctx.playerMove(1);
  await ctx.playerMove(99);
  assert.equal(calls.requests.length, 0);
});

test('forced switch offers only living benched slots and blocks attacks', async () => {
  const {ctx, calls, nodes} = harness();
  const view = fixtureView(); view.own[0].hp = 0; view.own[0].fainted = true; view.own[2].hp = 0;
  view.request = {forceSwitch: [true]};
  const accepted = [];
  ctx.EngineUI.render(view, {mode: 'multi', sendAction: async action => { accepted.push({...action}); }});
  assert.equal(ctx.battleState.phase, 'switch');
  assert.equal(nodes.get('switchGrid').children[0].disabled, false);
  assert.equal(nodes.get('switchGrid').children[1].disabled, true);
  await ctx.playerMove(0); await ctx.playerSwitch(2); await ctx.playerSwitch(1);
  assert.deepEqual(accepted, [{type: 'switch', index: 1}]);
  assert.equal(calls.requests.length, 0);
});

test('trapping prevents voluntary switches and a waiting request blocks all inputs', async () => {
  const {ctx, calls} = harness();
  const view = fixtureView(); view.request.active[0].trapped = true;
  ctx.EngineUI.render(view, {token: 'secret'});
  await ctx.playerSwitch(1);
  ctx.EngineUI.render({...view, request: {wait: true}});
  await ctx.playerMove(0);
  assert.equal(calls.requests.length, 0);
});

test('a websocket state update clears pending input and does not repeat old log entries', async () => {
  const {ctx, calls} = harness();
  const view = fixtureView(), transport = async () => {};
  ctx.EngineUI.render(view, {mode: 'multi', sendAction: transport});
  await ctx.playerMove(0);
  assert.equal(ctx.EngineUI.session.pending, true);
  const logs = calls.logs.length;
  ctx.EngineUI.render({...view, turn: 2}, {mode: 'multi', sendAction: transport});
  assert.equal(ctx.EngineUI.session.pending, false);
  assert.equal(calls.logs.length, logs);
});

test('network failure resynchronizes using an Authorization header without a token in the URL', async () => {
  let count = 0;
  const {ctx, calls} = harness(async () => {
    count++;
    if (count === 1) throw new Error('network disconnected');
    return {ok: true, json: async () => ({battle_id: 'battle-1', revision: 1, view: fixtureView({turn: 2})})};
  });
  ctx.EngineUI.render(fixtureView(), {token: 'top-secret', revision: 0});
  await ctx.playerMove(0);
  assert.equal(calls.requests[1].url, '/api/engine/battles/battle-1');
  assert.equal(calls.requests[1].options.headers.Authorization, 'Bearer top-secret');
  assert.equal(ctx.EngineUI.session.pending, false);
  assert.equal(ctx.battleState.turn, 2);
  assert.match(ctx.document.getElementById('engineNotice').textContent, /network disconnected/);
  assert.deepEqual(calls.legacy, []);
});

test('remote names and event messages remain literal text in controls and logs', () => {
  const {ctx, calls, nodes} = harness();
  const payload = '<img src=x onerror="attack()">', view = fixtureView();
  view.own[1].num = 999999; view.own[1].species = payload;
  view.request.active[0].moves[0].id = 'unknown'; view.request.active[0].moves[0].move = payload;
  view.events.push('|message|' + payload);
  ctx.EngineUI.render(view);
  assert.equal(nodes.get('moveGrid').children[0].children[0].textContent, payload);
  assert.ok(nodes.get('switchGrid').children[0].textContent.startsWith(payload));
  assert.equal(calls.logs.at(-1), payload);
  assert.equal(nodes.get('moveGrid').children[0].innerHTML, undefined);
});

test('server result calls the supplied owner once and does not report a client-calculated winner', () => {
  const {ctx, calls} = harness();
  let completed = 0;
  const view = fixtureView({ended: true, winner: 'p1', request: {wait: true}});
  ctx.EngineUI.render(view, {onEnd: () => { completed++; }});
  ctx.EngineUI.render(view);
  assert.equal(completed, 1);
  assert.equal(ctx.battleState.phase, 'ended');
  assert.deepEqual(calls.legacy, []);
});

test('legacy campaign actions continue to use their existing path', async () => {
  const {ctx, calls} = harness();
  ctx.battleState = {campaignMode: true};
  await ctx.playerMove(0); await ctx.playerSwitch(1); ctx.renderActions();
  assert.deepEqual(calls.legacy, ['move', 'switch', 'render']);
});

test('multiplayer connects to the current origin and attaches canonical engine sets to team messages', () => {
  const {ctx, calls} = harness();
  ctx.connectToWs('ws://127.0.0.1:8765/ws/multi');
  assert.equal(calls.socketUrl, 'ws://localhost:18765/ws/multi');
  ctx.multiWs.send(JSON.stringify({type: 'team_ready', team: ctx.selectedTeam}));
  const sent = JSON.parse(calls.socketMessages[0]);
  assert.equal(sent.team.length, 3);
  assert.equal(sent.team[0].engineSet.moves[0], 'flamethrower');
  assert.equal(sent.team[0].engineSet.hp, undefined);
  ctx.multiWs.send(JSON.stringify({type: 'entry_ready', team: [...ctx.selectedTeam, ...ctx.selectedTeam]}));
  assert.equal(JSON.parse(calls.socketMessages[1]).team.length, 6);
});

test('engine_state uses the latest request token for websocket actions', async () => {
  const {ctx, calls} = harness();
  ctx.connectToWs('ws://localhost/ws/multi');
  ctx.handleMultiMsg({type: 'engine_state', battle_id: 'multi-1', role: 'p2', view: fixtureView({battle_id: 'multi-1', side: 'p2'}), request_token: 'first-request', opp_nickname: '<img src=x>'});
  await ctx.playerMove(0);
  let sent = JSON.parse(calls.socketMessages.at(-1));
  assert.equal(sent.type, 'battle_action'); assert.equal(sent.request_token, 'first-request');
  assert.equal(sent.command_id, 'command-unique'); assert.equal(ctx.multiState.role, 'p2');
  ctx.handleMultiMsg({type: 'engine_state', battle_id: 'multi-1', role: 'p2', view: fixtureView({battle_id: 'multi-1', side: 'p2', turn: 2}), request_token: 'second-request'});
  await ctx.playerSwitch(1);
  sent = JSON.parse(calls.socketMessages.at(-1));
  assert.equal(sent.request_token, 'second-request'); assert.equal(sent.action, 'switch');
  assert.equal(calls.requests.length, 0);
});

test('authoritative multiplayer results never emit a client battle_end message', () => {
  const {ctx, calls, nodes} = harness();
  ctx.connectToWs('ws://localhost/ws/multi');
  ctx.handleMultiMsg({type: 'engine_state', battle_id: 'multi-1', role: 'p2', view: fixtureView({battle_id: 'multi-1', side: 'p2', ended: true, winner: 'p2', request: {wait: true}}), request_token: 'request'});
  assert.equal(nodes.get('resultCard').children[1].textContent, 'VICTORY!');
  assert.equal(calls.socketMessages.length, 0);
  assert.equal(ctx.battleState.phase, 'ended');
});

test('a server rejection clears pending controls and a disconnect aborts instead of declaring a winner', async () => {
  const {ctx, calls} = harness();
  ctx.connectToWs('ws://localhost/ws/multi');
  ctx.handleMultiMsg({type: 'engine_state', battle_id: 'multi-1', role: 'p1', view: fixtureView({battle_id: 'multi-1'}), request_token: 'request'});
  await ctx.playerMove(0);
  assert.equal(ctx.EngineUI.session.pending, true);
  ctx.handleMultiMsg({type: 'error', message: '<img src=x> invalid action'});
  assert.equal(ctx.EngineUI.session.pending, false);
  assert.equal(ctx.document.getElementById('engineNotice').textContent, '<img src=x> invalid action');
  ctx.handleMultiMsg({type: 'opponent_left'});
  assert.equal(ctx.battleState.phase, 'ended');
  assert.ok(!calls.legacy.includes('opponent_left'));
  assert.ok(calls.socketMessages.every(raw => JSON.parse(raw).type !== 'battle_end'));
});

test('settings control type hints, motion styling and legacy animation delays', async () => {
  const {ctx, nodes, calls} = harness();
  const view = fixtureView(); view.request.active[0].moves[0].type = 'fire';
  ctx.EngineUI.render(view);
  assert.equal(nodes.get('moveGrid').children[0].children.length, 3);
  ctx.gameSettings.showTypeHints = false;
  ctx.gameSettings.showAnimations = false;
  ctx.saveSettings();
  assert.equal(nodes.get('moveGrid').children[0].children.length, 2);
  assert.match(ctx.document.getElementById('engineMotionStyle').textContent, /animation:none/);
  await ctx.sleep(1000);
  assert.equal(calls.sleepDelays.length, 0);
  ctx.gameSettings.showAnimations = true; ctx.gameSettings.battleSpeed = 2;
  await ctx.sleep(1000);
  assert.equal(calls.sleepDelays[0], 500);
});

test('all built-in recommended teams serialize with the real embedded translation data', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'pokemon_battle_v3.html'), 'utf8');
  const firstScript = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const ctx = {document: {getElementById() { return null; }, querySelector() { return null; }}};
  ctx.window = ctx; vm.createContext(ctx);
  vm.runInContext(firstScript.slice(0, firstScript.indexOf('let battleState = null;')), ctx);
  vm.runInContext(source, ctx);
  vm.runInContext(`globalThis.recommendedTeams = RECOMMENDED_ENTRIES.map(entry => ({name:entry.name, team:entry.team.map(t => Object.assign({}, POKE_DB.find(p => p.id === t.id), {nature:t.nature, selectedItem:t.item, selectedAbility:t.ability, evs:t.evs, selectedMoves:t.moves}))}));`, ctx);
  for (const recommended of ctx.recommendedTeams) {
    const serialized = ctx.EngineUI.serializeTeam(recommended.team);
    assert.equal(serialized.length, 3, recommended.name);
    serialized.forEach(p => p.moves.forEach(move => assert.match(move, /^[a-z0-9 '\-:.]+$/i, recommended.name)));
  }
});
