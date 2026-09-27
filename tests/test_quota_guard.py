from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from splitrail_desktop.quota import BankedResetStatus, QuotaSnapshot, QuotaWindow
from splitrail_desktop.quota_guard import QuotaGuard, QuotaLimits


NOW = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
RESET = NOW + timedelta(days=3)


def snapshot(used=75, now=NOW, reset=RESET):
    return QuotaSnapshot(5, now, 'fresh', False, now,
                         (QuotaWindow('weekly-base', 'Week', 'weekly', used, 100-used, reset),),
                         BankedResetStatus('unsupported'))


class QuotaGuardTests(unittest.TestCase):
    def test_cutoff_uses_absolute_percentage_and_latches(self):
        guard = QuotaGuard()
        guard.arm(QuotaLimits(95, 5), snapshot(), NOW)
        guard.observe(snapshot(94.99, NOW + timedelta(seconds=60)), NOW + timedelta(seconds=60))
        self.assertFalse(guard.blocked)
        guard.observe(snapshot(95, NOW + timedelta(seconds=61)), NOW + timedelta(seconds=61))
        self.assertTrue(guard.blocked)
        guard.observe(snapshot(0, NOW + timedelta(seconds=62)), NOW + timedelta(seconds=62))
        self.assertTrue(guard.blocked)
        guard.disarm()
        self.assertFalse(guard.blocked)

    def test_reading_above_cutoff_still_stops(self):
        for used in (95, 96, 97, 100):
            with self.subTest(used=used):
                guard = QuotaGuard()
                guard.arm(QuotaLimits(95, 5), snapshot(94), NOW)
                now = NOW + timedelta(seconds=60)
                guard.observe(snapshot(used, now), now)
                self.assertTrue(guard.blocked)

    def test_unexpected_reset_with_unchanged_deadline_switches_to_five(self):
        guard = QuotaGuard()
        guard.arm(QuotaLimits(), snapshot(), NOW)
        for seconds, used in ((60, 0), (61, 4.9), (62, 5)):
            now = NOW + timedelta(seconds=seconds)
            guard.observe(snapshot(used, now), now)
            self.assertEqual(guard.limit, 5)
            self.assertEqual(guard.blocked, used == 5)

    def test_reset_already_above_fresh_limit_stops_immediately(self):
        guard = QuotaGuard()
        guard.arm(QuotaLimits(), snapshot(), NOW)
        guard.observe(snapshot(6, NOW + timedelta(seconds=60)), NOW + timedelta(seconds=60))
        self.assertTrue(guard.after_reset)
        self.assertTrue(guard.blocked)

    def test_changed_deadline_and_scheduled_reset_detected_without_usage_drop(self):
        for baseline, new_now, new_reset in (
            (snapshot(1), NOW + timedelta(seconds=60), RESET + timedelta(days=1)),
            (snapshot(1, reset=NOW + timedelta(seconds=30)), NOW + timedelta(seconds=60), RESET),
        ):
            with self.subTest(reset=new_reset):
                guard = QuotaGuard()
                guard.arm(QuotaLimits(), baseline, NOW)
                guard.observe(snapshot(5, new_now, new_reset), new_now)
                self.assertTrue(guard.after_reset)
                self.assertTrue(guard.blocked)

    def test_repeated_resets_never_restore_high_limit(self):
        guard = QuotaGuard()
        guard.arm(QuotaLimits(), snapshot(), NOW)
        for index, used in enumerate((0, 3, 0, 4, 0, 5), 1):
            now = NOW + timedelta(seconds=index)
            guard.observe(snapshot(used, now), now)
        self.assertEqual(guard.limit, 5)
        self.assertTrue(guard.blocked)

    def test_cached_out_of_order_read_cannot_invent_reset(self):
        guard = QuotaGuard()
        guard.arm(QuotaLimits(), snapshot(), NOW)
        guard.observe(snapshot(0, NOW - timedelta(seconds=10)), NOW + timedelta(seconds=10))
        self.assertFalse(guard.after_reset)
        guard.observe(snapshot(0), NOW + timedelta(seconds=20))
        self.assertFalse(guard.after_reset)
        guard.check_age(NOW + timedelta(seconds=121))
        self.assertTrue(guard.blocked)

    def test_missing_stale_or_future_quota_cannot_arm(self):
        for value in (None, replace(snapshot(), stale=True), replace(snapshot(), status='auth_required'),
                      replace(snapshot(), refreshed_at=None), snapshot(now=NOW-timedelta(seconds=121)),
                      snapshot(now=NOW+timedelta(minutes=1)), snapshot(reset=NOW),
                      replace(snapshot(), windows=())):
            with self.subTest(value=value), self.assertRaises(ValueError):
                QuotaGuard().arm(QuotaLimits(), value, NOW)

    def test_failed_reads_get_grace_but_do_not_extend_watchdog(self):
        guard = QuotaGuard()
        guard.arm(QuotaLimits(), snapshot(), NOW)
        guard.observe(replace(snapshot(now=NOW+timedelta(seconds=60)), stale=True), NOW+timedelta(seconds=60))
        self.assertFalse(guard.blocked)
        guard.observe(None, NOW+timedelta(seconds=121))
        self.assertTrue(guard.blocked)

    def test_zero_cap_and_arming_above_cap_stop_immediately(self):
        for limit, used in ((0, 0), (95, 96), (100, 100)):
            guard = QuotaGuard()
            guard.arm(QuotaLimits(limit, 0), snapshot(used), NOW)
            self.assertTrue(guard.blocked)

    def test_limit_validation_and_independent_reset_limit(self):
        for value in (float('nan'), float('inf'), -1, 101, True, '95'):
            with self.assertRaises(ValueError):
                QuotaLimits(value, 5)
            with self.assertRaises(ValueError):
                QuotaLimits(95, value)
        self.assertEqual(QuotaLimits(50, 80).after_reset, 80)

    def test_disarmed_guard_never_blocks(self):
        guard = QuotaGuard()
        guard.observe(snapshot(100), NOW)
        guard.check_age(NOW + timedelta(days=1))
        self.assertFalse(guard.blocked)
