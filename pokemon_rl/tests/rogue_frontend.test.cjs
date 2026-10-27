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
    run_id: 'run-1', token: 'run-secret', phase: 'node', total_nodes: 20, node_index: 0, completed_nodes: 0,
    node: {id: 'node-0', kind: 'battle', label: '숲', danger: '일반'}, money: 100, relics: [],
    party: ['Charizard', 'Garchomp', 'Clefable'].map(species => ({species, item: 'Leftovers', moves: ['Flamethrower'], resource: {hp: 100, maxhp: 150, item: '', moves: [{pp: 10}]}})),
    rewards: [], paths: [], recruit_candidates: [], rerolls_left: 1,
    ...extra,
  };
}
function catalog() {
  const starter_pool = ['Charizard', 'Garchomp', 'Clefable', 'Venusaur'].map(species => ({species, moves: ['Flamethrower'], ability: 'Blaze', item: '', nature: 'Serious'}));
  return {starter_pool, starter_team: starter_pool.slice(0, 3), species_ids: {charizard: 6, garchomp: 445, clefable: 36, venusaur: 3}};
}
function ok(data) { return {ok: true, json: async () => data}; }

function harness(handler) {
  const all = [], stored = new Map(), staticNodes = new Map();
  const calls = {requests: [], menu: 0};
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.style = {}; this.attributes = {}; this._text = '';
      this.classList = {add() {}, remove() {}}; all.push(this);
    }
    set textContent(text) { this.replaceChildren(); this._text = String(text ?? ''); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
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
    console, document, setTimeout() { return 1; }, clearTimeout() {}, crypto: {randomUUID: () => 'command-1'},
    localStorage: {getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key)},
    POKE_DB: [{id: 6, name: '리자몽'}, {id: 445, name: '한카리아스'}, {id: 36, name: '픽시'}, {id: 3, name: '이상해꽃'}],
    MOVE_EN_KO: {flamethrower: '화염방사'}, ITEMS_DB: {}, ABILITY_EN_KO: {}, TYPE_CHART: {}, gameSettings: {},
    battleState: null, selectedTeam: [], turnNumber: 1, multiState: {active: false}, multiWs: null,
    scrollTo() {}, addEventListener() {}, clearTurnTimer() {}, renderBattle() {}, renderActions() {}, addLog() {}, playBgm() {}, switchBattleTab() {},
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
  return {ctx, calls, stored, screen, buttons, button, nodes: staticNodes};
}

test('opening the run hub starts a recommended canonical team and stores only resume credentials', async () => {
  const {ctx, calls, stored, button} = harness();
  await ctx.RunUI.open();
  await button('추천 파티').onclick();
  const request = calls.requests.find(r => r.url === '/api/runs');
  assert.equal(request.body.team.length, 3); assert.equal(request.body.team[0].species, 'Charizard');
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

test('history rejects malformed credentials and bounds untrusted summaries without sending remote URLs', async () => {
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
  assert.equal(saved.length, 10); assert.equal(saved.filter(p => p.run_id === 'valid').length, 1);
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
  assert.ok(screen().textContent.includes('10개가 모두 진행 중'));
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
