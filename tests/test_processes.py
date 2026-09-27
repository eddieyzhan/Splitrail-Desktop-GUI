from contextlib import nullcontext
from dataclasses import replace
import os
import subprocess
import sys
from threading import Event
import unittest
from unittest.mock import Mock, patch

import psutil

from splitrail_desktop.processes import (CodexProcess, _describe, _kind,
                                         discover_codex_processes, stop_codex_processes)


TARGET = CodexProcess(123, 1000, '/example/codex', '/example/project', 'CLI session', 'sleeping')


class ProcessTests(unittest.TestCase):
    def process(self):
        value = Mock(pid=123)
        value.username.return_value = 'example'
        value.exe.return_value = '/example/codex'
        value.parents.return_value = []
        value.oneshot.side_effect = nullcontext
        value.cmdline.return_value = ['/example/codex', '--model', 'example', 'resume', 'PRIVATE-PROMPT']
        value.cwd.return_value = '/example/project'
        value.create_time.return_value = 1000
        value.status.return_value = 'sleeping'
        return value

    def test_inventory_omits_prompts_and_checks_owner_executable_and_helpers(self):
        process = self.process()
        item = _describe(process, 'example')
        self.assertEqual(item, TARGET)
        self.assertNotIn('PRIVATE', repr(item))
        self.assertIsNone(_describe(process, 'another-user'))
        process.exe.return_value = '/example/python'
        self.assertIsNone(_describe(process, 'example'))
        process.exe.return_value = '/example/codex'
        process.parents.return_value = [Mock(pid=os.getpid())]
        self.assertIsNone(_describe(process, 'example'))

    def test_kind_reads_only_command_prefix(self):
        self.assertEqual(_kind(['codex', '-s', 'read-only', '-a', 'never', 'app-server']), 'App server · multiple chats')
        self.assertEqual(_kind(['codex', 'exec', 'app-server']), 'CLI task')
        self.assertEqual(_kind(['codex', 'Please inspect app-server']), 'CLI session')

    def test_scanner_filters_names_before_reading_arguments(self):
        candidates = [Mock(pid=1, info={'name': 'python'}), Mock(pid=123, info={'name': 'codex'})]
        process = self.process()
        with patch('splitrail_desktop.processes.psutil.process_iter', return_value=candidates), \
             patch('splitrail_desktop.processes.psutil.Process', return_value=process) as factory:
            self.assertEqual(discover_codex_processes(), (TARGET,))
            self.assertEqual(factory.call_count, 2)  # our owner and one candidate

    def test_stop_rechecks_start_time_executable_owner_and_cancellation(self):
        for changed in (replace(TARGET, started=1001), replace(TARGET, executable='/other/codex'), None):
            process = self.process()
            with patch('splitrail_desktop.processes.psutil.Process', return_value=process), \
                 patch('splitrail_desktop.processes._describe', return_value=changed):
                result = stop_codex_processes((TARGET,), Event())
                process.terminate.assert_not_called()
                self.assertTrue(result.failures)
        cancel = Event()
        cancel.set()
        with patch('splitrail_desktop.processes.psutil.Process') as factory:
            stop_codex_processes((TARGET,), cancel)
            factory.return_value.terminate.assert_not_called()

    def test_exited_or_inaccessible_process_is_handled_without_force_kill(self):
        for failure in (psutil.NoSuchProcess(123), psutil.AccessDenied(123)):
            process = self.process()
            process.terminate.side_effect = failure
            with patch('splitrail_desktop.processes.psutil.Process', return_value=process), \
                 patch('splitrail_desktop.processes._describe', return_value=TARGET):
                result = stop_codex_processes((TARGET,), Event())
                self.assertEqual(bool(result.failures), isinstance(failure, psutil.AccessDenied))
                process.kill.assert_not_called()

    def test_real_stop_uses_only_disposable_synthetic_process(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            target = replace(TARGET, pid=child.pid, started=psutil.Process(child.pid).create_time())
            # Classify this disposable Python sleeper as the test's synthetic
            # engine. No discovery or real Codex process is ever targeted.
            with patch('splitrail_desktop.processes._describe', return_value=target):
                result = stop_codex_processes((target,), Event())
            self.assertEqual(result.stopped, (child.pid,))
            self.assertEqual(result.failures, ())
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
