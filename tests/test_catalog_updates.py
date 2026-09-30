import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop import catalog_updates as updates
from splitrail_desktop.pricing import CATALOG, resolve_rates, save_override


class CatalogUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.original = copy.deepcopy(CATALOG)
        self.addCleanup(lambda: CATALOG.update(self.original))
        updates._attempts.clear()
        for target, value in (('splitrail_desktop.portable.data_dir', self.directory),
                              ('splitrail_desktop.catalog_updates.time.time', 1800000000)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.open = patch('splitrail_desktop.catalog_updates.urllib.request.urlopen')
        self.download = self.open.start()
        self.addCleanup(self.open.stop)
        self.remote = copy.deepcopy(CATALOG)
        self.remote['models']['new-test-model'] = dict(provider='OpenAI', input=3, output=12,
                                                     cache_read=.15, cache_write=3.75)

    def response(self, value):
        self.download.return_value = io.BytesIO(json.dumps(value).encode())

    def test_public_download_is_cached_and_does_not_send_account_or_usage(self):
        self.response(self.remote)
        self.assertTrue(updates.refresh_prices())
        self.assertEqual(resolve_rates('new-test-model')['input'], 3)
        request = self.download.call_args.args[0]
        self.assertEqual(request.full_url, updates.CATALOG_URL)
        self.assertIsNone(request.data)
        self.assertNotIn('Authorization', request.headers)
        self.assertNotIn('new-test-model', request.full_url)
        self.assertEqual(self.download.call_args.kwargs['timeout'], 5)
        saved = (self.directory / 'price-catalog.json').read_bytes()
        self.assertFalse(updates.refresh_prices())
        self.download.assert_called_once()
        self.assertEqual((self.directory / 'price-catalog.json').read_bytes(), saved)

    def test_offline_restart_loads_cache_and_custom_rates_keep_priority(self):
        self.response(self.remote)
        updates.refresh_prices()
        save_override('new-test-model', dict(input=9, output=18, cache_read=0, cache_write=0))
        CATALOG.update(self.original)
        self.download.reset_mock()
        self.assertTrue(updates.refresh_prices(allow_network=False))
        self.download.assert_not_called()
        self.assertEqual(resolve_rates('new-test-model')['input'], 9)
        self.assertEqual(CATALOG['models']['new-test-model']['input'], 3)

    def test_failed_download_keeps_prices_and_backs_off(self):
        self.download.side_effect = OSError('offline')
        self.assertFalse(updates.refresh_prices())
        self.assertFalse(updates.refresh_prices())
        self.download.assert_called_once()
        self.assertEqual(CATALOG, self.original)
        self.assertFalse((self.directory / 'price-catalog.json').exists())
        with patch('splitrail_desktop.catalog_updates.time.time', return_value=1800000000 + updates.RETRY_INTERVAL):
            updates.refresh_prices()
        self.assertEqual(self.download.call_count, 2)

    def test_invalid_or_older_download_never_replaces_valid_cache(self):
        self.response(self.remote)
        updates.refresh_prices()
        saved = (self.directory / 'price-catalog.json').read_bytes()
        bad_values = []
        for field, bad in (('input', -1), ('output', float('nan')), ('input', True),
                           ('cache_read', 'bad'), ('long_input_multiplier', -2),
                           ('long_context_threshold', True), ('provider', []),
                           ('scheduled', {'from': '2027-01-01', 'long_input_multiplier': 0})):
            candidate = copy.deepcopy(self.remote)
            candidate['models']['new-test-model'][field] = bad
            bad_values.append(candidate)
        for field, bad in (('version', 2), ('currency', 'AUD'), ('models', {}),
                           ('verified', '20260930'), ('aliases', {'a': 'missing-model'}),
                           ('verified', '2020-01-01')):
            candidate = copy.deepcopy(self.remote)
            candidate[field] = bad
            bad_values.append(candidate)
        for candidate in bad_values:
            with self.subTest(candidate=candidate):
                self.response(candidate)
                updates._attempts.clear()
                with patch('splitrail_desktop.catalog_updates.time.time', return_value=1800000000 + updates.CHECK_INTERVAL):
                    self.assertFalse(updates.refresh_prices())
                self.assertEqual((self.directory / 'price-catalog.json').read_bytes(), saved)
                self.assertEqual(CATALOG, self.remote)

    def test_oversized_malformed_and_unwritable_downloads_use_bundled_prices(self):
        for raw in (b'x' * (updates.MAX_CATALOG_BYTES + 1), b'not json', b'null', b'[]'):
            updates._attempts.clear()
            self.download.return_value = io.BytesIO(raw)
            self.assertFalse(updates.refresh_prices())
            self.assertEqual(CATALOG, self.original)
        self.response(self.remote)
        updates._attempts.clear()
        with patch('splitrail_desktop.catalog_updates.write_export', side_effect=OSError('disk full')):
            self.assertFalse(updates.refresh_prices())
        self.assertEqual(CATALOG, self.original)

    def test_enabled_updates_refresh_after_one_day(self):
        self.response(self.remote)
        updates.refresh_prices()
        newer = copy.deepcopy(self.remote)
        newer['models']['new-test-model']['cache_read'] = .1
        self.response(newer)
        with patch('splitrail_desktop.catalog_updates.time.time', return_value=1800000000 + updates.CHECK_INTERVAL):
            self.assertTrue(updates.refresh_prices())
        self.assertEqual(self.download.call_count, 2)
        self.assertEqual(resolve_rates('new-test-model')['cache_read'], .1)

    def test_numeric_rate_strings_are_normalized_before_becoming_active(self):
        self.remote['models']['new-test-model']['input'] = '3.0'
        self.response(self.remote)
        self.assertTrue(updates.refresh_prices())
        self.assertEqual(resolve_rates('new-test-model')['input'], 3.0)
