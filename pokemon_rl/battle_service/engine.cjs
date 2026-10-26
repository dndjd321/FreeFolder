'use strict';

const crypto = require('node:crypto');
const {BattleStream, Dex, TeamValidator, toID} = require('pokemon-showdown');
const {extractChannelMessages} = require('pokemon-showdown/dist/sim/battle');
const ENGINE_VERSION = require('pokemon-showdown/package.json').version;
const FORMAT = 'gen9nationaldexag@@@Max Team Size = 3,Min Team Size = 3,Adjust Level = 50,Terastal Clause,Dynamax Clause,!Team Preview';
const RULESET_VERSION = 'freefolder-singles-v1';
const SCHEMA_VERSION = 1;
const SIDES = ['p1', 'p2'];
const clone = value => JSON.parse(JSON.stringify(value));

class EngineError extends Error {
  constructor(message, code = 'INVALID_REQUEST') { super(message); this.code = code; }
}
function requireObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new EngineError(`${label} must be an object`);
}
function integer(value, min, max, label) {
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new EngineError(`${label} must be an integer from ${min} to ${max}`);
  return value;
}
function text(value, max, label, fallback = '') {
  if (value === undefined) return fallback;
  if (typeof value !== 'string' || value.length > max || /[\r\n|\x00-\x1f]/.test(value)) throw new EngineError(`${label} is invalid`);
  return value;
}
function dexEntry(table, value, label, optional = false) {
  const raw = text(value, 80, label);
  if (!raw && optional) return '';
  const entry = table.get(raw);
  if (!entry.exists || entry.isNonstandard === 'Custom') throw new EngineError(`Unknown ${label}: ${raw}`, 'INVALID_TEAM');
  return entry.name;
}
function validateTeams(input) {
  requireObject(input, 'teams');
  const result = {};
  for (const side of SIDES) {
    if (!Array.isArray(input[side]) || input[side].length !== 3) throw new EngineError('Each side needs exactly three Pokemon', 'INVALID_TEAM');
    const team = input[side].map((raw, slot) => {
      requireObject(raw, 'Pokemon');
      if (!Array.isArray(raw.moves) || raw.moves.length < 1 || raw.moves.length > 4) throw new EngineError('Pokemon need one to four moves', 'INVALID_TEAM');
      const species = dexEntry(Dex.species, raw.species, 'species');
      const set = {
        name: text(raw.name, 20, 'Pokemon nickname', species), species,
        ability: dexEntry(Dex.abilities, raw.ability, 'ability'),
        item: dexEntry(Dex.items, raw.item, 'item', true),
        nature: dexEntry(Dex.natures, raw.nature || 'Serious', 'nature'),
        moves: raw.moves.map(move => dexEntry(Dex.moves, move, 'move')),
        level: 50, evs: {}, ivs: {},
      };
      if (raw.level !== undefined && raw.level !== 50) throw new EngineError('This format requires level 50', 'INVALID_TEAM');
      for (const stat of ['hp', 'atk', 'def', 'spa', 'spd', 'spe']) {
        set.evs[stat] = integer(raw.evs?.[stat] ?? 0, 0, 252, 'EV');
        set.ivs[stat] = integer(raw.ivs?.[stat] ?? 31, 0, 31, 'IV');
      }
      if (Object.values(set.evs).reduce((a, b) => a + b, 0) > 510) throw new EngineError('EV total exceeds 510', 'INVALID_TEAM');
      if (new Set(set.moves.map(toID)).size !== set.moves.length) throw new EngineError('Duplicate moves are invalid', 'INVALID_TEAM');
      if (raw.gender !== undefined) {
        if (!['M', 'F', 'N', ''].includes(raw.gender)) throw new EngineError('Invalid gender', 'INVALID_TEAM');
        set.gender = raw.gender;
      }
      return set;
    });
    // In pinned 0.11.11 validator.debug suppresses only advisory zero-EV and
    // level-50 warnings; learnset, ability, item and EV legality still run.
    // Do not enable debug on the battle: it would reveal exact opponent HP.
    const errors = new TeamValidator({...Dex.formats.get(FORMAT, true), debug: true}).validateTeam(team);
    if (errors) throw new EngineError(errors.join('; '), 'INVALID_TEAM');
    // Stable identifiers survive the engine reordering its party on switches.
    result[side] = team.map((set, slot) => ({...set, _adapterSlot: slot}));
  }
  return result;
}

/** Version-pinned boundary: resource hydration runs before switch-in events.
 * Showdown has no documented API for initial injured parties. Only durable
 * fields are set here; boosts, volatile status and active combat state reset.
 */
function hydrateResources(battle, initial) {
  if (initial == null) return;
  requireObject(initial, 'initial_state');
  for (const sideId of SIDES) {
    const states = initial[sideId];
    if (states === undefined) continue;
    if (!Array.isArray(states) || states.length !== 3) throw new EngineError('Resource state needs three slots');
    const side = battle[sideId];
    for (const pokemon of side.pokemon) {
      const state = states[pokemon.set._adapterSlot];
      if (state == null) continue;
      requireObject(state, 'resource');
      if (state.species && toID(state.species) !== toID(pokemon.set.species)) throw new EngineError('Resource species does not match team');
      if (state.maxhp !== undefined && state.maxhp !== pokemon.maxhp) throw new EngineError('Resource maxhp does not match canonical stats');
      pokemon.hp = integer(state.hp ?? pokemon.maxhp, 0, pokemon.maxhp, 'resource hp');
      pokemon.fainted = pokemon.hp === 0;
      const status = state.status ?? '';
      if (!['', 'brn', 'par', 'psn', 'tox', 'slp', 'frz'].includes(status)) throw new EngineError('Invalid resource status');
      pokemon.status = pokemon.fainted ? '' : status;
      pokemon.statusState = battle.initEffectState({id: pokemon.status, target: pokemon});
      if (status === 'slp') {
        pokemon.statusState.time = integer(state.sleepTurns ?? 2, 1, 4, 'sleep turns');
        pokemon.statusState.startTime = pokemon.statusState.time;
      }
      if (status === 'tox') pokemon.statusState.stage = 0;
      if (state.item !== undefined) {
        const item = dexEntry(Dex.items, state.item, 'resource item', true);
        if (item && toID(item) !== toID(pokemon.set.item)) throw new EngineError('Resource item differs from team build');
        pokemon.item = toID(item);
        pokemon.itemState = battle.initEffectState({id: pokemon.item, target: pokemon});
      }
      if (state.moves !== undefined) {
        if (!Array.isArray(state.moves) || state.moves.length !== pokemon.moveSlots.length) throw new EngineError('Resource moves differ from team');
        for (let i = 0; i < pokemon.moveSlots.length; i++) {
          const resourceMove = state.moves[i];
          const move = pokemon.moveSlots[i];
          if (toID(resourceMove.id) !== move.id) throw new EngineError('Resource move ID differs from team');
          const pp = integer(resourceMove.pp, 0, move.maxpp, 'resource pp');
          move.pp = pp;
          pokemon.baseMoveSlots[i].pp = pp;
        }
      }
    }
    side.pokemonLeft = side.pokemon.filter(pokemon => !pokemon.fainted).length;
    if (!side.pokemonLeft) throw new EngineError('A new battle requires a living Pokemon on each side');
    const living = side.pokemon.findIndex(pokemon => !pokemon.fainted);
    if (living > 0) [side.pokemon[0], side.pokemon[living]] = [side.pokemon[living], side.pokemon[0]];
    side.pokemon.forEach((pokemon, index) => { pokemon.position = index; });
  }
}

function moveInfo(battle, move, index) {
  const data = battle.dex.moves.get(move.id);
  return {index, id: move.id, name: data.name, move: data.name, pp: move.pp, maxpp: move.maxpp,
    type: data.type.toLowerCase(), basePower: data.basePower, power: data.basePower,
    accuracy: data.accuracy, category: data.category.toLowerCase(), priority: data.priority,
    disabled: !!move.disabled, target: data.target};
}
function pokemonInfo(battle, pokemon) {
  return {slot: pokemon.set._adapterSlot, position: pokemon.position, ident: pokemon.fullname,
    name: pokemon.name, species: pokemon.species.name, id: pokemon.species.id, num: pokemon.species.num,
    types: pokemon.getTypes().map(type => type.toLowerCase()), hp: pokemon.hp, maxhp: pokemon.maxhp,
    status: pokemon.status, fainted: pokemon.fainted, active: pokemon.isActive,
    item: pokemon.item, ability: pokemon.ability, stats: {...pokemon.storedStats}, boosts: {...pokemon.boosts},
    moves: pokemon.moveSlots.map((move, i) => moveInfo(battle, move, i))};
}
function sideRequest(battle, side) {
  if (battle.ended || !side.activeRequest || side.isChoiceDone()) return {wait: true};
  const request = clone(side.activeRequest);
  if (request.active) request.active.forEach(active => {
    if (!active) return;
    for (const field of ['canMegaEvo', 'canMegaEvoX', 'canMegaEvoY', 'canUltraBurst', 'canZMove', 'canDynamax', 'maxMoves', 'canTerastallize']) delete active[field];
    active.moves = active.moves.map((move, i) => ({...move, ...moveInfo(battle, move, i)}));
  });
  if (request.side?.pokemon) request.side.pokemon.forEach((p, index) => {
    p.slot = side.pokemon[index].set._adapterSlot;
    p.position = index;
  });
  return request;
}
function publicParty(lines, sideId) {
  const known = [];
  let activeName = null;
  for (const line of lines) {
    const fields = line.split('|');
    const tag = fields[1];
    if (['switch', 'drag', 'replace'].includes(tag) && fields[2]?.startsWith(sideId)) {
      const name = fields[2].replace(/^p[12][a-z]?:\s*/, '');
      const species = fields[3].split(',')[0];
      let entry = tag === 'replace' ? known.find(pokemon => pokemon.name === activeName) : known.find(pokemon => pokemon.name === name);
      if (!entry) { entry = {name, species, revealed: true, slot: known.length}; known.push(entry); }
      entry.name = name;
      entry.species = species;
      entry.condition = fields[4];
      activeName = name;
    } else if (['-damage', '-heal', '-status', '-curestatus', 'faint'].includes(tag) && fields[2]?.startsWith(sideId)) {
      const name = fields[2].replace(/^p[12][a-z]?:\s*/, '');
      const entry = known.find(pokemon => pokemon.name === name);
      if (!entry) continue;
      if (tag === 'faint') entry.condition = '0 fnt';
      else if (tag === '-damage' || tag === '-heal') entry.condition = fields[3];
      else if (tag === '-status') entry.condition = (entry.condition || '100/100').split(' ')[0] + ' ' + fields[3];
      else entry.condition = (entry.condition || '100/100').split(' ')[0];
    }
  }
  const party = known.map(entry => {
    const match = (entry.condition || '100/100').match(/^(\d+)(?:\/(\d+))?(?:\s+(\w+))?/);
    const species = Dex.species.get(entry.species);
    return {...entry, id: species.id, num: species.num, types: species.types.map(type => type.toLowerCase()),
      hp: Number(match?.[1] || 0), maxhp: Number(match?.[2] || 100),
      status: match?.[3] === 'fnt' ? '' : match?.[3] || '', fainted: match?.[3] === 'fnt', active: entry.name === activeName};
  });
  while (party.length < 3) party.push({slot: party.length, revealed: false, name: '???', species: null, hp: null, maxhp: null, active: false});
  return party;
}

class BattleEngine {
  constructor() { this.battles = new Map(); }
  info() { return {engine: 'pokemon-showdown', version: ENGINE_VERSION, ruleset: RULESET_VERSION, format: FORMAT, schema_version: SCHEMA_VERSION}; }
  async create(payload) {
    requireObject(payload, 'create');
    const teams = validateTeams(payload.teams);
    const seed = payload.seed ?? Array.from({length: 4}, () => crypto.randomInt(65536));
    if (!Array.isArray(seed) || seed.length !== 4) throw new EngineError('Seed needs four unsigned 16-bit integers');
    seed.forEach(value => integer(value, 0, 65535, 'seed'));
    const names = Object.fromEntries(SIDES.map(side => [side, text(payload.names?.[side], 30, 'player name', side)]));
    if (names.p1 === names.p2) throw new EngineError('Player names must be distinct');
    const stream = new BattleStream({noCatch: true, keepAlive: true});
    const messages = [];
    // The public stream's message transport is captured without creating an
    // unread queue. Side projections use Showdown's own channel splitter.
    stream.pushMessage = (type, data) => { if (type === 'sideupdate') messages.push(String(data)); };
    await stream.write('>start ' + JSON.stringify({formatid: FORMAT, seed, strictChoices: true}));
    const oldBegin = stream.battle.format.onBegin;
    stream.battle.format = {...stream.battle.format, onBegin() {
      oldBegin?.call(this);
      hydrateResources(this, payload.initial_state);
    }};
    for (const side of SIDES) await stream.write(`>player ${side} ` + JSON.stringify({name: names[side], team: teams[side]}));
    const id = crypto.randomUUID();
    const record = {id, stream, messages, actions: [], teams: clone(teams), seed: [...seed], names,
      initial_state: clone(payload.initial_state ?? null)};
    this.battles.set(id, record);
    return this.state(record);
  }
  get(id) {
    const record = this.battles.get(id);
    if (!record) throw new EngineError('Battle not found', 'BATTLE_NOT_FOUND');
    return record;
  }
  state(record) {
    const battle = record.stream.battle;
    const channels = extractChannelMessages(battle.log.join('\n'), [0, 1, 2]);
    // Wall-clock protocol markers are excluded from deterministic replays.
    const clean = lines => lines.filter(line => !line.startsWith('|t:|') && !line.startsWith('|timestamp|'));
    const events = {spectator: clean(channels[0]), p1: clean(channels[1]), p2: clean(channels[2])};
    const winner = battle.ended ? (battle.winner === record.names.p1 ? 'p1' : battle.winner === record.names.p2 ? 'p2' : null) : null;
    const requests = Object.fromEntries(SIDES.map(side => [side, sideRequest(battle, battle[side])]));
    const views = {};
    for (const side of SIDES) {
      views[side] = {battle_id: record.id, side, turn: battle.turn, ended: battle.ended, winner,
        own: battle[side].pokemon.map(pokemon => pokemonInfo(battle, pokemon)).sort((a, b) => a.slot - b.slot),
        opponent: publicParty(events[side], side === 'p1' ? 'p2' : 'p1'),
        request: requests[side], events: events[side]};
    }
    return {battle_id: record.id, turn: battle.turn, ended: battle.ended, winner, views, requests, events};
  }
  view(id, side) {
    if (!SIDES.includes(side)) throw new EngineError('Invalid side');
    return this.state(this.get(id)).views[side];
  }
  async choose(id, side, action) {
    if (!SIDES.includes(side)) throw new EngineError('Invalid side');
    requireObject(action, 'action');
    const record = this.get(id);
    const battle = record.stream.battle;
    if (battle.ended) throw new EngineError('Battle has ended', 'BATTLE_ENDED');
    const request = sideRequest(battle, battle[side]);
    if (request.wait) throw new EngineError('This side is not awaiting a choice', 'INVALID_ACTION');
    const index = integer(action.index, 0, action.type === 'switch' ? 2 : 3, 'action index');
    let choice;
    if (action.type === 'move') {
      const move = request.active?.[0]?.moves?.[index];
      if (!move || move.disabled || (move.pp !== undefined && move.pp <= 0 && move.id !== 'struggle')) throw new EngineError('Move is unavailable', 'INVALID_ACTION');
      choice = `move ${index + 1}`;
    } else if (action.type === 'switch') {
      const target = battle[side].pokemon.find(pokemon => pokemon.set._adapterSlot === index);
      if (!target || target.fainted || target.isActive || request.active?.[0]?.trapped) throw new EngineError('Switch is unavailable', 'INVALID_ACTION');
      choice = `switch ${target.position + 1}`;
    } else throw new EngineError('Only move and switch actions are supported', 'INVALID_ACTION');
    record.messages.length = 0;
    await record.stream.write(`>${side} ${choice}`);
    const error = record.messages.find(message => message.startsWith(`${side}\n|error|`));
    if (error) throw new EngineError(error.split('|error|')[1], 'INVALID_ACTION');
    record.actions.push({side, action: {type: action.type, index}});
    return this.state(record);
  }
  resources(id) {
    const record = this.get(id);
    const battle = record.stream.battle;
    return Object.fromEntries(SIDES.map(side => [side, battle[side].pokemon.map(pokemon => ({
      slot: pokemon.set._adapterSlot, species: pokemon.set.species, hp: pokemon.hp, maxhp: pokemon.maxhp,
      status: pokemon.status, sleepTurns: pokemon.statusState?.time ?? null, item: pokemon.item,
      moves: (pokemon.transformed ? pokemon.baseMoveSlots : pokemon.moveSlots).map(move => ({id: move.id, pp: move.pp, maxpp: move.maxpp})),
    })).sort((a, b) => a.slot - b.slot)]));
  }
  checkpoint(id) {
    const record = this.get(id);
    return {schema_version: SCHEMA_VERSION, engine_version: ENGINE_VERSION, ruleset: RULESET_VERSION,
      format: FORMAT, teams: clone(record.teams), names: clone(record.names), seed: [...record.seed],
      initial_state: clone(record.initial_state), actions: clone(record.actions)};
  }
  async restore(data) {
    requireObject(data, 'checkpoint');
    if (data.schema_version !== SCHEMA_VERSION || data.engine_version !== ENGINE_VERSION || data.ruleset !== RULESET_VERSION || data.format !== FORMAT) throw new EngineError('Checkpoint engine or ruleset version mismatch', 'VERSION_MISMATCH');
    if (!Array.isArray(data.actions) || data.actions.length > 5000) throw new EngineError('Checkpoint action list is invalid');
    const state = await this.create(data);
    try {
      for (const entry of data.actions) await this.choose(state.battle_id, entry.side, entry.action);
      return this.state(this.get(state.battle_id));
    } catch (error) { this.close(state.battle_id); throw error; }
  }
  close(id) {
    const record = this.get(id);
    record.stream.battle.destroy();
    this.battles.delete(id);
    return {closed: true};
  }
}

module.exports = {BattleEngine, EngineError, FORMAT, RULESET_VERSION, ENGINE_VERSION, hydrateResources};
