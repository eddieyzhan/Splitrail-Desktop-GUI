from __future__ import annotations

import base64
import copy
import io
import json
import os
from pathlib import Path
import signal
import ssl
import tempfile
import unittest
from unittest.mock import patch

from splitrail_desktop import native_worker as native, quota_http as http

FIXTURES = Path(__file__).parent / 'fixtures'
PRIVATE = 'SYNTHETIC_PRIVATE_TOKEN_ACCOUNT'
NOW = 1790000000.


def usage_payload():
    return {'rate_limit': {
                'primary_window': {'used_percent': 12, 'limit_window_seconds': 18000,
                                   'reset_at': 1788761744, 'reset_after_seconds': 100},
                'secondary_window': {'used_percent': 34, 'limit_window_seconds': 604800,
                                     'reset_at': 1790122749}},
            'additional_rate_limits': None, 'code_review_rate_limit': None,
            'spend_control': {'individual_limit': None, 'reached': False},
            'rate_limit_reset_credits': {'available_count': 1, 'applicable_available_count': 1},
            'account_id': PRIVATE, 'email': PRIVATE, 'user_id': PRIVATE,
            'credits': {'balance': PRIVATE}, 'plan_type': PRIVATE}


def rpc_payload(name='codex_rate_limits_one.json'):
    return json.loads((FIXTURES / name).read_text())


class Response:
    def __init__(self, payload=None, *, raw=None, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.stream = io.BytesIO(raw if raw is not None else json.dumps(payload or usage_payload()).encode())
        self.reads = []

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, size):
        self.reads.append(size)
        return self.stream.read(size)


class TransportTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.auth = self.root / 'auth.json'
        self.enterContext(patch.dict(os.environ, {'CODEX_HOME': str(self.root)}))
        self.write_auth()
        self.connection_type = self.enterContext(patch.object(http.http.client, 'HTTPSConnection'))
        self.connection = self.connection_type.return_value
        self.connection.getresponse.return_value = Response()

    def write_auth(self, **changes):
        value = {'auth_mode': 'chatgpt', 'tokens': {'access_token': PRIVATE, 'account_id': 'synthetic-account'},
                 'OPENAI_API_KEY': 'UNRELATED_API_KEY', **changes}
        self.auth.write_text(json.dumps(value))

    def test_fixed_get_strict_tls_minimal_headers_no_auth_mutation_or_metadata(self):
        before = self.auth.read_bytes()
        reading = http.read_usage()
        self.assertIsNotNone(reading)
        self.connection_type.assert_called_once()
        args, kwargs = self.connection_type.call_args
        self.assertEqual(args, ('chatgpt.com',))
        self.assertEqual(kwargs['timeout'], 10)
        self.assertIs(kwargs['context'].check_hostname, True)
        self.assertEqual(kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
        self.assertIsNone(kwargs['context'].keylog_filename)
        self.connection.request.assert_called_once_with('GET', '/backend-api/wham/usage',
            headers={'Authorization': 'Bearer ' + PRIVATE, 'Accept': 'application/json',
                     'ChatGPT-Account-Id': 'synthetic-account'})
        self.connection.close.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(reading.response))
        self.assertNotIn(PRIVATE, repr(reading))
        self.assertNotIn(PRIVATE, repr(http._credentials()))
        self.assertEqual(self.auth.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), {self.auth})
        self.assertEqual(reading.credit_signature, (1, 1))

    def test_explicit_api_key_and_unknown_modes_ignore_stale_oauth_tokens(self):
        for mode in ('api_key', 'apikey', 'unknown', None, False):
            with self.subTest(mode=mode):
                self.write_auth(auth_mode=mode)
                self.assertIsNone(http.read_usage())
        self.connection_type.assert_not_called()

    def test_legacy_auth_without_mode_and_camel_case_tokens_are_supported(self):
        self.auth.write_text(json.dumps({'tokens': {'accessToken': PRIVATE, 'accountId': 'synthetic-account'}}))
        self.assertIsNotNone(http.read_usage())

    def test_only_current_auth_file_is_considered(self):
        self.auth.unlink()
        (self.root / 'unrelated-auth.json').write_text(json.dumps({'tokens': {'access_token': PRIVATE}}))
        with patch.object(http.Path, 'home', side_effect=AssertionError('Unrelated home lookup')):
            self.assertIsNone(http.read_usage())
        self.connection_type.assert_not_called()

    def test_related_jwt_claim_is_used_only_for_account_header(self):
        claims = {'account_id': 'synthetic-account', 'email': PRIVATE}
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        self.write_auth(tokens={'access_token': PRIVATE, 'id_token': 'header.' + body + '.signature'})
        reading = http.read_usage()
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['ChatGPT-Account-Id'], 'synthetic-account')
        self.assertNotIn(PRIVATE, json.dumps(reading.response))

    def test_header_injection_and_oversized_auth_never_contact_endpoint(self):
        for tokens in ({'access_token': PRIVATE + '\r\nBad: header'},
                       {'access_token': PRIVATE, 'account_id': 'wrong\nheader'}):
            self.write_auth(tokens=tokens)
            self.assertIsNone(http.read_usage())
        self.auth.write_bytes(b' ' * (http.MAX_AUTH_BYTES + 1))
        self.assertIsNone(http.read_usage())
        self.connection_type.assert_not_called()

    def test_redirect_unauthorized_and_server_errors_never_follow_or_read_body(self):
        for status in (301, 302, 307, 308, 401, 403, 404, 429, 500):
            with self.subTest(status=status):
                response = Response(status=status, headers={'Location': 'https://untrusted.invalid/' + PRIVATE})
                self.connection.getresponse.return_value = response
                self.connection.request.reset_mock()
                self.assertIsNone(http.read_usage())
                self.connection.request.assert_called_once()
                self.assertEqual(response.reads, [])

    def test_two_megabyte_response_bound_and_compression_rejection(self):
        for response in (Response(headers={'Content-Length': str(http.MAX_RESPONSE_BYTES + 1)}),
                         Response(headers={'Content-Length': 'invalid'}),
                         Response(headers={'Content-Encoding': 'gzip'})):
            self.connection.getresponse.return_value = response
            self.assertIsNone(http.read_usage())
            self.assertEqual(response.reads, [])
        response = Response(raw=b' ' * (http.MAX_RESPONSE_BYTES + 1))
        self.connection.getresponse.return_value = response
        self.assertIsNone(http.read_usage())
        self.assertEqual(response.reads, [http.MAX_RESPONSE_BYTES + 1])

    def test_tls_timeout_malformed_json_errors_are_quiet_and_close_connection(self):
        for error in (ssl.SSLCertVerificationError(PRIVATE), TimeoutError(PRIVATE), OSError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.connection.request.side_effect = error
                self.assertIsNone(http.read_usage())
        self.connection.request.side_effect = None
        self.connection.getresponse.return_value = Response(raw=b'not-json-' + PRIVATE.encode())
        self.assertIsNone(http.read_usage())
        self.assertEqual(self.connection.close.call_count, 4)

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'POSIX total HTTP deadline')
    def test_whole_request_deadline_is_restored_even_on_failure(self):
        previous = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(TimeoutError):
            with http._http_deadline():
                remaining, _ = signal.getitimer(signal.ITIMER_REAL)
                self.assertTrue(0 < remaining <= 10)
                http._expired(signal.SIGALRM, None)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0., 0.))

    def test_auth_revision_change_during_request_discards_reading(self):
        def request(*args, **kwargs):
            self.write_auth(tokens={'access_token': 'NEW_SYNTHETIC_TOKEN', 'account_id': 'other-account'})
        self.connection.request.side_effect = request
        self.assertIsNone(http.read_usage())


class NormalizeTests(unittest.TestCase):
    def test_http_and_rpc_have_identical_base_window_ids_values_and_reset_signature(self):
        payload = usage_payload()
        normalized, counts = http.normalize(payload)
        response = rpc_payload()
        response['result']['rateLimits']['secondary'] = {
            'usedPercent': 34, 'windowDurationMins': 10080, 'resetsAt': 1790122749}
        self.assertEqual(native.parse_codex_quota(normalized)['windows'], native.parse_codex_quota(response)['windows'])
        self.assertEqual(counts, (1, 1))
        self.assertEqual(native.parse_codex_quota(normalized)['expiries'], [])

    def test_named_model_limits_keep_exact_ids_names_percent_and_reset(self):
        payload = usage_payload()
        payload['additional_rate_limits'] = [{'metered_feature': 'fixture-model', 'limit_name': 'Fixture model',
                                              'rate_limit': copy.deepcopy(payload['rate_limit'])}]
        normalized, _ = http.normalize(payload)
        windows = native.parse_codex_quota(normalized)['windows']
        self.assertEqual(windows[-1], {'id': 'fixture-model:secondary', 'label': 'Fixture model · Weekly',
                                      'kind': 'model', 'percent': 34., 'reset': 1790122749.})

    def test_count_only_credit_data_does_not_become_expiry_dates_or_available_count(self):
        payload = usage_payload()
        payload['rate_limit_reset_credits'] = {'available_count': 9, 'applicable_available_count': 2}
        normalized, counts = http.normalize(payload)
        self.assertEqual(counts, (9, 2))
        self.assertNotIn('rateLimitResetCredits', normalized['result'])
        self.assertEqual(native.parse_codex_quota(normalized)['expiries'], [])

    def test_unknown_features_and_malformed_numbers_force_rpc_instead_of_dropping_data(self):
        changes = ({'code_review_rate_limit': {'primary_window': {}}},
                   {'spend_control': {'individual_limit': {}, 'reached': False}},
                   {'spend_control': {'individual_limit': None, 'reached': True}},
                   {'additional_rate_limits': 'bad'}, {'rate_limit_reset_credits': {'credits': []}})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                http.normalize({**usage_payload(), **change})
        for key, value in (('used_percent', True), ('used_percent', float('nan')), ('used_percent', 101),
                           ('limit_window_seconds', 59), ('reset_at', 'bad')):
            payload = usage_payload()
            payload['rate_limit']['primary_window'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                http.normalize(payload)


class BankCacheTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch.dict(os.environ, {'SPLITRAIL_DATA_DIR': str(self.root)}))
        self.now = NOW
        self.clock = self.enterContext(patch.object(native.time, 'time', side_effect=lambda: self.now))
        self.payload = usage_payload()
        self.revision = 'a' * 64
        self.account = 'synthetic-account'
        self.http = self.enterContext(patch.object(http, 'read_usage', side_effect=self.reading))
        self.same = self.enterContext(patch.object(http, 'same_revision', return_value=True))
        self.find = self.enterContext(patch.object(native.runner, '_find_executable', return_value='/synthetic/codex'))
        self.rpc = self.enterContext(patch.object(native.runner, '_run_codex_reset_credit_read', return_value=rpc_payload()))
        self.legacy = self.enterContext(patch.object(native, '_legacy_quota', side_effect=AssertionError('Unexpected Node helper')))

    def reading(self):
        response, counts = http.normalize(self.payload)
        return http.Reading(response, self.now, self.revision, counts, self.root / 'auth.json', self.account)

    def test_initial_rpc_then_warm_http_preserves_expiries_without_any_helper(self):
        first = native.quota()
        self.rpc.assert_called_once_with('/synthetic/codex', 20)
        self.assertEqual(first['expiryStatus'], 'fresh')
        self.assertEqual(first['expiryTimestamp'], NOW)
        self.assertTrue(first['expiryDetailsComplete'])
        self.rpc.reset_mock()
        self.find.reset_mock()
        self.now += 45
        second = native.quota()
        self.assertEqual(second['expiries'], first['expiries'])
        self.assertEqual(second['timestamp'], self.now)
        self.assertEqual(second['expiryTimestamp'], NOW)
        self.assertEqual(second['expiryStatus'], 'cached')
        self.rpc.assert_not_called()
        self.find.assert_not_called()
        raw = (self.root / native.BANK_CACHE_FILE).read_text()
        for value in (PRIVATE, self.account, str(self.root), 'access_token', 'accountId', 'planType'):
            self.assertNotIn(value, raw)
        if os.name == 'posix':
            self.assertEqual((self.root / native.BANK_CACHE_FILE).stat().st_mode & 0o777, 0o600)

    def test_hourly_refresh_and_weekly_reset_or_count_changes_refresh_once(self):
        native.quota()
        self.now += 3600
        native.quota()
        self.assertEqual(self.rpc.call_count, 2)
        self.now += 1
        self.payload['rate_limit']['secondary_window']['reset_at'] += 604800
        native.quota()
        self.assertEqual(self.rpc.call_count, 3)
        self.payload['rate_limit_reset_credits']['applicable_available_count'] = 0
        native.quota()
        self.assertEqual(self.rpc.call_count, 4)
        native.quota()
        self.assertEqual(self.rpc.call_count, 4)

    def test_failed_rpc_preserves_authoritative_expiries_and_reports_cached_time(self):
        first = native.quota()
        self.now += 3601
        self.rpc.side_effect = native.runner.LocalCommandError(PRIVATE)
        result = native.quota()
        self.assertEqual(result['status'], 'fresh')
        self.assertEqual(result['expiryStatus'], 'cached')
        self.assertEqual(result['expiryTimestamp'], NOW)
        self.assertEqual(result['expiries'], first['expiries'])
        self.assertNotIn(PRIVATE, json.dumps(result))
        self.rpc.reset_mock()
        self.now += 45
        native.quota()
        self.rpc.assert_not_called()

    def test_failed_first_bank_read_is_explicit_and_does_not_launch_every_poll(self):
        self.rpc.side_effect = native.runner.MissingCommandError(PRIVATE)
        first = native.quota()
        self.assertEqual(first['expiryStatus'], 'unavailable')
        self.assertIsNone(first['expiryTimestamp'])
        self.assertEqual(first['expiries'], [])
        self.assertEqual(first['status'], 'fresh')
        self.now += 45
        native.quota()
        self.assertEqual(self.rpc.call_count, 1)

    def test_http_zero_count_cannot_delete_previous_bank_expiries_on_failed_rpc(self):
        first = native.quota()
        self.payload['rate_limit_reset_credits'] = {'available_count': 0, 'applicable_available_count': 0}
        self.rpc.return_value = {'result': {'rateLimitResetCredits': None}}
        result = native.quota()
        self.assertEqual(result['expiries'], first['expiries'])
        self.assertEqual(result['expiryStatus'], 'cached')
        self.rpc.return_value = rpc_payload('codex_rate_limits_zero.json')
        self.rpc.return_value['result']['accountId'] = self.account
        self.now += 3600
        result = native.quota()
        self.assertEqual(result['expiries'], [])
        self.assertEqual(result['expiryStatus'], 'fresh')
        self.assertTrue(result['expiryDetailsComplete'])

    def test_partial_expiry_details_remain_explicit_and_no_dates_are_invented(self):
        self.rpc.return_value = rpc_payload('codex_rate_limits_count_only.json')
        result = native.quota()
        self.assertEqual(result['expiries'], [])
        self.assertEqual(result['expiryStatus'], 'fresh')
        self.assertFalse(result['expiryDetailsComplete'])

    def test_account_switch_and_rpc_account_mismatch_cannot_reuse_previous_expiries(self):
        native.quota()
        self.revision = 'b' * 64
        self.account = 'other-synthetic-account'
        result = native.quota()
        self.assertEqual(result['expiryStatus'], 'unavailable')
        self.assertEqual(result['expiries'], [])
        self.assertIsNone(result['expiryTimestamp'])

    def test_auth_file_changed_during_bank_read_is_not_cached_or_attached(self):
        self.same.return_value = False
        result = native.quota()
        self.assertEqual(result['expiryStatus'], 'unavailable')
        self.assertEqual(result['expiries'], [])
        self.assertFalse((self.root / native.BANK_CACHE_FILE).exists())

    def test_corrupt_or_future_cache_forces_authoritative_read(self):
        path = self.root / native.BANK_CACHE_FILE
        path.write_text('{bad')
        native.quota()
        self.assertEqual(self.rpc.call_count, 1)
        value = json.loads(path.read_text())
        value['checkedAt'] = self.now + 3600
        path.write_text(json.dumps(value))
        native.quota()
        self.assertEqual(self.rpc.call_count, 2)

    def test_missing_auth_or_unsupported_http_falls_back_to_existing_rpc(self):
        self.http.side_effect = None
        self.http.return_value = None
        result = native.quota()
        self.rpc.assert_called_once_with('/synthetic/codex', 20)
        self.assertEqual(result['expiryStatus'], 'fresh')
        self.assertEqual(result['expiries'], [1789949949.])
        self.assertFalse((self.root / native.BANK_CACHE_FILE).exists())


if __name__ == '__main__':
    unittest.main()
