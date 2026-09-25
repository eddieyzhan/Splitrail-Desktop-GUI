"""Small native desktop setup shared by the application and UI tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_qt_dll_directory = None


def prepare_desktop_path() -> None:
    """Find per-user tools even when the desktop inherits an older PATH."""
    directories = ()
    if sys.platform == 'darwin':
        directories = (Path.home() / '.local/bin', Path.home() / '.cargo/bin',
                       Path.home() / '.npm-global/bin', Path('/opt/homebrew/bin'),
                       Path('/usr/local/bin'))
    elif sys.platform == 'win32':
        directories = (Path.home() / '.local/bin', Path.home() / '.cargo/bin',
                       Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'npm')
    elif sys.platform.startswith('linux'):
        # Desktop launchers may not inherit the user's interactive shell PATH.
        directories = (Path.home() / '.local/bin', Path.home() / '.cargo/bin',
                       Path.home() / '.npm-global/bin', Path('/usr/local/bin'))
    if directories:
        paths = os.environ.get('PATH', os.defpath).split(os.pathsep)
        known = {os.path.normcase(path) for path in paths}
        for directory in directories:
            if directory.is_dir() and os.path.normcase(str(directory)) not in known:
                paths.append(str(directory))
                known.add(os.path.normcase(str(directory)))
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
