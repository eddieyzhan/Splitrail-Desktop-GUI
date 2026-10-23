#!/usr/bin/env python3
"""Compare Mac dashboard memory with synthetic data and no account access.

Run with the release dependencies installed. For an older checkout, pass
--source /path/to/checkout. QT_QUICK_BACKEND=rhi selects the Metal comparison.
Output uses macOS physical footprint, not virtual address space or RSS.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import time


def footprint_mib():
    result = subprocess.run(['/usr/bin/footprint', '-p', str(os.getpid())],
                            capture_output=True, text=True, check=True)
    match = re.search(r'Footprint:\s+([\d.]+)\s+(KB|MB|GB)', result.stdout)
    if not match:
        raise RuntimeError('macOS footprint did not return a memory measurement')
    return float(match[1]) * {'KB': 1 / 1024, 'MB': 1, 'GB': 1024}[match[2]]


def cpu_seconds():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--cycles', type=int, default=3)
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    args = parser.parse_args()
    if sys.platform != 'darwin' or min(args.seconds, args.cycles) < 1:
        parser.error('Requires macOS and positive sample duration/cycle count')
    sys.path.insert(0, str(args.source / 'src'))
    with tempfile.TemporaryDirectory(prefix='splitrail-profile-') as data:
        os.environ['SPLITRAIL_DATA_DIR'] = data
        from splitrail_desktop.platform_support import prepare_qt
        prepare_qt()
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from PySide6.QtQuickControls2 import QQuickStyle
        from splitrail_desktop.desktop import DesktopController
        from splitrail_desktop.qt_app import DashboardWindow

        QQuickStyle.setStyle('Basic')
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        controller = DesktopController(demo=True, auto_refresh=False)
        dashboard = DashboardWindow(controller, args.source / 'src/splitrail_desktop/qml', app.font().family())
        dashboard.release_when_hidden = True
        controller._state['menuBarMode'] = True
        if not dashboard.show():
            raise RuntimeError('Dashboard failed to open')
        if args.width or args.height:
            dashboard.window.resize(args.width or dashboard.window.width(), args.height or dashboard.window.height())
        rows = []
        remaining = args.cycles

        def measure(stage, done):
            start_time, start_cpu = time.monotonic(), cpu_seconds()
            def finish():
                row = {'stage': stage, 'seconds': round(time.monotonic() - start_time, 1),
                       'cpu_percent': round(100 * (cpu_seconds() - start_cpu) / (time.monotonic() - start_time), 3),
                       'physical_mib': footprint_mib()}
                if dashboard.window:
                    row['size'] = [dashboard.window.width(), dashboard.window.height()]
                rows.append(row)
                print(json.dumps(row), flush=True)
                done()
            QTimer.singleShot(args.seconds * 1000, finish)

        def hide():
            dashboard.window.hide()
            QTimer.singleShot(1000, lambda: measure('hidden', reopen))

        def reopen():
            nonlocal remaining
            remaining -= 1
            if not remaining:
                app.quit()
                return
            dashboard.show()
            QTimer.singleShot(1000, lambda: measure('reopened', hide))

        QTimer.singleShot(2000, lambda: measure('visible', hide))
        try:
            return app.exec()
        finally:
            controller.close()
            dashboard.close()


if __name__ == '__main__':
    raise SystemExit(main())
