'use strict';

const readline = require('node:readline');
const {BattleEngine, EngineError} = require('./engine.cjs');
const engine = new BattleEngine();
const lines = readline.createInterface({input: process.stdin, crlfDelay: Infinity});

async function dispatch(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) throw new EngineError('Request must be an object');
  switch (request.op) {
    case 'ping': return engine.info();
    case 'create': return engine.create(request);
    case 'choose': return engine.choose(request.battle_id, request.side, request.action);
    case 'view': return engine.view(request.battle_id, request.side);
    case 'resources': return engine.resources(request.battle_id);
    case 'checkpoint': return engine.checkpoint(request.battle_id);
    case 'restore': return engine.restore(request.checkpoint);
    case 'replay': return engine.restore(request.replay);
    case 'close_battle': return engine.close(request.battle_id);
    default: throw new EngineError('Unknown operation');
  }
}

(async () => {
  for await (const line of lines) {
    let request;
    try {
      if (Buffer.byteLength(line, 'utf8') > 2 * 1024 * 1024) throw new EngineError('Request exceeds 2 MiB');
      request = JSON.parse(line);
      const result = await dispatch(request);
      process.stdout.write(JSON.stringify({id: request.id, ok: true, result}) + '\n');
    } catch (error) {
      process.stdout.write(JSON.stringify({id: request?.id ?? null, ok: false, error: {code: error.code || 'ENGINE_ERROR', message: error.message}}) + '\n');
    }
  }
  for (const id of [...engine.battles.keys()]) engine.close(id);
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
