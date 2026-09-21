"""Presentation helpers shared by the desktop interface and tests."""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import date
from .domain import UsageDataset, UsageTotal, SeriesPoint, daily_series, daily_data_rows

@dataclass(frozen=True)
class ChartBucket:
    label: str
    total: UsageTotal

def cycle_period_index(current: int, direction: int, period_count: int) -> int:
    if period_count <= 0:
        raise ValueError("period_count must be positive")
    return (current + direction) % period_count


def hourly_chart_buckets(dataset: UsageDataset, day: date) -> list[ChartBucket]:
    buckets = [ChartBucket(f'{hour:02d}:00', UsageTotal()) for hour in range(24)]
    for row in dataset.hours:
        if row.day == day:
            target = buckets[row.hour].total
            target.tokens += row.tokens
            target.cost += row.cost
    return buckets


def chart_series(dataset: UsageDataset, start: date, end: date) -> list[SeriesPoint]:
    if (end - start).days < 62:
        return daily_series(dataset, start, end)
    return [SeriesPoint(row.day, row.total) for row in reversed(daily_data_rows(dataset, start, end))]


def build_chart_buckets(points: list[SeriesPoint]) -> list[ChartBucket]:
    if not points:
        return []
    span_days = (points[-1].day - points[0].day).days + 1
    if span_days <= 62:
        return [ChartBucket(point.day.strftime("%d %b"), point.total) for point in points]
    monthly = span_days > 730
    buckets: dict[tuple[int, int], UsageTotal] = {}
    labels: dict[tuple[int, int], str] = {}
    for point in points:
        if monthly:
            key = (point.day.year, point.day.month)
            label = point.day.strftime("%b %Y")
        else:
            monday = point.day.fromordinal(point.day.toordinal() - point.day.weekday())
            key = (monday.year, monday.toordinal())
            label = monday.strftime("%d %b")
        target = buckets.setdefault(key, UsageTotal())
        target.tokens = target.tokens + point.total.tokens
        target.cost += point.total.cost
        target.conversations += point.total.conversations
        target.user_messages += point.total.user_messages
        target.ai_messages += point.total.ai_messages
        target.tool_calls += point.total.tool_calls
        labels[key] = label
    return [ChartBucket(labels[key], buckets[key]) for key in sorted(buckets)]


def chart_value(total: UsageTotal, metric: str) -> float:
    if metric == "Estimated cost (USD)":
        return total.cost
    if metric == "Conversations":
        return float(total.conversations)
    if metric == "Messages":
        return float(total.messages)
    return float(total.tokens.total)


def nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    normalized = value / magnitude
    step = 1 if normalized <= 1 else 2 if normalized <= 2 else 5 if normalized <= 5 else 10
    return step * magnitude


def format_axis(value: float, metric: str) -> str:
    if metric == "Estimated cost (USD)":
        return f"${value:,.2f}" if value < 100 else f"${value:,.0f}"
    return format_compact(int(value))


def format_integer(value: int) -> str:
    return f"{value:,}"


def format_compact(value: int) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_currency(value: float) -> str:
    return f"${value:,.2f}"


def format_percent(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—" + (f" {suffix}" if suffix else "")
    shown = f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return f"{shown}%" + (f" {suffix}" if suffix else "")


def format_date_range(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%d %b %Y")
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    if start.year == end.year:
        return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"
