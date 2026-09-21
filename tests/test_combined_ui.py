from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.app import SplitrailApp, format_currency, format_integer
from splitrail_desktop.combined import add_imported_usage
from splitrail_desktop.domain import aggregate_period
from splitrail_desktop.runner import CostDiagnostics, LocalCommandError, StatsCommandResult
from test_combined import collector_fixture, event


class CombinedDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target in ('splitrail_desktop.portable.data_dir', 'splitrail_desktop.sync.data_dir'):
            patcher = patch(target, return_value=Path(self.tmp.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict('os.environ', {'SPLITRAIL_BIN': '/example/splitrail'})
        patcher.start()
        self.addCleanup(patcher.stop)
        try:
            self.app = SplitrailApp(auto_refresh=False)
        except tk.TclError as exc:
            if 'no display name' in str(exc) or "couldn't connect to display" in str(exc):
                self.skipTest(str(exc))
            raise
        self.addCleanup(self.close_app)
        self.combined = add_imported_usage(collector_fixture(), [event('laptop')])
        self.result = StatsCommandResult(self.combined, CostDiagnostics(('unknown',), 0, 0, ()), 0)

    def close_app(self):
        for callback in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(callback)
        self.app._close()

    def load_result(self):
        self.app._loading_usage_mode = 'combined'
        self.app._finish_usage(self.result, None)
        self.app._render_period(date(2026, 9, 1), date(2026, 9, 2))
        self.app.update()

    def test_receive_default_metrics_data_and_chart_use_combined_scope(self):
        app = self.app
        self.assertEqual(app._usage_mode, 'combined')
        self.load_result()
        total = aggregate_period(self.combined, date(2026, 9, 1), date(2026, 9, 2)).total
        self.assertEqual(app.metric_cards[0].value_var.get(), format_currency(total.cost))
        self.assertEqual(app.metric_cards[1].value_var.get(), format_integer(total.tokens.total))
        self.assertEqual(app._cost_label(), 'Estimated USD')
        self.assertIn('Notifications · 1', app.notification_button.cget('text'))
        self.assertIn('needs pricing', app.notice_var.get())
        self.assertIn('All devices', app.source_status_var.get())
        self.assertEqual(app.metric_cards[4].label_var.get(), 'LOCAL TOOL CALLS')
        self.assertEqual(len(app.analyzer_tree.get_children()), 4)
        self.assertEqual(len(app.data_tree.get_children()), 1)
        self.assertEqual(app._selected_aggregate.total, total)
        self.assertTrue(app.chart.find_all())

    def test_sync_refresh_keeps_selected_all_tools_scope(self):
        self.load_result()
        self.app._results.put(('sync', {'status': 'Laptop usage is up to date'}, None))
        with patch.object(self.app, 'refresh_usage') as refresh:
            self.app._poll_results()
            refresh.assert_called_once_with()
        self.assertEqual(self.app._usage_mode, 'combined')
        self.assertEqual(self.app._usage_mode_var.get(), 'combined')

    def test_worker_routes_each_explicit_scope(self):
        with patch('splitrail_desktop.combined.run_combined_usage', return_value=self.result) as combined, \
             patch('splitrail_desktop.app.run_splitrail', return_value=self.result) as local, \
             patch('splitrail_desktop.portable.run_portable_usage', return_value=self.result) as codex:
            for mode, runner in [('combined', combined), ('local', local), ('codex', codex)]:
                self.app._loading_usage_mode = mode
                self.app._usage_worker()
                runner.assert_called_once_with()
                self.assertEqual(self.app._results.get_nowait(), ('usage', self.result, None))

    def test_failed_scope_change_keeps_previous_dataset_honestly_labelled(self):
        self.load_result()
        self.app._usage_mode = self.app._loading_usage_mode = 'local'
        self.app._finish_usage(None, LocalCommandError('collector unavailable'))
        self.assertIs(self.app._dataset, self.combined)
        self.assertEqual(self.app._displayed_usage_mode, 'combined')
        self.assertIn('previous All devices snapshot', self.app.notice_var.get())
        self.assertIn('requested This device', self.app.source_status_var.get())


if __name__ == '__main__':
    unittest.main()
