"""Small disposable worker bundled with the native AppKit Mac application."""
from pathlib import Path

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / 'scripts/native_worker_entry.py')],
    pathex=[str(root / 'src')],
    datas=[(str(root / 'src/splitrail_desktop/model_prices.json'), 'splitrail_desktop')],
    hiddenimports=['ijson.backends.yajl2_c'],
    excludes=['PySide6', 'shiboken6', 'tkinter', 'unittest', 'test'],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='splitrail-worker',
          console=True, strip=False, upx=False)
folder = COLLECT(exe, analysis.binaries, analysis.datas, name='worker', strip=False, upx=False)
