import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.platform_support import prepare_desktop_path, command_options


class PlatformTests(unittest.TestCase):
    def test_windows_desktop_finds_user_tools_without_replacing_path(self):
        import shutil
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
