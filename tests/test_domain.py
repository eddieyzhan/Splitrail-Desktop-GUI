from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from splitrail_desktop.domain import (
    DailyUsage,
    StatsDataError,
    TokenUsage,
    UsageDataset,
    aggregate_period,
    calendar_month,
    calendar_week,
    daily_data_rows,
    daily_series,
    parse_stats_json,
    parse_stats_payload,
    period_for_preset,
)


FIXTURES = Path(__file__).parent / "fixtures"


class StatsParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = (FIXTURES / "stats_valid.json").read_text(encoding="utf-8")

    def test_parses_complete_aggregate_schema_without_messages(self) -> None:
        dataset = parse_stats_json(self.raw)

        self.assertEqual(len(dataset.days), 4)
        self.assertEqual(dataset.first_day, date(2026, 8, 3))
        self.assertEqual(dataset.last_day, date(2026, 8, 10))
        self.assertEqual(dataset.analyzer_conversation_totals["Example CLI"], 4)
        first = dataset.days[0]
        self.assertEqual(first.tokens.total, 360)
        self.assertEqual(first.model_details[0].tokens.cache_read, 160)
        self.assertEqual(first.model_details[0].tokens.cache_write, 40)
        self.assertFalse(dataset.ignored_raw_messages)

    def test_accepts_legacy_daily_array_shape(self) -> None:
        payload = json.loads(self.raw)
        analyzer = payload["analyzer_stats"][0]
        analyzer["daily_stats"] = list(analyzer["daily_stats"].values())

        dataset = parse_stats_payload(payload)

        self.assertEqual(len(dataset.days), 4)

    def test_discards_unexpected_raw_message_entries(self) -> None:
        payload = json.loads(self.raw)
        payload["analyzer_stats"][0]["messages"] = [{"content": "fixture-only"}]

        dataset = parse_stats_payload(payload)

        self.assertTrue(dataset.ignored_raw_messages)
        self.assertFalse(hasattr(dataset, "messages"))

    def test_rejects_malformed_shapes_and_numbers(self) -> None:
        with self.assertRaisesRegex(StatsDataError, "analyzer_stats must be an array"):
            parse_stats_json('{"analyzer_stats": {}}')
        payload = json.loads(self.raw)
        payload["analyzer_stats"][0]["daily_stats"]["2026-08-03"]["stats"]["inputTokens"] = "100"
        with self.assertRaisesRegex(StatsDataError, "inputTokens must be a number"):
            parse_stats_payload(payload)


class PeriodAggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = parse_stats_json((FIXTURES / "stats_valid.json").read_text(encoding="utf-8"))

    def test_calendar_week_aggregation_is_inclusive(self) -> None:
        start, end = calendar_week(date(2026, 8, 5))
        result = aggregate_period(self.dataset, start, end)

        self.assertEqual((start, end), (date(2026, 8, 3), date(2026, 8, 9)))
        self.assertEqual(result.total.tokens.input, 130)
        self.assertEqual(result.total.tokens.output, 85)
        self.assertEqual(result.total.tokens.reasoning, 10)
        self.assertEqual(result.total.tokens.cached, 220)
        self.assertEqual(result.total.tokens.total, 445)
        self.assertAlmostEqual(result.total.cost, 2.25)
        self.assertEqual(result.total.conversations, 4)
        self.assertEqual(result.total.messages, 13)
        self.assertEqual(result.usage_days, 2)
        self.assertEqual(result.model_detail_days, 1)

    def test_model_detail_coverage_does_not_invent_missing_values(self) -> None:
        result = aggregate_period(self.dataset, date(2026, 8, 3), date(2026, 8, 9))

        example = result.by_model["example-model"]
        self.assertEqual(example.messages, 5)
        self.assertEqual(example.tokens.input, 100)
        self.assertEqual(example.tokens.cache_read, 160)
        self.assertEqual(example.detail_coverage, "Partial (1/2 days)")
        self.assertEqual(result.by_model["other-model"].detail_coverage, "Unavailable")

    def test_daily_series_fills_zero_usage_dates(self) -> None:
        points = daily_series(self.dataset, date(2026, 8, 3), date(2026, 8, 9))

        self.assertEqual(len(points), 7)
        self.assertEqual(points[0].total.tokens.total, 410)
        self.assertEqual(points[-1].total.tokens.total, 0)

    def test_data_rows_are_dense_daily_aggregates_with_models(self) -> None:
        rows = daily_data_rows(self.dataset, date(2026, 8, 3), date(2026, 8, 9))

        self.assertEqual([row.day for row in rows], [date(2026, 8, 4), date(2026, 8, 3)])
        self.assertEqual(rows[1].total.tokens.cached, 200)
        self.assertEqual(rows[1].total.tool_calls, 2)
        self.assertEqual(rows[1].analyzers, ("Example CLI", "Other Agent"))
        self.assertEqual(rows[1].models, ("example-model", "other-model"))

    def test_month_year_and_all_time_presets_are_calendar_explicit(self) -> None:
        self.assertEqual(calendar_month(date(2026, 2, 8)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(
            period_for_preset("Day", date(2026, 8, 5), self.dataset),
            (date(2026, 8, 5), date(2026, 8, 5)),
        )
        self.assertEqual(
            period_for_preset("Year", date(2026, 8, 19), self.dataset),
            (date(2026, 1, 1), date(2026, 8, 19)),
        )
        self.assertEqual(
            period_for_preset("All time", date(2026, 8, 19), self.dataset),
            (self.dataset.first_day, date(2026, 8, 19)),
        )

    def test_all_time_includes_complete_history_and_totals(self) -> None:
        start, end = period_for_preset("All time", date(2099, 1, 1), self.dataset)
        result = aggregate_period(self.dataset, start, end)

        self.assertEqual((start, end), (self.dataset.first_day, date(2099, 1, 1)))
        self.assertEqual(result.usage_days, 3)
        self.assertEqual(result.total.tokens.total, 495)
        self.assertAlmostEqual(result.total.cost, 2.75)
        self.assertEqual(len(daily_data_rows(self.dataset, start, end)), 3)

    def test_all_time_empty_dataset_uses_today(self) -> None:
        empty = parse_stats_payload({"analyzer_stats": []})
        today = date(2026, 8, 20)

        start, end = period_for_preset("All time", today, empty)
        result = aggregate_period(empty, start, end)

        self.assertEqual((start, end), (today, today))
        self.assertTrue(result.is_empty)
        self.assertEqual(daily_data_rows(empty, start, end), [])

    def test_all_time_includes_arbitrarily_old_history_and_excludes_future(self) -> None:
        def usage(day: date, tokens: int) -> DailyUsage:
            return DailyUsage("Codex CLI", day, 1, 1, 1, TokenUsage(input=tokens), 0.0, 0, {}, ())

        dataset = UsageDataset(
            (
                usage(date(2019, 1, 1), 100),
                usage(date(2020, 1, 1), 10),
                usage(date(2026, 8, 20), 20),
                usage(date(2026, 8, 21), 200),
            ),
            {"Codex CLI": 4},
        )
        start, end = period_for_preset("All time", date(2026, 8, 20), dataset)

        result = aggregate_period(dataset, start, end)

        self.assertEqual(result.total.tokens.total, 130)
        self.assertEqual(result.usage_days, 3)


if __name__ == "__main__":
    unittest.main()
