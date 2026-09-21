"""Shared Qt test setup. All accounts and usage directories are isolated."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from splitrail_desktop.desktop import DesktopController

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
        # Any unexpected network/collector call fails the test, never uses a real account.
        for target in ('splitrail_desktop.sync._api', 'splitrail_desktop.sync.account',
                       'splitrail_desktop.desktop.run_quota_axi', 'splitrail_desktop.desktop.run_splitrail'):
            guard = patch(target, side_effect=AssertionError('Unexpected external operation'))
            guard.start()
            self.addCleanup(guard.stop)
        self.controller = DesktopController(auto_refresh=False)
        self.addCleanup(self.controller.close)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), 'Background operation did not finish')
