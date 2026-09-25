from __future__ import annotations

import base64
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop import sync
from splitrail_desktop.domain import UsageDataset, aggregate_period
from splitrail_desktop.sync_payload import add_devices, encode_dataset, decode_dataset
from test_combined import collector_fixture


class FakeGitHub:
    def __init__(self):
        self.files = {}
        self.blobs = {}
        self.writes = []
        self.private = True
        self.branch = 'trunk'
        self.failure = None
        self.calls = []

    def __call__(self, endpoint, *, payload=None, method='GET', raw=False, missing_ok=False, query=None):
        self.calls.append((endpoint, method))
        if self.failure:
            raise self.failure
        if endpoint == 'user':
            return b'{"login":"example-user"}'
        if endpoint == 'user/repos':
            assert payload['private'] is True
            return b'{"private":true}'
        if '/contents/' not in endpoint and '/git/blobs/' not in endpoint:
            return json.dumps({'private': self.private, 'permissions': {'push': True}, 'default_branch': self.branch}).encode()
        if '/git/blobs/' in endpoint:
            return self.blobs[endpoint.rsplit('/', 1)[1]]
        path = endpoint.split('/contents/', 1)[1]
        if method == 'PUT':
            self.assert_branch(payload)
            self.writes.append(copy.deepcopy(payload))
            raw = base64.b64decode(payload['content'])
            import hashlib
            sha = hashlib.sha1(raw).hexdigest()
            self.files[path] = {'path': path, 'type': 'file', 'size': len(raw), 'sha': sha}
            self.blobs[sha] = raw
            return json.dumps({'content': {'sha': sha}}).encode()
        if path == 'usage':
            return json.dumps(list(self.files.values())).encode() if self.files else None
        return json.dumps(self.files[path]).encode() if path in self.files else None

    def assert_branch(self, payload):
        if payload['branch'] != self.branch:
            raise AssertionError('Sync hard-coded the branch')


class GitHubSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeGitHub()
        self.patch = patch('splitrail_desktop.sync._api', side_effect=self.api)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def device(self, name, **options):
        directory = self.root / name
        config = sync.configure('example/private-usage', directory, **options)
        return directory, config

    def snapshot(self, config):
        return encode_dataset(collector_fixture(), config['device'], config['scope'])

    def test_three_devices_roundtrip_costs_counts_and_repeated_sync(self):
        devices = [self.device(str(i)) for i in range(3)]
        for directory, config in devices:
            sync.sync_now(directory, snapshot=self.snapshot(config))
        for directory, config in devices:
            report = sync.sync_now(directory, snapshot=self.snapshot(config))
            self.assertEqual(report['devices'], 3)
            cached = sync.cached_devices(directory)
            self.assertEqual(len(cached), 2)
            combined = add_devices(collector_fixture(), cached, config['device'])
            full = aggregate_period(combined, combined.first_day, combined.last_day)
            local = aggregate_period(collector_fixture(), combined.first_day, combined.last_day)
            self.assertEqual(full.total.tokens.total, local.total.tokens.total * 3)
            self.assertAlmostEqual(full.total.cost, local.total.cost * 3)
        self.assertEqual(len(self.api.writes), 3)
        self.assertEqual(len(self.api.files), 3)
        for body in self.api.writes:
            self.assertIn('[skip ci]', body['message'])
            self.assertEqual(body['author']['email'], 'sync@users.noreply.github.com')
        self.assertTrue(all('.github' not in endpoint for endpoint, _ in self.api.calls))

    def test_source_costs_preserved_without_repricing_on_receiver(self):
        sender, cfg = self.device('a')
        snapshot = self.snapshot(cfg)
        snapshot['days'][0]['cost'] = 123.456789
        snapshot['days'][0]['models'][0]['cost'] = 123.456789
        sync.sync_now(sender, snapshot=snapshot)
        receiver, receive_cfg = self.device('b', receive_only=True)
        sync.sync_now(receiver)
        cached = sync.cached_devices(receiver)[cfg['device']]
        self.assertEqual(cached['days'][0]['cost'], 123.456789)
        self.assertEqual(cached['days'][0]['models'][0]['cost'], 123.456789)
        self.assertEqual(len(self.api.writes), 1)

    def test_public_repository_rejected_before_configuration_or_transfer(self):
        directory, config = self.device('a')
        self.api.private = False
        with self.assertRaises(sync.SyncError):
            sync.configure('example/public', self.root / 'b')
        with self.assertRaises(sync.SyncError):
            sync.sync_now(directory, snapshot=self.snapshot(config))
        self.assertEqual(self.api.writes, [])
        self.assertFalse((self.root / 'b' / sync.SETTINGS_FILE).exists())

    def test_own_device_excluded_and_identity_stable_across_disconnect(self):
        directory, config = self.device('a')
        sync.sync_now(directory, snapshot=self.snapshot(config))
        self.assertEqual(sync.cached_devices(directory), {})
        sync.disconnect(directory)
        self.assertIsNone(sync.settings(directory))
        self.assertFalse((directory / sync.CACHE_FILE).exists())
        updated = sync.configure('example/other-private', directory)
        self.assertEqual(updated['device'], config['device'])
        self.assertEqual(len(self.api.files), 1)

    def test_offline_auto_sync_retains_cache_and_marks_status(self):
        sender, sender_cfg = self.device('sender')
        sync.sync_now(sender, snapshot=self.snapshot(sender_cfg))
        receiver, cfg = self.device('receiver', automatic=True, receive_only=True)
        sync.sync_now(receiver)
        original = (receiver / sync.CACHE_FILE).read_bytes()
        self.api.failure = sync.SyncError('Offline')
        with patch('splitrail_desktop.sync.data_dir', return_value=receiver):
            self.assertIn('last downloaded', sync.auto_receive())
        self.assertEqual(original, (receiver / sync.CACHE_FILE).read_bytes())
        self.assertIn('Offline', sync.status_text(receiver))

    def test_invalid_download_is_atomic_and_does_not_follow_urls(self):
        sender, cfg = self.device('sender')
        sync.sync_now(sender, snapshot=self.snapshot(cfg))
        receiver, other = self.device('receiver', receive_only=True)
        sync.sync_now(receiver)
        original = (receiver / sync.CACHE_FILE).read_bytes()
        path = f"usage/{cfg['device']}.json"
        self.api.files[path]['sha'] = 'f' * 40
        self.api.files[path]['download_url'] = 'https://untrusted.invalid/payload'
        self.api.blobs['f' * 40] = b'{"invalid":true}'
        with self.assertRaises(ValueError):
            sync.sync_now(receiver)
        self.assertEqual(original, (receiver / sync.CACHE_FILE).read_bytes())
        self.assertFalse(any('untrusted' in endpoint for endpoint, _ in self.api.calls))

    def test_unknown_fields_are_removed_before_upload(self):
        directory, config = self.device('a')
        snapshot = self.snapshot(config)
        snapshot['credential'] = 'TEST_PRIVATE_MARKER'
        snapshot['days'][0]['prompt'] = 'TEST_PRIVATE_MARKER'
        snapshot['days'][0]['models'][0]['path'] = 'TEST_PRIVATE_MARKER'
        sync.sync_now(directory, snapshot=snapshot)
        raw = base64.b64decode(self.api.writes[0]['content'])
        self.assertNotIn(b'TEST_PRIVATE_MARKER', raw)

    def test_deleting_a_device_removes_its_contribution(self):
        first, cfg = self.device('first')
        sync.sync_now(first, snapshot=self.snapshot(cfg))
        second, other = self.device('second', receive_only=True)
        sync.sync_now(second)
        self.api.files.clear()
        sync.sync_now(second)
        self.assertEqual(sync.cached_devices(second), {})

    def test_same_device_new_snapshot_replaces_totals(self):
        sender, cfg = self.device('sender')
        receiver, _ = self.device('receiver', receive_only=True)
        sync.sync_now(sender, snapshot=self.snapshot(cfg))
        sync.sync_now(receiver)
        snapshot = self.snapshot(cfg)
        snapshot['days'][0]['tokens']['input'] += 100
        sync.sync_now(sender, snapshot=snapshot)
        sync.sync_now(receiver)
        cached = sync.cached_devices(receiver)
        self.assertEqual(cached[cfg['device']]['days'][0]['tokens']['input'], 150)
        self.assertEqual(len(cached), 1)

    def test_manual_connection_never_syncs_in_background(self):
        directory, _ = self.device('a', automatic=False)
        before = len(self.api.calls)
        with patch('splitrail_desktop.sync.data_dir', return_value=directory):
            self.assertIsNone(sync.auto_receive())
        self.assertEqual(before, len(self.api.calls))

    def test_repo_creation_is_private_and_bad_names_rejected(self):
        self.assertEqual(sync.create_private_repo('splitrail-usage'), 'example-user/splitrail-usage')
        for name in ('owner/repo', '../usage', 'x\nsecret', '', '--public'):
            with self.assertRaises(sync.SyncError):
                sync.create_private_repo(name)

    def test_local_scan_does_not_load_imports_or_downloads(self):
        _, config = self.device('a')
        from splitrail_desktop.runner import StatsCommandResult, CostDiagnostics
        with patch('splitrail_desktop.runner.run_splitrail', return_value=StatsCommandResult(collector_fixture(), CostDiagnostics((), 0, 0, ()), 0)) as collect:
            result = sync._collect(config)
        collect.assert_called_once()
        self.assertEqual(len(result['days']), 3)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux collector subprocess')
    def test_linux_desktop_sync_finds_cargo_collector_with_restricted_path(self):
        from splitrail_desktop.__main__ import main
        from test_activity import fixture, local_zone
        home = self.root / 'home'
        executable = home / '.cargo/bin/splitrail'
        executable.parent.mkdir(parents=True)
        executable.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "--version" ]; then\n'
            '  printf "splitrail 3.9.1\\n"\n'
            'elif [ "$1" = "stats" ] && [ "$2" = "--include-messages" ]; then\n'
            '  cat <<\'SYNTHETIC_USAGE\'\n' + json.dumps(fixture()) + '\n'
            'SYNTHETIC_USAGE\n'
            'else\n  exit 1\nfi\n', encoding='utf-8')
        executable.chmod(0o755)
        sender, config = self.device('sender')
        receiver, _ = self.device('receiver', receive_only=True)
        with local_zone('UTC'), \
             patch('splitrail_desktop.platform_support.Path.home', return_value=home), \
             patch('splitrail_desktop.runner.SPLITRAIL_FALLBACK', home / '.local/bin/splitrail'), \
             patch('splitrail_desktop.portable.data_dir', return_value=sender), \
             patch('splitrail_desktop.sync.data_dir', return_value=sender), \
             patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'SPLITRAIL_BIN': ''}):
            # Exercise the same startup as the desktop and command-line sync.
            with patch('builtins.print') as output:
                result = main(['--sync-usage'])
            self.assertEqual(result, 0, output.call_args_list)
            sync.sync_now(receiver)
        uploaded = sync.cached_devices(receiver)[config['device']]
        self.assertEqual(uploaded['scope'], 'all')
        self.assertEqual(uploaded['days'][0]['tokens']['input'], 300)
        self.assertEqual(len(self.api.writes), 1)
        self.assertNotIn(b'PRIVATE', base64.b64decode(self.api.writes[0]['content']))

    def test_missing_collector_explains_source_and_preserves_saved_totals(self):
        from splitrail_desktop.runner import MissingCommandError
        directory, config = self.device('a', automatic=True)
        sync.sync_now(directory, snapshot=self.snapshot(config))
        original = (directory / sync.CACHE_FILE).read_bytes()
        writes = len(self.api.writes)
        with patch('splitrail_desktop.runner.run_splitrail', side_effect=MissingCommandError('missing')), \
             patch('splitrail_desktop.sync.data_dir', return_value=directory):
            with self.assertRaisesRegex(sync.SyncError, 'All tools sync needs the Splitrail collector') as raised:
                sync.sync_now(directory)
            self.assertIn('Share from this device', str(raised.exception))
            self.assertIn('Codex', str(raised.exception))
            self.assertIn('last downloaded', sync.auto_receive())
        self.assertEqual(len(self.api.writes), writes)
        self.assertEqual((directory / sync.CACHE_FILE).read_bytes(), original)
        self.assertEqual(sync.settings(directory)['scope'], 'all')
        self.assertIn('Offline', sync.status_text(directory))

    def test_collector_failure_keeps_actionable_diagnostic(self):
        from splitrail_desktop.runner import LocalCommandError
        directory, _ = self.device('a')
        with patch('splitrail_desktop.runner.run_splitrail', side_effect=LocalCommandError(
                f'Upgrade to Splitrail 3.9.1 or newer (executable: {Path.home()}/bin/splitrail).')):
            with self.assertRaisesRegex(sync.SyncError, 'Upgrade to Splitrail 3.9.1') as raised:
                sync.sync_now(directory)
        self.assertNotIn(str(Path.home()), str(raised.exception))
        self.assertEqual(self.api.writes, [])

    def test_codex_sync_and_download_only_do_not_require_collector(self):
        from splitrail_desktop.runner import MissingCommandError
        sender, config = self.device('codex', scope='codex')
        receiver, _ = self.device('receiver', receive_only=True)
        with patch('splitrail_desktop.runner.run_splitrail', side_effect=MissingCommandError('missing')) as collector, \
             patch('splitrail_desktop.portable.scan_codex', return_value={'events': []}):
            sync.sync_now(sender)
            sync.sync_now(receiver)
        collector.assert_not_called()
        self.assertEqual(sync.cached_devices(receiver)[config['device']]['scope'], 'codex')

    def test_unreadable_local_logs_report_file_access_without_upload(self):
        directory, _ = self.device('a', scope='codex')
        with patch('splitrail_desktop.portable.scan_codex', side_effect=PermissionError('private path')):
            with self.assertRaisesRegex(sync.SyncError, 'Check file access') as raised:
                sync.sync_now(directory)
        self.assertNotIn('private path', str(raised.exception))
        self.assertEqual(self.api.writes, [])

    def test_conflict_preserves_previous_cache(self):
        directory, config = self.device('a')
        sync.sync_now(directory, snapshot=self.snapshot(config))
        old = (directory / sync.CACHE_FILE).read_bytes()
        self.api.failure = sync.SyncError('Try again', 409)
        with self.assertRaises(sync.SyncError):
            sync.sync_now(directory, snapshot=self.snapshot(config))
        self.assertEqual(old, (directory / sync.CACHE_FILE).read_bytes())


class WirePrivacyTests(unittest.TestCase):
    def test_roundtrip_and_unknown_names_are_pseudonymized(self):
        payload = encode_dataset(collector_fixture(), 'a' * 32, 'all')
        raw = json.dumps(payload)
        self.assertNotIn('claude-fixture', raw)
        self.assertNotIn('openai-codex/', raw)
        self.assertNotIn('ignored_raw_messages', raw)
        decoded = decode_dataset(payload)
        self.assertAlmostEqual(sum(day.cost for day in decoded.days), sum(day.cost for day in collector_fixture().days))
        self.assertEqual(encode_dataset(decoded, 'a' * 32, 'all'), payload)

    def test_malformed_counts_nan_identity_and_paths_rejected(self):
        baseline = encode_dataset(collector_fixture(), 'a' * 32, 'all')
        for field, value in [('cost', float('nan')), ('cost', -1), ('conversations', True), ('conversations', 10**30), ('tool', '/private/path')]:
            candidate = copy.deepcopy(baseline)
            candidate['days'][0][field] = value
            with self.assertRaises(ValueError):
                decode_dataset(candidate)
        with self.assertRaises(ValueError):
            decode_dataset(baseline, expected_device='b' * 32)


if __name__ == '__main__':
    unittest.main()
