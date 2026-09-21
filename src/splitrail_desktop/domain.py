from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


class StatsDataError(ValueError):
    """Raised when Splitrail's aggregate JSON cannot be interpreted safely."""


@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cached: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning_in_output: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.reasoning + self.cached - self.reasoning_in_output

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            reasoning=self.reasoning + other.reasoning,
            cached=self.cached + other.cached,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            reasoning_in_output=self.reasoning_in_output + other.reasoning_in_output,
        )


@dataclass(frozen=True)
class ModelDetail:
    name: str
    messages: int
    tokens: TokenUsage
    cost: float
    tool_calls: int


@dataclass(frozen=True)
class DailyUsage:
    analyzer: str
    day: date
    conversations: int
    user_messages: int
    ai_messages: int
    tokens: TokenUsage
    cost: float
    tool_calls: int
    model_messages: dict[str, int]
    model_details: tuple[ModelDetail, ...]


@dataclass(frozen=True)
class HourlyUsage:
    analyzer: str
    day: date
    hour: int
    tokens: TokenUsage
    cost: float


@dataclass(frozen=True)
class UsageDataset:
    days: tuple[DailyUsage, ...]
    analyzer_conversation_totals: dict[str, int]
    ignored_raw_messages: bool = False
    hours: tuple[HourlyUsage, ...] = ()

    @property
    def first_day(self) -> date | None:
        return min((item.day for item in self.days), default=None)

    @property
    def last_day(self) -> date | None:
        return max((item.day for item in self.days), default=None)


@dataclass
class UsageTotal:
    tokens: TokenUsage = field(default_factory=TokenUsage)
    cost: float = 0.0
    conversations: int = 0
    user_messages: int = 0
    ai_messages: int = 0
    tool_calls: int = 0

    @property
    def messages(self) -> int:
        return self.user_messages + self.ai_messages

    def add_day(self, item: DailyUsage) -> None:
        self.tokens = self.tokens + item.tokens
        self.cost += item.cost
        self.conversations += item.conversations
        self.user_messages += item.user_messages
        self.ai_messages += item.ai_messages
        self.tool_calls += item.tool_calls


@dataclass
class ModelTotal:
    name: str
    messages: int = 0
    tokens: TokenUsage = field(default_factory=TokenUsage)
    cost: float = 0.0
    tool_calls: int = 0
    usage_days: int = 0
    detailed_days: int = 0

    @property
    def detail_coverage(self) -> str:
        if not self.usage_days or not self.detailed_days:
            return "Unavailable"
        if self.detailed_days >= self.usage_days:
            return "Complete"
        return f"Partial ({self.detailed_days}/{self.usage_days} days)"


@dataclass(frozen=True)
class PeriodAggregate:
    start: date
    end: date
    total: UsageTotal
    by_analyzer: dict[str, UsageTotal]
    by_model: dict[str, ModelTotal]
    usage_days: int
    model_detail_days: int

    @property
    def is_empty(self) -> bool:
        return self.usage_days == 0


@dataclass(frozen=True)
class SeriesPoint:
    day: date
    total: UsageTotal


@dataclass(frozen=True)
class DailyDataRow:
    day: date
    total: UsageTotal
    analyzers: tuple[str, ...]
    models: tuple[str, ...]


def parse_stats_json(raw: str) -> UsageDataset:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StatsDataError(f"Splitrail returned malformed JSON: {exc}") from exc
    return parse_stats_payload(payload)


def parse_stats_payload(payload: Any) -> UsageDataset:
    root = _object(payload, "root")
    analyzers = _list(root.get("analyzer_stats"), "analyzer_stats")
    parsed: list[DailyUsage] = []
    conversation_totals: dict[str, int] = {}
    ignored_messages = False

    for analyzer_index, raw_analyzer in enumerate(analyzers):
        path = f"analyzer_stats[{analyzer_index}]"
        analyzer = _object(raw_analyzer, path)
        name = _string(analyzer.get("analyzer_name"), f"{path}.analyzer_name")
        conversation_totals[name] = _integer(
            analyzer.get("num_conversations", 0), f"{path}.num_conversations"
        )
        messages = analyzer.get("messages", [])
        if messages is not None:
            ignored_messages = ignored_messages or bool(_list(messages, f"{path}.messages"))

        daily_value = analyzer.get("daily_stats")
        if isinstance(daily_value, dict):
            daily_items = list(daily_value.items())
        elif isinstance(daily_value, list):
            daily_items = [(str(index), value) for index, value in enumerate(daily_value)]
        else:
            raise StatsDataError(f"{path}.daily_stats must be an object keyed by date or an array")
        for day_key, raw_day in daily_items:
            day_path = f"{path}.daily_stats[{day_key}]"
            item = _object(raw_day, day_path)
            try:
                parsed_day = date.fromisoformat(_string(item.get("date", day_key), f"{day_path}.date"))
            except ValueError as exc:
                raise StatsDataError(f"{day_path}.date is not an ISO calendar date") from exc
            stats = _object(item.get("stats"), f"{day_path}.stats")
            model_messages = {
                _string(model, f"{day_path}.models key"): _integer(count, f"{day_path}.models.{model}")
                for model, count in _object(item.get("models", {}), f"{day_path}.models").items()
            }
            model_details = _parse_model_details(item.get("model_stats"), day_path, name in ("Codex CLI", "Antigravity CLI"))
            parsed.append(
                DailyUsage(
                    analyzer=name,
                    day=parsed_day,
                    conversations=_integer(item.get("conversations", 0), f"{day_path}.conversations"),
                    user_messages=_integer(item.get("user_messages", 0), f"{day_path}.user_messages"),
                    ai_messages=_integer(item.get("ai_messages", 0), f"{day_path}.ai_messages"),
                    tokens=TokenUsage(
                        input=_integer(stats.get("inputTokens", 0), f"{day_path}.stats.inputTokens"),
                        output=_integer(stats.get("outputTokens", 0), f"{day_path}.stats.outputTokens"),
                        reasoning=_integer(stats.get("reasoningTokens", 0), f"{day_path}.stats.reasoningTokens"),
                        reasoning_in_output=_integer(stats.get("reasoningTokens", 0), f"{day_path}.stats.reasoningTokens") if name in ("Codex CLI", "Antigravity CLI") else 0,
                        cached=_integer(stats.get("cachedTokens", 0), f"{day_path}.stats.cachedTokens"),
                    ),
                    cost=_number(stats.get("costCents", 0), f"{day_path}.stats.costCents") / 100,
                    tool_calls=_integer(stats.get("toolCalls", 0), f"{day_path}.stats.toolCalls"),
                    model_messages=model_messages,
                    model_details=model_details,
                )
            )

    parsed.sort(key=lambda item: (item.day, item.analyzer))
    return UsageDataset(tuple(parsed), conversation_totals, ignored_messages)


def _parse_model_details(value: Any, day_path: str, reasoning_in_output: bool = False) -> tuple[ModelDetail, ...]:
    if value is None:
        return ()
    details = _object(value, f"{day_path}.model_stats")
    parsed: list[ModelDetail] = []
    for key, raw_detail in details.items():
        path = f"{day_path}.model_stats.{key}"
        detail = _object(raw_detail, path)
        name = _string(detail.get("model", key), f"{path}.model")
        parsed.append(
            ModelDetail(
                name=name,
                messages=_integer(detail.get("messageCount", 0), f"{path}.messageCount"),
                tokens=TokenUsage(
                    input=_integer(detail.get("inputTokens", 0), f"{path}.inputTokens"),
                    output=_integer(detail.get("outputTokens", 0), f"{path}.outputTokens"),
                    reasoning=_integer(detail.get("reasoningTokens", 0), f"{path}.reasoningTokens"),
                    reasoning_in_output=_integer(detail.get("reasoningTokens", 0), f"{path}.reasoningTokens") if reasoning_in_output else 0,
                    cached=_integer(detail.get("cachedTokens", 0), f"{path}.cachedTokens"),
                    cache_read=_integer(detail.get("cacheReadTokens", 0), f"{path}.cacheReadTokens"),
                    cache_write=_integer(detail.get("cacheCreationTokens", 0), f"{path}.cacheCreationTokens"),
                ),
                cost=_number(detail.get("cost", 0), f"{path}.cost"),
                tool_calls=_integer(detail.get("toolCalls", 0), f"{path}.toolCalls"),
            )
        )
    return tuple(parsed)


def aggregate_period(dataset: UsageDataset, start: date, end: date) -> PeriodAggregate:
    if end < start:
        raise ValueError("period end must not be before period start")
    total = UsageTotal()
    by_analyzer: dict[str, UsageTotal] = {}
    by_model: dict[str, ModelTotal] = {}
    usage_dates: set[date] = set()
    detailed_dates: set[date] = set()

    for item in dataset.days:
        if not start <= item.day <= end:
            continue
        usage_dates.add(item.day)
        total.add_day(item)
        by_analyzer.setdefault(item.analyzer, UsageTotal()).add_day(item)

        detail_by_name = {detail.name: detail for detail in item.model_details}
        model_names = set(item.model_messages) | set(detail_by_name)
        if item.model_details:
            detailed_dates.add(item.day)
        for name in model_names:
            model_total = by_model.setdefault(name, ModelTotal(name))
            model_total.usage_days += 1
            detail = detail_by_name.get(name)
            model_total.messages += item.model_messages.get(name, detail.messages if detail else 0)
            if detail:
                model_total.detailed_days += 1
                model_total.tokens = model_total.tokens + detail.tokens
                model_total.cost += detail.cost
                model_total.tool_calls += detail.tool_calls

    return PeriodAggregate(
        start=start,
        end=end,
        total=total,
        by_analyzer=by_analyzer,
        by_model=by_model,
        usage_days=len(usage_dates),
        model_detail_days=len(detailed_dates),
    )


def daily_series(dataset: UsageDataset, start: date, end: date) -> list[SeriesPoint]:
    by_day: dict[date, UsageTotal] = {}
    for item in dataset.days:
        if start <= item.day <= end:
            by_day.setdefault(item.day, UsageTotal()).add_day(item)
    result: list[SeriesPoint] = []
    cursor = start
    while cursor <= end:
        result.append(SeriesPoint(cursor, by_day.get(cursor, UsageTotal())))
        cursor += timedelta(days=1)
    return result


def daily_data_rows(dataset: UsageDataset, start: date, end: date) -> list[DailyDataRow]:
    totals: dict[date, UsageTotal] = {}
    analyzers: dict[date, set[str]] = {}
    models: dict[date, set[str]] = {}
    for item in dataset.days:
        if not start <= item.day <= end:
            continue
        totals.setdefault(item.day, UsageTotal()).add_day(item)
        analyzers.setdefault(item.day, set()).add(item.analyzer)
        models.setdefault(item.day, set()).update(item.model_messages)
        models[item.day].update(detail.name for detail in item.model_details)
    return [
        DailyDataRow(
            day=day,
            total=totals[day],
            analyzers=tuple(sorted(analyzers[day])),
            models=tuple(sorted(models[day])),
        )
        for day in sorted(totals, reverse=True)
    ]


def calendar_week(today: date) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def calendar_month(today: date) -> tuple[date, date]:
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1), today.replace(day=last_day)


def period_for_preset(preset: str, today: date, dataset: UsageDataset) -> tuple[date, date]:
    if preset == "Day":
        return today, today
    if preset == "Week":
        return calendar_week(today)
    if preset == "Month":
        return calendar_month(today)
    if preset == "Year":
        return today.replace(month=1, day=1), today
    if preset == "90 days":
        return today - timedelta(days=89), today
    if preset == "All time":
        return min(dataset.first_day or today, today), today
    raise ValueError(f"unknown period preset: {preset}")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StatsDataError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise StatsDataError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StatsDataError(f"{path} must be a non-empty string")
    return value.strip()


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatsDataError(f"{path} must be a number")
    if value < 0:
        raise StatsDataError(f"{path} must not be negative")
    return float(value)


def _integer(value: Any, path: str) -> int:
    number = _number(value, path)
    if not number.is_integer():
        raise StatsDataError(f"{path} must be a whole number")
    return int(number)
