from __future__ import annotations

import inspect
import unittest
from datetime import date, timedelta

from splitrail_desktop.app import (
    BG,
    BLUE,
    CARD,
    INK,
    PRIMARY_PERIODS,
    QuotaCard,
    TOP_LEVEL_SECTIONS,
    build_chart_buckets,
    chart_series,
    chart_value,
    cycle_period_index,
    nice_ceiling,
)
from splitrail_desktop.domain import DailyUsage, SeriesPoint, TokenUsage, UsageDataset, UsageTotal


class UiLogicTests(unittest.TestCase):
    def test_both_themes_define_every_color_role(self) -> None:
        from splitrail_desktop.themes import THEMES
        self.assertEqual(set(THEMES['Pearl']), set(THEMES['Nord']))
        self.assertEqual(THEMES['Nord']['BG'], '#2E3440')
        self.assertEqual(THEMES['Pearl']['CARD'], '#FFFFFF')

    def test_primary_period_and_section_contracts_are_explicit(self) -> None:
        self.assertEqual(PRIMARY_PERIODS[1], ("Current week", "Week"))
        self.assertEqual(PRIMARY_PERIODS[3], ("Current year", "Year"))
        self.assertEqual(PRIMARY_PERIODS[4], ("All time", "All time"))
        self.assertEqual(
            TOP_LEVEL_SECTIONS,
            ("Overview", "Analyzers", "Models", "Quotas", "Data"),
        )

    def test_quota_card_exposes_refresh_only_and_no_reset_consuming_control(self) -> None:
        source = inspect.getsource(QuotaCard)

        self.assertEqual(source.count("ttk.Button("), 1)
        self.assertIn("command=refresh_command", source)
        self.assertNotIn("consume", source.lower())
        self.assertNotIn("redeem", source.lower())

    def test_primary_period_cycles_forward_backward_and_wraps(self) -> None:
        self.assertEqual(cycle_period_index(3, 1, len(PRIMARY_PERIODS)), 4)
        self.assertEqual(cycle_period_index(4, 1, len(PRIMARY_PERIODS)), 0)
        self.assertEqual(cycle_period_index(0, -1, len(PRIMARY_PERIODS)), 4)
        self.assertEqual(cycle_period_index(4, -1, len(PRIMARY_PERIODS)), 3)

    def test_long_series_is_bucketed_without_losing_totals(self) -> None:
        points = []
        start = date(2026, 1, 1)
        for offset in range(70):
            total = UsageTotal(conversations=1)
            points.append(SeriesPoint(start + timedelta(days=offset), total))

        buckets = build_chart_buckets(points)

        self.assertLess(len(buckets), len(points))
        self.assertEqual(sum(bucket.total.conversations for bucket in buckets), 70)

    def test_sparse_long_history_buckets_by_date_span_without_expanding_rows(self) -> None:
        days = (
            DailyUsage("CLI", date(2020, 1, 1), 1, 1, 1, TokenUsage(input=10), 1.0, 0, {}, ()),
            DailyUsage("CLI", date(2026, 8, 20), 2, 2, 2, TokenUsage(output=20), 2.0, 0, {}, ()),
        )
        dataset = UsageDataset(days, {"CLI": 3})

        points = chart_series(dataset, days[0].day, days[-1].day)
        buckets = build_chart_buckets(points)

        self.assertEqual(len(points), 2)
        self.assertEqual(len(buckets), 2)
        self.assertEqual(sum(bucket.total.tokens.total for bucket in buckets), 30)
        self.assertEqual(sum(bucket.total.cost for bucket in buckets), 3.0)

    def test_chart_units_follow_selected_metric(self) -> None:
        total = UsageTotal(cost=12.5, conversations=3, user_messages=2, ai_messages=4)

        self.assertEqual(chart_value(total, "Estimated cost (USD)"), 12.5)
        self.assertEqual(chart_value(total, "Conversations"), 3)
        self.assertEqual(chart_value(total, "Messages"), 6)
        self.assertEqual(nice_ceiling(17), 20)


if __name__ == "__main__":
    unittest.main()
