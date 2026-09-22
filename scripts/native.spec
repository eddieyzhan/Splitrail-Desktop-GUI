"""Run through scripts/build_native.py on each target operating system."""
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'src'))
from splitrail_desktop import __version__
from PyInstaller.utils.hooks import collect_data_files

analysis = Analysis(
    [str(root / 'scripts/native_entry.py')],
    pathex=[str(root / 'src')],
    hookspath=[str(root / 'scripts/hooks')],
    datas=collect_data_files('splitrail_desktop'),
    hiddenimports=['ijson.backends.yajl2_c', 'PySide6.QtQuick'],
    excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick'],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='Splitrail',
          console=False, strip=False, upx=False)
folder = COLLECT(exe, analysis.binaries, analysis.datas, name='Splitrail',
                 strip=False, upx=False)
if sys.platform == 'darwin':
    app = BUNDLE(folder, name='Splitrail.app',
                 bundle_identifier='io.github.eddieyzhan.splitrail-desktop',
                 version=__version__,
                 info_plist={'NSHighResolutionCapable': True,
                             'LSMinimumSystemVersion': '14.0'})
