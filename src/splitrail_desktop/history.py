"""Keep a recovered usage checkpoint and record only subsequent usage.

The database is private app data. It contains whitelisted aggregates and hashed
request identities, never conversation content. A checkpoint is deliberately
not a new estimate of old usage.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from .domain import DailyUsage, ModelDetail, TokenUsage, UsageDataset
from .portable import data_dir, usage_dataset
from .sync_payload import count, decode_dataset, encode_dataset, safe_tool

HISTORY_FILE = 'usage-history-v1.sqlite'
# Bump when request identities or counting rules change. A new version starts
# another checkpoint instead of applying the new reader to recorded history.
ACCOUNTING_VERSION = 1
_DEVICE = '0' * 32
_COUNTS = ('conversations', 'user_messages', 'ai_messages', 'tool_calls')
_CODEX = {'Codex CLI', 'Codex via Pi'}


def available(directory: Path | None = None) -> bool:
    return ((directory or data_dir()) / HISTORY_FILE).exists()


def _encode(dataset):
    payload = encode_dataset(dataset, _DEVICE, 'all')
    # The wire schema reports session-days; the local collector additionally
    # reports distinct conversation counts. Retain those in this local extension.
    payload['conversation_totals'] = {safe_tool(name): count(value)
                                      for name, value in dataset.analyzer_conversation_totals.items()}
    return payload


def _decode(payload):
    dataset = decode_dataset(payload)
    if 'conversation_totals' in payload:
        totals = payload['conversation_totals']
        if not isinstance(totals, dict) or any(safe_tool(name) != name for name in totals):
            raise ValueError('Invalid saved conversation totals.')
        dataset = replace(dataset, analyzer_conversation_totals={name: count(value) for name, value in totals.items()})
    return dataset


def _canonical(dataset):
    return _decode(_encode(dataset))


@contextmanager
def _connect(path):
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute('PRAGMA busy_timeout=30000')
        with connection:
            yield connection
    finally:
        connection.close()


def _get(connection, key):
    row = connection.execute('SELECT value FROM checkpoints WHERE name=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _put(connection, key, value):
    connection.execute('INSERT OR REPLACE INTO checkpoints VALUES (?, ?)',
                       (key, json.dumps(value, separators=(',', ':'), allow_nan=False)))


def _state(baseline, reference, now):
    return {'version': ACCOUNTING_VERSION, 'cutoff': now.astimezone(timezone.utc).isoformat(),
            'day': now.astimezone().date().isoformat(),
            'recorded': _encode(baseline), 'reference': _encode(reference)}


def initialize(baseline: UsageDataset, reference: UsageDataset, events: list[dict], *,
               directory: Path | None = None, remote: dict[str, dict] | None = None,
               now: datetime | None = None) -> None:
    """Install a reviewed historical checkpoint once; refuse to replace it."""
    directory = directory or data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / HISTORY_FILE
    # Exclusive creation prevents an accidental repeat from resetting history.
    with path.open('xb'):
        pass
    path.chmod(0o600)
    now = now or datetime.now(timezone.utc)
    try:
        with _connect(path) as connection:
            connection.execute('CREATE TABLE checkpoints (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            connection.execute('CREATE TABLE requests (id TEXT PRIMARY KEY) WITHOUT ROWID')
            _put(connection, 'local', _state(baseline, reference, now))
            connection.executemany('INSERT OR IGNORE INTO requests VALUES (?)',
                                   ((event['id'],) for event in events))
            for identity, payload in (remote or {}).items():
                dataset = decode_dataset(payload, expected_device=identity)
                _put(connection, 'remote:' + identity, _state(dataset, dataset, now))
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _empty(row):
    return DailyUsage(row.analyzer, row.day, 0, 0, 0, TokenUsage(), 0, 0, {}, ())


def _tokens_delta(current, previous):
    values = {key: getattr(current, key) - getattr(previous, key)
              for key in TokenUsage.__dataclass_fields__}
    # A missing file or a redistributed counter is not negative consumption.
    if any(value < 0 for value in values.values()):
        return TokenUsage()
    result = TokenUsage(**values)
    if result.reasoning_in_output > min(result.reasoning, result.output):
        return TokenUsage()
    return result


def _detail_delta(current, previous):
    tokens = _tokens_delta(current.tokens, previous.tokens)
    return ModelDetail(current.name, max(0, current.messages - previous.messages), tokens,
                       max(0, current.cost - previous.cost) if tokens.total else 0,
                       max(0, current.tool_calls - previous.tool_calls))


def _row_delta(current, previous, *, metadata_only=False):
    previous = previous or _empty(current)
    tokens = TokenUsage() if metadata_only else _tokens_delta(current.tokens, previous.tokens)
    old_models = {detail.name: detail for detail in previous.model_details}
    details = []
    for detail in current.model_details:
        old = old_models.get(detail.name, ModelDetail(detail.name, 0, TokenUsage(), 0, 0))
        delta = _detail_delta(detail, old)
        if metadata_only:
            delta = replace(delta, tokens=TokenUsage(), cost=0)
        details.append(delta)
    return replace(current, **{key: max(0, getattr(current, key) - getattr(previous, key)) for key in _COUNTS},
                   tokens=tokens, cost=max(0, current.cost - previous.cost) if tokens.total else 0,
                   model_details=tuple(details),
                   model_messages={name: max(0, value - previous.model_messages.get(name, 0))
                                   for name, value in current.model_messages.items()})


def _add_row(left, right):
    models = {item.name: item for item in left.model_details}
    for item in right.model_details:
        old = models.get(item.name, ModelDetail(item.name, 0, TokenUsage(), 0, 0))
        models[item.name] = ModelDetail(item.name, old.messages + item.messages,
                                       old.tokens + item.tokens, old.cost + item.cost,
                                       old.tool_calls + item.tool_calls)
    messages = dict(left.model_messages)
    for name, value in right.model_messages.items():
        messages[name] = messages.get(name, 0) + value
    return replace(left, **{key: getattr(left, key) + getattr(right, key) for key in _COUNTS},
                   tokens=left.tokens + right.tokens, cost=left.cost + right.cost,
                   model_messages=messages, model_details=tuple(models[name] for name in sorted(models)))


def add_usage(left, right):
    rows = {(row.day, row.analyzer): row for row in left.days}
    for row in right.days:
        key = (row.day, row.analyzer)
        rows[key] = _add_row(rows.get(key, _empty(row)), row)
    hours = {(row.day, row.analyzer, row.hour): row for row in left.hours}
    for row in right.hours:
        key = (row.day, row.analyzer, row.hour)
        old = hours.get(key)
        hours[key] = replace(row, tokens=old.tokens + row.tokens, cost=old.cost + row.cost) if old else row
    totals = dict(left.analyzer_conversation_totals)
    for name, value in right.analyzer_conversation_totals.items():
        totals[name] = totals.get(name, 0) + value
    return UsageDataset(tuple(rows[key] for key in sorted(rows)), totals,
                        left.ignored_raw_messages, tuple(hours[key] for key in sorted(hours)))


def _advance(state, candidate, *, native=False):
    """Add positive aggregate increments after the checkpoint, once only."""
    reference = _decode(state['reference'])
    rows = {(row.day, row.analyzer): row for row in reference.days}
    day = date.fromisoformat(state['day'])
    increments = []
    for current in candidate.days:
        if current.day < day:
            continue
        key = (current.day, current.analyzer)
        old = rows.get(key, _empty(current))
        metadata_only = native and current.analyzer in _CODEX
        increments.append(_row_delta(current, old, metadata_only=metadata_only))
        # Keep a high-water reference through missing files and repeated scans.
        rows[key] = _add_row(old, _row_delta(current, old))
    hours = {(row.day, row.analyzer, row.hour): row for row in reference.hours}
    added_hours = []
    for current in candidate.hours:
        if current.day < day or native and current.analyzer in _CODEX:
            continue
        key = (current.day, current.analyzer, current.hour)
        old = hours.get(key, replace(current, tokens=TokenUsage(), cost=0))
        tokens = _tokens_delta(current.tokens, old.tokens)
        cost = max(0, current.cost - old.cost) if tokens.total else 0
        added_hours.append(replace(current, tokens=tokens, cost=cost))
        hours[key] = replace(old, tokens=old.tokens + tokens, cost=old.cost + cost)
    totals = dict(reference.analyzer_conversation_totals)
    added_totals = {}
    for name, value in candidate.analyzer_conversation_totals.items():
        added_totals[name] = max(0, value - totals.get(name, 0))
        totals[name] = max(value, totals.get(name, 0))
    state['reference'] = _encode(UsageDataset(tuple(rows.values()), totals, hours=tuple(hours.values())))
    increment = UsageDataset(tuple(increments), added_totals, hours=tuple(added_hours))
    return add_usage(_decode(state['recorded']), increment)


def record_usage(dataset: UsageDataset, events: list[dict] | None, *, scope='all',
                 directory: Path | None = None) -> tuple[UsageDataset, str | None]:
    path = (directory or data_dir()) / HISTORY_FILE
    if not path.exists():
        return dataset, None
    with _connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        state = _get(connection, 'local')
        if not state:
            raise ValueError('Saved usage checkpoint is missing.')
        candidate = _canonical(dataset)
        if state['version'] != ACCOUNTING_VERSION:
            # The existing figures become the next baseline. Do not recalculate
            # them using a changed parser, including the partially completed day.
            # A Codex-only refresh must not erase the reference for other tools.
            previous = _decode(state['reference'])
            rows = {(r.day, r.analyzer): r for r in previous.days}
            rows.update({(r.day, r.analyzer): r for r in candidate.days})
            hours = {(r.day, r.analyzer, r.hour): r for r in previous.hours}
            hours.update({(r.day, r.analyzer, r.hour): r for r in candidate.hours})
            totals = {**previous.analyzer_conversation_totals, **candidate.analyzer_conversation_totals}
            reference = UsageDataset(tuple(rows.values()), totals, hours=tuple(hours.values()))
            state = _state(_decode(state['recorded']), reference, datetime.now(timezone.utc))
            connection.executemany('INSERT OR IGNORE INTO requests VALUES (?)',
                                   ((event['id'],) for event in (events or [])))
        recorded = _advance(state, candidate, native=True)
        new = []
        for event in events or []:
            if datetime.fromisoformat(event['timestamp']).astimezone().date() < date.fromisoformat(state['day']):
                continue
            cursor = connection.execute('INSERT OR IGNORE INTO requests VALUES (?)', (event['id'],))
            if cursor.rowcount:
                new.append(event)
        if new:
            # Price each request once. Later rate/parser changes cannot reprice
            # these stored aggregates, and deletion of a source log cannot erase them.
            added = _canonical(usage_dataset(new))
            # Collector metadata was incremented above; avoid counting it twice.
            added = replace(added, days=tuple(replace(row, conversations=0, ai_messages=0,
                                  model_messages={}, model_details=tuple(replace(m, messages=0) for m in row.model_details))
                                  for row in added.days), analyzer_conversation_totals={})
            recorded = add_usage(recorded, added)
        state['recorded'] = _encode(recorded)
        _put(connection, 'local', state)
    allowed = (lambda name: name in _CODEX) if scope == 'codex' else (lambda name: name != 'Codex via Pi')
    result = replace(recorded, days=tuple(row for row in recorded.days if allowed(row.analyzer)),
                     hours=tuple(row for row in recorded.hours if allowed(row.analyzer)),
                     analyzer_conversation_totals={name: value for name, value in recorded.analyzer_conversation_totals.items() if allowed(name)})
    return result, 'Saved history preserved; counting changes apply to new usage only.'


def record_remote(payload: dict, *, directory: Path | None = None) -> dict:
    path = (directory or data_dir()) / HISTORY_FILE
    if not path.exists():
        return payload
    identity = payload['device']
    candidate = decode_dataset(payload, expected_device=identity)
    with _connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        key = 'remote:' + identity
        state = _get(connection, key)
        if state is None:
            state = _state(candidate, candidate, datetime.now(timezone.utc))
        recorded = _advance(state, candidate)
        state['recorded'] = _encode(recorded)
        _put(connection, key, state)
    return encode_dataset(recorded, identity, payload['scope'])
