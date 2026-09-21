from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from splitrail_desktop.portable import (
    SCHEMA, combined_usage, estimate_cost, import_export, make_event,
    merge_events, read_export, scan_codex, summary, usage_dataset, write_export,
)


def usage(inp=100, cached=60, output=10, reasoning=4, writes=0):
    return dict(input_tokens=inp, cached_input_tokens=cached, output_tokens=output,
                reasoning_output_tokens=reasoning, cache_write_input_tokens=writes,
                total_tokens=inp + output)


def row(kind, payload, second=0):
    return {"type": kind, "timestamp": f"2026-09-01T00:00:{second:02d}Z", "payload": payload}


def count(u, total=None, second=1):
    return row("event_msg", {"type": "token_count", "info": {
        "last_token_usage": u, "total_token_usage": total or u}}, second)


class PortableUsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def scan(self, rows, name="session", archived=False):
        folder = self.root / ("archived_sessions" if archived else "sessions")
        folder.mkdir(exist_ok=True)
        (folder / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return scan_codex(self.root, self.root / "empty-pi")

    def context(self):
        return row("turn_context", {"model": "gpt-6-astra"})

    def event(self, identity="one", u=None):
        return make_event(identity, "session", "2026-09-01T00:00:01Z", "gpt-6-astra", u or usage())

    def test_repeated_counters_are_not_requests(self):
        data = self.scan([self.context(), count(usage()), count(usage(), second=2), count(usage(), usage(200, 120, 20, 8), 3)])
        self.assertEqual(summary(data)["total_tokens"], 220)
        self.assertEqual(data["diagnostics"]["repeated_counters"], 1)

    def test_modern_records_supersede_ui_counters_and_deduplicate_response_ids(self):
        modern = row("token_usage_record", {"response_id": "response-1", "usage": usage()})
        data = self.scan([self.context(), modern, count(usage()), modern])
        self.assertEqual(summary(data)["total_tokens"], 110)
        self.assertEqual(data["diagnostics"]["copied_requests"], 1)

    def test_migration_retains_old_requests_before_modern_records(self):
        data = self.scan([self.context(), count(usage()), row("token_usage_record", {"response_id": "new", "usage": usage()}, 2), count(usage(), usage(200, 120, 20, 8), 3)])
        self.assertEqual(summary(data)["total_tokens"], 220)

    def test_cumulative_counter_recovers_a_missing_legacy_request(self):
        data = self.scan([self.context(), count(usage()), count(usage(), usage(300, 180, 30, 12), 3)])
        self.assertEqual(summary(data)["total_tokens"], 330)
        self.assertEqual(data["diagnostics"]["counter_gap_records"], 1)

    def test_fork_history_is_not_counted_again(self):
        rows = [row("session_meta", {"id": "child", "forked_from_id": "parent"}),
                count(usage()), self.context(), count(usage(), usage(200, 120, 20, 8), 2)]
        data = self.scan(rows)
        self.assertEqual(summary(data)["total_tokens"], 110)
        self.assertEqual(data["diagnostics"]["inherited_counters"], 1)

    def test_archived_and_active_copy_count_once(self):
        rows = [self.context(), count(usage())]
        self.scan(rows)
        data = self.scan(rows, archived=True)
        self.assertEqual(len(data["events"]), 1)

    def test_reasoning_cache_and_whole_request_long_context_pricing(self):
        event = self.event(u=usage(300_000, 200_000, 100, 40, 20_000))
        self.assertAlmostEqual(estimate_cost(event), 2.5075)
        tokens = usage_dataset([event]).days[0].tokens
        self.assertEqual(tokens.total, 300_100)
        self.assertEqual(tokens.reasoning, 40)

    def test_dated_pricing_and_unknown_model(self):
        event = self.event()
        event.update(model="gpt-5.6-luna", timestamp="2026-07-01T00:00:00+00:00")
        self.assertAlmostEqual(estimate_cost(event), .000106)
        event["model"] = "gpt-5.3-codex-spark"
        self.assertIsNone(estimate_cost(event))
        self.assertEqual(summary({"events": [event]})["unpriced_tokens"], 110)

    def test_repeat_and_overlapping_imports_are_idempotent(self):
        target = self.root / "laptop.json.gz"
        one, two = self.event(), self.event("two")
        write_export(target, {"schema": SCHEMA, "events": [one]})
        self.assertEqual(import_export(target, self.root), 1)
        self.assertEqual(import_export(target, self.root), 0)
        write_export(target, {"schema": SCHEMA, "events": [one, two]})
        self.assertEqual(import_export(target, self.root), 1)
        self.assertEqual(len(combined_usage({"events": [one]}, self.root)), 2)
        write_export(target, {"schema": SCHEMA, "events": [one]})
        self.assertEqual(import_export(target, self.root), 0)
        self.assertEqual(len(combined_usage({"events": []}, self.root)), 2)

    def test_import_strips_unrecognized_content_and_rejects_bad_counts(self):
        event = self.event()
        event["prompt"] = "not retained"
        path = self.root / "usage.json"
        write_export(path, {"schema": SCHEMA, "events": [event], "auth": "not retained"})
        self.assertNotIn("not retained", json.dumps(read_export(path)))
        event["usage"]["input_tokens"] = -1
        write_export(path, {"schema": SCHEMA, "events": [event]})
        with self.assertRaises(ValueError):
            read_export(path)

    def test_conflicting_import_leaves_existing_data_intact(self):
        target = self.root / "laptop.json.gz"
        event = self.event()
        write_export(target, {"schema": SCHEMA, "events": [event]})
        import_export(target, self.root)
        saved = (self.root / "imported-codex-usage.json.gz").read_bytes()
        event["usage"]["input_tokens"] += 1
        write_export(target, {"schema": SCHEMA, "events": [event]})
        with self.assertRaises(ValueError):
            import_export(target, self.root)
        self.assertEqual((self.root / "imported-codex-usage.json.gz").read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
