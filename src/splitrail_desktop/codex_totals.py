"""Use the request ledger for Codex tokens in every local dashboard and upload."""
from __future__ import annotations

from dataclasses import replace

from .domain import TokenUsage, UsageDataset
from .portable import scan_codex, usage_dataset


def reconcile_codex(dataset: UsageDataset, *, preserve_history: bool = False) -> tuple[UsageDataset, str | None]:
    def finish(result, warning=None, events=None):
        if preserve_history:
            from .history import record_usage
            result, note = record_usage(result, events)
            warning = ' '.join(part for part in (warning, note) if part) or None
        return result, warning
    # Pi is already included by the collector as Pi Agent. Adding Codex via Pi
    # here would count the same requests twice in All tools.
    try:
        local = scan_codex(include_pi=False)
    except OSError:
        return finish(dataset, 'Codex logs could not be read; showing saved or collector totals.')
    diagnostics = local['diagnostics']
    if not diagnostics.get('session_files'):
        return finish(dataset)
    if any(diagnostics.get(key) for key in ('malformed_lines', 'invalid_usage', 'invalid_counter_gaps')):
        return finish(dataset, 'Some Codex usage records could not be read; showing collector totals.')
    if not local['events'] and any(row.analyzer == 'Codex CLI' and row.tokens.total for row in dataset.days):
        return finish(dataset, 'Codex request history is unavailable; showing collector totals.')
    if preserve_history:
        from .history import available
        if available():
            # Historical request totals and prices are already recorded. Only
            # new identities need pricing; retain collector activity metadata.
            return finish(dataset, events=local['events'])
    native = usage_dataset(local['events'])
    native_days = {row.day: row for row in native.days}
    rows = []
    for row in dataset.days:
        if row.analyzer != 'Codex CLI':
            rows.append(row)
            continue
        ledger = native_days.pop(row.day, None)
        details = {detail.name: detail for detail in ledger.model_details} if ledger else {}
        # Retain collector message/tool metadata while replacing its token and
        # cost values, which miss modern records and repeat legacy UI counters.
        for detail in row.model_details:
            if detail.name in details:
                details[detail.name] = replace(details[detail.name], messages=detail.messages,
                                               tool_calls=detail.tool_calls)
            else:
                details[detail.name] = replace(detail, tokens=TokenUsage(), cost=0)
        rows.append(replace(row, tokens=ledger.tokens if ledger else TokenUsage(),
                            cost=ledger.cost if ledger else 0,
                            model_details=tuple(details[name] for name in sorted(details))))
    rows.extend(native_days.values())
    conversations = dict(dataset.analyzer_conversation_totals)
    if 'Codex CLI' not in conversations and native.days:
        conversations['Codex CLI'] = native.analyzer_conversation_totals['Codex CLI']
    result = replace(dataset, days=tuple(sorted(rows, key=lambda row: (row.day, row.analyzer))),
                     analyzer_conversation_totals=conversations,
                     hours=tuple(row for row in dataset.hours if row.analyzer != 'Codex CLI') + native.hours)
    return finish(result, events=local['events'])
