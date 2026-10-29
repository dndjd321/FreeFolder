(function () {
  'use strict';
  const KEY = 'freefolder_roguelike_v1';
  const HISTORY_KEY = 'freefolder_roguelike_history_v1';
  const RECORD_PREFIX = 'freefolder_roguelike_run_v2:';
  const REMOVED_PREFIX = 'freefolder_roguelike_removed_v2:';
  const PENDING_PREFIX = 'freefolder_roguelike_pending_v1:';
  const MAX_HISTORY = 10;
  const PROFILE_KEY = 'freefolder_roguelike_profile_v1';
  let state = null, catalog = null, busy = false, selected = [], screen;
  let catalogError = '';
  let selectedDifficulty = 'normal';
  let epoch = 0, active = false, internalNavigation = false;
  let memoryHistory = [], storageUnavailable = false;
  const removedRuns = new Set();
  let memoryPending = new Map();
  const volatilePending = new Set();
  let canonicalComplete = false, showingStart = false;
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
  const koAbility = value => value && ((typeof ABILITY_EN_KO !== 'undefined' &&
    Object.entries(ABILITY_EN_KO).find(([key]) => normalize(key) === normalize(value))?.[1]) || value);
  const koMove = value => (typeof MOVE_EN_KO !== 'undefined' &&
    Object.entries(MOVE_EN_KO).find(([key]) => normalize(key) === normalize(value))?.[1]) || value;
  const number = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const kindLabel = kind => ({battle:'일반 전투',elite:'정예 전투',boss:'보스 전투',rest:'쉼터',shop:'상점'})[kind] || '다음 구간';
  const statusLabel = status => ({brn:'화상',burn:'화상',par:'마비',slp:'잠듦',psn:'독',tox:'맹독',frz:'얼음'})[status] || status;
  const koType = value => {
    const key = String(value || '').toLowerCase();
    const names = {normal:'노말',fire:'불꽃',water:'물',electric:'전기',grass:'풀',ice:'얼음',fighting:'격투',poison:'독',ground:'땅',flying:'비행',psychic:'에스퍼',bug:'벌레',rock:'바위',ghost:'고스트',dragon:'드래곤',dark:'악',steel:'강철',fairy:'페어리'};
    return Object.hasOwn(names, key) ? names[key] : key;
  };
  const moveLabel = (move, fallback) => move.name_ko || move.name_en || move.id || (typeof fallback === 'string' ? fallback : '') || '기술';
  const signed = value => { const n = Number(value); return Number.isFinite(n) ? (n >= 0 ? '+' : '') + n : '+0'; };
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
      #rogueScreen .rogue-menu-return{display:block;margin:0 0 12px!important;background:#1e2d40;border:1px solid #475569;color:#dff9ff}
      #rogueScreen button:disabled,#rogueBattleTools button:disabled{cursor:wait;opacity:.5}
      .rogue-panel{background:#141b29;border:1px solid #334155;border-radius:12px;padding:14px;margin:14px 0;min-width:0}
      .rogue-panel h3{margin-top:0!important}.rogue-guide{border-left:3px solid #22d3ee}
      .rogue-boss{border-color:#a4723b;background:#241c20}.rogue-boss strong{color:#f9c873}
      .rogue-kicker{font-size:12px;color:#67e8f9!important;font-weight:700;letter-spacing:.04em}
      .rogue-grid,.rogue-party-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:14px 0}
      .rogue-card{border:1px solid #334155;border-radius:10px;padding:14px;background:#121b29;min-width:0;overflow-wrap:anywhere}
      .rogue-card strong{color:#e2e8f0}.rogue-card p{font-size:13px}.rogue-card button{width:100%;margin:8px 0 0!important;text-align:left}
      .rogue-lead{display:inline-block;padding:3px 8px;margin-left:8px;border:1px solid #22d3ee;border-radius:6px;color:#67e8f9;font-size:12px}
      .rogue-move-list{list-style:none;padding:0;margin:10px 0}.rogue-move-list li{padding:8px 0;border-top:1px solid #293345;line-height:1.6;font-size:12px}
      .rogue-move-list strong,.rogue-move-list span{display:block}.rogue-move-empty{color:#fbbf24}
      .rogue-preview{margin:10px 0;padding:10px;border:1px solid #334155;border-radius:8px;background:#101724;font-size:12px;min-width:0}
      .rogue-preview p{font-size:12px!important;margin:5px 0!important}.rogue-preview details{margin-top:8px}.rogue-preview summary{font-size:12px}
      .rogue-preview-party{padding:10px 0;border-top:1px solid #334155}
      .rogue-starter-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,145px),1fr));gap:8px;margin:12px 0}
      .rogue-starter-card{padding:8px}.rogue-starter-card>button{margin-top:0!important}
      .rogue-build{margin-top:6px}.rogue-build summary{font-size:12px}.rogue-build p{font-size:12px!important}
      .rogue-build-list{margin:6px 0;padding-left:18px;font-size:12px}.rogue-build-list li{margin:6px 0}
      .rogue-recruit-comparison article{border-top:1px solid #334155;padding:12px 0}
      .rogue-journal .rogue-grid{margin:10px 0}.rogue-journal .rogue-card{padding:12px}
      .rogue-timeline{list-style:none;margin:10px 0;padding:0;border-left:2px solid #334155}
      .rogue-timeline>li{padding:12px;margin:0 0 8px 10px;border:1px solid #334155;border-radius:8px;min-width:0;background:#101724}
      .rogue-timeline p{font-size:12px!important}.rogue-timeline details{margin-top:6px}.rogue-timeline summary{font-size:12px}
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
        #rogueBattleTools{gap:4px;margin:4px auto 0;padding:4px 0}
        #rogueBattleTools button{width:100%;margin:0!important}
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
  function validCreatedRun(run) {
    if (!validCredentials(run) || !['node', 'battle', 'reward', 'path', 'recruit', 'complete', 'failed'].includes(run.phase) ||
      !Number.isSafeInteger(run.total_nodes) || run.total_nodes < 1 || run.total_nodes > 1000 ||
      !Number.isSafeInteger(run.node_index) || run.node_index < 0 || run.node_index >= run.total_nodes ||
      !Number.isSafeInteger(run.completed_nodes) || run.completed_nodes < 0 || run.completed_nodes > run.total_nodes ||
      !Number.isSafeInteger(run.run_revision) || run.run_revision < 0 ||
      !run.node || typeof run.node !== 'object' || Array.isArray(run.node) ||
      !['battle', 'elite', 'boss', 'rest', 'shop'].includes(run.node.kind) || run.node.index !== run.node_index ||
      typeof run.node.id !== 'string' || !run.node.id ||
      !Array.isArray(run.party) || run.party.length !== 3 || !run.party.every(p => p && typeof p === 'object' && !Array.isArray(p) && typeof p.species === 'string' && p.species.length > 0) ||
      !['rewards', 'paths', 'recruit_candidates', 'relics'].every(key => Array.isArray(run[key]))) return false;
    return run.phase !== 'battle' || (run.battle && typeof run.battle.battle_id === 'string' && run.battle.battle_id.length > 0 &&
      Number.isSafeInteger(run.battle.revision) && run.battle.revision >= 0 && run.battle.view &&
      Array.isArray(run.battle.view.own) && run.battle.view.own.length === 3 && Array.isArray(run.battle.view.opponent));
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
      run_revision: boundedInteger(value.run_revision, 0, Number.MAX_SAFE_INTEGER, 0),
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
    for (const value of values) {
      const saved = savedRun(value);
      if (saved && !seen.has(saved.run_id)) { seen.add(saved.run_id); next.push(saved); }
    }
    return next;
  }
  function writeCache() {
    // These two compatibility values are disposable. Per-run records retain
    // every credential if another tab overwrites this index or quota fills it.
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify({version: 1, runs: memoryHistory})); } catch (_) {}
    try {
      const first = memoryHistory[0];
      if (first) localStorage.setItem(KEY, JSON.stringify({run_id: first.run_id, token: first.token}));
      else localStorage.removeItem(KEY);
    } catch (_) {}
  }
  function writeRecord(key, value) {
    try { localStorage.setItem(key, value); }
    catch (error) {
      const quota = error?.name === 'QuotaExceededError' || error?.code === 22 || error?.code === 1014;
      if (!quota || !canonicalComplete) throw error;
      // Free only an index that has already been migrated in full. Never evict
      // another run or a migration source that still holds its only credential.
      localStorage.removeItem(HISTORY_KEY);
      localStorage.setItem(key, value);
    }
  }
  function history() {
    if (storageUnavailable) return memoryHistory;
    try {
      const records = new Map(), keys = [];
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index); if (typeof key === 'string') keys.push(key);
      }
      for (const key of keys) {
        if (key.startsWith(REMOVED_PREFIX) && localStorage.getItem(key) === 'removed') removedRuns.add(key.slice(REMOVED_PREFIX.length));
      }
      for (const key of keys) {
        if (!key.startsWith(RECORD_PREFIX)) continue;
        const raw = localStorage.getItem(key);
        if (!raw || raw.length > 4096) continue;
        let saved;
        try { const parsed = JSON.parse(raw); if (parsed?.version === 2) saved = savedRun(parsed.run); } catch (_) {}
        if (saved && key === RECORD_PREFIX + saved.run_id && !removedRuns.has(saved.run_id)) records.set(saved.run_id, saved);
      }
      const raw = localStorage.getItem(HISTORY_KEY);
      let parsed;
      if (raw && raw.length <= 100000) { try { parsed = JSON.parse(raw); } catch (_) {} }
      let migration = [];
      if (parsed && parsed.version === 1 && Array.isArray(parsed.runs)) {
        migration = cleanHistory(parsed.runs);
      } else {
        // A valid empty index overrides the old single credential. Tombstones
        // also prevent an old tab's index from resurrecting an explicit removal.
        const legacy = localStorage.getItem(KEY);
        if (legacy && legacy.length <= 2048) {
          try { const saved = savedRun(JSON.parse(legacy)); if (saved) migration = [saved]; } catch (_) {}
        }
      }
      const missing = [];
      for (const saved of migration) {
        if (records.has(saved.run_id) || removedRuns.has(saved.run_id)) continue;
        records.set(saved.run_id, saved); missing.push(saved);
      }
      const preferred = [...migration, ...records.values()], seen = new Set();
      memoryHistory = preferred.map(saved => records.get(saved.run_id)).filter(saved => {
        if (!saved || seen.has(saved.run_id) || removedRuns.has(saved.run_id)) return false;
        seen.add(saved.run_id); return true;
      }).sort((a, b) => b.updated_at - a.updated_at);
      canonicalComplete = missing.length === 0;
      // Populate memory before migration writes, preserving every source entry
      // even when the browser becomes unavailable halfway through migration.
      for (const saved of missing) writeRecord(RECORD_PREFIX + saved.run_id, JSON.stringify({version: 2, run: saved}));
      canonicalComplete = true;
      if (missing.length) writeCache();
      return memoryHistory;
    } catch (_) { storageUnavailable = true; return memoryHistory; }
  }
  function credentials(runId) {
    return history().find(saved => runId === undefined || saved.run_id === runId) || null;
  }
  function finished(saved) { return saved.phase === 'complete' || saved.phase === 'failed'; }
  function forgetRun(runId) {
    const current = history(); removedRuns.add(runId);
    memoryHistory = current.filter(p => p.run_id !== runId);
    if (!storageUnavailable) {
      try {
        // The separate marker wins even if a delayed response writes the run's
        // record again after this removal or an old tab restores its index.
        writeRecord(REMOVED_PREFIX + runId, 'removed');
        localStorage.removeItem(RECORD_PREFIX + runId);
        writeCache();
      } catch (_) { storageUnavailable = true; }
    }
    return memoryHistory;
  }
  function upsertRun(saved) {
    const current = history(), existing = current.find(p => p.run_id === saved.run_id);
    if (removedRuns.has(saved.run_id)) return false;
    if (existing && existing.run_revision > saved.run_revision) return durableCredentials(saved);
    memoryHistory = [saved, ...current.filter(p => p.run_id !== saved.run_id)].sort((a, b) => b.updated_at - a.updated_at);
    if (!storageUnavailable) {
      try { writeRecord(RECORD_PREFIX + saved.run_id, JSON.stringify({version: 2, run: saved})); writeCache(); }
      catch (_) { storageUnavailable = true; }
    }
    return durableCredentials(saved);
  }
  function durableCredentials(saved) {
    if (storageUnavailable) return false;
    try {
      const entry = JSON.parse(localStorage.getItem(RECORD_PREFIX + saved.run_id) || 'null');
      return entry?.version === 2 && validCredentials(entry.run) && entry.run.run_id === saved.run_id &&
        entry.run.token === saved.token && localStorage.getItem(REMOVED_PREFIX + saved.run_id) !== 'removed';
    } catch (_) { storageUnavailable = true; return false; }
  }
  function remember(run) {
    const saved = savedRun({...run, node_label: run.node?.label, party: (run.party || []).map(p => p.species), updated_at: Date.now(), missing: false});
    if (!saved) return false;
    let current = history();
    // Ten is the creation limit, never a reason to discard an active credential
    // that has already been issued to another tab.
    if (!current.some(p => p.run_id === saved.run_id)) {
      while (current.length >= MAX_HISTORY) {
        const completed = current.map((p, i) => finished(p) ? i : -1).filter(i => i >= 0).pop();
        if (completed === undefined) break;
        forgetRun(current[completed].run_id); current = memoryHistory;
      }
    }
    return upsertRun(saved);
  }
  function pendingRequest(value) {
    const payload = value?.payload;
    if (!value || value.version !== 1 || typeof value.creation_id !== 'string' || typeof value.creation_secret !== 'string' ||
      !/^[0-9a-f]{64}$/.test(value.creation_id) || !/^[0-9a-f]{64}$/.test(value.creation_secret) ||
      !payload || typeof payload !== 'object' || Array.isArray(payload) || !Array.isArray(payload.team) || payload.team.length !== 3 ||
      !payload.team.every(p => p && typeof p === 'object' && !Array.isArray(p) && typeof p.species === 'string' && p.species.length > 0 && p.species.length <= 100) ||
      !['normal', 'hard'].includes(payload.difficulty) || Object.keys(payload).some(key => !['team', 'difficulty', 'seed'].includes(key)) ||
      (Object.hasOwn(payload, 'seed') && payload.seed !== null && !Number.isSafeInteger(payload.seed) &&
        !(typeof payload.seed === 'string' && Array.from(payload.seed).length <= 128))) return null;
    return {version: 1, creation_id: value.creation_id, creation_secret: value.creation_secret, payload,
      created_at: boundedInteger(value.created_at, 0, 8640000000000000, 0)};
  }
  function pendingRequests() {
    if (!storageUnavailable) {
      try {
        const found = new Map(), keys = [];
        for (let index = 0; index < localStorage.length; index++) {
          const key = localStorage.key(index); if (typeof key === 'string' && key.startsWith(PENDING_PREFIX)) keys.push(key);
        }
        for (const key of keys) {
          const raw = localStorage.getItem(key); if (!raw || raw.length > 32768) continue;
          let pending; try { pending = pendingRequest(JSON.parse(raw)); } catch (_) {}
          if (pending && key === PENDING_PREFIX + pending.creation_id) found.set(pending.creation_id, pending);
        }
        for (const id of volatilePending) if (memoryPending.has(id)) found.set(id, memoryPending.get(id));
        memoryPending = found;
      } catch (_) { storageUnavailable = true; }
    }
    return [...memoryPending.values()].sort((a, b) => a.created_at - b.created_at);
  }
  function payloadKey(payload) {
    return JSON.stringify(payload, (_key, value) => value && typeof value === 'object' && !Array.isArray(value) ?
      Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
  }
  function randomCreationCredential() {
    if (typeof crypto === 'undefined' || typeof crypto.getRandomValues !== 'function') throw new Error('안전한 탐험 시작 정보를 만들 수 없습니다. 브라우저를 업데이트한 뒤 다시 시도하세요.');
    const bytes = new Uint8Array(32); crypto.getRandomValues(bytes);
    return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  }
  function createPending(payload) {
    const pending = {version: 1, creation_id: randomCreationCredential(), creation_secret: randomCreationCredential(), payload, created_at: Date.now()};
    memoryPending.set(pending.creation_id, pending);
    if (!storageUnavailable) {
      try { writeRecord(PENDING_PREFIX + pending.creation_id, JSON.stringify(pending)); }
      catch (_) { storageUnavailable = true; volatilePending.add(pending.creation_id); }
    } else volatilePending.add(pending.creation_id);
    return pending;
  }
  function forgetPending(pending) {
    if (storageUnavailable) return;
    try {
      localStorage.removeItem(PENDING_PREFIX + pending.creation_id);
      memoryPending.delete(pending.creation_id); volatilePending.delete(pending.creation_id);
    } catch (_) { storageUnavailable = true; }
  }
  function renderPending() {
    const box = document.getElementById('roguePendingCreations'); if (!box) return;
    box.replaceChildren();
    const pending = pendingRequests(); if (!pending.length) return;
    box.appendChild(make('h3', '시작을 확인할 탐험'));
    box.appendChild(make('p', '응답을 받지 못한 탐험을 같은 정보로 다시 확인합니다.'));
    if (pending.some(p => volatilePending.has(p.creation_id))) {
      const warning = make('p', '시작 확인 정보는 현재 탭에서만 유지됩니다. 이 브라우저에 저장할 수 없어 새로고침하거나 닫으면 이 탐험을 복구할 수 없습니다.');
      warning.setAttribute('role', 'alert'); box.appendChild(warning);
    }
    const full = history().filter(p => !finished(p)).length >= MAX_HISTORY;
    for (const entry of pending) {
      const row = make('article', undefined, 'rogue-card');
      row.appendChild(make('strong', entry.payload.team.map(p => koSpecies(p.species)).join(' · ') + ' · ' + difficultyLabel(entry.payload.difficulty)));
      if (entry.created_at) row.appendChild(make('p', '시작 요청 ' + new Date(entry.created_at).toLocaleString('ko-KR')));
      button('이 탐험 시작 확인', () => {
        if (!row.isConnected) return;
        return submitPending(entry);
      }, row).disabled = busy;
      button('같은 파티로 별도 새 탐험', () => {
        if (!row.isConnected) return;
        return beginCreation(entry.payload, true);
      }, row).disabled = busy || full || !catalog || !!catalogError;
      box.appendChild(row);
    }
  }
  function markMissing(runId) {
    const saved = history().find(p => p.run_id === runId);
    if (saved) upsertRun({...saved, missing: true});
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
    b.type = 'button';
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
      epoch++; releaseBattle(); showStart(true);
    }, tools);
    document.getElementById('battleScreen')?.classList.add('rogue-battle-active');
  }
  function leaveToMenu() {
    active = false; showingStart = false; epoch++; releaseBattle(); screen.style.display = 'none'; goContentHub();
  }
  function modeReturn() {
    const back = button('◀ 모드 선택으로', leaveToMenu);
    back.id = 'rogueMenuButton'; back.className += ' rogue-menu-return'; back.disabled = false;
    return back;
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
  const journalKinds = ['created', 'battle_start', 'battle_end', 'reward', 'reroll', 'path', 'rest', 'shop', 'recruit', 'party_order'];
  const journalTotals = ['battles_started', 'battles_finished', 'wins', 'losses', 'draws', 'turns', 'money_earned', 'money_spent', 'hp_recovered', 'pp_recovered', 'revived', 'cured', 'recruits'];
  const journalInteger = value => Number.isSafeInteger(value) && value >= 0;
  function validJournalSnapshot(snapshot) {
    return snapshot && typeof snapshot === 'object' && typeof snapshot.node_label === 'string' &&
      ['node_index', 'hp', 'maxhp', 'pp', 'maxpp', 'fainted', 'money'].every(key => journalInteger(snapshot[key])) &&
      Array.isArray(snapshot.party) && snapshot.party.length <= 6 && snapshot.party.every(p => p && typeof p.species === 'string' &&
        ['hp', 'maxhp', 'pp', 'maxpp'].every(key => journalInteger(p[key])) && typeof p.status === 'string');
  }
  function validJournal(journal) {
    return journal && journal.version === 1 && journal.available === true && typeof journal.complete_history === 'boolean' &&
      journalInteger(journal.from_node) && journalInteger(journal.total_events) && journalInteger(journal.dropped_events) &&
      journal.counts && journalKinds.every(key => journalInteger(journal.counts[key])) &&
      journal.totals && journalTotals.every(key => journalInteger(journal.totals[key])) &&
      Array.isArray(journal.events) && journal.events.length <= 128 && journal.events.every(event => event &&
        journalKinds.includes(event.kind) && journalInteger(event.sequence) && journalInteger(event.node_index) &&
        typeof event.node_label === 'string' && event.details && typeof event.details === 'object' && !Array.isArray(event.details) &&
        validJournalSnapshot(event.before) && validJournalSnapshot(event.after));
  }
  function journalChoice(event) {
    const details = event.details, text = key => typeof details[key] === 'string' ? details[key] : '';
    const recovery = {heal:'HP 회복', pp:'PP 보충', cure:'상태이상 치료', revive:'동료 부활'};
    if (event.kind === 'created') return '탐험 출발' + (['normal', 'hard'].includes(details.difficulty) ? ' · ' + difficultyLabel(details.difficulty) : '');
    if (event.kind === 'battle_start') return '전투 시작' + (text('opponent_label') ? ' · ' + text('opponent_label') : '');
    if (event.kind === 'battle_end') {
      const result = details.winner === 'p1' ? '승리' : details.winner === 'p2' ? '패배' : ['draw', null].includes(details.winner) ? '무승부' : '전투 종료';
      return result + (journalInteger(details.turns) ? ' · ' + details.turns + '턴' : '');
    }
    if (event.kind === 'reward') return '보상 선택 · ' + (text('label') || recovery[details.reward_kind] || ({money:'골드', recruit:'동료 영입 기회', relic:'특전'})[details.reward_kind] || '보상');
    if (event.kind === 'reroll') return '보상 다시 뽑기' + (journalInteger(details.cost) ? ' · ' + details.cost + '골드 사용' : '');
    if (event.kind === 'path') return '길 선택 · ' + (text('label') || event.node_label) + (['battle', 'elite', 'boss', 'rest', 'shop'].includes(details.node_kind) ? ' · ' + kindLabel(details.node_kind) : '');
    if (event.kind === 'rest') return '쉼터 · ' + (recovery[details.option] || '회복 선택');
    if (event.kind === 'shop') {
      if (details.action === 'leave' || details.offer_kind === 'leave') return '상점을 떠남';
      return '상점 · ' + (text('label') || recovery[details.offer_kind] || ({recruit:'동료 영입 기회', relic:'특전'})[details.offer_kind] || '상품') +
        (journalInteger(details.price) ? ' · ' + details.price + '골드' : '');
    }
    if (event.kind === 'recruit') {
      if (details.skipped === true) return '현재 파티 유지';
      return '동료 영입' + (text('species') ? ' · ' + koSpecies(text('species')) : '') + (text('replaced_species') ? ' · 교체한 동료 ' + koSpecies(text('replaced_species')) : '');
    }
    return '선두 변경' + (text('lead_species') ? ' · ' + koSpecies(text('lead_species')) : '');
  }
  function renderJournal(run) {
    const ended = ['complete', 'failed'].includes(run.phase), journal = run.journal;
    const box = make(ended ? 'section' : 'details', undefined, 'rogue-panel rogue-journal');
    box.id = 'rogueJournal';
    if (ended) box.appendChild(make('h3', '탐험 결산'));
    else { box.open = false; box.appendChild(make('summary', '탐험 기록 보기')); }
    if (!validJournal(journal)) {
      const reason = journal?.reason;
      box.appendChild(make('p', !journal || reason === 'not_recorded' ? '이 탐험에는 이전 플레이 기록이 없습니다. 기록되지 않은 구간의 결산은 표시하지 않습니다.'
        : reason === 'unsupported_version' || (Number.isInteger(journal?.version) && journal.version !== 1) ? '현재 화면에서 지원하지 않는 탐험 기록입니다. 탐험 진행과 이어하기는 계속 이용할 수 있습니다.'
        : '탐험 기록을 읽을 수 없습니다. 탐험 진행과 이어하기는 계속 이용할 수 있습니다.'));
      screen.appendChild(box); return;
    }
    box.appendChild(make('p', journal.complete_history ? '탐험 시작부터의 기록입니다.'
      : '기록은 ' + (journal.from_node + 1) + '구간부터 시작되었습니다. 그 이전 행동은 기록되지 않아 아래 집계에 포함되지 않습니다.'));
    box.appendChild(make('p', (journal.complete_history ? '전체 탐험' : '기록을 시작한 구간 이후') + ' 집계 · ' + journal.total_events + '개 행동'));
    const totals = journal.totals, counts = journal.counts, grid = make('div', undefined, 'rogue-grid');
    for (const [title, lines] of [
      ['전투', ['시작 ' + totals.battles_started + '회 · 종료 ' + totals.battles_finished + '회',
        '승리 ' + totals.wins + '회 · 패배 ' + totals.losses + '회 · 무승부 ' + totals.draws + '회', '진행한 턴 ' + totals.turns + '턴']],
      ['골드', ['획득 ' + totals.money_earned + '골드 · 사용 ' + totals.money_spent + '골드']],
      ['파티 돌봄', ['HP 회복 ' + totals.hp_recovered + ' · PP 회복 ' + totals.pp_recovered,
        '부활 ' + totals.revived + '회 · 치료 ' + totals.cured + '회 · 영입 ' + totals.recruits + '회']],
    ]) {
      const card = make('div', undefined, 'rogue-card'); card.appendChild(make('strong', title));
      for (const line of lines) card.appendChild(make('p', line)); grid.appendChild(card);
    }
    box.appendChild(grid);
    box.appendChild(make('p', '회복량은 보상·쉼터·상점과 전투 후 회복의 실제 증가량입니다. 전투 중 기술 회복과 동료 교체는 포함하지 않습니다.'));
    box.appendChild(make('h4', '주요 선택'));
    box.appendChild(make('p', [['reward','보상'], ['reroll','재추첨'], ['path','갈림길'], ['rest','쉼터'], ['shop','상점 이용'], ['recruit','동료 선택'], ['party_order','선두 변경']]
      .map(([key, label]) => label + ' ' + counts[key] + '회').join(' · ')));
    if (journal.dropped_events) box.appendChild(make('p', '구간별 상세는 최근 ' + journal.events.length + '개만 남아 있습니다. 앞선 ' + journal.dropped_events + '개의 상세 기록은 생략됐지만 위 집계에는 포함됩니다.'));
    if (!journal.events.length) box.appendChild(make('p', '아직 기록된 행동이 없습니다.'));
    else {
      const timeline = make('details'); timeline.open = ended;
      timeline.appendChild(make('summary', '구간과 선택 기록 · ' + journal.events.length + '개'));
      const list = make('ol', undefined, 'rogue-timeline');
      for (const event of journal.events) {
        const row = make('li');
        row.appendChild(make('strong', (event.node_index + 1) + '구간 · ' + journalChoice(event)));
        if (event.node_label) row.appendChild(make('p', event.node_label));
        const old = event.before, next = event.after;
        row.appendChild(make('p', 'HP ' + old.hp + '/' + old.maxhp + ' → ' + next.hp + '/' + next.maxhp + ' · PP ' + old.pp + '/' + old.maxpp + ' → ' + next.pp + '/' + next.maxpp));
        row.appendChild(make('p', '기절 ' + old.fainted + ' → ' + next.fainted + '마리 · 골드 ' + old.money + ' → ' + next.money));
        const party = make('details'); party.appendChild(make('summary', '동료별 전후 자원'));
        for (const [label, snapshot] of [['선택 전', old], ['선택 후', next]]) {
          party.appendChild(make('strong', label));
          for (const p of snapshot.party) party.appendChild(make('p', koSpecies(p.species) + ' · HP ' + p.hp + '/' + p.maxhp + ' · PP ' + p.pp + '/' + p.maxpp +
            (p.hp === 0 ? ' · 기절' : '') + (p.status ? ' · ' + statusLabel(p.status) : '')));
        }
        row.appendChild(party); list.appendChild(row);
      }
      timeline.appendChild(list); box.appendChild(timeline);
    }
    screen.appendChild(box);
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
  function renderRecoveryPreview(preview, run, parent, context) {
    if (!preview?.before || !preview?.after) return;
    const box = make('div', undefined, 'rogue-preview'); box.setAttribute('aria-label', '선택 후 변화');
    box.appendChild(make('strong', '선택 후 변화'));
    box.appendChild(make('p', '파티 HP ' + number(preview.before.hp) + '/' + number(preview.before.maxhp) + ' → ' + number(preview.after.hp) + '/' + number(preview.after.maxhp) + ' (' + signed(preview.hp_gain) + ')'));
    box.appendChild(make('p', '기술 PP ' + number(preview.before.pp) + '/' + number(preview.before.maxpp) + ' → ' + number(preview.after.pp) + '/' + number(preview.after.maxpp) + ' (' + signed(preview.pp_gain) + ')'));
    box.appendChild(make('p', '부활 ' + number(preview.revived) + '마리 · 상태 치료 ' + number(preview.cured) + '마리'));
    if (preview.available === false) box.appendChild(make('p', context === 'rest'
      ? '현재 파티에는 추가로 회복될 자원이 없습니다. 이 선택으로 쉼터를 지나 다음 길로 진행할 수 있습니다.'
      : context === 'rest-disabled' ? '지금 선택할 수 없습니다. 다른 회복을 선택하세요.' : '현재 파티에는 추가 회복 효과가 없습니다.'));
    if (Array.isArray(preview.party) && preview.party.length) {
      const details = make('details'); details.appendChild(make('summary', '동료별 변화 보기'));
      for (const row of preview.party) {
        const pokemon = (run.party || [])[row.slot], before = row.before || {}, after = row.after || {};
        const item = make('div', undefined, 'rogue-preview-party');
        item.appendChild(make('strong', pokemon ? koSpecies(pokemon.species) : '동료 ' + (number(row.slot) + 1)));
        item.appendChild(make('p', 'HP ' + number(before.hp) + '/' + number(before.maxhp) + ' → ' + number(after.hp) + '/' + number(after.maxhp) + ' · PP ' + number(before.pp) + '/' + number(before.maxpp) + ' → ' + number(after.pp) + '/' + number(after.maxpp)));
        item.appendChild(make('p', '상태 ' + (before.status ? statusLabel(before.status) : '정상') + ' → ' + (after.status ? statusLabel(after.status) : '정상') + (row.revived ? ' · 부활' : '') + (row.cured ? ' · 치료' : '')));
        const moves = pokemon?.resource?.moves || [];
        for (const move of Array.isArray(before.moves) ? before.moves : []) {
          const next = (Array.isArray(after.moves) ? after.moves : []).find(p => p.id === move.id);
          if (!next || move.pp === next.pp) continue;
          const metadata = moves.find(p => p.id === move.id) || move;
          item.appendChild(make('p', moveLabel(metadata) + ' · PP ' + number(move.pp) + '/' + number(move.maxpp) + ' → ' + number(next.pp) + '/' + number(next.maxpp)));
        }
        details.appendChild(item);
      }
      box.appendChild(details);
    }
    parent.appendChild(box);
  }
  function renderBuildInfo(build, suppliedInfo, parent, expanded = false) {
    const info = suppliedInfo && typeof suppliedInfo === 'object' && !Array.isArray(suppliedInfo) ? suppliedInfo : {};
    const types = (Array.isArray(info.types) ? info.types : Array.isArray(build?.types) ? build.types : []).filter(type => typeof type === 'string');
    if (types.length) parent.appendChild(make('p', '타입: ' + types.map(koType).join(' · '), 'rogue-kicker'));
    const details = make('details', undefined, 'rogue-build'); details.open = expanded;
    details.appendChild(make('summary', '기술·특성·도구 보기'));
    const ability = typeof info.ability === 'string' ? info.ability : build?.ability;
    const item = typeof info.item === 'string' ? info.item : build?.item;
    details.appendChild(make('p', '특성: ' + (koAbility(ability) || '정보 없음')));
    details.appendChild(make('p', '도구: ' + (typeof item === 'string' ? koItem(item) : '정보 없음')));
    const moves = Array.isArray(info.moves) && info.moves.length ? info.moves : Array.isArray(build?.moves) ? build.moves : [];
    if (moves.length) {
      const list = make('ul', undefined, 'rogue-build-list');
      for (const value of moves) {
        const move = value && typeof value === 'object' ? value : {id: String(value || '')};
        const row = make('li');
        row.appendChild(make('strong', move.name_ko || koMove(move.name_en || move.id || '') || '기술'));
        const category = ({physical:'물리',special:'특수',status:'변화기'})[String(move.category || '').toLowerCase()];
        const parts = [move.type ? koType(move.type) : '', category || '', number(move.power) > 0 ? '기본 위력 ' + number(move.power) : ''].filter(Boolean);
        if (parts.length) row.appendChild(make('span', ' · ' + parts.join(' · ')));
        list.appendChild(row);
      }
      details.appendChild(list);
    } else details.appendChild(make('p', '기술 정보가 없습니다.'));
    parent.appendChild(details);
  }
  function renderRecruitComparison(run) {
    const comparison = make('details', undefined, 'rogue-panel rogue-recruit-comparison');
    comparison.appendChild(make('summary', '교체할 현재 파티 비교'));
    for (const pokemon of run.party || []) {
      const resource = pokemon.resource || {}, row = make('article');
      row.appendChild(make('strong', koSpecies(pokemon.species)));
      const moves = Array.isArray(resource.moves) ? resource.moves : [];
      row.appendChild(make('p', '현재 HP ' + number(resource.hp) + '/' + number(resource.maxhp) +
        (number(resource.hp) === 0 ? ' · 기절' : resource.status ? ' · ' + statusLabel(resource.status) : '')));
      if (moves.length) row.appendChild(make('p', '현재 PP ' + moves.reduce((sum, move) => sum + number(move.pp), 0) + '/' +
        (moves.every(move => typeof move.maxpp === 'number' && Number.isFinite(move.maxpp))
          ? moves.reduce((sum, move) => sum + number(move.maxpp), 0) : '?')));
      renderBuildInfo({...pokemon, item: Object.hasOwn(resource, 'item') ? resource.item : pokemon.item}, null, row);
      comparison.appendChild(row);
    }
    screen.appendChild(comparison);
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
    const canOrder = run.phase === 'node' && ['battle', 'elite', 'boss'].includes(run.node?.kind);
    const party = run.party || [];
    if (canOrder) screen.appendChild(make('p', '먼저 출전할 동료를 선두로 정할 수 있습니다. 파티의 HP·PP·도구는 그대로 유지됩니다.'));
    for (const [slot, pokemon] of party.entries()) {
      const card = make('div', undefined, 'rogue-card');
      const resource = pokemon.resource || {};
      card.appendChild(make('strong', koSpecies(pokemon.species)));
      if (slot === 0) card.appendChild(make('span', '선두', 'rogue-lead'));
      const dex = typeof POKE_DB !== 'undefined' ? POKE_DB.find(p => p.id === (catalog?.species_ids?.[normalize(pokemon.species)] || pokemon.id)) : null;
      // A form can share its base species' numeric sprite ID while changing
      // type (for example Rotom-Wash). Prefer canonical public form metadata.
      const types = Array.isArray(pokemon.types) ? pokemon.types : Array.isArray(resource.types) ? resource.types
        : String(pokemon.species || '').includes('-') ? null : dex?.types;
      if (Array.isArray(types) && types.length) card.appendChild(make('p', '타입: ' + types.map(koType).join(' · '), 'rogue-kicker'));
      card.appendChild(make('p', 'HP ' + resource.hp + '/' + resource.maxhp + (number(resource.hp) === 0 ? ' · 기절' : resource.status ? ' · ' + statusLabel(resource.status) : '')));
      const bar = make('div', undefined, 'rogue-resource-bar'), fill = make('div', undefined, 'rogue-resource-fill' + (number(resource.hp) / number(resource.maxhp) <= .5 ? ' rogue-resource-low' : ''));
      fill.style.width = Math.min(100, number(resource.maxhp) ? number(resource.hp) / number(resource.maxhp) * 100 : 0) + '%'; bar.appendChild(fill); card.appendChild(bar);
      const pp = resource.moves || resource.moveSlots || resource.movesPP || [];
      const item = Object.hasOwn(resource, 'item') ? resource.item : pokemon.item;
      card.appendChild(make('small', '기술 ' + (pokemon.moves || []).length + '개 · ' + koItem(item)));
      if (Array.isArray(pp) && pp.length) {
        const list = make('ul', undefined, 'rogue-move-list');
        for (const [index, move] of pp.entries()) {
          const row = make('li', undefined, number(move.pp) === 0 ? 'rogue-move-empty' : '');
          row.appendChild(make('strong', moveLabel(move, (pokemon.moves || [])[index])));
          const maxpp = typeof move.maxpp === 'number' && Number.isFinite(move.maxpp) ? move.maxpp : '?';
          row.appendChild(make('span', 'PP ' + number(move.pp) + '/' + maxpp + (number(move.pp) === 0 ? ' · PP 없음' : '')));
          const category = ({physical:'물리',special:'특수',status:'변화기'})[String(move.category || '').toLowerCase()];
          const detail = [move.type ? koType(move.type) : '', category || '', number(move.power) > 0 ? '위력 ' + number(move.power) : ''].filter(Boolean).join(' · ');
          if (detail) row.appendChild(make('span', detail));
          list.appendChild(row);
        }
        card.appendChild(list);
      }
      if (canOrder && slot !== 0) {
        const b = button(koSpecies(pokemon.species) + ' · 선두로', () => {
          if (state !== run || !active || busy || number(resource.hp) <= 0) return;
          return safeAct('party/order', {order: [slot, ...party.map((_, index) => index).filter(index => index !== slot)]});
        }, card);
        b.disabled = busy || number(resource.hp) <= 0;
        if (number(resource.hp) <= 0) b.setAttribute('title', '기절한 동료는 선두로 정할 수 없습니다.');
      }
      group.appendChild(card);
    }
    screen.appendChild(group);
  }
  function render(run) {
    record(run);
    if (!screen) mount();
    showingStart = false; state = run; remember(run);
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
        const choices = make('div', undefined, 'rogue-grid');
        for (const [id, label] of [['heal','HP 회복'],['pp','PP 보충'],['cure','상태이상 치료'],['revive','기절한 동료 1마리 부활 · HP 50%']]) {
          const option = (run.rest_options || []).find(p => p.id === id);
          const card = make('article', undefined, 'rogue-card');
          const b = button(option?.label || label, () => safeAct('rest', {option: id}), card);
          b.disabled = busy || option?.available === false;
          if (option?.label) b.setAttribute('title', option.label);
          renderRecoveryPreview(option?.preview, run, card, option?.available === false ? 'rest-disabled' : 'rest');
          choices.appendChild(card);
        }
        screen.appendChild(choices);
      } else if (run.node.kind === 'shop') {
        const choices = make('div', undefined, 'rogue-grid');
        for (const offer of run.shop || []) {
          const card = make('article', undefined, 'rogue-card');
          const b = button((offer.label || offer.name || offer.id) + ' · ' + offer.price + '골드', () => safeAct('shop', {offer_id: offer.id}), card);
          b.disabled = busy || run.money < offer.price || !!offer.bought || !!offer.sold;
          if (offer.bought || offer.sold) b.textContent += ' · 구매 완료';
          if (offer.description) card.appendChild(make('p', offer.description));
          renderRecoveryPreview(offer.preview, run, card, 'shop');
          choices.appendChild(card);
        }
        screen.appendChild(choices);
        button('상점을 떠난다', () => safeAct('shop', {offer_id: 'leave'}));
      } else {
        screen.appendChild(make('p', (run.node.danger || '일반') + ' 전투 · 현재 파티의 HP·PP·상태가 이어집니다.'));
      }
    } else if (run.phase === 'reward') {
      screen.appendChild(make('h3', '보상 하나를 선택하세요'));
      const choices = make('div', undefined, 'rogue-grid');
      for (const reward of run.rewards || []) {
        const card = make('article', undefined, 'rogue-card');
        button((reward.name || reward.label || reward.id) + (reward.description ? ' — ' + reward.description : ''), () => safeAct('reward', {reward_id: reward.id}), card);
        renderRecoveryPreview(reward.preview, run, card, 'reward');
        choices.appendChild(card);
      }
      screen.appendChild(choices);
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
      screen.appendChild(make('p', '후보의 기술·특성·도구와 현재 파티를 비교한 뒤 교체할 동료를 선택하세요. 선택한 후보가 그 동료의 파티 슬롯을 대신합니다.'));
      renderRecruitComparison(run);
      const choices = make('div', undefined, 'rogue-grid');
      for (const candidate of run.recruit_candidates || []) {
        const build = candidate.team || candidate.build || candidate;
        const row = make('article', undefined, 'rogue-card rogue-recruit-card');
        row.appendChild(make('strong', koSpecies(build.species || candidate.name || candidate.id)));
        renderBuildInfo(build, candidate.build_info, row, true);
        (run.party || []).forEach((p, slot) => button(koSpecies(p.species) + ' 대신 영입', () => safeAct('recruit', {candidate_id: candidate.id, replace_index: slot}), row));
        choices.appendChild(row);
      }
      screen.appendChild(choices);
      button('현재 파티 유지', () => safeAct('recruit', {candidate_id: 'skip'}));
    } else if (run.phase === 'complete' || run.phase === 'failed') {
      screen.appendChild(make('h3', run.phase === 'complete' ? '🏆 탐험 완주!' : '이번 탐험이 끝났습니다.'));
      screen.appendChild(make('p', '완료한 노드 ' + run.completed_nodes + '/' + run.total_nodes));
      button('새 탐험 준비', () => { state = null; showStart(); });
    }
    renderJournal(run);
    screen.appendChild(make('p', '진행 상황은 서버에 자동 저장됩니다.'));
    storageNotice();
    button('저장하고 메뉴로', leaveToMenu);
  }
  async function start(team) {
    return beginCreation({team, difficulty: selectedDifficulty});
  }
  async function beginCreation(requestedPayload, fresh = false) {
    if (busy || !active || !showingStart) return;
    const payload = JSON.parse(JSON.stringify(requestedPayload));
    const existing = !fresh && pendingRequests().find(p => payloadKey(p.payload) === payloadKey(payload));
    if (existing) return submitPending(existing);
    if (!catalog || catalogError) {
      notice(catalogError || '새 탐험을 시작하기 전에 시작 정보를 다시 불러오세요.');
      return;
    }
    const saved = history();
    const activeRuns = saved.filter(p => !finished(p)).length;
    if (activeRuns >= MAX_HISTORY) {
      notice('진행 중인 탐험이 ' + activeRuns + '개입니다. 이어할 탐험을 먼저 선택하거나, 필요 없는 접속 정보를 목록에서 정리한 뒤 새 탐험을 시작하세요.');
      return;
    }
    try { return await submitPending(createPending(payload)); }
    catch (error) { notice(error.message); }
  }
  async function submitPending(pending) {
    if (busy || !active || !showingStart) return;
    const ticket = ++epoch;
    busy = true;
    const previousButtons = [...screen.querySelectorAll('button')].map(b => [b, b.disabled]);
    previousButtons.forEach(([b]) => { b.disabled = true; });
    renderPending();
    try {
      const run = await request('/api/runs', {...pending.payload, creation_id: pending.creation_id, creation_secret: pending.creation_secret});
      if (!validCreatedRun(run)) throw new Error('탐험 시작 응답을 확인하지 못했습니다. 같은 탐험의 시작 확인을 다시 눌러 주세요.');
      const durable = remember(run);
      if (durable) forgetPending(pending);
      if (active && epoch === ticket) {
        busy = false;
        if (removedRuns.has(run.run_id)) notice('이 기기의 목록에서 제거한 탐험입니다. 접속 정보를 다시 등록하지 않았습니다.');
        else { state = run; render(run); }
      }
    } catch (error) { if (active && epoch === ticket) notice(error.message); }
    finally {
      busy = false; previousButtons.forEach(([b, disabled]) => { if (b.isConnected) b.disabled = disabled; });
      if (active && showingStart && epoch === ticket) renderPending();
    }
  }
  async function resume(runId) {
    const saved = credentials(runId);
    if (!saved || busy) return;
    if (!screen) mount();
    const ticket = ++epoch;
    showingStart = false;
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
        if (catalog && !catalogError) showStart();
        else showRecovery();
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
    screen.appendChild(make('p', '이 브라우저의 이어하기 목록입니다. 새 탐험을 시작해도 진행 중인 탐험은 유지됩니다. 새 탐험은 진행 중인 기록이 10개 미만일 때 시작할 수 있습니다.'));
    if (saved.length > MAX_HISTORY) screen.appendChild(make('p', '여러 탭에서 만든 탐험을 모두 보관했습니다. 이어갈 탐험을 선택하거나 불필요한 접속 정보를 정리하세요.'));
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
        button('목록에서 제거 확인', () => { forgetRun(entry.run_id); showStart(); }, confirmation);
        button('취소', () => { confirmation.replaceChildren(); remove.disabled = false; }, confirmation);
        remove.disabled = true;
      }, row);
      row.appendChild(confirmation); screen.appendChild(row);
    });
  }
  function showRecovery(focus = false) {
    showingStart = true; selected = [];
    releaseBattle(); showScreen(); screen.replaceChildren();
    const back = modeReturn();
    screen.appendChild(make('h2', '저장한 탐험 불러오기'));
    if (catalogError) notice(catalogError);
    screen.appendChild(make('p', '시작 정보를 불러오지 못해도 저장한 탐험과 시작 확인 정보는 유지됩니다. 이어갈 탐험을 선택하거나 시작 정보를 다시 불러오세요.'));
    const pending = make('div'); pending.id = 'roguePendingCreations'; screen.appendChild(pending); renderPending();
    renderHistory(); storageNotice();
    button('시작 정보 다시 불러오기', open);
    button('메뉴로', leaveToMenu);
    if (focus) back.focus?.();
  }
  function showStart(focus = false) {
    if (!catalog || catalogError) { showRecovery(focus); return; }
    showingStart = true;
    releaseBattle();
    showScreen(); screen.replaceChildren(); selected = [];
    const back = modeReturn();
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
    const pending = make('div'); pending.id = 'roguePendingCreations'; screen.appendChild(pending); renderPending();
    renderHistory(); storageNotice();
    const activeRuns = history().filter(p => !finished(p)).length, full = activeRuns >= MAX_HISTORY;
    if (full) screen.appendChild(make('p', '진행 중인 탐험이 ' + activeRuns + '개입니다. 기존 탐험을 이어하거나 목록을 정리한 뒤 새 탐험을 시작하세요.'));
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
    const grid = make('div', undefined, 'rogue-starter-grid');
    const startButton = make('button', '선택한 3마리로 시작', 'result-btn'); startButton.disabled = true;
    pool.forEach((pokemon, index) => {
      const card = make('article', undefined, 'rogue-card rogue-starter-card');
      card.setAttribute('aria-label', koSpecies(pokemon.species) + ' · 시작 빌드');
      const b = button(koSpecies(pokemon.species), () => {
        if (selected.includes(index)) selected = selected.filter(i => i !== index);
        else if (selected.length < 3) selected.push(index);
        b.style.outline = selected.includes(index) ? '2px solid #22d3ee' : '';
        b.setAttribute('aria-pressed', selected.includes(index) ? 'true' : 'false');
        selectionSummary.textContent = selected.length ? '선택한 동료: ' + selected.map(i => koSpecies(pool[i].species)).join(' · ') : '아래에서 동료 3마리를 골라 주세요.';
        startButton.textContent = '선택한 파티로 시작 · ' + selected.length + '/3';
        startButton.disabled = selected.length !== 3 || busy || full;
      }, card);
      b.setAttribute('aria-pressed', 'false');
      renderBuildInfo(pokemon, catalog?.starter_details?.[normalize(pokemon.species)], card);
      grid.appendChild(card);
    });
    screen.appendChild(grid); screen.appendChild(startButton);
    startButton.onclick = () => start(selected.map(index => pool[index]));
    button('◀ 메뉴로', leaveToMenu);
    if (focus) back.focus?.();
  }
  async function open() {
    if (!screen) mount();
    if (busy) return;
    const ticket = ++epoch; busy = true; showingStart = false; showScreen(); screen.replaceChildren(); notice('탐험을 불러오는 중...');
    modeReturn().focus?.();
    try { const next = await request('/api/runs/status'); if (active && epoch === ticket) { catalog = next; catalogError = ''; busy = false; showStart(true); } }
    catch (error) { if (active && epoch === ticket) { catalogError = '탐험 시작 정보를 불러오지 못했습니다. ' + error.message; busy = false; showRecovery(true); } }
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
    if (hub) {
      let b = document.getElementById('rogueStartButton');
      if (!b) {
        b = make('button', undefined, 'hub-card rogue'); b.type = 'button'; b.id = 'rogueStartButton';
        b.appendChild(make('span', '🌿', 'hub-card-icon'));
        b.appendChild(make('span', '로그라이크 탐험', 'hub-card-name'));
        b.appendChild(make('span', '3마리와 20구간 · 보상과 분기로 이어지는 탐험', 'hub-card-desc'));
        const grid = hub.querySelector?.('.hub-grid') || hub;
        if (grid.prepend) grid.prepend(b); else grid.appendChild(b);
      }
      b.onclick = open;
    }
    const originalHide = window.hideAllScreens;
    window.hideAllScreens = function () {
      screen.style.display = 'none';
      if (!internalNavigation) { active = false; epoch++; releaseBattle(); }
      return originalHide.apply(this, arguments);
    };
    window.addEventListener('storage', event => {
      const pendingChanged = String(event.key).startsWith(PENDING_PREFIX);
      if (event.key !== null && event.key !== KEY && event.key !== HISTORY_KEY && !String(event.key).startsWith(RECORD_PREFIX) && !String(event.key).startsWith(REMOVED_PREFIX) && !pendingChanged) return;
      if (active && showingStart && !busy && pendingChanged) { renderPending(); return; }
      // Do not rebuild the starter controls while this tab is composing a
      // party. The next action still scans storage before its capacity check.
      if (active && showingStart && !busy && selected.length === 0) showStart();
    });
  }
  if (document.readyState === 'complete') mount();
  else window.addEventListener('load', mount);
  window.RunUI = {open, resume, render, get state() { return state; }};
}());
