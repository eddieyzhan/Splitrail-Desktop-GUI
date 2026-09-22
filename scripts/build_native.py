#!/usr/bin/env python3
"""Build, smoke-test and archive a native app without collecting local user data."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    build_root = ROOT / 'build' / sys.platform
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(build_root / 'native-dist'),
                    '--workpath', str(build_root / 'pyinstaller'),
                    str(ROOT / 'scripts/native.spec')], cwd=ROOT, check=True)
    platform_name = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')
    arch = {'AMD64': 'x86_64', 'aarch64': 'arm64'}.get(platform.machine(), platform.machine())
    native = build_root / 'native-dist'
    bundle = native / ('Splitrail.app' if sys.platform == 'darwin' else 'Splitrail')
    executable = (bundle / 'Contents/MacOS/Splitrail' if sys.platform == 'darwin'
                  else bundle / ('Splitrail.exe' if sys.platform == 'win32' else 'Splitrail'))
    for arguments in (['--self-check'], ['--smoke-ui'], ['--smoke-ui', '--onboarding']):
        subprocess.run([str(executable), *arguments], cwd=native, timeout=45, check=True)

    # Keep notices alongside the bundle so macOS's ad-hoc signature stays intact.
    # A fresh staging directory cannot retain stale files from an earlier build.
    temporary = tempfile.TemporaryDirectory(prefix='release-', dir=build_root)
    staging = Path(temporary.name)
    shutil.copytree(bundle, staging / bundle.name, symlinks=True)
    for name in ('LICENSE', 'PRIVACY.md'):
        shutil.copyfile(ROOT / name, staging / name)
    shutil.copyfile(ROOT / 'docs/INSTALL.md', staging / 'INSTALL.md')
    shutil.copyfile(ROOT / 'docs/THIRD_PARTY.md', staging / 'THIRD_PARTY.md')
    notices = staging / 'licenses'
    shutil.copytree(ROOT / 'docs/licenses', notices)
    for package in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'ijson'):
        dist = distribution(package)
        for path in dist.files or []:
            if '.dist-info/licenses/' in str(path).replace('\\', '/'):
                relative = str(path).replace('\\', '/').split('.dist-info/licenses/', 1)[1]
                target = notices / package / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dist.locate_file(path), target)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    basename = output / f'splitrail-desktop-{platform_name}-{arch}'
    if sys.platform == 'darwin':
        # ditto preserves bundle symlinks, executable bits and extended attributes.
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', str(staging),
                        str(basename.with_suffix('.zip'))], check=True)
    else:
        shutil.make_archive(str(basename), 'zip' if sys.platform == 'win32' else 'gztar', staging)
    print(f'Native {platform_name}/{arch} release built and smoke-tested.')
    temporary.cleanup()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
