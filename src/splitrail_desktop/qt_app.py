"""Qt Quick application entry point; resources work from source and the zip app."""
from __future__ import annotations

import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch


class DashboardWindow:
    """Release the complete QML engine when hidden; retain only controller state."""
    def __init__(self, controller, directory, family):
        self.controller, self.directory, self.family = controller, directory, family
        self.engine = None
        self.window = None
        self.geometry = None
        self.release_when_hidden = False

    def show(self):
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtQml import QQmlApplicationEngine
        if self.engine is None:
            self.engine = QQmlApplicationEngine()
            self.engine.warnings.connect(lambda warnings: print('\n'.join(w.toString() for w in warnings), file=sys.stderr))
            self.engine.rootContext().setContextProperty('bridge', self.controller)
            self.engine.rootContext().setContextProperty('appFont', self.family)
            self.engine.load(QUrl.fromLocalFile(str(self.directory / 'Main.qml')))
            if not self.engine.rootObjects():
                self.close()
                return False
            self.window = self.engine.rootObjects()[0]
            if self.geometry is not None:
                self.window.setGeometry(self.geometry)
            self.window.setPersistentGraphics(False)
            self.window.setPersistentSceneGraph(False)
            self.window.visibleChanged.connect(lambda: QTimer.singleShot(0, self.release_hidden))
        self.window.showNormal()
        self.window.raise_()
        self.window.requestActivate()
        self.controller.setWindowVisible(True)
        return True

    def release_hidden(self):
        if self.release_when_hidden and self.window is not None and not self.window.isVisible():
            self.close()

    def close(self):
        if self.engine is not None:
            import gc
            import shiboken6
            if self.window is not None:
                self.geometry = self.window.geometry()
            engine, self.engine, self.window = self.engine, None, None
            shiboken6.delete(engine)
            gc.collect()


def run(*, demo=False, onboarding=False, smoke=False, codex_usage=False, quota_page=False, limits_page=False) -> int:
    from .platform_support import prepare_qt
    prepare_qt()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQuickControls2 import QQuickStyle
    from .desktop import DesktopController

    QQuickStyle.setStyle('Basic')
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Splitrail')
    app.setOrganizationName('Splitrail Desktop')
    available = set(QFontDatabase.families())
    candidates = ('.AppleSystemUIFont', 'SF Pro Text', 'Inter', 'Segoe UI', 'Adwaita Sans', 'DejaVu Sans')
    family = next((name for name in candidates if name in available), app.font().family())
    app.setFont(QFont(family, 10))
    with ExitStack() as stack:
        if demo or smoke:
            data = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            for target in ('splitrail_desktop.portable.data_dir', 'splitrail_desktop.sync.data_dir'):
                stack.enter_context(patch(target, return_value=data))
        controller = DesktopController(demo=demo or smoke, onboarding=onboarding,
                                       auto_refresh=not (demo or smoke), codex_usage=codex_usage,
                                       quota_page=quota_page, limits_page=limits_page)
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='splitrail-ui-')))
        for resource in files('splitrail_desktop').joinpath('qml').iterdir():
            if resource.is_file():
                (directory / resource.name).write_bytes(resource.read_bytes())
        dashboard = DashboardWindow(controller, directory, family)
        if not dashboard.show():
            controller.close()
            return 1
        menu_bar = None
        if sys.platform == 'darwin' and not (demo or smoke):
            from PySide6.QtWidgets import QSystemTrayIcon
            if QSystemTrayIcon.isSystemTrayAvailable():
                from .menubar import MenuBar
                menu_bar = MenuBar(app, dashboard, controller)
        if smoke:
            QTimer.singleShot(700, app.quit)
        try:
            return app.exec()
        finally:
            if menu_bar:
                menu_bar.close()
            controller.close()
            dashboard.close()
