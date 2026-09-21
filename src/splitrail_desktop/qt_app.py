"""Qt Quick application entry point; resources work from source and the zip app."""
from __future__ import annotations

import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch


def run(*, demo=False, onboarding=False, smoke=False, codex_usage=False) -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
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
        controller = DesktopController(demo=demo or smoke, onboarding=onboarding, auto_refresh=not (demo or smoke), codex_usage=codex_usage)
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='splitrail-ui-')))
        for resource in files('splitrail_desktop').joinpath('qml').iterdir():
            if resource.is_file():
                (directory / resource.name).write_bytes(resource.read_bytes())
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda warnings: print("\n".join(warning.toString() for warning in warnings), file=sys.stderr))
        engine.rootContext().setContextProperty('bridge', controller)
        engine.rootContext().setContextProperty('appFont', family)
        engine.load(QUrl.fromLocalFile(str(directory / 'Main.qml')))
        if not engine.rootObjects():
            controller.close()
            return 1
        if smoke:
            QTimer.singleShot(700, app.quit)
        try:
            return app.exec()
        finally:
            controller.close()
            import shiboken6
            shiboken6.delete(engine)
