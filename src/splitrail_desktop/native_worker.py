"""Disposable, Qt-free JSON worker for the native application.

Requests are {"task": name, "options": {...}}; responses contain either
"result" or a safe "error" string. Times are Unix seconds, costs are USD,
and percentages represent usage. No account records or raw diagnostics cross
this boundary. The native parent selects dates and aggregates these daily rows.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import preferences, runner, sync, worker
from .platform_support import prepare_desktop_path
from .quota import QuotaDataError, parse_banked_reset_response, parse_quota_json
from .quota_guard import QuotaLimits

QUOTA_TIMEOUT_SECONDS = 20
BANK_REFRESH_SECONDS = 3600
BANK_CACHE_FILE = 'native-quota-bank.json'
MAX_REQUEST_BYTES = 1024 * 1024
THEMES = ('Pearl', 'Nord')


class RequestError(ValueError):
    """Messages authored here, containing no untrusted input."""


def _options(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise RequestError('Invalid worker options.')


def _boolean(value):
    if type(value) is not bool:
        raise RequestError('Use true or false for this setting.')
    return value


def _collector_available():
    # Match DesktopController, including an explicitly configured collector.
    if os.environ.get('SPLITRAIL_BIN'):
        return True
    try:
        runner._find_executable('SPLITRAIL_BIN', 'splitrail', runner.SPLITRAIL_FALLBACK)
        return True
    except runner.MissingCommandError:
        return False


def _usage_mode(prefs):
    if not _collector_available():
        return 'codex'
    mode = prefs.get('usage_mode', 'combined')
    return mode if mode in ('combined', 'codex', 'local') else 'combined'


def _limits(value):
    if not isinstance(value, dict) or set(value) - {'current', 'after_reset'}:
        raise RequestError('Use current and after_reset quota limits.')
    try:
        limits = QuotaLimits(**value)
    except (TypeError, ValueError):
        raise RequestError('Enter quota percentages from 0 to 100.') from None
    return {'current': limits.current, 'after_reset': limits.after_reset}


def preference_summary(prefs=None):
    prefs = preferences.load() if prefs is None else prefs
    try:
        limits = _limits(prefs.get('quota_limits', {}))
    except RequestError:
        limits = _limits({})
    theme = prefs.get('theme', 'Pearl')
    return {'theme': theme if theme in THEMES else 'Pearl',
            'automatic_prices': prefs.get('automatic_prices', True) is not False,
            'quota_limits': limits, 'usage_mode': _usage_mode(prefs)}


def _epoch(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid timestamp')
    # Validate against datetime's supported range, without accepting milliseconds.
    datetime.fromtimestamp(value, timezone.utc)
    return float(value)


def _saved_timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        return _epoch(stamp.timestamp()) if stamp.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def settings_summary():
    config = sync.safe_settings()
    result = {'connected': bool(config), 'repository': '', 'automatic': False,
              'receiveOnly': False, 'scope': 'all' if _collector_available() else 'codex',
              'intervalHours': sync.DEFAULT_INTERVAL_HOURS, 'idleHours': sync.DEFAULT_IDLE_HOURS,
              'devices': 0, 'lastSync': None, 'nextSyncSeconds': None}
    if not config:
        return result
    result.update(repository=config['repository'], automatic=config['automatic'],
                  receiveOnly=config['receive_only'], scope=config['scope'],
                  intervalHours=config['interval_hours'], idleHours=config['idle_hours'], devices=1)
    try:
        state = sync._read(sync.data_dir() / sync.STATE_FILE)
        result['lastSync'] = _saved_timestamp(state.get('synced_at'))
        count = state.get('device_count')
        if type(count) is int and 1 <= count <= sync.MAX_DEVICES + 1:
            result['devices'] = count
        result['nextSyncSeconds'] = sync.seconds_until_auto_sync()
    except (sync.SyncError, ValueError, TypeError, OSError, OverflowError):
        # Damaged scheduling state must not hide otherwise valid local usage.
        pass
    return result


def project_usage(result, *, prefs=None):
    days = {}
    for row in result.dataset.days:
        key = row.day.isoformat()
        day = days.setdefault(key, {'day': key, 'cost': 0.0, 'tokens': 0,
                                    'conversations': 0, 'models': {}})
        day['cost'] += row.cost
        # This property subtracts reasoning already included in output and does
        # not double-count cache_read/cache_write, which are parts of cached.
        day['tokens'] += row.tokens.total
        day['conversations'] += row.conversations
        for detail in row.model_details:
            model = day['models'].setdefault(detail.name, {'name': detail.name, 'cost': 0.0, 'tokens': 0})
            model['cost'] += detail.cost
            model['tokens'] += detail.tokens.total
    for day in days.values():
        day['models'] = sorted(day['models'].values(), key=lambda model: model['name'])
    diagnostics = result.cost_diagnostics
    notices = []
    if diagnostics.unknown_models:
        notices.append(f'{len(diagnostics.unknown_models)} models need pricing. Add rates in Settings.')
    if diagnostics.fallback_session_count:
        notices.append('Some sessions used fallback model metadata.')
    if diagnostics.other_warning_count:
        notices.append('Some usage records could not be read completely; totals may be partial.')
    if result.exit_code:
        notices.append('The collector returned partial results.')
    return {'days': [days[key] for key in sorted(days)], 'timestamp': time.time(),
            'diagnostics': notices, 'sync': settings_summary(),
            'preferences': preference_summary(prefs)}


def usage():
    prefs = preference_summary()
    result = worker.execute('usage', {'mode': prefs['usage_mode'], 'include_hours': False,
                                      'automatic_prices': prefs['automatic_prices']})
    return project_usage(result, prefs=prefs)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}', value):
        raise ValueError('Invalid quota identifier')
    return value


def _label(value, default):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or not value.isprintable():
        return default
    return value


def _percent(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('Invalid quota percentage')
    return float(value)


def _direct_windows(result):
    snapshots = {}
    base = result.get('rateLimits')
    if base is not None:
        if not isinstance(base, dict):
            raise ValueError('Invalid rate limits')
        snapshots[_identifier(base.get('limitId') or 'codex')] = base
    named = result.get('rateLimitsByLimitId')
    if named is not None:
        if not isinstance(named, dict):
            raise ValueError('Invalid named rate limits')
        for key, snapshot in named.items():
            if not isinstance(snapshot, dict):
                raise ValueError('Invalid rate limits')
            # Prefer the current per-limit snapshot over its legacy duplicate.
            snapshots[_identifier(snapshot.get('limitId') or key)] = snapshot
    windows = []
    for limit_id, snapshot in sorted(snapshots.items()):
        for slot in ('primary', 'secondary'):
            window = snapshot.get(slot)
            if window is None:
                continue
            if not isinstance(window, dict):
                raise ValueError('Invalid quota window')
            duration = window.get('windowDurationMins')
            if duration is not None and (type(duration) is not int or duration <= 0):
                raise ValueError('Invalid quota duration')
            kind, label = {300: ('session', '5 hours'), 1440: ('daily', 'Daily'),
                           10080: ('weekly', 'Weekly')}.get(duration, ('unknown', slot.capitalize()))
            if limit_id != 'codex':
                kind = 'model'
                label = _label(snapshot.get('limitName'), limit_id) + ' · ' + label
            percent = _percent(window.get('usedPercent'))
            reset = _epoch(window['resetsAt']) if window.get('resetsAt') is not None else None
            if percent is None:
                # A reset time alone is not a usable quota reading.
                continue
            windows.append({'id': f'{limit_id}:{slot}', 'label': label, 'kind': kind,
                            'percent': percent, 'reset': reset})
    return windows


def parse_codex_quota(payload, *, timestamp=None):
    stamp = time.time() if timestamp is None else timestamp
    output = {'timestamp': stamp, 'status': 'unavailable', 'windows': [], 'expiries': []}
    if not isinstance(payload, dict) or 'error' in payload or not isinstance(payload.get('result'), dict):
        return output
    try:
        output['windows'] = _direct_windows(payload['result'])
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    good_credits = False
    try:
        credits = parse_banked_reset_response(payload, datetime.fromtimestamp(stamp, timezone.utc))
        good_credits = credits.status == 'fresh'
        if good_credits:
            # Never infer dates from availableCount or fill missing expiry dates.
            output['expiries'] = sorted(_epoch(value.timestamp()) for value in credits.expiries if value is not None)
    except (QuotaDataError, ValueError, TypeError, OverflowError, OSError):
        pass
    if output['windows'] or good_credits:
        output['status'] = 'fresh'
    return output


def _legacy_quota(timeout):
    # Do not call run_quota_axi: it launches a second Codex reset-credit helper.
    executable = runner._find_executable('QUOTA_AXI_BIN', 'quota-axi', runner.QUOTA_AXI_FALLBACK)
    completed = runner._run((executable, '--provider', 'codex', '--full', '--json'), timeout, 'quota-axi')
    if completed.returncode or len(completed.stdout.encode('utf-8')) > runner.MAX_OUTPUT_BYTES:
        return None
    snapshot = parse_quota_json(completed.stdout)
    if snapshot.status != 'fresh' or snapshot.stale or snapshot.refreshed_at is None:
        return None
    age = time.time() - snapshot.refreshed_at.timestamp()
    if not -5 <= age <= 120:
        return None
    windows = [{'id': _identifier(window.window_id), 'label': _label(window.label, 'Quota window'),
                'kind': _identifier(window.kind), 'percent': _percent(window.percent_used),
                'reset': _epoch(window.resets_at.timestamp()) if window.resets_at else None}
               for window in snapshot.windows if window.percent_used is not None]
    # Keep the source observation time: reading cached quota must not extend the
    # native cutoff watchdog's freshness deadline.
    return {'windows': windows, 'timestamp': snapshot.refreshed_at.timestamp()}


def _rpc_quota():
    deadline = time.monotonic() + QUOTA_TIMEOUT_SECONDS
    try:
        executable = runner._find_executable('CODEX_BIN', 'codex', runner.CODEX_FALLBACK)
        payload = runner._run_codex_reset_credit_read(executable, QUOTA_TIMEOUT_SECONDS)
    except (runner.LocalCommandError, OSError, ValueError, TimeoutError):
        return {**parse_codex_quota(None), 'expiryStatus': 'unavailable',
                'expiryTimestamp': None, 'expiryDetailsComplete': False}
    result = parse_codex_quota(payload)
    result.update(expiryStatus='unavailable', expiryTimestamp=None, expiryDetailsComplete=False)
    try:
        credits = parse_banked_reset_response(payload)
        if credits.status == 'fresh' and isinstance(payload, dict) and 'error' not in payload:
            result.update(expiryStatus='fresh', expiryTimestamp=result['timestamp'],
                          expiryDetailsComplete=credits.expiry_details_complete)
    except (QuotaDataError, ValueError, TypeError, OverflowError, OSError):
        pass
    body = payload.get('result') if isinstance(payload, dict) and 'error' not in payload else None
    # Explicit null/empty quota fields mean unavailable, not an old protocol.
    if isinstance(body, dict) and not {'rateLimits', 'rateLimitsByLimitId'} & body.keys():
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                legacy = _legacy_quota(remaining)
                if legacy and legacy['windows']:
                    result.update(legacy, status='fresh')
            except (runner.LocalCommandError, QuotaDataError, OSError, ValueError, TypeError, TimeoutError):
                pass
    return result


def _bank_cache(reading):
    from .portable import data_dir
    try:
        with (data_dir() / BANK_CACHE_FILE).open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return None
        cache = json.loads(raw)
        fields = {'revision', 'checkedAt', 'observedAt', 'weekly', 'credits', 'expiries', 'complete'}
        if not isinstance(cache, dict) or set(cache) != fields or cache['revision'] != reading.revision:
            return None
        if _epoch(cache['checkedAt']) > reading.timestamp + 5:
            return None
        if cache['observedAt'] is not None and _epoch(cache['observedAt']) > cache['checkedAt'] + 5:
            return None
        if not isinstance(cache['expiries'], list) or type(cache['complete']) is not bool:
            return None
        if cache['observedAt'] is None and cache['expiries']:
            return None
        for expiry in cache['expiries']:
            _epoch(expiry)
        if not isinstance(cache['weekly'], list) or not isinstance(cache['credits'], list) or len(cache['credits']) != 2:
            return None
        for value in cache['credits']:
            if value is not None and (type(value) is not int or value < 0):
                return None
        for window in cache['weekly']:
            if not isinstance(window, list) or len(window) != 2:
                return None
            _identifier(window[0])
            if window[1] is not None:
                _epoch(window[1])
        return cache
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _http_quota():
    from . import quota_http
    from .portable import data_dir, write_export
    reading = quota_http.read_usage()
    if reading is None:
        return None
    result = parse_codex_quota(reading.response, timestamp=reading.timestamp)
    if not result['windows']:
        return None
    cached = _bank_cache(reading)
    weekly = [[window['id'], window['reset']] for window in result['windows'] if window['kind'] == 'weekly']
    counts = list(reading.credit_signature)
    refresh = (cached is None or reading.timestamp - cached['checkedAt'] >= BANK_REFRESH_SECONDS
               or cached['weekly'] != weekly or cached['credits'] != counts)
    status = 'cached' if cached and cached['observedAt'] is not None else 'unavailable'
    bank = cached or {'observedAt': None, 'expiries': [], 'complete': False}
    if refresh:
        bank = {**bank, 'revision': reading.revision, 'checkedAt': reading.timestamp,
                'weekly': weekly, 'credits': counts}
        try:
            executable = runner._find_executable('CODEX_BIN', 'codex', runner.CODEX_FALLBACK)
            response = runner._run_codex_reset_credit_read(executable, QUOTA_TIMEOUT_SECONDS)
            if not isinstance(response, dict) or 'error' in response or not isinstance(response.get('result'), dict):
                raise ValueError('Invalid banked-reset read')
            body = response['result']
            if reading.account_id is not None and 'accountId' in body and body['accountId'] != reading.account_id:
                raise ValueError('Quota account changed')
            credits = parse_banked_reset_response(response)
            if credits.status == 'fresh':
                bank.update(observedAt=time.time(),
                            expiries=sorted(_epoch(value.timestamp()) for value in credits.expiries if value is not None),
                            complete=credits.expiry_details_complete)
                status = 'fresh'
        except (runner.LocalCommandError, QuotaDataError, OSError, ValueError, TypeError, OverflowError):
            pass
        # Do not attach an RPC read from an auth file changed during the HTTP
        # request. Failed reads retain prior authoritative dates and their age.
        if not quota_http.same_revision(reading):
            bank = {'observedAt': None, 'expiries': [], 'complete': False}
            status = 'unavailable'
        else:
            bank['checkedAt'] = max(bank['checkedAt'], bank['observedAt'] or 0)
            try:
                write_export(data_dir() / BANK_CACHE_FILE, bank)
            except (OSError, ValueError):
                pass
    result.update(expiries=bank['expiries'], expiryStatus=status, expiryTimestamp=bank['observedAt'],
                  expiryDetailsComplete=bank['complete'])
    return result


def quota():
    result = _http_quota()
    return result if result is not None else _rpc_quota()


def prices():
    from .catalog_updates import refresh_prices
    from .pricing import CATALOG, RATE_FIELDS, load_overrides, resolve_rates
    refresh_prices(allow_network=False)
    overrides = load_overrides()
    models = []
    for name in sorted(set(CATALOG['models']) | set(overrides)):
        rate = resolve_rates(name, overrides=overrides)
        models.append({'name': name, **{key: rate.get(key) if rate else None for key in RATE_FIELDS}})
    return {'verified': CATALOG['verified'],
            'automatic': preferences.load().get('automatic_prices', True) is not False, 'models': models}


def execute(task, options=None):
    options = {} if options is None else options
    if task == 'self-check':
        _options(options, ())
        from . import __version__
        from .pricing import CATALOG
        if any(name == 'PySide6' or name.startswith('PySide6.') for name in sys.modules):
            raise RequestError('The native worker loaded a Qt module.')
        return {'headless': True, 'version': __version__, 'pricingCount': len(CATALOG['models'])}
    if task in ('usage', 'quota', 'settings', 'disconnect', 'prices'):
        _options(options, ())
        if task == 'usage':
            return usage()
        if task == 'quota':
            return quota()
        if task == 'prices':
            return prices()
        if task == 'disconnect':
            sync.disconnect()
        return settings_summary()
    if task == 'sync':
        _options(options, ('automatic',))
        sync.sync_now(automatic=_boolean(options.get('automatic', False)))
        return usage()
    if task == 'configure':
        _options(options, ('repository', 'automatic', 'receiveOnly', 'scope', 'intervalHours', 'idleHours'))
        repository = options.get('repository')
        if not isinstance(repository, str):
            raise RequestError('Use owner/repository and select a usage source.')
        sync.configure(repository, automatic=_boolean(options.get('automatic', False)),
                       receive_only=_boolean(options.get('receiveOnly', False)),
                       scope=options.get('scope', 'all' if _collector_available() else 'codex'),
                       interval_hours=options.get('intervalHours'), idle_hours=options.get('idleHours'))
        return settings_summary()
    if task == 'preferences':
        _options(options, ('theme', 'automatic_prices', 'quota_limits', 'usage_mode'))
        changes = dict(options)
        if 'usage_mode' in changes:
            if changes['usage_mode'] not in ('codex', 'combined', 'local'):
                raise RequestError('Choose Codex, All devices or This device.')
            if changes['usage_mode'] != 'codex' and not _collector_available():
                raise RequestError('Install the Splitrail collector to track other tools. Codex tracking remains available.')
        if 'theme' in changes and changes['theme'] not in THEMES:
            raise RequestError('Choose Pearl or Nord.')
        if 'automatic_prices' in changes:
            _boolean(changes['automatic_prices'])
        if 'quota_limits' in changes:
            changes['quota_limits'] = _limits(changes['quota_limits'])
        if changes:
            preferences.save(**changes)
        return preference_summary()
    if task == 'price':
        from .pricing import RATE_FIELDS, save_override
        _options(options, ('model', 'rates'))
        if not isinstance(options.get('model'), str) or 'rates' not in options:
            raise RequestError('Enter a model ID.')
        rates = options['rates']
        if rates is not None:
            _options(rates, RATE_FIELDS)
            rates = {key: rates.get(key) for key in RATE_FIELDS}
            for key in ('cache_read', 'cache_write'):
                if rates[key] == '':
                    rates[key] = None
        save_override(options['model'], rates)
        return prices()
    if task in ('export', 'import'):
        _options(options, ('path',))
        if not isinstance(options.get('path'), str) or not options['path'] or '\0' in options['path']:
            raise RequestError('Choose a usage file.')
        result = worker.execute(task, {'path': options['path']})
        return usage() if task == 'import' else {'message': result}
    raise RequestError('Unknown worker task.')


def _safe_error(error, task):
    if isinstance(error, RequestError):
        return str(error)
    # Only exact, application-authored validation messages may be forwarded.
    # Prefix matching could leak a collector's appended stderr or private paths.
    safe = {
        'Use owner/repository and select a usage source.',
        'Use 1–168 hours; “If unchanged” must be at least “Every”.',
        'Disconnect the current repository before connecting another.',
        'Choose an active private repository. Public repositories cannot hold usage sync.',
        'This GitHub account needs write access to the private repository.',
        'Connect GitHub in Settings to enable sync.',
        'Install GitHub CLI, then choose Check connection.',
        'Sign in to GitHub again.',
        'GitHub denied access. Check repository permissions or try later.',
        'Repository not found. Check its name and your GitHub account.',
        'Another device updated the repository. Try Sync again.',
        'Could not reach GitHub. Check your connection and try again.',
        'Enter a model ID without spaces (for example, my-model-v1).',
    }
    safe.update(f'{field} must be a finite number from 0 to 1,000,000.'
                for field in ('Input', 'Output', 'Cache read', 'Cache write'))
    if str(error) in safe:
        return str(error)
    if isinstance(error, runner.MissingCommandError):
        return 'The usage collector was not found. Check its installation or use Codex usage.'
    return {'usage': 'Usage could not be refreshed. Check the collector and local file access.',
            'sync': 'Sync could not finish. Check the private repository, connection and local usage source.',
            'configure': 'Sync could not be configured. Check the private repository and GitHub connection.',
            'settings': 'Sync settings could not be read.',
            'preferences': 'Preferences could not be saved.',
            'prices': 'Model prices could not be read. Check custom pricing settings.',
            'price': 'Model prices could not be saved. Check the model and rates.',
            'export': 'Usage could not be exported. Check the destination and local file access.',
            'import': 'Usage could not be imported. Check the export file and local file access.',
            'disconnect': 'Sync could not be disconnected. Check local file access.',
            'quota': 'Quota could not be read. Check the Codex connection.'}.get(task, 'Invalid worker request.')


def _isolate_process():
    if os.name == 'posix' and os.getpgrp() != os.getpid():
        try:
            os.setsid()
        except OSError:
            pass  # A parent may already own the worker's session/group.


def _terminate(signum, frame):
    # SystemExit intentionally bypasses Exception handlers, unwinding the Codex
    # helper's finally block before the worker exits on parent cancellation.
    raise SystemExit(128 + signum)


def _invalid_constant(value):
    raise RequestError('Invalid JSON number.')


def main(request_path, response_path):
    _isolate_process()
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGTERM, _terminate)
    task = None
    temporary = None
    try:
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            try:
                path = Path(request_path)
                with path.open('rb') as stream:
                    raw = stream.read(MAX_REQUEST_BYTES + 1)
                if len(raw) > MAX_REQUEST_BYTES:
                    raise RequestError('Worker request is too large.')
                request = json.loads(raw, parse_constant=_invalid_constant)
                if not isinstance(request, dict) or set(request) - {'task', 'options'}:
                    raise RequestError('Invalid worker request.')
                task = request.get('task')
                if not isinstance(task, str) or not isinstance(request.get('options', {}), dict):
                    task = None
                    raise RequestError('Invalid worker request.')
                prepare_desktop_path()
                response = {'result': execute(task, request.get('options', {}))}
                encoded = json.dumps(response, allow_nan=False)
            except Exception as error:
                encoded = json.dumps({'error': _safe_error(error, task)}, allow_nan=False)
            destination = Path(response_path)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                             prefix='.native-response-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            os.replace(temporary, destination)
        return 0
    except OSError:
        # No response is possible if the parent removed/unwritable IPC directory.
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)


if __name__ == '__main__':
    raise SystemExit(main(*sys.argv[1:]) if len(sys.argv) == 3 else 2)
