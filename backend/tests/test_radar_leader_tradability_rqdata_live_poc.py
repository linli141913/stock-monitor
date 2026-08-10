import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from market_calendar import CalendarDay
from radar.sources.leader_tradability_rqdata_poc import (
    RqdataPocStatus,
    RqdataPocTransportError,
    RqdataTradabilityPocReport,
)
from run_rqdata_tradability_poc import main


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TRADING_NOW = datetime(2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ)


def calendar_day(kind):
    return CalendarDay(
        kind=kind,
        source_url="https://www.sse.com.cn/calendar",
        checked_at="2026-08-03T09:59:59+08:00",
    )


def report(status):
    return RqdataTradabilityPocReport(
        status=status,
        real_poc_status=(
            "completed"
            if status in (
                RqdataPocStatus.FIELD_CANDIDATE,
                RqdataPocStatus.PARTIAL,
            )
            else "failed"
        ),
        as_of=TRADING_NOW,
        trading_date=TRADING_NOW.date(),
        expected_count=2,
        reasons=("fixture-result",),
    )


class RqdataLivePocCliTests(unittest.TestCase):
    def run_main(self, argv, **overrides):
        output = []
        values = {
            "now_provider": lambda: TRADING_NOW,
            "day_kind_resolver": lambda market, day: calendar_day("full"),
            "token_reader": lambda prompt: "live-secret-token",
            "output_writer": output.append,
        }
        values.update(overrides)
        exit_code = main(argv, **values)
        self.assertEqual(len(output), 1)
        return exit_code, json.loads(output[0]), output[0]

    def test_confirmation_is_required_before_calendar_or_token(self):
        calls = []

        exit_code, evidence, raw_output = self.run_main(
            ["--symbols", "000725.XSHE"],
            day_kind_resolver=lambda market, day: calls.append("calendar"),
            token_reader=lambda prompt: calls.append("token"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(evidence["executionStatus"], "not_run")
        self.assertEqual(
            evidence["reason"],
            "rqdata_live_confirmation_missing",
        )
        self.assertEqual(calls, [])
        self.assertNotIn("token", raw_output.lower())

    def test_closed_or_unknown_calendar_never_reads_token(self):
        for kind in ("closed", "unknown"):
            token_calls = []
            with self.subTest(kind=kind):
                exit_code, evidence, _ = self.run_main(
                    [
                        "--symbols",
                        "000725.XSHE",
                        "--confirm-live-poc",
                    ],
                    day_kind_resolver=(
                        lambda market, day, value=kind: calendar_day(value)
                    ),
                    token_reader=lambda prompt: token_calls.append(prompt),
                )
                self.assertEqual(exit_code, 2)
                self.assertEqual(evidence["executionStatus"], "not_run")
                self.assertEqual(
                    evidence["reason"],
                    f"rqdata_live_calendar_{kind}",
                )
        self.assertEqual(token_calls, [])

    def test_non_trading_window_never_reads_token(self):
        token_calls = []
        noon = datetime(2026, 8, 3, 12, 0, tzinfo=SHANGHAI_TZ)

        exit_code, evidence, _ = self.run_main(
            ["--symbols", "000725.XSHE", "--confirm-live-poc"],
            now_provider=lambda: noon,
            token_reader=lambda prompt: token_calls.append(prompt),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            evidence["reason"],
            "rqdata_live_market_lunch_break",
        )
        self.assertEqual(token_calls, [])

    def test_invalid_symbols_are_rejected_before_token(self):
        token_calls = []
        exit_code, evidence, _ = self.run_main(
            [
                "--symbols",
                "000725.XSHE",
                "000725.XSHE",
                "--confirm-live-poc",
            ],
            token_reader=lambda prompt: token_calls.append(prompt),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(evidence["executionStatus"], "blocked")
        self.assertIn(
            "rqdata_query_symbol_duplicated",
            evidence["report"]["reasons"],
        )
        self.assertEqual(token_calls, [])

    def test_valid_live_run_uses_hidden_token_and_emits_only_evidence(self):
        captured = []
        transport = object()

        def transport_builder(token):
            captured.append(("token", token))
            return transport

        def poc_runner(query, *, fetched_at, transport):
            captured.append(("run", query, fetched_at, transport))
            return report(RqdataPocStatus.FIELD_CANDIDATE)

        exit_code, evidence, raw_output = self.run_main(
            [
                "--symbols",
                "000725.XSHE",
                "600000.XSHG",
                "--confirm-live-poc",
            ],
            transport_builder=transport_builder,
            poc_runner=poc_runner,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(evidence["executionStatus"], "completed")
        self.assertEqual(evidence["report"]["status"], "field_candidate")
        self.assertEqual(captured[0], ("token", "live-secret-token"))
        _, query, fetched_at, used_transport = captured[1]
        self.assertEqual(
            query.expected_order_book_ids,
            ("000725.XSHE", "600000.XSHG"),
        )
        self.assertTrue(query.include_dynamic_snapshot)
        self.assertEqual(query.as_of, TRADING_NOW)
        self.assertEqual(fetched_at, TRADING_NOW)
        self.assertIs(used_transport, transport)
        self.assertNotIn("live-secret-token", raw_output)
        self.assertNotIn("records", evidence["report"])

    def test_partial_result_has_distinct_exit_and_stays_nonformal(self):
        exit_code, evidence, _ = self.run_main(
            ["--symbols", "000725.XSHE", "--confirm-live-poc"],
            transport_builder=lambda token: object(),
            poc_runner=lambda query, **kwargs: report(
                RqdataPocStatus.PARTIAL
            ),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(evidence["executionStatus"], "completed")
        self.assertEqual(evidence["report"]["status"], "partial")
        self.assertFalse(evidence["report"]["gate"]["formalUsable"])

    def test_token_and_transport_failures_are_redacted(self):
        def failed_token_reader(prompt):
            raise RuntimeError("upstream-secret-token")

        token_exit, token_evidence, token_output = self.run_main(
            ["--symbols", "000725.XSHE", "--confirm-live-poc"],
            token_reader=failed_token_reader,
        )
        self.assertEqual(token_exit, 1)
        self.assertEqual(
            token_evidence["reason"],
            "rqdata_live_token_input_failed",
        )
        self.assertNotIn("upstream-secret", token_output)

        def failed_builder(token):
            raise RqdataPocTransportError(
                "rqdata_http_authentication_failed"
            )

        build_exit, build_evidence, build_output = self.run_main(
            ["--symbols", "000725.XSHE", "--confirm-live-poc"],
            transport_builder=failed_builder,
        )
        self.assertEqual(build_exit, 1)
        self.assertEqual(
            build_evidence["reason"],
            "rqdata_http_authentication_failed",
        )
        self.assertNotIn("live-secret-token", build_output)


if __name__ == "__main__":
    unittest.main()
