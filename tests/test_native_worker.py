from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop import native_worker as native, preferences, pricing, sync
from splitrail_desktop.domain import DailyUsage, HourlyUsage, ModelDetail, TokenUsage, UsageDataset
from splitrail_desktop.runner import CostDiagnostics, LocalCommandError, MissingCommandError, StatsCommandResult

FIXTURES = Path(__file__).parent / 'fixtures'
SOURCE = Path(__file__).resolve().parents[1] / 'src'
MARKER = 'PRIVATE_SYNTHETIC_ACCOUNT_PATH_TOKEN'


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def usage_fixture():
    tokens = TokenUsage(input=100, output=40, reasoning=10, cached=20,
                        cache_read=15, cache_write=5, reasoning_in_output=10)
    model = ModelDetail('openai-codex/gpt-6.1-sol', 4, tokens, 1.25, 0)
    row = DailyUsage(MARKER, date(2026, 9, 1), 2, 10, 4, tokens, 2.0, 0,
                     {model.name: 4}, (model,))
    extra = TokenUsage(input=7, output=4, reasoning=3, cached=5, cache_read=3, cache_write=2)
    second = replace(row, analyzer='Imported Codex CLI', conversations=3, tokens=extra, cost=.5,
                     model_details=(replace(model, tokens=extra, cost=.25),))
    third = replace(row, day=date(2026, 9, 2), conversations=1,
                    model_details=(replace(model, name='gpt-6.1-sol'),))
    dataset = UsageDataset((third, second, row), {MARKER: 99999}, True,
                           (HourlyUsage(MARKER, row.day, 12, tokens, 99),))
    return StatsCommandResult(dataset, CostDiagnostics((MARKER,), 1, 2, (MARKER,)), 0)


class IsolatedWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.environment = self.enterContext(patch.dict(os.environ, {'SPLITRAIL_DATA_DIR': str(self.data)}))
        self.enterContext(patch.object(native, '_collector_available', return_value=False))
        self.enterContext(patch.object(native, '_isolate_process'))
        self.prepare = self.enterContext(patch.object(native, 'prepare_desktop_path'))
        self.api = self.enterContext(patch.object(sync, '_api', side_effect=AssertionError('Unexpected network access')))
        self.enterContext(patch('splitrail_desktop.catalog_updates.refresh_prices', return_value=False))

    def request(self, task, options=None):
        request, response = self.root / 'request.json', self.root / 'response.json'
        request.write_text(json.dumps({'task': task, 'options': options or {}}), encoding='utf-8')
        self.assertEqual(native.main(request, response), 0)
        return json.loads(response.read_text(encoding='utf-8'))

    def configure(self, **options):
        with patch.object(sync, '_private_repo', return_value={'private': True}):
            return sync.configure('example/private-usage', **options)

    def test_projection_merges_days_models_and_exact_token_total_semantics(self):
        source = usage_fixture()
        result = native.project_usage(source)
        self.assertEqual([row['day'] for row in result['days']], ['2026-09-01', '2026-09-02'])
        first, second = result['days']
        self.assertEqual(first['tokens'], 179)  # 160 + 19; neither reasoning nor caches counted twice.
        self.assertEqual(first['conversations'], 5)
        self.assertEqual(first['cost'], 2.5)  # Preserve costs outside model detail coverage.
        self.assertEqual(first['models'], [{'name': 'openai-codex/gpt-6.1-sol', 'tokens': 179, 'cost': 1.5}])
        self.assertEqual(second['tokens'], 160)
        self.assertEqual(second['models'][0]['name'], 'gpt-6.1-sol')
        self.assertEqual(set(first), {'day', 'tokens', 'conversations', 'cost', 'models'})
        self.assertEqual(set(result), {'days', 'timestamp', 'diagnostics', 'sync', 'preferences'})
        self.assertIsInstance(result['timestamp'], float)
        self.assertNotIn(MARKER, json.dumps(result))
        self.assertEqual(len(result['diagnostics']), 3)
        self.assertEqual(len(source.dataset.hours), 1)  # Source object remains unchanged.

    def test_empty_usage_is_empty_without_invented_days(self):
        source = StatsCommandResult(UsageDataset((), {}), CostDiagnostics((), 0, 0, ()), 0)
        self.assertEqual(native.project_usage(source)['days'], [])

    def test_self_check_rejects_a_loaded_qt_module(self):
        with patch.dict(sys.modules, {'PySide6.synthetic_native_test': object()}):
            response = self.request('self-check')
        self.assertEqual(response, {'error': 'The native worker loaded a Qt module.'})

    def test_full_model_names_are_not_coalesced_by_alias(self):
        source = usage_fixture()
        row = source.dataset.days[0]
        source = replace(source, dataset=replace(source.dataset, days=(replace(row, day=date(2026, 9, 1)),
                                                                     source.dataset.days[2])))
        result = native.project_usage(source)
        self.assertEqual([model['name'] for model in result['days'][0]['models']],
                         ['gpt-6.1-sol', 'openai-codex/gpt-6.1-sol'])

    def test_usage_selects_codex_without_collector_and_requests_no_hours(self):
        preferences.save(usage_mode='local', automatic_prices=False)
        with patch.object(native.worker, 'execute', return_value=usage_fixture()) as execute:
            result = self.request('usage')['result']
        execute.assert_called_once_with('usage', {'mode': 'codex', 'include_hours': False, 'automatic_prices': False})
        self.assertEqual(result['preferences']['usage_mode'], 'codex')
        self.prepare.assert_called_once_with()

    def test_collector_modes_match_desktop_preferences_and_defaults(self):
        with patch.object(native, '_collector_available', return_value=True), \
             patch.object(native.worker, 'execute', return_value=usage_fixture()) as execute:
            for mode, expected in [(None, 'combined'), ('local', 'local'), ('codex', 'codex'),
                                   ('combined', 'combined'), ('invalid', 'combined')]:
                with self.subTest(mode=mode):
                    preferences.save(usage_mode=mode)
                    native.execute('usage')
                    self.assertEqual(execute.call_args.args[1]['mode'], expected)
                    self.assertIs(execute.call_args.args[1]['include_hours'], False)

    def test_preferences_are_allowlisted_validated_and_persisted_atomically(self):
        preferences.save(onboarded=True, account=MARKER)
        response = self.request('preferences', {'theme': 'Nord', 'automatic_prices': False,
                                               'quota_limits': {'current': 88, 'after_reset': 7}})
        self.assertEqual(response['result']['quota_limits'], {'current': 88, 'after_reset': 7})
        self.assertEqual(response['result']['theme'], 'Nord')
        self.assertNotIn(MARKER, json.dumps(response))
        saved = (self.data / 'preferences.json').read_bytes()
        for options in ({'theme': 'bad'}, {'theme': ['Nord']}, {'automatic_prices': 1},
                        {'quota_limits': {'current': True}}, {'quota_limits': {'current': -1}},
                        {'quota_limits': {'after_reset': 101}}, {'quota_limits': {'unknown': 3}},
                        {'quota_limits': []}, {'usage_mode': 'invalid'}, {'account': MARKER}):
            with self.subTest(options=options):
                self.assertIn('error', self.request('preferences', options))
                self.assertEqual((self.data / 'preferences.json').read_bytes(), saved)

    def test_explicit_usage_source_requires_collector_for_other_tools(self):
        self.assertEqual(self.request('preferences', {'usage_mode': 'codex'})['result']['usage_mode'], 'codex')
        self.assertIn('error', self.request('preferences', {'usage_mode': 'combined'}))
        self.assertEqual(preferences.load()['usage_mode'], 'codex')
        with patch.object(native, '_collector_available', return_value=True):
            self.assertEqual(self.request('preferences', {'usage_mode': 'combined'})['result']['usage_mode'], 'combined')

    def test_invalid_saved_preferences_use_safe_defaults(self):
        preferences.save(theme=MARKER, quota_limits={'current': 'bad'}, automatic_prices=False, account=MARKER)
        result = native.preference_summary()
        self.assertEqual(result['theme'], 'Pearl')
        self.assertEqual(result['quota_limits'], {'current': 95, 'after_reset': 5})
        self.assertIs(result['automatic_prices'], False)
        self.assertNotIn(MARKER, json.dumps(result))

    def test_settings_only_expose_safe_fields_and_epoch_timestamps(self):
        config = self.configure(automatic=True, scope='codex', receive_only=True, interval_hours=2, idle_hours=6)
        stamp = datetime.now(timezone.utc).isoformat()
        (self.data / sync.STATE_FILE).write_text(json.dumps({'synced_at': stamp, 'device_count': 3,
                                                            'fingerprint': MARKER, 'credential': MARKER}))
        result = self.request('settings')['result']
        self.assertEqual(set(result), {'connected', 'repository', 'automatic', 'receiveOnly', 'scope',
                                      'intervalHours', 'idleHours', 'devices', 'lastSync', 'nextSyncSeconds'})
        self.assertEqual(result['repository'], 'example/private-usage')
        self.assertEqual(result['devices'], 3)
        self.assertTrue(result['receiveOnly'])
        self.assertEqual(result['lastSync'], datetime.fromisoformat(stamp).timestamp())
        self.assertTrue(7198 <= result['nextSyncSeconds'] <= 7200)
        self.assertNotIn(config['device'], json.dumps(result))
        self.assertNotIn(MARKER, json.dumps(result))
        self.api.assert_not_called()

    def test_invalid_sync_state_does_not_break_usage(self):
        self.configure()
        (self.data / sync.STATE_FILE).write_text('{broken')
        with patch.object(native.worker, 'execute', return_value=usage_fixture()):
            result = native.execute('usage')
        self.assertTrue(result['sync']['connected'])
        self.assertIsNone(result['sync']['lastSync'])
        (self.data / sync.SETTINGS_FILE).write_text('{broken')
        self.assertFalse(native.settings_summary()['connected'])

    def test_configure_maps_camel_case_and_keeps_existing_private_validation(self):
        with patch.object(sync, '_private_repo', return_value={'private': True}) as check:
            result = self.request('configure', {'repository': 'example/private-usage', 'automatic': True,
                                               'receiveOnly': True, 'scope': 'codex',
                                               'intervalHours': 2, 'idleHours': 6})['result']
        check.assert_called_once_with('example/private-usage', writable=False)
        self.assertEqual(result['intervalHours'], 2)
        self.assertEqual(result['idleHours'], 6)
        self.assertTrue(result['receiveOnly'])
        with patch.object(sync, '_private_repo') as check:
            for options in ({'automatic': 'true'}, {'receiveOnly': 1}, {'intervalHours': True},
                            {'intervalHours': 6, 'idleHours': 2}, {'scope': 'unsafe'},
                            {'repository': 'https://example.invalid/private'}):
                self.assertIn('error', self.request('configure', {'repository': 'example/private-usage', **options}))
            check.assert_not_called()

    def test_public_repository_is_rejected_without_usage_or_configuration(self):
        self.api.side_effect = None
        self.api.return_value = b'{"private":false}'
        result = self.request('configure', {'repository': 'example/public'})
        self.assertIn('private repository', result['error'])
        self.assertFalse((self.data / sync.SETTINGS_FILE).exists())
        self.configure(scope='codex')
        with patch.object(native.worker, 'execute') as execute, patch.object(sync, '_collect') as collect:
            result = self.request('sync')
        self.assertIn('private repository', result['error'])
        collect.assert_not_called()
        execute.assert_not_called()
        self.assertFalse(any(call.kwargs.get('method') == 'PUT' for call in self.api.call_args_list))

    def test_sync_finishes_before_usage_and_returns_safe_sync_metadata(self):
        order = []
        def synchronize(**options):
            order.append(('sync', options))
            self.configure(scope='codex')
            (self.data / sync.STATE_FILE).write_text(json.dumps({'synced_at': '2026-09-03T10:00:00Z',
                                                                'device_count': 4}))
            return {'status': MARKER, 'devices': 4, 'account': MARKER}
        def collect(task, options):
            order.append((task, options))
            return usage_fixture()
        with patch.object(sync, 'sync_now', side_effect=synchronize), \
             patch.object(native.worker, 'execute', side_effect=collect):
            result = self.request('sync', {'automatic': True})['result']
        self.assertEqual([item[0] for item in order], ['sync', 'usage'])
        self.assertEqual(order[0][1], {'automatic': True})
        self.assertEqual(result['sync']['devices'], 4)
        self.assertNotIn(MARKER, json.dumps(result))

    def test_disconnect_uses_existing_implementation_and_retains_preferences(self):
        self.configure()
        preferences.save(theme='Nord')
        (self.data / sync.CACHE_FILE).write_text('{}')
        result = self.request('disconnect')['result']
        self.assertFalse(result['connected'])
        self.assertIsNone(result['lastSync'])
        self.assertFalse((self.data / sync.CACHE_FILE).exists())
        self.assertTrue((self.data / 'sync-device.json').exists())
        self.assertEqual(preferences.load()['theme'], 'Nord')

    def test_prices_use_catalogue_resolution_and_override_restore(self):
        preferences.save(automatic_prices=False)
        model = 'openai/gpt-6.1-sol'
        rates = {'input': '9.5', 'output': '19', 'cache_read': '', 'cache_write': 0}
        saved = self.request('price', {'model': model, 'rates': rates})['result']
        self.assertIs(saved['automatic'], False)
        self.assertEqual(saved['verified'], pricing.CATALOG['verified'])
        actual = next(row for row in saved['models'] if row['name'] == pricing.model_key(model))
        self.assertEqual(actual, {'name': pricing.model_key(model), 'input': 9.5, 'output': 19.,
                                  'cache_read': None, 'cache_write': 0.})
        restored = self.request('price', {'model': model, 'rates': None})['result']
        actual = next(row for row in restored['models'] if row['name'] == pricing.model_key(model))
        self.assertEqual(actual['input'], pricing.CATALOG['models'][pricing.model_key(model)]['input'])
        self.assertEqual(pricing.load_overrides(), {})

    def test_bad_prices_use_existing_validator_and_preserve_previous_file(self):
        rates = {'input': 1, 'output': 2, 'cache_read': None, 'cache_write': None}
        self.request('price', {'model': 'custom-fixture', 'rates': rates})
        saved = pricing.overrides_path().read_bytes()
        for value in (True, -1, 'nan', 'inf', 'PRIVATE', 1000001):
            response = self.request('price', {'model': 'custom-fixture', 'rates': {**rates, 'input': value}})
            self.assertIn('Input must be a finite number', response['error'])
            self.assertEqual(pricing.overrides_path().read_bytes(), saved)
        self.assertIn('error', self.request('price', {'model': 'custom-fixture'}))
        self.assertEqual(pricing.overrides_path().read_bytes(), saved)

    def test_export_wraps_worker_and_import_refreshes_usage_after_success(self):
        target = str(self.root / 'synthetic-usage.json.gz')
        def dispatch(task, options):
            return usage_fixture() if task == 'usage' else '2 requests exported'
        with patch.object(native.worker, 'execute', side_effect=dispatch) as execute:
            self.assertEqual(self.request('export', {'path': target}), {'result': {'message': '2 requests exported'}})
            execute.assert_called_once_with('export', {'path': target})
            execute.reset_mock()
            result = self.request('import', {'path': target})['result']
            self.assertEqual([call.args[0] for call in execute.call_args_list], ['import', 'usage'])
            self.assertEqual(execute.call_args_list[0].args[1], {'path': target})
            self.assertEqual(result['days'][0]['tokens'], 179)
        with patch.object(native.worker, 'execute', side_effect=ValueError(MARKER)) as execute:
            self.assertIn('error', self.request('import', {'path': target}))
            self.assertEqual(execute.call_count, 1)

    def test_errors_are_serialized_without_raw_exception_details_or_output(self):
        for exception in (RuntimeError(MARKER), ValueError('/private/' + MARKER),
                          LocalCommandError('Bearer ' + MARKER), sync.SyncError(MARKER),
                          MissingCommandError(MARKER)):
            with self.subTest(exception=type(exception).__name__), \
                 patch.object(native.worker, 'execute', side_effect=exception), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                response = self.request('usage')
            self.assertEqual(set(response), {'error'})
            self.assertNotIn(MARKER, response['error'])
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')

    def test_serialization_failures_and_malformed_requests_still_return_safe_errors(self):
        request, response = self.root / 'request.json', self.root / 'response.json'
        for raw in ('[1,2]', '{broken', '{"task":[]}', '{"task":"settings","options":null}',
                    '{"task":"preferences","options":{"quota_limits":{"current":NaN}}}',
                    '{"task":"settings","account":"' + MARKER + '"}'):
            request.write_text(raw)
            self.assertEqual(native.main(request, response), 0)
            self.assertEqual(set(json.loads(response.read_text())), {'error'})
            self.assertNotIn(MARKER, response.read_text())
        with patch.object(native, 'execute', return_value={'private': MARKER, 'cost': float('nan')}):
            result = self.request('usage')
            self.assertEqual(set(result), {'error'})
            self.assertNotIn(MARKER, json.dumps(result))
        self.assertEqual(list(self.root.glob('.native-response-*')), [])


class QuotaTests(unittest.TestCase):
    def setUp(self):
        # These tests exercise the existing RPC branch without reading auth.
        self.enterContext(patch.object(native, '_http_quota', return_value=None))

    def test_actual_direct_fixture_uses_quota_and_expiries_without_account_metadata(self):
        result = native.parse_codex_quota(fixture('codex_rate_limits_one.json'), timestamp=1790000000.)
        self.assertEqual(result, {'timestamp': 1790000000., 'status': 'fresh',
                                 'windows': [{'id': 'codex:primary', 'label': '5 hours', 'kind': 'session',
                                              'percent': 12., 'reset': 1788761744.}],
                                 'expiries': [1789949949.]})
        for secret in ('synthetic-account', 'fixture-plan', 'Synthetic reset', 'balance', 'accountId'):
            self.assertNotIn(secret, json.dumps(result))

    def test_named_limits_are_included_and_legacy_duplicates_are_replaced(self):
        payload = fixture('codex_rate_limits_one.json')
        snapshot = copy.deepcopy(payload['result']['rateLimits'])
        snapshot['primary']['usedPercent'] = 21
        snapshot['secondary'] = {'usedPercent': 32, 'windowDurationMins': 10080, 'resetsAt': 1790122749}
        named = copy.deepcopy(snapshot)
        named.update(limitId='model-fixture', limitName='Fixture model')
        payload['result']['rateLimitsByLimitId'] = {'codex': snapshot, 'model-fixture': named}
        windows = native.parse_codex_quota(payload)['windows']
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0]['percent'], 21.)
        self.assertEqual(windows[1]['kind'], 'weekly')
        self.assertEqual(windows[3]['kind'], 'model')
        self.assertEqual(windows[3]['label'], 'Fixture model · Weekly')
        del payload['result']['rateLimits']
        self.assertEqual(native.parse_codex_quota(payload)['windows'], windows)

    def test_missing_reset_is_null_and_zero_usage_is_a_valid_reading(self):
        payload = {'result': {'rateLimits': {'primary': {'usedPercent': 0, 'windowDurationMins': 300}}}}
        result = native.parse_codex_quota(payload)
        self.assertEqual(result['status'], 'fresh')
        self.assertEqual(result['windows'][0]['percent'], 0.)
        self.assertIsNone(result['windows'][0]['reset'])

    def test_absent_malformed_and_rpc_errors_are_honestly_unavailable(self):
        for payload in (None, [], {}, {'result': None}, {'error': MARKER, 'result': {}},
                        fixture('codex_rate_limits_absent.json'), fixture('codex_rate_limits_malformed.json'),
                        {'result': {'rateLimits': []}}, {'result': {'rateLimitsByLimitId': []}}):
            with self.subTest(payload=payload):
                result = native.parse_codex_quota(payload)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['windows'], [])
                self.assertEqual(result['expiries'], [])

    def test_invalid_percentages_and_timestamps_cannot_become_fresh(self):
        for field, value in [('usedPercent', True), ('usedPercent', -1), ('usedPercent', 101),
                             ('usedPercent', '40'), ('usedPercent', float('nan')),
                             ('usedPercent', float('inf')), ('usedPercent', None),
                             ('resetsAt', '2026-09-01'), ('resetsAt', True), ('resetsAt', -1),
                             ('resetsAt', 1790000000000), ('windowDurationMins', True)]:
            with self.subTest(field=field, value=value):
                window = {'usedPercent': 12, 'windowDurationMins': 300, 'resetsAt': 1790000000}
                window[field] = value
                result = native.parse_codex_quota({'result': {'rateLimits': {'primary': window}}})
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['windows'], [])

    def test_reset_count_only_zero_and_partial_details_never_invent_expiries(self):
        for name in ('codex_rate_limits_zero.json', 'codex_rate_limits_count_only.json'):
            result = native.parse_codex_quota(fixture(name))
            self.assertEqual(result['status'], 'fresh')
            self.assertEqual(result['expiries'], [])
            self.assertNotIn('count', result)
        payload = fixture('codex_rate_limits_multiple.json')
        result = native.parse_codex_quota(payload)
        self.assertEqual(result['expiries'], [1789949949., 1790036349., 1790122749.])
        credits = payload['result']['rateLimitResetCredits']['credits']
        credits[0]['expiresAt'] = None
        del credits[1]['expiresAt']
        result = native.parse_codex_quota(payload)
        self.assertEqual(result['expiries'], [1790122749.])

    def test_bad_reset_credit_details_do_not_erase_valid_quota_windows(self):
        payload = fixture('codex_rate_limits_one.json')
        payload['result']['rateLimitResetCredits'] = fixture('codex_rate_limits_malformed.json')['result']['rateLimitResetCredits']
        result = native.parse_codex_quota(payload)
        self.assertEqual(result['status'], 'fresh')
        self.assertTrue(result['windows'])
        self.assertEqual(result['expiries'], [])

    def test_quota_calls_read_only_helper_once_with_twenty_second_timeout(self):
        with patch.object(native.runner, '_find_executable', return_value='/synthetic/codex'), \
             patch.object(native.runner, '_run_codex_reset_credit_read',
                          return_value=fixture('codex_rate_limits_one.json')) as read, \
             patch.object(native, '_legacy_quota') as legacy, \
             patch.object(native.runner, 'run_quota_axi') as old_flow:
            result = native.execute('quota')
        read.assert_called_once_with('/synthetic/codex', 20)
        legacy.assert_not_called()
        old_flow.assert_not_called()
        self.assertEqual(result['status'], 'fresh')

    def test_failures_explicit_null_and_empty_quota_do_not_launch_node_fallback(self):
        with patch.object(native.runner, '_find_executable', return_value='/synthetic/codex'), \
             patch.object(native, '_legacy_quota') as legacy:
            for failure in (MissingCommandError(MARKER), LocalCommandError(MARKER), TimeoutError(MARKER)):
                with patch.object(native.runner, '_run_codex_reset_credit_read', side_effect=failure):
                    self.assertEqual(native.quota()['status'], 'unavailable')
            with patch.object(native.runner, '_run_codex_reset_credit_read',
                              return_value=fixture('codex_rate_limits_absent.json')):
                self.assertEqual(native.quota()['status'], 'unavailable')
            legacy.assert_not_called()

    def test_only_old_protocol_falls_back_without_a_second_codex_call(self):
        payload = fixture('quota_fresh.json')
        stamp = datetime.fromisoformat(payload['providers'][0]['state']['refreshedAt'].replace('Z', '+00:00')).timestamp()
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload), MARKER)
        with patch.object(native.runner, '_find_executable', return_value='/synthetic/helper'), \
             patch.object(native.runner, '_run_codex_reset_credit_read', return_value={'result': {}}) as codex, \
             patch.object(native.runner, '_run', return_value=completed) as legacy, \
             patch.object(native.time, 'time', return_value=stamp), \
             patch.object(native.time, 'monotonic', side_effect=[10., 13.]):
            result = native.quota()
        codex.assert_called_once_with('/synthetic/helper', 20)
        legacy.assert_called_once_with(('/synthetic/helper', '--provider', 'codex', '--full', '--json'), 17., 'quota-axi')
        self.assertEqual(result['status'], 'fresh')
        self.assertEqual(len(result['windows']), 2)
        self.assertEqual(result['expiries'], [])
        self.assertNotIn(MARKER, json.dumps(result))
        self.assertNotIn('fixture@example.invalid', json.dumps(result))

    def test_legacy_timestamp_does_not_renew_a_cached_reading(self):
        payload = fixture('quota_fresh.json')
        stamp = datetime.fromisoformat(payload['providers'][0]['state']['refreshedAt'].replace('Z', '+00:00')).timestamp()
        with patch.object(native.runner, '_find_executable', return_value='/synthetic/helper'), \
             patch.object(native.runner, '_run_codex_reset_credit_read', return_value={'result': {}}), \
             patch.object(native.runner, '_run', return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), '')), \
             patch.object(native.time, 'time', return_value=stamp + 90):
            result = native.quota()
        self.assertEqual(result['status'], 'fresh')
        self.assertEqual(result['timestamp'], stamp)

    def test_legacy_stale_auth_empty_and_old_cached_data_are_unavailable(self):
        for name in ('quota_stale.json', 'quota_auth_required.json', 'quota_absent.json', 'quota_fresh.json'):
            with self.subTest(name=name), \
                 patch.object(native.runner, '_find_executable', return_value='/synthetic/helper'), \
                 patch.object(native.runner, '_run_codex_reset_credit_read', return_value={'result': {}}), \
                 patch.object(native.runner, '_run', return_value=subprocess.CompletedProcess([], 0, json.dumps(fixture(name)), '')), \
                 patch.object(native.time, 'time', return_value=1900000000.):
                self.assertEqual(native.quota()['status'], 'unavailable')

    def test_exhausted_deadline_prevents_fallback(self):
        with patch.object(native.runner, '_find_executable', return_value='/synthetic/helper'), \
             patch.object(native.runner, '_run_codex_reset_credit_read', return_value={'result': {}}), \
             patch.object(native.time, 'monotonic', side_effect=[10., 31.]), \
             patch.object(native, '_legacy_quota') as legacy:
            self.assertEqual(native.quota()['status'], 'unavailable')
        legacy.assert_not_called()


class WorkerProcessTests(unittest.TestCase):
    def test_real_ipc_usage_settings_prices_and_self_check_cannot_import_qt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = '''
import importlib.abc, json, sys
from pathlib import Path
from unittest.mock import patch
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PySide6' or fullname.startswith('PySide6.'):
            raise AssertionError('Qt imported into the native worker')
sys.meta_path.insert(0, NoQt())
from splitrail_desktop import native_worker as native, preferences, quota_http
root = Path(sys.argv[1])
preferences.save(automatic_prices=False)
with patch.object(native, '_collector_available', return_value=False), \
     patch('splitrail_desktop.portable.scan_codex', return_value={'events': [], 'diagnostics': {}}), \
     patch('splitrail_desktop.sync._api', side_effect=AssertionError('Unexpected GitHub access')):
    for task in ('self-check', 'settings', 'usage', 'prices'):
        request = root / 'request.json'
        request.write_text(json.dumps({'task': task, 'options': {}}))
        assert native.main(request, root / (task + '.json')) == 0
        assert 'result' in json.loads((root / (task + '.json')).read_text())
assert not any(name == 'PySide6' or name.startswith('PySide6.') for name in sys.modules)
'''
            environment = {**os.environ, 'PYTHONPATH': str(SOURCE), 'SPLITRAIL_DATA_DIR': str(root / 'data')}
            completed = subprocess.run([sys.executable, '-c', script, str(root)], env=environment,
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, '')
            check = json.loads((root / 'self-check.json').read_text())['result']
            self.assertTrue(check['headless'])
            self.assertGreaterEqual(check['pricingCount'], 50)
            self.assertEqual(json.loads((root / 'usage.json').read_text())['result']['days'], [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX process group cancellation')
    def test_sigterm_unwinds_codex_cleanup_and_worker_owns_its_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex = root / 'codex'
            ready = root / 'ready'
            codex.write_text('#!' + sys.executable + '\n'
                             'import json,os,sys\n'
                             'from pathlib import Path\n'
                             'first=json.loads(sys.stdin.readline())\n'
                             'print(json.dumps({"id":first["id"],"result":{}}),flush=True)\n'
                             'second=json.loads(sys.stdin.readline())\n'
                             f'Path({str(ready)!r}).write_text(str(os.getpid()))\n'
                             'for line in sys.stdin: pass\n')
            codex.chmod(0o755)
            request = root / 'request.json'
            request.write_text(json.dumps({'task': 'quota', 'options': {}}))
            environment = {**os.environ, 'PYTHONPATH': str(SOURCE), 'CODEX_BIN': str(codex),
                           'CODEX_HOME': str(root / 'no-auth'),
                           'SPLITRAIL_DATA_DIR': str(root / 'data')}
            process = subprocess.Popen([sys.executable, '-m', 'splitrail_desktop.native_worker',
                                        str(request), str(root / 'response.json')], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 8
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                child = int(ready.read_text())
                self.assertEqual(os.getpgid(process.pid), process.pid)
                self.assertEqual(os.getpgid(child), process.pid)
                process.terminate()
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 128 + signal.SIGTERM)
                self.assertEqual(stdout + stderr, b'')
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=8)


if __name__ == '__main__':
    unittest.main()
