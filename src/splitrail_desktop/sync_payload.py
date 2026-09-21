"""Strict, content-free daily and hourly totals exchanged between devices.

Only explicit numeric fields are serialized. Unknown model/tool identifiers are
pseudonymized; paths, account metadata and source records never enter the wire format.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, replace
from datetime import date

from .domain import DailyUsage, HourlyUsage, ModelDetail, TokenUsage, UsageDataset
from .portable import digest
from .pricing import CATALOG, model_key

SCHEMA = 'splitrail-device-usage-v1'
TOKEN_FIELDS = tuple(TokenUsage.__dataclass_fields__)
COUNT_FIELDS = ('conversations', 'user_messages', 'ai_messages', 'tool_calls')
TOOLS = {'Codex CLI', 'Codex via Pi', 'Claude Code', 'Gemini CLI', 'Antigravity CLI',
         'Pi Agent', 'Cursor', 'Cline', 'Roo Code', 'Aider', 'OpenCode', 'Amp',
         'Copilot', 'GitHub Copilot', 'Windsurf', 'Kilo Code', 'Droid', 'Goose'}


def safe_model(name: str) -> str:
    key = model_key(name)
    if key in CATALOG['models']:
        return key
    if re.fullmatch(r'custom-[a-f0-9]{16}', name):
        return name
    return 'custom-' + digest(name)[:16]


def safe_tool(name: str) -> str:
    if name in TOOLS or re.fullmatch(r'tool-[a-f0-9]{16}', name):
        return name
    return 'tool-' + digest(name)[:16]


def count(value) -> int:
    if type(value) is not int or not 0 <= value <= 10**16:
        raise ValueError('Invalid usage count')
    return value


def cost(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10**12:
        raise ValueError('Invalid usage cost')
    return float(value)


def tokens(value) -> TokenUsage:
    if not isinstance(value, dict):
        raise ValueError('Invalid token totals')
    result = TokenUsage(**{key: count(value.get(key, 0)) for key in TOKEN_FIELDS})
    if result.reasoning_in_output > min(result.reasoning, result.output):
        raise ValueError('Invalid reasoning totals')
    return result


def encode_dataset(dataset: UsageDataset, device: str, scope: str) -> dict:
    rows = []
    for day in dataset.days:
        messages = {}
        for name, value in day.model_messages.items():
            key = safe_model(name)
            messages[key] = messages.get(key, 0) + value
        rows.append({'day': day.day.isoformat(), 'tool': safe_tool(day.analyzer),
                     **{key: getattr(day, key) for key in COUNT_FIELDS},
                     'tokens': asdict(day.tokens), 'cost': day.cost,
                     'models': [{'model': safe_model(m.name), 'messages': m.messages,
                                 'tokens': asdict(m.tokens), 'cost': m.cost, 'tool_calls': m.tool_calls}
                                for m in day.model_details],
                     'model_messages': messages})
    payload = {'schema': SCHEMA, 'device': device, 'scope': scope, 'days': rows}
    if dataset.hours:
        payload['hours'] = [{'day': hour.day.isoformat(), 'hour': hour.hour,
                             'tool': safe_tool(hour.analyzer), 'tokens': asdict(hour.tokens),
                             'cost': hour.cost} for hour in dataset.hours]
    # Fail closed before sending even if a collector/model unexpectedly changes.
    decode_dataset(payload, expected_device=device)
    return payload


def decode_dataset(payload: dict, *, expected_device: str | None = None) -> UsageDataset:
    if not isinstance(payload, dict) or payload.get('schema') != SCHEMA:
        raise ValueError('Unsupported device snapshot')
    device = payload.get('device', '')
    if not isinstance(device, str) or not re.fullmatch(r'[a-f0-9]{32}', device) or (expected_device and device != expected_device):
        raise ValueError('Invalid device identity')
    if payload.get('scope') not in ('all', 'codex'):
        raise ValueError('Invalid usage scope')
    rows = payload.get('days')
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError('Invalid daily usage')
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid usage row')
        tool = row.get('tool')
        if not isinstance(tool, str) or safe_tool(tool) != tool:
            raise ValueError('Invalid tool name')
        day = date.fromisoformat(row['day'])
        details = row.get('models', [])
        messages = row.get('model_messages', {})
        if not isinstance(details, list) or len(details) > 1000 or not isinstance(messages, dict) or len(messages) > 1000:
            raise ValueError('Invalid model details')
        models = []
        for item in details:
            name = item['model']
            if not isinstance(name, str) or safe_model(name) != name:
                raise ValueError('Invalid model identifier')
            models.append(ModelDetail(name, count(item['messages']), tokens(item['tokens']),
                                      cost(item['cost']), count(item['tool_calls'])))
        for name, value in messages.items():
            if not isinstance(name, str) or safe_model(name) != name:
                raise ValueError('Invalid model identifier')
            count(value)
        parsed.append(DailyUsage(tool, day, **{key: count(row[key]) for key in COUNT_FIELDS},
                                 tokens=tokens(row['tokens']), cost=cost(row['cost']),
                                 model_messages=dict(messages), model_details=tuple(models)))
    totals = {}
    for row in parsed:
        totals[row.analyzer] = totals.get(row.analyzer, 0) + row.conversations
    # Optional extension: old clients and daily-only snapshots remain compatible.
    hours = payload.get('hours', [])
    if not isinstance(hours, list) or len(hours) > 500_000:
        raise ValueError('Invalid hourly usage')
    parsed_hours, seen = [], set()
    day_keys = {(row.day, row.analyzer) for row in parsed}
    for row in hours:
        if not isinstance(row, dict):
            raise ValueError('Invalid hourly row')
        day = date.fromisoformat(row['day'])
        hour = count(row['hour'])
        tool = row.get('tool')
        if hour > 23 or not isinstance(tool, str) or safe_tool(tool) != tool:
            raise ValueError('Invalid hourly identifier')
        key = (day, tool, hour)
        if key in seen or (day, tool) not in day_keys:
            raise ValueError('Duplicate or unmatched hourly usage')
        seen.add(key)
        parsed_hours.append(HourlyUsage(tool, day, hour, tokens(row['tokens']), cost(row['cost'])))
    return UsageDataset(tuple(parsed), totals, hours=tuple(parsed_hours))


def add_devices(local: UsageDataset, remote: dict[str, dict], own_id: str | None = None) -> UsageDataset:
    if not remote:
        return local
    days = list(local.days)
    hours = list(local.hours)
    totals = dict(local.analyzer_conversation_totals)
    for device, payload in sorted(remote.items()):
        if device == own_id:
            continue
        data = decode_dataset(payload, expected_device=device)
        for row in data.days:
            name = f'{row.analyzer} · {device[:6]}'
            days.append(replace(row, analyzer=name))
        hours.extend(replace(row, analyzer=f'{row.analyzer} · {device[:6]}') for row in data.hours)
        for name, value in data.analyzer_conversation_totals.items():
            key = f'{name} · {device[:6]}'
            totals[key] = totals.get(key, 0) + value
    return UsageDataset(tuple(sorted(days, key=lambda d: (d.day, d.analyzer))), totals,
                        local.ignored_raw_messages, tuple(hours))
