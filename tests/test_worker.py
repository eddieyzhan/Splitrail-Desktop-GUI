import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from splitrail_desktop import demo
from splitrail_desktop.runner import LocalCommandError
from splitrail_desktop.worker import BackgroundWorker, encode_result, decode_result
from splitrail_desktop.domain import HourlyUsage


class WorkerTests(unittest.TestCase):
    def test_result_roundtrip_preserves_display_names_counts_diagnostics_and_hours(self):
        result = demo.usage()
        day = result.dataset.days[0]
        result = replace(result, dataset=replace(result.dataset, ignored_raw_messages=True,
                         analyzer_conversation_totals={'Codex CLI · abc123': 1234},
                         hours=(HourlyUsage(day.analyzer, day.day, 12, day.tokens, day.cost),)))
        raw = json.dumps(encode_result(result), default=lambda value: value.isoformat())
        self.assertEqual(decode_result(json.loads(raw)), result)

    @unittest.skipIf(os.name == 'nt', 'POSIX executable fixture; native smoke covers Windows helpers')
    def test_real_helper_returns_aggregates_and_honours_detail_option(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = json.loads((Path(__file__).parent / 'fixtures/stats_valid.json').read_text())
            collector = root / 'collector'
            collector.write_text('#!' + sys.executable + '\nimport sys,json\n'
                'if "--version" in sys.argv: print("splitrail 3.10.3")\n'
                'else:\n'
                f'    open({str(root / "args")!r},"w").write(json.dumps(sys.argv[1:]))\n'
                f'    print({json.dumps(payload)!r})\n')
            collector.chmod(0o755)
            worker = BackgroundWorker()
            self.addCleanup(worker.close)
            with patch('splitrail_desktop.portable.data_dir', return_value=root / 'data'), \
                 patch.dict(os.environ, {'SPLITRAIL_BIN': str(collector), 'CODEX_HOME': str(root / 'codex')}):
                for detail in (False, True):
                    result = worker.run('usage', mode='local', include_hours=detail, automatic_prices=False)
                    self.assertTrue(result.dataset.days)
                    arguments = json.loads((root / 'args').read_text())
                    self.assertEqual('--include-messages' in arguments, detail)
                    self.assertFalse(worker._processes)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-tree fixture')
    def test_closing_stops_helper_and_its_collector(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector = root / 'collector'
            collector.write_text('#!' + sys.executable + '\nimport sys,time,os\n'
                'if "--version" in sys.argv: print("splitrail 3.10.3")\n'
                f'else:\n    open({str(root / "pid")!r},"w").write(str(os.getpid()))\n    time.sleep(60)\n')
            collector.chmod(0o755)
            worker = BackgroundWorker()
            self.addCleanup(worker.close)
            errors = []
            def collect():
                try:
                    worker.run('usage', mode='local', include_hours=False, automatic_prices=False)
                except LocalCommandError as error:
                    errors.append(error)
            with patch('splitrail_desktop.portable.data_dir', return_value=root / 'data'), \
                 patch.dict(os.environ, {'SPLITRAIL_BIN': str(collector), 'CODEX_HOME': str(root / 'codex')}):
                thread = threading.Thread(target=collect)
                thread.start()
                deadline = time.monotonic() + 10
                while not (root / 'pid').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / 'pid').exists())
                pid = int((root / 'pid').read_text())
                worker.close()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertTrue(errors)
                self.assertTrue(not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE)

    def test_closed_worker_cannot_start_more_work(self):
        worker = BackgroundWorker()
        worker.close()
        with self.assertRaises(LocalCommandError):
            worker.run('usage')
