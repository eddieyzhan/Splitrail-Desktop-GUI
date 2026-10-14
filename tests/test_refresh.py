from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

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

    def test_unchanged_result_returns_to_one_hour(self) -> None:
        self.policy.record_success(self.dataset)
        changed = replace(self.dataset, days=self.dataset.days + (replace(self.dataset.days[0], cost=99.0),))
        self.policy.record_success(changed)

        decision = self.policy.record_success(changed)

        self.assertEqual(decision.outcome, "unchanged")
        self.assertEqual(decision.interval_seconds, NORMAL_REFRESH_SECONDS)
        self.assertEqual(NORMAL_REFRESH_SECONDS, 3600)

    def test_activity_wakes_idle_policy_without_changing_usage_baseline(self):
        self.policy.record_success(self.dataset)
        baseline = self.policy.last_successful_fingerprint
        self.assertEqual(self.policy.record_activity().interval_seconds, 300)
        self.assertEqual(self.policy.last_successful_fingerprint, baseline)
        self.assertEqual(self.policy.record_success(self.dataset).interval_seconds, 3600)

    def test_activity_probe_notices_append_and_archive_without_opening_logs(self):
        import tempfile
        from unittest.mock import patch
        from splitrail_desktop.refresh import codex_activity_stamp
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / 'sessions'
            sessions.mkdir()
            log = sessions / 'test.jsonl'
            log.write_text('synthetic')
            with patch.object(Path, 'open', side_effect=AssertionError('Probe must not read log contents')):
                first = codex_activity_stamp(root, root / 'pi')
                self.assertEqual(first, codex_activity_stamp(root, root / 'pi'))
            log.write_text('synthetic appended')
            second = codex_activity_stamp(root, root / 'pi')
            self.assertNotEqual(first, second)
            (root / 'archived_sessions').mkdir()
            log.rename(root / 'archived_sessions/test.jsonl')
            self.assertNotEqual(second, codex_activity_stamp(root, root / 'pi'))

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



if __name__ == "__main__":
    unittest.main()
