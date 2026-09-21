from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .domain import DailyUsage, ModelDetail, TokenUsage, UsageDataset


NORMAL_REFRESH_SECONDS = 15 * 60
FAST_REFRESH_SECONDS = 5 * 60


@dataclass(frozen=True)
class RefreshDecision:
    """The cadence selected after one usage refresh result."""

    outcome: str
    interval_seconds: int

    @property
    def changed(self) -> bool:
        return self.outcome == "changed"


def usage_fingerprint(dataset: UsageDataset) -> str:
    """Return a stable fingerprint of the usage values shown by the UI.

    The parsed aggregate is normalized before hashing: analyzer/day rows,
    model-message mappings, and model-detail rows are all ordered by value.
    Diagnostics and parser bookkeeping are intentionally not part of the
    fingerprint, so stderr wording and ignored raw message entries cannot
    switch the refresh cadence.
    """

    normalized = {
        "days": [_daily_usage_key(item) for item in sorted(dataset.days, key=_daily_sort_key)],
    }
    encoded = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AdaptiveRefreshPolicy:
    """Track the last successful usage result and its automatic cadence."""

    def __init__(
        self,
        *,
        normal_interval_seconds: int = NORMAL_REFRESH_SECONDS,
        fast_interval_seconds: int = FAST_REFRESH_SECONDS,
    ) -> None:
        if normal_interval_seconds <= 0 or fast_interval_seconds <= 0:
            raise ValueError("refresh intervals must be positive")
        self.normal_interval_seconds = normal_interval_seconds
        self.fast_interval_seconds = fast_interval_seconds
        self.last_successful_fingerprint: str | None = None
        self.interval_seconds = normal_interval_seconds

    @property
    def has_baseline(self) -> bool:
        return self.last_successful_fingerprint is not None

    def record_success(self, dataset: UsageDataset) -> RefreshDecision:
        current = usage_fingerprint(dataset)
        if self.last_successful_fingerprint is None:
            outcome = "baseline"
            interval = self.normal_interval_seconds
        elif current != self.last_successful_fingerprint:
            outcome = "changed"
            interval = self.fast_interval_seconds
        else:
            outcome = "unchanged"
            interval = self.normal_interval_seconds
        self.last_successful_fingerprint = current
        self.interval_seconds = interval
        return RefreshDecision(outcome, interval)

    def record_failure(self) -> RefreshDecision:
        """Keep both the baseline and currently active cadence unchanged."""

        return RefreshDecision("failed", self.interval_seconds)

    # These aliases read naturally at call sites and keep the helper easy to
    # use in focused tests without exposing mutable implementation details.
    observe_success = record_success
    observe_failure = record_failure


def _daily_sort_key(item: DailyUsage) -> tuple[str, str, str]:
    return (item.day.isoformat(), item.analyzer, _canonical_json(_daily_usage_key(item)))


def _daily_usage_key(item: DailyUsage) -> dict[str, object]:
    return {
        "analyzer": item.analyzer,
        "day": item.day.isoformat(),
        "conversations": item.conversations,
        "user_messages": item.user_messages,
        "ai_messages": item.ai_messages,
        "tokens": _token_key(item.tokens),
        "cost": item.cost,
        "tool_calls": item.tool_calls,
        "model_messages": [[name, count] for name, count in sorted(item.model_messages.items())],
        "model_details": [_model_detail_key(detail) for detail in sorted(item.model_details, key=_model_detail_sort_key)],
    }


def _model_detail_sort_key(detail: ModelDetail) -> tuple[str, str]:
    return (detail.name, _canonical_json(_model_detail_key(detail)))


def _model_detail_key(detail: ModelDetail) -> dict[str, object]:
    return {
        "name": detail.name,
        "messages": detail.messages,
        "tokens": _token_key(detail.tokens),
        "cost": detail.cost,
        "tool_calls": detail.tool_calls,
    }


def _token_key(tokens: TokenUsage) -> dict[str, int]:
    return {
        "input": tokens.input,
        "output": tokens.output,
        "reasoning": tokens.reasoning,
        "cached": tokens.cached,
        "cache_read": tokens.cache_read,
        "cache_write": tokens.cache_write,
        "reasoning_in_output": tokens.reasoning_in_output,
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
