"""Deterministic, synthetic data for safe screenshots and trying the interface."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from .domain import DailyUsage, ModelDetail, TokenUsage, UsageDataset
from .runner import CostDiagnostics, StatsCommandResult
from .quota import parse_quota_json
import json


def usage() -> StatsCommandResult:
    rows = []
    today = date.today()
    for offset in range(27):
        day = today - timedelta(days=26 - offset)
        for index, (tool, model) in enumerate((('Claude Code', 'claude-sonnet-4-6'), ('Gemini CLI', 'gemini-2.5-pro'), ('Codex CLI', 'gpt-5.4'))):
            n = 1 + ((offset * 7 + index * 11) % 17)
            tokens = TokenUsage(input=n*8000, cached=n*2300, output=n*1400, cache_read=n*2300)
            cost = round(n * (0.26 + index * 0.12), 2)
            detail = ModelDetail(model, n*3, tokens, cost, n*2)
            rows.append(DailyUsage(tool, day, n, n*2, n*3, tokens, cost, n*2, {model: n*3}, (detail,)))
    return StatsCommandResult(UsageDataset(tuple(rows), {}), CostDiagnostics((), 0, 0, ()), 0)


def quota():
    now = datetime.now(timezone.utc)
    return parse_quota_json(json.dumps({'schemaVersion': 5, 'generatedAt': now.isoformat(), 'providers': [{
        'provider': 'codex', 'state': {'status': 'fresh', 'stale': False, 'refreshedAt': now.isoformat()},
        'windows': [{'id': 'weekly', 'kind': 'weekly', 'label': 'Week', 'percentUsed': 38,
                     'percentRemaining': 62, 'resetsAt': (now + timedelta(days=3, hours=5)).isoformat(),
                     'windowSeconds': 604800}]}]}))
