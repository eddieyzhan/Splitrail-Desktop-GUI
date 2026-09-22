"""Use system input methods and image formats without GPL-only Qt add-ons."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [entry for entry in binaries
            if 'virtualkeyboard' not in entry[0].lower()
            and Path(entry[0]).stem.lower() not in {'qpdf', 'libqpdf'}]
