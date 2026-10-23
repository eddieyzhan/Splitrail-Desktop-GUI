"""Content-free Codex usage ledger, portable between Windows and Linux.

Never reads credentials or exports prompts, responses, paths, or quota data.
Prices are standard API equivalents, not subscription billing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .domain import DailyUsage, HourlyUsage, ModelDetail, TokenUsage, UsageDataset

SCHEMA = "splitrail-codex-usage-v1"
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
MAX_IMPORT_BYTES = 100 * 1024 * 1024
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens")


def data_dir() -> Path:
    if os.environ.get('SPLITRAIL_DATA_DIR'):
        return Path(os.environ['SPLITRAIL_DATA_DIR'])
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) if os.name == "nt" else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    return base / "splitrail-desktop"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize_usage(value: dict) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("Usage must be an object")
    result = {key: value.get(key, 0) for key in FIELDS}
    if any(type(n) is not int or n < 0 for n in result.values()):
        raise ValueError("Token counts must be nonnegative integers")
    if result["cached_input_tokens"] + result["cache_write_input_tokens"] > result["input_tokens"]:
        raise ValueError("Cached input exceeds total input")
    if result["reasoning_output_tokens"] > result["output_tokens"]:
        raise ValueError("Reasoning exceeds output")
    return result


def estimate_cost(event: dict) -> float | None:
    from .pricing import resolve_rates, token_cost
    rate = resolve_rates(event["model"], event["timestamp"][:10])
    if rate is None:
        return None
    u = event["usage"]
    return token_cost(rate,
                      u["input_tokens"] - u["cached_input_tokens"] - u["cache_write_input_tokens"],
                      u["output_tokens"], u["cached_input_tokens"], u["cache_write_input_tokens"],
                      request_input=u["input_tokens"])


def make_event(identity: object, session: str, timestamp: str, model: str | None,
               usage: dict, source: str = "Codex CLI") -> dict:
    stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Usage timestamps must have a timezone")
    return {"id": digest(identity), "session": digest(session),
            "timestamp": stamp.astimezone(timezone.utc).isoformat(),
            "model": model or "unknown", "source": source, "usage": normalize_usage(usage)}


def _scan_state(path: Path, kind: str) -> dict:
    return {"model": None, "session": digest(path.stem if kind == "pi" else path.stem[-36:]),
            "previous": {}, "previous_key": digest(None), "previous_none": True,
            "previous_truthy": False, "modern_started": False, "forked": False,
            "context_started": False}


def _bump(diagnostics, key):
    diagnostics[key] = diagnostics.get(key, 0) + 1


def _remember_total(state, total):
    # Equality/identity use the complete object, but only numeric counter fields
    # survive the line. Extra fields can contain arbitrary session content.
    previous = {key: total.get(key, 0) for key in FIELDS} if total else {}
    if any(type(n) not in (int, float, bool, type(None)) for n in previous.values()):
        raise ValueError("Invalid cumulative counters")
    state.update(previous=previous, previous_key=digest(total),
                 previous_none=total is None, previous_truthy=bool(total))


def _scan_event(identity, state, timestamp, usage, *, model=None, source="Codex CLI"):
    event = make_event(identity, "unused", timestamp, model or state["model"], usage, source)
    event["session"] = state["session"]
    return event


def _codex_record(raw, state, diagnostics, file_session):
    if b'"event_msg"' in raw[:250] and b'"token_count"' not in raw:
        return
    try:
        row = json.loads(raw)
    except (ValueError, UnicodeError):
        _bump(diagnostics, "malformed_lines")
        return
    payload = row.get("payload", {})
    kind = row.get("type")
    if kind in ("turn_context", "session_meta"):
        if kind == "session_meta":
            state["forked"] = bool(payload.get("forked_from_id") or payload.get("parent_thread_id") or
                                   (payload.get("id") and digest(payload["id"]) != file_session))
        else:
            state["context_started"] = True
        state["model"] = payload.get("model") or state["model"]
        return
    recovered = None
    if kind == "token_usage_record":
        state["modern_started"] = True
        usage = payload.get("usage")
        if usage is None:
            _bump(diagnostics, "invalid_usage")
            return
        identity = ["response", payload["response_id"]] if payload.get("response_id") else ["record", row.get("timestamp"), usage]
        if payload.get("thread_id"):
            state["session"] = digest(payload["thread_id"])
        _bump(diagnostics, "modern_records")
    elif kind == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        if not info:
            return
        total = info.get("total_token_usage")
        if state["forked"] and not state["context_started"]:
            _remember_total(state, total)
            _bump(diagnostics, "inherited_counters")
            return
        if total is not None and digest(total) == state["previous_key"]:
            _bump(diagnostics, "repeated_counters")
            return
        if state["modern_started"]:
            _remember_total(state, total)
            return
        usage = info.get("last_token_usage")
        inherited_baseline = state["forked"] and state["previous_none"]
        if inherited_baseline:
            _bump(diagnostics, "inherited_baselines")
        previous = state["previous"]
        if usage is not None and total and not inherited_baseline:
            delta = {key: total.get(key, 0) - previous.get(key, 0) for key in FIELDS}
            if all(value >= 0 for value in delta.values()):
                residual = {key: delta[key] - usage.get(key, 0) for key in FIELDS}
                if all(value >= 0 for value in residual.values()) and (residual["input_tokens"] or residual["output_tokens"]):
                    recovered = residual
        if usage is None and total:
            if inherited_baseline:
                _remember_total(state, total)
                return
            if state["previous_truthy"] and total.get("input_tokens", 0) < previous.get("input_tokens", 0):
                _bump(diagnostics, "counter_resets")
                usage = total
            else:
                usage = {key: max(0, total.get(key, 0) - previous.get(key, 0)) for key in FIELDS}
        _remember_total(state, total)
        if usage is None:
            return
        state["model"] = info.get("model") or state["model"]
        identity = ["legacy", row.get("timestamp"), usage, total]
        _bump(diagnostics, "legacy_records")
    else:
        return
    try:
        event = _scan_event(identity, state, row["timestamp"], usage)
    except (KeyError, TypeError, ValueError):
        _bump(diagnostics, "invalid_usage")
        return
    if recovered:
        try:
            gap = _scan_event(["counter-gap", identity], state, row["timestamp"], recovered)
            if recovered["input_tokens"] > 272_000:
                gap["model"] = "unknown-counter-gap"
            yield "gap", gap
            _bump(diagnostics, "counter_gap_records")
        except ValueError:
            _bump(diagnostics, "invalid_counter_gaps")
    if event["usage"]["input_tokens"] or event["usage"]["output_tokens"]:
        yield "codex", event


def _codex_line(raw, state, diagnostics, file_session):
    try:
        yield from _codex_record(raw, state, diagnostics, file_session)
    except (AttributeError, KeyError, TypeError, ValueError):
        _bump(diagnostics, "invalid_usage")


def _pi_line(raw, state, diagnostics):
    try:
        row = json.loads(raw)
        msg = row.get("message", {})
        if msg.get("provider") != "openai-codex" or not msg.get("usage"):
            return
        u = msg["usage"]
        usage = dict(zip(FIELDS, (u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0), u.get("cacheRead", 0), u.get("cacheWrite", 0), u.get("output", 0), u.get("reasoning", 0))))
        event = _scan_event(["pi", row.get("id"), row.get("timestamp"), usage], state,
                            row["timestamp"], usage, model=msg.get("model"), source="Codex via Pi")
        yield "pi", event
        _bump(diagnostics, "pi_records")
    except (AttributeError, KeyError, ValueError, TypeError):
        _bump(diagnostics, "invalid_pi_lines")


def _scan_sources(root, pi_root, include_pi, cache, diagnostics):
    from .scan_cache import uncached_file

    events = {}
    codex_files = sorted({p for name in ("sessions", "archived_sessions") for p in (root / name).rglob("*.jsonl")})
    sources = [("codex", root, codex_files)]
    if include_pi:
        sources.append(("pi", pi_root, sorted(pi_root.rglob("*.jsonl"))))
    scopes = []
    for kind, source_root, files in sources:
        scope = digest([kind, str(source_root.absolute())])
        scopes.append(scope)
        for path in files:
            if kind == "codex":
                diagnostics["session_files"] += 1
            state = _scan_state(path, kind)
            file_session = state["session"]
            parse = _pi_line if kind == "pi" else lambda raw, state, local: _codex_line(raw, state, local, file_session)
            records = (cache.scan_file(path, scope, kind, state, parse, diagnostics) if cache else
                       uncached_file(path, kind, state, parse, diagnostics))
            try:
                for mode, event in records:
                    if mode == "gap":
                        events.setdefault(event["id"], event)
                        continue
                    if mode == "codex" and event["id"] in events:
                        diagnostics["copied_requests"] += 1
                        if events[event["id"]]["model"] != "unknown":
                            continue
                    events[event["id"]] = event
            except FileNotFoundError:
                diagnostics["scan_missing_files"] += 1
    if cache:
        cache.finish(scopes)
    return events


def scan_codex(root: Path | None = None, pi_root: Path | None = None, *, include_pi: bool = True) -> dict:
    from .scan_cache import CacheUnavailable, ScanCache
    import sqlite3

    root = root or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    pi_root = pi_root if pi_root is not None else Path.home() / ".pi/agent/sessions"
    diagnostics = defaultdict(int, scan_bytes_read=0, scan_probe_bytes=0)
    cache = None
    try:
        cache = ScanCache(data_dir())
        events = _scan_sources(root, pi_root, include_pi, cache, diagnostics)
    except (CacheUnavailable, sqlite3.Error):
        if cache:
            cache.close()
        # Cache trouble must never hide recorded usage. No exception text (which
        # might contain a private path) is copied to diagnostics or exports.
        diagnostics = defaultdict(int, scan_cache_errors=1,
                                  scan_bytes_read=diagnostics["scan_bytes_read"],
                                  scan_probe_bytes=diagnostics["scan_probe_bytes"])
        events = _scan_sources(root, pi_root, include_pi, None, diagnostics)
    finally:
        if cache:
            cache.close()
    from .pricing import CATALOG
    return {"schema": SCHEMA, "exported_at": datetime.now(timezone.utc).isoformat(),
            "pricing_as_of": CATALOG['verified'], "pricing_source": PRICE_SOURCE,
            "diagnostics": dict(diagnostics), "events": sorted(events.values(), key=lambda e: (e["timestamp"], e["id"]))}


def read_export(path: Path) -> dict:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as stream:
        raw = stream.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("Usage export exceeds 100 MB")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or not isinstance(payload.get("events"), list):
        raise ValueError("Choose a Splitrail Codex usage export (.json or .json.gz)")
    clean = []
    for event in payload["events"]:
        if not isinstance(event, dict):
            raise ValueError("Invalid usage event")
        if any(not isinstance(event.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", event[k]) for k in ("id", "session")):
            raise ValueError("Invalid usage identity")
        if not isinstance(event.get("model"), str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", event["model"]):
            raise ValueError("Invalid model name")
        if event.get("source") not in ("Codex CLI", "Codex via Pi"):
            raise ValueError("Invalid usage source")
        normalized = make_event("unused", "unused", event["timestamp"], event["model"], event["usage"], event["source"])
        normalized.update(id=event["id"], session=event["session"])
        clean.append(normalized)
    return {"schema": SCHEMA, "events": clean}


def write_export(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    if path.name.endswith(".gz"):
        raw = gzip.compress(raw, mtime=0)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".usage-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def merge_events(*payloads: dict) -> list[dict]:
    events = {}
    for payload in payloads:
        for event in payload["events"]:
            old = events.get(event["id"])
            if old and old["usage"] != event["usage"]:
                raise ValueError("Conflicting token counts for an existing request; import was not applied")
            if old and old["model"] != "unknown":
                continue
            events[event["id"]] = event
    return sorted(events.values(), key=lambda e: (e["timestamp"], e["id"]))


def import_export(path: Path, directory: Path | None = None) -> int:
    directory = directory or data_dir()
    target = directory / "imported-codex-usage.json.gz"
    incoming = read_export(path)
    previous = read_export(target) if target.exists() else {"events": []}
    events = merge_events(previous, incoming)
    write_export(target, {"schema": SCHEMA, "events": events})
    return len(events) - len(previous["events"])


def combined_usage(local: dict, directory: Path | None = None) -> list[dict]:
    directory = directory or data_dir()
    payloads = [local]
    for filename in ("imported-codex-usage.json.gz",):
        target = directory / filename
        if target.exists():
            payloads.append(read_export(target))
    return merge_events(*payloads)


def usage_dataset(events: list[dict]) -> UsageDataset:
    groups = defaultdict(list)
    sessions = defaultdict(set)
    hourly = {}
    for event in events:
        stamp = datetime.fromisoformat(event["timestamp"]).astimezone()
        groups[(event["source"], stamp.date())].append(event)
        u = event['usage']
        tokens = TokenUsage(input=u['input_tokens']-u['cached_input_tokens'],
                            output=u['output_tokens'], cached=u['cached_input_tokens'],
                            reasoning=u['reasoning_output_tokens'], reasoning_in_output=u['reasoning_output_tokens'],
                            cache_read=u['cached_input_tokens'], cache_write=u['cache_write_input_tokens'])
        key = (event['source'], stamp.date(), stamp.hour)
        previous = hourly.get(key, (TokenUsage(), 0.0))
        hourly[key] = (previous[0]+tokens, previous[1]+(estimate_cost(event) or 0))
        sessions[event["source"]].add(event["session"])
    days = []
    for (source, day), items in sorted(groups.items()):
        by_model = defaultdict(list)
        for event in items:
            by_model[event["model"]].append(event)
        details = []
        for model, model_items in sorted(by_model.items()):
            counts = {key: sum(e["usage"][key] for e in model_items) for key in FIELDS}
            tokens = TokenUsage(input=counts["input_tokens"] - counts["cached_input_tokens"],
                                cached=counts["cached_input_tokens"], output=counts["output_tokens"],
                                reasoning=counts["reasoning_output_tokens"],
                                reasoning_in_output=counts["reasoning_output_tokens"],
                                cache_read=counts["cached_input_tokens"], cache_write=counts["cache_write_input_tokens"])
            details.append(ModelDetail(model, len(model_items), tokens,
                                       sum(estimate_cost(e) or 0 for e in model_items), 0))
        tokens = TokenUsage()
        for detail in details:
            tokens += detail.tokens
        days.append(DailyUsage(source, day, len({e["session"] for e in items}), 0, len(items), tokens,
                               sum(d.cost for d in details), 0, {d.name: d.messages for d in details}, tuple(details)))
    return UsageDataset(tuple(sorted(days, key=lambda d: (d.day, d.analyzer))),
                        {key: len(value) for key, value in sessions.items()},
                        hours=tuple(HourlyUsage(source, day, hour, tokens, cost)
                                    for (source, day, hour), (tokens, cost) in sorted(hourly.items())))


def summary(payload: dict) -> dict:
    events = payload["events"]
    counts = {key: sum(e["usage"][key] for e in events) for key in FIELDS}
    models = {}
    for model in sorted({e["model"] for e in events}):
        subset = [e for e in events if e["model"] == model]
        models[model] = {"requests": len(subset), "tokens": sum(e["usage"]["input_tokens"] + e["usage"]["output_tokens"] for e in subset),
                         "estimated_usd": sum(estimate_cost(e) or 0 for e in subset),
                         "priced": all(estimate_cost(e) is not None for e in subset)}
    return {"exported_at": payload.get("exported_at"), "first_usage": min((e["timestamp"] for e in events), default=None),
            "last_usage": max((e["timestamp"] for e in events), default=None), "requests": len(events),
            "total_tokens": counts["input_tokens"] + counts["output_tokens"], **counts,
            "estimated_usd": sum(estimate_cost(e) or 0 for e in events),
            "unpriced_tokens": sum(e["usage"]["input_tokens"] + e["usage"]["output_tokens"] for e in events if estimate_cost(e) is None),
            "models": models, "diagnostics": payload.get("diagnostics", {})}


def run_portable_usage(*, automatic_sync: bool = True):
    from .runner import CostDiagnostics, StatsCommandResult
    from .sync import auto_receive
    sync_note = auto_receive() if automatic_sync else None
    local = scan_codex()
    events = combined_usage(local)
    unknown = tuple(sorted({e["model"] for e in events if estimate_cost(e) is None}))
    lines = [f"Codex across devices: {len(events):,} unique requests; {local['diagnostics'].get('session_files', 0)} local session files.",
             "Standard API-equivalent USD, including dated GPT-5.6 rates; Fast/priority charges are not inferred.",
             "Only recorded usage is recoverable. User-message and tool-call counts are not collected in this view."]
    if sync_note:
        lines.append(sync_note)
    for name in unknown:
        lines.append(f"Unknown model: {name}. Defaulting to $0; cost estimate is partial.")
    for name in ("malformed_lines", "invalid_usage", "invalid_pi_lines", "counter_resets", "counter_gap_records", "invalid_counter_gaps"):
        if local["diagnostics"].get(name):
            lines.append(f"Log coverage: {name} = {local['diagnostics'][name]}")
    from .sync import cached_devices, safe_settings as settings
    from .sync_payload import add_devices
    config = settings()
    remote = cached_devices()
    # The explicit Codex view includes only Codex rows from mixed collector snapshots.
    remote = {key: {**value, 'days': [row for row in value['days'] if row['tool'] in ('Codex CLI', 'Codex via Pi')],
                       'hours': [row for row in value.get('hours', []) if row['tool'] in ('Codex CLI', 'Codex via Pi')]}
              for key, value in remote.items()}
    from .history import add_usage, record_usage
    local_dataset, history_note = record_usage(usage_dataset(local['events']), local['events'], scope='codex')
    local_ids = {event['id'] for event in local['events']}
    imported = [event for event in events if event['id'] not in local_ids]
    dataset = add_devices(add_usage(local_dataset, usage_dataset(imported)), remote, config['device'] if config else None)
    if history_note:
        lines.append(history_note)
    return StatsCommandResult(dataset, CostDiagnostics(unknown, 0, sum(local["diagnostics"].get(name, 0) for name in ("malformed_lines", "invalid_usage", "invalid_pi_lines", "invalid_counter_gaps")) + bool(sync_note and ("unavailable" in sync_note or "attention" in sync_note)), tuple(lines)), 0)
