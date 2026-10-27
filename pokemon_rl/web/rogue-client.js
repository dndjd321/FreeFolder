(function () {
  'use strict';
  const KEY = 'freefolder_roguelike_v1';
  const HISTORY_KEY = 'freefolder_roguelike_history_v1';
  const MAX_HISTORY = 10;
  const PROFILE_KEY = 'freefolder_roguelike_profile_v1';
  let state = null, catalog = null, busy = false, selected = [], screen;
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
        const error = new Error(typeof result.detail === 'string' ? result.detail : '요청을 처리하지 못했습니다.');
        error.status = response.status; throw error;
      }
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('서버 응답이 지연되고 있습니다. 연결을 확인하고 탐험을 다시 불러오세요.');
      throw error;
    } finally { if (timeout !== null) clearTimeout(timeout); }
  }
  function notice(message) {
    let box = document.getElementById('rogueNotice');
    if (!box) { box = make('div', '', 'multi-status'); box.id = 'rogueNotice'; screen.appendChild(box); }
    box.textContent = message;
    box.setAttribute('role', 'alert');
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
  }
  function leaveToMenu() {
    active = false; epoch++; releaseBattle(); screen.style.display = 'none'; goContentHub();
  }
  async function act(operation, data = {}) {
    if (!state || busy) return;
    const runId = state.run_id, token = state.token, ticket = epoch;
    busy = true;
    const previousButtons = [...screen.querySelectorAll('button')].map(b => [b, b.disabled]);
    previousButtons.forEach(([b]) => { b.disabled = true; });
    try {
      const next = await request('/api/runs/' + encodeURIComponent(runId) + '/' + operation,
        {...data}, token);
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
        if (state?.run_id === runId) { state = next; remember(next); if (active && epoch === ticket) render(next); }
      }
      catch (recoveryError) { if (recoveryError.status === 404) markMissing(runId); }
      if (active && epoch === ticket) notice(error.message);
      throw error;
    } finally {
      busy = false;
      previousButtons.forEach(([b, disabled]) => { if (b.isConnected) b.disabled = disabled; });
    }
  }
  function safeAct(operation, data) { return act(operation, data).catch(() => null); }
  function renderParty(run) {
    const group = make('div'); group.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap;margin:18px 0';
    for (const pokemon of run.party || []) {
      const card = make('div'); card.style.cssText = 'border:1px solid #334155;border-radius:10px;padding:14px;min-width:180px;background:#141422';
      const resource = pokemon.resource || {};
      card.appendChild(make('strong', koSpecies(pokemon.species)));
      card.appendChild(make('p', 'HP ' + resource.hp + '/' + resource.maxhp + (resource.status ? ' · ' + resource.status : '')));
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
      if (banner) { banner.textContent = '로그라이크 ' + (run.node_index + 1) + '/20 · ' + run.node.label + (storageUnavailable ? ' · 이 기기 이어하기 저장 불가' : ''); banner.style.display = ''; }
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
    screen.appendChild(make('p', (run.node_index + 1) + '/' + run.total_nodes + ' · ' + (run.node?.label || '') + ' · 골드 ' + run.money));
    renderParty(run);
    if (run.relics?.length) screen.appendChild(make('p', '런 특전: ' + run.relics.map(r => r.name + (r.stacks > 1 ? ' ×' + r.stacks : '')).join(' · ')));
    if (run.phase === 'node') {
      if (run.node.kind === 'rest') {
        screen.appendChild(make('p', '이번 쉼터에서 한 가지를 선택하세요.'));
        button('HP 회복', () => safeAct('rest', {option: 'heal'}));
        button('PP 보충', () => safeAct('rest', {option: 'pp'}));
        button('상태이상 치료', () => safeAct('rest', {option: 'cure'}));
        button('기절한 동료 1마리 부활 · HP 50%', () => safeAct('rest', {option: 'revive'}));
      } else if (run.node.kind === 'shop') {
        for (const offer of run.shop || []) {
          const b = button((offer.name || offer.label || offer.id) + ' · ' + offer.price + '골드', () => safeAct('shop', {offer_id: offer.id}));
          b.disabled = busy || run.money < offer.price || offer.bought || offer.sold;
          if (offer.bought || offer.sold) b.textContent += ' · 구매 완료';
        }
        button('상점을 떠난다', () => safeAct('shop', {offer_id: 'leave'}));
      } else {
        screen.appendChild(make('p', (run.node.danger || '일반') + ' 전투 · 현재 파티의 HP·PP·상태가 이어집니다.'));
        button('⚔️ 전투 시작', () => { document.getElementById('battleLog')?.replaceChildren(); return safeAct('battle/start'); });
      }
    } else if (run.phase === 'reward') {
      screen.appendChild(make('h3', '보상 하나를 선택하세요'));
      for (const reward of run.rewards || []) button((reward.name || reward.label || reward.id) + (reward.description ? ' — ' + reward.description : ''), () => safeAct('reward', {reward_id: reward.id}));
      button('보상 다시 뽑기 · 남은 횟수 ' + run.rerolls_left, () => safeAct('reroll')).disabled = busy || run.rerolls_left <= 0;
    } else if (run.phase === 'path') {
      screen.appendChild(make('h3', '다음 길 선택'));
      for (const path of run.paths || []) button((path.label || path.id) + ' · ' + (path.danger || path.kind || ''), () => safeAct('path', {path_id: path.id}));
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
    try {
      const run = await request('/api/runs', {team});
      busy = false; remember(run);
      if (active && epoch === ticket) { state = run; render(run); }
    } catch (error) { if (active && epoch === ticket) notice(error.message); }
    finally { busy = false; }
  }
  async function resume(runId) {
    const saved = credentials(runId);
    if (!saved || busy) return;
    if (!screen) mount();
    const ticket = ++epoch;
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
      row.appendChild(make('strong', (entry.node_label || '저장한 탐험') + ' · ' + phases[entry.phase]));
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
    renderHistory(); storageNotice();
    const full = history().length >= MAX_HISTORY && history().every(p => !finished(p));
    if (full) screen.appendChild(make('p', '진행 중인 탐험이 10개입니다. 기존 탐험을 이어하거나 목록을 정리한 뒤 새 탐험을 시작하세요.'));
    const pool = catalog?.starter_pool || [];
    button('추천 파티로 새 탐험', () => start(catalog?.starter_team || pool.slice(0, 3))).disabled = busy || full;
    screen.appendChild(make('h3', '시작 파티 직접 선택 · 3마리'));
    const grid = make('div'); grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
    const startButton = make('button', '선택한 3마리로 시작', 'result-btn'); startButton.disabled = true;
    pool.forEach((pokemon, index) => {
      const b = button(koSpecies(pokemon.species), () => {
        if (selected.includes(index)) selected = selected.filter(i => i !== index);
        else if (selected.length < 3) selected.push(index);
        b.style.outline = selected.includes(index) ? '2px solid #22d3ee' : '';
        startButton.textContent = '선택한 파티로 시작 · ' + selected.length + '/3';
        startButton.disabled = selected.length !== 3 || busy || full;
      }, grid);
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
    screen = make('section'); screen.id = 'rogueScreen';
    screen.style.cssText = 'display:none;max-width:1050px;margin:30px auto;padding:24px;color:#cbd5e1;background:#101018;border:1px solid #334155;border-radius:14px';
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
