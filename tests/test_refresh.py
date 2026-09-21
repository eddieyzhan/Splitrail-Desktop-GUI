from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from splitrail_desktop.app import SplitrailApp
from splitrail_desktop.domain import UsageDataset, parse_stats_json
from splitrail_desktop.refresh import (
    FAST_REFRESH_SECONDS,
    NORMAL_REFRESH_SECONDS,
    AdaptiveRefreshPolicy,
    usage_fingerprint,
)


FIXTURES = Path(__file__).parent / "fixtures"


class AdaptiveRefreshPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = parse_stats_json((FIXTURES / "stats_valid.json").read_text(encoding="utf-8"))
        self.policy = AdaptiveRefreshPolicy()

    def test_first_success_establishes_baseline_without_fast_mode(self) -> None:
        decision = self.policy.record_success(self.dataset)

        self.assertEqual(decision.outcome, "baseline")
        self.assertEqual(decision.interval_seconds, NORMAL_REFRESH_SECONDS)
        self.assertTrue(self.policy.has_baseline)

    def test_changed_result_selects_five_minutes(self) -> None:
        self.policy.record_success(self.dataset)
        changed = replace(self.dataset, days=self.dataset.days + (replace(self.dataset.days[0], cost=99.0),))

        decision = self.policy.record_success(changed)

        self.assertTrue(decision.changed)
        self.assertEqual(decision.interval_seconds, FAST_REFRESH_SECONDS)

    def test_repeated_changes_stay_in_fast_mode(self) -> None:
        self.policy.record_success(self.dataset)
        first_change = replace(self.dataset, days=self.dataset.days + (replace(self.dataset.days[0], cost=99.0),))
        second_change = replace(self.dataset, days=self.dataset.days + (replace(self.dataset.days[0], cost=100.0),))

        self.assertEqual(self.policy.record_success(first_change).interval_seconds, FAST_REFRESH_SECONDS)
        self.assertEqual(self.policy.record_success(second_change).interval_seconds, FAST_REFRESH_SECONDS)

    def test_unchanged_result_returns_to_fifteen_minutes(self) -> None:
        self.policy.record_success(self.dataset)
        changed = replace(self.dataset, days=self.dataset.days + (replace(self.dataset.days[0], cost=99.0),))
        self.policy.record_success(changed)

        decision = self.policy.record_success(changed)

        self.assertEqual(decision.outcome, "unchanged")
        self.assertEqual(decision.interval_seconds, NORMAL_REFRESH_SECONDS)

    def test_failed_result_preserves_baseline_and_cadence(self) -> None:
        self.policy.record_success(self.dataset)
        changed = replace(self.dataset, days=self.dataset.days + (replace(self.dataset.days[0], cost=99.0),))
        self.policy.record_success(changed)
        baseline = self.policy.last_successful_fingerprint

        decision = self.policy.record_failure()

        self.assertEqual(decision.outcome, "failed")
        self.assertEqual(decision.interval_seconds, FAST_REFRESH_SECONDS)
        self.assertEqual(self.policy.last_successful_fingerprint, baseline)

    def test_fingerprint_ignores_bookkeeping_and_input_order(self) -> None:
        reordered_days = tuple(reversed(self.dataset.days))
        reordered = UsageDataset(
            reordered_days,
            {"unrelated": 123, **self.dataset.analyzer_conversation_totals},
            ignored_raw_messages=not self.dataset.ignored_raw_messages,
        )

        self.assertEqual(usage_fingerprint(self.dataset), usage_fingerprint(reordered))


class _FakeScheduler:
    _schedule_auto_refresh = SplitrailApp._schedule_auto_refresh
    _cancel_auto_refresh = SplitrailApp._cancel_auto_refresh
    callback = SplitrailApp._auto_refresh_callback
    refresh_all = SplitrailApp.refresh_all

    def __init__(self) -> None:
        self._auto_refresh_enabled = True
        self._closed = False
        self._auto_refresh_after_id = None
        self._auto_refresh_generation = 0
        self._adaptive_refresh = AdaptiveRefreshPolicy()
        self._usage_refreshing = False
        self._quota_refreshing = False
        self.callbacks: list[tuple[str, int, object]] = []
        self.cancelled: list[str] = []
        self.usage_calls = 0
        self.quota_calls = 0

    def after(self, delay_ms: int, callback: object) -> str:
        token = f"timer-{len(self.callbacks)}"
        self.callbacks.append((token, delay_ms, callback))
        return token

    def after_cancel(self, token: str) -> None:
        self.cancelled.append(token)

    def start_usage(self) -> None:
        self.usage_calls += 1

    def start_quota(self) -> None:
        self.quota_calls += 1


class AutomaticTimerTests(unittest.TestCase):
    def test_manual_refresh_replaces_pending_timer_and_stale_callback_is_ignored(self) -> None:
        fake = _FakeScheduler()
        fake._schedule_auto_refresh(900_000)
        stale_generation = fake._auto_refresh_generation
        fake.refresh_usage = fake.start_usage  # type: ignore[attr-defined]
        fake.refresh_quota = fake.start_quota  # type: ignore[attr-defined]

        fake.refresh_all()
        self.assertEqual(fake.cancelled, ["timer-0"])
        self.assertEqual(fake.usage_calls, 1)
        self.assertEqual(fake.quota_calls, 1)

        fake.callback(stale_generation)
        self.assertIsNone(fake._auto_refresh_after_id)

    def test_stale_callback_does_not_consume_current_timer(self) -> None:
        fake = _FakeScheduler()
        fake._schedule_auto_refresh(900_000)
        stale_generation = fake._auto_refresh_generation
        fake._schedule_auto_refresh(300_000)
        current_token = fake._auto_refresh_after_id

        fake.callback(stale_generation)

        self.assertEqual(fake._auto_refresh_after_id, current_token)
        self.assertEqual(len(fake.callbacks), 2)

    def test_callback_during_busy_refresh_keeps_one_future_attempt_without_overlap(self) -> None:
        fake = _FakeScheduler()
        fake._schedule_auto_refresh(900_000)
        generation = fake._auto_refresh_generation
        fake._usage_refreshing = True

        fake.callback(generation)

        self.assertEqual(fake.usage_calls, 0)
        self.assertEqual(fake.quota_calls, 0)
        self.assertEqual(fake._auto_refresh_after_id, "timer-1")
        self.assertEqual(len(fake.callbacks), 2)
        self.assertEqual(fake.callbacks[-1][1], NORMAL_REFRESH_SECONDS * 1000)


if __name__ == "__main__":
    unittest.main()
