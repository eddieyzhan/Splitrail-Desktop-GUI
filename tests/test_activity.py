import copy
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.activity import parse_collector_stream, read_collector, _BoundedStream
from splitrail_desktop.domain import StatsDataError
from splitrail_desktop.portable import make_event, usage_dataset
from splitrail_desktop.presentation import hourly_chart_buckets
from splitrail_desktop.sync_payload import encode_dataset, decode_dataset, add_devices


@contextmanager
def local_zone(zone):
    if not hasattr(time, 'tzset'):
        # Windows has no tzset. Exercise the same real ZoneInfo conversions
        # without changing the machine's timezone or skipping parser tests.
        class LocalDateTime(datetime):
            def astimezone(self, tz=None):
                return super().astimezone(tz if tz is not None else ZoneInfo(zone))
        with patch('splitrail_desktop.activity.datetime', LocalDateTime), \
             patch('splitrail_desktop.portable.datetime', LocalDateTime):
            yield
        return
    try:
        with patch.dict(os.environ, {'TZ': zone}):
            time.tzset()
            yield
    finally:
        time.tzset()



def fixture():
    stats = {'inputTokens': 100, 'outputTokens': 40, 'cachedTokens': 20,
             'cacheReadTokens': 20, 'reasoningTokens': 10, 'cost': 0.1}
    records = [{'date': stamp, 'role': 'assistant', 'model': 'gpt-5.4', 'stats': stats,
                'sessionName': 'PRIVATE TITLE', 'conversationHash': 'PRIVATE ID',
                'content': 'PRIVATE CONTENT'}
               for stamp in ('2026-09-22T02:10:00Z', '2026-09-22T02:40:00Z', '2026-09-22T14:15:00Z')]
    return {'analyzer_stats': [{'daily_stats': {'2026-09-22': {
        'date': '2026-09-22', 'stats': {'inputTokens': 300, 'outputTokens': 120,
        'cachedTokens': 60, 'reasoningTokens': 30, 'costCents': 30},
        'model_stats': {'gpt-5.4': {'inputTokens': 300, 'outputTokens': 120,
            'cachedTokens': 60, 'reasoningTokens': 30, 'cacheReadTokens': 60,
            'messageCount': 3, 'cost': 0.3}}, 'ai_messages': 3}},
        'messages': records, 'analyzer_name': 'Codex CLI', 'num_conversations': 1}]}


def inconsistent_reasoning_fixture(analyzer_name='Antigravity CLI'):
    payload = fixture()
    analyzer = payload['analyzer_stats'][0]
    analyzer['analyzer_name'] = analyzer_name
    # Daily/model totals are consistent, but the optional 02:00 detail claims
    # more included reasoning than output. The other hour remains usable.
    for record, output, reasoning, cost in zip(
            analyzer['messages'], (5, 5, 110), (12, 12, 6), (.01, .01, .28)):
        record['stats'] = dict(record['stats'], outputTokens=output,
                               reasoningTokens=reasoning, cost=cost)
    return payload


class HourlyUsageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch('splitrail_desktop.portable.data_dir', return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def parse(self, payload):
        with local_zone('UTC'):
            return parse_collector_stream(io.BytesIO(json.dumps(payload).encode()))

    def test_hourly_records_sum_without_double_counting_cached_or_reasoning(self):
        dataset = self.parse(fixture())
        self.assertEqual(len(dataset.hours), 2)
        self.assertEqual(dataset.hours[0].hour, 2)
        self.assertEqual(dataset.hours[0].tokens.total, 320)
        buckets = hourly_chart_buckets(dataset, date(2026, 9, 22))
        self.assertEqual(len(buckets), 24)
        self.assertEqual(buckets[0].total.tokens.total, 0)
        self.assertEqual(sum(b.total.tokens.total for b in buckets), dataset.days[0].tokens.total)
        self.assertAlmostEqual(sum(b.total.cost for b in buckets), dataset.days[0].cost)
        self.assertNotIn('PRIVATE', repr(dataset))

    def test_parser_handles_analyzer_name_before_or_after_records(self):
        payload = fixture()
        first = self.parse(payload)
        analyzer = payload['analyzer_stats'][0]
        payload['analyzer_stats'][0] = dict(reversed(list(analyzer.items())))
        self.assertEqual(self.parse(payload), first)

    def test_missing_or_invalid_timing_keeps_daily_totals(self):
        payload = fixture()
        payload['analyzer_stats'][0]['messages'][0]['date'] = 'invalid'
        payload['analyzer_stats'][0]['messages'][1]['date'] = '2026-09-22T02:40:00'
        dataset = self.parse(payload)
        self.assertEqual(dataset.days[0].tokens.total, 480)
        self.assertEqual(sum(h.tokens.total for h in dataset.hours), 160)

    def test_inconsistent_hourly_reasoning_keeps_daily_totals_and_valid_hours(self):
        for analyzer in ('Antigravity CLI', 'Codex CLI', 'Gemini CLI'):
            with self.subTest(analyzer=analyzer):
                payload = inconsistent_reasoning_fixture(analyzer)
                daily_only = copy.deepcopy(payload)
                daily_only['analyzer_stats'][0]['messages'] = []
                dataset = self.parse(payload)
                self.assertEqual(dataset.days, self.parse(daily_only).days)
                self.assertEqual(dataset.days[0].tokens.output, 120)
                self.assertEqual(dataset.days[0].tokens.reasoning, 30)
                self.assertEqual(dataset.days[0].cost, .3)
                if analyzer == 'Gemini CLI':
                    # Separate reasoning can legitimately exceed output.
                    self.assertEqual([h.hour for h in dataset.hours], [2, 14])
                    self.assertEqual(dataset.hours[0].tokens.reasoning, 24)
                else:
                    self.assertEqual([h.hour for h in dataset.hours], [14])
                    self.assertEqual(dataset.hours[0].tokens.output, 110)
                    self.assertEqual(dataset.hours[0].tokens.reasoning, 6)
                    self.assertEqual(dataset.hours[0].cost, .28)
                uploaded = encode_dataset(dataset, 'a' * 32, 'all')
                self.assertEqual(decode_dataset(uploaded).days, dataset.days)
                self.assertEqual(decode_dataset(uploaded).hours, dataset.hours)

    def test_user_records_do_not_add_usage(self):
        payload = fixture()
        payload['analyzer_stats'][0]['messages'][0]['role'] = 'user'
        self.assertEqual(sum(h.tokens.total for h in self.parse(payload).hours), 320)

    def test_custom_prices_apply_to_hourly_and_daily_data(self):
        from splitrail_desktop.pricing import save_override, reprice_dataset
        save_override('gpt-5.4', {'input': 1, 'output': 2, 'cache_read': .1})
        dataset, _, _ = reprice_dataset(self.parse(fixture()))
        self.assertAlmostEqual(sum(h.cost for h in dataset.hours), dataset.days[0].cost)

    def test_local_midnight_and_dst_are_bucketed_by_wall_clock(self):
        payload = fixture()
        records = payload['analyzer_stats'][0]['messages']
        records[0]['date'] = '2026-09-21T23:30:00Z'
        records[1]['date'] = '2026-09-22T00:30:00Z'
        records[2]['date'] = '2026-09-22T14:30:00Z'
        with local_zone('Australia/Sydney'):
            dataset = parse_collector_stream(io.BytesIO(json.dumps(payload).encode()))
        self.assertEqual([(h.day, h.hour) for h in dataset.hours],
                         [(date(2026, 9, 22), 9), (date(2026, 9, 22), 10), (date(2026, 9, 23), 0)])
        records[:] = [dict(records[0], date='2026-11-01T05:30:00Z'),
                      dict(records[1], date='2026-11-01T06:30:00Z')]
        with local_zone('America/New_York'):
            dataset = parse_collector_stream(io.BytesIO(json.dumps(payload).encode()))
        self.assertEqual(len(dataset.hours), 1)
        self.assertEqual(dataset.hours[0].hour, 1)
        self.assertEqual(dataset.hours[0].tokens.total, 320)

    def test_portable_requests_use_same_local_day_and_hour(self):
        event = make_event('request', 'session', '2026-09-22T16:10:00Z', 'gpt-5.4',
                           {'input_tokens': 100, 'output_tokens': 40, 'cached_input_tokens': 20,
                            'reasoning_output_tokens': 10})
        with local_zone('Australia/Sydney'):
            dataset = usage_dataset([event])
        self.assertEqual(dataset.days[0].day, date(2026, 9, 23))
        self.assertEqual(dataset.hours[0].day, dataset.days[0].day)
        self.assertEqual(dataset.hours[0].hour, 2)
        self.assertEqual(dataset.hours[0].tokens, dataset.days[0].tokens)
        self.assertEqual(dataset.hours[0].cost, dataset.days[0].cost)

    def test_sync_preserves_hours_and_excludes_all_record_metadata(self):
        dataset = self.parse(fixture())
        payload = encode_dataset(dataset, 'b'*32, 'all')
        self.assertNotIn('PRIVATE', json.dumps(payload))
        self.assertEqual(decode_dataset(payload).hours, dataset.hours)
        combined = add_devices(dataset, {'b'*32: payload}, 'a'*32)
        self.assertEqual(sum(h.tokens.total for h in combined.hours), 960)
        self.assertEqual(len(combined.days), 2)
        legacy = copy.deepcopy(payload)
        del legacy['hours']
        self.assertEqual(decode_dataset(legacy).hours, ())
        self.assertEqual(add_devices(dataset, {'b'*32: legacy}, 'a'*32).hours, dataset.hours)

    def test_sync_rejects_bad_hours_duplicates_and_unknown_days(self):
        payload = encode_dataset(self.parse(fixture()), 'a'*32, 'all')
        for field, bad in [('hour', 24), ('hour', -1), ('hour', True), ('cost', float('nan')),
                           ('tool', '/private/path'), ('day', '2026-09-23')]:
            candidate = copy.deepcopy(payload)
            candidate['hours'][0][field] = bad
            with self.assertRaises(ValueError):
                decode_dataset(candidate)
        payload['hours'].append(payload['hours'][0])
        with self.assertRaises(ValueError):
            decode_dataset(payload)

    def test_stream_rejects_missing_schema_and_bounds_input(self):
        for payload in ({}, [], {'analyzer_stats': {}}, {'analyzer_stats': [5]}):
            with self.assertRaises(StatsDataError):
                self.parse(payload)
        self.assertEqual(self.parse({'analyzer_stats': []}).days, ())
        with patch('splitrail_desktop.activity.MAX_STREAM_BYTES', 8):
            with self.assertRaises(ValueError):
                _BoundedStream(io.BytesIO(b'0123456789')).read(10)

    def test_collector_command_is_local_and_never_uses_shell_or_files(self):
        class Process:
            stdout = io.BytesIO(json.dumps(fixture()).encode())
            stderr = io.BytesIO(b'warning')
            def wait(self, timeout=None): return 0
            def poll(self): return 0
        with patch('splitrail_desktop.activity.subprocess.Popen', return_value=Process()) as start, local_zone('UTC'):
            dataset, code, stderr = read_collector('/example/splitrail', 2)
        self.assertEqual(start.call_args.args[0], ('/example/splitrail', 'stats', '--include-messages'))
        self.assertFalse(start.call_args.kwargs['shell'])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, 'warning')
        self.assertEqual(len(dataset.hours), 2)
