"""Download public prices only; never send usage, model names or credentials."""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.request
from datetime import date

from .portable import write_export
from .pricing import CATALOG, validate_rates

CATALOG_URL = ('https://raw.githubusercontent.com/eddieyzhan/Splitrail-Desktop-GUI/'
               'main/src/splitrail_desktop/model_prices.json')
MAX_CATALOG_BYTES = 1024 * 1024
CHECK_INTERVAL = 24 * 60 * 60
RETRY_INTERVAL = 60 * 60
_lock = threading.Lock()
_attempts: dict[str, float] = {}


def _identifier(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}', value))


def validate_catalog(value: dict) -> dict:
    """Reject incompatible or malformed data before changing prices or the cache."""
    if (not isinstance(value, dict) or type(value.get('version')) is not int or
            value['version'] != 1 or value.get('currency') != 'USD' or
            value.get('unit') != 'per million tokens'):
        raise ValueError('Unsupported price catalogue')
    value = json.loads(json.dumps(value, allow_nan=False))
    if not isinstance(value.get('verified'), str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value['verified']):
        raise ValueError('Invalid verification date')
    date.fromisoformat(value['verified'])
    models = value['models']
    if not isinstance(models, dict) or not 1 <= len(models) <= 5000:
        raise ValueError('Invalid catalogue models')
    for name, rates in models.items():
        if not _identifier(name) or not isinstance(rates, dict):
            raise ValueError('Invalid model price')
        if not isinstance(rates.get('provider'), str) or not 1 <= len(rates['provider']) <= 100:
            raise ValueError('Invalid model provider')
        rates.update(validate_rates(rates))
        for field in ('long_input_multiplier', 'long_output_multiplier'):
            if field in rates and (type(rates[field]) not in (int, float) or
                                   not math.isfinite(rates[field]) or not 1 <= rates[field] <= 100):
                raise ValueError('Invalid context multiplier')
        if 'long_context_threshold' in rates and (
                type(rates['long_context_threshold']) is not int or rates['long_context_threshold'] <= 0):
            raise ValueError('Invalid context threshold')
        for field, boundary in (('historical', 'before'), ('scheduled', 'from')):
            if field in rates:
                stage = rates[field]
                if not isinstance(stage, dict):
                    raise ValueError('Invalid dated prices')
                if set(stage) - {boundary, 'input', 'output', 'cache_read', 'cache_write'}:
                    raise ValueError('Unsupported dated price fields')
                date.fromisoformat(stage[boundary])
                normalized = validate_rates({**rates, **stage})
                stage.update({key: normalized[key] for key in stage if key in normalized})
    for field in ('aliases', 'unpriced', 'sources'):
        if not isinstance(value.get(field), dict) or len(value[field]) > 5000:
            raise ValueError('Invalid catalogue metadata')
        for name, target in value[field].items():
            if not _identifier(name) or not isinstance(target, str) or len(target) > 1000:
                raise ValueError('Invalid catalogue metadata')
            if field == 'aliases' and target not in models:
                raise ValueError('Unknown price alias')
    return value


def _apply(catalog: dict) -> bool:
    if catalog['verified'] < CATALOG['verified'] or catalog == CATALOG:
        return False
    # Replace nested mappings together; readers already holding one can finish.
    CATALOG.update(catalog)
    return True


def refresh_prices(*, allow_network: bool = True) -> bool:
    """Load validated cached prices and check GitHub at most once per day.

    Called on the usage worker, never on Qt's UI thread for a network request.
    Failed downloads retain the last valid catalogue and retry after an hour.
    """
    from .portable import data_dir
    path = data_dir() / 'price-catalog.json'
    with _lock:
        changed, checked = False, 0.0
        try:
            if path.stat().st_size > MAX_CATALOG_BYTES:
                raise ValueError('Price cache too large')
            cached = json.loads(path.read_text(encoding='utf-8'))
            catalog = validate_catalog(cached['catalog'])
            checked = cached['checked_at']
            if type(checked) not in (int, float) or not math.isfinite(checked):
                raise ValueError('Invalid price cache timestamp')
            if catalog['verified'] < CATALOG['verified']:
                checked = 0.0
            changed = _apply(catalog)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            checked = 0.0
        if not allow_network:
            return changed
        now = time.time()
        key = str(path)
        if 0 <= now - checked < CHECK_INTERVAL or 0 <= now - _attempts.get(key, 0) < RETRY_INTERVAL:
            return changed
        _attempts[key] = now
        try:
            request = urllib.request.Request(CATALOG_URL, headers={
                'Accept': 'application/json', 'User-Agent': 'Splitrail-Desktop-Prices/1'})
            # No GitHub CLI, tokens, account, usage, or model-specific requests.
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read(MAX_CATALOG_BYTES + 1)
            if len(raw) > MAX_CATALOG_BYTES:
                raise ValueError('Price catalogue too large')
            catalog = validate_catalog(json.loads(raw))
            if catalog['verified'] < CATALOG['verified']:
                raise ValueError('Price catalogue is older than local prices')
            write_export(path, {'checked_at': now, 'catalog': catalog})
            return _apply(catalog) or changed
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            return changed
