"""Read the existing quota-axi Codex usage endpoint without starting a helper.

Only the current Codex auth.json is eligible. Credentials remain in memory;
there is no credential refresh, account discovery, redirect or alternate URL.
The snake_case schema follows quota-axi's providers/codex.js. Unsupported
shapes return None so the caller can retain the established read-only RPC.
"""
from __future__ import annotations

import base64
import contextlib
from dataclasses import dataclass, field
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import signal
import ssl
import threading
import time

HOST = 'chatgpt.com'
USAGE_PATH = '/backend-api/wham/usage'
HTTP_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_AUTH_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Credential:
    access_token: str = field(repr=False)
    account_id: str | None = field(repr=False)
    path: Path = field(repr=False)
    revision: str


@dataclass(frozen=True)
class Reading:
    response: dict
    timestamp: float
    revision: str
    credit_signature: tuple[int | None, int | None]
    auth_path: Path = field(repr=False)
    account_id: str | None = field(default=None, repr=False)


def _revision(path):
    stat = path.stat()
    # Bind the bank cache to this auth-file revision, without persisting an
    # account ID, credential, credential hash or the path itself.
    value = (str(path.absolute()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return hashlib.sha256(repr(value).encode()).hexdigest()


def same_revision(reading):
    try:
        return _revision(reading.auth_path) == reading.revision
    except OSError:
        return False


def _header(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and all(33 <= ord(character) <= 126 for character in value))


def _jwt_account(token):
    if not isinstance(token, str):
        return None
    try:
        part = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        if isinstance(claims, dict):
            return claims.get('https://api.openai.com/auth/account_id') or claims.get('account_id')
    except (ValueError, TypeError, IndexError):
        pass
    return None


def _credentials():
    path = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'auth.json'
    try:
        revision = _revision(path)
        with path.open('rb') as stream:
            raw = stream.read(MAX_AUTH_BYTES + 1)
        if len(raw) > MAX_AUTH_BYTES or revision != _revision(path):
            return None
        payload = json.loads(raw)
        if isinstance(payload, dict) and 'auth_mode' in payload and payload['auth_mode'] != 'chatgpt':
            return None  # Stale OAuth tokens must not override an explicit API-key mode.
        tokens = payload.get('tokens') if isinstance(payload, dict) else None
        if not isinstance(tokens, dict):
            return None
        access = tokens.get('access_token') or tokens.get('accessToken')
        account = tokens.get('account_id') or tokens.get('accountId')
        if account is None:
            account = (_jwt_account(tokens.get('id_token') or tokens.get('idToken'))
                       or _jwt_account(access))
        if not _header(access, 16384) or (account is not None and not _header(account, 200)):
            return None
        return Credential(access, account, path, revision)
    except (OSError, ValueError, TypeError):
        return None


def _expired(signum, frame):
    raise TimeoutError('Quota request timed out')


@contextlib.contextmanager
def _http_deadline():
    # Socket timeouts alone allow a slow response to reset the deadline on
    # every read. The disposable POSIX worker owns this otherwise-unused timer.
    previous = None
    if (hasattr(signal, 'setitimer') and threading.current_thread() is threading.main_thread()
            and signal.getitimer(signal.ITIMER_REAL) == (0., 0.)):
        previous = signal.signal(signal.SIGALRM, _expired)
        signal.setitimer(signal.ITIMER_REAL, HTTP_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        if previous is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def _fetch(credential):
    # Constructing this context explicitly avoids SSLKEYLOGFILE's optional
    # logging side effect in create_default_context(). Certificate verification
    # and hostname verification are mandatory with PROTOCOL_TLS_CLIENT.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    headers = {'Authorization': 'Bearer ' + credential.access_token, 'Accept': 'application/json'}
    if credential.account_id:
        headers['ChatGPT-Account-Id'] = credential.account_id
    connection = http.client.HTTPSConnection(HOST, timeout=HTTP_TIMEOUT_SECONDS, context=context)
    try:
        with _http_deadline():
            connection.request('GET', USAGE_PATH, headers=headers)
            response = connection.getresponse()
            # HTTPSConnection never follows redirects, consults proxy/netrc
            # credentials, accepts cookies, or retries authentication.
            if response.status != 200:
                return None
            size = response.getheader('Content-Length')
            if size is not None and (not size.isdecimal() or int(size) > MAX_RESPONSE_BYTES):
                return None
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                return None
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return None
            return json.loads(raw)
    finally:
        connection.close()


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Unsupported quota number')
    return value


def _window(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Unsupported quota window')
    percent = _number(value.get('used_percent'))
    if not 0 <= percent <= 100:
        raise ValueError('Unsupported quota percentage')
    duration = value.get('limit_window_seconds')
    if type(duration) is not int or duration <= 0 or duration % 60:
        raise ValueError('Unsupported quota duration')
    reset = value.get('reset_at')
    if reset is not None and _number(reset) < 0:
        raise ValueError('Unsupported reset timestamp')
    # Keep an absent absolute reset unknown, rather than inventing a date.
    return {'usedPercent': percent, 'windowDurationMins': duration // 60, 'resetsAt': reset}


def _pair(value, identity, name=None):
    if not isinstance(value, dict):
        raise ValueError('Unsupported rate limits')
    return {'limitId': identity, 'limitName': name,
            'primary': _window(value.get('primary_window')),
            'secondary': _window(value.get('secondary_window'))}


def normalize(payload):
    """Translate verified window fields only; HTTP credits never invent dates."""
    if not isinstance(payload, dict):
        raise ValueError('Unsupported usage response')
    # These optional concepts do not have a proven equivalent in this worker's
    # RPC projection. Keep the RPC path instead of silently dropping them.
    spend = payload.get('spend_control')
    if (payload.get('code_review_rate_limit') is not None or
            (spend is not None and (not isinstance(spend, dict) or
                                   set(spend) - {'individual_limit', 'reached'} or
                                   spend.get('individual_limit') is not None or
                                   spend.get('reached') is not False))):
        raise ValueError('Additional quota schema requires RPC')
    base = _pair(payload.get('rate_limit'), 'codex')
    additional = payload.get('additional_rate_limits')
    if additional is None:
        additional = []
    if not isinstance(additional, list):
        raise ValueError('Unsupported named limits')
    named = {}
    for value in additional:
        if not isinstance(value, dict):
            raise ValueError('Unsupported named limit')
        identity = value.get('metered_feature') or value.get('limit_name')
        name = value.get('limit_name') or identity
        if not isinstance(identity, str) or not identity or identity == 'codex' or identity in named:
            raise ValueError('Unsupported limit identity')
        named[identity] = _pair(value.get('rate_limit'), identity, name)
    counts = payload.get('rate_limit_reset_credits')
    signature = (None, None)
    if counts is not None:
        if not isinstance(counts, dict):
            raise ValueError('Unsupported reset-credit schema')
        # Only use these count fields to invalidate an RPC expiry snapshot.
        # In particular, available_count is not assumed to be applicable_count.
        values = tuple(counts.get(key) for key in ('available_count', 'applicable_available_count'))
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            raise ValueError('Unsupported reset-credit count')
        if set(counts) - {'available_count', 'applicable_available_count'}:
            raise ValueError('New reset-credit schema requires RPC')
        signature = values
    return {'result': {'rateLimits': base, 'rateLimitsByLimitId': named}}, signature


def read_usage():
    credential = _credentials()
    if credential is None:
        return None
    try:
        payload = _fetch(credential)
        response, signature = normalize(payload)
        reading = Reading(response, time.time(), credential.revision, signature, credential.path, credential.account_id)
        return reading if same_revision(reading) else None
    except (OSError, ValueError, TypeError, OverflowError, http.client.HTTPException):
        # No exception text may contain a credential, HTTP body or account data.
        return None
