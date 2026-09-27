"""Quota cutoff state machine. Pure decisions; process control lives elsewhere."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .quota import QuotaSnapshot

MAX_QUOTA_AGE_SECONDS = 120
PROCESS_REFRESH_SECONDS = 10


@dataclass(frozen=True)
class QuotaLimits:
    current: float = 95
    after_reset: float = 5

    def __post_init__(self):
        for value in (self.current, self.after_reset):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError('Enter percentages from 0 to 100.')


def fresh_weekly(snapshot: QuotaSnapshot | None, now: datetime) -> bool:
    if snapshot is None or snapshot.stale or snapshot.status != 'fresh':
        return False
    stamp = snapshot.refreshed_at  # generation time alone cannot prove freshness
    window = snapshot.base_weekly
    return bool(stamp and -5 <= (now - stamp).total_seconds() <= MAX_QUOTA_AGE_SECONDS
                and window and window.percent_used is not None and window.resets_at
                and window.resets_at > now)


class QuotaGuard:
    def __init__(self):
        self.armed = False
        self.limits = QuotaLimits()
        self.after_reset = False
        self.reason = ''
        self.last_snapshot: QuotaSnapshot | None = None

    @property
    def limit(self) -> float:
        return self.limits.after_reset if self.after_reset else self.limits.current

    @property
    def blocked(self) -> bool:
        return self.armed and bool(self.reason)

    def arm(self, limits: QuotaLimits, snapshot: QuotaSnapshot | None, now: datetime | None = None):
        current = now or datetime.now(timezone.utc)
        if not fresh_weekly(snapshot, current):
            raise ValueError('Refresh quota first. Arming needs a fresh weekly reading and reset time.')
        self.limits = limits
        self.after_reset = False
        self.reason = ''
        self.last_snapshot = None
        self.armed = True
        self.observe(snapshot, current)

    def disarm(self):
        self.armed = False
        self.reason = ''

    def observe(self, snapshot: QuotaSnapshot | None, now: datetime | None = None):
        if not self.armed or self.blocked:
            return
        current = now or datetime.now(timezone.utc)
        if not fresh_weekly(snapshot, current):
            self.check_age(current)
            return
        previous = self.last_snapshot
        if previous and snapshot.refreshed_at <= previous.refreshed_at:
            # Out-of-order or duplicate cached readings cannot reopen headroom.
            self.check_age(current)
            return
        window = snapshot.base_weekly
        if previous:
            old = previous.base_weekly
            if (window.window_id != old.window_id or
                    window.percent_used < old.percent_used or
                    abs((window.resets_at - old.resets_at).total_seconds()) > 60 or
                    current >= old.resets_at):
                # The reset cap stays in force through all further resets.
                self.after_reset = True
        self.last_snapshot = snapshot
        if window.percent_used >= self.limit:
            self.reason = f'Weekly usage reached {window.percent_used:g}% (limit {self.limit:g}%).'

    def check_age(self, now: datetime | None = None):
        if not self.armed or self.blocked or self.last_snapshot is None:
            return
        current = now or datetime.now(timezone.utc)
        # Allow one polling interval around a scheduled reset to obtain the new
        # window. Never let an expired source timestamp disable the watchdog.
        age = (current - self.last_snapshot.refreshed_at).total_seconds()
        if age > MAX_QUOTA_AGE_SECONDS or age < -5:
            self.reason = 'No fresh quota for two minutes. Stopping protected processes.'
