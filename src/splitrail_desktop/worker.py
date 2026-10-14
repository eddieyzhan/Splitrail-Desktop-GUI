"""Run history-heavy work in disposable processes, returning only small results."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from . import portable
from .domain import DailyUsage, HourlyUsage, ModelDetail, TokenUsage, UsageDataset
from .platform_support import command_options
from .runner import CostDiagnostics, LocalCommandError, MissingCommandError, StatsCommandResult

MAX_RESULT_BYTES = 50 * 1024 * 1024


def encode_result(result):
    if not isinstance(result, StatsCommandResult):
        return result
    return {'usage': asdict(result)}


def decode_result(value):
    if not isinstance(value, dict) or 'usage' not in value:
        return value
    result = value['usage']
    data = result['dataset']
    def row(item, hourly=False):
        fields = {**item, 'day': date.fromisoformat(item['day']), 'tokens': TokenUsage(**item['tokens'])}
        if hourly:
            return HourlyUsage(**fields)
        fields['model_details'] = tuple(ModelDetail(**{**m, 'tokens': TokenUsage(**m['tokens'])})
                                        for m in item['model_details'])
        return DailyUsage(**fields)
    dataset = UsageDataset(tuple(row(item) for item in data['days']), data['analyzer_conversation_totals'],
                           data['ignored_raw_messages'], tuple(row(item, True) for item in data['hours']))
    diagnostics = result['cost_diagnostics']
    diagnostics = CostDiagnostics(**{**diagnostics, 'unknown_models': tuple(diagnostics['unknown_models']),
                                    'lines': tuple(diagnostics['lines'])})
    return StatsCommandResult(dataset, diagnostics, result['exit_code'])


def execute(task, options):
    if task == 'usage':
        from .catalog_updates import refresh_prices
        refresh_prices(allow_network=options['automatic_prices'])
        mode = options['mode']
        if mode == 'combined':
            from .combined import run_combined_usage
            return run_combined_usage(automatic_sync=False, include_hours=options['include_hours'])
        if mode == 'codex':
            return portable.run_portable_usage(automatic_sync=False)
        if mode == 'local':
            from .runner import run_splitrail
            return run_splitrail(include_hours=options['include_hours'])
        raise ValueError('Unknown usage source')
    if task == 'sync':
        from .sync import sync_now
        return sync_now(automatic=options.get('automatic', False))
    if task == 'export':
        payload = portable.scan_codex()
        portable.write_export(Path(options['path']), payload)
        return f"{len(payload['events']):,} requests exported"
    if task == 'import':
        return f"{portable.import_export(Path(options['path'])):,} new requests added"
    raise ValueError('Unknown background task')


def worker_main(request_path, result_path):
    try:
        request = json.loads(Path(request_path).read_text(encoding='utf-8'))
        result = {'result': encode_result(execute(request['task'], request['options']))}
    except Exception as exc:
        result = {'error': str(exc), 'error_type': type(exc).__name__}
    Path(result_path).write_text(json.dumps(result, default=lambda value: value.isoformat(), allow_nan=False),
                                 encoding='utf-8')
    return 0


def _stop(process):
    if os.name != 'nt':
        # The helper and its collector have their own process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        import psutil
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
    process.wait()


class BackgroundWorker:
    def __init__(self):
        self._lock = threading.Lock()
        self._processes = set()
        self._closed = False

    def run(self, task, **options):
        with tempfile.TemporaryDirectory(prefix='splitrail-worker-') as directory:
            root = Path(directory)
            request, response = root / 'request.json', root / 'result.json'
            request.write_text(json.dumps({'task': task, 'options': options}), encoding='utf-8')
            environment = {**os.environ, 'SPLITRAIL_DATA_DIR': str(portable.data_dir())}
            source = Path(__file__).resolve().parent.parent
            if getattr(sys, 'frozen', False):
                command = [sys.executable]
            elif source.suffix == '.pyz':
                command = [sys.executable, str(source)]
            else:
                command = [sys.executable, '-m', 'splitrail_desktop']
                environment['PYTHONPATH'] = str(source) + os.pathsep + environment.get('PYTHONPATH', '')
            with self._lock:
                if self._closed:
                    raise LocalCommandError('Background work cancelled')
                process = subprocess.Popen([*command, '--worker', str(request), str(response)],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, env=environment,
                                           start_new_session=os.name != 'nt', **command_options())
                self._processes.add(process)
            try:
                deadline = time.monotonic() + (600 if task == 'sync' else 240)
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise LocalCommandError('Background work timed out; previous totals are retained.')
                    time.sleep(.05)
                if process.returncode or not response.exists():
                    raise LocalCommandError('Background work stopped before returning a result.')
                if response.stat().st_size > MAX_RESULT_BYTES:
                    raise LocalCommandError('Background result exceeded the size limit.')
                result = json.loads(response.read_text(encoding='utf-8'))
                if 'error' in result:
                    from .sync import SyncError
                    error = {'MissingCommandError': MissingCommandError, 'SyncError': SyncError}.get(
                        result.get('error_type'), LocalCommandError)
                    raise error(result['error'])
                return decode_result(result['result'])
            finally:
                _stop(process)
                with self._lock:
                    self._processes.discard(process)

    def close(self):
        with self._lock:
            self._closed = True
            processes = tuple(self._processes)
        for process in processes:
            _stop(process)
