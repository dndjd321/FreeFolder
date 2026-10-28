"""Stable credentials and request fingerprints for recoverable run creation.

The browser retains both creation values before sending its first request.
Only the derived run ID, token hash and request hash belong in the database.
The access token can be recovered from the retained secret after a lost HTTP
response, a service restart, or restoration of the committed database snapshot.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

from services.engine_api import canonical_team


CREATION_VERSION = 1
_KEY = re.compile(r'[0-9a-f]{64}\Z')
_ID_DOMAIN = b'freefolder/run-creation-id/v1\0'
_TOKEN_DOMAIN = b'freefolder/run-access-token/v1\0'
_REQUEST_DOMAIN = b'freefolder/run-creation-request/v1\0'


def creation_credentials(creation_id, creation_secret):
    """Return stable run ID, access token and its database authentication hash.

    Each input must encode 32 cryptographically random bytes as lowercase hex.
    This helper requires both values; legacy calls without them are handled by
    the caller. Error messages deliberately contain no supplied credentials.

    The run ID depends only on the creation ID, making the existing primary key
    the uniqueness constraint. The token is scoped to that ID and requires the
    secret. A caller must authenticate its hash before returning an existing
    run, and compare the separately retained request hash before reusing it.
    """
    if any(not isinstance(value, str) or not _KEY.fullmatch(value)
           for value in (creation_id, creation_secret)):
        raise ValueError('탐험 생성 자격증명 형식을 확인하세요.')
    encoded_id = creation_id.encode('ascii')
    run_id = 'c1_' + hashlib.sha256(_ID_DOMAIN + encoded_id).hexdigest()
    digest = hmac.digest(bytes.fromhex(creation_secret), _TOKEN_DOMAIN + encoded_id, 'sha256')
    token = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return {'run_id': run_id, 'token': token,
            'token_hash': hashlib.sha256(token.encode('ascii')).hexdigest()}


def creation_request_hash(team, seed=None, difficulty='normal'):
    """Fingerprint the immutable creation request, before generating auto seed.

    Team defaults and ordering use the same canonicalization as RunService.
    An omitted/null seed remains a distinct automatic-seed sentinel; explicit
    seeds use the service's stored string representation. This fingerprint
    must be stored once, never reconstructed from a party modified by play.
    """
    if not isinstance(difficulty, str) or difficulty not in ('normal', 'hard'):
        raise ValueError('탐험 난이도는 기본 또는 전략 중에서 선택하세요.')
    normalized_team = canonical_team(team)
    if seed is not None and (not isinstance(seed, (str, int)) or isinstance(seed, bool)
                             or len(str(seed)) > 128):
        raise ValueError('시드 형식을 확인하세요.')
    payload = {'version': CREATION_VERSION, 'team': normalized_team,
               'difficulty': difficulty, 'seed': None if seed is None else str(seed)}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(_REQUEST_DOMAIN + encoded).hexdigest()
