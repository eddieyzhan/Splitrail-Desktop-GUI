"""Small, local-only inventory of native Codex engines, never conversation text."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import psutil


@dataclass(frozen=True)
class CodexProcess:
    pid: int
    started: float
    executable: str
    directory: str
    kind: str
    status: str

    @property
    def key(self) -> str:
        return f'{self.pid}:{self.started:.6f}'


def _kind(arguments: list[str]) -> str:
    # Only inspect the command prefix. A prompt containing "app-server" is not
    # an app-server. Never retain or display arguments (which can contain secrets).
    takes_value = {'-c', '--config', '-m', '--model', '-p', '--profile', '-s',
                   '--sandbox', '-a', '--ask-for-approval', '-C', '--cd',
                   '--add-dir', '--enable', '--disable', '-i', '--image'}
    args = iter(arguments[1:])
    for arg in args:
        if arg in takes_value:
            next(args, None)
        elif arg.startswith('-') and arg != '--':
            continue
        else:
            return {'app-server': 'App server · multiple chats', 'exec': 'CLI task',
                    'e': 'CLI task', 'mcp-server': 'MCP server'}.get(arg, 'CLI session')
    return 'CLI session'


def _describe(process: psutil.Process, owner: str) -> CodexProcess | None:
    if process.pid == os.getpid() or process.username() != owner:
        return None
    executable = process.exe()
    if Path(executable).name.lower() not in ('codex', 'codex.exe'):
        return None
    # Splitrail's own short-lived, read-only quota helpers are never targets.
    if any(parent.pid == os.getpid() for parent in process.parents()):
        return None
    with process.oneshot():
        kind = _kind(process.cmdline())
        try:
            directory = process.cwd()
        except (psutil.AccessDenied, FileNotFoundError):
            directory = 'Directory unavailable'
        return CodexProcess(process.pid, process.create_time(), executable,
                            directory, kind, process.status())


def discover_codex_processes() -> tuple[CodexProcess, ...]:
    owner = psutil.Process().username()
    found = []
    # Fetch names first; expensive attributes are read for Codex candidates only.
    for candidate in psutil.process_iter(['name']):
        if (candidate.info['name'] or '').lower() not in ('codex', 'codex.exe'):
            continue
        try:
            # A new handle avoids stale attributes from process_iter's PID cache.
            item = _describe(psutil.Process(candidate.pid), owner)
            if item is not None:
                found.append(item)
        except (psutil.Error, OSError):
            continue
    return tuple(sorted(found, key=lambda item: (item.started, item.pid)))


@dataclass(frozen=True)
class StopResult:
    stopped: tuple[int, ...]
    failures: tuple[str, ...]


def stop_codex_processes(targets: tuple[CodexProcess, ...], cancel: Event) -> StopResult:
    """Terminate only verified engines. Do not kill arbitrary shell/tool children."""
    stopped, failures, pending = [], [], []
    owner = psutil.Process().username()
    for target in targets:
        if cancel.is_set():
            break
        try:
            process = psutil.Process(target.pid)
            current = _describe(process, owner)
            if (current is None or current.started != target.started or
                    current.executable != target.executable):
                failures.append(f'PID {target.pid}: identity changed; left alone')
                continue
            if cancel.is_set():
                break
            process.terminate()
            pending.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            failures.append(f'PID {target.pid}: could not stop; check permissions')
    if pending:
        gone, alive = psutil.wait_procs(pending, timeout=1)
        stopped.extend(p.pid for p in gone)
        failures.extend(f'PID {p.pid}: still open after stop request; will retry' for p in alive)
    return StopResult(tuple(stopped), tuple(failures))
