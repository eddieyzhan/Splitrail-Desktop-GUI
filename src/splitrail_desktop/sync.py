"""Opt-in, private multi-device sync using the user's GitHub CLI credentials."""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .portable import data_dir, digest, write_export
from .platform_support import external_command_env, path_executable
from .sync_payload import decode_dataset, encode_dataset

SETTINGS_FILE = 'github-sync-v2.json'
CACHE_FILE = 'github-devices.json'
STATE_FILE = 'github-sync-state-v2.json'
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_DEVICES = 100
DEFAULT_INTERVAL_HOURS = 1
DEFAULT_IDLE_HOURS = 3
REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*')
DEVICE_PATH = re.compile(r'usage/([a-f0-9]{32})\.json')
_LOCK = threading.RLock()


class SyncError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_size > 100 * 1024 * 1024:
        raise SyncError('Local sync data is too large.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise SyncError('Invalid local sync data. Disconnect and set up sync again.')
    return value


def settings(directory: Path | None = None) -> dict | None:
    value = _read((directory or data_dir()) / SETTINGS_FILE)
    if not value:
        return None
    if (value.get('version') != 2 or not REPOSITORY.fullmatch(value.get('repository', '')) or
            not re.fullmatch(r'[a-f0-9]{32}', value.get('device', '')) or
            value.get('scope') not in ('all', 'codex') or type(value.get('automatic')) is not bool or
            type(value.get('receive_only')) is not bool):
        raise SyncError('Invalid sync settings. Disconnect and set up sync again.')
    value.setdefault('interval_hours', DEFAULT_INTERVAL_HOURS)
    value.setdefault('idle_hours', DEFAULT_IDLE_HOURS)
    _validate_intervals(value['interval_hours'], value['idle_hours'])
    return value


def _validate_intervals(interval_hours: int, idle_hours: int) -> None:
    if (type(interval_hours) is not int or type(idle_hours) is not int or
            not 1 <= interval_hours <= idle_hours <= 168):
        raise SyncError('Use 1–168 hours; “If unchanged” must be at least “Every”.')


def safe_settings(directory: Path | None = None) -> dict | None:
    """UI availability check; invalid settings must never prevent local usage."""
    try:
        return settings(directory)
    except (SyncError, ValueError, OSError, TypeError):
        return None


def _gh() -> str:
    executable = path_executable('gh')
    if not executable:
        raise SyncError('Install GitHub CLI, then choose Check connection.')
    return executable


def _environment() -> dict:
    # Always address github.com explicitly, regardless of a shell's GH_HOST.
    return {**external_command_env(), 'GH_HOST': 'github.com', 'GH_PROMPT_DISABLED': '1', 'GH_PAGER': 'cat'}


def _api(endpoint: str, *, payload: dict | None = None, method: str = 'GET',
         raw: bool = False, missing_ok: bool = False, query: str | None = None) -> bytes | None:
    command = [_gh(), 'api', '--hostname', 'github.com', endpoint, '--method', method]
    if query:
        command += ['--jq', query]
    if raw:
        command += ['-H', 'Accept: application/vnd.github.raw+json']
    if payload is not None:
        command += ['--input', '-']
    try:
        result = subprocess.run(command, input=json.dumps(payload, allow_nan=False).encode() if payload is not None else None,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
                                env=_environment(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError('Could not reach GitHub. Check your connection and try again.') from exc
    if result.returncode:
        # Never expose raw auth or HTTP diagnostics in the interface or files.
        match = re.search(rb'HTTP (\d{3})', result.stderr)
        status = int(match[1]) if match else None
        if missing_ok and status == 404:
            return None
        messages = {401: 'Sign in to GitHub again.', 403: 'GitHub denied access. Check repository permissions or try later.',
                    404: 'Repository not found. Check its name and your GitHub account.',
                    409: 'Another device updated the repository. Try Sync again.',
                    422: 'GitHub could not apply this change. The repository name may already be taken.'}
        raise SyncError(messages.get(status, 'GitHub request failed. Check your connection and account access.'), status)
    if len(result.stdout) > MAX_SNAPSHOT_BYTES:
        raise SyncError('This device snapshot exceeds the 8 MB sync limit.')
    return result.stdout


def account() -> str:
    value = json.loads(_api('user') or b'{}')
    login = value.get('login', '')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
        raise SyncError('Could not identify the signed-in GitHub account.')
    return login


def sign_in(on_code, cancel: threading.Event) -> str:
    """Use GitHub CLI's browser flow; retain only its one-time device code."""
    process = subprocess.Popen([_gh(), 'auth', 'login', '--hostname', 'github.com',
                                '--git-protocol', 'https', '--web', '--skip-ssh-key'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=_environment(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    def stop_when_cancelled():
        if cancel.wait(180) and process.poll() is None:
            process.terminate()
        elif process.poll() is None:
            process.terminate()
    watcher = threading.Thread(target=stop_when_cancelled, daemon=True)
    watcher.start()
    try:
        for line in iter(process.stdout.readline, b''):
            match = re.search(rb'\b([A-Z0-9]{4}-[A-Z0-9]{4})\b', line)
            if match:
                on_code(match[1].decode('ascii'))
        if process.wait() != 0:
            raise SyncError('Sign-in was cancelled or expired. Try again.')
        return account()
    finally:
        if process.poll() is None:
            process.terminate()
        process.stdout.close()


def _private_repo(repository: str, *, writable: bool = False) -> dict:
    value = json.loads(_api(f'repos/{repository}') or b'{}')
    if value.get('private') is not True or value.get('archived') or value.get('disabled'):
        raise SyncError('Choose an active private repository. Public repositories cannot hold usage sync.')
    if writable and not value.get('permissions', {}).get('push'):
        raise SyncError('This GitHub account needs write access to the private repository.')
    branch = value.get('default_branch', 'main')
    if not isinstance(branch, str) or not branch:
        raise SyncError('The repository has no default branch.')
    return value


def create_private_repo(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name):
        raise SyncError('Use a repository name containing letters, numbers, dots, dashes or underscores.')
    login = account()
    value = json.loads(_api('user/repos', method='POST', payload={
        'name': name, 'private': True, 'auto_init': True, 'has_issues': False,
        'has_projects': False, 'has_wiki': False,
        'description': 'Private Splitrail usage totals. No conversation content.'}) or b'{}')
    repository = f'{login}/{name}'
    if value.get('private') is not True:
        raise SyncError('GitHub did not confirm a private repository. No usage has been uploaded.')
    return repository


def configure(repository: str, directory: Path | None = None, *, scope: str = 'all',
              automatic: bool = False, receive_only: bool = False,
              interval_hours: int | None = None, idle_hours: int | None = None) -> dict:
    if not REPOSITORY.fullmatch(repository) or scope not in ('all', 'codex'):
        raise SyncError('Use owner/repository and select a usage source.')
    directory = directory or data_dir()
    with _LOCK:
        current = settings(directory)
        if interval_hours is None:
            interval_hours = current['interval_hours'] if current else DEFAULT_INTERVAL_HOURS
        if idle_hours is None:
            idle_hours = current['idle_hours'] if current else DEFAULT_IDLE_HOURS
        _validate_intervals(interval_hours, idle_hours)
        if current and current['repository'].lower() != repository.lower():
            raise SyncError('Disconnect the current repository before connecting another.')
        _private_repo(repository, writable=not receive_only)
        identity_file = directory / 'sync-device.json'
        identity = _read(identity_file).get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            identity = uuid.uuid4().hex
            write_export(identity_file, {'id': identity})
        config = {'version': 2, 'repository': repository, 'device': identity,
                  'scope': scope, 'automatic': bool(automatic), 'receive_only': bool(receive_only),
                  'interval_hours': interval_hours, 'idle_hours': idle_hours}
        write_export(directory / SETTINGS_FILE, config)
        return config


def disconnect(directory: Path | None = None) -> None:
    """Disable sync and forget downloaded totals; never delete GitHub data or sign out gh."""
    with _LOCK:
        directory = directory or data_dir()
        for filename in (SETTINGS_FILE, CACHE_FILE, STATE_FILE):
            (directory / filename).unlink(missing_ok=True)


def set_intervals(interval_hours: int, idle_hours: int, directory: Path | None = None) -> dict:
    """Change local scheduling preferences, even while GitHub is offline."""
    _validate_intervals(interval_hours, idle_hours)
    directory = directory or data_dir()
    with _LOCK:
        config = settings(directory)
        if not config:
            raise SyncError('Connect GitHub in Settings to enable sync.')
        config.update(interval_hours=interval_hours, idle_hours=idle_hours)
        write_export(directory / SETTINGS_FILE, config)
        return config


def _collect(config: dict) -> dict:
    # Never upload imports, received devices, quota or account information.
    from .runner import LocalCommandError, MissingCommandError, run_splitrail
    try:
        if config['scope'] == 'all':
            dataset = run_splitrail().dataset
        else:
            from .portable import scan_codex, usage_dataset
            from .history import record_usage
            events = scan_codex()['events']
            dataset, _ = record_usage(usage_dataset(events), events, scope='codex')
    except MissingCommandError as exc:
        raise SyncError(
            'All tools sync needs the Splitrail collector, which was not found. '
            'Install Splitrail 3.9.1 or newer, or choose Codex under '
            'Settings → GitHub sync → Options → Share from this device.'
        ) from exc
    except LocalCommandError as exc:
        detail = str(exc).replace(str(Path.home()), '~')
        raise SyncError('All tools sync could not read local usage. ' + detail) from exc
    except OSError as exc:
        raise SyncError('Local usage files could not be read. Check file access and try Sync again.') from exc
    try:
        return encode_dataset(dataset, config['device'], config['scope'])
    except (ValueError, KeyError, TypeError) as exc:
        raise SyncError(
            'Local usage totals failed validation. No usage was uploaded. '
            'Refresh usage and try Sync again. If this continues, update '
            'Splitrail Desktop and the usage collector.'
        ) from exc


def cached_devices(directory: Path | None = None) -> dict[str, dict]:
    directory = directory or data_dir()
    config = safe_settings(directory)
    if not config:
        return {}
    cache = _read(directory / CACHE_FILE)
    if cache.get('repository') != config['repository']:
        return {}
    return cache.get('devices', {})


def _sync_delay(config: dict, state: dict) -> int:
    stamp = state.get('checked_at') or state.get('synced_at')
    if not stamp:
        return 0
    try:
        checked = datetime.fromisoformat(stamp)
        if checked.tzinfo is None:
            return 0
        elapsed = max(0, (datetime.now(timezone.utc) - checked).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 0
    hours = config['idle_hours'] if state.get('unchanged') and not state.get('error') else config['interval_hours']
    return max(0, math.ceil(hours * 3600 - elapsed))


def seconds_until_auto_sync(directory: Path | None = None) -> int | None:
    """Read the persisted schedule without contacting GitHub or scanning logs."""
    directory = directory or data_dir()
    config = settings(directory)
    if not config or not config['automatic']:
        return None
    return _sync_delay(config, _read(directory / STATE_FILE))


def sync_now(directory: Path | None = None, *, snapshot: dict | None = None,
             automatic: bool = False) -> dict | None:
    directory = directory or data_dir()
    with _LOCK:
        config = settings(directory)
        if not config:
            if automatic:
                return None
            raise SyncError('Connect GitHub in Settings to enable sync.')
        state = _read(directory / STATE_FILE)
        if automatic and (not config['automatic'] or _sync_delay(config, state) > 0):
            return None
        # Persist attempts too: offline retries and restarts must respect the interval.
        state.update(checked_at=datetime.now(timezone.utc).isoformat(), unchanged=False)
        write_export(directory / STATE_FILE, state)
        try:
            return _sync(config, directory, state, snapshot)
        except (SyncError, OSError, ValueError, KeyError, TypeError):
            state.update(error=True, checked_at=datetime.now(timezone.utc).isoformat())
            write_export(directory / STATE_FILE, state)
            raise


def _sync(config: dict, directory: Path, state: dict, snapshot: dict | None) -> dict:
    repository = config['repository']
    repo = _private_repo(repository, writable=not config['receive_only'])
    branch = repo.get('default_branch', 'main')
    fingerprint = None
    if not config['receive_only']:
        local = snapshot if snapshot is not None else _collect(config)
        # Reconstruct a whitelist payload, dropping all unknown fields.
        data = decode_dataset(local, expected_device=config['device'])
        clean = encode_dataset(data, config['device'], local['scope'])
        raw = json.dumps(clean, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise SyncError('This device snapshot exceeds the 8 MB sync limit.')
        fingerprint = digest(clean)
        endpoint = f"repos/{repository}/contents/usage/{config['device']}.json"
        meta = _api(endpoint, missing_ok=True, query='{sha,size}')
        meta = json.loads(meta) if meta else {}
        if state.get('fingerprint') != fingerprint or state.get('sent_sha') != meta.get('sha'):
            body = {'message': 'Update device usage [skip ci]', 'branch': branch,
                    'content': base64.b64encode(raw).decode('ascii'),
                    'committer': {'name': 'Splitrail Sync', 'email': 'sync@users.noreply.github.com'},
                    'author': {'name': 'Splitrail Sync', 'email': 'sync@users.noreply.github.com'}}
            if meta.get('sha'):
                body['sha'] = meta['sha']
            # Recheck immediately before writing, after a potentially slow local scan.
            _private_repo(repository, writable=True)
            result = json.loads(_api(endpoint, payload=body, method='PUT') or b'{}')
            state.update(fingerprint=fingerprint, sent_sha=result.get('content', {}).get('sha'))
            write_export(directory / STATE_FILE, state)
    # Recheck privacy before reading; never follow arbitrary download URLs.
    _private_repo(repository)
    entries = _api(f'repos/{repository}/contents/usage', missing_ok=True)
    entries = json.loads(entries) if entries else []
    if not isinstance(entries, list) or len(entries) > MAX_DEVICES:
        raise SyncError('The sync repository contains too many device files.')
    previous = _read(directory / CACHE_FILE)
    old_devices = previous.get('devices', {}) if previous.get('repository') == repository else {}
    old_shas = previous.get('shas', {}) if previous.get('repository') == repository else {}
    devices, shas = {}, {}
    for entry in entries:
        match = DEVICE_PATH.fullmatch(entry.get('path', ''))
        if entry.get('type') != 'file' or not match:
            continue
        identity = match[1]
        if identity == config['device']:
            continue
        if type(entry.get('size')) is not int or not 0 <= entry['size'] <= MAX_SNAPSHOT_BYTES:
            raise SyncError('A remote device snapshot is too large.')
        sha = entry.get('sha')
        if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{40,64}', sha):
            raise SyncError('Invalid remote snapshot reference.')
        if sha and sha == old_shas.get(identity) and identity in old_devices:
            payload = old_devices[identity]
        else:
            # Pin the content to the listed blob, so a concurrent write cannot mix versions.
            _private_repo(repository)
            raw = _api(f'repos/{repository}/git/blobs/{sha}', raw=True)
            payload = json.loads(raw or b'{}')
        data = decode_dataset(payload, expected_device=identity)
        from .history import record_remote
        devices[identity] = record_remote(encode_dataset(data, identity, payload['scope']), directory=directory)
        shas[identity] = sha
    # One atomic cache swap after every snapshot validates. A failure preserves old totals.
    write_export(directory / CACHE_FILE, {'repository': repository, 'devices': devices, 'shas': shas})
    # Compare completed totals, including remote changes and deleted devices.
    usage_fingerprint = digest({'local': fingerprint, 'devices': devices})
    changed = state.get('usage_fingerprint') != usage_fingerprint
    state.update(synced_at=datetime.now(timezone.utc).isoformat(), device_count=len(devices) + 1,
                 usage_fingerprint=usage_fingerprint, unchanged=not changed)
    state['checked_at'] = state['synced_at']
    state.pop('error', None)
    write_export(directory / STATE_FILE, state)
    return {'status': 'Up to date', 'devices': len(devices) + 1, 'changed': changed}


def auto_receive(directory: Path | None = None) -> str | None:
    try:
        config = settings(directory)
    except (SyncError, ValueError, OSError, TypeError):
        return 'Sync settings need attention. Open Settings; local usage is still available.'
    if config and config['automatic']:
        try:
            result = sync_now(directory, automatic=True)
            if result:
                return f"Synced · {result['devices']} devices"
            if not _read((directory or data_dir()) / STATE_FILE).get('error'):
                return None
        except (SyncError, OSError, ValueError, KeyError, TypeError):
            pass
        return 'Sync unavailable. Showing the last downloaded usage; try Sync again.'
    return None


def status_text(directory: Path | None = None) -> str:
    directory = directory or data_dir()
    try:
        config = settings(directory)
    except (SyncError, ValueError, OSError, TypeError):
        return 'Sync settings need attention · Open Settings'
    if not config:
        return 'Local only · Connect GitHub to sync devices'
    state = _read(directory / STATE_FILE)
    if state.get('error'):
        return 'Offline · Showing saved device totals'
    if not state.get('synced_at'):
        return 'Connected · Ready to sync'
    from .quota import format_refresh_age
    stamp = datetime.fromisoformat(state['synced_at'])
    return f"{state.get('device_count', 1)} devices · {format_refresh_age(stamp)}"
