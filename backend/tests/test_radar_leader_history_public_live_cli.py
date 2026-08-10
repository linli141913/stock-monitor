import json
import multiprocessing
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import run_public_history_poc as public_history_cli
from run_public_history_poc import (
    _history_exception_code,
    _history_series_issue_code,
    _query_symbol,
    _run_bounded_worker,
    main,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 8, 20, 0, tzinfo=SHANGHAI_TZ)


def partial_evidence():
    return {
        "contractId": "radar-leader-public-history-poc-v1",
        "executionStatus": "completed",
        "realPocStatus": "partial",
        "resolutionStatus": "partial",
        "reason": "industry_membership_forward_window_incomplete",
        "reasons": ["industry_membership_forward_window_incomplete"],
        "memberCount": 12,
        "expectedSeriesCount": 13,
        "completeSeriesCount": 13,
        "seriesCoverage": 1.0,
        "historyInputReady": False,
        "sourceStatuses": {
            "industryClassification": "completed",
            "tencentHistory": "completed",
        },
        "sourceFailures": [],
        "historyEndDate": "2026-08-07",
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def completed_worker_target(symbol, as_of, output_queue):
    output_queue.put(json.dumps(partial_evidence()))


def sleeping_worker_target(symbol, as_of, output_queue):
    time.sleep(1)


class PublicHistoryPocCliTests(unittest.TestCase):
    def run_main(self, argv, **overrides):
        output = []
        values = {
            "now_provider": lambda: NOW,
            "live_runner": lambda symbol, as_of: partial_evidence(),
            "output_writer": output.append,
        }
        values.update(overrides)
        exit_code = main(argv, **values)
        self.assertEqual(len(output), 1)
        return exit_code, json.loads(output[0]), output[0]

    def test_confirmation_is_required_before_live_collection(self):
        calls = []

        exit_code, evidence, _ = self.run_main(
            ["--symbol", "600519"],
            live_runner=lambda symbol, as_of: calls.append(symbol),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(evidence["realPocStatus"], "not_run")
        self.assertEqual(
            evidence["reason"],
            "public_history_confirmation_missing",
        )
        self.assertEqual(calls, [])

    def test_invalid_symbol_is_rejected_before_live_collection(self):
        calls = []

        exit_code, evidence, _ = self.run_main(
            ["--symbol", "00700", "--confirm-live-poc"],
            live_runner=lambda symbol, as_of: calls.append(symbol),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(evidence["realPocStatus"], "not_run")
        self.assertEqual(evidence["reason"], "public_history_symbol_invalid")
        self.assertEqual(calls, [])

    def test_beijing_exchange_symbol_is_rejected_outside_project_scope(self):
        with self.assertRaisesRegex(
            ValueError,
            "history_symbol_exchange_unknown",
        ):
            _query_symbol("920023")

    def test_industry_members_only_keep_shanghai_and_shenzhen(self):
        self.assertTrue(hasattr(
            public_history_cli,
            "_select_sh_sz_industry_members",
        ))
        accepted = (
            SimpleNamespace(
                security_identity="600519",
                division_code="15",
            ),
            SimpleNamespace(
                security_identity="000858",
                division_code="15",
            ),
            SimpleNamespace(
                security_identity="920023",
                division_code="15",
            ),
            SimpleNamespace(
                security_identity="600000",
                division_code="66",
            ),
        )
        master = (
            SimpleNamespace(symbol="600519", exchange="sse"),
            SimpleNamespace(symbol="000858", exchange="szse"),
            SimpleNamespace(symbol="920023", exchange="bse"),
            SimpleNamespace(symbol="600000", exchange="sse"),
        )

        members, excluded = (
            public_history_cli._select_sh_sz_industry_members(
                accepted,
                master,
                "15",
            )
        )

        self.assertEqual(members, ("000858", "600519"))
        self.assertEqual(excluded, 1)

    def test_history_source_failures_keep_stable_reason_types(self):
        self.assertEqual(
            _history_exception_code(ValueError(
                "tencent_history_symbol_missing"
            )),
            "symbol_missing",
        )
        self.assertEqual(
            _history_exception_code(RuntimeError("secret response")),
            "request_failed",
        )
        self.assertEqual(
            _history_series_issue_code(
                SimpleNamespace(points=()),
                (datetime(2026, 8, 7).date(),),
            ),
            "dates_incomplete",
        )

    def test_partial_live_result_is_redacted_and_keeps_gates_closed(self):
        captured = []

        exit_code, evidence, raw = self.run_main(
            ["--symbol", "600519", "--confirm-live-poc"],
            live_runner=lambda symbol, as_of: (
                captured.append((symbol, as_of)) or partial_evidence()
            ),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(captured, [("600519", NOW)])
        self.assertEqual(evidence["realPocStatus"], "partial")
        self.assertFalse(evidence["formalUsable"])
        self.assertNotIn("records", raw.lower())
        self.assertNotIn("response", raw.lower())

    def test_live_failure_does_not_leak_exception_details(self):
        def failed_runner(symbol, as_of):
            raise RuntimeError("https://upstream/?token=secret-value")

        exit_code, evidence, raw = self.run_main(
            ["--symbol", "600519", "--confirm-live-poc"],
            live_runner=failed_runner,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(evidence["realPocStatus"], "failed")
        self.assertEqual(evidence["reason"], "public_history_execution_failed")
        self.assertNotIn("secret-value", raw)

    def test_bounded_worker_timeout_has_no_child_process_left(self):
        existing = {item.pid for item in multiprocessing.active_children()}

        result = _run_bounded_worker(
            "600519",
            NOW,
            worker_target=sleeping_worker_target,
            timeout_seconds=0.2,
        )

        self.assertEqual(result["realPocStatus"], "failed")
        self.assertEqual(result["reason"], "public_history_worker_timeout")
        remaining = {item.pid for item in multiprocessing.active_children()}
        self.assertEqual(remaining - existing, set())

    def test_bounded_worker_accepts_only_sanitized_summary(self):
        result = _run_bounded_worker(
            "600519",
            NOW,
            worker_target=completed_worker_target,
        )

        self.assertEqual(result["realPocStatus"], "partial")
        self.assertEqual(result["seriesCoverage"], 1.0)
        self.assertFalse(result["formalUsable"])


if __name__ == "__main__":
    unittest.main()
