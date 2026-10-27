"""HTTP routes for the persistent roguelike service."""
import asyncio

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from services.engine_api import _json
from services.run_service import ROOT, RunService, RunStaleError


def install_run_routes(app, service=None):
    service = service or RunService()
    app.state.run_service = service

    def token_for(request, body=None):
        authorization = request.headers.get('authorization', '')
        if authorization.startswith('Bearer '):
            return authorization[7:]
        return (body or {}).get('token', '')

    async def invoke(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except RunStaleError as exc:
            raise HTTPException(409, {'code': exc.code, 'message': str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            status = 503 if getattr(exc, 'code', '').startswith(('NODE', 'WORKER', 'SAVE_')) else 400
            raise HTTPException(status, str(exc)) from exc

    @app.get('/api/runs/status')
    async def run_status():
        return {'content_version': service.content['content_version'], 'total_nodes': len(service.content['nodes']),
                'storage': await asyncio.to_thread(service.storage_status),
                'route_guide': service.route_guide(),
                'default_difficulty': 'normal',
                'difficulty_options': [
                    {'id': 'normal', 'label': '기본', 'description': '상대가 위력 우선 AI로 행동합니다.'},
                    {'id': 'hard', 'label': '전략', 'description': '정예·보스가 상성과 교체를 고려하는 AI로 행동합니다.'}],
                'pool': [p['species'] for p in service.content['recruit_pool']],
                'starter_pool': service.content['recruit_pool'],
                'starter_team': [service.pool[key] for key in ('charizard', 'garchomp', 'clefable')],
                'species_ids': service.species_ids,
                'themes': len(service.content['themes']), 'bosses': len(service.content['bosses'])}

    @app.get('/web/rogue-client.js')
    async def rogue_script():
        return FileResponse(ROOT / 'web/rogue-client.js', media_type='application/javascript')

    @app.post('/api/runs')
    async def create_run(request: Request):
        body = await _json(request)
        return await invoke(service.create, body.get('team'), body.get('seed'), body.get('difficulty', 'normal'))

    @app.get('/api/runs/{run_id}')
    async def get_run(run_id: str, request: Request):
        return await invoke(service.get, run_id, token_for(request))

    @app.post('/api/runs/{run_id}/{operation:path}')
    async def run_operation(run_id: str, operation: str, request: Request):
        body = await _json(request)
        args = (run_id, token_for(request, body))
        operations = {
            'resume': (service.resume, ()),
            'battle/start': (service.battle_start, ()),
            'battle/action': (service.battle_action, (body.get('action'), body.get('revision'), body.get('command_id'))),
            'battle/finish': (service.battle_finish, ()),
            'reward': (service.choose_reward, (body.get('reward_id'),)),
            'reroll': (service.reroll_rewards, ()),
            'path': (service.choose_path, (body.get('path_id'),)),
            'rest': (service.rest, (body.get('option'),)),
            'shop': (service.shop, (body.get('offer_id'),)),
            'recruit': (service.recruit, (body.get('candidate_id'), body.get('replace_index', 0))),
            'party/order': (service.party_order, (body.get('order'),)),
        }
        if operation not in operations:
            raise HTTPException(404, '지원하지 않는 런 작업입니다.')
        fn, extra = operations[operation]
        if operation == 'resume':
            return await invoke(fn, *args)
        return await invoke(service.perform, *args, operation, body.get('run_revision'), *extra)

    @app.on_event('shutdown')
    async def close_runs():
        await asyncio.to_thread(service.close)

    return service
