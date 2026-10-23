from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop import portable, scan_cache
from test_portable import count, row, usage


def encoded(rows, newline=True):
    return ("\n".join(json.dumps(record) for record in rows) + ("\n" if newline else "")).encode()


def modern(identity="request-1", second=1, **extra):
    return row("token_usage_record", {"response_id": identity, "usage": usage(), **extra}, second)


def context(model="gpt-6-astra"):
    return row("turn_context", {"model": model})


def pi_record(identity="pi-1", **extra):
    return {"id": identity, "timestamp": "2026-09-01T00:00:01Z", "message": {
        "provider": "openai-codex", "model": "gpt-6-astra", "content": "synthetic response",
        "usage": {"input": 40, "cacheRead": 50, "cacheWrite": 10, "output": 10, "reasoning": 4}, **extra}}


class ScanCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "codex"
        self.pi = self.base / "pi"
        self.directory = self.base / "data"
        self.path = self.root / "sessions" / "parent.jsonl"
        environment = patch.dict(os.environ, {"SPLITRAIL_DATA_DIR": str(self.directory)})
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, rows, path=None, *, newline=True, append=False):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab" if append else "wb") as stream:
            stream.write(encoded(rows, newline))
        return path

    def scan(self, **kwargs):
        return portable.scan_codex(self.root, self.pi, **kwargs)

    def fresh(self, **kwargs):
        with patch.object(scan_cache, "ScanCache", side_effect=scan_cache.CacheUnavailable):
            return self.scan(**kwargs)

    def assert_equivalent(self, cached, **kwargs):
        fresh = self.fresh(**kwargs)
        self.assertEqual(cached["events"], fresh["events"])
        for key in ("schema", "pricing_as_of", "pricing_source"):
            self.assertEqual(cached[key], fresh[key])
        semantic = lambda result: {key: value for key, value in result["diagnostics"].items()
                                   if not key.startswith("scan_")}
        self.assertEqual(semantic(cached), semantic(fresh))

    def test_warm_scan_reads_no_log_bytes_and_append_only_reads_suffix_and_probes(self):
        self.write([context(), modern()])
        cold = self.scan()
        self.assertGreaterEqual(cold["diagnostics"]["scan_bytes_read"], self.path.stat().st_size)
        warm = self.scan()
        self.assertEqual(warm["diagnostics"]["scan_bytes_read"], 0)
        self.assertEqual(warm["diagnostics"]["scan_cache_hits"], 1)
        self.assert_equivalent(warm)
        suffix = encoded([modern("request-2", 2)])
        with self.path.open("ab") as stream:
            stream.write(suffix)
        appended = self.scan()
        diag = appended["diagnostics"]
        self.assertEqual(diag["scan_bytes_read"] - diag["scan_probe_bytes"], len(suffix))
        self.assertLessEqual(diag["scan_probe_bytes"], 4 * scan_cache.ANCHOR_BYTES)
        self.assertEqual(diag["scan_cache_incremental_files"], 1)
        self.assertEqual(len(appended["events"]), 2)
        self.assert_equivalent(appended)

    def test_checkpoint_survives_a_new_worker_process(self):
        self.write([context(), modern(thread_id="private-thread-id")])
        original = self.scan()
        code = """import json, sys
from pathlib import Path
from splitrail_desktop.portable import scan_codex, digest
result = scan_codex(Path(sys.argv[1]), Path(sys.argv[2]))
print(json.dumps([result['diagnostics']['scan_bytes_read'], digest(result['events'])]))
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.root), str(self.pi)],
                                capture_output=True, text=True, check=True, env=os.environ.copy())
        self.assertEqual(json.loads(result.stdout), [0, portable.digest(original["events"])])

    def test_every_legacy_modern_model_and_counter_boundary_matches_a_fresh_scan(self):
        records = [context(), count(usage(), usage(300, 180, 30, 12)),
                   count(usage(), usage(300, 180, 30, 12), 2),
                   context("gpt-5.6-luna"), count(None, usage(400, 240, 40, 16), 3),
                   count(None, usage(50, 30, 5, 2), 4),
                   modern("invalid", 5, usage=None), count(usage(), usage(500, 300, 50, 20), 6),
                   modern("modern", 7, thread_id="new-thread"), context(),
                   modern("modern", 8), modern("another", 9)]
        for index, record in enumerate(records):
            with self.subTest(boundary=index):
                self.write([record], append=True)
                result = self.scan()
                self.assert_equivalent(result)
                self.assert_equivalent(self.scan())
        self.assertEqual(result["diagnostics"]["counter_resets"], 1)
        self.assertEqual(result["diagnostics"]["counter_gap_records"], 1)
        self.assertEqual(result["diagnostics"]["repeated_counters"], 1)
        self.assertEqual(result["diagnostics"]["copied_requests"], 1)
        self.assertEqual(result["diagnostics"]["invalid_usage"], 1)
        self.assertEqual(result["events"][-1]["session"], portable.digest("new-thread"))

    def test_fork_replay_and_baselines_survive_each_checkpoint(self):
        for first_context in (False, True):
            for cumulative_only in (False, True):
                with self.subTest(first_context=first_context, cumulative_only=cumulative_only):
                    self.path.unlink(missing_ok=True)
                    records = [row("session_meta", {"id": "child", "parent_thread_id": "parent"})]
                    if first_context:
                        records.append(context())
                    records.append(count(None if cumulative_only else usage(), usage(1000, 600, 100, 40)))
                    if not first_context:
                        records.append(context())
                    records.append(count(usage(), usage(1100, 660, 110, 44), 2))
                    for record in records:
                        self.write([record], append=True)
                        result = self.scan()
                        self.assert_equivalent(result)
                    self.assertEqual(portable.summary(result)["total_tokens"],
                                     220 if first_context and not cumulative_only else 110)

    def test_large_counter_gap_keeps_unknown_model_across_checkpoints(self):
        self.write([context(), count(usage())])
        self.scan()
        self.write([count(usage(), usage(400_200, 240_120, 40_020, 16_008), 2)], append=True)
        result = self.scan()
        self.assertTrue(any(event["model"] == "unknown-counter-gap" for event in result["events"]))
        self.assert_equivalent(result)
        self.assert_equivalent(self.scan())

    def test_unknown_duplicate_model_is_enriched_in_original_file_order(self):
        self.write([modern()], self.root / "archived_sessions" / "unknown.jsonl")
        self.write([context(), modern(), context("gpt-5.6-luna"), modern()])
        result = self.scan()
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["model"], "gpt-6-astra")
        self.assertEqual(result["diagnostics"]["copied_requests"], 2)
        self.assert_equivalent(result)
        self.assert_equivalent(self.scan())

    def test_archive_move_reuses_checkpoint_and_copy_deduplicates(self):
        self.write([context(), count(usage())])
        before = self.scan()
        archived = self.root / "archived_sessions" / self.path.name
        archived.parent.mkdir()
        self.path.rename(archived)
        moved = self.scan()
        self.assertEqual(moved["events"], before["events"])
        self.assertEqual(moved["diagnostics"]["scan_cache_hits"], 1)
        self.assertEqual(moved["diagnostics"]["scan_bytes_read"] - moved["diagnostics"]["scan_probe_bytes"], 0)
        self.assert_equivalent(moved)
        shutil.copyfile(archived, self.path)
        copied = self.scan()
        self.assertEqual(len(copied["events"]), 1)
        self.assertEqual(copied["diagnostics"]["copied_requests"], 1)
        self.assert_equivalent(copied)

    def test_basename_change_invalidates_path_dependent_fork_state(self):
        self.write([row("session_meta", {"id": "parent"}), context(), count(usage(), usage(300, 180, 30, 12))])
        before = self.scan()
        self.assertEqual(portable.summary(before)["total_tokens"], 330)
        self.path.rename(self.path.with_name("child.jsonl"))
        after = self.scan()
        self.assertEqual(portable.summary(after)["total_tokens"], 110)
        self.assert_equivalent(after)

    def test_truncation_and_regrowth_never_retain_old_requests(self):
        self.write([context(), modern(), modern("request-2", 2)])
        self.scan()
        self.write([context(), modern("request-3", 3)])
        truncated = self.scan()
        self.assertEqual(truncated["diagnostics"]["scan_cache_resets"], 1)
        self.assertEqual(len(truncated["events"]), 1)
        self.assert_equivalent(truncated)
        # Truncate and regrow past the previous size between workers, same inode.
        self.write([context(), modern("request-4", 4), modern("request-5", 5), modern("request-6", 6)])
        regrown = self.scan()
        self.assertEqual(regrown["diagnostics"]["scan_cache_resets"], 1)
        self.assertEqual(len(regrown["events"]), 3)
        self.assert_equivalent(regrown)
        self.path.write_bytes(b"")
        self.assertEqual(self.scan()["events"], [])

    def test_atomic_replacement_and_same_size_edits_invalidate(self):
        self.write([context(), modern("request-1")])
        self.scan()
        stat = self.path.stat()
        replacement = self.write([context(), modern("request-2")], self.path.with_suffix(".replacement"))
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        replacement.replace(self.path)
        self.assert_equivalent(self.scan())
        self.write([context(), modern("request-3")])
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        edited = self.scan()
        self.assertEqual(edited["diagnostics"]["scan_cache_resets"], 1)
        self.assert_equivalent(edited)

    def test_missing_files_are_removed_and_pi_toggle_does_not_purge_pi_cache(self):
        self.write([context(), modern()])
        pi_path = self.write([pi_record()], self.pi / "pi-session.jsonl")
        self.scan()
        codex_only = self.scan(include_pi=False)
        self.assertEqual(len(codex_only["events"]), 1)
        self.assert_equivalent(codex_only, include_pi=False)
        self.assertEqual(self.scan()["diagnostics"]["scan_bytes_read"], 0)
        self.path.unlink()
        pi_only = self.scan()
        self.assertEqual({event["source"] for event in pi_only["events"]}, {"Codex via Pi"})
        self.assert_equivalent(pi_only)
        pi_path.unlink()
        self.assertEqual(self.scan()["events"], [])
        with closing(sqlite3.connect(self.directory / scan_cache.CACHE_NAME)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_partial_final_line_is_provisional_until_newline(self):
        self.write([context()])
        pending = encoded([count(usage(), usage(300, 180, 30, 12))], newline=False)
        split = len(pending) // 2
        with self.path.open("ab") as stream:
            stream.write(pending[:split])
        broken = self.scan()
        self.assertEqual(broken["events"], [])
        self.assertEqual(broken["diagnostics"]["malformed_lines"], 1)
        self.assert_equivalent(broken)
        with self.path.open("ab") as stream:
            stream.write(pending[split:])
        valid_tail = self.scan()
        self.assertEqual(portable.summary(valid_tail)["total_tokens"], 330)
        self.assert_equivalent(valid_tail)
        self.assert_equivalent(self.scan())
        with self.path.open("ab") as stream:
            stream.write(b"\n" + encoded([count(usage(), usage(400, 240, 40, 16), 2)]))
        completed = self.scan()
        self.assertEqual(portable.summary(completed)["total_tokens"], 440)
        self.assertNotIn("malformed_lines", completed["diagnostics"])
        self.assert_equivalent(completed)
        self.assertEqual(self.scan()["diagnostics"]["scan_bytes_read"], 0)

    def test_previously_valid_tail_can_be_invalidated_by_more_bytes(self):
        self.write([context(), modern()], newline=False)
        self.assertEqual(len(self.scan()["events"]), 1)
        with self.path.open("ab") as stream:
            stream.write(b"trailing-garbage\n")
        result = self.scan()
        self.assertEqual(result["events"], [])
        self.assert_equivalent(result)

    def test_partial_context_does_not_commit_model_state(self):
        self.write([context()], newline=False)
        self.scan()
        with self.path.open("ab") as stream:
            stream.write(b"garbage\n" + encoded([modern()]))
        result = self.scan()
        self.assertEqual(result["events"][0]["model"], "unknown")
        self.assert_equivalent(result)

    def test_pi_append_partial_lines_duplicates_and_archive_moves(self):
        path = self.write([pi_record()], self.pi / "session.jsonl")
        self.scan()
        tail = encoded([pi_record("pi-2")], newline=False)
        with path.open("ab") as stream:
            stream.write(tail[:-8])
        self.assert_equivalent(self.scan())
        with path.open("ab") as stream:
            stream.write(tail[-8:])
        self.assert_equivalent(self.scan())
        with path.open("ab") as stream:
            stream.write(b"\n")
        completed = self.scan()
        self.assertEqual(len(completed["events"]), 2)
        self.assert_equivalent(completed)
        nested = path.parent / "archive" / path.name
        nested.parent.mkdir()
        path.rename(nested)
        moved = self.scan()
        self.assertEqual(moved["diagnostics"]["scan_cache_hits"], 1)
        self.assert_equivalent(moved)
        self.write([pi_record("pi-2", model="gpt-5.6-luna")], path.with_name("z-copy.jsonl"))
        copied = self.scan()
        self.assertEqual(len(copied["events"]), 2)
        self.assertEqual(copied["diagnostics"]["pi_records"], 3)
        self.assert_equivalent(copied)

    def test_giant_irrelevant_records_are_discarded_with_bounded_allocation(self):
        payload = b'{"type":"response_item","payload":{"output":"' + b"x" * (8 * 1024 * 1024) + b'"}}\n'
        payload += encoded([context(), modern()])
        stream = io.BytesIO(payload)
        diagnostics = defaultdict(int)
        tracemalloc.start()
        try:
            records = list(scan_cache.lines(stream, len(payload), "codex", diagnostics))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertIsNone(records[0][3])
        self.assertEqual(len(records), 3)
        self.assertLess(peak, 8 * scan_cache.READ_CHUNK)
        self.assertEqual(diagnostics["scan_bytes_read"], len(payload))

    def test_only_allowlisted_content_free_state_and_events_reach_sqlite(self):
        secret = "PRIVATE_PROMPT_RESPONSE_SENTINEL"
        counts = usage()
        counts.update(prompt=secret, extra={"response": secret})
        self.write([row("session_meta", {"id": "parent", "prompt": secret}), context(),
                    row("response_item", {"content": secret}), count(counts),
                    modern("private-response-id", thread_id="private-thread-id", content=secret)])
        self.write([pi_record(content=secret)], self.pi / "private-session-name.jsonl")
        self.scan()
        for path in self.directory.iterdir():
            raw = path.read_bytes()
            for forbidden in (secret, "private-response-id", "private-thread-id",
                              "private-session-name", str(self.base)):
                self.assertNotIn(forbidden.encode(), raw)
        self.assert_equivalent(self.scan())

    def test_unsafe_model_is_not_written_to_cache_and_output_is_preserved(self):
        model = "private free text pretending to be a model"
        self.write([context(model), modern()])
        result = self.scan()
        self.assertEqual(result["events"][0]["model"], model)
        self.assertEqual(result["diagnostics"]["scan_cache_errors"], 1)
        self.assert_equivalent(result)
        for path in self.directory.iterdir():
            self.assertNotIn(model.encode(), path.read_bytes())

    def test_unavailable_or_corrupt_cache_falls_back_without_losing_usage(self):
        self.write([context(), modern()])
        self.directory.write_text("not a directory")
        unavailable = self.scan()
        self.assertEqual(unavailable["diagnostics"]["scan_cache_errors"], 1)
        self.assert_equivalent(unavailable)
        self.directory.unlink()
        self.directory.mkdir()
        (self.directory / scan_cache.CACHE_NAME).write_bytes(b"not a sqlite database")
        corrupt = self.scan()
        self.assertEqual(corrupt["diagnostics"]["scan_cache_errors"], 1)
        self.assert_equivalent(corrupt)

    def test_schema_change_rebuilds_the_disposable_cache(self):
        self.write([context(), modern()])
        before = self.scan()
        with closing(sqlite3.connect(self.directory / scan_cache.CACHE_NAME)) as connection:
            connection.execute("PRAGMA user_version = 0")
        after = self.scan()
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(after["diagnostics"]["scan_cache_misses"], 1)
        self.assertEqual(self.scan()["diagnostics"]["scan_bytes_read"], 0)

    def test_damaged_checkpoint_rebuilds_without_replaying_stale_events(self):
        self.write([context(), modern()])
        before = self.scan()
        with closing(sqlite3.connect(self.directory / scan_cache.CACHE_NAME)) as connection:
            connection.execute("UPDATE files SET state = '{}' ")
            connection.commit()
        after = self.scan()
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(after["diagnostics"]["scan_cache_resets"], 1)
        self.assertEqual(self.scan()["diagnostics"]["scan_bytes_read"], 0)

    def test_concurrent_short_lived_workers_share_atomic_cache(self):
        self.write([context(), count(usage()), modern("modern", 2)])
        code = """import json, sys
from pathlib import Path
from splitrail_desktop.portable import scan_codex, digest
result = scan_codex(Path(sys.argv[1]), Path(sys.argv[2]))
print(json.dumps([result['diagnostics']['scan_bytes_read'], digest(result['events'])]))
"""
        command = [sys.executable, "-c", code, str(self.root), str(self.pi)]
        workers = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=os.environ.copy()) for _ in range(2)]
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(min(result[0] for result in results), 0)
        self.assert_equivalent(self.scan())

    def test_file_disappearing_after_discovery_is_not_replayed(self):
        self.write([context(), modern()])
        self.scan()
        scan_file = scan_cache.ScanCache.scan_file

        def removed(cache, path, *args):
            path.unlink()
            yield from scan_file(cache, path, *args)

        with patch.object(scan_cache.ScanCache, "scan_file", removed):
            result = self.scan()
        self.assertEqual(result["events"], [])
        self.assertEqual(result["diagnostics"]["scan_missing_files"], 1)
        self.assertEqual(self.scan()["events"], [])

    def test_interrupted_cache_write_rolls_back_events_and_checkpoint_together(self):
        self.write([context(), modern()])
        self.scan()
        self.write([modern("request-2", 2)], append=True)
        add = scan_cache.ScanCache._add

        def interrupted(*args):
            add(*args)
            raise sqlite3.OperationalError("synthetic interruption")

        with patch.object(scan_cache.ScanCache, "_add", interrupted):
            fallback = self.scan()
        self.assertEqual(fallback["diagnostics"]["scan_cache_errors"], 1)
        self.assert_equivalent(fallback)
        result = self.scan()
        self.assertEqual(len(result["events"]), 2)
        self.assert_equivalent(result)
        self.assertEqual(self.scan()["diagnostics"]["scan_bytes_read"], 0)

    def test_growth_during_scan_is_bounded_and_not_checkpointed(self):
        self.write([context(), modern()])
        original = scan_cache.lines

        def growing(*args):
            yield from original(*args)
            self.write([modern("request-2", 2)], append=True)

        with patch.object(scan_cache, "lines", growing):
            snapshot = self.scan()
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(snapshot["diagnostics"]["scan_changed_files"], 1)
        result = self.scan()
        self.assertEqual(len(result["events"]), 2)
        self.assert_equivalent(result)


if __name__ == "__main__":
    unittest.main()
