from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from splitrail_desktop.quota import (
    format_banked_resets,
    QuotaDataError,
    format_countdown,
    format_local_reset,
    format_refresh_age,
    is_snapshot_stale,
    parse_banked_reset_response,
    parse_quota_json,
    parse_quota_payload,
    preserve_banked_resets,
    unavailable_banked_resets,
    weekly_pace_percent,
)


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class QuotaParsingTests(unittest.TestCase):
    def test_weekly_pace_uses_the_reset_timestamp(self) -> None:
        reset = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
        self.assertEqual(weekly_pace_percent(reset, reset - timedelta(days=7)), 0)
        self.assertEqual(weekly_pace_percent(reset, reset - timedelta(days=3, hours=12)), 50)
        self.assertAlmostEqual(weekly_pace_percent(reset, reset - timedelta(days=1)), 100 * 6 / 7)
        self.assertIsNone(weekly_pace_percent(None, reset))
        self.assertIsNone(weekly_pace_percent(reset, reset))
        self.assertIsNone(weekly_pace_percent(reset, reset - timedelta(days=8)))

    def test_refresh_age_units_and_missing_or_future_timestamps(self) -> None:
        now = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        for seconds, expected in ((0, "0s"), (12, "12s"), (59, "59s"),
                                  (60, "1m"), (3599, "59m"), (3600, "1h"),
                                  (86399, "23h"), (86400, "1d")):
            with self.subTest(seconds=seconds):
                self.assertEqual(format_refresh_age(now - timedelta(seconds=seconds), now), f"Updated {expected} ago")
        self.assertEqual(format_refresh_age(None, now), "Refresh time unknown")
        self.assertEqual(format_refresh_age(now + timedelta(seconds=30), now), "Updated 0s ago")
        self.assertEqual(format_refresh_age(now.astimezone(ZoneInfo("Australia/Sydney")), now), "Updated 0s ago")

    def test_separates_base_weekly_from_named_model_windows(self) -> None:
        snapshot = parse_quota_json(fixture("quota_fresh.json"))

        self.assertEqual(snapshot.schema_version, 5)
        self.assertEqual(snapshot.status, "fresh")
        self.assertFalse(snapshot.stale)
        self.assertIsNotNone(snapshot.base_weekly)
        assert snapshot.base_weekly is not None
        self.assertEqual(snapshot.base_weekly.percent_used, 6)
        self.assertEqual(snapshot.base_weekly.percent_remaining, 94)
        self.assertEqual([window.label for window in snapshot.named_windows], ["Example model week"])
        self.assertFalse(hasattr(snapshot, "account"))

    def test_credits_are_not_relabelled_as_banked_resets(self) -> None:
        snapshot = parse_quota_json(fixture("quota_fresh.json"))

        self.assertFalse(snapshot.banked_resets.supported)
        self.assertIsNone(snapshot.banked_resets.available)
        self.assertIsNone(snapshot.banked_resets.count)

    def test_unknown_future_quota_field_is_not_treated_as_authoritative(self) -> None:
        payload = json.loads(fixture("quota_fresh.json"))
        payload["schemaVersion"] = 6
        payload["providers"][0]["bankedResets"] = {"available": True, "count": 99}

        snapshot = parse_quota_payload(payload)

        self.assertEqual(snapshot.schema_version, 6)
        self.assertFalse(snapshot.banked_resets.supported)
        self.assertIsNone(snapshot.banked_resets.count)
        self.assertIn("schema v6", snapshot.banked_resets.reason or "")

    def test_missing_quota_schema_version_is_rejected(self) -> None:
        payload = json.loads(fixture("quota_fresh.json"))
        del payload["schemaVersion"]

        with self.assertRaisesRegex(QuotaDataError, "schemaVersion"):
            parse_quota_payload(payload)

    def test_absent_provider_is_an_honest_unavailable_state(self) -> None:
        snapshot = parse_quota_json(fixture("quota_absent.json"))

        self.assertEqual(snapshot.status, "unavailable")
        self.assertEqual(snapshot.windows, ())

    def test_auth_required_is_data_not_a_parse_failure(self) -> None:
        snapshot = parse_quota_json(fixture("quota_auth_required.json"))

        self.assertEqual(snapshot.status, "auth_required")
        self.assertIsNone(snapshot.base_weekly)

    def test_stale_source_remains_marked_stale(self) -> None:
        snapshot = parse_quota_json(fixture("quota_stale.json"))

        self.assertTrue(snapshot.stale)
        self.assertTrue(is_snapshot_stale(snapshot, datetime(2026, 8, 19, tzinfo=timezone.utc)))

    def test_fresh_source_becomes_stale_after_age_limit(self) -> None:
        snapshot = parse_quota_json(fixture("quota_fresh.json"))

        self.assertFalse(is_snapshot_stale(snapshot, datetime(2026, 8, 19, 13, 50, tzinfo=timezone.utc)))
        self.assertTrue(is_snapshot_stale(snapshot, datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)))

    def test_rejects_invalid_percent_without_exposing_payload(self) -> None:
        with self.assertRaisesRegex(QuotaDataError, "percentage from 0 to 100"):
            parse_quota_payload(
                {
                    "schemaVersion": 5,
                    "providers": [
                        {
                            "provider": "codex",
                            "windows": [{"id": "w", "kind": "weekly", "label": "week", "percentUsed": 150}],
                            "state": {"status": "fresh", "stale": False},
                        }
                    ]
                }
            )


class BankedResetParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observed = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)

    def test_current_codex_read_shape_retains_only_count_and_expiry(self) -> None:
        status = parse_banked_reset_response(
            json.loads(fixture("codex_rate_limits_one.json")),
            self.observed,
        )

        self.assertEqual(status.status, "fresh")
        self.assertEqual(status.count, 1)
        self.assertTrue(status.available)
        self.assertEqual(status.expiries, (datetime(2026, 9, 21, 0, 19, 9, tzinfo=timezone.utc),))
        self.assertTrue(status.expiry_details_complete)
        self.assertEqual(status.observed_at, self.observed)
        self.assertFalse(hasattr(status, "id"))
        self.assertFalse(hasattr(status, "account"))

    def test_zero_is_an_exact_supported_count(self) -> None:
        status = parse_banked_reset_response(
            json.loads(fixture("codex_rate_limits_zero.json")),
            self.observed,
        )

        self.assertEqual(status.count, 0)
        self.assertFalse(status.available)
        self.assertEqual(status.expiries, ())
        self.assertTrue(status.expiry_details_complete)

    def test_multiple_resets_keep_every_authoritative_expiry(self) -> None:
        status = parse_banked_reset_response(
            json.loads(fixture("codex_rate_limits_multiple.json")),
            self.observed,
        )

        self.assertEqual(status.count, 3)
        self.assertEqual(
            status.expiries,
            (
                datetime(2026, 9, 21, 0, 19, 9, tzinfo=timezone.utc),
                datetime(2026, 9, 22, 0, 19, 9, tzinfo=timezone.utc),
                datetime(2026, 9, 23, 0, 19, 9, tzinfo=timezone.utc),
            ),
        )
        self.assertTrue(status.expiry_details_complete)

    def test_count_only_shape_does_not_invent_expiries(self) -> None:
        status = parse_banked_reset_response(
            json.loads(fixture("codex_rate_limits_count_only.json")),
            self.observed,
        )

        self.assertEqual(status.count, 2)
        self.assertEqual(status.expiries, ())
        self.assertFalse(status.expiry_details_complete)

    def test_absent_and_null_data_are_distinguished_honestly(self) -> None:
        absent = parse_banked_reset_response(json.loads(fixture("codex_rate_limits_absent.json")))
        unavailable = parse_banked_reset_response({"id": 2, "result": {"rateLimitResetCredits": None}})

        self.assertEqual(absent.status, "unsupported")
        self.assertIn("does not expose", absent.reason or "")
        self.assertEqual(unavailable.status, "unavailable")
        self.assertIn("did not provide", unavailable.reason or "")

    def test_malformed_expiry_is_rejected_without_payload_values_in_error(self) -> None:
        with self.assertRaisesRegex(QuotaDataError, "must be a non-negative whole number") as raised:
            parse_banked_reset_response(json.loads(fixture("codex_rate_limits_malformed.json")))

        self.assertNotIn("not-a-unix-timestamp", str(raised.exception))

    def test_failed_refresh_preserves_prior_valid_reset_snapshot_as_stale(self) -> None:
        base = parse_quota_json(fixture("quota_fresh.json"))
        valid = parse_banked_reset_response(json.loads(fixture("codex_rate_limits_one.json")), self.observed)
        previous = replace(base, banked_resets=valid)
        current = replace(base, banked_resets=unavailable_banked_resets("latest read failed"))

        merged = preserve_banked_resets(current, previous)

        self.assertEqual(merged.banked_resets.status, "stale")
        self.assertEqual(merged.banked_resets.count, 1)
        self.assertEqual(merged.banked_resets.expiries, valid.expiries)
        self.assertEqual(merged.banked_resets.reason, "latest read failed")


class ResetFormattingTests(unittest.TestCase):
    def test_countdown_is_live_and_human_readable(self) -> None:
        reset = datetime(2026, 8, 21, 5, 28, 29, tzinfo=timezone.utc)

        self.assertEqual(
            format_countdown(reset, datetime(2026, 8, 19, 5, 28, 28, tzinfo=timezone.utc)),
            "2d 0h 00m 01s",
        )
        self.assertEqual(format_countdown(reset, reset), "reset due")
        self.assertEqual(format_countdown(None), "reset time unavailable")

    def test_reset_is_rendered_in_exact_local_time(self) -> None:
        reset = datetime(2026, 8, 21, 5, 28, 29, tzinfo=timezone.utc)

        shown = format_local_reset(reset, ZoneInfo("Australia/Sydney"))

        self.assertEqual(shown, "Fri 21 Aug 2026, 3:28:29 PM AEST")

    def test_banked_reset_count_and_all_expiries_use_local_time(self) -> None:
        observed = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
        status = parse_banked_reset_response(
            json.loads(fixture("codex_rate_limits_multiple.json")),
            observed,
        )

        shown = format_banked_resets(status, ZoneInfo("Australia/Sydney"), observed)

        self.assertEqual(
            shown,
            "Banked resets available: 3\n"
            "Reset 1 expiry: Mon 21 Sep 2026, 10:19:09 AM AEST\n"
            "Reset 2 expiry: Tue 22 Sep 2026, 10:19:09 AM AEST\n"
            "Reset 3 expiry: Wed 23 Sep 2026, 10:19:09 AM AEST",
        )

    def test_zero_and_stale_states_are_visible_without_guessing(self) -> None:
        observed = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
        zero = parse_banked_reset_response(json.loads(fixture("codex_rate_limits_zero.json")), observed)
        old = parse_banked_reset_response(json.loads(fixture("codex_rate_limits_one.json")), observed)

        self.assertEqual(format_banked_resets(zero, now=observed), "Banked resets available: 0")
        stale = format_banked_resets(old, now=datetime(2026, 9, 3, 10, 16, tzinfo=timezone.utc))
        self.assertIn("Banked resets available: 1 (stale snapshot)", stale)
        self.assertIn("Last valid reset read:", stale)


if __name__ == "__main__":
    unittest.main()
