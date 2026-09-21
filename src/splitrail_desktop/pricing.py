"""Offline, versioned token prices and atomic user overrides (USD / 1M tokens)."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import replace
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from .domain import UsageDataset

CATALOG = json.loads(files('splitrail_desktop').joinpath('model_prices.json').read_text())
RATE_FIELDS = ('input', 'output', 'cache_read', 'cache_write')


class PricingError(ValueError):
    pass


def normalize_model(name: str) -> str:
    name = name.strip().lower()
    prefixes = {'models', 'openai', 'openai-codex', 'anthropic', 'google', 'google-vertex',
                'google-gemini-cli', 'google-antigravity', 'antigravity', 'xai', 'x-ai'}
    while '/' in name and name.split('/', 1)[0] in prefixes:
        name = name.split('/', 1)[1]
    if name.startswith('gemini-'):
        name = re.sub(r'^gemini-(\d+)-(\d+)(?=-)', r'gemini-\1.\2', name)
    if name.startswith('claude-'):
        name = name.replace('.', '-')
    return name


def model_key(name: str) -> str:
    name = normalize_model(name)
    if name in CATALOG['models']:
        return name
    name = CATALOG['aliases'].get(name, name)
    # Only dated snapshots and explicit latest aliases inherit rates. Never
    # remove feature suffixes such as -spark, -pro, -image or -audio.
    base = re.sub(r'-(?:20\d{2}-?\d{2}-?\d{2}|latest)$', '', name)
    return base if base in CATALOG['models'] else name


def overrides_path() -> Path:
    from .portable import data_dir
    return data_dir() / 'model-prices.json'


def validate_rates(value: dict) -> dict:
    if not isinstance(value, dict):
        raise PricingError('Rates must be an object.')
    result = {}
    for field in RATE_FIELDS:
        raw = value.get(field)
        if raw is None and field in ('cache_read', 'cache_write'):
            result[field] = None
            continue
        try:
            if isinstance(raw, bool):
                raise ValueError
            rate = float(raw)
            if not math.isfinite(rate) or not 0 <= rate <= 1_000_000:
                raise ValueError
        except (ValueError, TypeError, OverflowError) as exc:
            raise PricingError(f"{field.replace('_', ' ').capitalize()} must be a finite number from 0 to 1,000,000.") from exc
        result[field] = rate
    return result


@lru_cache(maxsize=8)
def _read_overrides(path: str, stamp: int, size: int) -> dict[str, dict]:
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload.get('models'), dict):
            raise PricingError('Unsupported custom pricing file.')
        result = {}
        for name, value in payload['models'].items():
            key = normalize_model(name)
            if not key or len(key) > 200 or any(c.isspace() for c in key):
                raise PricingError('Invalid model name in custom pricing.')
            result[key] = validate_rates(value)
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise PricingError(f'Cannot read custom pricing: {exc}') from exc


def load_overrides() -> dict[str, dict]:
    path = overrides_path()
    try:
        info = path.stat()
    except FileNotFoundError:
        return {}
    return {k: dict(v) for k, v in _read_overrides(str(path), info.st_mtime_ns, info.st_size).items()}


def save_override(name: str, rates: dict | None) -> None:
    key = normalize_model(name)
    if not key or len(key) > 200 or any(c.isspace() for c in key):
        raise PricingError('Enter a model ID without spaces (for example, my-model-v1).')
    values = load_overrides()
    if rates is None:
        values.pop(key, None)
    else:
        values[key] = validate_rates(rates)
    path = overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'version': 1, 'models': values}, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        _read_overrides.cache_clear()


def custom_rates(name: str, overrides: dict) -> dict | None:
    return overrides.get(normalize_model(name), overrides.get(model_key(name)))


def resolve_rates(name: str, day: str = '', overrides: dict | None = None) -> dict | None:
    overrides = load_overrides() if overrides is None else overrides
    custom = custom_rates(name, overrides)
    if custom is not None:
        return dict(custom)
    value = CATALOG['models'].get(model_key(name))
    if value is None:
        return None
    rate = dict(value)
    historical = rate.get('historical')
    if historical and day and day < historical['before']:
        rate.update(historical)
    scheduled = rate.get('scheduled')
    if scheduled and day and day >= scheduled['from']:
        rate.update(scheduled)
    return rate


def token_cost(rate: dict, inp: int, out: int, read: int = 0, write: int = 0,
               *, request_input: int | None = None) -> float | None:
    if (read and rate['cache_read'] is None) or (write and rate['cache_write'] is None):
        return None
    # Only request-level records can select a long-context tier. A day's total
    # must never be mistaken for a single large request.
    long = request_input is not None and request_input > rate.get('long_context_threshold', float('inf'))
    multiplier = rate.get('long_input_multiplier', 1) if long else 1
    output_multiplier = rate.get('long_output_multiplier', 1) if long else 1
    return ((inp * rate['input'] + read * (rate['cache_read'] or 0)
             + write * (rate['cache_write'] or 0)) * multiplier
            + out * rate['output'] * output_multiplier) / 1_000_000


def reprice_dataset(dataset: UsageDataset, overrides: dict | None = None) -> tuple[UsageDataset, set[str], set[str]]:
    """Fill missing collector prices; explicit overrides replace reported costs.

    Preserve collector request-level/historical costs and all non-model residuals.
    Splitrail input excludes cached tokens; cached total includes reads + writes.
    """
    overrides = load_overrides() if overrides is None else overrides
    days, resolved, unpriced = [], set(), set()
    for day in dataset.days:
        details, adjustment = [], 0.0
        for detail in day.model_details:
            tokens = detail.tokens
            rate = resolve_rates(detail.name, day.day.isoformat(), overrides)
            custom = custom_rates(detail.name, overrides) is not None
            cost = None
            if rate is not None and (custom or detail.cost == 0):
                read = tokens.cache_read or max(0, tokens.cached - tokens.cache_write)
                cost = token_cost(rate, tokens.input,
                                  tokens.output + tokens.reasoning - tokens.reasoning_in_output,
                                  read, tokens.cache_write)
            if cost is not None:
                adjustment += cost - detail.cost
                detail = replace(detail, cost=cost)
                resolved.add(detail.name)
            elif tokens.total > 0 and (detail.cost == 0 or custom):
                unpriced.add(detail.name)
            details.append(detail)
        days.append(replace(day, cost=max(0, day.cost + adjustment), model_details=tuple(details)))
    return replace(dataset, days=tuple(days)), resolved - unpriced, unpriced
