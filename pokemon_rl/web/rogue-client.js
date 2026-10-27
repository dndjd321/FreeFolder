(function () {
  'use strict';
  const KEY = 'freefolder_roguelike_v1';
  const HISTORY_KEY = 'freefolder_roguelike_history_v1';
  const MAX_HISTORY = 10;
  const PROFILE_KEY = 'freefolder_roguelike_profile_v1';
  let state = null, catalog = null, busy = false, selected = [], screen;
  let selectedDifficulty = 'normal';
  let epoch = 0, active = false, internalNavigation = false;
  let memoryHistory = [], storageUnavailable = false;
  const make = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const koSpecies = species => {
    const num = catalog?.species_ids?.[normalize(species)];
    return (typeof POKE_DB !== 'undefined' && POKE_DB.find(p => p.id === num)?.name) || ({mimikyu:'따라큐'})[normalize(species)] || species;
  };
  const koItem = value => {
    if (!value) return '도구 없음';
    return (typeof ITEMS_DB !== 'undefined' && Object.entries(ITEMS_DB).find(([key]) => normalize(key) === normalize(value))?.[1]?.name) || value;
  };
  const number = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const kindLabel = kind => ({battle:'일반 전투',elite:'정예 전투',boss:'보스 전투',rest:'쉼터',shop:'상점'})[kind] || '다음 구간';
  const statusLabel = status => ({brn:'화상',burn:'화상',par:'마비',slp:'잠듦',psn:'독',tox:'맹독',frz:'얼음'})[status] || status;
  const difficulty = value => value === 'hard' ? 'hard' : 'normal';
  const difficultyLabel = value => difficulty(value) === 'hard' ? '전략 탐험' : '기본 탐험';
  function mountStyles() {
    if (document.getElementById('rogueFlowStyles')) return;
    const style=make('style'); style.id='rogueFlowStyles';
    style.textContent = `
      #rogueScreen,#rogueScreen *,#rogueBattleTools,#rogueBattleTools *{box-sizing:border-box}
      #rogueScreen{font-family:'Noto Sans KR',sans-serif;font-size:14px;line-height:1.6;overflow-wrap:anywhere}
      #rogueScreen h2{font-size:clamp(21px,4vw,28px);margin:0 0 8px;color:#dff9ff}
      #rogueScreen h3{font-size:17px;margin:18px 0 8px;color:#dff9ff}
      #rogueScreen p{margin:8px 0;color:#bac8da}
      #rogueScreen button,#rogueBattleTools button{font-family:inherit;font-size:13px;line-height:1.5;letter-spacing:normal;text-transform:none;min-height:44px;min-width:44px;max-width:100%;white-space:normal;overflow-wrap:anywhere;touch-action:manipulation}
      #rogueScreen button:focus-visible,#rogueBattleTools button:focus-visible{outline:3px solid #fbbf24;outline-offset:3px}
      #rogueScreen button:disabled,#rogueBattleTools button:disabled{cursor:wait;opacity:.5}
      .rogue-panel{background:#141b29;border:1px solid #334155;border-radius:12px;padding:14px;margin:14px 0;min-width:0}
      .rogue-panel h3{margin-top:0!important}.rogue-guide{border-left:3px solid #22d3ee}
      .rogue-boss{border-color:#a4723b;background:#241c20}.rogue-boss strong{color:#f9c873}
      .rogue-kicker{font-size:12px;color:#67e8f9!important;font-weight:700;letter-spacing:.04em}
      .rogue-grid,.rogue-party-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:14px 0}
      .rogue-card{border:1px solid #334155;border-radius:10px;padding:14px;background:#121b29;min-width:0;overflow-wrap:anywhere}
      .rogue-card strong{color:#e2e8f0}.rogue-card p{font-size:13px}.rogue-card button{width:100%;margin:8px 0 0!important;text-align:left}
      #rogueScreen .rogue-difficulty-option{width:100%;text-align:left;min-height:88px;border-color:#475569;background:#141b29;color:#b7c9df}
      #rogueScreen .rogue-difficulty-option strong,#rogueScreen .rogue-difficulty-option span{display:block}
      #rogueScreen .rogue-difficulty-option span{margin-top:6px;font-weight:400;font-size:12px}
      #rogueScreen .rogue-difficulty-option[aria-pressed="true"]{border-color:#22d3ee;background:#0d3441;color:#ecfeff;box-shadow:inset 0 0 0 1px #22d3ee}
      .rogue-resource-bar{height:7px;background:#293345;border-radius:6px;overflow:hidden;margin:8px 0}
      .rogue-resource-fill{height:100%;background:#22c55e}.rogue-resource-low{background:#f59e0b}
      .rogue-resource-summary{display:flex;flex-wrap:wrap;gap:8px;color:#dff9ff}.rogue-resource-summary span{padding:5px 9px;border:1px solid #334155;border-radius:7px;font-size:13px}
      #rogueScreen summary{cursor:pointer;min-height:44px;padding:10px 0;font-weight:700;color:#dff9ff}
      .rogue-route{list-style:none;display:grid;grid-template-columns:repeat(10,minmax(0,1fr));gap:5px;padding:0;margin:10px 0}
      .rogue-route li{border:1px solid #334155;border-radius:6px;padding:7px 3px;text-align:center;font-size:11px;line-height:1.4;min-width:0;color:#8292aa}
      .rogue-route .current{border-color:#22d3ee;background:#0d3441;color:#cffafe;font-weight:700}.rogue-route .completed{background:#133327;color:#86efac}.rogue-route .boss{border-color:#8a6133}
      #rogueNotice{padding:12px;border:1px solid #eab308;border-radius:10px;color:#fde68a;background:#302819;margin:12px 0;white-space:pre-wrap;overflow-wrap:anywhere}
      #rogueBattleTools{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;max-width:960px;width:100%;margin:8px auto 0;padding:8px 0;font-family:'Noto Sans KR',sans-serif;color:#b7c9df;font-size:12px;text-align:left}
      #rogueBattleTools #rogueNotice{flex-basis:100%;margin:0}#rogueBattleTools button{margin:0!important}
      #battleScreen.rogue-battle-active .move-btn,#battleScreen.rogue-battle-active .tab-btn,#battleScreen.rogue-battle-active .switch-btn{min-height:44px;touch-action:manipulation}
      @media(max-width:600px){
        #rogueScreen{width:calc(100% - 16px)!important;margin:8px auto!important;padding:16px!important}
        #rogueScreen .rogue-route{grid-template-columns:repeat(5,minmax(0,1fr))}
        #rogueScreen .rogue-grid,#rogueScreen .rogue-party-grid{grid-template-columns:minmax(0,1fr)}
        #rogueScreen button{padding:11px 12px!important;margin:5px 0!important}
        #rogueScreen .rogue-choices{display:flex;flex-direction:column;gap:6px}#rogueScreen .rogue-choices button{width:100%}
        #battleScreen.rogue-battle-active .battle-field{grid-template-columns:minmax(0,1fr);box-sizing:border-box}
        #battleScreen.rogue-battle-active .battle-actions,#battleScreen.rogue-battle-active .battle-log-wrap{box-sizing:border-box;max-width:100%;width:100%;min-width:0}
        #battleScreen.rogue-battle-active .poke-battle-name{white-space:normal;overflow-wrap:anywhere}
        #battleScreen.rogue-battle-active .battle-log{max-height:115px}
        #battleScreen.rogue-battle-active .move-btn{min-height:66px}
        #rogueBattleTools{display:block}#rogueBattleTools button{width:100%;margin:6px 0!important}
      }`;
    document.head.appendChild(style);
  }
  function profile() {
    try { const p = JSON.parse(localStorage.getItem(PROFILE_KEY) || '{}'); return p && typeof p === 'object' && !Array.isArray(p) ? p : {}; } catch (_) { return {}; }
  }
  function record(run) {
    const p = profile();
    p.best = Math.max(Number(p.best) || 0, Number(run.completed_nodes) || 0);
    p.seen = [...new Set([...(Array.isArray(p.seen) ? p.seen : []), ...(run.party || []).map(mon => mon.species)])];
    p.finished = Array.isArray(p.finished) ? p.finished : [];
    if (['complete', 'failed'].includes(run.phase) && !p.finished.includes(run.run_id)) {
      if (run.phase === 'complete') p.clears = (Number(p.clears) || 0) + 1;
      p.finished.push(run.run_id); p.finished = p.finished.slice(-100);
    }
    try { localStorage.setItem(PROFILE_KEY, JSON.stringify(p)); } catch (_) {}
  }
  function validCredentials(saved) {
    return saved && typeof saved === 'object' && !Array.isArray(saved) &&
      typeof saved.run_id === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(saved.run_id) &&
      typeof saved.token === 'string' && /^[a-zA-Z0-9_-]{1,256}$/.test(saved.token);
  }
  function boundedInteger(value, min, max, fallback) {
    return Number.isSafeInteger(value) && value >= min && value <= max ? value : fallback;
  }
  function savedRun(value) {
    if (!validCredentials(value)) return null;
    const phases = ['node', 'battle', 'reward', 'path', 'recruit', 'complete', 'failed'];
    const total = boundedInteger(value.total_nodes, 1, 1000, 20);
    return {
      run_id: value.run_id, token: value.token,
      difficulty: difficulty(value.difficulty),
      phase: phases.includes(value.phase) ? value.phase : 'unknown',
      node_index: boundedInteger(value.node_index, 0, total - 1, 0),
      completed_nodes: boundedInteger(value.completed_nodes, 0, total, 0), total_nodes: total,
      node_label: typeof value.node_label === 'string' ? value.node_label.slice(0, 80) : '',
      party: Array.isArray(value.party) ? value.party.filter(p => typeof p === 'string' && p.length <= 60).slice(0, 3) : [],
      updated_at: boundedInteger(value.updated_at, 0, 8640000000000000, 0), missing: value.missing === true,
    };
  }
  function cleanHistory(values) {
    if (!Array.isArray(values)) return [];
    const seen = new Set(), next = [];
    for (const value of values.slice(0, MAX_HISTORY * 4)) {
      const saved = savedRun(value);
      if (saved && !seen.has(saved.run_id)) { seen.add(saved.run_id); next.push(saved); }
      if (next.length === MAX_HISTORY) break;
    }
    return next;
  }
  function storeHistory(values) {
    memoryHistory = cleanHistory(values);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify({version: 1, runs: memoryHistory}));
      const first = memoryHistory[0];
      if (first) localStorage.setItem(KEY, JSON.stringify({run_id: first.run_id, token: first.token}));
      else localStorage.removeItem(KEY);
    } catch (_) { storageUnavailable = true; }
    return memoryHistory;
  }
  function history() {
    if (storageUnavailable) return memoryHistory;
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      let parsed;
      if (raw && raw.length <= 100000) { try { parsed = JSON.parse(raw); } catch (_) {} }
      if (parsed && parsed.version === 1 && Array.isArray(parsed.runs)) {
        memoryHistory = cleanHistory(parsed.runs); return memoryHistory;
      }
      // Migrate a prior browser's single resume credential once. A present,
      // valid empty history intentionally overrides a stale legacy credential.
      const legacy = localStorage.getItem(KEY);
      let saved;
      if (legacy && legacy.length <= 2048) { try { saved = savedRun(JSON.parse(legacy)); } catch (_) {} }
      memoryHistory = saved ? [saved] : [];
      if (saved) storeHistory(memoryHistory);
      return memoryHistory;
    } catch (_) { storageUnavailable = true; return memoryHistory; }
  }
  function credentials(runId) {
    return history().find(saved => runId === undefined || saved.run_id === runId) || null;
  }
  function finished(saved) { return saved.phase === 'complete' || saved.phase === 'failed'; }
  function remember(run) {
    const saved = savedRun({...run, node_label: run.node?.label, party: (run.party || []).map(p => p.species), updated_at: Date.now(), missing: false});
    if (!saved) return;
    const next = history().filter(p => p.run_id !== saved.run_id);
    // Retain active runs first when a completed record can make room.
    if (next.length >= MAX_HISTORY) {
      const completed = next.map((p, i) => finished(p) ? i : -1).filter(i => i >= 0).pop();
      if (completed !== undefined) next.splice(completed, 1);
    }
    storeHistory([saved, ...next]);
  }
  function markMissing(runId) {
    storeHistory(history().map(saved => saved.run_id === runId ? {...saved, missing: true} : saved));
  }
  function storageNotice(parent = screen) {
    if (!storageUnavailable) return;
    const warning = make('p', '이 브라우저에 이어하기 정보를 저장할 수 없습니다. 현재 탭에서만 유지되며, 새로고침하거나 닫으면 접근 정보를 잃을 수 있습니다.');
    warning.setAttribute('role', 'alert'); parent.appendChild(warning);
  }
  async function request(path, body, token) {
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeout = controller ? setTimeout(() => controller.abort(), 20000) : null;
    try {
      const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
        ...(controller ? {signal: controller.signal} : {}),
        ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = result.detail;
        const error = new Error(typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : '요청을 처리하지 못했습니다.');
        if (typeof detail?.code === 'string') error.code = detail.code;
        error.status = response.status; throw error;
      }
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('서버 응답이 지연되고 있습니다. 연결을 확인하고 탐험을 다시 불러오세요.');
      throw error;
    } finally { if (timeout !== null) clearTimeout(timeout); }
  }
  function notice(message, retry = false) {
    let box = document.getElementById('rogueNotice');
    const inBattle = active && state?.phase === 'battle' && typeof EngineUI !== 'undefined' && EngineUI.session?.mode === 'run';
    const parent = inBattle ? battleTools() : screen;
    if (!box) { box = make('div'); box.id = 'rogueNotice'; }
    if (box.parentNode !== parent) parent.appendChild(box);
    box.replaceChildren(); box.appendChild(make('span', message));
    box.setAttribute('role', 'alert');
    if (retry && state?.run_id) {
      const runId = state.run_id;
      const b = button('저장 상태 다시 불러오기', () => resume(runId), box);
      b.disabled = false;
    }
  }
  function button(text, handler, parent = screen) {
    const b = make('button', text, 'result-btn');
    b.style.cssText = 'margin:6px;padding:12px 18px;cursor:pointer';
    b.disabled = busy;
    b.onclick = handler;
    parent.appendChild(b);
    return b;
  }
  function showScreen() {
    hideOtherScreens(); active = true;
    document.getElementById('battleScreen')?.classList.remove('active');
    document.getElementById('resultOverlay')?.classList.remove('show');
    screen.style.display = 'block';
    if (typeof clearTurnTimer === 'function') clearTurnTimer();
    window.scrollTo(0, 0);
  }
  function hideOtherScreens() {
    internalNavigation = true;
    try { hideAllScreens(); } finally { internalNavigation = false; }
  }
  function releaseBattle() {
    if (typeof EngineUI !== 'undefined' && EngineUI.session?.mode === 'run') {
      EngineUI.close();
      if (typeof battleState !== 'undefined' && battleState?.serverEngine) battleState = null;
    }
    const tools = document.getElementById('rogueBattleTools'); if (tools) tools.style.display = 'none';
    document.getElementById('battleScreen')?.classList.remove('rogue-battle-active');
  }
  function battleTools() {
    let tools = document.getElementById('rogueBattleTools');
    if (!tools) {
      tools = make('div'); tools.id = 'rogueBattleTools';
      const header = document.querySelector('#battleScreen .battle-header') || document.getElementById('battleScreen');
      header.appendChild(tools);
    }
    tools.style.display = '';
    return tools;
  }
  function renderBattleTools(run) {
    const tools = battleTools(); tools.replaceChildren();
    tools.appendChild(make('span', difficultyLabel(run.difficulty) + ' · ' + (run.node_index + 1) + '/' + run.total_nodes + ' · 선택한 행동은 자동으로 저장됩니다.'));
    button('탐험 준비로 · 진행 유지', () => {
      if (busy) return;
      epoch++; releaseBattle(); showStart();
    }, tools);
    document.getElementById('battleScreen')?.classList.add('rogue-battle-active');
  }
  function leaveToMenu() {
    active = false; epoch++; releaseBattle(); screen.style.display = 'none'; goContentHub();
  }
  async function act(operation, data = {}) {
    if (!state || busy) return;
    const runId = state.run_id, token = state.token, ticket = epoch;
    const runRevision = Number.isSafeInteger(state.run_revision) && state.run_revision >= 0 ? state.run_revision : 0;
    busy = true;
    const tools = document.getElementById('rogueBattleTools');
    const previousButtons = [...screen.querySelectorAll('button'), ...(tools ? tools.querySelectorAll('button') : [])].map(b => [b, b.disabled]);
    previousButtons.forEach(([b]) => { b.disabled = true; });
    try {
      const next = await request('/api/runs/' + encodeURIComponent(runId) + '/' + operation,
        {...data, run_revision: runRevision}, token);
      busy = false;
      if (state?.run_id === runId) {
        state = next; remember(next);
        if (active && epoch === ticket) render(next);
      }
      return next;
    } catch (error) {
      // The server may have committed an action before the response was lost.
      // Resume from its checkpoint instead of submitting another move.
      try {
        const next = await request('/api/runs/' + encodeURIComponent(runId) + '/resume', {}, token);
        busy = false;
        if (state?.run_id === runId) {
          state = next; remember(next);
          if (active && epoch === ticket) {
            render(next);
            notice(error.code === 'RUN_STALE'
              ? '다른 화면에서 탐험이 진행되어 최신 상태를 불러왔습니다. 내용을 확인하고 다시 선택하세요.'
              : '연결을 복구해 저장한 진행을 불러왔습니다. ' + error.message);
          }
        }
        return next;
      }
      catch (recoveryError) { if (recoveryError.status === 404) markMissing(runId); }
      if (active && epoch === ticket) notice(error.message, true);
      throw error;
    } finally {
      busy = false;
      previousButtons.forEach(([b, disabled]) => { if (b.isConnected) b.disabled = disabled; });
    }
  }
  function safeAct(operation, data) { return act(operation, data).catch(() => null); }
  function panel(title, className = '') {
    const box = make('section', undefined, 'rogue-panel ' + className);
    box.appendChild(make('h3', title)); return box;
  }
  function renderStepGuide(run) {
    const guides = {
      node: run.node?.kind === 'rest' ? '한 가지 회복을 선택하세요. 파티의 HP·PP·기절 상태를 확인하면 다음 전투를 준비하기 쉽습니다.'
        : run.node?.kind === 'shop' ? '필요한 물품을 사고 상점을 떠나세요. 남은 골드와 파티 상태를 함께 확인하세요.'
        : '상대의 전투 테마와 다음 보스를 확인한 뒤 전투를 시작하세요. 파티의 HP·PP·상태는 다음 전투에도 이어집니다.',
      reward: '보상은 하나만 받을 수 있습니다. 파티에 필요한 회복·기술 보충과 앞으로의 보스 대비를 비교하세요.',
      path: '다음 구간은 아래 카드에서 선택하세요. 상대 테마·준비할 점·받을 수 있는 보상을 비교할 수 있습니다.',
      recruit: '새 동료를 영입하려면 현재 파티 한 마리와 교체하세요. 현재 파티를 유지할 수도 있습니다.',
      complete: '20개 구간을 모두 돌파했습니다. 다른 파티로 새 탐험을 준비할 수 있습니다.',
      failed: '이번 탐험 기록은 남아 있습니다. 새 탐험에서는 휴식과 보상으로 파티 자원을 관리해 보세요.',
    };
    const box = panel('지금 할 일', 'rogue-guide'); box.appendChild(make('p', guides[run.phase] || '다음 행동을 선택하세요.'));
    screen.appendChild(box); return box;
  }
  function renderJourney(journey) {
    if (!journey) return;
    const nodes = Array.isArray(journey.nodes) ? journey.nodes.slice(0, 40) : [];
    if (nodes.length) {
      const box = make('details', undefined, 'rogue-panel');
      const current = nodes.find(node => node.status === 'current');
      box.appendChild(make('summary', nodes.length + '구간 여정 보기' + (current ? ' · 현재 ' + (number(current.index) + 1) + '구간' : '')));
      box.appendChild(make('p', '현재 위치와 회복 구간, 보스까지의 길을 확인하세요.'));
      const route = make('ol', undefined, 'rogue-route'); route.setAttribute('aria-label', '탐험 구간 진행 상황');
      for (const node of nodes) {
        const status = ['completed','current','upcoming'].includes(node.status) ? node.status : 'upcoming';
        const item = make('li', (number(node.index) + 1) + ' · ' + (node.kind_label || kindLabel(node.kind)), status + (node.kind === 'boss' ? ' boss' : ''));
        item.setAttribute('aria-label', (number(node.index) + 1) + '구간 ' + (node.label || kindLabel(node.kind)) + ' · ' + ({completed:'완료',current:'현재',upcoming:'예정'})[status]);
        if (status === 'current') item.setAttribute('aria-current', 'step');
        route.appendChild(item);
      }
      box.appendChild(route); screen.appendChild(box);
    }
    if (journey.next_boss) {
      const boss = journey.next_boss, box = panel('다음 보스 예고', 'rogue-boss');
      box.appendChild(make('strong', (number(boss.index) + 1) + '구간 · ' + (boss.label || '보스 전투')));
      box.appendChild(make('p', number(boss.distance) === 0 ? '지금 보스전에 도착했습니다.' : '현재 위치에서 ' + number(boss.distance) + '구간 뒤에 만납니다.', 'rogue-kicker'));
      if (boss.description) box.appendChild(make('p', boss.description));
      if (boss.preparation) box.appendChild(make('p', '준비할 점: ' + boss.preparation));
      if (boss.reward) box.appendChild(make('p', '승리 보상: ' + boss.reward));
      screen.appendChild(box);
    }
  }
  function resourceTotals(run) {
    const party = run.party || [], totals = {hp:0,maxhp:0,pp:0,maxpp:0,fainted:0,status:0,exhausted:0};
    for (const pokemon of party) {
      const resource = pokemon.resource || {}, moves = resource.moves || resource.moveSlots || resource.movesPP || [];
      totals.hp += number(resource.hp); totals.maxhp += number(resource.maxhp);
      if (number(resource.hp) === 0) totals.fainted++;
      if (resource.status) totals.status++;
      for (const move of Array.isArray(moves) ? moves : []) {
        totals.pp += number(move.pp); totals.maxpp += number(move.maxpp ?? move.pp);
        if (number(move.pp) === 0) totals.exhausted++;
      }
    }
    return totals;
  }
  function renderParty(run) {
    const totals = resourceTotals(run), summary = make('div', undefined, 'rogue-resource-summary');
    summary.setAttribute('aria-label', '파티 자원 요약');
    for (const text of ['파티 HP ' + totals.hp + '/' + totals.maxhp, '기술 PP ' + totals.pp + '/' + totals.maxpp,
      '기절 ' + totals.fainted + '마리', '상태이상 ' + totals.status + '마리']) summary.appendChild(make('span', text));
    screen.appendChild(summary);
    if (totals.fainted || totals.exhausted || (totals.maxhp && totals.hp / totals.maxhp <= .5)) {
      screen.appendChild(make('p', [totals.fainted ? '기절한 동료는 부활이 필요합니다.' : '', totals.exhausted ? 'PP가 없는 기술이 ' + totals.exhausted + '개입니다.' : '',
        totals.maxhp && totals.hp / totals.maxhp <= .5 ? '파티 HP가 절반 이하입니다. 회복 기회를 확인하세요.' : ''].filter(Boolean).join(' ')));
    }
    const group = make('div', undefined, 'rogue-party-grid');
    for (const pokemon of run.party || []) {
      const card = make('div', undefined, 'rogue-card');
      const resource = pokemon.resource || {};
      card.appendChild(make('strong', koSpecies(pokemon.species)));
      card.appendChild(make('p', 'HP ' + resource.hp + '/' + resource.maxhp + (number(resource.hp) === 0 ? ' · 기절' : resource.status ? ' · ' + statusLabel(resource.status) : '')));
      const bar = make('div', undefined, 'rogue-resource-bar'), fill = make('div', undefined, 'rogue-resource-fill' + (number(resource.hp) / number(resource.maxhp) <= .5 ? ' rogue-resource-low' : ''));
      fill.style.width = Math.min(100, number(resource.maxhp) ? number(resource.hp) / number(resource.maxhp) * 100 : 0) + '%'; bar.appendChild(fill); card.appendChild(bar);
      const pp = resource.moves || resource.moveSlots || resource.movesPP || [];
      const item = Object.hasOwn(resource, 'item') ? resource.item : pokemon.item;
      card.appendChild(make('small', '기술 ' + (pokemon.moves || []).length + '개 · ' + koItem(item)));
      if (Array.isArray(pp) && pp.length) card.appendChild(make('p', 'PP ' + pp.map(m => m.pp ?? m).join(' / ')));
      group.appendChild(card);
    }
    screen.appendChild(group);
  }
  function render(run) {
    record(run);
    if (!screen) mount();
    state = run; remember(run);
    if (run.phase === 'battle' && run.battle?.view) {
      const ticket = epoch, runId = run.run_id, revision = run.battle.revision;
      active = true; screen.style.display = 'none'; hideOtherScreens();
      document.getElementById('battleScreen').classList.add('active');
      const banner = document.getElementById('campaignBanner');
      if (banner) { banner.textContent = '로그라이크 ' + (run.node_index + 1) + '/20 · ' + difficultyLabel(run.difficulty) + ' · ' + run.node.label + (storageUnavailable ? ' · 이 기기 이어하기 저장 불가' : ''); banner.style.display = ''; }
      renderBattleTools(run);
      EngineUI.render(run.battle.view, {battle_id: run.battle.battle_id, mode: 'run', revision: run.battle.revision,
        sendAction: async action => {
          if (!active || epoch !== ticket || state?.run_id !== runId || state?.phase !== 'battle') throw new Error('탐험 화면이 변경되었습니다. 현재 탐험을 다시 불러오세요.');
          const command_id = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : String(Date.now()) + Math.random();
          const next = await act('battle/action', {action, revision, command_id});
          return next?.phase === 'battle' ? {...next.battle, mode: 'run'} : null;
        },
        onEnd: () => active && epoch === ticket && state?.run_id === runId ? safeAct('battle/finish') : null});
      return;
    }
    releaseBattle();
    showScreen(); screen.replaceChildren();
    screen.appendChild(make('h2', '🌿 로그라이크 탐험'));
    screen.appendChild(make('p', difficultyLabel(run.difficulty) + ' · ' + (run.node_index + 1) + '/' + run.total_nodes + ' · ' + (run.node?.label || '') + ' · 골드 ' + run.money));
    const guide = renderStepGuide(run);
    if (run.phase === 'node' && !['rest', 'shop'].includes(run.node.kind)) {
      if (run.node.description) guide.appendChild(make('p', run.node.description));
      if (run.node.preparation) guide.appendChild(make('p', '준비할 점: ' + run.node.preparation));
      if (run.node.reward) guide.appendChild(make('p', '승리 보상: ' + run.node.reward));
      button('⚔️ 전투 시작', () => { document.getElementById('battleLog')?.replaceChildren(); return safeAct('battle/start'); }, guide);
    }
    renderJourney(run.journey);
    renderParty(run);
    if (run.relics?.length) screen.appendChild(make('p', '런 특전: ' + run.relics.map(r => r.name + (r.stacks > 1 ? ' ×' + r.stacks : '')).join(' · ')));
    if (run.phase === 'node') {
      if (run.node.kind === 'rest') {
        screen.appendChild(make('p', '이번 쉼터에서 한 가지를 선택하세요.'));
        const choices = make('div', undefined, 'rogue-choices');
        for (const [id, label] of [['heal','HP 회복'],['pp','PP 보충'],['cure','상태이상 치료'],['revive','기절한 동료 1마리 부활 · HP 50%']]) {
          const option = (run.rest_options || []).find(p => p.id === id);
          const b = button(label, () => safeAct('rest', {option: id}), choices);
          b.disabled = busy || option?.available === false;
          if (option?.label) b.setAttribute('title', option.label);
        }
        screen.appendChild(choices);
      } else if (run.node.kind === 'shop') {
        for (const offer of run.shop || []) {
          const b = button((offer.name || offer.label || offer.id) + ' · ' + offer.price + '골드', () => safeAct('shop', {offer_id: offer.id}));
          b.disabled = busy || run.money < offer.price || offer.bought || offer.sold;
          if (offer.bought || offer.sold) b.textContent += ' · 구매 완료';
        }
        button('상점을 떠난다', () => safeAct('shop', {offer_id: 'leave'}));
      } else {
        screen.appendChild(make('p', (run.node.danger || '일반') + ' 전투 · 현재 파티의 HP·PP·상태가 이어집니다.'));
      }
    } else if (run.phase === 'reward') {
      screen.appendChild(make('h3', '보상 하나를 선택하세요'));
      for (const reward of run.rewards || []) button((reward.name || reward.label || reward.id) + (reward.description ? ' — ' + reward.description : ''), () => safeAct('reward', {reward_id: reward.id}));
      button('보상 다시 뽑기 · 남은 횟수 ' + run.rerolls_left, () => safeAct('reroll')).disabled = busy || run.rerolls_left <= 0;
    } else if (run.phase === 'path') {
      screen.appendChild(make('h3', '다음 길 선택'));
      if (run.journey?.branch_note) screen.appendChild(make('p', run.journey.branch_note));
      const choices = make('div', undefined, 'rogue-grid');
      for (const path of run.paths || []) {
        const card = make('article', undefined, 'rogue-card');
        card.appendChild(make('strong', path.label || path.id));
        card.appendChild(make('p', (path.kind_label || kindLabel(path.kind)) + ' · ' + (path.danger || '일반') + (path.theme_label ? ' · ' + path.theme_label : ''), 'rogue-kicker'));
        if (path.description) card.appendChild(make('p', path.description));
        if (path.preparation) card.appendChild(make('p', '준비할 점: ' + path.preparation));
        if (path.reward) card.appendChild(make('p', '예상 보상: ' + path.reward));
        button((path.label || path.id) + ' · 이 길로 간다', () => safeAct('path', {path_id: path.id}), card);
        choices.appendChild(card);
      }
      screen.appendChild(choices);
    } else if (run.phase === 'recruit') {
      screen.appendChild(make('h3', '새 동료 영입'));
      for (const candidate of run.recruit_candidates || []) {
        const row = make('div'); row.appendChild(make('p', koSpecies(candidate.team?.species || candidate.species || candidate.build?.species || candidate.name || candidate.id)));
        (run.party || []).forEach((p, slot) => button(koSpecies(p.species) + '와 교체', () => safeAct('recruit', {candidate_id: candidate.id, replace_index: slot}), row));
        screen.appendChild(row);
      }
      button('현재 파티 유지', () => safeAct('recruit', {candidate_id: 'skip'}));
    } else if (run.phase === 'complete' || run.phase === 'failed') {
      screen.appendChild(make('h3', run.phase === 'complete' ? '🏆 탐험 완주!' : '이번 탐험이 끝났습니다.'));
      screen.appendChild(make('p', '완료한 노드 ' + run.completed_nodes + '/' + run.total_nodes));
      button('새 탐험 준비', () => { state = null; showStart(); });
    }
    screen.appendChild(make('p', '진행 상황은 서버에 자동 저장됩니다.'));
    storageNotice();
    button('저장하고 메뉴로', leaveToMenu);
  }
  async function start(team) {
    if (busy) return;
    const saved = history();
    if (saved.length >= MAX_HISTORY && saved.every(p => !finished(p))) {
      notice('최근 탐험 10개가 모두 진행 중입니다. 이어할 탐험을 먼저 선택하거나, 필요 없는 접속 정보를 목록에서 정리한 뒤 새 탐험을 시작하세요.');
      return;
    }
    const ticket = ++epoch;
    busy = true;
    const chosenDifficulty = selectedDifficulty;
    const previousButtons = [...screen.querySelectorAll('button')].map(b => [b, b.disabled]);
    previousButtons.forEach(([b]) => { b.disabled = true; });
    try {
      const run = await request('/api/runs', {team, difficulty: chosenDifficulty});
      busy = false; remember(run);
      if (active && epoch === ticket) { state = run; render(run); }
    } catch (error) { if (active && epoch === ticket) notice(error.message); }
    finally { busy = false; previousButtons.forEach(([b, disabled]) => { if (b.isConnected) b.disabled = disabled; }); }
  }
  async function resume(runId) {
    const saved = credentials(runId);
    if (!saved || busy) return;
    if (!screen) mount();
    const ticket = ++epoch;
    releaseBattle();
    showScreen(); screen.replaceChildren(); notice('저장한 탐험을 불러오는 중...');
    busy = true;
    try {
      const run = await request('/api/runs/' + encodeURIComponent(saved.run_id) + '/resume', {}, saved.token);
      busy = false; remember(run);
      if (active && epoch === ticket) render(run);
    }
    catch (error) {
      if (error.status === 404) markMissing(saved.run_id);
      if (active && epoch === ticket) {
        busy = false;
        if (catalog) showStart();
        else {
          showScreen(); screen.replaceChildren(); renderHistory(); storageNotice();
          button('탐험 준비 화면으로', open);
        }
        notice(error.status === 404 ? '이 탐험을 서버에서 찾지 못했습니다. 접속 정보와 다른 탐험 목록은 유지됩니다. 서버 저장이 복원된 뒤 다시 시도할 수 있습니다.' : error.message);
      }
    }
    finally { busy = false; }
  }
  function renderHistory() {
    const saved = history();
    if (!saved.length) return;
    const phases = {node: '다음 구간 준비', battle: '전투 중', reward: '보상 선택', path: '갈림길 선택', recruit: '동료 영입', complete: '완주', failed: '탐험 종료', unknown: '상태 확인 전'};
    screen.appendChild(make('h3', '최근 탐험 · ' + saved.length + '/' + MAX_HISTORY));
    screen.appendChild(make('p', '이 브라우저의 이어하기 목록입니다. 새 탐험을 시작해도 진행 중인 탐험은 유지됩니다. 목록은 최대 10개이며 완료한 탐험부터 정리됩니다.'));
    saved.forEach((entry, index) => {
      const row = make('div'); row.style.cssText = 'margin:10px 0;padding:12px;border:1px solid #334155;border-radius:8px';
      row.appendChild(make('strong', (entry.node_label || '저장한 탐험') + ' · ' + phases[entry.phase] + ' · ' + difficultyLabel(entry.difficulty)));
      row.appendChild(make('p', '완료한 구간 ' + entry.completed_nodes + '/' + entry.total_nodes + (entry.party.length ? ' · ' + entry.party.map(koSpecies).join(' · ') : '')));
      if (entry.updated_at) row.appendChild(make('small', '마지막 플레이 ' + new Date(entry.updated_at).toLocaleString('ko-KR')));
      if (entry.missing) row.appendChild(make('p', '서버에서 찾지 못함 · 접속 정보를 보관하고 있습니다. 서버 저장 복원 후 다시 시도하세요.'));
      button(finished(entry) ? '결과 다시 보기' : index === 0 ? '▶ 저장한 탐험 이어하기' : '이 탐험 이어하기', () => resume(entry.run_id), row);
      const confirmation = make('div');
      const remove = button('이 기기 목록에서 제거', () => {
        confirmation.replaceChildren();
        confirmation.appendChild(make('p', '서버의 탐험은 삭제되지 않습니다. 이 기기의 접속 정보를 제거하면 다시 이어갈 수 없게 될 수 있습니다.'));
        button('목록에서 제거 확인', () => { storeHistory(history().filter(p => p.run_id !== entry.run_id)); showStart(); }, confirmation);
        button('취소', () => { confirmation.replaceChildren(); remove.disabled = false; }, confirmation);
        remove.disabled = true;
      }, row);
      row.appendChild(confirmation); screen.appendChild(row);
    });
  }
  function showStart() {
    releaseBattle();
    showScreen(); screen.replaceChildren(); selected = [];
    screen.appendChild(make('h2', '🌿 로그라이크 탐험'));
    screen.appendChild(make('p', '3마리와 함께 20개 노드를 돌파하세요. 전투 뒤에는 보상 하나를 고르고, 휴식과 상점에서 다음 싸움을 준비합니다.'));
    const p = profile();
    screen.appendChild(make('p', '최고 기록 ' + (Number(p.best) || 0) + '/20 · 완주 ' + (Number(p.clears) || 0) + '회 · 동료 도감 ' + (Array.isArray(p.seen) ? p.seen.length : 0) + '종'));
    const help = make('details', undefined, 'rogue-panel rogue-guide');
    help.appendChild(make('summary', '처음 탐험하는 방법'));
    const steps = make('ol');
    for (const text of ['추천 파티 또는 직접 고른 3마리로 출발합니다.', '전투 뒤 보상 하나를 고르고 다음 길을 선택합니다.', 'HP·PP·상태는 이어집니다. 쉼터와 상점에서 파티를 돌보세요.', '진행은 자동으로 남습니다. 탐험 준비 화면의 최근 목록에서 이어갈 수 있습니다.']) steps.appendChild(make('li', text));
    help.appendChild(steps);
    const firstBoss = catalog?.route_guide?.bosses?.[0];
    if (firstBoss) {
      help.appendChild(make('strong', '첫 보스 · ' + (number(firstBoss.index) + 1) + '구간 ' + (firstBoss.label || '보스 전투')));
      if (firstBoss.preparation) help.appendChild(make('p', '준비할 점: ' + firstBoss.preparation));
    }
    screen.appendChild(help);
    renderHistory(); storageNotice();
    const full = history().length >= MAX_HISTORY && history().every(p => !finished(p));
    if (full) screen.appendChild(make('p', '진행 중인 탐험이 10개입니다. 기존 탐험을 이어하거나 목록을 정리한 뒤 새 탐험을 시작하세요.'));
    screen.appendChild(make('h3', '새 탐험 난이도'));
    const modes = make('div', undefined, 'rogue-grid'); modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', '새 탐험 난이도');
    const modeStatus = make('p', '선택한 난이도: ' + difficultyLabel(selectedDifficulty) + ' · 새 탐험에 적용됩니다.'); modeStatus.setAttribute('aria-live', 'polite');
    const modeButtons = [];
    for (const [id, label, description] of [
      ['normal', '기본 탐험', '파티를 돌보며 20구간을 익혀 보세요. 처음 시작할 때 권장합니다.'],
      ['hard', '전략 탐험', '정예와 보스의 전투 판단이 더 까다로워집니다. 회복과 파티 조합을 신중하게 준비하세요.'],
    ]) {
      const b = button('', () => {
        if (busy || full) return;
        selectedDifficulty = id;
        for (const [value, option] of modeButtons) option.setAttribute('aria-pressed', value === selectedDifficulty ? 'true' : 'false');
        modeStatus.textContent = '선택한 난이도: ' + label + ' · 새 탐험에 적용됩니다.';
      }, modes);
      b.className += ' rogue-difficulty-option';
      b.appendChild(make('strong', label)); b.appendChild(make('span', description));
      b.setAttribute('aria-pressed', id === selectedDifficulty ? 'true' : 'false');
      b.disabled = busy || full; modeButtons.push([id, b]);
    }
    screen.appendChild(modes); screen.appendChild(modeStatus);
    const pool = catalog?.starter_pool || [];
    const recommended = catalog?.starter_team || pool.slice(0,3);
    if (recommended.length) screen.appendChild(make('p', '추천 파티: ' + recommended.map(p => koSpecies(p.species)).join(' · ')));
    button('추천 파티로 새 탐험', () => start(catalog?.starter_team || pool.slice(0, 3))).disabled = busy || full;
    screen.appendChild(make('h3', '시작 파티 직접 선택 · 3마리'));
    const selectionSummary = make('p', '아래에서 동료 3마리를 골라 주세요.'); selectionSummary.setAttribute('aria-live', 'polite');
    screen.appendChild(selectionSummary);
    const grid = make('div'); grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
    const startButton = make('button', '선택한 3마리로 시작', 'result-btn'); startButton.disabled = true;
    pool.forEach((pokemon, index) => {
      const b = button(koSpecies(pokemon.species), () => {
        if (selected.includes(index)) selected = selected.filter(i => i !== index);
        else if (selected.length < 3) selected.push(index);
        b.style.outline = selected.includes(index) ? '2px solid #22d3ee' : '';
        b.setAttribute('aria-pressed', selected.includes(index) ? 'true' : 'false');
        selectionSummary.textContent = selected.length ? '선택한 동료: ' + selected.map(i => koSpecies(pool[i].species)).join(' · ') : '아래에서 동료 3마리를 골라 주세요.';
        startButton.textContent = '선택한 파티로 시작 · ' + selected.length + '/3';
        startButton.disabled = selected.length !== 3 || busy || full;
      }, grid);
      b.setAttribute('aria-pressed', 'false');
    });
    screen.appendChild(grid); screen.appendChild(startButton);
    startButton.onclick = () => start(selected.map(index => pool[index]));
    button('◀ 메뉴로', leaveToMenu);
  }
  async function open() {
    if (!screen) mount();
    if (busy) return;
    const ticket = ++epoch; busy = true; showScreen(); screen.replaceChildren(); notice('탐험을 불러오는 중...');
    button('메뉴로', leaveToMenu).disabled = false;
    try { const next = await request('/api/runs/status'); if (active && epoch === ticket) { catalog = next; busy = false; showStart(); } }
    catch (error) { if (active && epoch === ticket) { busy = false; showScreen(); screen.replaceChildren(); notice('탐험 서버에 연결하지 못했습니다. ' + error.message); button('메뉴로', leaveToMenu); } }
    finally { busy = false; }
  }
  function mount() {
    if (screen) return;
    mountStyles();
    screen = make('section'); screen.id = 'rogueScreen';
    screen.setAttribute('aria-label', '로그라이크 탐험 준비와 진행');
    screen.style.cssText = 'display:none;width:calc(100% - 24px);box-sizing:border-box;max-width:1050px;margin:24px auto;padding:24px;color:#cbd5e1;background:#101018;border:1px solid #334155;border-radius:14px';
    document.body.appendChild(screen);
    const hub = document.getElementById('contentHubScreen');
    if (hub) { const b = button('🌿 로그라이크 탐험 · 보상과 분기 · 20노드', open, hub); b.id = 'rogueStartButton'; }
    const originalHide = window.hideAllScreens;
    window.hideAllScreens = function () {
      screen.style.display = 'none';
      if (!internalNavigation) { active = false; epoch++; releaseBattle(); }
      return originalHide.apply(this, arguments);
    };
  }
  if (document.readyState === 'complete') mount();
  else window.addEventListener('load', mount);
  window.RunUI = {open, resume, render, get state() { return state; }};
}());
