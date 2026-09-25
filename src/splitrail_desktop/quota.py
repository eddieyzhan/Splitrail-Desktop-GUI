from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any


class QuotaDataError(ValueError):
    """Raised when a quota or reset-credit response is structurally invalid."""


@dataclass(frozen=True)
class QuotaWindow:
    window_id: str
    label: str
    kind: str
    percent_used: float | None
    percent_remaining: float | None
    resets_at: datetime | None


@dataclass(frozen=True)
class BankedResetStatus:
    status: str
    count: int | None = None
    expiries: tuple[datetime | None, ...] = ()
    expiry_details_complete: bool = False
    observed_at: datetime | None = None
    reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.status != "unsupported"

    @property
    def available(self) -> bool | None:
        return None if self.count is None else self.count > 0


@dataclass(frozen=True)
class QuotaSnapshot:
    schema_version: int
    generated_at: datetime | None
    status: str
    stale: bool
    refreshed_at: datetime | None
    windows: tuple[QuotaWindow, ...]
    banked_resets: BankedResetStatus

    @property
    def base_weekly(self) -> QuotaWindow | None:
        return next((window for window in self.windows if window.kind == "weekly"), None)

    @property
    def named_windows(self) -> tuple[QuotaWindow, ...]:
        base = self.base_weekly
        return tuple(window for window in self.windows if window is not base)


def weekly_pace_percent(resets_at: datetime | None, now: datetime | None = None) -> float | None:
    """Evenly paced allowance used in the seven days before a weekly reset."""
    if resets_at is None:
        return None
    current = now or datetime.now(timezone.utc)
    remaining = (resets_at - current).total_seconds()
    week = timedelta(days=7).total_seconds()
    if remaining <= 0 or remaining > week:
        return None
    return 100 * (1 - remaining / week)


def parse_quota_json(raw: str) -> QuotaSnapshot:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise QuotaDataError(f"quota-axi returned malformed JSON: {exc}") from exc
    return parse_quota_payload(payload)


def parse_quota_payload(payload: Any) -> QuotaSnapshot:
    root = _object(payload, "root")
    schema_version = _required_nonnegative_int(root.get("schemaVersion"), "schemaVersion")
    providers = _list(root.get("providers"), "providers")
    provider = next(
        (
            _object(value, f"providers[{index}]")
            for index, value in enumerate(providers)
            if isinstance(value, dict) and value.get("provider") == "codex"
        ),
        None,
    )
    if provider is None:
        return QuotaSnapshot(
            schema_version=schema_version,
            generated_at=_optional_timestamp(root.get("generatedAt"), "generatedAt"),
            status="unavailable",
            stale=False,
            refreshed_at=None,
            windows=(),
            banked_resets=_quota_axi_banked_reset_status(schema_version),
        )

    state = _object(provider.get("state"), "codex.state")
    status = _string(state.get("status"), "codex.state.status")
    stale = _boolean(state.get("stale", status == "stale"), "codex.state.stale")
    windows = tuple(
        _parse_window(value, index) for index, value in enumerate(_list(provider.get("windows", []), "codex.windows"))
    )

    # Deliberately retain only quota display fields. Account, attempts, OAuth source,
    # errors, credentials, and credits never enter the application model.
    return QuotaSnapshot(
        schema_version=schema_version,
        generated_at=_optional_timestamp(root.get("generatedAt"), "generatedAt"),
        status=status,
        stale=stale,
        refreshed_at=_optional_timestamp(state.get("refreshedAt"), "codex.state.refreshedAt"),
        windows=windows,
        banked_resets=_quota_axi_banked_reset_status(schema_version),
    )


def _parse_window(value: Any, index: int) -> QuotaWindow:
    path = f"codex.windows[{index}]"
    item = _object(value, path)
    used = _optional_percent(item.get("percentUsed"), f"{path}.percentUsed")
    remaining = _optional_percent(item.get("percentRemaining"), f"{path}.percentRemaining")
    if used is None and remaining is not None:
        used = 100 - remaining
    if remaining is None and used is not None:
        remaining = 100 - used
    return QuotaWindow(
        window_id=_string(item.get("id", f"window-{index}"), f"{path}.id"),
        label=_string(item.get("label", item.get("kind", "Quota window")), f"{path}.label"),
        kind=_string(item.get("kind", "unknown"), f"{path}.kind"),
        percent_used=used,
        percent_remaining=remaining,
        resets_at=_optional_timestamp(item.get("resetsAt"), f"{path}.resetsAt"),
    )


def _quota_axi_banked_reset_status(schema_version: int) -> BankedResetStatus:
    return BankedResetStatus(
        status="unsupported",
        reason=f"quota-axi schema v{schema_version} has no authoritative reset-credit field",
    )


def parse_banked_reset_response(payload: Any, observed_at: datetime | None = None) -> BankedResetStatus:
    """Retain only safe display fields from Codex app-server's v2 read response."""
    root = _object(payload, "codex app-server response")
    result = _object(root.get("result"), "codex app-server response.result")
    if "rateLimitResetCredits" not in result:
        return BankedResetStatus(
            status="unsupported",
            reason="installed Codex app-server does not expose rate-limit reset credits",
        )
    value = result["rateLimitResetCredits"]
    if value is None:
        return BankedResetStatus(
            status="unavailable",
            reason="Codex did not provide reset-credit data for this account",
        )

    summary = _object(value, "codex rateLimitResetCredits")
    count = _required_nonnegative_int(summary.get("availableCount"), "codex rateLimitResetCredits.availableCount")
    details = summary.get("credits")
    expiries: list[datetime | None] = []
    available_detail_count = 0
    details_were_provided = details is not None
    if details_were_provided:
        for index, raw_detail in enumerate(_list(details, "codex rateLimitResetCredits.credits")):
            path = f"codex rateLimitResetCredits.credits[{index}]"
            detail = _object(raw_detail, path)
            reset_type = _string(detail.get("resetType"), f"{path}.resetType")
            if reset_type != "codexRateLimits":
                raise QuotaDataError(f"{path}.resetType is not a Codex rate-limit reset")
            status = _string(detail.get("status"), f"{path}.status")
            if status not in ("available", "redeeming", "redeemed", "unknown"):
                raise QuotaDataError(f"{path}.status is not supported")
            if status != "available":
                continue
            available_detail_count += 1
            if "expiresAt" in detail:
                expiries.append(_optional_epoch_timestamp(detail["expiresAt"], f"{path}.expiresAt"))
    if available_detail_count > count:
        raise QuotaDataError("codex reset-credit details exceed availableCount")

    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return BankedResetStatus(
        status="fresh",
        count=count,
        expiries=tuple(expiries),
        expiry_details_complete=(
            details_were_provided and available_detail_count == count and len(expiries) == count
        ),
        observed_at=observed.astimezone(timezone.utc),
    )


def unavailable_banked_resets(reason: str) -> BankedResetStatus:
    return BankedResetStatus(status="unavailable", reason=reason)


def preserve_banked_resets(current: QuotaSnapshot, previous: QuotaSnapshot | None) -> QuotaSnapshot:
    """Keep a prior valid read when a later independent reset-credit read fails."""
    if current.banked_resets.status == "fresh" or previous is None:
        return current
    prior = previous.banked_resets
    if prior.status not in ("fresh", "stale") or prior.count is None:
        return current
    retained = replace(
        prior,
        status="stale",
        reason=current.banked_resets.reason or "the latest reset-credit read was unavailable",
    )
    return replace(current, banked_resets=retained)


def is_banked_reset_status_stale(
    status: BankedResetStatus,
    now: datetime | None = None,
    max_age_seconds: int = 900,
) -> bool:
    if status.status == "stale":
        return True
    if status.status != "fresh" or status.observed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current.astimezone(timezone.utc) - status.observed_at).total_seconds() > max_age_seconds


def format_banked_resets(
    status: BankedResetStatus,
    zone: tzinfo | None = None,
    now: datetime | None = None,
) -> str:
    if status.status == "unsupported":
        return f"Banked resets unsupported: {status.reason or 'read-only source not supported'}"
    if status.status == "unavailable" or status.count is None:
        return f"Banked resets unavailable: {status.reason or 'no authoritative data'}"

    stale = is_banked_reset_status_stale(status, now)
    heading = f"Banked resets available: {status.count}"
    if stale:
        heading += " (stale snapshot)"
    lines = [heading]
    for index, expiry in enumerate(status.expiries, start=1):
        shown = "Does not expire" if expiry is None else format_local_reset(expiry, zone)
        lines.append(f"Reset {index} expiry: {shown}")
    if status.count and not status.expiry_details_complete:
        if status.expiries:
            lines.append(f"Expiry details: {len(status.expiries)} of {status.count} provided by Codex")
        else:
            lines.append("Expiry details: not provided by Codex")
    if stale:
        if status.observed_at is not None:
            lines.append(f"Last valid reset read: {format_local_reset(status.observed_at, zone)}")
        if status.reason:
            lines.append(f"Latest refresh: {status.reason}")
    return "\n".join(lines)


def format_countdown(resets_at: datetime | None, now: datetime | None = None) -> str:
    if resets_at is None:
        return "reset time unavailable"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = math.ceil((resets_at - current.astimezone(timezone.utc)).total_seconds())
    if seconds <= 0:
        return "reset due"
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes:02d}m {seconds:02d}s"
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes}m {seconds:02d}s"


def format_local_reset(value: datetime | None, zone: tzinfo | None = None) -> str:
    if value is None:
        return "Reset date unavailable"
    local = value.astimezone(zone) if zone else value.astimezone()
    return local.strftime("%a %d %b %Y, %I:%M:%S %p %Z").replace(" 0", " ")


def format_refresh_age(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "Refresh time unknown"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = max(0, int((current - value).total_seconds()))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"Updated {seconds // size}{unit} ago"
    return f"Updated {seconds}s ago"


def is_snapshot_stale(snapshot: QuotaSnapshot, now: datetime | None = None, max_age_seconds: int = 900) -> bool:
    if snapshot.stale:
        return True
    observed = snapshot.refreshed_at or snapshot.generated_at
    if observed is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current.astimezone(timezone.utc) - observed).total_seconds() > max_age_seconds


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QuotaDataError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise QuotaDataError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuotaDataError(f"{path} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise QuotaDataError(f"{path} must be a boolean")
    return value


def _optional_percent(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise QuotaDataError(f"{path} must be a percentage from 0 to 100")
    return float(value)


def _optional_nonnegative_int(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not float(value).is_integer():
        raise QuotaDataError(f"{path} must be a non-negative whole number")
    return int(value)


def _required_nonnegative_int(value: Any, path: str) -> int:
    parsed = _optional_nonnegative_int(value, path)
    if parsed is None:
        raise QuotaDataError(f"{path} must be a non-negative whole number")
    return parsed


def _optional_timestamp(value: Any, path: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QuotaDataError(f"{path} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QuotaDataError(f"{path} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise QuotaDataError(f"{path} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_epoch_timestamp(value: Any, path: str) -> datetime | None:
    if value is None:
        return None
    seconds = _required_nonnegative_int(value, path)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise QuotaDataError(f"{path} must be a valid Unix timestamp in seconds") from exc
