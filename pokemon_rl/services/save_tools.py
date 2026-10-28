"""Operator-only SQLite snapshots and restore into a new, stopped save location.

The inspector never instantiates RunService or changes the inspected database.
It checks SQLite integrity and saved-state metadata, not a full engine replay.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

from services.save_format import SAVE_VERSION
from services.run_journal import JOURNAL_VERSION, public_journal
from services.run_balance import SUPPORTED_PROFILES


ROOT = Path(__file__).resolve().parents[1]
_PHASES = {'node', 'battle', 'reward', 'path', 'recruit', 'complete', 'failed'}
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def default_db_path():
    """Use the same save location as the application, without opening it."""
    return Path(os.environ.get('FREEFOLDER_SAVE_DIR', ROOT / 'runtime')) / 'runs.sqlite3'


class DatabaseLease:
    """A process-lifetime advisory lease shared by the game and restore tool.

    The lock file is intentionally retained: removing it could create two
    distinct inodes and let a running owner and a new owner both hold a lock.
    SQLite online backups do not need the source database's lease.
    """

    def __init__(self, db_path):
        self.path = None if str(db_path) == ':memory:' else Path(db_path).expanduser().resolve()
        self._file = None

    def acquire(self):
        if self.path is None or self._file is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + '.lock')
        stream = lock_path.open('a+b')
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError('저장소가 이미 사용 중이거나 잠글 수 없습니다. 해당 게임 서버를 종료하세요.') from exc
        self._file = stream
        return self

    def close(self):
        stream, self._file = self._file, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.close()


def _versions():
    content = json.loads((ROOT / 'content/roguelike.json').read_text(encoding='utf-8'))
    lock = json.loads((ROOT / 'battle_service/package-lock.json').read_text(encoding='utf-8'))
    engine = (ROOT / 'battle_service/engine.cjs').read_text(encoding='utf-8')
    values = {}
    for key in ('FORMAT', 'RULESET_VERSION'):
        match = re.search(r"^const " + key + r" = '([^']*)';$", engine, re.MULTILINE)
        if not match:
            raise RuntimeError('현재 엔진 버전 계약을 읽을 수 없습니다.')
        values[key] = match.group(1)
    match = re.search(r'^const SCHEMA_VERSION = (\d+);$', engine, re.MULTILINE)
    if not match:
        raise RuntimeError('현재 엔진 저장 형식을 읽을 수 없습니다.')
    return {'schema': SAVE_VERSION, 'content': content['content_version'],
            'nodes': len(content['nodes']), 'engine_schema': int(match.group(1)),
            'engine': lock['packages']['node_modules/pokemon-showdown']['version'],
            'ruleset': values['RULESET_VERSION'], 'format': values['FORMAT']}


def _source_path(path):
    if str(path) == ':memory:':
        raise ValueError('메모리 저장소는 파일 백업·검증 대상으로 사용할 수 없습니다.')
    result = Path(path).expanduser().resolve(strict=True)
    if not result.is_file():
        raise ValueError('저장 파일을 선택하세요.')
    return result


def _read_only(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection


def _pairs(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError('중복 JSON 필드')
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError('유한하지 않은 JSON 숫자')


def _integer(value, low, high=None):
    return type(value) is int and value >= low and (high is None or value <= high)


def _validate_current_state(state, expected):
    required = {'run_id', 'seed', 'schema_version', 'content_version', 'phase', 'node_index', 'node',
                'team', 'resources', 'money', 'relics', 'rewards', 'paths', 'recruit_candidates',
                'completed_nodes', 'outcome', 'battle', 'commands', 'rerolls_left', 'reward_roll',
                'shop_bought', 'last_replay'}
    if not required <= state.keys():
        raise ValueError('현재 형식의 저장에 필수 필드가 없습니다.')
    if not isinstance(state['seed'], str) or len(state['seed']) > 128 or state['phase'] not in _PHASES:
        raise ValueError('런의 시드 또는 진행 단계가 올바르지 않습니다.')
    if 'run_revision' in state and not _integer(state['run_revision'], 0):
        raise ValueError('런의 입력 버전이 올바르지 않습니다.')
    if 'balance_profile' in state:
        profile = state['balance_profile']
        if (not isinstance(profile, str) or not 1 <= len(profile) <= 64
                or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', profile)):
            raise ValueError('탐험 밸런스 규칙의 형식이 올바르지 않습니다.')
    if 'creation' in state:
        creation = state['creation']
        if not isinstance(creation, dict) or not _integer(creation.get('version'), 1):
            raise ValueError('탐험 생성 복구 정보의 형식이 올바르지 않습니다.')
        if creation['version'] == 1:
            if (set(creation) != {'version', 'request_hash'}
                    or not isinstance(creation.get('request_hash'), str)
                    or not _HASH.fullmatch(creation['request_hash'])):
                raise ValueError('탐험 생성 복구 정보의 형식이 올바르지 않습니다.')
    if 'journal' in state:
        journal = state['journal']
        if not isinstance(journal, dict) or not _integer(journal.get('version'), 1):
            raise ValueError('탐험 기록의 형식이 올바르지 않습니다.')
        if journal['version'] == JOURNAL_VERSION:
            checked = public_journal(state)
            if not checked.pop('available', False) or checked != journal:
                raise ValueError('탐험 기록의 형식이 올바르지 않습니다.')
            if (journal['from_node'] >= expected['nodes']
                    or (journal['complete_history'] and journal['from_node'] != 0)):
                raise ValueError('탐험 기록의 범위가 올바르지 않습니다.')
            for event in journal['events']:
                if (event['node_index'] >= expected['nodes']
                        or any(point['node_index'] >= expected['nodes'] or len(point['party']) != 3
                               for point in (event['before'], event['after']))):
                    raise ValueError('탐험 기록의 구간 또는 파티 크기가 올바르지 않습니다.')
    if not _integer(state['node_index'], 0, expected['nodes'] - 1) or not _integer(state['completed_nodes'], 0, expected['nodes']):
        raise ValueError('런의 진행 위치가 올바르지 않습니다.')
    node = state['node']
    if not isinstance(node, dict) or node.get('index') != state['node_index'] or node.get('kind') not in {'battle', 'elite', 'boss', 'rest', 'shop'}:
        raise ValueError('현재 노드 정보가 올바르지 않습니다.')
    if not _integer(state['money'], 0) or not _integer(state['rerolls_left'], 0) or not _integer(state['reward_roll'], 0):
        raise ValueError('런의 재화 또는 보상 횟수가 올바르지 않습니다.')
    for key in ('relics', 'commands'):
        if not isinstance(state[key], dict):
            raise ValueError('런의 특전 또는 입력 기록이 올바르지 않습니다.')
    for key in ('team', 'resources', 'rewards', 'paths', 'recruit_candidates', 'shop_bought'):
        if not isinstance(state[key], list):
            raise ValueError('런의 파티 또는 선택 목록이 올바르지 않습니다.')
    if len(state['team']) != 3 or len(state['resources']) != 3:
        raise ValueError('저장된 파티 크기가 올바르지 않습니다.')
    for build in state['team']:
        if not isinstance(build, dict) or not isinstance(build.get('species'), str) or not isinstance(build.get('moves'), list):
            raise ValueError('저장된 파티 구성이 올바르지 않습니다.')
    for resource in state['resources']:
        if not isinstance(resource, dict) or not _integer(resource.get('maxhp'), 1) or not _integer(resource.get('hp'), 0, resource['maxhp']):
            raise ValueError('저장된 파티 HP가 올바르지 않습니다.')
        if not isinstance(resource.get('moves'), list) or not isinstance(resource.get('item'), str):
            raise ValueError('저장된 기술 또는 도구가 올바르지 않습니다.')
        for move in resource['moves']:
            if not isinstance(move, dict) or not isinstance(move.get('id'), str) or not _integer(move.get('maxpp'), 0) or not _integer(move.get('pp'), 0, move['maxpp']):
                raise ValueError('저장된 기술 PP가 올바르지 않습니다.')
    if state['phase'] == 'battle':
        battle = state['battle']
        if not isinstance(battle, dict) or not _integer(battle.get('revision'), 0) or not isinstance(battle.get('checkpoint'), dict):
            raise ValueError('진행 중인 전투의 저장 정보가 올바르지 않습니다.')
    elif state['battle'] is not None:
        raise ValueError('진행 단계와 전투 저장 정보가 다릅니다.')
    if state['last_replay'] is not None and not isinstance(state['last_replay'], dict):
        raise ValueError('이전 전투의 저장 정보가 올바르지 않습니다.')


def _inspect_connection(connection):
    expected = _versions()
    integrity = list(connection.execute('PRAGMA integrity_check'))
    if integrity != [('ok',)]:
        raise ValueError('SQLite 무결성 검사에 실패했습니다. 원본은 보존되었습니다.')
    schema = list(connection.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    if ('runs', 'table') not in schema:
        raise ValueError('게임 런 저장 테이블이 없는 파일입니다.')
    columns = list(connection.execute('PRAGMA table_info(runs)'))
    if [(row[1], row[2].upper()) for row in columns] != [('id', 'TEXT'), ('token_hash', 'TEXT'), ('state', 'TEXT'), ('updated', 'REAL')]:
        raise ValueError('게임 런 테이블 스키마가 올바르지 않습니다.')
    if [row[5] for row in columns] != [1, 0, 0, 0]:
        raise ValueError('게임 런 테이블의 기본 키가 올바르지 않습니다.')
    if [row[3] for row in columns] != [0, 1, 1, 1]:
        raise ValueError('게임 런 테이블의 필수 값 제약이 올바르지 않습니다.')
    reasons = set()
    if any(name != 'runs' or kind != 'table' for name, kind in schema):
        reasons.add('현재 버전이 지원하지 않는 추가 테이블·뷰·트리거가 있습니다.')
    schemas, contents, engines = set(), set(), set()
    run_count = 0
    for run_id, token_hash, encoded, updated in connection.execute('SELECT id,token_hash,state,updated FROM runs'):
        run_count += 1
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128 or not isinstance(token_hash, str) or not _HASH.fullmatch(token_hash):
            raise ValueError('저장 행의 식별자 또는 인증 해시가 올바르지 않습니다.')
        if type(updated) not in (int, float) or not math.isfinite(updated) or updated < 0 or not isinstance(encoded, str):
            raise ValueError('저장 행의 수정 시각 또는 데이터 형식이 올바르지 않습니다.')
        try:
            state = json.loads(encoded, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
        except (ValueError, RecursionError) as exc:
            raise ValueError('저장 행에 올바르지 않은 JSON 데이터가 있습니다.') from exc
        if not isinstance(state, dict) or ('run_id' in state and state['run_id'] != run_id):
            raise ValueError('저장 행과 런 식별자가 일치하지 않습니다.')
        schema_version, content_version = state.get('schema_version'), state.get('content_version')
        if type(schema_version) is int:
            schemas.add(schema_version)
        if isinstance(content_version, str):
            contents.add(content_version)
        current = type(schema_version) is int and schema_version == expected['schema'] and content_version == expected['content']
        if type(schema_version) is not int or schema_version != expected['schema']:
            reasons.add('현재 버전과 다른 런 저장 형식이 있습니다.')
        if content_version != expected['content']:
            reasons.add('현재 버전과 다른 런 콘텐츠가 있습니다.')
        if current:
            _validate_current_state(state, expected)
            if 'creation' in state and state['creation']['version'] != 1:
                reasons.add('현재 버전이 지원하지 않는 탐험 생성 복구 정보가 있습니다.')
            if 'journal' in state and state['journal']['version'] != JOURNAL_VERSION:
                reasons.add('현재 버전이 지원하지 않는 탐험 기록이 있습니다.')
            if 'balance_profile' in state and state['balance_profile'] not in SUPPORTED_PROFILES:
                reasons.add('현재 버전이 지원하지 않는 탐험 밸런스 규칙이 있습니다.')
            if state.get('difficulty', 'normal') not in ('normal', 'hard'):
                reasons.add('현재 버전이 지원하지 않는 탐험 난이도가 있습니다.')
            if isinstance(state.get('battle'), dict) and state['battle'].get('ai_policy', 'maxpower') not in ('maxpower', 'heuristic-v2'):
                reasons.add('현재 버전이 지원하지 않는 전투 AI 정책이 있습니다.')
        checkpoints = []
        if isinstance(state.get('battle'), dict) and isinstance(state['battle'].get('checkpoint'), dict):
            checkpoints.append(state['battle']['checkpoint'])
        if isinstance(state.get('last_replay'), dict):
            checkpoints.append(state['last_replay'])
        for checkpoint in checkpoints:
            version = checkpoint.get('engine_version')
            if isinstance(version, str):
                engines.add(version)
            if (type(checkpoint.get('schema_version')) is not int or checkpoint.get('schema_version') != expected['engine_schema'] or version != expected['engine']
                    or checkpoint.get('ruleset') != expected['ruleset'] or checkpoint.get('format') != expected['format']):
                reasons.add('현재 버전과 다른 전투 엔진·규칙의 checkpoint가 있습니다.')
            elif not isinstance(checkpoint.get('actions'), list) or len(checkpoint['actions']) > 5000:
                raise ValueError('전투 checkpoint의 입력 기록이 올바르지 않습니다.')
    return {'valid': True, 'compatible': not reasons, 'reasons': sorted(reasons), 'run_count': run_count,
            'schema_versions': sorted(schemas), 'content_versions': sorted(contents),
            'checkpoint_engine_versions': sorted(engines), 'engine_restore_verified': False,
            'validation_scope': 'SQLite 무결성, 런 구조와 버전 메타데이터 검사입니다. 전투 엔진 전체 재생 검증은 포함하지 않습니다.'}


def inspect_save(source):
    """Read and validate a save without modifying it or starting a worker."""
    path = _source_path(source)
    try:
        with closing(_read_only(path)) as connection:
            return _inspect_connection(connection)
    except sqlite3.Error as exc:
        raise ValueError('SQLite 저장 파일을 읽거나 검증할 수 없습니다. 원본은 보존되었습니다.') from exc


def _paths(source, destination):
    source_path = _source_path(source)
    if str(destination) == ':memory:':
        raise ValueError('출력은 새 SQLite 파일 경로여야 합니다.')
    raw_destination = Path(destination).expanduser()
    if raw_destination.is_symlink():
        raise FileExistsError('출력 경로에 심볼릭 링크가 이미 있습니다. 다른 새 파일 경로를 선택하세요.')
    destination_path = raw_destination.resolve()
    if source_path == destination_path or (destination_path.exists() and os.path.samefile(source_path, destination_path)):
        raise ValueError('원본과 출력이 같은 파일입니다. 다른 경로를 선택하세요.')
    return source_path, destination_path


def _snapshot(source, destination, *, require_compatible):
    # A destination lease prevents a game process from opening this still-new
    # output between the existence check and the atomic publication.
    with DatabaseLease(destination):
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('출력 파일이 이미 있습니다. 기존 파일은 덮어쓰지 않습니다.')
        fd, name = tempfile.mkstemp(prefix='.' + destination.name + '.', suffix='.tmp', dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            deadline = time.monotonic() + 30
            def progress(_status, _remaining, _total):
                if time.monotonic() > deadline:
                    raise RuntimeError('백업 대기 시간이 초과되었습니다. 원본과 기존 출력은 보존되었습니다.')
            with closing(_read_only(source)) as reader, closing(sqlite3.connect(str(temporary))) as writer:
                reader.backup(writer, pages=256, progress=progress, sleep=0.05)
                writer.commit()
            summary = inspect_save(temporary)
            if require_compatible and not summary['compatible']:
                raise ValueError('현재 버전과 호환되지 않아 복원할 수 없습니다. inspect 결과의 reasons를 확인하세요.')
            with temporary.open('r+b') as stream:
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link creation is atomic and refuses an existing destination;
            # unlike rename/replace this cannot overwrite another backup on POSIX.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise FileExistsError('출력 파일이 이미 있습니다. 기존 파일은 덮어쓰지 않습니다.') from None
            except OSError as exc:
                raise RuntimeError('현재 파일시스템에서 덮어쓰기 없는 원자적 저장을 지원하지 않습니다. 다른 출력 위치를 선택하세요.') from exc
            return summary
        except sqlite3.Error as exc:
            raise ValueError('SQLite 백업을 만들 수 없습니다. 원본과 기존 출력은 보존되었습니다.') from exc
        finally:
            temporary.unlink(missing_ok=True)


def backup_save(source, destination):
    """Create a consistent online snapshot, including an older-version archive."""
    source, destination = _paths(source, destination)
    return {**_snapshot(source, destination, require_compatible=False), 'operation': 'backup'}


def restore_save(source, destination):
    """Restore a compatible snapshot into a new location with no running owner."""
    source, destination = _paths(source, destination)
    return {**_snapshot(source, destination, require_compatible=True), 'operation': 'restore'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='운영자용 런 SQLite 검증·온라인 백업·새 경로 복원. 인증정보와 private 상태는 출력하지 않습니다.')
    commands = parser.add_subparsers(dest='command', required=True)
    inspect_parser = commands.add_parser('inspect', help='무결성과 현재 버전 호환성 검사')
    inspect_parser.add_argument('source', nargs='?', default=None)
    for name in ('backup', 'restore'):
        command = commands.add_parser(name, help='새 파일로 ' + ('온라인 백업' if name == 'backup' else '서버가 정지된 저장 위치에 복원'))
        command.add_argument('source')
        command.add_argument('destination')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = inspect_save(args.source or default_db_path())
        else:
            result = (backup_save if args.command == 'backup' else restore_save)(args.source, args.destination)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print('저장 작업 실패: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
