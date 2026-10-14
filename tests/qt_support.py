"""Shared Qt test setup. All accounts and usage directories are isolated."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

if sys.platform.startswith('linux') and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from splitrail_desktop.platform_support import prepare_qt
prepare_qt()

from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from splitrail_desktop.desktop import DesktopController
from splitrail_desktop.worker import execute

QQuickStyle.setStyle('Basic')
APP = QApplication.instance() or QApplication([])


class QtCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        for target in ('splitrail_desktop.portable.data_dir', 'splitrail_desktop.sync.data_dir'):
            patcher = patch(target, return_value=self.data)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {'SPLITRAIL_BIN': '/example/splitrail'})
        env.start()
        self.addCleanup(env.stop)
        prices = patch('splitrail_desktop.desktop.refresh_prices', return_value=False)
        prices.start()
        self.addCleanup(prices.stop)
        # Any unexpected network/collector call fails the test, never uses a real account.
        for target in ('splitrail_desktop.sync._api', 'splitrail_desktop.sync.account',
                       'splitrail_desktop.desktop.run_quota_axi', 'splitrail_desktop.runner.run_splitrail',
                       'splitrail_desktop.desktop.stop_codex_processes'):
            guard = patch(target, side_effect=AssertionError('Unexpected external operation'))
            guard.start()
            self.addCleanup(guard.stop)
        scanner = patch('splitrail_desktop.desktop.discover_codex_processes', return_value=())
        scanner.start()
        self.addCleanup(scanner.stop)
        probe = patch('splitrail_desktop.desktop.codex_activity_stamp', return_value='unchanged')
        probe.start()
        self.addCleanup(probe.stop)
        self.controller = DesktopController(auto_refresh=False)
        # Controller tests keep their synthetic engines in-process. Separate
        # integration tests exercise the real helper, protocol and cancellation.
        worker = patch.object(self.controller._worker, 'run', side_effect=lambda task, **options: execute(task, options))
        worker.start()
        self.addCleanup(worker.stop)
        catalog = patch('splitrail_desktop.catalog_updates.refresh_prices', return_value=False)
        catalog.start()
        self.addCleanup(catalog.stop)
        self.addCleanup(self.controller.close)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), 'Background operation did not finish')
