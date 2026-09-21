"""Add received requests to the all-tools collector aggregate."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .domain import UsageDataset
from .portable import (combined_usage, data_dir, estimate_cost, merge_events,
                       scan_codex, usage_dataset)
from .runner import CostDiagnostics, LocalCommandError, StatsCommandResult, run_splitrail
from .sync import auto_receive, cached_devices, safe_settings as settings
from .sync_payload import add_devices


def distinct_imported_events(local: dict, imported: list[dict]) -> list[dict]:
    """Use local identities only for exclusion, never to recalculate local usage.

    Splitrail supplies aggregate rows, not request IDs. The portable local scan
    supplies the available normalized Codex/Pi identities. Conflicting imported
    counts are rejected just as in the portable view, including local overlaps.
    """
    local_ids = {event["id"] for event in local["events"]}
    return [event for event in merge_events(local, {"events": imported})
            if event["id"] not in local_ids]


def add_imported_usage(dataset: UsageDataset, events: list[dict]) -> UsageDataset:
    """Keep every collector row and append separately labelled imported rows."""
    imported = usage_dataset(merge_events({"events": events}))
    days = tuple(replace(day, analyzer=f"Imported {day.analyzer}") for day in imported.days)
    conversations = dict(dataset.analyzer_conversation_totals)
    conversations.update({f"Imported {name}": count
                          for name, count in imported.analyzer_conversation_totals.items()})
    hours = tuple(replace(hour, analyzer=f"Imported {hour.analyzer}") for hour in imported.hours)
    return UsageDataset(dataset.days + days, conversations, dataset.ignored_raw_messages, dataset.hours + hours)


def run_combined_usage(directory: Path | None = None) -> StatsCommandResult:
    # Never silently substitute a Codex-only subtotal when the collector fails.
    try:
        local_result = run_splitrail()
    except LocalCommandError as exc:
        raise LocalCommandError(
            f"All tools + imported usage requires the local Splitrail collector. {exc} "
            "For the narrower Codex-only view, choose Usage → Codex across devices (only)."
        ) from exc
    sync_note = auto_receive()
    imported = combined_usage({"events": []}, directory or data_dir())
    # With no imports there is no overlap to check and no reason to rescan logs.
    local = scan_codex() if imported else {"events": [], "diagnostics": {}}
    events = distinct_imported_events(local, imported)
    diagnostics = local_result.cost_diagnostics
    unknown = tuple(sorted(set(diagnostics.unknown_models) |
                           {e["model"] for e in events if estimate_cost(e) is None}))
    lines = (
        f"All local tools retained; added {len(events):,} distinct imported requests; "
        f"excluded {len(imported) - len(events):,} requests found in local Codex/Pi logs.",
        "Local prices use Splitrail with catalogue fallbacks and custom rates. Imported requests use API-equivalent pricing.",
        "Overlap uses available normalized local request IDs; the collector exposes no request identities. "
        "Its existing local scan/dedup accounting is preserved.",
        "Imported conversations are active session-days; imported requests count as AI messages. "
        "User messages and tool calls are available for local tools only.",
    ) + tuple(f"Unpriced imported model: {name}; tokens retained." for name in unknown
              if name not in diagnostics.unknown_models)
    lines += tuple(f"Local identity scan: {name} = {local['diagnostics'][name]}"
                   for name in ("malformed_lines", "invalid_usage", "invalid_pi_lines", "invalid_counter_gaps")
                   if local.get("diagnostics", {}).get(name))
    lines += ((sync_note,) if sync_note else ())
    config = settings(directory)
    dataset = add_devices(add_imported_usage(local_result.dataset, events), cached_devices(directory),
                          config['device'] if config else None)
    return StatsCommandResult(
        dataset,
        CostDiagnostics(unknown, diagnostics.fallback_session_count,
                        diagnostics.other_warning_count + bool(sync_note and ("unavailable" in sync_note or "attention" in sync_note)), diagnostics.lines + lines),
        local_result.exit_code,
    )
