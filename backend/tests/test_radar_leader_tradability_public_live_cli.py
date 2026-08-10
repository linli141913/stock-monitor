import io
import json
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market_calendar import CalendarDay
from run_public_tradability_poc import (
    _run_bounded_worker,
    _worker_main,
    main,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TRADING_NOW = datetime(2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ)


def calendar_day(kind):
    return CalendarDay(
        kind=kind,
        source_url=(
            "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
        ),
        checked_at="2026-08-03T09:59:59+08:00",
    )


def completed_evidence():
    return {
        "contractId": "radar-leader-tradability-public-live-poc-v1",
        "executionStatus": "completed",
        "realPocStatus": "completed",
        "reason": None,
        "resolutionStatus": "partial",
        "sourceStatuses": {"tencentQuote": "completed"},
        "sourceFailures": [],
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def completed_worker_target(symbols, as_of, output_queue):
    output_queue.put(json.dumps(completed_evidence()))


def sleeping_worker_target(symbols, as_of, output_queue):
    time.sleep(1)


def noisy_completed_worker_target(symbols, as_of, output_queue):
    print("public-live-worker-stdout-secret", flush=True)
    print(
        "public-live-worker-stderr-secret",
        file=sys.stderr,
        flush=True,
    )
    output_queue.put(json.dumps(completed_evidence()))


class PublicLivePocCliTests(unittest.TestCase):
    def run_main(self, argv, **overrides):
        output = []
        values = {
            "now_provider": lambda: TRADING_NOW,
            "day_kind_resolver": lambda market, day: calendar_day("full"),
            "live_runner": lambda symbols, as_of: completed_evidence(),
            "output_writer": output.append,
        }
        values.update(overrides)
        exit_code = main(argv, **values)
        self.assertEqual(len(output), 1)
        return exit_code, json.loads(output[0]), output[0]

    def test_confirmation_is_required_before_calendar_or_live_runner(self):
        calls = []

        exit_code, evidence, _ = self.run_main(
            ["--symbols", "000725"],
            day_kind_resolver=lambda market, day: calls.append("calendar"),
            live_runner=lambda symbols, as_of: calls.append("live"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(evidence["executionStatus"], "not_run")
        self.assertEqual(
            evidence["reason"],
            "public_live_confirmation_missing",
        )
        self.assertEqual(calls, [])

    def test_invalid_symbols_are_rejected_before_calendar(self):
        calls = []

        exit_code, evidence, _ = self.run_main(
            [
                "--symbols", "000725", "000725",
                "--confirm-live-poc",
            ],
            day_kind_resolver=lambda market, day: calls.append("calendar"),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(evidence["executionStatus"], "blocked")
        self.assertEqual(evidence["reason"], "public_live_query_invalid")
        self.assertEqual(calls, [])

    def test_closed_calendar_never_calls_live_runner(self):
        calls = []

        exit_code, evidence, _ = self.run_main(
            ["--symbols", "000725", "--confirm-live-poc"],
            day_kind_resolver=lambda market, day: calendar_day("closed"),
            live_runner=lambda symbols, as_of: calls.append("live"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            evidence["reason"],
            "public_live_calendar_closed",
        )
        self.assertEqual(calls, [])

    def test_lunch_break_never_calls_live_runner(self):
        calls = []
        noon = datetime(2026, 8, 3, 12, 0, tzinfo=SHANGHAI_TZ)

        exit_code, evidence, _ = self.run_main(
            ["--symbols", "000725", "--confirm-live-poc"],
            now_provider=lambda: noon,
            live_runner=lambda symbols, as_of: calls.append("live"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            evidence["reason"],
            "public_live_market_lunch_break",
        )
        self.assertEqual(calls, [])

    def test_valid_window_emits_only_redacted_summary(self):
        captured = []

        exit_code, evidence, raw = self.run_main(
            [
                "--symbols", "000725", "600000",
                "--confirm-live-poc",
            ],
            live_runner=lambda symbols, as_of: (
                captured.append((symbols, as_of)) or completed_evidence()
            ),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(evidence["executionStatus"], "completed")
        self.assertEqual(captured, [(("000725", "600000"), TRADING_NOW)])
        self.assertNotIn("records", raw)
        self.assertNotIn("response", raw.lower())

    def test_live_runner_failure_is_redacted(self):
        def failed_runner(symbols, as_of):
            raise RuntimeError("https://upstream/?token=secret-value")

        exit_code, evidence, raw = self.run_main(
            ["--symbols", "000725", "--confirm-live-poc"],
            live_runner=failed_runner,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            evidence["reason"],
            "public_live_execution_failed",
        )
        self.assertNotIn("secret-value", raw)

    def test_bounded_worker_timeout_returns_stable_failure(self):
        existing_children = {
            process.pid for process in multiprocessing.active_children()
        }
        started = time.monotonic()
        result = _run_bounded_worker(
            ("000725",),
            TRADING_NOW,
            worker_target=sleeping_worker_target,
            timeout_seconds=0.2,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(result["executionStatus"], "blocked")
        self.assertEqual(result["realPocStatus"], "failed")
        self.assertEqual(result["reason"], "public_live_worker_timeout")
        self.assertLess(elapsed, 1)
        remaining_children = {
            process.pid for process in multiprocessing.active_children()
        }
        self.assertEqual(
            remaining_children - existing_children,
            set(),
        )

    def test_bounded_worker_returns_sanitized_child_evidence(self):
        result = _run_bounded_worker(
            ("000725",),
            TRADING_NOW,
            worker_target=completed_worker_target,
        )

        self.assertEqual(result["executionStatus"], "completed")
        self.assertEqual(result["realPocStatus"], "completed")
        self.assertEqual(result["resolutionStatus"], "partial")
        self.assertEqual(
            result["sourceStatuses"],
            {"tencentQuote": "completed"},
        )
        self.assertFalse(result["formalUsable"])

    def test_worker_stdout_and_stderr_cannot_bypass_json_summary(self):
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)
        try:
            with tempfile.TemporaryFile() as capture:
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(capture.fileno(), 1)
                os.dup2(capture.fileno(), 2)
                result = _run_bounded_worker(
                    ("000725",),
                    TRADING_NOW,
                    worker_target=noisy_completed_worker_target,
                )
                sys.stdout.flush()
                sys.stderr.flush()
                capture.seek(0)
                leaked_output = capture.read().decode(
                    "utf-8",
                    errors="replace",
                )
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)

        self.assertEqual(result["executionStatus"], "completed")
        self.assertNotIn("public-live-worker", leaked_output)

    def test_direct_worker_invocation_is_rejected_before_collection(self):
        output = io.StringIO()

        forged_token = "caller-controlled-token"
        with (
            patch.dict(
                os.environ,
                {"RADAR_PUBLIC_LIVE_WORKER_TOKEN": forged_token},
                clear=True,
            ),
            patch(
                "run_public_tradability_poc.run_public_live_poc",
                side_effect=AssertionError("collector must not run"),
            ),
            redirect_stdout(output),
        ):
            exit_code = _worker_main([
                "--worker",
                "--worker-token",
                forged_token,
                "--as-of",
                TRADING_NOW.isoformat(),
                "--symbols",
                "000725",
            ])

        evidence = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(evidence["executionStatus"], "not_run")
        self.assertEqual(
            evidence["reason"],
            "public_live_worker_unauthorized",
        )

    def test_collection_crossing_into_lunch_break_is_rejected(self):
        times = iter((
            TRADING_NOW,
            datetime(2026, 8, 3, 12, 0, tzinfo=SHANGHAI_TZ),
        ))

        exit_code, evidence, _ = self.run_main(
            ["--symbols", "000725", "--confirm-live-poc"],
            now_provider=lambda: next(times),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(evidence["executionStatus"], "blocked")
        self.assertEqual(
            evidence["reason"],
            "public_live_market_window_closed_during_execution",
        )


if __name__ == "__main__":
    unittest.main()
