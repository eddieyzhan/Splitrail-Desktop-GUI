#!/usr/bin/env python3
"""Build AppKit + a disposable headless worker. No network or CI required."""
from __future__ import annotations

import os
import plistlib
import platform
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, **kwargs):
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Build this target on macOS.')
    sys.path.insert(0, str(ROOT / 'src'))
    from splitrail_desktop import __version__
    build = ROOT / 'build/macos-native'
    build.mkdir(parents=True, exist_ok=True)
    developer = Path(os.environ.get('DEVELOPER_DIR', '/Library/Developer/CommandLineTools'))
    swift = developer / 'usr/bin/swiftc'
    if not swift.exists():
        swift = developer / 'Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc'
    sdk = Path(os.environ.get('SDKROOT', str(developer / 'SDKs/MacOSX.sdk')))
    if not sdk.exists():
        sdk = developer / 'Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk'
    if not swift.exists() or not sdk.exists():
        raise SystemExit('Set DEVELOPER_DIR and SDKROOT to an existing Apple toolchain; this script never installs one.')
    for name, files, flags in (
        ('ModelTests', ['macos/AutoRecovery.swift', 'macos/RecoverySimulation.swift',
                        'macos/Model.swift', 'macos/tests/ModelTests.swift'], []),
        ('AutoRecoveryTests', ['macos/AutoRecovery.swift', 'macos/RecoverySimulation.swift',
                               'macos/tests/AutoRecoveryTests.swift'],
         ['-swift-version', '6', '-warnings-as-errors']),
        ('TurnStopperTests', ['macos/TurnStopper.swift', 'macos/tests/TurnStopperTests.swift'],
         ['-swift-version', '6', '-warnings-as-errors', '-D', 'TURN_STOPPER_TESTS']),
    ):
        test = build / name
        run(swift, '-O', '-sdk', sdk, '-target', f'{platform.machine()}-apple-macosx14.0',
            '-module-cache-path', build / 'swift-cache', *flags,
            *(ROOT / file for file in files), '-o', test)
        run(test)
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', build / 'dist',
        '--workpath', build / 'pyinstaller', ROOT / 'scripts/native_worker.spec', cwd=ROOT)
    bundle = build / 'Splitrail.app'
    if bundle.exists():
        shutil.rmtree(bundle)
    executable = bundle / 'Contents/MacOS/Splitrail'
    resources = bundle / 'Contents/Resources'
    executable.parent.mkdir(parents=True)
    resources.mkdir()
    shutil.copytree(build / 'dist/worker', resources / 'worker', symlinks=True)
    run(swift, '-O', '-whole-module-optimization', '-sdk', sdk, '-target',
        f'{platform.machine()}-apple-macosx14.0', '-module-cache-path', build / 'swift-cache',
        *sorted((ROOT / 'macos').glob('*.swift')), '-o', executable)
    info = {'CFBundleExecutable': 'Splitrail', 'CFBundleIdentifier': 'io.github.eddieyzhan.splitrail-desktop',
            'CFBundleName': 'Splitrail', 'CFBundleDisplayName': 'Splitrail', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': __version__, 'CFBundleVersion': __version__,
            'LSUIElement': True, 'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
            'NSPrincipalClass': 'NSApplication',
            'NSAccessibilityUsageDescription': 'Splitrail presses the Codex Stop button when your armed quota limit is reached. It keeps the chat open.'}
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign', '--force', '--deep', '--sign', '-', bundle)
    run('codesign', '--verify', '--deep', '--strict', bundle)
    run(executable, '--self-check')
    with tempfile.TemporaryDirectory(prefix='splitrail-native-smoke-') as temporary:
        environment = {**os.environ, 'SPLITRAIL_DATA_DIR': temporary}
        run(executable, '--demo', '--smoke-ui', env=environment, timeout=30)
        # Assert the bundled worker is headless and can project synthetic usage.
        import json
        request, response = Path(temporary) / 'request.json', Path(temporary) / 'response.json'
        request.write_text(json.dumps({'task': 'self-check', 'options': {}}))
        run(resources / 'worker/splitrail-worker', request, response, env=environment, timeout=30)
        result = json.loads(response.read_text())
        assert result.get('result', {}).get('headless') is True, result
    from smoke_collector import check_collector
    check_collector(resources / 'worker/splitrail-worker', cwd=resources)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='release-', dir=build) as directory:
        staging = Path(directory)
        shutil.copytree(bundle, staging / bundle.name, symlinks=True)
        for name in ('LICENSE', 'PRIVACY.md'):
            shutil.copyfile(ROOT / name, staging / name)
        for name in ('INSTALL.md', 'THIRD_PARTY.md', 'MACOS.md', 'TURN_INTERRUPTION.md', 'AUTO_RECOVERY.md'):
            shutil.copyfile(ROOT / 'docs' / name, staging / name)
        shutil.copytree(ROOT / 'docs/licenses', staging / 'licenses')
        for package in ('ijson', 'psutil'):
            dist = distribution(package)
            for path in dist.files or []:
                if '.dist-info/licenses/' in str(path):
                    target = staging / 'licenses' / package / str(path).split('.dist-info/licenses/', 1)[1]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(dist.locate_file(path), target)
        run('ditto', '-c', '-k', '--sequesterRsrc', staging, output / f'splitrail-desktop-macos-{platform.machine()}.zip')
    print(f'Built and verified {bundle}')


if __name__ == '__main__':
    main()
