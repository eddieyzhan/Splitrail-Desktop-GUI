from __future__ import annotations

import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from splitrail_desktop.quota import unavailable_banked_resets
from splitrail_desktop.runner import (
    CODEX_APP_SERVER_ARGUMENTS,
    CODEX_RESET_READ_METHODS,
    LocalCommandError,
    _check_splitrail_version,
    _run_codex_reset_credit_read,
    analyze_splitrail_stderr,
    run_codex_banked_resets,
    run_quota_axi,
    run_splitrail,
)


FIXTURES = Path(__file__).parent / "fixtures"


class SafeCommandRunnerTests(unittest.TestCase):
    def test_application_source_never_contains_reset_consuming_rpc(self) -> None:
        source_root = Path(__file__).parent.parent / "src" / "splitrail_desktop"
        application_source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(source_root.glob("*.py"))
        )

        self.assertNotIn("account/rateLimitResetCredit/consume", application_source)

    @patch("splitrail_desktop.runner._find_executable", return_value="/safe/splitrail")
    @patch("splitrail_desktop.runner.subprocess.run")
    def test_splitrail_uses_aggregate_command_without_shell_or_messages(self, run_mock, _find_mock) -> None:
        run_mock.side_effect = [
            subprocess.CompletedProcess([], 0, "splitrail 3.9.1\n", ""),
            subprocess.CompletedProcess(
                [], 0, (FIXTURES / "stats_valid.json").read_text(encoding="utf-8"),
                "WARNING: Unknown model: future-model. Defaulting to $0.\n",
            ),
        ]

        result = run_splitrail()

        arguments = run_mock.call_args.args[0]
        options = run_mock.call_args.kwargs
        self.assertEqual(run_mock.call_args_list[0].args[0], ("/safe/splitrail", "--version"))
        self.assertEqual(arguments, ("/safe/splitrail", "stats"))
        self.assertNotIn("--include-messages", arguments)
        self.assertFalse(options["shell"])
        self.assertEqual(result.cost_diagnostics.unknown_models, ("future-model",))

    @patch("splitrail_desktop.runner._find_executable", return_value="/safe/quota-axi")
    @patch("splitrail_desktop.runner.subprocess.run")
    def test_quota_uses_full_codex_machine_interface_without_shell(self, run_mock, _find_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(FIXTURES / "quota_fresh.json").read_text(encoding="utf-8"),
            stderr="",
        )

        with patch(
            "splitrail_desktop.runner.run_codex_banked_resets",
            return_value=unavailable_banked_resets("fixture unavailable"),
        ) as banked_mock:
            result = run_quota_axi()

        arguments = run_mock.call_args.args[0]
        options = run_mock.call_args.kwargs
        self.assertEqual(arguments, ("/safe/quota-axi", "--provider", "codex", "--full", "--json"))
        self.assertFalse(options["shell"])
        self.assertEqual(result.snapshot.status, "fresh")
        banked_mock.assert_called_once_with()

    @patch("splitrail_desktop.runner.subprocess.Popen")
    def test_codex_fallback_sends_only_allowlisted_read_methods(self, popen_mock) -> None:
        class RecordingInput(io.StringIO):
            def close(self) -> None:
                self.close_requested = True

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = RecordingInput()
                self.stdout = io.StringIO(
                    json.dumps({"id": 1, "result": {"userAgent": "codex-cli/fixture"}})
                    + "\n"
                    + json.dumps(
                        json.loads((FIXTURES / "codex_rate_limits_one.json").read_text(encoding="utf-8"))
                    )
                    + "\n"
                )
                self.returncode = None

            def wait(self, timeout: float | None = None) -> int:
                self.returncode = 0
                return 0

            def terminate(self) -> None:
                raise AssertionError("fixture process should exit after stdin closes")

            def kill(self) -> None:
                raise AssertionError("fixture process should not require killing")

        process = FakeProcess()
        popen_mock.return_value = process

        response = _run_codex_reset_credit_read("/safe/codex", 2)

        arguments = popen_mock.call_args.args[0]
        options = popen_mock.call_args.kwargs
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(arguments, ("/safe/codex", *CODEX_APP_SERVER_ARGUMENTS))
        self.assertEqual(CODEX_APP_SERVER_ARGUMENTS, ("-s", "read-only", "-a", "never", "app-server"))
        self.assertFalse(options["shell"])
        self.assertEqual([request["method"] for request in requests], list(CODEX_RESET_READ_METHODS))
        self.assertEqual(CODEX_RESET_READ_METHODS, ("initialize", "account/rateLimits/read"))
        self.assertNotIn("consume", process.stdin.getvalue().lower())
        self.assertEqual(response["result"]["rateLimitResetCredits"]["availableCount"], 1)

    @patch("splitrail_desktop.runner._run_codex_reset_credit_read")
    @patch("splitrail_desktop.runner._find_executable", return_value="/safe/codex")
    def test_malformed_codex_reset_data_becomes_honest_unavailable_state(
        self,
        _find_mock,
        read_mock,
    ) -> None:
        read_mock.return_value = json.loads(
            (FIXTURES / "codex_rate_limits_malformed.json").read_text(encoding="utf-8")
        )

        status = run_codex_banked_resets()

        self.assertEqual(status.status, "unavailable")
        self.assertIsNone(status.count)
        self.assertEqual(status.reason, "Codex returned malformed reset-credit data")

    @patch("splitrail_desktop.runner._run")
    def test_pricing_engine_version_check(self, run_mock) -> None:
        for version in ("3.9.1", "3.10.0", "4.0.0", "3.9.1+build"):
            with self.subTest(version=version):
                run_mock.return_value = subprocess.CompletedProcess([], 0, f"splitrail {version}\n", "")
                _check_splitrail_version("/safe/splitrail", 5)
        for version in ("3.7.0", "3.9.0", "3.9.1-rc.1", "unknown", ""):
            with self.subTest(version=version):
                run_mock.return_value = subprocess.CompletedProcess([], 0, f"splitrail {version}\n", "")
                with self.assertRaisesRegex(LocalCommandError, "3.9.1"):
                    _check_splitrail_version("/safe/splitrail", 5)
        run_mock.return_value = subprocess.CompletedProcess([], 1, "splitrail 3.9.1", "failed")
        with self.assertRaises(LocalCommandError):
            _check_splitrail_version("/safe/splitrail", 5)

    @patch("splitrail_desktop.runner._find_executable", return_value="/safe/splitrail")
    @patch("splitrail_desktop.runner._run")
    def test_outdated_engine_does_not_load_zero_priced_stats(self, run_mock, _find_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "splitrail 3.7.0\n", "")
        with self.assertRaisesRegex(LocalCommandError, r"GPT-6 Astra at \$0"):
            run_splitrail()
        run_mock.assert_called_once_with(("/safe/splitrail", "--version"), 5, "Splitrail version check")

    def test_unknown_model_diagnostics_preserve_decimal_versions(self) -> None:
        diagnostics = analyze_splitrail_stderr(
            "WARNING: Unknown model: gpt-5.3-codex-spark. Defaulting to $0.\n"
            "WARNING: Unknown model: gpt-5.6-future. Defaulting to $0.\n"
            "WARNING: Unknown model: gpt-5.6-future. Defaulting to $0.\n"
            "WARNING: Unknown model: gemini-3.8-flash; no pricing\n"
        )
        self.assertEqual(diagnostics.unknown_models, (
            "gemini-3.8-flash", "gpt-5.3-codex-spark", "gpt-5.6-future",
        ))

    def test_warning_diagnostics_distinguish_partial_and_fallback_costs(self) -> None:
        diagnostics = analyze_splitrail_stderr(
            "WARNING: Unknown model: model-z. Defaulting to $0.\n"
            "WARNING: session /tmp/example missing model metadata; using fallback model model-y for cost estimation.\n"
            "NOTICE: harmless detail\n"
        )

        self.assertTrue(diagnostics.is_partial)
        self.assertTrue(diagnostics.uses_fallbacks)
        self.assertEqual(diagnostics.fallback_session_count, 1)
        self.assertEqual(diagnostics.other_warning_count, 1)


if __name__ == "__main__":
    unittest.main()
