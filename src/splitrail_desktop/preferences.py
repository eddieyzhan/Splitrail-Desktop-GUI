"""Small local-only UI preferences, separate from sync data."""
from __future__ import annotations
import json
from . import portable


def load() -> dict:
    path = portable.data_dir() / 'preferences.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(**changes) -> None:
    value = load()
    value.update(changes)
    portable.write_export(portable.data_dir() / 'preferences.json', value)
