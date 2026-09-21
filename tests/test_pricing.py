from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.domain import DailyUsage, ModelDetail, TokenUsage, UsageDataset, aggregate_period
from splitrail_desktop.portable import estimate_cost, make_event
from splitrail_desktop.pricing import (CATALOG, PricingError, load_overrides, normalize_model,
                                      overrides_path, reprice_dataset, resolve_rates, save_override)
from splitrail_desktop.runner import run_splitrail
import subprocess


def dataset(name='gemini-3.8-flash', cost=0, tokens=None):
    tokens = tokens or TokenUsage(input=1_000_000, output=200_000, reasoning=100_000,
                                  cached=500_000, cache_read=500_000)
    detail = ModelDetail(name, 10, tokens, cost, 2)
    day = DailyUsage('Antigravity CLI', date(2026, 9, 22), 1, 2, 10, tokens,
                     cost + 1.25, 2, {name: 10}, (detail,))
    return UsageDataset((day,), {'Antigravity CLI': 1})


class PricingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        patcher = patch('splitrail_desktop.portable.data_dir', return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gemini_aliases_include_provider_and_version_variants(self):
        for name in ('gemini-3.8-flash', 'antigravity/gemini-3-8-flash', 'google/gemini-3.8-flash', 'models/gemini-3.8-flash'):
            self.assertEqual(resolve_rates(name)['input'], .75)
        self.assertEqual(resolve_rates('anthropic/claude-haiku-4-5-20251001')['output'], 5)
        self.assertEqual(resolve_rates('openai/gpt-5.4-2026-03-05')['input'], 2.5)
        for name in ('gpt-5.3-codex-spark', 'gemini-3.8-flash-image', 'claude-opus-9', 'unrelated/gpt-5'):
            self.assertIsNone(resolve_rates(name))

    def test_catalog_covers_all_requested_providers(self):
        self.assertGreaterEqual(len(CATALOG['models']), 50)
        self.assertEqual({v['provider'] for v in CATALOG['models'].values()}, {'OpenAI', 'Anthropic', 'Google', 'xAI'})
        self.assertEqual(resolve_rates('grok-code-fast-1'), resolve_rates('grok-build-0.1'))

    def test_gemini_cost_includes_thinking_and_cache_once(self):
        original = dataset()
        updated, resolved, unknown = reprice_dataset(original)
        expected = .75 + .3 * 3.75 + .5 * .075
        self.assertAlmostEqual(updated.days[0].model_details[0].cost, expected)
        self.assertAlmostEqual(updated.days[0].cost, expected + 1.25)
        self.assertEqual(updated.days[0].tokens, original.days[0].tokens)
        self.assertEqual(resolved, {'gemini-3.8-flash'})
        self.assertEqual(unknown, set())
        self.assertEqual(original.days[0].model_details[0].cost, 0)
        self.assertEqual(aggregate_period(updated, date(2026, 9, 1), date(2026, 9, 30)).total.cost, expected + 1.25)

    def test_daily_totals_do_not_trigger_request_context_premium(self):
        updated, _, _ = reprice_dataset(dataset('gemini-2.5-pro'))
        self.assertAlmostEqual(updated.days[0].model_details[0].cost, 1.25 + .3 * 10 + .5 * .125)

    def test_existing_collector_prices_are_preserved(self):
        original = dataset(cost=73.5)
        updated, _, _ = reprice_dataset(original)
        self.assertEqual(updated, original)

    def test_codex_reasoning_is_not_charged_twice(self):
        tokens = TokenUsage(input=1_000_000, output=200_000, reasoning=100_000, reasoning_in_output=100_000)
        updated, _, _ = reprice_dataset(dataset('gpt-6-astra', tokens=tokens))
        self.assertEqual(updated.days[0].model_details[0].cost, 20)

    def test_claude_cache_total_is_not_added_to_cache_detail(self):
        tokens = TokenUsage(input=1_000_000, output=100_000, cached=600_000, cache_read=500_000, cache_write=100_000)
        updated, _, _ = reprice_dataset(dataset('claude-sonnet-4-6', tokens=tokens))
        self.assertAlmostEqual(updated.days[0].model_details[0].cost, 3 + 1.5 + .15 + .375)

    def test_overrides_persist_apply_and_remove(self):
        rates = dict(input=2, output=4, cache_read=.1, cache_write=3)
        save_override('antigravity/gemini-3-8-flash', rates)
        self.assertEqual(load_overrides(), {'gemini-3.8-flash': rates})
        updated, _, _ = reprice_dataset(dataset(cost=90))
        self.assertAlmostEqual(updated.days[0].cost, 2 + 1.2 + .05 + 1.25)
        save_override('gemini-3.8-flash', None)
        self.assertEqual(load_overrides(), {})
        self.assertEqual(resolve_rates('gemini-3.8-flash')['input'], .75)

    def test_unknown_model_can_be_free_and_keeps_all_tokens(self):
        save_override('my-model', dict(input=0, output=0, cache_read=0, cache_write=0))
        updated, resolved, unknown = reprice_dataset(dataset('my-model'))
        self.assertEqual(updated.days[0].cost, 1.25)
        self.assertEqual(resolved, {'my-model'})
        self.assertFalse(unknown)
        self.assertEqual(updated.days[0].tokens, dataset().days[0].tokens)

    def test_unsupported_cache_rates_stay_unpriced(self):
        save_override('my-model', dict(input=1, output=2, cache_read=None, cache_write=None))
        _, resolved, unknown = reprice_dataset(dataset('my-model'))
        self.assertFalse(resolved)
        self.assertEqual(unknown, {'my-model'})

    def test_incomplete_custom_rate_keeps_existing_cost_and_warns(self):
        save_override('my-model', dict(input=1, output=2))
        updated, resolved, unknown = reprice_dataset(dataset('my-model', cost=12))
        self.assertEqual(updated.days[0].cost, 13.25)
        self.assertFalse(resolved)
        self.assertEqual(unknown, {'my-model'})

    def test_invalid_rates_do_not_replace_saved_file(self):
        rates = dict(input=1, output=2, cache_read=0, cache_write=0)
        save_override('my-model', rates)
        saved = overrides_path().read_bytes()
        for value in (-1, float('nan'), float('inf'), 'not a number', True, None, 1e100):
            with self.subTest(value=value), self.assertRaises(PricingError):
                save_override('my-model', {**rates, 'input': value})
            self.assertEqual(overrides_path().read_bytes(), saved)

    def test_failed_atomic_write_preserves_saved_file(self):
        save_override('my-model', dict(input=1, output=2))
        saved = overrides_path().read_bytes()
        with patch('splitrail_desktop.pricing.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                save_override('my-model', dict(input=3, output=4))
        self.assertEqual(overrides_path().read_bytes(), saved)
        self.assertEqual(list(self.directory.iterdir()), [overrides_path()])

    def test_malformed_custom_file_is_not_silently_overwritten(self):
        overrides_path().write_text('{bad json')
        with self.assertRaises(PricingError):
            save_override('new-model', dict(input=1, output=2))
        self.assertEqual(overrides_path().read_text(), '{bad json')

    def test_portable_usage_applies_custom_price_without_counting_reasoning_twice(self):
        save_override('my-model', dict(input=2, output=4, cache_read=.1, cache_write=3))
        event = make_event('id', 'session', '2026-09-22T00:00:00Z', 'my-model',
                           dict(input_tokens=1_000_000, cached_input_tokens=500_000,
                                cache_write_input_tokens=100_000, output_tokens=200_000, reasoning_output_tokens=100_000))
        self.assertAlmostEqual(estimate_cost(event), .4 * 2 + .5 * .1 + .1 * 3 + .2 * 4)

    def test_promotion_end_uses_usage_date(self):
        self.assertEqual(resolve_rates('gemini-3.8-flash', '2026-12-31')['input'], .75)
        self.assertEqual(resolve_rates('gemini-3.8-flash', '2027-01-01')['input'], 1.5)

    def test_runner_repairs_missing_model_prices_and_diagnostics(self):
        from splitrail_desktop.domain import parse_stats_payload
        payload = {'analyzer_stats': [{'analyzer_name': 'Pi Agent', 'daily_stats': {
            '2026-09-22': {'stats': {'costCents': 0}, 'models': {'antigravity/gemini-3-8-flash': 1},
            'model_stats': {'antigravity/gemini-3-8-flash': {'inputTokens': 1_000_000, 'outputTokens': 100_000, 'cost': 0}}}}}]}
        completed = subprocess.CompletedProcess(['splitrail', 'stats'], 0, json.dumps(payload),
            'Unknown model: antigravity/gemini-3-8-flash. Defaulting to $0.\nUnknown model: unrecoverable-model. Defaulting to $0.')
        with patch('splitrail_desktop.runner._find_executable', return_value='splitrail'), \
             patch('splitrail_desktop.runner._check_splitrail_version'), \
             patch('splitrail_desktop.activity.read_collector', return_value=(
                 parse_stats_payload(payload),
                 0, completed.stderr)):
            result = run_splitrail()
        self.assertAlmostEqual(result.dataset.days[0].cost, 1.125)
        self.assertEqual(result.cost_diagnostics.unknown_models, ('unrecoverable-model',))
        self.assertFalse(any('Unknown model: antigravity/' in n for n in result.cost_diagnostics.lines))

class AntigravityTokenTests(unittest.TestCase):
    def test_collector_reasoning_is_a_subset_of_output(self):
        from splitrail_desktop.domain import parse_stats_payload
        counts = dict(inputTokens=1_000_000, outputTokens=200_000, reasoningTokens=100_000)
        payload = {'analyzer_stats': [{'analyzer_name': 'Antigravity CLI', 'daily_stats': {
            '2026-09-22': {'stats': counts, 'models': {'gemini-3.8-flash': 1},
                          'model_stats': {'gemini-3.8-flash': counts}}}}]}
        original = parse_stats_payload(payload)
        self.assertEqual(original.days[0].tokens.total, 1_200_000)
        self.assertEqual(original.days[0].model_details[0].tokens.total, 1_200_000)
        updated, _, _ = reprice_dataset(original, overrides={})
        self.assertEqual(updated.days[0].cost, .75 + .2 * 3.75)
