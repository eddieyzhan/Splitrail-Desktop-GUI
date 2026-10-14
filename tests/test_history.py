from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from splitrail_desktop import history
from splitrail_desktop.domain import TokenUsage, UsageDataset, HourlyUsage
from splitrail_desktop.portable import usage_dataset
from splitrail_desktop.sync_payload import decode_dataset, encode_dataset
from test_combined import collector_fixture, event


class PreservedHistoryTests(unittest.TestCase):
    def test_history_connection_closes_and_rolls_back_failed_update(self):
        path = self.root / 'transaction.sqlite'
        with history._connect(path) as connection:
            connection.execute('CREATE TABLE example (value INTEGER)')
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
        with self.assertRaises(RuntimeError):
            with history._connect(path) as connection:
                connection.execute('INSERT INTO example VALUES (1)')
                raise RuntimeError('failed update')
        with history._connect(path) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM example').fetchone()[0], 0)

    def test_deferred_hourly_detail_catches_up_without_counting_daily_totals_twice(self):
        self.baseline = self.reference = UsageDataset((), {})
        self.initialize()
        row = replace(collector_fixture().days[1], day=date(2026, 9, 3))
        daily = UsageDataset((row,), {row.analyzer: row.conversations})
        hour = HourlyUsage(row.analyzer, row.day, 10, row.tokens, row.cost)
        detailed = replace(daily, hours=(hour,))
        first = self.record([], daily)
        self.assertFalse(first.hours)
        second = self.record([], detailed)
        self.assertEqual(first.days, second.days)
        self.assertEqual(second.hours, (hour,))
        self.assertEqual(self.record([], daily), second)
        self.assertEqual(self.record([], detailed), second)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = event('old')
        self.now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
        self.baseline = collector_fixture()
        self.reference = usage_dataset([self.old])

    def initialize(self, **kwargs):
        history.initialize(self.baseline, self.reference, [self.old],
                           directory=self.root, now=self.now, **kwargs)

    def record(self, events, dataset=None):
        return history.record_usage(dataset or usage_dataset(events), events, directory=self.root)[0]

    def test_checkpoint_keeps_historical_tokens_costs_models_and_hourly_rows(self):
        self.initialize()
        expected = decode_dataset(encode_dataset(self.baseline, '0' * 32, 'all'))
        # A new reader can calculate different old values or lose all old logs.
        changed = dict(self.old)
        changed['usage'] = {**changed['usage'], 'input_tokens': 900_000}
        for events in ([changed], []):
            result = self.record(events)
            self.assertEqual({(r.day, r.analyzer): r for r in result.days},
                             {(r.day, r.analyzer): r for r in expected.days})
            self.assertEqual(result.hours, expected.hours)
        with self.assertRaises(FileExistsError):
            self.initialize()

    def test_new_request_counted_once_survives_deletion_restart_and_repricing(self):
        self.initialize()
        request = event('new', '2026-09-03')
        first = self.record([self.old, request])
        new_day = next(row for row in first.days if row.day == date(2026, 9, 3))
        self.assertEqual(new_day.tokens.total, 300_100)
        self.assertGreater(new_day.cost, 0)
        with patch('splitrail_desktop.portable.estimate_cost', return_value=9999):
            repeated = self.record([self.old, request])
            deleted = self.record([])
        self.assertEqual(first, repeated)
        self.assertEqual(first, deleted)
        self.assertEqual(self.record([request]), first)
        self.assertEqual(sum(row.tokens.total for row in first.hours), 300_100)

    def test_cutover_midday_excludes_seen_requests_and_includes_new_ones(self):
        self.old = event('seen-today', '2026-09-02')
        self.reference = usage_dataset([self.old])
        self.baseline = self.reference
        self.initialize()
        request = event('new-today', '2026-09-02')
        result = self.record([self.old, request])
        self.assertEqual(sum(row.tokens.total for row in result.days), 600_200)
        self.assertEqual(self.record([request]), result)

    def test_new_accounting_version_checkpoints_existing_results(self):
        self.initialize()
        request = event('already-recorded', '2026-09-03')
        before = self.record([request])
        changed_identity = {**request, 'id': 'f' * 64}
        with patch.object(history, 'ACCOUNTING_VERSION', 2):
            after = self.record([changed_identity])
            self.assertEqual(after, before)
            new = event('future', '2099-01-01')
            later = self.record([changed_identity, new])
        self.assertEqual(sum(row.tokens.total for row in later.days),
                         sum(row.tokens.total for row in before.days) + 300_100)

    def test_other_tools_add_only_future_deltas_without_readding_restored_history(self):
        self.reference = self.baseline
        self.initialize()
        row = replace(self.baseline.days[1], day=date(2026, 9, 3))
        candidate = UsageDataset((row,), {row.analyzer: 3})
        first = self.record([], candidate)
        self.assertEqual(sum(r.tokens.total for r in first.days),
                         sum(r.tokens.total for r in self.baseline.days) + row.tokens.total)
        self.assertEqual(self.record([], candidate), first)
        self.assertEqual(self.record([], UsageDataset((), {})), first)
        increased = replace(row, tokens=row.tokens + TokenUsage(input=10), cost=row.cost + 1)
        second = self.record([], replace(candidate, days=(increased,)))
        self.assertEqual(sum(r.tokens.total for r in second.days),
                         sum(r.tokens.total for r in first.days) + 10)

    def test_remote_historical_recalculation_cannot_replace_checkpoint(self):
        device = 'a' * 32
        remote = encode_dataset(self.baseline, device, 'all')
        self.initialize(remote={device: remote})
        lower = encode_dataset(UsageDataset((), {}), device, 'all')
        result = history.record_remote(lower, directory=self.root)
        self.assertEqual(sorted(result['days'], key=lambda r: (r['day'], r['tool'])),
                         sorted(remote['days'], key=lambda r: (r['day'], r['tool'])))
        future = replace(self.baseline.days[0], day=date(2026, 9, 3))
        payload = encode_dataset(UsageDataset((future,), {'Codex CLI': 3}), device, 'all')
        first = history.record_remote(payload, directory=self.root)
        self.assertEqual(history.record_remote(payload, directory=self.root), first)
        self.assertEqual(history.record_remote(lower, directory=self.root), first)

    def test_codex_view_and_all_tools_share_one_checkpoint_without_pi_overlap(self):
        self.baseline = history.add_usage(self.baseline, usage_dataset([event('pi', source='Codex via Pi')]))
        self.initialize()
        all_tools = self.record([])
        codex, _ = history.record_usage(UsageDataset((), {}), [], scope='codex', directory=self.root)
        self.assertNotIn('Codex via Pi', {row.analyzer for row in all_tools.days})
        self.assertEqual({row.analyzer for row in codex.days}, {'Codex CLI', 'Codex via Pi'})
        self.assertEqual([row for row in all_tools.days if row.analyzer == 'Codex CLI'],
                         [row for row in codex.days if row.analyzer == 'Codex CLI'])

    def test_database_retains_no_request_content_and_transaction_rolls_back(self):
        self.initialize()
        new = event('new', '2026-09-03')
        new['prompt'] = 'PRIVATE_PROMPT_SENTINEL'
        self.record([new])
        raw = (self.root / history.HISTORY_FILE).read_bytes()
        self.assertNotIn(b'PRIVATE_PROMPT_SENTINEL', raw)
        invalid = {**event('broken', '2026-09-04'), 'usage': None}
        with self.assertRaises(TypeError):
            self.record([invalid], UsageDataset((), {}))
        with sqlite3.connect(self.root / history.HISTORY_FILE) as connection:
            self.assertIsNone(connection.execute('SELECT id FROM requests WHERE id=?', (invalid['id'],)).fetchone())


if __name__ == '__main__':
    unittest.main()
