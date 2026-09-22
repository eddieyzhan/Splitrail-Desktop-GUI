"""Stream normalized collector statistics into content-free hourly aggregates.

No request/session identifiers, titles, paths or conversation fields are retained.
Daily collector totals remain authoritative; only timing detail is added here.
"""
from __future__ import annotations

import subprocess
import threading
from dataclasses import replace
from datetime import datetime

import ijson
from ijson.common import ObjectBuilder

from .domain import (DailyUsage, HourlyUsage, ModelDetail, StatsDataError,
                     TokenUsage, UsageDataset, _integer, _number, parse_stats_payload)
from .pricing import load_overrides, reprice_dataset
from .platform_support import command_options

MAX_STREAM_BYTES = 512 * 1024 * 1024
STAT_FIELDS = {'inputTokens', 'outputTokens', 'reasoningTokens', 'cachedTokens',
               'cacheReadTokens', 'cacheCreationTokens', 'cost'}


def _add_record(groups, record):
    if record.get('role') != 'assistant':
        return
    try:
        stamp = datetime.fromisoformat(record['date'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return
        local = stamp.astimezone()
        stats = record['stats']
        tokens = TokenUsage(**{target: _integer(stats.get(source, 0), source)
                              for target, source in (
                                  ('input', 'inputTokens'), ('output', 'outputTokens'),
                                  ('reasoning', 'reasoningTokens'), ('cached', 'cachedTokens'),
                                  ('cache_read', 'cacheReadTokens'), ('cache_write', 'cacheCreationTokens'))})
        cost = _number(stats.get('cost', 0), 'cost')
        model = record.get('model') or ''
        if not isinstance(model, str):
            return
        key = (local.date(), local.hour, model)
        previous = groups.get(key, (TokenUsage(), 0.0))
        groups[key] = (previous[0] + tokens, previous[1] + cost)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        # Missing timing detail must not discard valid daily totals. The chart
        # compares coverage with daily totals and exposes gaps in Notifications.
        return


def _hours(groups, analyzer, overrides):
    by_hour = {}
    for (day, hour, model), (tokens, cost) in groups.items():
        if analyzer in ('Codex CLI', 'Antigravity CLI'):
            tokens = replace(tokens, reasoning_in_output=tokens.reasoning)
        by_hour.setdefault((day, hour), []).append(ModelDetail(model, 0, tokens, cost, 0))
    result = []
    for (day, hour), details in sorted(by_hour.items()):
        total = TokenUsage()
        for detail in details:
            total += detail.tokens
        row = DailyUsage(analyzer, day, 0, 0, 0, total, sum(d.cost for d in details),
                         0, {}, tuple(d for d in details if d.name))
        priced, _, _ = reprice_dataset(UsageDataset((row,), {}), overrides)
        result.append(HourlyUsage(analyzer, day, hour, total, priced.days[0].cost))
    return result


def parse_collector_stream(stream) -> UsageDataset:
    """Read one JSON stream without materializing its per-request record array."""
    prefix = 'analyzer_stats.item'
    analyzers, hours = [], []
    analyzer, record, groups, daily = {}, {}, {}, None
    overrides = load_overrides()
    root_object = False
    analyzer_list = False
    for path, event, value in ijson.parse(stream, use_float=True):
        if path == '' and event == 'start_map':
            root_object = True
        elif path == 'analyzer_stats' and event == 'start_array':
            analyzer_list = True
        elif path == prefix and event == 'start_map':
            analyzer, groups, daily = {}, {}, None
        elif path == prefix and event not in ('end_map', 'map_key'):
            raise StatsDataError('Invalid collector analyzer')
        elif path == prefix + '.daily_stats' and event in ('start_map', 'start_array'):
            daily = ObjectBuilder()
            daily.event(event, value)
        elif path.startswith(prefix + '.daily_stats') and daily is not None:
            daily.event(event, value)
            if path == prefix + '.daily_stats' and event in ('end_map', 'end_array'):
                analyzer['daily_stats'] = daily.value
                daily = None
        elif path == prefix + '.messages.item' and event == 'start_map':
            record = {'stats': {}}
        elif path == prefix + '.messages.item' and event == 'end_map':
            _add_record(groups, record)
            record = {}
        elif event in ('string', 'number', 'null'):
            if path in (prefix + '.analyzer_name', prefix + '.num_conversations'):
                analyzer[path.rsplit('.', 1)[1]] = value
            elif path in (prefix + '.messages.item.date', prefix + '.messages.item.model', prefix + '.messages.item.role'):
                record[path.rsplit('.', 1)[1]] = value
            elif path.startswith(prefix + '.messages.item.stats.'):
                field = path.rsplit('.', 1)[1]
                if field in STAT_FIELDS:
                    record.setdefault('stats', {})[field] = value
        elif path == prefix and event == 'end_map':
            if not isinstance(analyzer.get('analyzer_name'), str) or 'daily_stats' not in analyzer:
                raise StatsDataError('Collector is missing aggregate fields')
            analyzers.append(analyzer)
            hours.extend(_hours(groups, analyzer['analyzer_name'], overrides))
    # No messages array or other source metadata crosses this boundary.
    if not root_object or not analyzer_list:
        raise StatsDataError('Collector is missing its analyzer list')
    dataset = parse_stats_payload({'analyzer_stats': analyzers})
    return replace(dataset, hours=tuple(hours))


class _BoundedStream:
    def __init__(self, stream):
        self.stream, self.total = stream, 0

    def read(self, size=-1):
        block = self.stream.read(size)
        self.total += len(block)
        if self.total > MAX_STREAM_BYTES:
            raise ValueError('Collector statistics exceeded the streaming limit')
        return block


def read_collector(executable: str, timeout: float):
    """Drain both pipes, enforce a deadline, and never write records to disk."""
    process = subprocess.Popen((executable, 'stats', '--include-messages'),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, shell=False, **command_options())
    expired = threading.Event()
    diagnostics = bytearray()
    def drain():
        while block := process.stderr.read(4096):
            remaining = max(0, 64 * 1024 - len(diagnostics))
            diagnostics.extend(block[:remaining])
    def expire():
        expired.set()
        process.kill()
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    try:
        dataset = parse_collector_stream(_BoundedStream(process.stdout))
        code = process.wait(timeout=max(1, timeout))
        if expired.is_set():
            raise TimeoutError('Collector timed out')
        reader.join(timeout=1)
        return dataset, code, diagnostics.decode('utf-8', errors='replace')
    except (ijson.JSONError, ValueError, OverflowError):
        # Parser exceptions can include excerpts of the input. Never expose them.
        if expired.is_set():
            raise TimeoutError('Collector timed out') from None
        raise StatsDataError('Collector returned invalid usage statistics') from None
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
