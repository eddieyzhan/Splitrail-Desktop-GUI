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
PRICE_DATE = "2026-09-22"
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
MAX_IMPORT_BYTES = 100 * 1024 * 1024
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens")


def data_dir() -> Path:
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


def scan_codex(root: Path | None = None, pi_root: Path | None = None) -> dict:
    root = root or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    events: dict[str, dict] = {}
    diagnostics = defaultdict(int)
    files = sorted({p for name in ("sessions", "archived_sessions") for p in (root / name).rglob("*.jsonl")})
    for path in files:
        diagnostics["session_files"] += 1
        model = None
        session = path.stem[-36:]
        previous = None
        modern_started = False
        forked = False
        context_started = False
        # Bound at the observed file size so an active session cannot extend the scan.
        remaining = path.stat().st_size
        with path.open("rb") as stream:
            while remaining > 0:
                line = stream.readline(remaining)
                remaining -= len(line)
                if not line:
                    break
                # Large response/tool records need not be parsed or retained.
                if not any(k in line[:250] for k in (b'"turn_context"', b'"session_meta"', b'"event_msg"', b'"token_usage_record"')):
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    diagnostics["malformed_lines"] += 1
                    continue
                payload = row.get("payload", {})
                kind = row.get("type")
                if kind in ("turn_context", "session_meta"):
                    if kind == "session_meta":
                        forked = bool(payload.get("forked_from_id") or payload.get("parent_thread_id") or
                                      (payload.get("id") and payload["id"] != path.stem[-36:]))
                    else:
                        context_started = True
                    model = payload.get("model") or model
                    continue
                identity = None
                recovered = None
                if kind == "token_usage_record":
                    modern_started = True
                    usage = payload.get("usage")
                    if usage is None:
                        diagnostics["invalid_usage"] += 1
                        continue
                    identity = ["response", payload["response_id"]] if payload.get("response_id") else ["record", row.get("timestamp"), usage]
                    session = payload.get("thread_id") or session
                    diagnostics["modern_records"] += 1
                elif kind == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info")
                    if not info:
                        continue
                    total = info.get("total_token_usage")
                    # Older Codex forks replay the parent's counters with new
                    # timestamps, before the child's first turn_context.
                    # They are history, not requests billed to the new thread.
                    if forked and not context_started:
                        previous = total
                        diagnostics["inherited_counters"] += 1
                        continue
                    if total is not None and total == previous:
                        diagnostics["repeated_counters"] += 1
                        continue
                    if modern_started:
                        previous = total
                        continue
                    usage = info.get("last_token_usage")
                    if usage is not None and total and total != previous:
                        delta = {key: total.get(key, 0) - (previous or {}).get(key, 0) for key in FIELDS}
                        if all(value >= 0 for value in delta.values()):
                            residual = {key: delta[key] - usage.get(key, 0) for key in FIELDS}
                            if all(value >= 0 for value in residual.values()) and (residual["input_tokens"] or residual["output_tokens"]):
                                recovered = residual
                    if usage is None and total:
                        if previous and total.get("input_tokens", 0) < previous.get("input_tokens", 0):
                            diagnostics["counter_resets"] += 1
                            usage = total
                        else:
                            usage = {key: max(0, total.get(key, 0) - (previous or {}).get(key, 0)) for key in FIELDS}
                    previous = total
                    if usage is None:
                        continue
                    model = info.get("model") or model
                    # Copied fork histories retain event timestamps and counters.
                    # Do not use the new file/session id as request identity.
                    identity = ["legacy", row.get("timestamp"), usage, total]
                    diagnostics["legacy_records"] += 1
                else:
                    continue
                try:
                    event = make_event(identity, session, row["timestamp"], model, usage)
                except (KeyError, TypeError, ValueError):
                    diagnostics["invalid_usage"] += 1
                    continue
                if recovered:
                    try:
                        gap = make_event(["counter-gap", identity], session, row["timestamp"], model, recovered)
                        # Missing request boundaries can affect long-context rates.
                        if recovered["input_tokens"] > 272_000:
                            gap["model"] = "unknown-counter-gap"
                        events.setdefault(gap["id"], gap)
                        diagnostics["counter_gap_records"] += 1
                    except ValueError:
                        diagnostics["invalid_counter_gaps"] += 1
                if not event["usage"]["input_tokens"] and not event["usage"]["output_tokens"]:
                    continue
                if event["id"] in events:
                    diagnostics["copied_requests"] += 1
                    if events[event["id"]]["model"] != "unknown":
                        continue
                events[event["id"]] = event
    # Codex-authenticated requests made through Pi also use the same allowance.
    pi_root = pi_root if pi_root is not None else Path.home() / ".pi/agent/sessions"
    for path in sorted(pi_root.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    msg = row.get("message", {})
                    if msg.get("provider") != "openai-codex" or not msg.get("usage"):
                        continue
                    u = msg["usage"]
                    usage = dict(zip(FIELDS, (u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0), u.get("cacheRead", 0), u.get("cacheWrite", 0), u.get("output", 0), u.get("reasoning", 0))))
                    event = make_event(["pi", row.get("id"), row.get("timestamp"), usage], path.stem,
                                       row["timestamp"], msg.get("model"), usage, "Codex via Pi")
                    events[event["id"]] = event
                    diagnostics["pi_records"] += 1
                except (KeyError, ValueError, TypeError):
                    diagnostics["invalid_pi_lines"] += 1
    return {"schema": SCHEMA, "exported_at": datetime.now(timezone.utc).isoformat(),
            "pricing_as_of": PRICE_DATE, "pricing_source": PRICE_SOURCE,
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


def run_portable_usage():
    from .runner import CostDiagnostics, StatsCommandResult
    from .sync import auto_receive
    sync_note = auto_receive()
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
    dataset = add_devices(usage_dataset(events), remote, config['device'] if config else None)
    return StatsCommandResult(dataset, CostDiagnostics(unknown, 0, sum(local["diagnostics"].get(name, 0) for name in ("malformed_lines", "invalid_usage", "invalid_pi_lines", "invalid_counter_gaps")) + bool(sync_note and ("unavailable" in sync_note or "attention" in sync_note)), tuple(lines)), 0)
