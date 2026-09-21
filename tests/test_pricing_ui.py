from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.app import SplitrailApp
from splitrail_desktop.pricing import load_overrides
from splitrail_desktop.runner import CostDiagnostics, StatsCommandResult
from splitrail_desktop.settings_ui import PricingSettings
from splitrail_desktop.quota import parse_quota_json
from test_pricing import dataset


class PricingUiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for target in ('splitrail_desktop.portable.data_dir', 'splitrail_desktop.sync.data_dir'):
            patcher = patch(target, return_value=Path(temporary.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.app = SplitrailApp(auto_refresh=False)
        except tk.TclError as exc:
            if 'no display name' in str(exc) or "couldn't connect to display" in str(exc):
                self.skipTest(str(exc))
            raise
        self.addCleanup(self.close)

    def close(self):
        for callback in self.app.tk.splitlist(self.app.tk.call('after', 'info')):
            self.app.after_cancel(callback)
        self.app._close()

    def test_settings_save_reload_remove_and_validation(self):
        with patch.object(self.app, 'refresh_usage') as refresh:
            dialog = PricingSettings(self.app, ('unknown-model',), self.app._pricing_changed)
            self.app.update()
            self.assertEqual(dialog.filter_var.get(), 'Needs pricing')
            self.assertEqual(dialog.model_var.get(), 'unknown-model')
            for field, value in dict(input='1', output='2', cache_read='.1', cache_write='0').items():
                dialog.rate_vars[field].set(value)
            dialog.save_rate()
            self.assertEqual(load_overrides()['unknown-model']['input'], 1)
            refresh.assert_called_once()
            dialog.destroy()
            dialog = PricingSettings(self.app, (), self.app._pricing_changed)
            dialog.tree.selection_set('unknown-model')
            self.app.update()
            self.assertEqual(dialog.rate_vars['output'].get(), '2')
            dialog.rate_vars['input'].set('NaN')
            dialog.save_rate()
            self.assertIn('finite number', dialog.feedback_var.get())
            self.assertEqual(load_overrides()['unknown-model']['input'], 1)
            dialog.remove_rate()
            self.assertEqual(load_overrides(), {})
            dialog.destroy()

    def test_headers_align_and_fit_at_minimum_and_normal_size(self):
        buttons = (self.app.refresh_all_button, self.app.transfer_button, self.app.sync_button,
                   self.app.notification_button, self.app.settings_button)
        for width, height in ((1050, 700), (1240, 880)):
            self.app.geometry(f'{width}x{height}')
            self.app.update()
            self.assertEqual(len({button.winfo_height() for button in buttons}), 1)
            for button in buttons:
                self.assertLessEqual(button.winfo_x() + button.winfo_width(), button.master.winfo_width())
            self.assertLess(self.app.period_selector.winfo_rooty(), self.app.period_card.winfo_rooty() + self.app.period_card.winfo_height())

    def test_notifications_preserve_independent_errors(self):
        self.app._show_notice('Usage needs pricing', 'warning', ('usage diagnostic',))
        self.app._hide_notice('quota')
        self.assertEqual(self.app._details, ('usage diagnostic',))
        self.app._show_notice('Quota offline', 'error', source='quota')
        self.app._hide_notice()
        self.assertEqual(self.app.notification_button.cget('text'), 'Notifications · 1')
        self.assertEqual(self.app._notifications['quota'][0], 'Quota offline')
        self.app._show_details()
        self.app.update()
        self.assertFalse(any(widget.winfo_class() == 'Frame' and widget.grid_info().get('row') == 1
                             for widget in self.app.winfo_children()))

    def test_background_refresh_retains_custom_range(self):
        self.app._custom_range = (date(2026, 9, 20), date(2026, 9, 22))
        self.app.active_period_var.set('CUSTOM RANGE')
        self.app._loading_usage_mode = self.app._usage_mode
        result = StatsCommandResult(dataset(), CostDiagnostics((), 0, 0, ()), 0)
        self.app._finish_usage(result, None)
        self.assertEqual(self.app._selected_aggregate.start, date(2026, 9, 20))
        self.assertEqual(self.app._selected_aggregate.end, date(2026, 9, 22))

    def test_saving_during_refresh_schedules_another_refresh(self):
        self.app._usage_refreshing = True
        self.app._pricing_changed()
        self.assertTrue(self.app._pricing_refresh_pending)
        self.app._loading_usage_mode = self.app._usage_mode
        with patch.object(self.app, 'refresh_usage') as refresh:
            self.app._finish_usage(StatsCommandResult(dataset(), CostDiagnostics((), 0, 0, ()), 0), None)
            refresh.assert_called_once()
        self.assertFalse(self.app._pricing_refresh_pending)

    def test_quota_age_ticks_without_overwriting_refresh_or_failure_status(self):
        snapshot = parse_quota_json((Path(__file__).parent / 'fixtures/quota_fresh.json').read_text())
        now = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
        snapshot = replace(snapshot, refreshed_at=now - timedelta(seconds=12))
        card = self.app.quota_card
        with patch('splitrail_desktop.quota.datetime') as clock:
            clock.now.return_value = now
            card.set_snapshot(snapshot)
            self.assertEqual(card.status_var.get(), 'Updated 12s ago')
            clock.now.return_value = now + timedelta(seconds=1)
            card.update_countdown(snapshot)
            self.assertEqual(card.status_var.get(), 'Updated 13s ago')
            card.set_refreshing(True)
            card.update_countdown(snapshot)
            self.assertEqual(card.status_var.get(), 'Refreshing…')
            card.set_refreshing(False)
            card.set_refresh_failed()
            card.update_countdown(snapshot)
            self.assertEqual(card.status_var.get(), 'Refresh failed')
            card.set_snapshot(snapshot)
            self.assertEqual(card.status_var.get(), 'Updated 13s ago')
            card.set_snapshot(replace(snapshot, refreshed_at=None, generated_at=now))
            self.assertEqual(card.status_var.get(), 'Updated 1s ago')
