from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from .domain import StatsDataError, UsageDataset, parse_stats_json
from .quota import (
    BankedResetStatus,
    QuotaDataError,
    QuotaSnapshot,
    parse_banked_reset_response,
    parse_quota_json,
    unavailable_banked_resets,
)


SPLITRAIL_FALLBACK = Path.home() / ".local/bin/splitrail"
QUOTA_AXI_FALLBACK = Path.home() / ".npm-global/bin/quota-axi"
CODEX_FALLBACK = Path.home() / ".npm-global/bin/codex"
MAX_OUTPUT_BYTES = 50 * 1024 * 1024
MIN_SPLITRAIL_VERSION = (3, 9, 1)
MAX_CODEX_RPC_OUTPUT_BYTES = 4 * 1024 * 1024
CODEX_APP_SERVER_ARGUMENTS = ("-s", "read-only", "-a", "never", "app-server")
CODEX_RESET_READ_METHODS = ("initialize", "account/rateLimits/read")


class LocalCommandError(RuntimeError):
    pass


class MissingCommandError(LocalCommandError):
    pass


@dataclass(frozen=True)
class CostDiagnostics:
    unknown_models: tuple[str, ...]
    fallback_session_count: int
    other_warning_count: int
    lines: tuple[str, ...]

    @property
    def is_partial(self) -> bool:
        return bool(self.unknown_models)

    @property
    def uses_fallbacks(self) -> bool:
        return self.fallback_session_count > 0

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.unknown_models:
            count = len(self.unknown_models)
            parts.append(f"{count} model{'s' if count != 1 else ''} {'need' if count != 1 else 'needs'} pricing")
        if self.fallback_session_count:
            count = self.fallback_session_count
            parts.append(f"fallback model metadata used for {count} session{'s' if count != 1 else ''}")
        if self.other_warning_count:
            parts.append(f"{self.other_warning_count} other diagnostic line{'s' if self.other_warning_count != 1 else ''}")
        return "; ".join(parts)


@dataclass(frozen=True)
class StatsCommandResult:
    dataset: UsageDataset
    cost_diagnostics: CostDiagnostics
    exit_code: int


@dataclass(frozen=True)
class QuotaCommandResult:
    snapshot: QuotaSnapshot
    exit_code: int
    diagnostic: str | None = None


def run_splitrail(timeout_seconds: float = 45) -> StatsCommandResult:
    executable = _find_executable("SPLITRAIL_BIN", "splitrail", SPLITRAIL_FALLBACK)
    _check_splitrail_version(executable, min(timeout_seconds, 5))
    from .activity import read_collector
    try:
        dataset, exit_code, stderr = read_collector(executable, timeout_seconds)
    except (StatsDataError, OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        raise LocalCommandError("Splitrail usage could not load: " + str(exc)) from exc
    diagnostics = analyze_splitrail_stderr(stderr)
    from .pricing import reprice_dataset
    dataset, resolved, unpriced = reprice_dataset(dataset)
    remaining = (set(diagnostics.unknown_models) - resolved) | unpriced
    lines = tuple(line for line in diagnostics.lines
                  if not any(re.search(r"Unknown model:\s*" + re.escape(name) + r"(?:\.\s|;|$)", line, re.I)
                             for name in resolved))
    if resolved:
        lines += ("Local catalogue supplied prices for: " + ", ".join(sorted(resolved)),)
    lines += tuple(f"Missing price: {name}. Add a rate in Settings → Model pricing."
                   for name in sorted(unpriced - set(diagnostics.unknown_models)))
    diagnostics = CostDiagnostics(tuple(sorted(remaining)), diagnostics.fallback_session_count,
                                  diagnostics.other_warning_count, lines)
    if exit_code:
        extra = f"Splitrail exited with status {exit_code} after producing valid aggregate JSON."
        diagnostics = CostDiagnostics(
            diagnostics.unknown_models,
            diagnostics.fallback_session_count,
            diagnostics.other_warning_count + 1,
            (*diagnostics.lines, extra),
        )
    return StatsCommandResult(dataset, diagnostics, exit_code)


def _check_splitrail_version(executable: str, timeout_seconds: float) -> None:
    completed = _run((executable, "--version"), timeout_seconds, "Splitrail version check")
    match = re.fullmatch(r"splitrail\s+(\d+)\.(\d+)\.(\d+)(?:\+\S+)?\s*", completed.stdout)
    required = ".".join(map(str, MIN_SPLITRAIL_VERSION))
    if completed.returncode or match is None:
        raise LocalCommandError(
            f"Could not verify Splitrail version. Install Splitrail {required} or newer "
            f"for current model pricing (executable: {executable})."
        )
    version = tuple(map(int, match.groups()))
    if version < MIN_SPLITRAIL_VERSION:
        installed = ".".join(map(str, version))
        raise LocalCommandError(
            f"Splitrail {installed} has outdated model pricing and can report GPT-6 Astra at $0. "
            f"Upgrade to Splitrail {required} or newer (executable: {executable}), then refresh."
        )


def run_quota_axi(timeout_seconds: float = 30) -> QuotaCommandResult:
    executable = _find_executable("QUOTA_AXI_BIN", "quota-axi", QUOTA_AXI_FALLBACK)
    completed = _run(
        (executable, "--provider", "codex", "--full", "--json"),
        timeout_seconds,
        "quota-axi",
    )
    if len(completed.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise LocalCommandError("quota-axi output exceeded the 50 MB safety limit")
    try:
        snapshot = parse_quota_json(completed.stdout)
    except QuotaDataError as exc:
        if completed.returncode:
            raise LocalCommandError(f"quota-axi exited with status {completed.returncode}") from exc
        raise LocalCommandError(str(exc)) from exc
    banked_resets = run_codex_banked_resets()
    snapshot = replace(snapshot, banked_resets=banked_resets)
    diagnostics: list[str] = []
    if completed.returncode:
        diagnostics.append(f"quota-axi exited with status {completed.returncode} after producing valid JSON")
    elif completed.stderr.strip():
        diagnostics.append("quota-axi returned a diagnostic on stderr; quota JSON still loaded")
    if banked_resets.status != "fresh":
        diagnostics.append(banked_resets.reason or "Banked-reset read was unavailable")
    return QuotaCommandResult(snapshot, completed.returncode, "; ".join(diagnostics) or None)


def run_codex_banked_resets(timeout_seconds: float = 20) -> BankedResetStatus:
    try:
        executable = _find_executable("CODEX_BIN", "codex", CODEX_FALLBACK)
        response = _run_codex_reset_credit_read(executable, timeout_seconds)
    except MissingCommandError:
        return unavailable_banked_resets("Codex CLI is not installed")
    except LocalCommandError:
        return unavailable_banked_resets("Codex read-only reset-credit request failed")
    try:
        return parse_banked_reset_response(response, datetime.now(timezone.utc))
    except QuotaDataError:
        return unavailable_banked_resets("Codex returned malformed reset-credit data")


def _run_codex_reset_credit_read(executable: str, timeout_seconds: float) -> dict[str, Any]:
    arguments = (executable, *CODEX_APP_SERVER_ARGUMENTS)
    try:
        process = subprocess.Popen(
            arguments,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
    except FileNotFoundError as exc:
        raise MissingCommandError("Codex executable was not found") from exc
    except OSError as exc:
        raise LocalCommandError(f"Could not start Codex app-server: {exc}") from exc

    assert process.stdin is not None
    assert process.stdout is not None
    responses: queue.Queue[dict[str, Any] | Exception | None] = queue.Queue()
    reader = threading.Thread(
        target=_read_codex_rpc_output,
        args=(process.stdout, responses),
        name="codex-reset-credit-reader",
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + timeout_seconds
    pending: dict[int, dict[str, Any]] = {}
    try:
        _send_codex_rpc(
            process.stdin,
            1,
            "initialize",
            {"clientInfo": {"name": "splitrail-desktop", "version": "1"}},
        )
        initialized = _wait_for_codex_rpc(1, responses, pending, deadline)
        if "error" in initialized or not isinstance(initialized.get("result"), dict):
            raise LocalCommandError("Installed Codex app-server did not accept protocol initialization")
        _send_codex_rpc(process.stdin, 2, "account/rateLimits/read", {})
        response = _wait_for_codex_rpc(2, responses, pending, deadline)
        if "error" in response or not isinstance(response.get("result"), dict):
            raise LocalCommandError("Codex reset-credit read did not return a result")
        return response
    except (BrokenPipeError, OSError) as exc:
        raise LocalCommandError("Codex app-server closed before the reset-credit read completed") from exc
    finally:
        _finish_codex_process(process)


def _read_codex_rpc_output(
    stream: IO[str],
    responses: queue.Queue[dict[str, Any] | Exception | None],
) -> None:
    total_bytes = 0
    try:
        while True:
            line = stream.readline(MAX_CODEX_RPC_OUTPUT_BYTES + 1)
            if not line:
                break
            total_bytes += len(line.encode("utf-8"))
            if total_bytes > MAX_CODEX_RPC_OUTPUT_BYTES:
                responses.put(LocalCommandError("Codex app-server output exceeded the 4 MB safety limit"))
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and isinstance(message.get("id"), int):
                responses.put(message)
    except (OSError, UnicodeError) as exc:
        responses.put(LocalCommandError(f"Could not read Codex app-server output: {exc}"))
    finally:
        responses.put(None)


def _send_codex_rpc(stream: IO[str], request_id: int, method: str, params: dict[str, Any]) -> None:
    if method not in CODEX_RESET_READ_METHODS:
        raise LocalCommandError("Refusing non-allowlisted Codex app-server method")
    request = {"id": request_id, "method": method, "params": params}
    stream.write(json.dumps(request, separators=(",", ":")) + "\n")
    stream.flush()


def _wait_for_codex_rpc(
    request_id: int,
    responses: queue.Queue[dict[str, Any] | Exception | None],
    pending: dict[int, dict[str, Any]],
    deadline: float,
) -> dict[str, Any]:
    if request_id in pending:
        return pending.pop(request_id)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocalCommandError("Codex read-only reset-credit request timed out")
        try:
            message = responses.get(timeout=remaining)
        except queue.Empty as exc:
            raise LocalCommandError("Codex read-only reset-credit request timed out") from exc
        if message is None:
            raise LocalCommandError("Codex app-server exited before replying")
        if isinstance(message, Exception):
            raise message
        message_id = message.get("id")
        if message_id == request_id:
            return message
        if isinstance(message_id, int):
            pending[message_id] = message


def _finish_codex_process(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def analyze_splitrail_stderr(stderr: str) -> CostDiagnostics:
    unknown_models: set[str] = set()
    fallback_count = 0
    other = 0
    safe_lines: list[str] = []
    home = str(Path.home())
    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        safe_lines.append(line.replace(home, "~"))
        unknown = re.search(r"Unknown model:\s*(.+?)(?:\.\s+Defaulting\b|;|$)", line, re.IGNORECASE)
        if unknown:
            unknown_models.add(unknown.group(1).strip())
        elif "missing model metadata" in line.lower() and "fallback model" in line.lower():
            fallback_count += 1
        else:
            other += 1
    return CostDiagnostics(tuple(sorted(unknown_models)), fallback_count, other, tuple(safe_lines[:100]))


def _find_executable(env_name: str, command: str, fallback: Path) -> str:
    configured = os.environ.get(env_name)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise MissingCommandError(f"{command} is not executable at {path}")
    discovered = shutil.which(command)
    if discovered:
        return discovered
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise MissingCommandError(f"{command} was not found. Expected an executable named '{command}'.")


def _run(arguments: tuple[str, ...], timeout_seconds: float, label: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MissingCommandError(f"{label} executable was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise LocalCommandError(f"{label} did not finish within {timeout_seconds:g} seconds") from exc
    except OSError as exc:
        raise LocalCommandError(f"Could not start {label}: {exc}") from exc


def _safe_failure_detail(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "No diagnostic was provided."
    first = lines[0].replace(str(Path.home()), "~")
    return first[:300]
