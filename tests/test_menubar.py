from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from qt_support import QtCase, APP
from splitrail_desktop import demo
from splitrail_desktop.qt_app import DashboardWindow
from splitrail_desktop.menubar import MenuBar


class MenuBarTests(QtCase):
    def test_menu_reopens_destroyed_dashboard_without_losing_data_or_page(self):
        c = self.controller
        c._state['onboarding'] = False
        c._accept_usage(demo.usage())
        c.quota_snapshot = demo.quota()
        directory = Path(__file__).parents[1] / 'src/splitrail_desktop/qml'
        dashboard = DashboardWindow(c, directory, 'Sans Serif')
        self.addCleanup(dashboard.close)
        self.assertTrue(dashboard.show())
        was_quit = APP.quitOnLastWindowClosed()
        self.addCleanup(APP.setQuitOnLastWindowClosed, was_quit)
        with patch('splitrail_desktop.menubar.QSystemTrayIcon'):
            menu = MenuBar(APP, dashboard, c)
        self.addCleanup(menu.close)
        for _ in range(2):
            dashboard.window.findChild(QObject, 'dashboardView').setProperty('page', 'History')
            dashboard.window.resize(1000, 700)
            dashboard.window.close()
            QTest.qWait(50)
            self.assertIsNone(dashboard.engine)
            self.assertFalse(c.closed)
            self.assertTrue(c.state['hasUsage'])
            self.assertFalse(c._clock.isActive())
            menu.update_status()
            self.assertIn('% used', menu.status.text())
            next(a for a in menu.menu.actions() if a.text() == 'Open Splitrail').trigger()
            QTest.qWait(30)
            self.assertTrue(dashboard.window.isVisible())
            self.assertEqual(dashboard.window.width(), 1000)
            self.assertEqual(dashboard.window.findChild(QObject, 'dashboardView').property('page'), 'History')

