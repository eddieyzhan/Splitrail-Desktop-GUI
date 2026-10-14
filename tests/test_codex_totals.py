from dataclasses import replace
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from splitrail_desktop.codex_totals import reconcile_codex
from splitrail_desktop.domain import HourlyUsage, TokenUsage, UsageDataset, aggregate_period
from splitrail_desktop.portable import usage_dataset
from splitrail_desktop.sync_payload import encode_dataset, decode_dataset
from test_combined import collector_fixture, event


class CodexTotalsTests(TestCase):
    def reconcile(self, dataset, events, **diagnostics):
        with patch('splitrail_desktop.codex_totals.scan_codex', return_value={
                'events': events, 'diagnostics': {'session_files': 2, **diagnostics}}) as scan:
            result = reconcile_codex(dataset)
            scan.assert_called_once_with(include_pi=False)
            return result

    def test_native_counts_replace_codex_only_and_preserve_other_tools(self):
        collector = collector_fixture()
        request = event('modern')
        native = usage_dataset([request])
        result, warning = self.reconcile(collector, [request])
        self.assertIsNone(warning)
        codex = next(row for row in result.days if row.analyzer == 'Codex CLI')
        self.assertEqual(codex.tokens, native.days[0].tokens)
        self.assertEqual(codex.cost, native.days[0].cost)
        self.assertEqual(codex.user_messages, collector.days[0].user_messages)
        self.assertEqual(codex.tool_calls, collector.days[0].tool_calls)
        self.assertEqual(sum(model.tokens.total for model in codex.model_details), codex.tokens.total)
        self.assertEqual([row for row in result.days if row.analyzer != 'Codex CLI'], list(collector.days[1:]))
        self.assertNotIn('Codex via Pi', {row.analyzer for row in result.days})

    def test_modern_only_days_appear_in_all_tools_and_sync_payload(self):
        collector = collector_fixture()
        requests = [event('old'), event('new', '2026-09-02')]
        result, _ = self.reconcile(collector, requests)
        full = aggregate_period(result, date(2026, 9, 1), date(2026, 9, 2))
        self.assertEqual(full.by_analyzer['Codex CLI'].tokens.total, 600_200)
        # The same reconciled dataset is encoded for All tools sync.
        payload = encode_dataset(result, 'a' * 32, 'all')
        remote = decode_dataset(payload)
        self.assertEqual(sum(r.tokens.total for r in remote.days), full.total.tokens.total)
        self.assertEqual(sum(r.tokens.total for r in remote.hours), 600_200)

    def test_old_collector_only_codex_counts_do_not_survive_as_extra_rows(self):
        collector = collector_fixture()
        extra = replace(collector.days[0], day=date(2026, 9, 2))
        collector = replace(collector, days=collector.days + (extra,),
                            hours=(HourlyUsage('Codex CLI', date(2026, 9, 2), 1, TokenUsage(input=999), 1),))
        result, _ = self.reconcile(collector, [event('modern')])
        removed = next(row for row in result.days if row.day == date(2026, 9, 2))
        self.assertEqual(removed.tokens.total, 0)
        self.assertEqual(removed.cost, 0)
        self.assertEqual(sum(row.tokens.total for row in result.hours), 300_100)

    def test_absent_or_unreadable_logs_preserve_collector_totals(self):
        collector = collector_fixture()
        result, warning = self.reconcile(collector, [], session_files=0)
        self.assertIs(result, collector)
        self.assertIsNone(warning)
        result, warning = self.reconcile(collector, [])
        self.assertIs(result, collector)
        self.assertIn('history is unavailable', warning)
        for diagnostic in ('malformed_lines', 'invalid_usage', 'invalid_counter_gaps'):
            result, warning = self.reconcile(collector, [], **{diagnostic: 1})
            self.assertIs(result, collector)
            self.assertIn('showing collector totals', warning)
        with patch('splitrail_desktop.codex_totals.scan_codex', side_effect=PermissionError('PRIVATE')):
            result, warning = reconcile_codex(collector)
            self.assertIs(result, collector)
            self.assertNotIn('PRIVATE', warning)

    def test_no_collector_codex_rows_still_includes_local_request_history(self):
        result, _ = self.reconcile(UsageDataset((), {}), [event('modern')])
        self.assertEqual(sum(row.tokens.total for row in result.days), 300_100)
        self.assertEqual(result.analyzer_conversation_totals['Codex CLI'], 1)
