const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const engineSource = fs.readFileSync(path.join(root, 'web', 'engine-client.js'), 'utf8');
const runSource = fs.readFileSync(path.join(root, 'web', 'rogue-client.js'), 'utf8');
const KEY = 'freefolder_roguelike_v1';
const HISTORY_KEY = 'freefolder_roguelike_history_v1';
const RECORD_PREFIX = 'freefolder_roguelike_run_v2:';
const REMOVED_PREFIX = 'freefolder_roguelike_removed_v2:';
const PENDING_PREFIX = 'freefolder_roguelike_pending_v1:';
let cryptoSequence = 0;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function view(turn = 1) {
  return {
    battle_id: 'battle-1', side: 'p1', turn, ended: false, winner: null,
    own: [6, 445, 36].map((num, slot) => ({slot, num, species: 'Charizard', name: 'Charizard', hp: 100, maxhp: 150,
      active: slot === 0, types: ['fire'], stats: {}, boosts: {}, moves: [], status: ''})),
    opponent: [{slot: 0, num: 3, species: 'Venusaur', name: 'Venusaur', hp: 100, maxhp: 100, active: true, revealed: true, types: ['grass']}],
    request: {active: [{moves: [{id: 'flamethrower', move: 'Flamethrower', pp: 10, maxpp: 15, basePower: 90, type: 'fire'}]}]}, events: [],
  };
}
function run(extra = {}) {
  return {
    run_id: 'run-1', token: 'run-secret', run_revision: 0, phase: 'node', total_nodes: 20, node_index: 0, completed_nodes: 0,
    node: {id: 'node-0', index: 0, kind: 'battle', label: '숲', danger: '일반'}, money: 100, relics: [],
    party: ['Charizard', 'Garchomp', 'Clefable'].map(species => ({species, item: 'Leftovers', moves: ['Flamethrower'], resource: {hp: 100, maxhp: 150, item: '', moves: [{pp: 10}]}})),
    rewards: [], paths: [], recruit_candidates: [], rerolls_left: 1,
    ...extra,
  };
}
function catalog() {
  const starter_pool = ['Charizard', 'Garchomp', 'Clefable', 'Venusaur'].map(species => ({species, moves: ['Flamethrower'], ability: 'Blaze', item: '', nature: 'Serious'}));
  return {starter_pool, starter_team: starter_pool.slice(0, 3), species_ids: {charizard: 6, garchomp: 445, clefable: 36, venusaur: 3}};
}
function recoveryPreviewFixture(kind = 'heal') {
  // The public shape comes from services.run_preparation.resource_preview.
  // Values include a fainted member and capped resources so the renderer must
  // use the server's actual gains rather than calculate a label's percentage.
  const before = [
    {hp: 20, maxhp: 100, pp: 1, maxpp: 10, status: 'brn', sleep_turns: null, moves: [{id: 'flamethrower', pp: 1, maxpp: 10}]},
    {hp: 0, maxhp: 100, pp: 5, maxpp: 10, status: '', sleep_turns: null, moves: [{id: 'earthquake', pp: 5, maxpp: 10}]},
    {hp: 100, maxhp: 100, pp: 10, maxpp: 10, status: '', sleep_turns: null, moves: [{id: 'moonblast', pp: 10, maxpp: 10}]},
  ];
  if (kind === 'none') for (const row of before) { row.hp = 100; row.pp = 10; row.status = ''; row.moves[0].pp = 10; }
  const after = JSON.parse(JSON.stringify(before));
  if (kind === 'heal') after[0].hp = 90;
  if (kind === 'pp') { after[0].pp = 10; after[0].moves[0].pp = 10; after[1].pp = 10; after[1].moves[0].pp = 10; }
  if (kind === 'revive') after[1].hp = 50;
  if (kind === 'cure') after[0].status = '';
  const hp_gain = kind === 'heal' ? 70 : kind === 'revive' ? 50 : 0, pp_gain = kind === 'pp' ? 14 : 0;
  return {before: {hp: kind === 'none' ? 300 : 120, maxhp: 300, pp: kind === 'none' ? 30 : 16, maxpp: 30, fainted: kind === 'none' ? 0 : 1, status_count: kind === 'none' ? 0 : 1},
    after: {hp: kind === 'none' ? 300 : 120 + hp_gain, maxhp: 300, pp: kind === 'none' ? 30 : 16 + pp_gain, maxpp: 30, fainted: ['revive','none'].includes(kind) ? 0 : 1, status_count: ['cure','none'].includes(kind) ? 0 : 1},
    hp_gain, pp_gain, revived: kind === 'revive' ? 1 : 0, cured: kind === 'cure' ? 1 : 0, available: kind !== 'none',
    party: before.map((row, slot) => ({slot, before: row, after: after[slot], hp_gain: after[slot].hp - row.hp,
      pp_gain: after[slot].pp - row.pp, revived: kind === 'revive' && slot === 1, cured: kind === 'cure' && slot === 0}))};
}
function partyForPreview(preview) {
  return run().party.map((pokemon, slot) => ({...pokemon, resource: {
    ...pokemon.resource, slot, hp:preview.party[slot].before.hp, maxhp:preview.party[slot].before.maxhp,
    status:preview.party[slot].before.status, moves:preview.party[slot].before.moves.map(move => ({...move,
      name_ko:({flamethrower:'화염방사',earthquake:'지진',moonblast:'문포스'})[move.id]})),
  }}));
}
function ok(data) { return {ok: true, json: async () => data}; }

function harness(handler, sharedStorage) {
  const all = [], stored = sharedStorage || new Map(), staticNodes = new Map(), listeners = new Map();
  const calls = {requests: [], menu: 0};
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.style = {}; this.attributes = {}; this._text = '';
      this.classList = {add() {}, remove() {}}; all.push(this);
    }
    set textContent(text) { this.replaceChildren(); this._text = String(text ?? ''); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    appendChild(child) {
      if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
      child.parentNode = this; this.children.push(child); return child;
    }
    replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this._text = ''; children.forEach(child => this.appendChild(child)); }
    setAttribute(key, value) { this.attributes[key] = value; }
    get isConnected() { return this === document.body || this === document.head || !!this.parentNode?.isConnected; }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(selector === 'button' && child.tagName === 'BUTTON' ? [child] : []), ...child.querySelectorAll(selector)]); }
  }
  const document = {
    readyState: 'complete', body: new Element('body'), head: new Element('head'),
    getElementById(key) { return staticNodes.get(key) || all.find(node => node.id === key && node.isConnected) || null; },
    createElement(tag) { return new Element(tag); }, querySelector() { return null; },
  };
  for (const key of ['contentHubScreen', 'battleScreen', 'resultOverlay', 'resultCard', 'campaignBanner', 'battleLog', 'startBattleBtn', 'serverBadge', 'teamServerBadge', 'moveGrid', 'switchGrid', 'switchReason', 'aiHpNum']) {
    const node = document.createElement(key === 'startBattleBtn' ? 'button' : 'div'); node.id = key; staticNodes.set(key, node); document.body.appendChild(node);
  }
  const ctx = {
    console, document, setTimeout() { return 1; }, clearTimeout() {}, crypto: {randomUUID: () => 'command-1',
      getRandomValues(bytes) { bytes.fill(++cryptoSequence); return bytes; }},
    localStorage: {get length() { return stored.size; }, key: index => [...stored.keys()][index] ?? null,
      getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key)},
    POKE_DB: [{id: 6, name: '리자몽'}, {id: 445, name: '한카리아스'}, {id: 36, name: '픽시'}, {id: 3, name: '이상해꽃'}],
    MOVE_EN_KO: {flamethrower: '화염방사'}, ITEMS_DB: {}, ABILITY_EN_KO: {}, TYPE_CHART: {}, gameSettings: {},
    battleState: null, selectedTeam: [], turnNumber: 1, multiState: {active: false}, multiWs: null,
    scrollTo() {}, addEventListener(type, handler) { listeners.set(type, handler); }, clearTurnTimer() {}, renderBattle() {}, renderActions() {}, addLog() {}, playBgm() {}, switchBattleTab() {},
    startBattle() {}, playerMove() {}, playerSwitch() {}, goTeamSelect() {}, checkServer() {}, connectToWs() {}, handleMultiMsg() {},
    sleep: () => Promise.resolve(), saveSettings() {}, hideAllScreens() {},
    goContentHub() { calls.menu++; ctx.hideAllScreens(); },
    async fetch(url, options) {
      const record = {url, options, body: options.body ? JSON.parse(options.body) : undefined}; calls.requests.push(record);
      if (handler) { const result = await handler(record, ctx); if (result) return result; }
      if (url === '/api/runs/status') return ok(catalog());
      if (url === '/api/runs') return ok(run());
      return ok(run());
    },
  };
  ctx.window = ctx; vm.createContext(ctx);
  vm.runInContext(engineSource, ctx, {filename: 'engine-client.js'});
  vm.runInContext(runSource, ctx, {filename: 'rogue-client.js'});
  const screen = () => document.getElementById('rogueScreen');
  const buttons = () => screen().querySelectorAll('button');
  const button = text => {
    const result = buttons().find(b => b.textContent.includes(text));
    assert.ok(result, 'Missing button: ' + text + '; found ' + buttons().map(b => b.textContent).join(', '));
    return result;
  };
  return {ctx, calls, stored, screen, buttons, button, nodes: staticNodes, listeners};
}

function descendants(node) { return node.children.flatMap(child => [child, ...descendants(child)]); }
function creationFixture(extra = {}) {
  return {version: 1, creation_id: 'a'.repeat(64), creation_secret: 'b'.repeat(64), created_at: 123,
    payload: {team: catalog().starter_team, difficulty: 'hard', seed: 71}, ...extra};
}
function journalFixture() {
  // Captured public_journal values for created/start/end/reward/reroll/path/
  // rest/shop/recruit/order. These counters come from the Python helper;
  // the UI must display them rather than reconstruct them from recent events.
  const member = (species, hp, pp, maxpp, status = '') => ({species, hp, maxhp: 100, pp, maxpp, status});
  const fresh = {node_index: 0, node_label: '숲', hp: 300, maxhp: 300, pp: 34, maxpp: 34, fainted: 0, money: 100,
    party: [member('Charizard', 100, 10, 10), member('Garchomp', 100, 8, 8), member('Clefable', 100, 16, 16)]};
  const battle = {...fresh, hp: 115, pp: 23, fainted: 1, money: 120,
    party: [member('Charizard', 35, 5, 10, 'brn'), member('Garchomp', 0, 2, 8, 'slp'), member('Clefable', 80, 16, 16)]};
  const reward = {...battle, hp: 165, party: [member('Charizard', 85, 5, 10, 'brn'), ...battle.party.slice(1)]};
  const reroll = {...reward, money: 115};
  const path = {...reroll, node_index: 1, node_label: '고요한 쉼터'};
  const rest = {...path, hp: 215, fainted: 0, party: [path.party[0], member('Garchomp', 50, 2, 8), path.party[2]]};
  const shop = {...rest, pp: 34, money: 90, party: [member('Charizard', 85, 10, 10, 'brn'), member('Garchomp', 50, 8, 8), rest.party[2]]};
  const recruit = {...shop, hp: 230, party: [member('Venusaur', 100, 10, 10), ...shop.party.slice(1)]};
  const order = {...recruit, party: [recruit.party[1], recruit.party[0], recruit.party[2]]};
  const rows = [
    ['created', fresh, fresh, {difficulty: 'hard'}],
    ['battle_start', fresh, fresh, {opponent_label: '숲의 수호자'}],
    ['battle_end', fresh, battle, {winner: 'p1', turns: 7, post_battle_heal: 5}],
    ['reward', battle, reward, {reward_id: 'heal', reward_kind: 'heal', label: '따뜻한 열매'}],
    ['reroll', reward, reroll, {rerolls_left: 0, cost: 5}],
    ['path', reroll, path, {path_id: 'node-1-0', node_kind: 'rest', theme: 'forest', label: '고요한 쉼터'}],
    ['rest', path, rest, {option: 'revive'}],
    ['shop', rest, shop, {offer_id: 'pp', offer_kind: 'pp', price: 25, label: '기술 보충', action: 'buy'}],
    ['recruit', shop, recruit, {candidate_id: 'venusaur', species: 'Venusaur', replace_index: 0, replaced_species: 'Charizard', skipped: false}],
    ['party_order', recruit, order, {order: [1, 0, 2], lead_species: 'Garchomp'}],
  ];
  return JSON.parse(JSON.stringify({version: 1, available: true, from_node: 0, complete_history: true, total_events: 10, dropped_events: 0,
    counts: {created: 1, battle_start: 1, battle_end: 1, reward: 1, reroll: 1, path: 1, rest: 1, shop: 1, recruit: 1, party_order: 1},
    totals: {battles_started: 1, battles_finished: 1, wins: 1, losses: 0, draws: 0, turns: 7, money_earned: 20, money_spent: 30,
      hp_recovered: 105, pp_recovered: 11, revived: 1, cured: 1, recruits: 1},
    events: rows.map(([kind, before, after, details], index) => ({sequence: index + 1, kind, node_index: after.node_index,
      node_label: after.node_label, before, after, details}))}));
}

test('opening the run hub starts a recommended canonical team and stores only resume credentials', async () => {
  const {ctx, calls, stored, button} = harness();
  await ctx.RunUI.open();
  await button('추천 파티').onclick();
  const request = calls.requests.find(r => r.url === '/api/runs');
  assert.equal(request.body.team.length, 3); assert.equal(request.body.team[0].species, 'Charizard');
  assert.equal(request.body.difficulty, 'normal');
  assert.equal(ctx.RunUI.state.phase, 'node');
  assert.deepEqual(JSON.parse(stored.get(KEY)), {run_id: 'run-1', token: 'run-secret'});
});

test('battle action uses the run endpoint and transitions to reward without stale EngineUI controls', async () => {
  const {ctx, calls, screen, nodes} = harness(({url}) => {
    if (url.endsWith('/battle/start')) return ok(run({phase: 'battle', battle: {battle_id: 'battle-1', revision: 0, view: view()}}));
    if (url.endsWith('/battle/action')) return ok(run({phase: 'reward', rewards: [{id: 'reward-1', label: 'HP 회복'}]}));
  });
  ctx.RunUI.render(run());
  await screen().querySelectorAll('button').find(b => b.textContent.includes('전투 시작')).onclick();
  assert.equal(ctx.EngineUI.session.mode, 'run');
  await nodes.get('moveGrid').children[0].onclick();
  const action = calls.requests.find(r => r.url.endsWith('/battle/action'));
  assert.equal(action.body.revision, 0); assert.equal(action.body.action.type, 'move');
  assert.equal(action.options.headers.Authorization, 'Bearer run-secret');
  assert.equal(ctx.RunUI.state.phase, 'reward');
  assert.equal(ctx.EngineUI.session, null); assert.equal(ctx.battleState, null);
  assert.equal(screen().style.display, 'block');
  assert.ok(screen().textContent.includes('보상 하나를 선택하세요'));
});

test('successive battle updates preserve the run transport and use each new revision', async () => {
  let revision = 0;
  const {ctx, calls, nodes} = harness(({url}) => {
    if (url.endsWith('/battle/action')) { revision++; return ok(run({phase: 'battle', battle: {battle_id: 'battle-1', revision, view: view(revision + 1)}})); }
  });
  ctx.RunUI.render(run({phase: 'battle', battle: {battle_id: 'battle-1', revision: 0, view: view()}}));
  await nodes.get('moveGrid').children[0].onclick();
  await nodes.get('moveGrid').children[0].onclick();
  const actions = calls.requests.filter(r => r.url.endsWith('/battle/action'));
  assert.deepEqual(actions.map(r => r.body.revision), [0, 1]);
  assert.equal(ctx.EngineUI.session.mode, 'run'); assert.equal(ctx.EngineUI.session.pending, false);
});

test('recruitment reads candidate.team and submits its offered ID and replacement slot', async () => {
  const {ctx, calls, button, screen} = harness();
  await ctx.RunUI.open();
  ctx.RunUI.render(run({phase: 'recruit', recruit_candidates: [{id: 'candidate-offer', team: {species: 'Venusaur'}}]}));
  assert.ok(screen().textContent.includes('이상해꽃'));
  await button('리자몽와 교체').onclick();
  const request = calls.requests.find(r => r.url.endsWith('/recruit'));
  assert.equal(request.body.candidate_id, 'candidate-offer'); assert.equal(request.body.replace_index, 0);
});

test('bought shop offers are disabled and consumed held items remain absent', () => {
  const {ctx, button, screen} = harness();
  ctx.RunUI.render(run({node: {kind: 'shop', label: '상점'}, shop: [{id: 'offer', label: '회복', price: 10, bought: true}]}));
  assert.equal(button('구매 완료').disabled, true);
  assert.ok(screen().textContent.includes('도구 없음'));
  assert.ok(!screen().textContent.includes('Leftovers'));
});

test('reward and path selections submit only the offered identifiers', async () => {
  const {ctx, calls, button} = harness(({url}) => {
    if (url.endsWith('/reward')) return ok(run({phase: 'path', paths: [{id: 'node-1-b', label: '바위 동굴', danger: '정예'}]}));
  });
  ctx.RunUI.render(run({phase: 'reward', rewards: [{id: 'reward-id', label: '돈 획득'}]}));
  await button('돈 획득').onclick(); await button('바위 동굴').onclick();
  assert.equal(calls.requests[0].body.reward_id, 'reward-id');
  assert.equal(calls.requests[1].body.path_id, 'node-1-b');
});

test('a delayed action cannot reopen the run screen after navigation to the menu', async () => {
  const pending = deferred();
  const {ctx, calls, button, screen} = harness(({url}) => url.endsWith('/reward') ? pending.promise : undefined);
  ctx.RunUI.render(run({phase: 'reward', rewards: [{id: 'reward-id', label: '회복 선택'}]}));
  const action = button('회복 선택').onclick();
  button('회복 선택').onclick();
  ctx.goContentHub();
  pending.resolve(ok(run({phase: 'path', paths: [{id: 'node-1', label: '동굴'}]})));
  await action;
  assert.equal(screen().style.display, 'none');
  assert.equal(calls.requests.filter(r => r.url.endsWith('/reward')).length, 1);
  assert.equal(ctx.RunUI.state.phase, 'path');
});

test('lost action response resumes the checkpoint instead of sending the same selection twice', async () => {
  const {ctx, calls, button, screen} = harness(({url}) => {
    if (url.endsWith('/reward')) throw new Error('connection lost');
    if (url.endsWith('/resume')) return ok(run({phase: 'path', paths: [{id: 'rest', label: '쉼터'}]}));
  });
  ctx.RunUI.render(run({phase: 'reward', rewards: [{id: 'reward-id', label: '회복 선택'}]}));
  await button('회복 선택').onclick();
  assert.equal(calls.requests.filter(r => r.url.endsWith('/reward')).length, 1);
  const resumed = calls.requests.find(r => r.url.endsWith('/resume'));
  assert.equal(resumed.options.headers.Authorization, 'Bearer run-secret');
  assert.equal(ctx.RunUI.state.phase, 'path'); assert.ok(screen().textContent.includes('connection lost'));
});

test('an error and failed recovery restore the old controls for a later retry', async () => {
  const {ctx, button} = harness(() => { throw new Error('offline'); });
  ctx.RunUI.render(run({phase: 'reward', rewards: [{id: 'reward-id', label: '회복 선택'}]}));
  const choice = button('회복 선택');
  await choice.onclick();
  assert.equal(choice.disabled, false);
  assert.equal(ctx.RunUI.state.phase, 'reward');
});

test('old battle callbacks cannot submit an action or finish a different run', async () => {
  const {ctx, calls} = harness();
  ctx.RunUI.render(run({phase: 'battle', battle: {battle_id: 'battle-1', revision: 0, view: view()}}));
  const oldAction = ctx.EngineUI.session.sendAction, oldEnd = ctx.EngineUI.session.onEnd;
  ctx.goContentHub();
  ctx.RunUI.render(run({run_id: 'run-2', token: 'second-secret', phase: 'battle', battle: {battle_id: 'battle-2', revision: 0, view: {...view(), battle_id: 'battle-2'}}}));
  await assert.rejects(oldAction({type: 'move', index: 0}), /화면이 변경/);
  oldEnd();
  assert.equal(calls.requests.length, 0);
});

test('late catalog response does not reopen a screen after the loading menu button is used', async () => {
  const pending = deferred();
  const {ctx, button, screen} = harness(({url}) => url.endsWith('/status') ? pending.promise : undefined);
  const opening = ctx.RunUI.open();
  await button('메뉴로').onclick();
  pending.resolve(ok(catalog())); await opening;
  assert.equal(screen().style.display, 'none');
});

test('direct resume opens its own screen and restores the saved run with bearer authentication', async () => {
  const {ctx, stored, calls, screen} = harness(({url}) => url.endsWith('/resume') ? ok(run({phase: 'path', paths: []})) : undefined);
  stored.set(KEY, JSON.stringify({run_id: 'run-1', token: 'saved-secret'}));
  await ctx.RunUI.resume();
  assert.equal(ctx.RunUI.state.phase, 'path'); assert.equal(screen().style.display, 'block');
  assert.equal(calls.requests[0].options.headers.Authorization, 'Bearer saved-secret');
});

test('remote reward descriptions render as text and malformed saved credentials are ignored', async () => {
  const {ctx, stored, button, screen} = harness();
  stored.set(KEY, '{invalid');
  await ctx.RunUI.open();
  assert.ok(!screen().textContent.includes('이어하기'));
  ctx.RunUI.render(run({phase: 'reward', rewards: [{id: 'reward-id', label: '<img src=x>', description: '<script>attack()</script>'}]}));
  const reward = button('<img src=x>');
  assert.equal(reward.innerHTML, undefined); assert.ok(reward.textContent.includes('<script>attack()</script>'));
});

test('rest offers a single-use revive choice through the same offered operation', async () => {
  const {ctx, calls, button} = harness();
  ctx.RunUI.render(run({node: {kind: 'rest', label: '쉼터'}}));
  await button('부활').onclick();
  assert.equal(calls.requests[0].url, '/api/runs/run-1/rest');
  assert.equal(calls.requests[0].body.option, 'revive');
});

test('a legacy resume credential migrates into bounded history without an eager resume request', async () => {
  const {ctx, stored, calls, screen} = harness();
  stored.set(KEY, JSON.stringify({run_id: 'old-run', token: 'old-secret', checkpoint: 'must-not-copy'}));
  await ctx.RunUI.open();
  const saved = JSON.parse(stored.get(HISTORY_KEY));
  assert.equal(saved.version, 1); assert.equal(saved.runs.length, 1);
  assert.equal(saved.runs[0].run_id, 'old-run'); assert.equal(saved.runs[0].token, 'old-secret');
  assert.equal(saved.runs[0].checkpoint, undefined);
  assert.equal(saved.runs[0].difficulty, 'normal');
  assert.ok(screen().textContent.includes('상태 확인 전'));
  assert.ok(!screen().textContent.includes('old-secret'));
  assert.deepEqual(calls.requests.map(r => r.url), ['/api/runs/status']);
});

test('starting a new expedition preserves older credentials and resumes the selected run with only a bearer token', async () => {
  const {ctx, stored, calls, button, screen} = harness(({url}) => {
    if (url === '/api/runs') return ok(run({run_id: 'run-new', token: 'new-secret'}));
    if (url === '/api/runs/run-old/resume') return ok(run({run_id: 'run-old', token: 'old-secret', phase: 'path'}));
  });
  ctx.RunUI.render(run({run_id: 'run-old', token: 'old-secret', node_index: 3, completed_nodes: 3}));
  await ctx.RunUI.open(); await button('추천 파티').onclick(); await ctx.RunUI.open();
  let saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.deepEqual(saved.map(p => p.run_id), ['run-new', 'run-old']);
  assert.equal(saved[1].completed_nodes, 3);
  assert.equal(saved[1].resource, undefined); assert.equal(saved[1].battle, undefined);
  assert.ok(!screen().textContent.includes('old-secret')); assert.ok(!screen().textContent.includes('new-secret'));
  await button('이 탐험 이어하기').onclick();
  const resumed = calls.requests.find(r => r.url === '/api/runs/run-old/resume');
  assert.equal(resumed.options.headers.Authorization, 'Bearer old-secret');
  assert.deepEqual(resumed.body, {}); assert.ok(!resumed.url.includes('old-secret'));
  assert.equal(ctx.RunUI.state.run_id, 'run-old');
  saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.deepEqual(saved.map(p => p.run_id), ['run-old', 'run-new']);
  assert.equal(saved.filter(p => p.run_id === 'run-old').length, 1);
});

test('history rejects malformed credentials and bounds summaries without truncating valid access records', async () => {
  const {ctx, stored, calls, screen} = harness();
  stored.set(HISTORY_KEY, JSON.stringify({version: 1, runs: [
    {run_id: 'https://evil.example/run', token: 'secret'},
    {run_id: 'unsafe', token: 'secret\r\nAuthorization:bad'},
    {run_id: 'valid', token: 'first-secret', phase: '<script>', node_index: -9, total_nodes: 999999, completed_nodes: 1e50,
      node_label: '<img src=x onerror=attack()>', party: ['<script>attack()</script>'], updated_at: Infinity},
    {run_id: 'valid', token: 'duplicate-secret'},
    ...Array.from({length: 20}, (_, i) => ({run_id: 'extra-' + i, token: 'secret-' + i})),
  ]}));
  await ctx.RunUI.open();
  assert.ok(!screen().textContent.includes('evil.example'));
  assert.ok(screen().textContent.includes('<img src=x onerror=attack()>'));
  assert.ok(screen().textContent.includes('<script>attack()</script>'));
  ctx.RunUI.render(run({run_id: 'valid', token: 'first-secret'}));
  const saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.equal(saved.length, 21); assert.equal(saved.filter(p => p.run_id === 'valid').length, 1);
  assert.equal(saved[0].token, 'first-secret'); assert.equal(saved[0].node_index, 0);
  assert.equal(saved[0].total_nodes, 20); assert.equal(saved[0].completed_nodes, 0);
  assert.deepEqual(calls.requests.map(r => r.url), ['/api/runs/status']);
});

test('ten active expeditions block another creation rather than silently evicting an active credential', async () => {
  const {ctx, stored, calls, button, screen} = harness();
  const runs = Array.from({length: 10}, (_, i) => ({run_id: 'active-' + i, token: 'secret-' + i, phase: 'battle'}));
  stored.set(HISTORY_KEY, JSON.stringify({version: 1, runs}));
  await ctx.RunUI.open();
  assert.equal(button('추천 파티').disabled, true);
  await button('추천 파티').onclick();
  assert.equal(calls.requests.filter(r => r.url === '/api/runs').length, 0);
  assert.deepEqual(JSON.parse(stored.get(HISTORY_KEY)).runs.map(p => p.run_id), runs.map(p => p.run_id));
  assert.ok(screen().textContent.includes('진행 중인 탐험이 10개입니다.'));
});

test('a full history makes space from completed records while preserving active expeditions', async () => {
  const {ctx, stored, button} = harness(({url}) => url === '/api/runs' ? ok(run({run_id: 'new-run', token: 'new-secret'})) : undefined);
  const runs = Array.from({length: 10}, (_, i) => ({run_id: 'saved-' + i, token: 'secret-' + i, phase: i === 2 ? 'complete' : 'node'}));
  stored.set(HISTORY_KEY, JSON.stringify({version: 1, runs}));
  await ctx.RunUI.open(); assert.equal(button('추천 파티').disabled, false); await button('추천 파티').onclick();
  const saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.equal(saved.length, 10); assert.equal(saved[0].run_id, 'new-run');
  assert.ok(!saved.some(p => p.run_id === 'saved-2'));
  assert.ok(runs.filter(p => p.phase === 'node').every(p => saved.some(row => row.run_id === p.run_id)));
});

test('a missing selected run stays in history and never removes or probes another run', async () => {
  let restored = false;
  const {ctx, stored, calls, screen} = harness(({url}) => {
    if (url === '/api/runs/gone-run/resume') return restored ? ok(run({run_id: 'gone-run', token: 'gone-secret'})) : {ok: false, status: 404, json: async () => ({detail: 'not found'})};
  });
  ctx.RunUI.render(run({run_id: 'other-run', token: 'other-secret'}));
  ctx.RunUI.render(run({run_id: 'gone-run', token: 'gone-secret'}));
  await ctx.RunUI.open(); await ctx.RunUI.resume('gone-run');
  let saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.equal(saved.length, 2); assert.equal(saved.find(p => p.run_id === 'gone-run').missing, true);
  assert.equal(saved.find(p => p.run_id === 'other-run').missing, false);
  assert.ok(screen().textContent.includes('서버에서 찾지 못함'));
  assert.ok(!screen().textContent.includes('gone-secret'));
  assert.equal(calls.requests.filter(r => r.url.endsWith('/resume')).length, 1);
  restored = true; await ctx.RunUI.resume('gone-run');
  saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.equal(saved.find(p => p.run_id === 'gone-run').missing, false); assert.equal(saved.length, 2);
});

test('a network failure preserves all history and does not label the selected run missing', async () => {
  const {ctx, stored, screen} = harness(({url}) => { if (url.endsWith('/resume')) throw new Error('offline'); });
  ctx.RunUI.render(run({run_id: 'other-run', token: 'other-secret'}));
  ctx.RunUI.render(run());
  await ctx.RunUI.open(); await ctx.RunUI.resume('run-1');
  const saved = JSON.parse(stored.get(HISTORY_KEY)).runs;
  assert.equal(saved.length, 2); assert.equal(saved[0].missing, false);
  assert.ok(screen().textContent.includes('offline'));
});

test('removing a history entry requires confirmation and only removes this browsers credential', async () => {
  const {ctx, stored, calls, button, screen} = harness();
  ctx.RunUI.render(run({run_id: 'older-run', token: 'older-secret'})); ctx.RunUI.render(run());
  await ctx.RunUI.open();
  const requestsBefore = calls.requests.length;
  await button('이 기기 목록에서 제거').onclick();
  assert.equal(JSON.parse(stored.get(HISTORY_KEY)).runs.length, 2);
  assert.ok(screen().textContent.includes('서버의 탐험은 삭제되지 않습니다'));
  await button('취소').onclick();
  assert.equal(button('이 기기 목록에서 제거').disabled, false);
  await button('이 기기 목록에서 제거').onclick(); await button('목록에서 제거 확인').onclick();
  assert.deepEqual(JSON.parse(stored.get(HISTORY_KEY)).runs.map(p => p.run_id), ['older-run']);
  assert.deepEqual(JSON.parse(stored.get(KEY)), {run_id: 'older-run', token: 'older-secret'});
  await button('이 기기 목록에서 제거').onclick(); await button('목록에서 제거 확인').onclick();
  assert.equal(stored.has(KEY), false); assert.equal(JSON.parse(stored.get(HISTORY_KEY)).runs.length, 0);
  assert.equal(calls.requests.length, requestsBefore);
  await ctx.RunUI.open(); assert.ok(!screen().textContent.includes('이어하기'));
});

test('unavailable local storage keeps current and prior credentials in memory with a visible persistence warning', async () => {
  const {ctx, button, screen, calls} = harness(({url}) => url === '/api/runs' ? ok(run({run_id: 'new-run', token: 'new-secret'})) : undefined);
  ctx.localStorage.getItem = () => { throw new Error('storage disabled'); };
  ctx.localStorage.setItem = () => { throw new Error('storage disabled'); };
  ctx.RunUI.render(run());
  assert.ok(screen().textContent.includes('현재 탭에서만 유지'));
  await ctx.RunUI.open(); await button('추천 파티').onclick(); await ctx.RunUI.open();
  assert.ok(screen().textContent.includes('최근 탐험 · 2/10'));
  assert.ok(screen().textContent.includes('새로고침하거나 닫으면'));
  await button('이 탐험 이어하기').onclick();
  const resumed = calls.requests.find(r => r.url === '/api/runs/run-1/resume');
  assert.equal(resumed.options.headers.Authorization, 'Bearer run-secret');
  assert.ok(!screen().textContent.includes('new-secret'));
});

test('malformed legacy URLs and token values never become resume requests', async () => {
  for (const saved of [{run_id: '//evil.example', token: 'secret'}, {run_id: 'valid', token: 123}, {run_id: 'valid', token: 'x'.repeat(257)}]) {
    const {ctx, stored, calls, screen} = harness();
    stored.set(KEY, JSON.stringify(saved));
    await ctx.RunUI.open(); await ctx.RunUI.resume();
    assert.ok(!screen().textContent.includes('이어하기'));
    assert.deepEqual(calls.requests.map(r => r.url), ['/api/runs/status']);
  }
});

test('opening guidance names the first boss and makes the selected starter party explicit', async () => {
  const {ctx, screen, button} = harness(({url}) => url.endsWith('/status') ? ok({...catalog(), route_guide: {
    bosses: [{index: 4, label: '비의 수호자', preparation: '물 타입을 대비하세요.'}],
  }}) : undefined);
  await ctx.RunUI.open();
  assert.ok(screen().textContent.includes('처음 탐험하는 방법'));
  assert.ok(screen().textContent.includes('5구간 비의 수호자'));
  assert.ok(screen().textContent.includes('추천 파티: 리자몽 · 한카리아스 · 픽시'));
  const charizard = button('리자몽');
  await charizard.onclick();
  assert.equal(charizard.attributes['aria-pressed'], 'true');
  assert.ok(screen().textContent.includes('선택한 동료: 리자몽'));
  await charizard.onclick();
  assert.equal(charizard.attributes['aria-pressed'], 'false');
});

test('the journey shows exactly the offered route and a public boss warning with a current marker', () => {
  const {ctx, screen} = harness();
  const nodes = Array.from({length: 20}, (_, index) => ({index, kind: index === 4 ? 'boss' : 'battle', kind_label: index === 4 ? '보스' : '일반전', label: '공개 구간', status: index < 2 ? 'completed' : index === 2 ? 'current' : 'upcoming'}));
  ctx.RunUI.render(run({node_index: 2, journey: {nodes, next_boss: {index: 4, label: '<비의 보스>', distance: 2,
    description: '비를 사용하는 상대입니다.', preparation: '전기 기술을 준비하세요.', reward: '현재 특전 기준 40골드'}}}));
  const route = descendants(screen()).find(node => node.className === 'rogue-route');
  assert.equal(route.children.length, 20);
  assert.equal(route.children.filter(node => node.attributes['aria-current'] === 'step').length, 1);
  assert.ok(screen().textContent.includes('5구간 · <비의 보스>'));
  assert.ok(screen().textContent.includes('현재 위치에서 2구간 뒤'));
  assert.ok(screen().textContent.includes('전기 기술을 준비하세요.'));
  assert.ok(screen().textContent.includes('현재 특전 기준 40골드'));
  assert.equal(descendants(screen()).find(node => node.textContent === '5구간 · <비의 보스>').innerHTML, undefined);
});

test('branch cards compare public preparation and rewards while only submitting the offered path id', async () => {
  const {ctx, screen, calls, button} = harness();
  const party = run().party.map((p, i) => ({...p, resource: {hp: [0,40,30][i], maxhp: 150, status: i === 1 ? 'brn' : '', item: '', moves: [{pp: [0,3,2][i], maxpp: [24,8,10][i]}]}}));
  ctx.RunUI.render(run({phase: 'path', party, journey: {branch_note: '두 길은 위험도 분류와 기본 보상이 같습니다.'},
    paths: [{id: 'offered-rain', label: '비의 길', kind: 'battle', kind_label: '일반 전투', danger: '일반', theme_label: '비',
      description: '물을 사용하는 상대', preparation: '전기 기술로 대비', reward: '20골드와 보상 선택'}]}));
  for (const text of ['파티 HP 70/450','기술 PP 5/42','기절 1마리','상태이상 1마리','화상','PP가 없는 기술이 1개','전기 기술로 대비','20골드와 보상 선택','위험도 분류와 기본 보상이 같습니다.']) assert.ok(screen().textContent.includes(text), text);
  await button('비의 길 · 이 길로 간다').onclick();
  assert.deepEqual(calls.requests[0].body, {path_id: 'offered-rain', run_revision: 0});
});

test('rest choices respect server availability without disabling the usable recovery choices', () => {
  const {ctx, button} = harness();
  ctx.RunUI.render(run({node: {kind: 'rest', label: '쉼터'}, rest_options: [{id: 'revive', available: false}, {id: 'heal', available: true}]}));
  assert.equal(button('부활').disabled, true);
  assert.equal(button('HP 회복').disabled, false);
});

test('battle preparation navigation waits for the save and resumes the same expedition with a new view', async () => {
  const pending = deferred();
  const battle = revision => run({phase: 'battle', battle: {battle_id: 'battle-1', revision, view: view(revision + 1)}});
  const {ctx, calls, nodes, screen, button} = harness(({url}) => url.endsWith('/battle/action') ? pending.promise : url.endsWith('/resume') ? ok(battle(1)) : undefined);
  ctx.RunUI.render(battle(0));
  const tools = ctx.document.getElementById('rogueBattleTools');
  const leave = () => tools.querySelectorAll('button').find(b => b.textContent.includes('탐험 준비로'));
  const move = nodes.get('moveGrid').children[0].onclick();
  assert.equal(leave().disabled, true);
  leave().onclick(); assert.equal(ctx.EngineUI.session.mode, 'run');
  pending.resolve(ok(battle(1))); await move;
  leave().onclick();
  assert.equal(ctx.EngineUI.session, null); assert.equal(screen().style.display, 'block');
  assert.ok(screen().textContent.includes('최근 탐험 · 1/10'));
  await button('저장한 탐험 이어하기').onclick();
  assert.equal(ctx.EngineUI.session.view.turn, 2);
  assert.equal(ctx.EngineUI.session.mode, 'run');
  assert.equal(calls.requests.filter(r => r.url.endsWith('/battle/action')).length, 1);
});

test('a battle error is shown in its visible toolbar and offers an authenticated checkpoint retry', async () => {
  let offline = true;
  const {ctx, nodes, calls} = harness(({url}) => {
    if (offline) throw new Error('network offline');
    if (url.endsWith('/resume')) return ok(run({phase: 'battle', battle: {battle_id: 'battle-1', revision: 1, view: view(2)}}));
  });
  ctx.RunUI.render(run({phase: 'battle', battle: {battle_id: 'battle-1', revision: 0, view: view()}}));
  await nodes.get('moveGrid').children[0].onclick();
  const notice = ctx.document.getElementById('rogueNotice'), tools = ctx.document.getElementById('rogueBattleTools');
  assert.equal(notice.parentNode, tools); assert.ok(notice.textContent.includes('network offline'));
  const retry = tools.querySelectorAll('button').find(b => b.textContent.includes('저장 상태 다시'));
  assert.ok(retry); assert.equal(retry.disabled, false);
  offline = false; await retry.onclick();
  assert.equal(ctx.EngineUI.session.view.turn, 2);
  const request = calls.requests.at(-1);
  assert.equal(request.options.headers.Authorization, 'Bearer run-secret');
  assert.equal(request.url, '/api/runs/run-1/resume');
  assert.deepEqual(request.body, {});
});

test('strategic expedition selection changes only the new run and preserves its mode in history', async () => {
  const {ctx, calls, stored, screen, button} = harness(({url, body}) => url === '/api/runs' ? ok(run({difficulty: body.difficulty})) : undefined);
  await ctx.RunUI.open();
  assert.equal(button('기본 탐험').attributes['aria-pressed'], 'true');
  assert.ok(screen().textContent.includes('정예와 보스의 전투 판단이 더 까다로워집니다.'));
  await button('전략 탐험').onclick();
  assert.equal(button('기본 탐험').attributes['aria-pressed'], 'false');
  assert.equal(button('전략 탐험').attributes['aria-pressed'], 'true');
  await button('추천 파티로').onclick();
  assert.equal(calls.requests.find(r => r.url === '/api/runs').body.difficulty, 'hard');
  assert.equal(ctx.RunUI.state.difficulty, 'hard');
  assert.ok(screen().textContent.includes('전략 탐험 · 1/20'));
  assert.equal(JSON.parse(stored.get(HISTORY_KEY)).runs[0].difficulty, 'hard');
  await ctx.RunUI.open();
  const historyRow = descendants(screen()).find(n => n.tagName === 'STRONG' && n.textContent.includes('다음 구간 준비'));
  assert.ok(historyRow.textContent.includes('전략 탐험'));
});

test('a directly selected party submits the same selected expedition difficulty', async () => {
  const {ctx, calls, button} = harness(({url, body}) => url === '/api/runs' ? ok(run({difficulty: body.difficulty})) : undefined);
  await ctx.RunUI.open();
  await button('전략 탐험').onclick();
  for (const species of ['리자몽', '한카리아스', '픽시']) await button(species).onclick();
  await button('선택한 파티로 시작 · 3/3').onclick();
  const request = calls.requests.find(r => r.url === '/api/runs');
  assert.equal(request.body.difficulty, 'hard');
  assert.deepEqual(request.body.team.map(p => p.species), ['Charizard', 'Garchomp', 'Clefable']);
});

test('legacy and unsupported history difficulty values display as basic without changing their credentials', async () => {
  const {ctx, stored, screen} = harness();
  stored.set(HISTORY_KEY, JSON.stringify({version: 1, runs: [
    {run_id: 'legacy-run', token: 'legacy-secret', phase: 'node', node_label: '과거 탐험'},
    {run_id: 'invalid-mode', token: 'mode-secret', phase: 'node', node_label: '알 수 없는 설정', difficulty: '<script>hard</script>'},
  ]}));
  await ctx.RunUI.open();
  const rows = descendants(screen()).filter(n => n.tagName === 'STRONG' && n.textContent.includes('다음 구간 준비'));
  assert.equal(rows.length, 2);
  assert.ok(rows.every(row => row.textContent.includes('기본 탐험')));
  assert.ok(!screen().textContent.includes('<script>hard</script>'));
  assert.ok(!screen().textContent.includes('legacy-secret'));
  ctx.RunUI.render(run());
  assert.ok(screen().textContent.includes('기본 탐험 · 1/20'));
});

test('a pending creation locks difficulty and cannot create another expedition', async () => {
  const pending = deferred();
  const {ctx, calls, button} = harness(({url}) => url === '/api/runs' ? pending.promise : undefined);
  await ctx.RunUI.open();
  const normal = button('기본 탐험'), strategic = button('전략 탐험'), start = button('추천 파티로');
  const creating = start.onclick();
  assert.equal(normal.disabled, true); assert.equal(strategic.disabled, true);
  strategic.onclick(); start.onclick();
  assert.equal(normal.attributes['aria-pressed'], 'true');
  assert.equal(strategic.attributes['aria-pressed'], 'false');
  assert.equal(calls.requests.filter(r => r.url === '/api/runs').length, 1);
  assert.equal(calls.requests.find(r => r.url === '/api/runs').body.difficulty, 'normal');
  pending.resolve(ok(run({difficulty: 'normal'}))); await creating;
  assert.equal(ctx.RunUI.state.difficulty, 'normal');
});

test('mutations copy the current run revision across reward and path transitions', async () => {
  const {ctx, calls, button} = harness(({url}) => {
    if (url.endsWith('/reward')) return ok(run({run_revision: 37, phase: 'path', paths: [{id: 'new-path', label: '최신 갈림길'}]}));
    if (url.endsWith('/path')) return ok(run({run_revision: 51, node: {kind: 'rest', label: '쉼터'}}));
  });
  ctx.RunUI.render(run({run_revision: 12, phase: 'reward', rewards: [{id: 'current-offer', label: '현재 보상'}]}));
  await button('현재 보상').onclick();
  await button('최신 갈림길').onclick();
  await button('HP 회복').onclick();
  assert.deepEqual(calls.requests.map(r => r.body.run_revision), [12, 37, 51]);
});

test('different run revisions remain distinct when each new battle starts at battle revision zero', async () => {
  const {ctx, calls, nodes} = harness(({url, body}) => url.endsWith('/battle/action') ? ok(run({phase: 'path', run_revision: body.run_revision + 1})) : undefined);
  for (const [battleId, runRevision] of [['first-battle', 9], ['second-battle', 41]]) {
    ctx.RunUI.render(run({run_revision: runRevision, phase: 'battle', battle: {battle_id: battleId, revision: 0, view: {...view(), battle_id: battleId}}}));
    await nodes.get('moveGrid').children[0].onclick();
  }
  const requests = calls.requests.filter(r => r.url.endsWith('/battle/action'));
  assert.deepEqual(requests.map(r => r.body.revision), [0, 0]);
  assert.deepEqual(requests.map(r => r.body.run_revision), [9, 41]);
});

test('a stale selection reloads the checkpoint and asks for a new choice without replaying the action', async () => {
  const {ctx, calls, screen, button} = harness(({url}) => {
    if (url.endsWith('/reward')) return {ok: false, status: 409, json: async () => ({detail: {code: 'RUN_STALE', message: '최신 탐험 화면을 불러오세요.'}})};
    if (url.endsWith('/resume')) return ok(run({run_revision: 24, phase: 'path', paths: [{id: 'fresh-path', label: '새로 열린 길'}]}));
  });
  ctx.RunUI.render(run({run_revision: 7, phase: 'reward', rewards: [{id: 'stale-reward', label: '이전 화면의 보상'}]}));
  await button('이전 화면의 보상').onclick();
  assert.deepEqual(calls.requests.map(r => r.url), ['/api/runs/run-1/reward', '/api/runs/run-1/resume']);
  assert.equal(calls.requests[0].body.run_revision, 7);
  assert.deepEqual(calls.requests[1].body, {});
  assert.equal(calls.requests[1].options.headers.Authorization, 'Bearer run-secret');
  assert.equal(ctx.RunUI.state.run_revision, 24);
  assert.ok(screen().textContent.includes('최신 상태를 불러왔습니다. 내용을 확인하고 다시 선택하세요.'));
  assert.ok(screen().textContent.includes('새로 열린 길'));
});

test('a run response from before revision metadata sends the legacy zero revision', async () => {
  const {ctx, calls, button} = harness();
  const oldRun = run({node: {kind: 'rest', label: '쉼터'}}); delete oldRun.run_revision;
  ctx.RunUI.render(oldRun);
  await button('HP 회복').onclick();
  assert.equal(calls.requests[0].body.run_revision, 0);
});

test('two tabs that create distinct expeditions from nine active runs preserve all eleven credentials in either response order', async () => {
  for (const reverse of [false, true]) {
    const shared = new Map(), firstResponse = deferred(), secondResponse = deferred();
    const oldRuns = Array.from({length: 9}, (_, i) => ({run_id: 'existing-' + i, token: 'secret-' + i, phase: 'node'}));
    shared.set(HISTORY_KEY, JSON.stringify({version: 1, runs: oldRuns}));
    const first = harness(({url}) => url === '/api/runs' ? firstResponse.promise : undefined, shared);
    const second = harness(({url}) => url === '/api/runs' ? secondResponse.promise : undefined, shared);
    await first.ctx.RunUI.open(); await second.ctx.RunUI.open();
    await second.button('전략 탐험').onclick();
    const creatingFirst = first.button('추천 파티로').onclick(), creatingSecond = second.button('추천 파티로').onclick();
    const responses = [
      () => { firstResponse.resolve(ok(run({run_id: 'created-first', token: 'first-credential'}))); return creatingFirst; },
      () => { secondResponse.resolve(ok(run({run_id: 'created-second', token: 'second-credential'}))); return creatingSecond; },
    ];
    for (const finish of reverse ? responses.reverse() : responses) await finish();
    // Simulate an older tab overwriting the disposable array after both commits.
    shared.set(HISTORY_KEY, JSON.stringify({version: 1, runs: oldRuns.slice(0, 3)}));
    const reloaded = harness(undefined, shared); await reloaded.ctx.RunUI.open();
    assert.ok(reloaded.screen().textContent.includes('최근 탐험 · 11/10'));
    assert.ok(reloaded.screen().textContent.includes('여러 탭에서 만든 탐험을 모두 보관했습니다.'));
    const records = [...shared.keys()].filter(key => key.startsWith(RECORD_PREFIX)).map(key => JSON.parse(shared.get(key)).run);
    assert.equal(records.length, 11);
    assert.deepEqual(records.map(p => p.run_id).sort(), [...oldRuns.map(p => p.run_id), 'created-first', 'created-second'].sort());
    assert.equal(reloaded.button('추천 파티로').disabled, true);
    await reloaded.button('추천 파티로').onclick();
    assert.equal(reloaded.calls.requests.filter(r => r.url === '/api/runs').length, 0);
    assert.ok(!reloaded.screen().textContent.includes('first-credential'));
    first.listeners.get('storage')({key: RECORD_PREFIX + 'created-second'});
    // The first tab is displaying its created run, so a storage event must not
    // navigate it out of that active expedition.
    assert.equal(first.ctx.RunUI.state.run_id, 'created-first');
  }
});

test('single and array legacy values migrate into independent records that survive losing the index', async () => {
  for (const useArray of [false, true]) {
    const shared = new Map(), credential = {run_id: 'legacy-run', token: 'legacy-credential', phase: 'node'};
    shared.set(useArray ? HISTORY_KEY : KEY, JSON.stringify(useArray ? {version: 1, runs: [credential]} : credential));
    const old = harness(undefined, shared); await old.ctx.RunUI.open();
    const independent = JSON.parse(shared.get(RECORD_PREFIX + 'legacy-run'));
    assert.equal(independent.version, 2); assert.equal(independent.run.token, 'legacy-credential');
    shared.delete(HISTORY_KEY); shared.delete(KEY);
    const reloaded = harness(undefined, shared); await reloaded.ctx.RunUI.open();
    assert.ok(reloaded.screen().textContent.includes('최근 탐험 · 1/10'));
    await reloaded.button('저장한 탐험 이어하기').onclick();
    const request = reloaded.calls.requests.find(r => r.url.endsWith('/resume'));
    assert.equal(request.options.headers.Authorization, 'Bearer legacy-credential');
  }
});

test('an explicit removal survives an old array and a delayed response from another tab', async () => {
  const shared = new Map(), first = harness(undefined, shared), second = harness(undefined, shared);
  first.ctx.RunUI.render(run({run_id: 'kept-run', token: 'kept-credential', node: {kind: 'battle', label: '남길 탐험'}}));
  first.ctx.RunUI.render(run({run_id: 'removed-run', token: 'removed-credential', node: {kind: 'battle', label: '지울 탐험'}}));
  await first.ctx.RunUI.open(); await second.ctx.RunUI.open();
  const staleArray = shared.get(HISTORY_KEY);
  await first.button('이 기기 목록에서 제거').onclick();
  await first.button('목록에서 제거 확인').onclick();
  assert.equal(shared.get(REMOVED_PREFIX + 'removed-run'), 'removed');
  shared.set(HISTORY_KEY, staleArray);
  second.ctx.RunUI.render(run({run_id: 'removed-run', token: 'removed-credential', run_revision: 999, node: {kind: 'battle', label: '지울 탐험'}}));
  const reloaded = harness(undefined, shared); await reloaded.ctx.RunUI.open();
  assert.ok(reloaded.screen().textContent.includes('최근 탐험 · 1/10'));
  assert.ok(reloaded.screen().textContent.includes('남길 탐험'));
  assert.ok(!reloaded.screen().textContent.includes('지울 탐험'));
  assert.equal(shared.has(RECORD_PREFIX + 'removed-run'), false);
  assert.equal(first.calls.requests.filter(r => r.url.includes('/remove') || r.options.method === 'DELETE').length, 0);
});

test('an older server revision from another tab cannot regress the persisted run summary', async () => {
  const shared = new Map(), latest = harness(undefined, shared), stale = harness(undefined, shared);
  latest.ctx.RunUI.render(run({run_revision: 40, phase: 'reward', node_index: 4, completed_nodes: 5, node: {kind: 'boss', label: '최신 보스'}}));
  stale.ctx.RunUI.render(run({run_revision: 3, node: {kind: 'battle', label: '오래된 화면'}}));
  const record = JSON.parse(shared.get(RECORD_PREFIX + 'run-1')).run;
  assert.equal(record.run_revision, 40); assert.equal(record.phase, 'reward');
  const reloaded = harness(undefined, shared); await reloaded.ctx.RunUI.open();
  assert.ok(reloaded.screen().textContent.includes('최신 보스 · 보상 선택'));
  assert.ok(!reloaded.screen().textContent.includes('오래된 화면'));
});

test('quota recovery discards only the migrated index and retains all independent credentials', async () => {
  const shared = new Map(), h = harness(undefined, shared);
  shared.set(KEY, JSON.stringify({run_id: 'old-run', token: 'old-credential'}));
  await h.ctx.RunUI.open();
  const original = h.ctx.localStorage.setItem;
  h.ctx.localStorage.setItem = (key, value) => {
    if (key === HISTORY_KEY || (key === RECORD_PREFIX + 'new-run' && shared.has(HISTORY_KEY))) {
      const error = new Error('quota full'); error.name = 'QuotaExceededError'; throw error;
    }
    original(key, value);
  };
  h.ctx.RunUI.render(run({run_id: 'new-run', token: 'new-credential'}));
  assert.equal(shared.has(HISTORY_KEY), false);
  assert.equal(JSON.parse(shared.get(RECORD_PREFIX + 'old-run')).run.token, 'old-credential');
  assert.equal(JSON.parse(shared.get(RECORD_PREFIX + 'new-run')).run.token, 'new-credential');
  const reloaded = harness(undefined, shared); await reloaded.ctx.RunUI.open();
  assert.ok(reloaded.screen().textContent.includes('최근 탐험 · 2/10'));
  assert.ok(!h.screen().textContent.includes('이 브라우저에 이어하기 정보를 저장할 수 없습니다.'));
});

test('quota failure halfway through migration preserves the array and every credential in memory', async () => {
  const shared = new Map(), source = JSON.stringify({version: 1, runs: [
    {run_id: 'first-old', token: 'first-credential'}, {run_id: 'second-old', token: 'second-credential'},
  ]});
  shared.set(HISTORY_KEY, source);
  const h = harness(undefined, shared), original = h.ctx.localStorage.setItem;
  h.ctx.localStorage.setItem = (key, value) => {
    if (key === RECORD_PREFIX + 'second-old') { const error = new Error('quota full'); error.name = 'QuotaExceededError'; throw error; }
    original(key, value);
  };
  await h.ctx.RunUI.open();
  assert.equal(shared.get(HISTORY_KEY), source);
  assert.ok(h.screen().textContent.includes('최근 탐험 · 2/10'));
  assert.ok(h.screen().textContent.includes('이 브라우저에 이어하기 정보를 저장할 수 없습니다.'));
  h.ctx.RunUI.render(run({run_id: 'memory-only', token: 'memory-credential'}));
  await h.ctx.RunUI.open();
  assert.ok(h.screen().textContent.includes('최근 탐험 · 3/10'));
  const recovered = harness(undefined, shared); await recovered.ctx.RunUI.open();
  assert.ok(recovered.screen().textContent.includes('최근 탐험 · 2/10'));
  assert.equal(JSON.parse(shared.get(RECORD_PREFIX + 'second-old')).run.token, 'second-credential');
});

test('a storage event refreshes a visible preparation list without submitting any operation', async () => {
  const shared = new Map(), first = harness(undefined, shared), second = harness(undefined, shared);
  await first.ctx.RunUI.open(); await second.ctx.RunUI.open();
  second.ctx.RunUI.render(run({run_id: 'other-tab', token: 'other-credential'}));
  first.listeners.get('storage')({key: RECORD_PREFIX + 'other-tab'});
  assert.ok(first.screen().textContent.includes('최근 탐험 · 1/10'));
  assert.equal(first.calls.requests.length, 1);
  assert.ok(!first.screen().textContent.includes('other-credential'));
});

test('background storage updates preserve a composed party and difficulty, including while creation is pending', async () => {
  const shared = new Map(), pending = deferred();
  const first = harness(({url}) => url === '/api/runs' ? pending.promise : undefined, shared), second = harness(undefined, shared);
  await first.ctx.RunUI.open(); await second.ctx.RunUI.open();
  await first.button('전략 탐험').onclick();
  await first.button('리자몽').onclick(); await first.button('한카리아스').onclick();
  let scrolls = 0; first.ctx.scrollTo = () => { scrolls++; };
  second.ctx.RunUI.render(run({run_id: 'other-tab', token: 'other-credential'}));
  first.listeners.get('storage')({key: RECORD_PREFIX + 'other-tab'});
  assert.ok(first.screen().textContent.includes('선택한 동료: 리자몽 · 한카리아스'));
  assert.equal(first.button('전략 탐험').attributes['aria-pressed'], 'true');
  assert.equal(scrolls, 0);
  await first.button('픽시').onclick();
  const creating = first.button('선택한 파티로 시작 · 3/3').onclick();
  second.ctx.RunUI.render(run({run_id: 'other-tab', token: 'other-credential', run_revision: 9}));
  first.listeners.get('storage')({key: RECORD_PREFIX + 'other-tab'});
  assert.equal(first.button('선택한 파티로 시작 · 3/3').disabled, true);
  const request = first.calls.requests.find(r => r.url === '/api/runs');
  assert.deepEqual(request.body.team.map(p => p.species), ['Charizard', 'Garchomp', 'Clefable']);
  assert.equal(request.body.difficulty, 'hard');
  pending.resolve(ok(run({run_id: 'composed-run', token: 'composed-credential', difficulty: 'hard'}))); await creating;
  assert.equal(first.ctx.RunUI.state.run_id, 'composed-run');
});

test('a lead change sends only an order and run revision, then displays the server-owned resource binding', async () => {
  const pending = deferred(), prepared = run({run_revision: 32});
  prepared.party.forEach((p, i) => { p.resource.hp = 10 + i; p.resource.moves[0].pp = 3 + i; });
  const original = JSON.stringify(prepared.party);
  const {ctx, calls, button, screen} = harness(({url}) => url.endsWith('/party/order') ? pending.promise : undefined);
  await ctx.RunUI.open(); ctx.RunUI.render(prepared);
  const chooseLead = button('한카리아스 · 선두로');
  const action = chooseLead.onclick();
  assert.equal(ctx.RunUI.state.party[0].species, 'Charizard');
  assert.equal(JSON.stringify(prepared.party), original);
  const request = calls.requests.find(r => r.url.endsWith('/party/order'));
  assert.deepEqual(request.body, {order: [1, 0, 2], run_revision: 32});
  const ordered = [prepared.party[1], prepared.party[0], prepared.party[2]];
  pending.resolve(ok(run({run_revision: 33, party: ordered}))); await action;
  assert.equal(ctx.RunUI.state.party[0].species, 'Garchomp');
  const cards = descendants(screen()).filter(n => n.className === 'rogue-card');
  assert.ok(cards[0].textContent.includes('한카리아스선두'));
  assert.ok(cards[0].textContent.includes('HP 11/150'));
  assert.ok(cards[0].textContent.includes('PP 4/?'));
  await chooseLead.onclick();
  assert.equal(calls.requests.filter(r => r.url.endsWith('/party/order')).length, 1);
});

test('fainted members and non-battle preparation screens do not permit lead changes', async () => {
  const {ctx, calls, button, buttons} = harness();
  await ctx.RunUI.open();
  const party = run().party; party[1].resource.hp = 0;
  ctx.RunUI.render(run({party}));
  const fainted = button('한카리아스 · 선두로');
  assert.equal(fainted.disabled, true); await fainted.onclick();
  assert.equal(calls.requests.filter(r => r.url.endsWith('/party/order')).length, 0);
  for (const extra of [{node:{kind:'rest'}}, {node:{kind:'shop'}}, {phase:'reward'}, {phase:'path'}, {phase:'recruit'}, {phase:'complete'}]) {
    ctx.RunUI.render(run({party, ...extra}));
    assert.equal(buttons().filter(b => b.textContent.includes(' · 선두로')).length, 0);
  }
  ctx.RunUI.render(run({party})); const stale = button('픽시 · 선두로');
  ctx.RunUI.render(run({party, phase:'battle', battle:{battle_id:'battle-1',revision:0,view:view()}}));
  await stale.onclick();
  assert.equal(calls.requests.filter(r => r.url.endsWith('/party/order')).length, 0);
});

test('party cards show each move name, type and exact PP while consumed items remain absent', async () => {
  const {ctx, screen} = harness(); await ctx.RunUI.open();
  const party = run().party;
  party[0].types = ['fire','flying']; party[0].item = 'Leftovers'; party[0].resource.item = '';
  party[0].resource.moves = [
    {id:'flamethrower',name_ko:'화염방사',name_en:'Flamethrower',type:'fire',category:'special',power:90,pp:0,maxpp:24},
    {id:'airslash',name_en:'Air Slash',type:'flying',category:'special',power:75,pp:5,maxpp:24},
    {id:'roost',type:'flying',category:'status',power:0,pp:8,maxpp:16},
  ];
  ctx.RunUI.render(run({party}));
  for (const text of ['타입: 불꽃 · 비행','화염방사','PP 0/24 · PP 없음','불꽃 · 특수 · 위력 90','Air Slash','PP 5/24','roost','PP 8/16','변화기','도구 없음']) assert.ok(screen().textContent.includes(text), text);
  assert.ok(!screen().textContent.includes('Leftovers'));
});

test('rest cards use the server bonus label and display actual capped resource changes without mutating them', async () => {
  const preview = recoveryPreviewFixture('heal'), before = JSON.stringify(preview);
  const {ctx, calls, screen, button} = harness(); await ctx.RunUI.open();
  ctx.RunUI.render(run({run_revision: 22, party:partyForPreview(preview), node:{kind:'rest',label:'쉼터'}, rest_options:[{
    id:'heal',label:'살아 있는 파티 HP 70% 회복',available:true,preview,
  }]}));
  const card = button('살아 있는 파티 HP 70% 회복').parentNode;
  for (const text of ['파티 HP 120/300 → 190/300 (+70)','기술 PP 16/30 → 16/30 (+0)','부활 0마리 · 상태 치료 0마리','동료별 변화 보기','HP 20/100 → 90/100']) assert.ok(card.textContent.includes(text), text);
  assert.equal(JSON.stringify(preview), before);
  assert.ok(!screen().textContent.includes('살아 있는 파티 HP 50% 회복'));
  await button('살아 있는 파티 HP 70% 회복').onclick();
  const request = calls.requests.find(r => r.url.endsWith('/rest'));
  assert.deepEqual(request.body, {option:'heal',run_revision:22});
});

test('a zero-change rest can advance while an unavailable revival remains disabled', async () => {
  const preview = recoveryPreviewFixture('none');
  const {ctx, calls, button} = harness(); await ctx.RunUI.open();
  ctx.RunUI.render(run({party:partyForPreview(preview), node:{kind:'rest',label:'쉼터'}, rest_options:[
    {id:'heal',label:'HP 회복 · 현재 변화 없음',available:true,preview},
    {id:'revive',label:'기절한 동료 부활',available:false,preview},
  ]}));
  const heal = button('HP 회복 · 현재 변화 없음'), revive = button('기절한 동료 부활');
  assert.equal(heal.disabled, false); assert.equal(revive.disabled, true);
  assert.ok(heal.parentNode.textContent.includes('이 선택으로 쉼터를 지나 다음 길로 진행할 수 있습니다.'));
  assert.ok(!revive.parentNode.textContent.includes('쉼터를 지나 다음 길로 진행'));
  await heal.onclick();
  assert.equal(calls.requests.find(r => r.url.endsWith('/rest')).body.option, 'heal');
});

test('shop recovery cards show named PP changes and retain price and purchased guards', async () => {
  const preview = recoveryPreviewFixture('pp');
  const {ctx, calls, button} = harness(); await ctx.RunUI.open();
  const party = partyForPreview(preview);
  ctx.RunUI.render(run({run_revision:19,party,money:20,node:{kind:'shop',label:'상점'},shop:[
    {id:'expensive',label:'HP 회복',price:25,preview:recoveryPreviewFixture('heal')},
    {id:'offered-pp',label:'모든 기술 PP 14 회복',price:10,preview},
    {id:'bought',label:'이미 산 회복',price:5,bought:true,preview},
  ]}));
  assert.equal(button('HP 회복 · 25골드').disabled, true);
  assert.equal(button('이미 산 회복').disabled, true);
  const choice = button('모든 기술 PP 14 회복');
  assert.equal(choice.disabled, false);
  assert.ok(choice.parentNode.textContent.includes('기술 PP 16/30 → 30/30 (+14)'));
  assert.ok(choice.parentNode.textContent.includes('화염방사 · PP 1/10 → 10/10'));
  await choice.onclick();
  assert.deepEqual(calls.requests.find(r => r.url.endsWith('/shop')).body, {offer_id:'offered-pp',run_revision:19});
});

test('a stale lead order reloads the new party and does not replay old slot indices', async () => {
  const prepared = run({run_revision: 8}), ordered = [prepared.party[2], prepared.party[0], prepared.party[1]];
  const {ctx, calls, button, screen} = harness(({url}) => {
    if (url.endsWith('/party/order')) return {ok:false,status:409,json:async()=>({detail:{code:'RUN_STALE',message:'다른 탭에서 파티가 바뀌었습니다.'}})};
    if (url.endsWith('/resume')) return ok(run({run_revision: 12, party: ordered}));
  });
  await ctx.RunUI.open(); ctx.RunUI.render(prepared);
  await button('한카리아스 · 선두로').onclick();
  assert.equal(ctx.RunUI.state.party[0].species, 'Clefable');
  assert.equal(calls.requests.filter(r => r.url.endsWith('/party/order')).length, 1);
  assert.ok(screen().textContent.includes('내용을 확인하고 다시 선택하세요.'));
});

test('revival and status treatment previews expose their actual affected companion counts', async () => {
  const {ctx, button} = harness(); await ctx.RunUI.open();
  for (const [kind, label, count, detail] of [
    ['revive','기절한 동료 1마리 HP 50% 회복','부활 1마리 · 상태 치료 0마리','HP 0/100 → 50/100'],
    ['cure','주요 상태이상 치료','부활 0마리 · 상태 치료 1마리','상태 화상 → 정상'],
  ]) {
    const preview = recoveryPreviewFixture(kind);
    ctx.RunUI.render(run({party:partyForPreview(preview),node:{kind:'rest'},rest_options:[{id:kind,label,available:true,preview}]}));
    const card = button(label).parentNode;
    assert.ok(card.textContent.includes(count)); assert.ok(card.textContent.includes(detail));
  }
});

test('a form uses canonical public types and never guesses from its base species sprite id', async () => {
  const {ctx, screen} = harness(({url}) => url.endsWith('/status') ? ok({...catalog(),species_ids:{...catalog().species_ids,rotomwash:479}}) : undefined);
  ctx.POKE_DB.push({id:479,name:'로토무',types:['electric','ghost']});
  await ctx.RunUI.open();
  const party = run().party; party[0].species = 'Rotom-Wash';
  ctx.RunUI.render(run({party}));
  let card = descendants(screen()).find(n => n.className === 'rogue-card');
  assert.ok(!card.textContent.includes('전기 · 고스트'));
  party[0].types = ['electric','water'];
  ctx.RunUI.render(run({party}));
  card = descendants(screen()).find(n => n.className === 'rogue-card');
  assert.ok(card.textContent.includes('타입: 전기 · 물'));
});

test('creation credentials and a fixed payload are durable before the first POST and removed only after the run is durable', async () => {
  const h = harness(({url, body, options}) => {
    if (url !== '/api/runs') return;
    assert.match(body.creation_id, /^[0-9a-f]{64}$/); assert.match(body.creation_secret, /^[0-9a-f]{64}$/);
    assert.notEqual(body.creation_id, body.creation_secret);
    const pending = JSON.parse(h.stored.get(PENDING_PREFIX + body.creation_id));
    assert.deepEqual(pending.payload, {team: catalog().starter_team, difficulty: 'normal'});
    assert.equal(pending.creation_secret, body.creation_secret);
    assert.equal(options.headers.Authorization, undefined);
    assert.ok(!url.includes(body.creation_id)); assert.ok(!h.screen().textContent.includes(body.creation_secret));
    return ok(run());
  });
  const remove = h.ctx.localStorage.removeItem;
  h.ctx.localStorage.removeItem = key => {
    if (key.startsWith(PENDING_PREFIX)) assert.equal(JSON.parse(h.stored.get(RECORD_PREFIX + 'run-1')).run.token, 'run-secret');
    remove(key);
  };
  await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 0);
  assert.equal(h.ctx.RunUI.state.run_id, 'run-1');
});

test('a lost creation response retries the same credentials both from the start button and after a reload', async () => {
  const shared = new Map();
  const first = harness(({url}) => { if (url === '/api/runs') throw new Error('response lost after commit'); }, shared);
  await first.ctx.RunUI.open(); await first.button('추천 파티로').onclick();
  assert.ok(first.screen().textContent.includes('시작을 확인할 탐험'));
  const original = first.calls.requests.find(r => r.url === '/api/runs').body;
  await first.button('추천 파티로').onclick();
  assert.deepEqual(first.calls.requests.filter(r => r.url === '/api/runs').map(r => r.body), [original, original]);
  const reloaded = harness(({url}) => url === '/api/runs' ? ok(run({run_revision: 12, phase: 'reward', rewards: [{id: 'now', label: '현재 보상'}]})) : undefined, shared);
  delete reloaded.ctx.crypto;
  await reloaded.ctx.RunUI.open();
  assert.equal(reloaded.calls.requests.filter(r => r.url === '/api/runs').length, 0);
  await reloaded.button('이 탐험 시작 확인').onclick();
  assert.deepEqual(reloaded.calls.requests.find(r => r.url === '/api/runs').body, original);
  assert.equal(reloaded.ctx.RunUI.state.run_revision, 12); assert.ok(reloaded.screen().textContent.includes('현재 보상'));
  assert.equal([...shared.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 0);
});

test('reload recovery uses the stored seed and party even when the catalog and current difficulty have changed', async () => {
  const h = harness(({url}) => url.endsWith('/status') ? ok({...catalog(), starter_team: catalog().starter_pool.slice(1)}) : undefined);
  const pending = creationFixture(); h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending));
  await h.ctx.RunUI.open();
  assert.equal(h.button('기본 탐험').attributes['aria-pressed'], 'true');
  await h.button('이 탐험 시작 확인').onclick();
  const body = h.calls.requests.find(r => r.url === '/api/runs').body;
  assert.deepEqual(body, {...pending.payload, creation_id: pending.creation_id, creation_secret: pending.creation_secret});
  assert.ok(!h.screen().textContent.includes(pending.creation_secret));
});

test('a different intended party or difficulty gets independent credentials without discarding the unresolved request', async () => {
  const h = harness(({url}) => { if (url === '/api/runs') throw new Error('offline'); });
  await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
  await h.button('전략 탐험').onclick(); await h.button('추천 파티로').onclick();
  const requests = h.calls.requests.filter(r => r.url === '/api/runs');
  assert.equal(requests.length, 2); assert.notEqual(requests[0].body.creation_id, requests[1].body.creation_id);
  assert.deepEqual(requests.map(r => r.body.difficulty), ['normal', 'hard']);
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 2);
  assert.equal(h.buttons().filter(b => b.textContent === '이 탐험 시작 확인').length, 2);
});

test('two independent tabs reuse one pending request for the same payload and do not recreate its key after success', async () => {
  const shared = new Map(), one = deferred(), two = deferred();
  const first = harness(({url}) => url === '/api/runs' ? one.promise : undefined, shared);
  const second = harness(({url}) => url === '/api/runs' ? two.promise : undefined, shared);
  await first.ctx.RunUI.open(); await second.ctx.RunUI.open();
  const creatingOne = first.button('추천 파티로').onclick(), creatingTwo = second.button('추천 파티로').onclick();
  const firstBody = first.calls.requests.find(r => r.url === '/api/runs').body;
  assert.deepEqual(second.calls.requests.find(r => r.url === '/api/runs').body, firstBody);
  assert.equal([...shared.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 1);
  two.resolve(ok(run({run_revision: 7}))); await creatingTwo;
  assert.equal(shared.has(PENDING_PREFIX + firstBody.creation_id), false);
  one.resolve(ok(run({run_revision: 0}))); await creatingOne;
  assert.equal(shared.has(PENDING_PREFIX + firstBody.creation_id), false);
  assert.equal(JSON.parse(shared.get(RECORD_PREFIX + 'run-1')).run.run_revision, 7);
});

test('pending recovery is available at the ten-run creation limit and retains the recovered eleventh credential', async () => {
  const h = harness(), pending = creationFixture();
  h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending));
  h.stored.set(HISTORY_KEY, JSON.stringify({version: 1, runs: Array.from({length: 10}, (_, i) => ({run_id: 'active-' + i, token: 'secret-' + i, phase: 'battle'}))}));
  await h.ctx.RunUI.open();
  assert.equal(h.button('추천 파티로').disabled, true); assert.equal(h.button('이 탐험 시작 확인').disabled, false);
  await h.button('이 탐험 시작 확인').onclick();
  assert.equal(h.calls.requests.filter(r => r.url === '/api/runs').length, 1);
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(RECORD_PREFIX)).length, 11);
  await h.ctx.RunUI.open(); assert.ok(h.screen().textContent.includes('최근 탐험 · 11/10'));
});

test('a failed canonical run write keeps the durable pending request for a fresh browser to recover', async () => {
  const shared = new Map(), h = harness(undefined, shared), set = h.ctx.localStorage.setItem;
  h.ctx.localStorage.setItem = (key, value) => {
    if (key.startsWith(RECORD_PREFIX)) { const error = new Error('quota'); error.name = 'QuotaExceededError'; throw error; }
    set(key, value);
  };
  await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
  const original = h.calls.requests.find(r => r.url === '/api/runs').body;
  assert.ok(shared.has(PENDING_PREFIX + original.creation_id)); assert.equal(shared.has(RECORD_PREFIX + 'run-1'), false);
  assert.equal(h.ctx.RunUI.state.run_id, 'run-1'); assert.ok(h.screen().textContent.includes('현재 탭에서만 유지'));
  const recovered = harness(undefined, shared); await recovered.ctx.RunUI.open(); await recovered.button('이 탐험 시작 확인').onclick();
  assert.deepEqual(recovered.calls.requests.find(r => r.url === '/api/runs').body, original);
  assert.equal(shared.has(PENDING_PREFIX + original.creation_id), false);
  assert.equal(JSON.parse(shared.get(RECORD_PREFIX + 'run-1')).run.token, 'run-secret');
});

test('an unavailable pending store supports same-tab retries with an explicit reload recovery warning', async () => {
  const h = harness(({url}) => { if (url === '/api/runs') throw new Error('offline'); });
  h.ctx.localStorage.getItem = () => { throw new Error('disabled'); };
  h.ctx.localStorage.setItem = () => { throw new Error('disabled'); };
  await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
  assert.ok(h.screen().textContent.includes('새로고침하거나 닫으면 이 탐험을 복구할 수 없습니다.'));
  const body = h.calls.requests.find(r => r.url === '/api/runs').body;
  await h.button('이 탐험 시작 확인').onclick();
  assert.deepEqual(h.calls.requests.filter(r => r.url === '/api/runs').map(r => r.body), [body, body]);
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 0);
});

test('missing secure entropy prevents a new creation without falling back to command IDs or weak randomness', async () => {
  const h = harness(); delete h.ctx.crypto.getRandomValues;
  await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
  assert.equal(h.calls.requests.filter(r => r.url === '/api/runs').length, 0);
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 0);
  assert.ok(h.screen().textContent.includes('안전한 탐험 시작 정보를 만들 수 없습니다.'));
});

test('creation conflicts, timeouts and malformed successful bodies retain the exact pending request', async () => {
  for (const response of [
    () => ({ok: false, status: 409, json: async () => ({detail: {code: 'CREATION_CONFLICT', message: '시작 요청이 일치하지 않습니다.'}})}),
    () => { const error = new Error('aborted'); error.name = 'AbortError'; throw error; },
    () => ok({}),
  ]) {
    const h = harness(({url}) => url === '/api/runs' ? response() : undefined);
    await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
    const original = h.calls.requests.find(r => r.url === '/api/runs').body;
    await h.button('이 탐험 시작 확인').onclick();
    assert.deepEqual(h.calls.requests.filter(r => r.url === '/api/runs').map(r => r.body), [original, original]);
    assert.ok(h.stored.has(PENDING_PREFIX + original.creation_id)); assert.equal(h.ctx.RunUI.state, null);
  }
});

test('pending storage changes update only their panel while preserving the selected party and difficulty', async () => {
  const h = harness(); await h.ctx.RunUI.open(); await h.button('전략 탐험').onclick(); await h.button('리자몽').onclick();
  let scrolls = 0; h.ctx.scrollTo = () => { scrolls++; };
  const pending = creationFixture(); h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending));
  h.listeners.get('storage')({key: PENDING_PREFIX + pending.creation_id});
  assert.ok(h.screen().textContent.includes('선택한 동료: 리자몽'));
  assert.equal(h.button('전략 탐험').attributes['aria-pressed'], 'true'); assert.equal(scrolls, 0);
  assert.equal(h.button('이 탐험 시작 확인').disabled, false); assert.equal(h.calls.requests.length, 1);
  h.stored.delete(PENDING_PREFIX + pending.creation_id); h.listeners.get('storage')({key: PENDING_PREFIX + pending.creation_id});
  assert.equal(h.buttons().filter(b => b.textContent === '이 탐험 시작 확인').length, 0);
  assert.ok(h.screen().textContent.includes('선택한 동료: 리자몽'));
});

test('late creation success persists access without reopening a screen after navigation', async () => {
  const response = deferred(), h = harness(({url}) => url === '/api/runs' ? response.promise : undefined);
  await h.ctx.RunUI.open(); const creating = h.button('추천 파티로').onclick();
  h.ctx.goContentHub(); response.resolve(ok(run())); await creating;
  assert.equal(h.screen().style.display, 'none'); assert.equal(h.ctx.RunUI.state, null);
  assert.equal(JSON.parse(h.stored.get(RECORD_PREFIX + 'run-1')).run.token, 'run-secret');
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 0);
});

test('a creation response cannot resurrect a run removed by another tab and keeps the unresolved source', async () => {
  const shared = new Map(), response = deferred(), h = harness(({url}) => url === '/api/runs' ? response.promise : undefined, shared);
  await h.ctx.RunUI.open(); const creating = h.button('추천 파티로').onclick();
  const body = h.calls.requests.find(r => r.url === '/api/runs').body;
  shared.set(REMOVED_PREFIX + 'run-1', 'removed'); response.resolve(ok(run())); await creating;
  assert.equal(shared.has(RECORD_PREFIX + 'run-1'), false); assert.equal(shared.has(PENDING_PREFIX + body.creation_id), true);
  assert.equal(h.ctx.RunUI.state, null); assert.ok(h.screen().textContent.includes('목록에서 제거한 탐험입니다.'));
  assert.ok(!h.screen().textContent.includes('run-secret'));
});

test('malformed pending credentials and mismatched key identities never become creation requests', async () => {
  const h = harness(), valid = creationFixture();
  for (const [index, value] of [
    {...valid, creation_secret: 'short'}, {...valid, creation_id: [valid.creation_id]},
    {...valid, payload: {...valid.payload, team: []}}, {...valid, payload: {...valid.payload, seed: true}},
    {...valid, payload: {...valid.payload, extra: true}}, valid,
  ].entries()) h.stored.set(PENDING_PREFIX + index, JSON.stringify(value));
  await h.ctx.RunUI.open();
  assert.equal(h.buttons().filter(b => b.textContent === '이 탐험 시작 확인').length, 0);
  assert.equal(h.calls.requests.filter(r => r.url === '/api/runs').length, 0);
  assert.ok(!h.screen().textContent.includes(valid.creation_secret));
});

test('pending quota failure preserves every old credential and the in-memory request for an explicit retry', async () => {
  const h = harness(({url}) => { if (url === '/api/runs') throw new Error('offline'); });
  h.ctx.RunUI.render(run({run_id: 'prior-run', token: 'prior-token'})); await h.ctx.RunUI.open();
  const set = h.ctx.localStorage.setItem;
  h.ctx.localStorage.setItem = (key, value) => {
    if (key.startsWith(PENDING_PREFIX)) { const error = new Error('quota'); error.name = 'QuotaExceededError'; throw error; }
    set(key, value);
  };
  await h.button('추천 파티로').onclick();
  assert.equal(JSON.parse(h.stored.get(RECORD_PREFIX + 'prior-run')).run.token, 'prior-token');
  assert.ok(h.screen().textContent.includes('새로고침하거나 닫으면 이 탐험을 복구할 수 없습니다.'));
  const original = h.calls.requests.find(r => r.url === '/api/runs').body;
  await h.button('이 탐험 시작 확인').onclick();
  assert.deepEqual(h.calls.requests.filter(r => r.url === '/api/runs').map(r => r.body), [original, original]);
});

test('failure to remove an already fulfilled pending key leaves a harmless same-run retry after reload', async () => {
  const shared = new Map(), h = harness(undefined, shared), remove = h.ctx.localStorage.removeItem;
  h.ctx.localStorage.removeItem = key => { if (key.startsWith(PENDING_PREFIX)) throw new Error('delete disabled'); remove(key); };
  await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
  const original = h.calls.requests.find(r => r.url === '/api/runs').body;
  assert.ok(shared.has(PENDING_PREFIX + original.creation_id)); assert.ok(shared.has(RECORD_PREFIX + 'run-1'));
  const reloaded = harness(undefined, shared); await reloaded.ctx.RunUI.open(); await reloaded.button('이 탐험 시작 확인').onclick();
  assert.deepEqual(reloaded.calls.requests.find(r => r.url === '/api/runs').body, original);
  assert.equal([...shared.keys()].filter(key => key.startsWith(RECORD_PREFIX)).length, 1);
  assert.equal(shared.has(PENDING_PREFIX + original.creation_id), false);
});

test('an old pending recovery button cannot submit after navigation or after a storage refresh removes its row', async () => {
  const h = harness(), pending = creationFixture(); h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending));
  await h.ctx.RunUI.open(); const stale = h.button('이 탐험 시작 확인');
  h.stored.delete(PENDING_PREFIX + pending.creation_id); h.listeners.get('storage')({key: PENDING_PREFIX + pending.creation_id});
  await stale.onclick(); assert.equal(h.calls.requests.filter(r => r.url === '/api/runs').length, 0);
  h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending));
  h.listeners.get('storage')({key: PENDING_PREFIX + pending.creation_id});
  const hidden = h.button('이 탐험 시작 확인'); h.ctx.goContentHub(); await hidden.onclick();
  assert.equal(h.calls.requests.filter(r => r.url === '/api/runs').length, 0);
});

test('reloaded pending requests preserve supported string seeds exactly through the server length limit', async () => {
  for (const seed of ['', '여정-123', 's'.repeat(128), '🌿'.repeat(128)]) {
    const h = harness(), pending = creationFixture({payload: {...creationFixture().payload, seed}});
    h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending));
    await h.ctx.RunUI.open(); await h.button('이 탐험 시작 확인').onclick();
    assert.deepEqual(h.calls.requests.find(r => r.url === '/api/runs').body, {...pending.payload,
      creation_id: pending.creation_id, creation_secret: pending.creation_secret});
    assert.equal(h.stored.has(PENDING_PREFIX + pending.creation_id), false);
  }
  const h = harness(), pending = creationFixture({payload: {...creationFixture().payload, seed: 's'.repeat(129)}});
  h.stored.set(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending)); await h.ctx.RunUI.open();
  assert.equal(h.buttons().filter(b => b.textContent === '이 탐험 시작 확인').length, 0);
});

test('invalid successful run states keep pending access until a structurally valid retry arrives', async () => {
  for (const invalid of [
    {phase: 'unknown'}, {party: []}, {party: [...run().party, run().party[0]]}, {party: [null, ...run().party.slice(1)]},
    {node: {...run().node, kind: 'unknown'}}, {node: {...run().node, index: 1}}, {node: {...run().node, id: null}},
    {node_index: -1}, {node_index: 20}, {node_index: 0.5}, {total_nodes: 0}, {total_nodes: '20'},
    {completed_nodes: 21}, {run_revision: '0'}, {paths: {}}, {phase: 'battle'},
    {phase: 'battle', battle: {battle_id: 'battle-1', revision: 0, view: {...view(), own: []}}},
  ]) {
    let valid = false;
    const h = harness(({url}) => url === '/api/runs' ? ok(run(valid ? {} : invalid)) : undefined);
    await h.ctx.RunUI.open(); await h.button('추천 파티로').onclick();
    const original = h.calls.requests.find(r => r.url === '/api/runs').body;
    assert.equal(h.ctx.RunUI.state, null); assert.equal(h.stored.has(RECORD_PREFIX + 'run-1'), false);
    assert.equal(h.stored.has(PENDING_PREFIX + original.creation_id), true);
    assert.ok(h.screen().textContent.includes('탐험 시작 응답을 확인하지 못했습니다.'));
    valid = true; await h.button('이 탐험 시작 확인').onclick();
    assert.deepEqual(h.calls.requests.filter(r => r.url === '/api/runs').map(r => r.body), [original, original]);
    assert.equal(h.ctx.RunUI.state.run_id, 'run-1'); assert.equal(h.stored.has(PENDING_PREFIX + original.creation_id), false);
  }
});

test('finished expedition summaries show server counters and all public choice types with actual resource transitions', async () => {
  const h = harness(), journal = journalFixture(), original = JSON.stringify(journal);
  await h.ctx.RunUI.open(); h.calls.requests.length = 0;
  h.ctx.RunUI.render(run({phase: 'complete', completed_nodes: 20, node_index: 19, journal}));
  const box = h.ctx.document.getElementById('rogueJournal');
  assert.equal(box.tagName, 'SECTION');
  for (const text of ['탐험 결산', '탐험 시작부터의 기록', '전체 탐험 집계 · 10개 행동', '승리 1회 · 패배 0회 · 무승부 0회', '진행한 턴 7턴',
    '획득 20골드 · 사용 30골드', 'HP 회복 105 · PP 회복 11', '부활 1회 · 치료 1회 · 영입 1회', '보상 1회 · 재추첨 1회',
    '탐험 출발 · 전략 탐험', '전투 시작 · 숲의 수호자', '승리 · 7턴', '보상 선택 · 따뜻한 열매', '보상 다시 뽑기 · 5골드 사용',
    '길 선택 · 고요한 쉼터 · 쉼터', '쉼터 · 동료 부활', '상점 · 기술 보충 · 25골드', '동료 영입 · 이상해꽃 · 교체한 동료 리자몽',
    '선두 변경 · 한카리아스', 'HP 165/300 → 215/300', 'PP 23/34 → 34/34', '기절 1 → 0마리', '골드 115 → 90', '잠듦']) assert.ok(box.textContent.includes(text), text);
  const timeline = descendants(box).find(node => node.tagName === 'DETAILS' && node.children[0]?.textContent.includes('구간과 선택 기록'));
  assert.equal(timeline.open, true); assert.equal(descendants(timeline).filter(node => node.tagName === 'LI').length, 10);
  assert.equal(JSON.stringify(journal), original); assert.equal(h.calls.requests.length, 0);
});

test('partial journals describe the measured starting node and never infer the earlier completed run', () => {
  const h = harness(), journal = journalFixture(), event = journal.events[5];
  event.sequence = 1; event.node_index = 8; event.node_label = '새 관문'; event.after.node_index = 8; event.after.node_label = '새 관문'; event.before.node_index = 7;
  journal.complete_history = false; journal.from_node = 7; journal.total_events = 1; journal.events = [event];
  for (const key of Object.keys(journal.counts)) journal.counts[key] = key === 'path' ? 1 : 0;
  for (const key of Object.keys(journal.totals)) journal.totals[key] = 0;
  h.ctx.RunUI.render(run({phase: 'failed', node_index: 10, completed_nodes: 10, journal}));
  const box = h.ctx.document.getElementById('rogueJournal');
  assert.ok(box.textContent.includes('기록은 8구간부터 시작되었습니다. 그 이전 행동은 기록되지 않아 아래 집계에 포함되지 않습니다.'));
  assert.ok(box.textContent.includes('기록을 시작한 구간 이후 집계 · 1개 행동'));
  assert.ok(box.textContent.includes('승리 0회 · 패배 0회')); assert.ok(!box.textContent.includes('전체 탐험 집계'));
  assert.ok(box.textContent.includes('9구간 · 길 선택'));
});

test('a bounded timeline retains cumulative totals for every event and explains the omitted older details separately', () => {
  const h = harness(), journal = journalFixture(), base = journal.events[0].after;
  journal.total_events = 151; journal.dropped_events = 23;
  for (const key of Object.keys(journal.counts)) journal.counts[key] = key === 'created' ? 1 : key === 'reward' ? 150 : 0;
  for (const key of Object.keys(journal.totals)) journal.totals[key] = key === 'money_earned' ? 150 : 0;
  journal.events = Array.from({length: 128}, (_, index) => ({sequence: index + 24, kind: 'reward', node_index: 0, node_label: '숲',
    before: {...base, money: index + 122}, after: {...base, money: index + 123}, details: {reward_kind: 'money', label: '골드 꾸러미'}}));
  h.ctx.RunUI.render(run({phase: 'complete', journal}));
  const box = h.ctx.document.getElementById('rogueJournal');
  assert.ok(box.textContent.includes('전체 탐험 집계 · 151개 행동')); assert.ok(box.textContent.includes('보상 150회'));
  assert.ok(box.textContent.includes('획득 150골드')); assert.ok(!box.textContent.includes('획득 128골드'));
  assert.ok(box.textContent.includes('최근 128개만 남아 있습니다. 앞선 23개의 상세 기록은 생략됐지만 위 집계에는 포함됩니다.'));
  assert.equal(descendants(box).filter(node => node.tagName === 'LI').length, 128);
});

test('missing invalid and unsupported journals remain unavailable without blocking offered gameplay', async () => {
  for (const journal of [undefined, {version: 1, available: false, reason: 'not_recorded'}, {version: 1, available: false, reason: 'invalid'},
    {version: 1, available: false, reason: 'unsupported_version'}, {...journalFixture(), version: 2},
    {...journalFixture(), totals: {turns: 'PRIVATE-BAD-DATA'}}, {...journalFixture(), events: [null]}]) {
    const h = harness(); h.ctx.RunUI.render(run({phase: 'reward', journal, rewards: [{id: 'choice', label: '고를 수 있는 보상'}]}));
    const box = h.ctx.document.getElementById('rogueJournal');
    assert.equal(box.tagName, 'DETAILS'); assert.equal(box.open, false);
    assert.ok(box.textContent.includes('기록')); assert.ok(!box.textContent.includes('HP 회복 105'));
    assert.ok(!box.textContent.includes('PRIVATE-BAD-DATA')); assert.equal(h.button('고를 수 있는 보상').disabled, false);
    await h.button('고를 수 있는 보상').onclick();
    assert.deepEqual(h.calls.requests.map(r => r.url), ['/api/runs/run-1/reward']);
  }
});

test('journal labels stay literal text and unknown private fields never enter the summary or event details', () => {
  const h = harness(), journal = journalFixture(), marker = 'PRIVATE-NEVER-DISPLAY';
  const attack = '<img src=x onerror=attack()>';
  journal.token = marker; journal.checkpoint = marker; journal.totals.opponent_team = marker;
  journal.events[3].details.label = attack; journal.events[5].node_label = '<script>attack()</script>';
  for (const event of journal.events) {
    event.checkpoint = marker; event.details.token = marker; event.details.enemy_team = marker;
    event.before.party[0].secret = marker; event.after.opponent = marker;
  }
  h.ctx.RunUI.render(run({phase: 'failed', journal}));
  const box = h.ctx.document.getElementById('rogueJournal');
  assert.ok(box.textContent.includes(attack)); assert.ok(box.textContent.includes('<script>attack()</script>'));
  assert.ok(!box.textContent.includes(marker)); assert.ok(!box.textContent.includes('run-secret'));
  assert.equal(descendants(box).filter(node => ['IMG', 'SCRIPT'].includes(node.tagName)).length, 0);
});

test('opening journal details preserves each phase choice and sends only the selected offered operation', async () => {
  const cases = [
    [{phase: 'node'}, '전투 시작', 'battle/start', {}],
    [{phase: 'node', node: {id: 'rest', kind: 'rest', label: '쉼터'}}, 'HP 회복', 'rest', {option: 'heal'}],
    [{phase: 'node', node: {id: 'shop', kind: 'shop', label: '상점'}, shop: [{id: 'offer', label: '회복 물품', price: 20}]}, '회복 물품', 'shop', {offer_id: 'offer'}],
    [{phase: 'reward', rewards: [{id: 'reward', label: '유지된 보상'}]}, '유지된 보상', 'reward', {reward_id: 'reward'}],
    [{phase: 'path', paths: [{id: 'path', label: '유지된 갈림길'}]}, '유지된 갈림길', 'path', {path_id: 'path'}],
    [{phase: 'recruit', recruit_candidates: [{id: 'new-member', team: {species: 'Venusaur'}}]}, '리자몽와 교체', 'recruit', {candidate_id: 'new-member', replace_index: 0}],
  ];
  for (const [extra, label, operation, body] of cases) {
    const h = harness(), prepared = run({...extra, run_revision: 42, journal: journalFixture()});
    await h.ctx.RunUI.open(); h.calls.requests.length = 0; h.ctx.RunUI.render(prepared);
    const choice = h.button(label), box = h.ctx.document.getElementById('rogueJournal');
    assert.equal(box.open, false); box.open = true;
    const timeline = descendants(box).find(node => node.tagName === 'DETAILS' && node.children[0]?.textContent.includes('구간과 선택 기록'));
    timeline.open = true;
    assert.equal(h.ctx.RunUI.state, prepared); assert.equal(choice.isConnected, true); assert.equal(choice.disabled, false); assert.equal(h.calls.requests.length, 0);
    await choice.onclick();
    assert.deepEqual(h.calls.requests.map(r => [r.url, r.body]), [['/api/runs/run-1/' + operation, {...body, run_revision: 42}]]);
  }
});

test('recent finished expeditions reopen the server result journal without adding client-side history counters', async () => {
  const journal = journalFixture(), original = JSON.stringify(journal);
  const h = harness(({url}) => url.endsWith('/resume') ? ok(run({phase: 'failed', completed_nodes: 8, journal})) : undefined);
  h.ctx.RunUI.render(run({phase: 'failed', completed_nodes: 8, journal})); await h.ctx.RunUI.open();
  await h.button('결과 다시 보기').onclick();
  assert.equal(h.ctx.RunUI.state.phase, 'failed'); assert.ok(h.ctx.document.getElementById('rogueJournal').textContent.includes('HP 회복 105'));
  assert.deepEqual(h.calls.requests.map(r => r.url), ['/api/runs/status', '/api/runs/run-1/resume']);
  assert.equal(JSON.stringify(journal), original);
});

test('battle controls and live logs remain separate from the preparation journal', async () => {
  const h = harness(), journal = journalFixture(); h.ctx.RunUI.render(run({journal}));
  h.ctx.RunUI.render(run({phase: 'battle', journal, battle: {battle_id: 'battle-1', revision: 0, view: view()}}));
  const tools = h.ctx.document.getElementById('rogueBattleTools');
  assert.equal(h.screen().style.display, 'none'); assert.ok(!tools.textContent.includes('탐험 기록'));
  assert.ok(!h.nodes.get('battleLog').textContent.includes('탐험 결산'));
  assert.ok(h.nodes.get('moveGrid').children.length > 0);
  await h.nodes.get('moveGrid').children[0].onclick();
  assert.equal(h.calls.requests[0].url, '/api/runs/run-1/battle/action');
});

test('explicit separate creation keeps the original pending request and gives the same party new recoverable credentials within the capacity guard', async () => {
  const h = harness(({url}) => { if (url === '/api/runs') throw new Error('response lost'); }), original = creationFixture();
  h.stored.set(PENDING_PREFIX + original.creation_id, JSON.stringify(original)); await h.ctx.RunUI.open();
  await h.button('같은 파티로 별도 새 탐험').onclick();
  const separate = h.calls.requests.find(r => r.url === '/api/runs').body;
  assert.notEqual(separate.creation_id, original.creation_id); assert.notEqual(separate.creation_secret, original.creation_secret);
  assert.deepEqual({team: separate.team, seed: separate.seed, difficulty: separate.difficulty}, original.payload);
  assert.equal(h.stored.get(PENDING_PREFIX + original.creation_id), JSON.stringify(original));
  assert.equal([...h.stored.keys()].filter(key => key.startsWith(PENDING_PREFIX)).length, 2);
  await h.buttons().filter(b => b.textContent === '이 탐험 시작 확인')[1].onclick();
  assert.deepEqual(h.calls.requests.filter(r => r.url === '/api/runs').map(r => r.body), [separate, separate]);
  for (let i = 0; i < 10; i++) h.stored.set(RECORD_PREFIX + 'active-' + i, JSON.stringify({version: 2, run: {run_id: 'active-' + i, token: 'token-' + i, phase: 'battle'}}));
  h.listeners.get('storage')({key: RECORD_PREFIX + 'active-9'});
  const fresh = h.button('같은 파티로 별도 새 탐험'); assert.equal(fresh.disabled, true);
  await fresh.onclick(); assert.equal(h.calls.requests.filter(r => r.url === '/api/runs').length, 2);
  assert.equal(h.button('이 탐험 시작 확인').disabled, false); await h.button('이 탐험 시작 확인').onclick();
  const recoveredOriginal = h.calls.requests.filter(r => r.url === '/api/runs').at(-1).body;
  assert.deepEqual(recoveredOriginal, {...original.payload, creation_id: original.creation_id, creation_secret: original.creation_secret});
});
