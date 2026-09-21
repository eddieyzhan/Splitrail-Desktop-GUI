from __future__ import annotations
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.app import SplitrailApp
from splitrail_desktop.onboarding import SettingsWindow, WelcomeWindow
from splitrail_desktop import preferences, sync, demo


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target in ('splitrail_desktop.portable.data_dir', 'splitrail_desktop.sync.data_dir'):
            patcher = patch(target, return_value=self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.app = SplitrailApp(auto_refresh=False)
        except tk.TclError as exc:
            if 'display' in str(exc):
                self.skipTest(str(exc))
            raise
        self.addCleanup(self.app._close)
        self.refresh = patch.object(self.app, 'refresh_usage')
        self.refresh.start()
        self.addCleanup(self.refresh.stop)

    def finish(self, dialog):
        deadline = time.monotonic() + 3
        while dialog.busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.app.update()
        self.assertFalse(dialog.busy, 'Background operation did not finish')

    def test_welcome_local_choice_never_uses_github(self):
        with patch('splitrail_desktop.sync._api') as api:
            welcome = WelcomeWindow(self.app)
            welcome.local()
            self.assertTrue(preferences.load()['onboarded'])
            self.assertIsNone(sync.settings())
            api.assert_not_called()

    def test_setup_requires_consent_before_creating_repository(self):
        dialog = SettingsWindow(self.app, tab='sync')
        with patch('splitrail_desktop.sync.create_private_repo') as create:
            dialog.connect()
            self.assertFalse(dialog.busy)
            create.assert_not_called()
            self.assertIn('Allow usage sync', dialog.feedback.get())
        dialog.close()

    def test_browser_code_shown_and_cancelled_on_close(self):
        dialog = SettingsWindow(self.app, tab='sync')
        def authenticate(on_code, cancel):
            on_code('ABCD-1234')
            cancel.wait(3)
            raise sync.SyncError('Cancelled')
        with patch('splitrail_desktop.sync.sign_in', side_effect=authenticate):
            dialog.authenticate()
            deadline = time.monotonic() + 2
            while not dialog.code_var.get() and time.monotonic() < deadline:
                self.app.update()
                time.sleep(.01)
            self.assertIn('ABCD-1234', dialog.code_var.get())
            dialog.close()
            self.assertTrue(dialog.cancel.is_set())
            self.app.update()

    def test_create_save_and_disconnect_with_options(self):
        dialog = SettingsWindow(self.app, tab='sync')
        dialog.consent.set(True)
        dialog.automatic.set(True)
        dialog.scope.set('Codex only')
        with patch('splitrail_desktop.sync.create_private_repo', return_value='example/private') as create, \
             patch('splitrail_desktop.sync._private_repo', return_value={'private': True}):
            dialog.connect()
            self.finish(dialog)
            create.assert_called_once_with('splitrail-usage')
        config = sync.settings()
        self.assertEqual(config['repository'], 'example/private')
        self.assertEqual(config['scope'], 'codex')
        self.assertTrue(config['automatic'])
        self.assertEqual(self.app.sync_button.cget('text'), 'Sync')
        dialog.disconnect()
        self.finish(dialog)
        self.assertIsNone(sync.settings())
        self.assertEqual(self.app.sync_button.cget('text'), 'Connect GitHub')
        dialog.close()

    def test_creation_success_then_config_failure_retries_existing_repo(self):
        dialog = SettingsWindow(self.app, tab='sync')
        dialog.consent.set(True)
        with patch('splitrail_desktop.sync.create_private_repo', return_value='example/private') as create, \
             patch('splitrail_desktop.sync.configure', side_effect=sync.SyncError('Temporary failure')):
            dialog.connect()
            self.finish(dialog)
            self.assertEqual(dialog.destination.get(), 'Use existing repository')
            self.assertEqual(dialog.repo_var.get(), 'example/private')
            dialog.connect()
            self.finish(dialog)
            create.assert_called_once()
        dialog.close()

    def test_corrupt_config_can_be_disconnected_without_blocking_local_app(self):
        (self.root / sync.SETTINGS_FILE).write_text('{bad json')
        dialog = SettingsWindow(self.app, tab='sync')
        self.assertTrue(dialog.invalid_config)
        self.assertNotEqual(dialog.disconnect_button.cget('state'), 'disabled')
        dialog.disconnect()
        self.finish(dialog)
        self.assertIsNone(sync.settings())
        dialog.close()

    def test_theme_changes_persist_and_keep_loaded_data(self):
        dialog = SettingsWindow(self.app)
        self.app._loading_usage_mode = self.app._usage_mode
        self.app._finish_usage(demo.usage(), None)
        dataset = self.app._dataset
        for name in ('Nord', 'Pearl'):
            dialog.theme.set(name)
            dialog.change_theme()
            self.app.update()
            self.assertEqual(preferences.load()['theme'], name)
            self.assertIs(self.app._dataset, dataset)
        dialog.close()

    def test_sync_actions_visible_at_minimum_size(self):
        dialog = SettingsWindow(self.app, tab='sync')
        dialog.geometry('720x700')
        self.app.update()
        for button in (dialog.connect_button, dialog.sync_button, dialog.disconnect_button):
            self.assertGreater(button.winfo_height(), 25)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
        dialog.select('general')
        self.app.update()
        button = dialog.source_button
        self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
        self.assertLessEqual(button.winfo_y() + button.winfo_height(), button.master.winfo_height())
        dialog.close()

    def test_demo_has_no_network_or_usage_access(self):
        dialog = SettingsWindow(self.app, tab='sync')
        self.app._demo_mode = True
        with patch('splitrail_desktop.sync.account') as account, patch('splitrail_desktop.app.run_splitrail') as usage:
            dialog.run('account', sync.account)
            self.app.refresh_all()
            account.assert_not_called()
            usage.assert_not_called()
        dialog.close()


if __name__ == '__main__':
    unittest.main()
