from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.combined import add_imported_usage, distinct_imported_events, run_combined_usage
from splitrail_desktop.domain import (DailyUsage, ModelDetail, TokenUsage, UsageDataset,
                                      aggregate_period, daily_data_rows, daily_series)
from splitrail_desktop.portable import SCHEMA, import_export, make_event, write_export
from splitrail_desktop.runner import CostDiagnostics, MissingCommandError, LocalCommandError, StatsCommandResult


def event(identity, day='2026-09-01', model='gpt-6-astra', source='Codex CLI'):
    return make_event(identity, 'session', day + 'T00:00:00Z', model, {
        'input_tokens': 300_000, 'cached_input_tokens': 200_000,
        'cache_write_input_tokens': 20_000, 'output_tokens': 100,
        'reasoning_output_tokens': 40,
    }, source)


def collector_fixture():
    # Deliberately different from portable pricing/counting: a merge must never
    # replace the collector's local Codex or reprice its other tool history.
    rows = []
    for analyzer, model, cost in [('Codex CLI', 'gpt-6-astra', 99.12),
                                  ('Claude Code', 'claude-fixture', 12.34),
                                  ('Pi Agent', 'openai-codex/gpt-6-astra', 4.56)]:
        tokens = TokenUsage(input=50, cached=400, output=30, reasoning=10,
                            reasoning_in_output=10 if analyzer == 'Codex CLI' else 0)
        detail = ModelDetail(model, 5, tokens, cost, 3)
        rows.append(DailyUsage(analyzer, date(2026, 9, 1), 2, 4, 5, tokens,
                               cost, 3, {model: 5}, (detail,)))
    return UsageDataset(tuple(rows), {row.analyzer: 2 for row in rows}, True)


class CombinedUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.local = {'events': [event('local'), event('pi-local', source='Codex via Pi')]}
        self.base = collector_fixture()
        self.laptop = [event('laptop'), event('unknown', '2026-09-02', 'future-model')]
        self.diagnostics = CostDiagnostics(('local-unknown',), 2, 1, ('local warning',))
        self.result = StatsCommandResult(self.base, self.diagnostics, 7)

    def run_combined(self):
        with patch('splitrail_desktop.combined.run_splitrail', return_value=self.result), \
             patch('splitrail_desktop.combined.auto_receive', return_value='Laptop usage is up to date'), \
             patch('splitrail_desktop.combined.scan_codex', return_value=self.local):
            return run_combined_usage(self.directory)

    def write_snapshot(self, filename, events):
        write_export(self.directory / filename, {'schema': SCHEMA, 'events': events})

    def test_all_local_rows_preserved_and_only_distinct_imports_added(self):
        imported = self.local['events'] + self.laptop + self.laptop
        distinct = distinct_imported_events(self.local, imported)
        combined = add_imported_usage(self.base, distinct)
        self.assertEqual(distinct, self.laptop)
        self.assertEqual(combined.days[:len(self.base.days)], self.base.days)
        self.assertTrue(all(a is b for a, b in zip(combined.days, self.base.days)))
        self.assertEqual(combined.analyzer_conversation_totals['Codex CLI'], 2)
        self.assertTrue(combined.ignored_raw_messages)
        full = aggregate_period(combined, date(2026, 9, 1), date(2026, 9, 2))
        baseline = aggregate_period(self.base, full.start, full.end)
        self.assertEqual(full.total.tokens.total, baseline.total.tokens.total + 600_200)
        self.assertAlmostEqual(full.total.cost, baseline.total.cost + 2.5075)
        for name, total in baseline.by_analyzer.items():
            self.assertEqual(full.by_analyzer[name], total)
        self.assertEqual(full.total.tool_calls, baseline.total.tool_calls)
        self.assertEqual(full.total.user_messages, baseline.total.user_messages)
        self.assertEqual(full.total.ai_messages, baseline.total.ai_messages + 2)
        self.assertEqual(full.by_analyzer['Imported Codex CLI'].tokens.reasoning, 80)
        self.assertEqual(full.by_analyzer['Imported Codex CLI'].tokens.cache_write, 40_000)

    def test_daily_model_and_chart_sums_reconcile(self):
        combined = add_imported_usage(self.base, self.laptop)
        full = aggregate_period(combined, date(2026, 9, 1), date(2026, 9, 2))
        for totals in ([r.total for r in daily_data_rows(combined, full.start, full.end)],
                       [r.total for r in daily_series(combined, full.start, full.end)],
                       list(full.by_analyzer.values()), list(full.by_model.values())):
            self.assertEqual(sum(t.tokens.total for t in totals), full.total.tokens.total)
            self.assertAlmostEqual(sum(t.cost for t in totals), full.total.cost)
        second_day = aggregate_period(combined, full.end, full.end)
        self.assertEqual(second_day.total.tokens.total, 300_100)
        self.assertEqual(second_day.total.cost, 0)
        self.assertEqual(second_day.by_model['future-model'].tokens.total, 300_100)

    def test_repeat_manual_sync_refresh_and_restart_do_not_accumulate(self):
        self.write_snapshot('export.json.gz', self.local['events'] + self.laptop)
        self.assertEqual(import_export(self.directory/'export.json.gz', self.directory), 4)
        self.assertEqual(import_export(self.directory/'export.json.gz', self.directory), 0)
        first = self.run_combined()
        self.assertEqual(first, self.run_combined())
        self.write_snapshot('export.json.gz', self.laptop[:1])
        self.assertEqual(import_export(self.directory/'export.json.gz', self.directory), 0)
        self.assertEqual(first, self.run_combined())
        self.assertEqual(first.dataset, add_imported_usage(self.base, self.laptop))
        self.assertEqual(first.cost_diagnostics.unknown_models, ('future-model', 'local-unknown'))
        self.assertTrue(first.cost_diagnostics.is_partial)
        self.assertEqual(first.cost_diagnostics.fallback_session_count, 2)
        self.assertEqual(first.cost_diagnostics.lines[0], 'local warning')
        self.assertEqual(first.exit_code, 7)

    def test_local_overlap_does_not_reprice_unknown_local_record(self):
        self.local['events'][0]['model'] = 'unknown'
        self.write_snapshot('imported-codex-usage.json.gz', [event('local')])
        self.assertEqual(self.run_combined().dataset, self.base)

    def test_conflicting_overlap_rejects_without_rewriting_cache_or_collector(self):
        bad = event('local')
        bad['usage']['output_tokens'] += 1
        self.write_snapshot('imported-codex-usage.json.gz', [bad])
        path = self.directory/'imported-codex-usage.json.gz'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Conflicting token counts'):
            self.run_combined()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.base, collector_fixture())

    def test_no_imports_keep_local_history_without_portable_scan(self):
        with patch('splitrail_desktop.combined.run_splitrail', return_value=self.result), \
             patch('splitrail_desktop.combined.auto_receive'), \
             patch('splitrail_desktop.combined.scan_codex') as scan:
            self.assertEqual(run_combined_usage(self.directory).dataset, self.base)
            scan.assert_not_called()

    def test_missing_collector_is_an_error_not_a_codex_subtotal(self):
        with patch('splitrail_desktop.combined.run_splitrail', side_effect=MissingCommandError('missing CLI')), \
             patch('splitrail_desktop.combined.scan_codex') as scan, \
             patch('splitrail_desktop.combined.auto_receive') as sync:
            with self.assertRaisesRegex(LocalCommandError, 'requires the local Splitrail collector'):
                run_combined_usage(self.directory)
            scan.assert_not_called()
            sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
