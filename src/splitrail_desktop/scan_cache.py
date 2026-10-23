"""Disposable, local, content-free checkpoints for the portable usage scanner.

Only normalized usage events, hashed identities, numeric counters and parser flags
reach SQLite. Log fragments (including unfinished lines) are never persisted.
One transaction covers events and their checkpoints, also serializing workers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

CACHE_NAME = "usage-scan.sqlite3"
VERSION = 1  # Bump when persisted parser state or event interpretation changes.
READ_CHUNK = 64 * 1024
ANCHOR_BYTES = 4096
_MODEL = re.compile(r"[A-Za-z0-9_.:/-]{1,100}")
_HASH = re.compile(r"[a-f0-9]{64}")
_COUNTERS = {"input_tokens", "cached_input_tokens", "cache_write_input_tokens",
             "output_tokens", "reasoning_output_tokens"}


class CacheUnavailable(Exception):
    """Use a fresh scan when the disposable cache cannot be trusted or written."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stamp(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
            getattr(stat, "st_birthtime_ns", getattr(stat, "st_birthtime", None))]


def _read(stream, length, diagnostics, *, probe=False):
    raw = stream.read(length)
    diagnostics["scan_bytes_read"] += len(raw)
    if probe:
        diagnostics["scan_probe_bytes"] += len(raw)
    return raw


def _anchors(stream, size, diagnostics, expected=None):
    ranges = [(0, min(size, ANCHOR_BYTES)),
              (max(min(size, ANCHOR_BYTES), size - ANCHOR_BYTES), size)]
    result = []
    for start, end in ranges:
        stream.seek(start)
        raw = _read(stream, end - start, diagnostics, probe=True)
        result.append([start, end, hashlib.sha256(raw).hexdigest()])
        if len(raw) != end - start:
            return None
    return result if expected is None else result == expected


def lines(stream, size, kind, diagnostics):
    """Bound the snapshot and discard irrelevant Codex records in small chunks.

    Offsets always denote complete physical lines. A final unterminated record
    is returned for this scan's compatibility, but is never checkpointed.
    """
    position = stream.tell()
    while position < size:
        start = position
        fragment = stream.readline(min(READ_CHUNK, size - position))
        diagnostics["scan_bytes_read"] += len(fragment)
        if not fragment:
            break
        position += len(fragment)
        keep = kind == "pi" or any(marker in fragment[:250] for marker in (
            b'"turn_context"', b'"session_meta"', b'"event_msg"', b'"token_usage_record"'))
        parts = [fragment] if keep else None
        while not fragment.endswith(b"\n") and position < size:
            fragment = stream.readline(min(READ_CHUNK, size - position))
            diagnostics["scan_bytes_read"] += len(fragment)
            if not fragment:
                break
            position += len(fragment)
            if keep:
                parts.append(fragment)
        yield start, position, fragment.endswith(b"\n"), b"".join(parts) if keep else None


def uncached_file(path, kind, state, parse, diagnostics):
    local = {}
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        for _, _, _, raw in lines(stream, size, kind, diagnostics):
            if raw is not None:
                yield from parse(raw, state, local)
    for key, value in local.items():
        diagnostics[key] += value


def _safe_model(model):
    return model is None or isinstance(model, str) and _MODEL.fullmatch(model)


def _check_state(state):
    # Explicit allowlist: never serialize arbitrary dictionaries from a log.
    if (set(state) != {"model", "session", "previous", "previous_key", "previous_none",
                       "previous_truthy", "modern_started", "forked", "context_started"}
            or not _safe_model(state["model"])
            or not all(isinstance(state[k], str) and _HASH.fullmatch(state[k])
                       for k in ("session", "previous_key"))
            or not isinstance(state["previous"], dict)
            or not set(state["previous"]) <= _COUNTERS
            or not all(type(n) in (int, float, bool, type(None)) for n in state["previous"].values())
            or not all(type(state[k]) is bool for k in (
                "previous_none", "previous_truthy", "modern_started", "forked", "context_started"))):
        raise CacheUnavailable()


class ScanCache:
    def __init__(self, directory: Path):
        self.connection = None
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / CACHE_NAME
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            self.connection = sqlite3.connect(path, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA cache_size = -2048")
            self.connection.execute("BEGIN IMMEDIATE")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version != VERSION:
                self.connection.execute("DROP TABLE IF EXISTS events")
                self.connection.execute("DROP TABLE IF EXISTS files")
                self.connection.execute("""CREATE TABLE files (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, path_key TEXT UNIQUE NOT NULL,
                    identity TEXT NOT NULL, stamp TEXT, offset INTEGER, anchors TEXT,
                    state TEXT, diagnostics TEXT)""")
                self.connection.execute("CREATE INDEX file_identity ON files(identity)")
                self.connection.execute("""CREATE TABLE events (
                    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
                    position INTEGER, ordinal INTEGER, mode TEXT, event TEXT,
                    PRIMARY KEY(file_id, position, ordinal)) WITHOUT ROWID""")
                self.connection.execute(f"PRAGMA user_version = {VERSION}")
            self.connection.execute("CREATE TEMP TABLE seen (id INTEGER PRIMARY KEY)")
        except (OSError, sqlite3.Error):
            self.close()
            raise CacheUnavailable() from None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def finish(self, scopes):
        for scope in scopes:
            self.connection.execute("DELETE FROM files WHERE scope = ? AND id NOT IN (SELECT id FROM seen)", (scope,))
        self.connection.commit()

    def _add(self, file_id, position, ordinal, mode, event):
        if (not _safe_model(event["model"])
                or set(event) != {"id", "session", "timestamp", "model", "source", "usage"}
                or set(event["usage"]) != _COUNTERS
                or not all(type(n) is int for n in event["usage"].values())):
            raise CacheUnavailable()
        self.connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                                (file_id, position, ordinal, mode, json.dumps(event, separators=(",", ":"))))

    def scan_file(self, path, scope, kind, state, parse, diagnostics):
        with path.open("rb") as stream:
            initial_state = state
            stamp = _stamp(os.fstat(stream.fileno()))
            path_key = _hash([scope, str(path.absolute())])
            # A different basename can change legacy fork detection/session ids.
            seed = path.stem if kind == "pi" else path.stem[-36:]
            identity = _hash([scope, kind, stamp[0], stamp[1], stamp[5], seed,
                              path_key if not stamp[1] else None])
            record = self.connection.execute("SELECT * FROM files WHERE path_key = ?", (path_key,)).fetchone()
            if record is None:
                record = self.connection.execute("SELECT * FROM files WHERE identity = ?", (identity,)).fetchone()
            reusable = False
            if record is not None and record["identity"] == identity and record["stamp"]:
                try:
                    old_stamp = json.loads(record["stamp"])
                    if not isinstance(old_stamp, list) or len(old_stamp) != 6:
                        raise CacheUnavailable()
                    reusable = stamp == old_stamp
                    moved = record["path_key"] != path_key
                    if not reusable and (stamp[2] > old_stamp[2] or
                                         moved and stamp[2:4] == old_stamp[2:4]):
                        reusable = _anchors(stream, old_stamp[2], diagnostics,
                                            json.loads(record["anchors"])) is True
                    if reusable:
                        state = json.loads(record["state"])
                        _check_state(state)
                        local = json.loads(record["diagnostics"])
                        offset = record["offset"]
                        if (not 0 <= offset <= old_stamp[2] or not isinstance(local, dict)
                                or not all(type(n) is int and n >= 0 for n in local.values())):
                            raise CacheUnavailable()
                except (CacheUnavailable, ValueError, TypeError, KeyError, IndexError):
                    # A damaged checkpoint is disposable; rebuild just this file.
                    reusable, state = False, initial_state
            if record is None:
                cursor = self.connection.execute("INSERT INTO files(scope, path_key, identity) VALUES (?, ?, ?)",
                                                 (scope, path_key, identity))
                file_id = cursor.lastrowid
            else:
                file_id = record["id"]
            self.connection.execute("INSERT OR IGNORE INTO seen VALUES (?)", (file_id,))
            if reusable:
                diagnostics["scan_cache_hits"] += 1
                if stamp[2] > old_stamp[2]:
                    diagnostics["scan_cache_incremental_files"] += 1
                for saved in self.connection.execute(
                        "SELECT mode, event FROM events WHERE file_id = ? ORDER BY position, ordinal", (file_id,)):
                    try:
                        yield saved["mode"], json.loads(saved["event"])
                    except (ValueError, TypeError):
                        raise CacheUnavailable() from None
            else:
                diagnostics["scan_cache_misses"] += 1
                if record is not None:
                    diagnostics["scan_cache_resets"] += 1
                self.connection.execute("DELETE FROM events WHERE file_id = ?", (file_id,))
                local, offset = {}, 0
            stream.seek(offset)
            saved_state, saved_local, saved_offset = state, local, offset
            for position, end, complete, raw in lines(stream, stamp[2], kind, diagnostics):
                if not complete:
                    # Valid JSON without a newline still participates in this scan.
                    # Its effects remain provisional until the writer finishes it.
                    saved_state = dict(state)
                    saved_local = dict(local)
                if raw is not None:
                    for ordinal, (mode, event) in enumerate(parse(raw, state, local)):
                        if complete:
                            self._add(file_id, position, ordinal, mode, event)
                        yield mode, event
                if complete:
                    saved_offset = end
            for key, value in local.items():
                diagnostics[key] += value
            changed = _stamp(os.fstat(stream.fileno())) != stamp
            if not changed and (not reusable or stamp != old_stamp or
                                record["path_key"] != path_key or saved_offset != offset):
                _check_state(saved_state)
                anchors = _anchors(stream, stamp[2], diagnostics)
                changed = anchors is None or _stamp(os.fstat(stream.fileno())) != stamp
                if not changed:
                    self.connection.execute("""UPDATE files SET path_key = ?, identity = ?, stamp = ?,
                        offset = ?, anchors = ?, state = ?, diagnostics = ? WHERE id = ?""",
                        (path_key, identity, json.dumps(stamp), saved_offset, json.dumps(anchors),
                         json.dumps(saved_state), json.dumps(saved_local), file_id))
            if changed:
                # Never checkpoint a file that changed while being read. The next
                # worker will rebuild it from a consistent bounded snapshot.
                self.connection.execute("DELETE FROM files WHERE id = ?", (file_id,))
                diagnostics["scan_changed_files"] += 1
