import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.platform_support import prepare_desktop_path, command_options


class PlatformTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux executable discovery')
    def test_linux_desktop_finds_user_tools_and_preserves_path_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            existing = home / 'existing'
            local_bin = home / '.local/bin'
            cargo_bin = home / '.cargo/bin'
            npm_bin = home / '.npm-global/bin'
            for folder, command in ((existing, 'splitrail'), (local_bin, 'gh'),
                                    (cargo_bin, 'splitrail'), (npm_bin, 'quota-axi')):
                folder.mkdir(parents=True)
                executable = folder / command
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
            with patch('splitrail_desktop.platform_support.Path.home', return_value=home), \
                 patch.dict(os.environ, {'PATH': os.pathsep.join((str(existing), str(local_bin)))}):
                prepare_desktop_path()
                first = os.environ['PATH']
                prepare_desktop_path()
                self.assertEqual(first, os.environ['PATH'])
                self.assertEqual(first.split(os.pathsep)[:4],
                                 list(map(str, (existing, local_bin, cargo_bin, npm_bin))))
                self.assertEqual(first.split(os.pathsep).count(str(local_bin)), 1)
                self.assertEqual(shutil.which('splitrail'), str(existing / 'splitrail'))
                self.assertEqual(shutil.which('gh'), str(local_bin / 'gh'))
                self.assertEqual(shutil.which('quota-axi'), str(npm_bin / 'quota-axi'))

    def test_linux_desktop_without_path_keeps_system_defaults_and_skips_missing_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            local_bin = home / '.local/bin'
            local_bin.mkdir(parents=True)
            # A file with a bin directory's name is not a usable PATH entry.
            (home / '.cargo').mkdir()
            (home / '.cargo/bin').touch()
            with patch('splitrail_desktop.platform_support.sys.platform', 'linux'), \
                 patch('splitrail_desktop.platform_support.Path.home', return_value=home), \
                 patch.dict(os.environ):
                os.environ.pop('PATH', None)
                prepare_desktop_path()
                paths = os.environ['PATH'].split(os.pathsep)
                defaults = os.defpath.split(os.pathsep)
                self.assertEqual(paths[:len(defaults)], defaults)
                self.assertIn(str(local_bin), paths)
                self.assertNotIn(str(home / '.cargo/bin'), paths)
                self.assertNotIn(str(home / '.npm-global/bin'), paths)

    def test_windows_desktop_finds_user_tools_without_replacing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            local_bin = home / '.local/bin'
            npm = home / 'AppData/Roaming/npm'
            for folder in (local_bin, npm):
                folder.mkdir(parents=True)
            (local_bin / 'splitrail.exe').touch()
            with patch('splitrail_desktop.platform_support.sys.platform', 'win32'), \
                 patch('splitrail_desktop.platform_support.Path.home', return_value=home), \
                 patch.dict(os.environ, {'PATH': str(home / 'existing'), 'APPDATA': str(npm.parent)}):
                prepare_desktop_path()
                first = os.environ['PATH']
                prepare_desktop_path()
                self.assertEqual(first, os.environ['PATH'])
                self.assertEqual(first.split(os.pathsep), [str(home / 'existing'), str(local_bin), str(npm)])
                if os.name == 'nt':
                    self.assertEqual(Path(shutil.which('splitrail')), local_bin / 'splitrail.exe')

    def test_finder_path_preserves_existing_entries_and_adds_installed_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / '.local/bin').mkdir(parents=True)
            with patch('splitrail_desktop.platform_support.sys.platform', 'darwin'), \
                 patch('splitrail_desktop.platform_support.Path.home', return_value=home), \
                 patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
                prepare_desktop_path()
                first = os.environ['PATH']
                prepare_desktop_path()
                self.assertEqual(first, os.environ['PATH'])
                self.assertTrue(first.startswith('/usr/bin:/bin'))
                self.assertIn(str(home / '.local/bin'), first.split(os.pathsep))
                self.assertNotIn(str(home / '.cargo/bin'), first.split(os.pathsep))

    def test_native_child_runs_without_console_or_shell(self):
        import subprocess
        result = subprocess.run([sys.executable, '-c', 'print("synthetic")'],
                                capture_output=True, text=True, check=True,
                                **command_options())
        self.assertEqual(result.stdout.strip(), 'synthetic')
