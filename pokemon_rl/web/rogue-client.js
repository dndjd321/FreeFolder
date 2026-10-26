(function () {
  'use strict';
  const KEY = 'freefolder_roguelike_v1';
  let state = null, catalog = null, busy = false, selected = [], screen;
  let epoch = 0, active = false, internalNavigation = false;
  const make = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const koSpecies = species => {
    const num = catalog?.species_ids?.[normalize(species)];
    return (typeof POKE_DB !== 'undefined' && POKE_DB.find(p => p.id === num)?.name) || species;
  };
  function credentials() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      return saved && typeof saved.run_id === 'string' && typeof saved.token === 'string' ? saved : null;
    } catch (_) { return null; }
  }
  function remember(run) {
    try { localStorage.setItem(KEY, JSON.stringify({run_id: run.run_id, token: run.token})); } catch (_) {}
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
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '요청을 처리하지 못했습니다.');
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
        {token, ...data}, token);
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
        const next = await request('/api/runs/' + encodeURIComponent(runId) + '/resume', {token}, token);
        busy = false;
        if (state?.run_id === runId) { state = next; remember(next); if (active && epoch === ticket) render(next); }
      }
      catch (_) {}
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
      card.appendChild(make('small', '기술 ' + (pokemon.moves || []).length + '개 · ' + (item || '도구 없음')));
      if (Array.isArray(pp) && pp.length) card.appendChild(make('p', 'PP ' + pp.map(m => m.pp ?? m).join(' / ')));
      group.appendChild(card);
    }
    screen.appendChild(group);
  }
  function render(run) {
    if (!screen) mount();
    state = run; remember(run);
    if (run.phase === 'battle' && run.battle?.view) {
      const ticket = epoch, runId = run.run_id, revision = run.battle.revision;
      active = true; screen.style.display = 'none'; hideOtherScreens();
      document.getElementById('battleScreen').classList.add('active');
      const banner = document.getElementById('campaignBanner');
      if (banner) { banner.textContent = '로그라이크 ' + (run.node_index + 1) + '/20 · ' + run.node.label; banner.style.display = ''; }
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
    button('저장하고 메뉴로', leaveToMenu);
  }
  async function start(team) {
    if (busy) return;
    const ticket = ++epoch;
    busy = true;
    try {
      const run = await request('/api/runs', {team});
      busy = false; remember(run);
      if (active && epoch === ticket) { state = run; render(run); }
    } catch (error) { if (active && epoch === ticket) notice(error.message); }
    finally { busy = false; }
  }
  async function resume() {
    const saved = credentials();
    if (!saved || busy) return;
    if (!screen) mount();
    const ticket = ++epoch;
    showScreen(); screen.replaceChildren(); notice('저장한 탐험을 불러오는 중...');
    busy = true;
    try {
      const run = await request('/api/runs/' + encodeURIComponent(saved.run_id) + '/resume', {token: saved.token}, saved.token);
      busy = false; remember(run);
      if (active && epoch === ticket) render(run);
    }
    catch (error) { if (active && epoch === ticket) notice(error.message); }
    finally { busy = false; }
  }
  function showStart() {
    showScreen(); screen.replaceChildren(); selected = [];
    screen.appendChild(make('h2', '🌿 로그라이크 탐험'));
    screen.appendChild(make('p', '3마리와 함께 20개 노드를 돌파하세요. 전투 뒤에는 보상 하나를 고르고, 휴식과 상점에서 다음 싸움을 준비합니다.'));
    if (credentials()) button('▶ 저장한 탐험 이어하기', resume);
    const pool = catalog?.starter_pool || [];
    button('추천 파티로 새 탐험', () => start(catalog?.starter_team || pool.slice(0, 3)));
    screen.appendChild(make('h3', '시작 파티 직접 선택 · 3마리'));
    const grid = make('div'); grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
    const startButton = make('button', '선택한 3마리로 시작', 'result-btn'); startButton.disabled = true;
    pool.forEach((pokemon, index) => {
      const b = button(koSpecies(pokemon.species), () => {
        if (selected.includes(index)) selected = selected.filter(i => i !== index);
        else if (selected.length < 3) selected.push(index);
        b.style.outline = selected.includes(index) ? '2px solid #22d3ee' : '';
        startButton.textContent = '선택한 파티로 시작 · ' + selected.length + '/3';
        startButton.disabled = selected.length !== 3 || busy;
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
