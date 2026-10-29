/* Server battle presentation. Combat decisions are never simulated in this file. */
(function () {
  'use strict';

  const legacy = {
    startBattle: window.startBattle, playerMove: window.playerMove,
    playerSwitch: window.playerSwitch, renderActions: window.renderActions,
    goTeamSelect: window.goTeamSelect, checkServer: window.checkServer,
    connectToWs: window.connectToWs, handleMultiMsg: window.handleMultiMsg,
    sleep: window.sleep, saveSettings: window.saveSettings, hideAllScreens: window.hideAllScreens,
  };
  let session = null;
  let generation = 0;
  let pendingStart = null;
  const actionButtons = new WeakMap();
  let actionFocus = null;
  const RESUME_KEY = 'freefolder_multiplayer_resume_v1';
  let activeSocket = null, socketGeneration = 0, reconnectTimer = null, reconnectAttempts = 0;
  let memoryResumeToken = '', liveResumeToken = '', provisionalToken = '';
  let resumePending = false, resumeIntent = false, localDisconnected = false, peerDisconnected = false;
  const id = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const byId = value => document.getElementById(value);
  const moveNames = new Map();
  const itemNames = new Map();
  const abilityNames = new Map();
  if (typeof MOVE_EN_KO !== 'undefined') Object.entries(MOVE_EN_KO).forEach(([key, ko]) => moveNames.set(id(key), ko));
  if (typeof ITEMS_DB !== 'undefined') Object.entries(ITEMS_DB).forEach(([key, item]) => itemNames.set(id(key), item.name));
  if (typeof ABILITY_EN_KO !== 'undefined') Object.entries(ABILITY_EN_KO).forEach(([key, ko]) => abilityNames.set(id(key), ko));

  function dexByNum(num) {
    return typeof POKE_DB === 'undefined' ? null : POKE_DB.find(p => p.id === Number(num));
  }
  function englishValue(value, entries, label) {
    if (!value || value === '없음') return '';
    const match = entries.find(([key, translated]) => value === translated || id(value) === id(key));
    if (match) return match[0];
    if (/^[a-z0-9 '\-:.]+$/i.test(value)) return value;
    throw new Error(label + ' 이름을 확인하세요: ' + value);
  }
  function serializeTeam(team) {
    const selected = Array.isArray(team) ? team.filter(Boolean) : [];
    if (selected.length < 1 || selected.length > 6) throw new Error('포켓몬을 1~6마리 선택하세요.');
    return selected.map(p => {
      const moves = (p.selectedMoves || p.moves || []).map(value => {
        const text = typeof value === 'object' ? value.nameEn || value.id || value.name : value;
        const known = (p.allMoves || []).find(m => typeof m === 'object' &&
          (m.name === text || m.nameEn === text || moveNames.get(id(m.nameEn)) === text));
        if (known && known.nameEn) return known.nameEn;
        const translated = [...moveNames].find(([, ko]) => ko === text);
        if (translated) return translated[0];
        if (/^[a-z0-9 '\-:.]+$/i.test(text)) return text;
        throw new Error('기술 이름을 확인하세요: ' + text);
      });
      if (!moves.length || moves.length > 4) throw new Error('기술을 1~4개 선택하세요.');
      const ability = englishValue(p.selectedAbility || p.ability || p.abilities?.[0] || '', [...abilityNames], '특성');
      const item = englishValue(p.selectedItem || p.item || '', [...itemNames], '도구');
      const evs = p.evs || {};
      const canonicalStats = Object.hasOwn(evs, 'spa') || Object.hasOwn(evs, 'spe');
      return {
        id: Number(p.id), moves, ability, item, nature: p.nature || 'Serious',
        evs: {
          hp: Number(evs.hp || 0), atk: Number(evs.atk || 0), def: Number(evs.def || 0),
          spa: Number((canonicalStats ? evs.spa : evs.spatk) || 0),
          spd: Number((canonicalStats ? evs.spd : evs.spdef) || 0),
          spe: Number((canonicalStats ? evs.spe : evs.spd) || 0),
        },
      };
    });
  }

  async function api(url, options = {}) {
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), 15000) : null;
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', ...options,
        headers: {'Content-Type': 'application/json', ...(options.headers || {})},
        ...(controller ? {signal: controller.signal} : {}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '서버 요청에 실패했습니다. (' + response.status + ')');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('서버 응답이 지연되고 있습니다. 연결 상태를 확인하고 다시 시도하세요.');
      throw error;
    } finally { if (timer !== null) clearTimeout(timer); }
  }
  function badge(text, failed = false) {
    ['serverBadge', 'teamServerBadge'].forEach(key => {
      const element = byId(key);
      if (element) { element.className = 'server-badge ' + (failed ? 'offline' : 'online'); element.textContent = text; }
    });
  }
  function notice(message) {
    let element = byId('engineNotice');
    const parent = (session ? byId('moveGrid') : byId('startBattleBtn'))?.parentNode;
    if (!element) {
      element = document.createElement('div'); element.id = 'engineNotice';
      element.setAttribute('role', 'alert'); element.style.cssText = 'color:#fbbf24;padding:10px;white-space:pre-wrap';
    }
    if (parent && element.parentNode !== parent) parent.appendChild(element);
    element.textContent = message || '';
    element.style.display = message ? '' : 'none';
  }
  function commandId() {
    return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : String(Date.now()) + '-' + Math.random().toString(36).slice(2);
  }
  function settings() {
    const value = typeof gameSettings === 'undefined' ? {} : gameSettings;
    const speed = Number(value.battleSpeed);
    return {speed: Number.isFinite(speed) && speed > 0 ? Math.min(4, Math.max(0.25, speed)) : 1,
      animations: value.showAnimations !== false, hints: value.showTypeHints !== false};
  }
  function applySettings() {
    if (!document.head) return;
    let style = byId('engineMotionStyle');
    if (!style) { style = document.createElement('style'); style.id = 'engineMotionStyle'; document.head.appendChild(style); }
    const config = settings();
    style.textContent = config.animations
      ? '#battleScreen .hp-fill{transition-duration:' + (0.3 / config.speed) + 's!important}#battleScreen .attacking,#battleScreen .hit{animation-duration:' + (0.3 / config.speed) + 's!important}'
      : '#battleScreen *,#battleScreen *::before,#battleScreen *::after{animation:none!important;transition:none!important}';
  }
  function mapPokemon(p, own) {
    const dex = dexByNum(p.num);
    const stats = p.stats || {}, boosts = p.boosts || {};
    const hp = Number.isFinite(p.hp) ? p.hp : 100;
    const maxHp = Number.isFinite(p.maxhp) && p.maxhp > 0 ? p.maxhp : 100;
    return {
      id: Number.isSafeInteger(p.num) && p.num > 0 ? p.num : 0,
      name: dex?.name || (p.revealed === false ? '미공개' : p.species || p.name || '포켓몬'),
      currentHp: hp, maxHp, types: Array.isArray(p.types) ? p.types : [],
      status: ({brn: 'burn'})[p.status] || p.status || null,
      ability: own ? abilityNames.get(id(p.ability)) || p.ability || '' : '',
      item: own ? itemNames.get(id(p.item)) || p.item || '' : '',
      atk: stats.atk || 1, def: stats.def || 1, spatk: stats.spa || 1, spdef: stats.spd || 1, spd: stats.spe || 1,
      rank_atk: boosts.atk || 0, rank_def: boosts.def || 0, rank_spatk: boosts.spa || 0,
      rank_spdef: boosts.spd || 0, rank_spd: boosts.spe || 0, rank_acc: boosts.accuracy || 0, rank_eva: boosts.evasion || 0,
      moves: own ? (p.moves || []).map(m => ({
        name: moveNames.get(id(m.id || m.name)) || m.name, nameEn: m.id,
        power: m.basePower || 0, accuracy: m.accuracy === true ? 0 : m.accuracy,
        pp: m.pp, maxPp: m.maxpp, type: m.type, category: m.category, priority: m.priority || 0,
      })) : [],
    };
  }
  function actorLabel(value, view) {
    const ownSide = view.side || 'p1';
    const match = String(value || '').match(/^(p[12])[a-z]?:\s*(.*)$/);
    if (!match) return value || '';
    const list = match[1] === ownSide ? view.own : view.opponent;
    const p = list.find(p => p.name === match[2] || p.species === match[2]);
    return (match[1] === ownSide ? '' : '상대 ') + (dexByNum(p?.num)?.name || p?.species || match[2]);
  }
  function describeEvent(line, view) {
    const fields = String(line).split('|'), tag = fields[1];
    const actor = actorLabel(fields[2], view);
    if (tag === 'move') return actor + '의 ' + (moveNames.get(id(fields[3])) || fields[3]) + '!';
    if (tag === 'switch' || tag === 'drag') return actor + ' 등장!';
    if (tag === '-damage') return actor + '의 HP가 줄었습니다. (' + fields[3] + ')';
    if (tag === '-heal') return actor + '의 HP가 회복되었습니다. (' + fields[3] + ')';
    if (tag === 'faint') return actor + '는 쓰러졌습니다!';
    if (tag === '-status') return actor + '에게 상태이상이 생겼습니다: ' + fields[3];
    if (tag === '-curestatus') return actor + '의 상태이상이 회복되었습니다.';
    if (tag === '-miss') return actor + '의 공격이 빗나갔습니다.';
    if (tag === '-supereffective') return '효과가 뛰어납니다!';
    if (tag === '-resisted') return '효과가 별로입니다.';
    if (tag === '-immune') return actor + '에게 효과가 없습니다.';
    if (tag === '-crit') return '급소에 맞았습니다!';
    if (tag === '-boost' || tag === '-unboost') return actor + '의 ' + fields[3] + (tag === '-boost' ? ' 상승 ' : ' 하락 ') + fields[4];
    if (tag === '-weather') return '날씨: ' + fields[2];
    if (tag === '-ability') return actor + '의 특성: ' + (abilityNames.get(id(fields[3])) || fields[3]);
    if (tag === 'cant') return actor + '는 행동하지 못했습니다. (' + fields[3] + ')';
    if (tag === 'error' || tag === 'message' || tag === '-message') return fields.slice(2).join(' ');
    return '';
  }
  function fieldFromEvents(events, side) {
    // Every view contains the full public event history. Rebuild from zero so
    // repeated renders and checkpoint replay cannot count a hazard twice.
    const field = {}, weatherNames = {RainDance: 'rain', SunnyDay: 'sun', Sandstorm: 'sand', Hail: 'hail', Snow: 'snow',
      PrimordialSea: 'heavyrain', DesolateLand: 'harshsun', DeltaStream: 'strongwinds'};
    const conditions = {reflect: 'reflect', lightscreen: 'lightScreen', stealthrock: 'stealthRock',
      spikes: 'spikes', toxicspikes: 'toxicSpikes', stickyweb: 'stickyWeb', tailwind: 'tailwind', auroraveil: 'auroraVeil'};
    let weather = null;
    for (const line of events) {
      const f = String(line).split('|');
      if (f[1] === '-weather') weather = weatherNames[f[2]] || null;
      if (f[1] === '-sidestart' || f[1] === '-sideend') {
        const target = String(f[2]).match(/^(p[12])(?::|$)/)?.[1];
        const key = conditions[id(String(f[3]).replace(/^move: /, ''))];
        if (target && key) {
          const name = key + (target === side ? 'Player' : 'Ai');
          const maximum = key === 'spikes' ? 3 : key === 'toxicSpikes' ? 2 : 1;
          field[name] = f[1] === '-sideend' ? 0 : Math.min(maximum, (field[name] || 0) + 1);
        }
      }
      if (f[1] === '-swapsideconditions') {
        for (const key of Object.values(conditions)) {
          [field[key + 'Player'], field[key + 'Ai']] = [field[key + 'Ai'] || 0, field[key + 'Player'] || 0];
        }
      }
      if (f[1] === '-fieldstart' || f[1] === '-fieldend') {
        const effect = String(f[2]).replace(/^move: /, '');
        if (effect === 'Trick Room') field.trickRoom = f[1] === '-fieldstart' ? 1 : 0;
        const terrain = {'Grassy Terrain': 'grassy', 'Electric Terrain': 'electric', 'Psychic Terrain': 'psychic', 'Misty Terrain': 'misty'}[effect];
        if (terrain) { field.terrain = f[1] === '-fieldstart' ? terrain : null; field.terrainTurns = field.terrain ? 1 : 0; }
      }
    }
    return {field, weather};
  }
  function moveDescription(move) {
    const category = String(move.category || '').toLowerCase();
    if (category === 'status' || move.damageKind === 'status') return '변화기';
    const prefix = ({physical: '물리 · ', special: '특수 · '})[category] || '';
    if (move.damageKind === 'fixed') {
      const amount = move.fixedDamage === 'level' ? ' (레벨)' : Number.isFinite(move.fixedDamage) ? ' ' + move.fixedDamage : '';
      return prefix + '고정 피해' + amount;
    }
    if (move.damageKind === 'conditional') return prefix + '상황에 따른 피해';
    if (move.damageKind === 'ohko') return prefix + '일격기';
    if (move.damageKind === 'variable') return prefix + '위력 변동';
    return prefix + (move.basePower > 0 ? '위력 ' + move.basePower : '위력 정보 없음');
  }
  function typeHint(move, opponent) {
    const category = String(move.category || '').toLowerCase();
    if (category === 'status' || move.damageKind === 'status' || move.typeHint === 'none' ||
        (move.target && !['normal', 'any', 'allAdjacent', 'allAdjacentFoes', 'randomNormal', 'scripted'].includes(move.target)) ||
        !opponent?.types?.length || !move.type || typeof TYPE_CHART === 'undefined') return '';
    const chart = TYPE_CHART[String(move.type).toLowerCase()];
    if (!chart) return '';
    const effectiveness = opponent.types.reduce((total, type) => total * (chart[String(type).toLowerCase()] ?? 1), 1);
    if (effectiveness === 0) return '타입 기준 · 효과 없음';
    if (move.typeHint === 'immunity' || ['fixed', 'conditional', 'ohko'].includes(move.damageKind)) return '';
    return '타입 기준 · ' + (effectiveness >= 2 ? '효과가 뛰어남' : effectiveness < 1 ? '효과가 별로' : '보통 효과');
  }
  function renderResult(view) {
    if (session.endedShown) return;
    session.endedShown = true;
    if (session.onEnd) { Promise.resolve(session.onEnd(view)).catch(error => notice(error.message)); return; }
    const won = view.winner === view.side, draw = !view.winner;
    const card = byId('resultCard');
    if (!card) return;
    card.replaceChildren();
    for (const [cls, text] of [['result-icon', draw ? '🤝' : won ? '🏆' : '💀'], ['result-title ' + (won ? 'win' : 'lose'), draw ? 'DRAW' : won ? 'VICTORY!' : 'DEFEATED...'], ['result-sub', draw ? '무승부입니다.' : won ? '배틀에서 이겼습니다!' : '다음 배틀에 다시 도전하세요.']]) {
      const element = document.createElement('div'); element.className = cls; element.textContent = text; card.appendChild(element);
    }
    const again = document.createElement('button'); again.className = 'result-btn'; again.textContent = '▶ 다시 플레이'; again.onclick = () => window.goTeamSelect(); card.appendChild(again);
    byId('resultOverlay')?.classList.add('show');
    if (typeof playBgm === 'function') playBgm(won ? 'win' : 'lose');
  }
  function render(view, context = {}) {
    if (!view || !Array.isArray(view.own) || !view.own.length || !Array.isArray(view.opponent) || !view.opponent.length) throw new Error('서버 배틀 화면 데이터를 확인할 수 없습니다.');
    cancelPendingStart();
    const battleId = context.battle_id || context.battleId || view.battle_id;
    if (!session || session.id !== battleId) session = {id: battleId, eventsSeen: 0, pending: false, endedShown: false};
    Object.assign(session, context, {id: battleId, view, pending: context.pending ?? false});
    const own = [...view.own].sort((a, b) => a.slot - b.slot), opponent = view.opponent;
    const ownActive = Math.max(0, own.findIndex(p => p.active));
    const opponentActive = Math.max(0, opponent.findIndex(p => p.active));
    const request = view.request || {wait: true};
    const forced = (request.forceSwitch || []).some(Boolean);
    const events = Array.isArray(view.events) ? view.events : [];
    const field = fieldFromEvents(events, view.side || 'p1');
    battleState = {
      serverEngine: true, turn: view.turn || 1, phase: view.ended ? 'ended' : forced ? 'switch' : request.wait ? 'waiting' : 'action',
      player: {party: own.map(p => mapPokemon(p, true)), active: ownActive, revealed: own.map(() => true)},
      ai: {party: opponent.map(p => mapPokemon(p, false)), active: opponentActive, revealed: opponent.map(p => p.revealed !== false)},
      ...field,
    };
    clearTurnTimer();
    if (typeof turnNumber !== 'undefined') turnNumber = battleState.turn;
    if (typeof renderBattle === 'function') renderBattle();
    const opponentHp = byId('aiHpNum'), activeOpponent = opponent[opponentActive];
    if (opponentHp) opponentHp.textContent = activeOpponent?.revealed === false ? '미공개' : Math.round(battleState.ai.party[opponentActive].currentHp / battleState.ai.party[opponentActive].maxHp * 100) + '%';
    events.slice(session.eventsSeen).forEach(line => { const text = describeEvent(line, view); if (text) addLog(text, 'system'); });
    session.eventsSeen = events.length;
    badge('● 서버 전투 엔진 (Showdown)');
    applySettings();
    renderActionControls();
    if (view.ended) renderResult(view);
    return battleState;
  }
  function captureActionFocus() {
    const focused = document.activeElement, control = actionButtons.get(focused);
    if (!byId('battleScreen')?.classList.contains?.('active')) { actionFocus = null; return; }
    if (control?.owner === session && !focused.disabled) {
      actionFocus = {...control, anchor: null};
    } else if (actionFocus?.owner !== session || (focused && focused !== document.body && focused !== actionFocus.anchor)) {
      actionFocus = null;
    }
  }
  function releaseActionFocusFromUser(event) {
    if (!actionFocus) return;
    for (let target = event.target; target; target = target.parentNode) {
      if (target === actionFocus.anchor || actionButtons.get(target)?.owner === actionFocus.owner) return;
    }
    actionFocus = null;
  }
  function restoreActionFocus(moveControls, switchControls, forced, blocked) {
    if (!actionFocus || actionFocus.owner !== session || session.view.ended) { actionFocus = null; return; }
    let type = forced ? 'switch' : actionFocus.type;
    const switched = type === 'switch' && !forced && session.view.own.some(p => p.active && p.slot === actionFocus.index);
    if (switched) type = 'move';
    let controls = type === 'move' ? moveControls : switchControls;
    if (!blocked && !forced && !controls.some(control => !control.button.disabled)) {
      type = type === 'move' ? 'switch' : 'move';
      controls = type === 'move' ? moveControls : switchControls;
    }
    switchBattleTab(type === 'move' ? 'fight' : 'switch');
    const target = !blocked && ((!forced && !switched && controls.find(control => control.index === actionFocus.index && !control.button.disabled)) ||
      controls.find(control => !control.button.disabled));
    if (target) {
      actionFocus = null;
      target.button.focus?.();
    } else {
      // Keep a native, persistent control focused while the action buttons are
      // disabled. Moving to any other control relinquishes this focus intent.
      const anchor = byId(type === 'move' ? 'tabFight' : 'tabSwitch');
      if (!anchor || anchor.disabled) { actionFocus = null; return; }
      actionFocus.anchor = anchor;
      anchor.focus?.();
    }
  }
  function renderActionControls() {
    if (!session || !battleState?.serverEngine) return;
    captureActionFocus();
    renderServerTimer();
    const request = session.view.request || {wait: true}, forced = (request.forceSwitch || []).some(Boolean);
    const active = request.active?.[0] || {}, own = session.view.own;
    const moves = byId('moveGrid'), switches = byId('switchGrid');
    if (!moves || !switches) return;
    const moveControls = [], switchControls = [];
    moves.replaceChildren(); switches.replaceChildren();
    const blocked = session.pending || session.paused || session.view.ended || request.wait;
    if (forced || request.wait || session.view.ended) {
      const text = document.createElement('div'); text.className = 'log-entry';
      text.textContent = session.view.ended ? '배틀이 종료되었습니다.' : forced ? '교체할 포켓몬을 선택하세요.' : '상대의 행동을 기다리고 있습니다.';
      moves.appendChild(text);
    } else (active.moves || []).forEach((move, index) => {
      const button = document.createElement('button'); button.className = 'move-btn';
      const control = {owner: session, type: 'move', index, button};
      actionButtons.set(button, control); moveControls.push(control);
      button.disabled = blocked || !!move.disabled || (move.pp !== undefined && move.pp <= 0 && move.id !== 'struggle');
      const title = document.createElement('div'); title.className = 'mb-name'; title.textContent = moveNames.get(id(move.id || move.move)) || move.move || move.name;
      const detail = document.createElement('div'); detail.className = 'mb-power'; detail.textContent = moveDescription(move) + ' · PP ' + move.pp + '/' + move.maxpp;
      button.appendChild(title); button.appendChild(detail);
      if (settings().hints && typeof TYPE_CHART !== 'undefined') {
        const opponent = session.view.opponent.find(p => p.active && p.revealed !== false);
        const label = typeHint(move, opponent);
        if (label) {
          const hint = document.createElement('div'); hint.className = 'mb-effect';
          hint.textContent = label;
          button.appendChild(hint);
        }
      }
      button.onclick = () => sendAction('move', index); moves.appendChild(button);
    });
    own.forEach(p => {
      if (p.active) return;
      const button = document.createElement('button'); button.className = 'switch-btn';
      const control = {owner: session, type: 'switch', index: p.slot, button};
      actionButtons.set(button, control); switchControls.push(control);
      button.disabled = blocked || !!p.fainted || p.hp <= 0 || (!forced && !!active.trapped);
      button.textContent = (dexByNum(p.num)?.name || p.species || p.name) + ' · HP ' + p.hp + '/' + p.maxhp;
      button.onclick = () => sendAction('switch', p.slot); switches.appendChild(button);
    });
    if (forced) {
      byId('switchReason').textContent = '교체할 포켓몬을 선택하세요.';
      switchBattleTab('switch');
    }
    restoreActionFocus(moveControls, switchControls, forced, blocked);
  }
  function renderServerTimer() {
    clearTurnTimer();
    if (session?.mode !== 'multi' || session.paused || !Number.isFinite(session.deadline_ms)) return;
    const timer = byId('turnTimer'), number = byId('turnTimerNum');
    if (!timer || !number || typeof setInterval !== 'function') return;
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((session.deadline_ms - Date.now()) / 1000));
      timer.style.display = ''; number.textContent = String(seconds);
      timer.className = 'turn-timer' + (seconds <= 5 ? ' danger' : seconds <= 15 ? ' warn' : '');
    };
    tick();
    if (typeof turnTimerInterval !== 'undefined') turnTimerInterval = setInterval(tick, 500);
  }
  async function sendAction(type, index) {
    const current = session;
    if (!current || current.pending || current.paused || current.view.ended) return;
    const request = current.view.request || {}, forced = (request.forceSwitch || []).some(Boolean);
    if (request.wait || !Number.isInteger(index)) return;
    if (type === 'move') {
      const move = request.active?.[0]?.moves?.[index];
      if (forced || !move || move.disabled || (move.pp !== undefined && move.pp <= 0 && move.id !== 'struggle')) return;
    } else if (type === 'switch') {
      const p = current.view.own.find(p => p.slot === index);
      if (!p || p.active || p.fainted || p.hp <= 0 || (!forced && request.active?.[0]?.trapped)) return;
    } else return;
    current.pending = true; notice(''); renderActionControls();
    const action = {type, index};
    try {
      if (typeof current.sendAction === 'function') {
        const result = await current.sendAction(action);
        if (result?.view && session === current) render(result.view, {...current, ...result, pending: false});
        return;
      }
      const command = commandId();
      const response = await api('/api/engine/battles/' + encodeURIComponent(current.id) + '/action', {
        method: 'POST', body: JSON.stringify({token: current.token, revision: current.revision, command_id: command, action}),
      });
      if (session === current) { render(response.view, {...current, ...response, pending: false}); notice(''); }
    } catch (error) {
      if (session === current) {
        current.pending = false;
        if (!current.sendAction) {
          try { const response = await api('/api/engine/battles/' + encodeURIComponent(current.id), {headers: {Authorization: 'Bearer ' + current.token}}); render(response.view, {...current, ...response}); } catch (_) {}
        }
        notice('행동을 처리하지 못했습니다. ' + error.message); renderActionControls();
      }
    } finally { if (session === current && typeof current.sendAction !== 'function') { current.pending = false; renderActionControls(); } }
  }
  function releasePendingStart(owner) {
    if (!owner || pendingStart !== owner) return;
    pendingStart = null;
    if (owner.button) {
      owner.button.textContent = owner.label || '▶ 배틀 시작!';
      owner.button.disabled = selectedTeam.filter(Boolean).length < 3;
    }
  }
  function cancelPendingStart() {
    if (!pendingStart) return;
    generation++;
    releasePendingStart(pendingStart);
  }
  async function close() {
    const current = session; session = null; generation++;
    actionFocus = null;
    releasePendingStart(pendingStart);
    clearTurnTimer();
    if (current?.token && !current.sendAction) {
      try { await api('/api/engine/battles/' + encodeURIComponent(current.id), {method: 'DELETE', body: JSON.stringify({token: current.token})}); } catch (_) {}
    }
  }
  async function startServerBattle() {
    const button = byId('startBattleBtn');
    if (button?.disabled) return;
    const owner = {generation: ++generation, button, label: button?.textContent};
    pendingStart = owner;
    if (button) { button.disabled = true; button.textContent = '배틀 준비 중...'; }
    notice('');
    try {
      if (selectedTeam.filter(Boolean).length !== 3) throw new Error('포켓몬 3마리를 선택하세요.');
      const response = await api('/api/engine/battles', {method: 'POST', body: JSON.stringify({team: serializeTeam(selectedTeam), difficulty: typeof aiDifficulty === 'undefined' ? 'normal' : aiDifficulty})});
      if (generation !== owner.generation || pendingStart !== owner) {
        try { await api('/api/engine/battles/' + encodeURIComponent(response.battle_id), {method: 'DELETE', body: JSON.stringify({token: response.token})}); } catch (_) {}
        return;
      }
      releasePendingStart(owner);
      hideAllScreens(); byId('battleScreen').classList.add('active'); byId('resultOverlay')?.classList.remove('show');
      if (byId('campaignBanner')) byId('campaignBanner').style.display = 'none';
      byId('battleLog').replaceChildren();
      if (typeof window.scrollTo === 'function') window.scrollTo(0, 0);
      if (typeof playBgm === 'function') playBgm('battle');
      render(response.view, response);
      switchBattleTab((response.view.request?.forceSwitch || []).some(Boolean) ? 'switch' : 'fight');
    } catch (error) {
      if (generation === owner.generation) { badge('● 서버 전투 연결 실패', true); notice('배틀을 시작하지 못했습니다. ' + error.message); }
    } finally {
      releasePendingStart(owner);
    }
  }

  function abortMultiplayer(message) {
    if (!session || session.mode !== 'multi' || session.view.ended) return;
    session.view = {...session.view, ended: true}; session.pending = false;
    battleState.phase = 'ended'; clearTurnTimer(); renderActionControls(); notice(message);
    const button = document.createElement('button'); button.className = 'result-btn';
    button.textContent = '로비로 돌아가기'; button.onclick = () => window.goTeamSelect();
    byId('moveGrid')?.appendChild(button);
  }
  function readResumeToken() {
    try {
      const value = sessionStorage.getItem(RESUME_KEY);
      return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,256}$/.test(value) ? value : memoryResumeToken;
    } catch (_) { return memoryResumeToken; }
  }
  function saveResumeToken(value) {
    memoryResumeToken = value || '';
    try { if (value) sessionStorage.setItem(RESUME_KEY, value); else sessionStorage.removeItem(RESUME_KEY); } catch (_) {}
  }
  function cancelReconnect() {
    if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  function pauseMultiplayer(message) {
    if (session?.mode === 'multi' && !session.view.ended) {
      session.paused = true; session.pending = false;
      battleState.phase = 'reconnecting'; clearTurnTimer(); renderActionControls(); notice(message);
    } else if (resumeIntent) {
      const status = byId('multiMainStatus'); if (status) status.textContent = message;
    }
  }
  function scheduleReconnect() {
    if (reconnectTimer !== null) return;
    if (reconnectAttempts >= 5) {
      pauseMultiplayer('자동 재접속에 실패했습니다. 연결을 확인한 뒤 다시 연결하세요.');
      const box = byId('engineNotice') || byId('multiMainStatus');
      if (box) {
        const retry = document.createElement('button'); retry.className = 'result-btn'; retry.textContent = '다시 연결';
        retry.onclick = () => { reconnectAttempts = 0; resumeIntent = true; connectSocket(); }; box.appendChild(retry);
      }
      return;
    }
    const delay = 1000 * Math.pow(2, reconnectAttempts++), ticket = socketGeneration;
    pauseMultiplayer('연결이 끊겼습니다. ' + Math.round(delay / 1000) + '초 뒤 다시 접속합니다. (' + reconnectAttempts + '/5)');
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (ticket === socketGeneration && readResumeToken()) connectSocket(undefined, true);
    }, delay);
  }
  function leaveMultiplayer() {
    cancelReconnect(); resumeIntent = false; resumePending = false; reconnectAttempts = 0;
    localDisconnected = false; peerDisconnected = false; saveResumeToken('');
    if (typeof multiWs !== 'undefined' && multiWs?.readyState === 1) multiWs.send(JSON.stringify({type: 'leave_room'}));
  }
  function resumeFailed(message) {
    cancelReconnect(); resumeIntent = false; resumePending = false; reconnectAttempts = 0;
    localDisconnected = false; peerDisconnected = false; saveResumeToken('');
    if (session?.mode === 'multi') { close(); battleState = null; }
    multiState.active = false; multiState.role = null; multiState.roomCode = null;
    hideAllScreens(); byId('multiScreen')?.classList.add('active');
    const status = byId('multiMainStatus');
    if (status) status.textContent = '이전 배틀을 복구할 수 없습니다. 새 방을 만들거나 참가하세요. ' + (message || '');
  }
  function decorateSocket(socket) {
    if (!socket || socket._engineUIWrapped) return;
    socket._engineUIWrapped = true;
    const send = socket.send.bind(socket);
    socket.send = function (raw) {
      if (typeof raw !== 'string') return send(raw);
      let data;
      try { data = JSON.parse(raw); } catch (_) { return send(raw); }
      if (data?.type === 'leave_room') {
        cancelReconnect(); resumeIntent = false; resumePending = false; saveResumeToken('');
      }
      if (data && ['team_ready', 'entry_ready', 'pick_ready'].includes(data.type) && Array.isArray(data.team)) {
        try { data.team = data.team.map(p => ({...p, engineSet: serializeTeam([p])[0]})); }
        catch (error) { notice('팀을 전송하지 못했습니다. ' + error.message); throw error; }
        return send(JSON.stringify(data));
      }
      return send(raw);
    };
    const onopen = socket.onopen, onmessage = socket.onmessage;
    socket.onopen = function (event) {
      if (socket !== activeSocket) return;
      const old = readResumeToken();
      if (old) {
        resumePending = true; resumeIntent = true;
        send(JSON.stringify({type: 'resume_session', resume_token: old}));
      }
      if (onopen) onopen.call(socket, event);
    };
    socket.onmessage = function (event) { if (socket === activeSocket && onmessage) onmessage.call(socket, event); };
    const onclose = socket.onclose;
    socket.onclose = function (event) {
      if (socket !== activeSocket) return;
      if (onclose) onclose.call(socket, event);
      localDisconnected = true;
      const recoverable = readResumeToken() && (resumeIntent || (session?.mode === 'multi' && !session.view.ended));
      if (recoverable) { resumeIntent = true; scheduleReconnect(); }
      else abortMultiplayer('서버 연결이 끊겨 배틀을 계속할 수 없습니다. 로비에서 다시 접속하세요.');
    };
  }
  function connectSocket(url, retrying = false) {
    cancelReconnect(); socketGeneration++;
    if (!retrying) reconnectAttempts = 0;
    activeSocket = null;
    const ownUrl = typeof location !== 'undefined' && /^https?:$/.test(location.protocol)
      ? (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/multi' : url;
    legacy.connectToWs(ownUrl);
    activeSocket = multiWs;
    decorateSocket(activeSocket);
  }
  function receiveMultiplayer(data) {
    if (!data || typeof data !== 'object') return legacy.handleMultiMsg(data);
    if (data.type === 'session') {
      if (typeof data.resume_token === 'string') {
        provisionalToken = data.resume_token;
        // Lobby and team-selection sockets are discarded by the server on
        // disconnect. Keep their credential only for this live connection.
        if (!resumePending && !resumeIntent) liveResumeToken = data.resume_token;
      }
      return;
    }
    if (data.type === 'session_resumed') {
      // The identity acknowledgement precedes the authoritative battle view.
      // A disconnect in that gap must keep the same bounded recovery attempt.
      resumePending = false; resumeIntent = true; cancelReconnect();
      liveResumeToken = data.resume_token || readResumeToken(); provisionalToken = '';
      multiState.roomCode = data.room_code || null;
      notice(''); return;
    }
    if (data.type === 'error' && resumePending && data.code === 'resume_busy') {
      // A page reload can reach the server before its old socket has closed.
      // Preserve the battle credential and let the normal retry budget settle
      // that ordering without permitting an active-session takeover.
      resumePending = false; resumeIntent = true; localDisconnected = true;
      scheduleReconnect(); return;
    }
    if (data.type === 'error' && resumePending) { liveResumeToken = provisionalToken; provisionalToken = ''; resumeFailed(data.message); return; }
    if (data.type === 'opponent_disconnected') {
      peerDisconnected = true;
      pauseMultiplayer('상대의 연결이 끊겼습니다. 최대 ' + (Number(data.seconds) || 60) + '초 동안 재접속을 기다립니다.'); return;
    }
    if (data.type === 'opponent_reconnected') {
      peerDisconnected = false;
      if (session?.mode === 'multi') { session.paused = localDisconnected; renderActionControls(); notice(localDisconnected ? '서버에 다시 접속하는 중입니다.' : '상대가 다시 접속했습니다.'); }
      return;
    }
    if (data.type === 'room_left') { saveResumeToken(''); return; }
    if (data.type === 'engine_state') {
      const newBattle = !session || session.id !== data.battle_id;
      if (newBattle) peerDisconnected = false;
      localDisconnected = false; resumeIntent = false; resumePending = false; reconnectAttempts = 0; cancelReconnect();
      if (data.view?.ended) saveResumeToken('');
      else if (liveResumeToken) saveResumeToken(liveResumeToken);
      multiState.active = true; multiState.role = data.role; multiState.oppNickname = data.opp_nickname || '상대';
      multiState.waitingForOpponent = !!data.view?.request?.wait;
      if (newBattle) {
        hideAllScreens(); byId('battleScreen').classList.add('active');
        byId('resultOverlay')?.classList.remove('show'); byId('battleLog').replaceChildren();
        if (byId('campaignBanner')) byId('campaignBanner').style.display = 'none';
        if (typeof playBgm === 'function') playBgm('battle');
      }
      const me = document.querySelector('#playerCard .battle-card-label'), opponent = document.querySelector('#aiCard .battle-card-label');
      if (me) me.textContent = '🧢 ' + (typeof gameSettings !== 'undefined' ? gameSettings.nickname : 'PLAYER');
      if (opponent) opponent.textContent = '⚔ ' + multiState.oppNickname;
      render(data.view, {
        battle_id: data.battle_id, mode: 'multi', request_token: data.request_token,
        paused: peerDisconnected || !!data.paused, deadline_ms: data.deadline_ms,
        sendAction: async action => {
          if (!multiWs || multiWs.readyState !== 1) throw new Error('서버 연결이 끊겼습니다.');
          multiWs.send(JSON.stringify({type: 'battle_action', action: action.type, index: action.index,
            command_id: commandId(), request_token: data.request_token}));
        },
      });
      if (newBattle) switchBattleTab((data.view.request?.forceSwitch || []).some(Boolean) ? 'switch' : 'fight');
      return;
    }
    if (data.type === 'opponent_left' && resumeIntent) {
      resumeFailed(data.message || '상대가 나가 이전 배틀이 종료되었습니다.'); return;
    }
    if (data.type === 'opponent_left' && session?.mode === 'multi') {
      abortMultiplayer('상대가 나가 배틀이 종료되었습니다. 로비에서 새 배틀을 시작하세요.'); return;
    }
    if (data.type === 'error' && session?.mode === 'multi') {
      session.pending = false; renderActionControls(); notice(data.message || '행동을 처리하지 못했습니다.');
    }
    if (data.type === 'team_select' && session?.mode === 'multi') close();
    return legacy.handleMultiMsg(data);
  }
  function limitUnsupportedRules() {
    const double = document.querySelector('input[name="battleFormat"][value="double"]');
    if (double) {
      double.disabled = true; double.checked = false;
      const single = document.querySelector('input[name="battleFormat"][value="single"]'); if (single) single.checked = true;
      const label = double.closest('label');
      if (label) { label.style.opacity = '.5'; label.title = '더블 배틀은 준비 중입니다.'; label.appendChild(document.createTextNode(' (준비 중)')); }
    }
    const weather = byId('ruleWeather');
    if (weather) { weather.value = ''; weather.disabled = true; weather.title = '고정 날씨는 준비 중입니다. 기술과 특성의 날씨는 적용됩니다.'; }
  }

  window.hideAllScreens = function () {
    // A pending free-battle request belongs to the current team screen.
    // The successful start releases it before performing its own transition.
    cancelPendingStart();
    if (typeof legacy.hideAllScreens === 'function') return legacy.hideAllScreens.apply(this, arguments);
  };
  window.startBattle = startServerBattle;
  window.playerMove = index => battleState?.serverEngine ? sendAction('move', index) : legacy.playerMove(index);
  window.playerSwitch = index => battleState?.serverEngine ? sendAction('switch', index) : legacy.playerSwitch(index);
  window.renderActions = () => {
    applySettings();
    if (battleState?.serverEngine) return renderActionControls();
    const result = legacy.renderActions();
    if (battleState?.campaignMode) badge('● 기존 캠페인 전투');
    return result;
  };
  window.goTeamSelect = function () { if (session?.mode === 'multi') leaveMultiplayer(); close(); return legacy.goTeamSelect(); };
  window.handleMultiMsg = receiveMultiplayer;
  window.connectToWs = connectSocket;
  window.checkServer = async function () {
    if (battleState?.campaignMode) { await legacy.checkServer(); badge('● 기존 캠페인 전투'); return; }
    try {
      const status = await api('/api/engine/status');
      const neural = document.querySelector('.diff-btn[data-diff="neural"]');
      if (neural && status.ai) {
        neural.disabled = !status.ai.neural;
        neural.title = status.ai.neural ? status.ai.note : '이 서버에서는 신경망 AI를 사용할 수 없습니다.';
        neural.textContent = status.ai.neural ? '🧠 신경망 (실험 모델)' : '🧠 신경망 (사용 불가)';
        if (!status.ai.neural && typeof aiDifficulty !== 'undefined' && aiDifficulty === 'neural') setDifficulty('normal');
      }
      badge('● 서버 전투 엔진 준비됨');
    }
    catch (_) { badge('● 서버 전투 연결을 확인하세요', true); }
  };
  window.sleep = ms => settings().animations ? legacy.sleep(ms / settings().speed) : Promise.resolve();
  window.saveSettings = function () { const result = legacy.saveSettings(); applySettings(); if (session) renderActionControls(); return result; };
  document.addEventListener?.('focusin', releaseActionFocusFromUser);
  document.addEventListener?.('pointerdown', releaseActionFocusFromUser);
  window.EngineUI = {render, serializeTeam, sendAction, close, start: startServerBattle, applySettings, get session() { return session; }};
  limitUnsupportedRules();
  applySettings();
  const restoreSession = () => {
    if (readResumeToken()) {
      resumeIntent = true; hideAllScreens(); byId('multiScreen')?.classList.add('active');
      const status = byId('multiMainStatus'); if (status) status.textContent = '이전 배틀에 다시 접속하는 중입니다...';
      connectSocket();
    }
  };
  if (document.readyState === 'complete') restoreSession();
  else if (typeof window.addEventListener === 'function') window.addEventListener('load', restoreSession, {once: true});
}());
