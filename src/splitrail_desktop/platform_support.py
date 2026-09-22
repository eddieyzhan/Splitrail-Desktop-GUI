"""Small native desktop setup shared by the application and UI tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_qt_dll_directory = None


def prepare_desktop_path() -> None:
    """Finder does not inherit interactive shell PATH customizations."""
    if sys.platform == 'darwin':
        paths = os.environ.get('PATH', '/usr/bin:/bin').split(os.pathsep)
        for directory in (Path.home() / '.local/bin', Path.home() / '.cargo/bin',
                          Path.home() / '.npm-global/bin', Path('/opt/homebrew/bin'),
                          Path('/usr/local/bin')):
            if directory.is_dir() and str(directory) not in paths:
                paths.append(str(directory))
        os.environ['PATH'] = os.pathsep.join(paths)


def prepare_qt() -> None:
    """Keep Qt's own DLL directory available while QML loads plugins on Windows.

    Python's secure DLL lookup does not use PATH for these dependencies. Keep
    the directory handle alive for the process, including delayed QML imports.
    """
    global _qt_dll_directory
    if sys.platform == 'win32' and _qt_dll_directory is None:
        import PySide6
        _qt_dll_directory = os.add_dll_directory(str(Path(PySide6.__file__).resolve().parent))


def command_options() -> dict:
    """Do not flash a console when refreshing from a Windows GUI."""
    if sys.platform == 'win32':
        import subprocess
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}
