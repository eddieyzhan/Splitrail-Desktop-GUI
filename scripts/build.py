#!/usr/bin/env python3
"""Build a source-only zip application without host paths or local timestamps."""
from __future__ import annotations

import io
import zipapp
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'src'
TARGET = ROOT / 'dist/splitrail-desktop.pyz'


def main() -> int:
    TARGET.parent.mkdir(exist_ok=True)
    entries = {path.relative_to(SOURCE).as_posix(): path.read_bytes()
               for path in (SOURCE / 'splitrail_desktop').glob('*.py')}
    entries['splitrail_desktop/model_prices.json'] = (SOURCE / 'splitrail_desktop/model_prices.json').read_bytes()
    entries['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    entries['__main__.py'] = b'from splitrail_desktop.__main__ import main\nraise SystemExit(main())\n'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, raw)
    buffer.seek(0)
    zipapp.create_archive(buffer, TARGET, interpreter='/usr/bin/env python3')
    TARGET.chmod(0o755)
    print(f'Built {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size:,} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
