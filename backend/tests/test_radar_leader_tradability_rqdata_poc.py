import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime
from io import BytesIO
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from radar.sources.leader_tradability_rqdata_poc import (
    RQDATA_HTTP_API_URL,
    RQDATA_POC_TIMEOUT_SECONDS,
    RqdataPocStatus,
    RqdataPocTransportError,
    RqdataTradabilityPocQuery,
    build_rqdata_http_transport,
    run_rqdata_tradability_poc,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ)


def make_query(**overrides):
    values = {
        "as_of": AS_OF,
        "trading_date": date(2026, 8, 3),
        "expected_order_book_ids": (
            "000725.XSHE",
            "600000.XSHG",
        ),
        "include_dynamic_snapshot": False,
    }
    values.update(overrides)
    return RqdataTradabilityPocQuery(**values)


def complete_responses(**overrides):
    responses = {
        "instruments": (
            "order_book_id,exchange,board_type,listed_date,"
            "de_listed_date,status,special_type\n"
            "000725.XSHE,XSHE,MainBoard,2001-01-12,"
            "0000-00-00,Active,Normal\n"
            "600000.XSHG,XSHG,MainBoard,1999-11-10,"
            "0000-00-00,Active,ST\n"
        ),
        "get_price": (
            "order_book_id,date,prev_close,limit_up,limit_down\n"
            "000725.XSHE,2026-08-03,10.00,11.00,9.00\n"
            "600000.XSHG,2026-08-03,8.00,8.40,7.60\n"
        ),
        "is_suspended": {
            "000725.XSHE": (
                "order_book_id,date,is_suspended\n"
                "000725.XSHE,2026-08-03,false\n"
            ),
            "600000.XSHG": (
                "order_book_id,date,is_suspended\n"
                "600000.XSHG,2026-08-03,true\n"
            ),
        },
        "is_st_stock": {
            "000725.XSHE": (
                "order_book_id,date,is_st_stock\n"
                "000725.XSHE,2026-08-03,0\n"
            ),
            "600000.XSHG": (
                "order_book_id,date,is_st_stock\n"
                "600000.XSHG,2026-08-03,1\n"
            ),
        },
        "current_snapshot": (
            "order_book_id,datetime,trading_phase_code,"
            "prev_close,limit_up,limit_down\n"
            "000725.XSHE,2026-08-03 09:59:30,T,"
            "10.00,11.00,9.00\n"
            "600000.XSHG,2026-08-03 09:59:30,T,"
            "8.00,8.40,7.60\n"
        ),
    }
    responses.update(overrides)
    return responses


def response_transport(responses, calls):
    def transport(method, payload, timeout):
        calls.append((method, payload, timeout))
        response = responses[method]
        if isinstance(response, dict):
            return response[payload["order_book_id"]]
        return response

    return transport


def http_opener_from_responses(responses, calls):
    def opener(request, timeout):
        request_payload = json.loads(request.data.decode("utf-8"))
        method = request_payload.pop("method")
        calls.append((method, request_payload, timeout))
        response = responses[method]
        if isinstance(response, dict):
            response = response[request_payload["order_book_id"]]
        return FakeHttpResponse(response.encode("utf-8"))

    return opener


class FakeHttpResponse:
    def __init__(self, body):
        self.body = body

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class RqdataTradabilityPocTests(unittest.TestCase):
    def test_absent_transport_is_explicit_not_run_and_formal_safe(self):
        report = run_rqdata_tradability_poc(
            make_query(),
            fetched_at=AS_OF,
            transport=None,
        )

        self.assertEqual(report.status, RqdataPocStatus.NOT_RUN)
        self.assertEqual(report.real_poc_status, "not_run")
        self.assertEqual(report.records, ())
        self.assertEqual(report.call_summaries, ())
        self.assertEqual(report.reasons, ("rqdata_real_poc_not_run",))
        self.assertFalse(report.formal_score_ready)
        self.assertFalse(report.formal_gate_ready)
        self.assertFalse(report.formal_usable)
        self.assertFalse(report.state_transition_allowed)

        evidence = report.to_evidence()
        self.assertEqual(evidence["status"], "not_run")
        self.assertNotIn("records", evidence)
        self.assertNotIn("responseBody", evidence)
        self.assertNotIn("token", repr(report).lower())

        with self.assertRaises(FrozenInstanceError):
            report.status = RqdataPocStatus.BLOCKED

    def test_invalid_query_is_blocked_before_transport(self):
        calls = []

        def transport(method, payload, timeout):
            calls.append((method, payload, timeout))
            return ""

        report = run_rqdata_tradability_poc(
            make_query(
                expected_order_book_ids=(
                    "000725.XSHE",
                    "000725.XSHE",
                )
            ),
            fetched_at=AS_OF,
            transport=transport,
        )

        self.assertEqual(report.status, RqdataPocStatus.BLOCKED)
        self.assertEqual(calls, [])
        self.assertIn(
            "rqdata_query_symbol_duplicated",
            report.reasons,
        )

    def test_fixture_field_matrix_is_partial_not_real_and_never_formal(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(),
                calls,
            ),
        )

        self.assertEqual(
            report.status,
            RqdataPocStatus.PARTIAL,
        )
        self.assertEqual(report.real_poc_status, "not_run")
        self.assertIn("rqdata_fixture_transport_only", report.reasons)
        self.assertEqual(
            [item[0] for item in calls],
            [
                "instruments",
                "get_price",
                "is_suspended",
                "is_suspended",
                "is_st_stock",
                "is_st_stock",
                "current_snapshot",
            ],
        )
        self.assertEqual(len(report.records), 2)
        first, second = report.records
        self.assertFalse(first.is_suspended)
        self.assertFalse(first.is_st_stock)
        self.assertTrue(second.is_suspended)
        self.assertTrue(second.is_st_stock)
        self.assertEqual(first.limit_up, 11.0)
        self.assertEqual(second.special_type, "ST")
        self.assertEqual(
            second.snapshot_at.isoformat(),
            "2026-08-03T09:59:30+08:00",
        )
        self.assertEqual(
            len(report.call_summaries),
            7,
        )
        self.assertTrue(all(
            item.response_digest.startswith("sha256:")
            for item in report.call_summaries
        ))
        self.assertEqual(
            [item.row_count for item in report.call_summaries],
            [2, 2, 1, 1, 1, 1, 2],
        )
        self.assertFalse(report.formal_usable)
        self.assertFalse(report.state_transition_allowed)

        price_payload = calls[1][1]
        self.assertEqual(price_payload["adjust_type"], "none")
        self.assertEqual(price_payload["frequency"], "1d")
        self.assertNotIn("token", repr(calls).lower())

        suspension_payload = calls[2][1]
        self.assertEqual(
            suspension_payload,
            {"order_book_id": "000725.XSHE", "count": 1},
        )

    def test_get_price_accepts_official_http_datetime_column(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    get_price=(
                        "order_book_id,datetime,prev_close,limit_up,limit_down\n"
                        "000725.XSHE,2026-08-03,10.00,11.00,9.00\n"
                        "600000.XSHG,2026-08-03,8.00,8.40,7.60\n"
                    )
                ),
                calls,
            ),
        )

        self.assertEqual(report.status, RqdataPocStatus.PARTIAL)
        self.assertEqual(report.records[0].prev_close, 10.0)
        self.assertNotIn(
            "rqdata_get_price_response_fields_missing",
            report.reasons,
        )

    def test_get_price_rejects_ambiguous_date_columns(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    get_price=(
                        "order_book_id,date,datetime,prev_close,"
                        "limit_up,limit_down\n"
                        "000725.XSHE,2026-08-03,2026-08-03,"
                        "10.00,11.00,9.00\n"
                        "600000.XSHG,2026-08-03,2026-08-03,"
                        "8.00,8.40,7.60\n"
                    )
                ),
                calls,
            ),
        )

        self.assertEqual(report.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_get_price_response_fields_unverified",
            report.reasons,
        )
        self.assertEqual(len(calls), 2)

    def test_only_default_http_transport_can_mark_real_poc_completed(self):
        responses = complete_responses()
        calls = []
        opener = http_opener_from_responses(responses, calls)
        module_path = (
            "radar.sources.leader_tradability_rqdata_poc.urlopen"
        )
        with patch(module_path, side_effect=opener):
            transport = build_rqdata_http_transport("secret-token")
            report = run_rqdata_tradability_poc(
                make_query(include_dynamic_snapshot=True),
                fetched_at=AS_OF,
                transport=transport,
            )

        self.assertEqual(report.status, RqdataPocStatus.FIELD_CANDIDATE)
        self.assertEqual(report.real_poc_status, "completed")
        self.assertNotIn("rqdata_fixture_transport_only", report.reasons)
        self.assertEqual(len(calls), 7)

    def test_static_only_run_is_partial_and_does_not_call_snapshot(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(),
                calls,
            ),
        )

        self.assertEqual(report.status, RqdataPocStatus.PARTIAL)
        self.assertEqual(len(calls), 6)
        self.assertNotIn("current_snapshot", [item[0] for item in calls])
        self.assertIn(
            "rqdata_dynamic_snapshot_not_run",
            report.reasons,
        )

    def test_empty_method_is_partial_but_missing_columns_are_blocked(self):
        empty_calls = []
        empty = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(get_price=""),
                empty_calls,
            ),
        )
        self.assertEqual(empty.status, RqdataPocStatus.PARTIAL)
        self.assertIn(
            "rqdata_get_price_response_empty",
            empty.reasons,
        )
        self.assertEqual(len(empty_calls), 2)

        malformed_calls = []
        malformed = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    is_st_stock={
                        "000725.XSHE": (
                            "order_book_id,date\n"
                            "000725.XSHE,2026-08-03\n"
                        ),
                        "600000.XSHG": (
                            "order_book_id,date,is_st_stock\n"
                            "600000.XSHG,2026-08-03,1\n"
                        ),
                    }
                ),
                malformed_calls,
            ),
        )
        self.assertEqual(malformed.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_is_st_stock_response_fields_missing",
            malformed.reasons,
        )
        self.assertEqual(len(malformed_calls), 5)

    def test_live_empty_response_is_no_data_not_completed(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeHttpResponse(b"")

        module_path = (
            "radar.sources.leader_tradability_rqdata_poc.urlopen"
        )
        with patch(module_path, side_effect=opener):
            report = run_rqdata_tradability_poc(
                make_query(),
                fetched_at=AS_OF,
                transport=build_rqdata_http_transport("secret-token"),
            )

        self.assertEqual(report.status, RqdataPocStatus.PARTIAL)
        self.assertEqual(report.real_poc_status, "no_data")
        self.assertIn("rqdata_no_usable_response_data", report.reasons)
        self.assertEqual(len(calls), 1)

    def test_identity_date_and_dynamic_time_drift_are_rejected(self):
        duplicate_calls = []
        duplicate = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    is_suspended={
                        "000725.XSHE": (
                            "order_book_id,date,is_suspended\n"
                            "000725.XSHE,2026-08-03,false\n"
                            "000725.XSHE,2026-08-03,true\n"
                            "300001.XSHE,2026-08-03,false\n"
                        ),
                        "600000.XSHG": (
                            "order_book_id,date,is_suspended\n"
                            "600000.XSHG,2026-08-03,true\n"
                        ),
                    }
                ),
                duplicate_calls,
            ),
        )
        self.assertEqual(duplicate.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_is_suspended_response_identity_mismatch",
            duplicate.reasons,
        )

        stale_calls = []
        stale = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    current_snapshot=(
                        "order_book_id,datetime,trading_phase_code,"
                        "prev_close,limit_up,limit_down\n"
                        "000725.XSHE,2026-08-03 09:58:00,T,"
                        "10.00,11.00,9.00\n"
                        "600000.XSHG,2026-08-03 09:58:00,S,"
                        "8.00,8.40,7.60\n"
                    )
                ),
                stale_calls,
            ),
        )
        self.assertEqual(stale.status, RqdataPocStatus.PARTIAL)
        self.assertIn(
            "rqdata_current_snapshot_stale",
            stale.reasons,
        )

    def test_dynamic_observation_time_uses_fetched_at(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=datetime(
                2026,
                8,
                3,
                10,
                5,
                tzinfo=SHANGHAI_TZ,
            ),
            transport=response_transport(
                complete_responses(),
                calls,
            ),
        )

        self.assertEqual(report.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_dynamic_observation_time_mismatch",
            report.reasons,
        )
        self.assertEqual(calls, [])

    def test_missing_price_limits_remain_missing_not_zero(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    get_price=(
                        "order_book_id,date,prev_close,limit_up,limit_down\n"
                        "000725.XSHE,2026-08-03,10.00,,\n"
                        "600000.XSHG,2026-08-03,8.00,8.40,7.60\n"
                    ),
                    current_snapshot=(
                        "order_book_id,datetime,trading_phase_code,"
                        "prev_close,limit_up,limit_down\n"
                        "000725.XSHE,2026-08-03 09:59:30,T,10.00,,\n"
                        "600000.XSHG,2026-08-03 09:59:30,S,"
                        "8.00,8.40,7.60\n"
                    ),
                ),
                calls,
            ),
        )

        self.assertEqual(report.status, RqdataPocStatus.PARTIAL)
        self.assertIsNone(report.records[0].limit_up)
        self.assertIsNone(report.records[0].limit_down)
        self.assertIn(
            "rqdata_price_limit_values_missing",
            report.reasons,
        )

    def test_http_transport_uses_fixed_endpoint_and_hides_token(self):
        captured = []
        secret = "rqdata-secret-token"

        def opener(request, timeout):
            captured.append((request, timeout))
            return FakeHttpResponse(b"order_book_id\n000725.XSHE\n")

        transport = build_rqdata_http_transport(
            secret,
            opener=opener,
        )
        response = transport(
            "instruments",
            {"order_book_ids": ["000725.XSHE"]},
            RQDATA_POC_TIMEOUT_SECONDS,
        )

        self.assertEqual(response, "order_book_id\n000725.XSHE\n")
        self.assertEqual(len(captured), 1)
        request, timeout = captured[0]
        self.assertEqual(request.full_url, RQDATA_HTTP_API_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 20.0)
        self.assertEqual(request.get_header("Token"), secret)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["method"], "instruments")
        self.assertNotIn("token", body)
        self.assertNotIn(secret, repr(transport))
        self.assertNotIn(secret, response)

    def test_http_transport_rejects_credentials_and_method_override(self):
        with self.assertRaises(RqdataPocTransportError) as empty:
            build_rqdata_http_transport("")
        self.assertEqual(
            empty.exception.reason_code,
            "rqdata_http_token_unverified",
        )

        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeHttpResponse(b"ok")

        transport = build_rqdata_http_transport(
            "secret",
            opener=opener,
        )
        with self.assertRaises(RqdataPocTransportError) as unknown:
            transport("delete", {}, 20.0)
        self.assertEqual(
            unknown.exception.reason_code,
            "rqdata_http_method_unverified",
        )
        with self.assertRaises(RqdataPocTransportError) as override:
            transport(
                "instruments",
                {"method": "delete"},
                20.0,
            )
        self.assertEqual(
            override.exception.reason_code,
            "rqdata_http_payload_unverified",
        )
        self.assertEqual(calls, [])

    def test_http_failures_are_stable_redacted_and_not_retried(self):
        cases = (
            (401, "rqdata_http_authentication_failed"),
            (403, "rqdata_http_permission_denied"),
            (429, "rqdata_http_quota_or_rate_limited"),
            (503, "rqdata_http_source_unavailable"),
        )
        for status_code, expected_reason in cases:
            calls = []

            def opener(request, timeout, code=status_code):
                calls.append((request, timeout))
                raise HTTPError(
                    request.full_url,
                    code,
                    "upstream secret response",
                    None,
                    BytesIO(b"token=never-expose"),
                )

            transport = build_rqdata_http_transport(
                "secret-token",
                opener=opener,
            )
            with self.subTest(status_code=status_code):
                with self.assertRaises(RqdataPocTransportError) as caught:
                    transport("instruments", {}, 20.0)
                self.assertEqual(caught.exception.reason_code, expected_reason)
                self.assertEqual(str(caught.exception), expected_reason)
                self.assertNotIn("secret", repr(caught.exception))
                self.assertNotIn("never-expose", str(caught.exception))
                self.assertIsNone(caught.exception.__context__)
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(len(calls), 1)

    def test_network_encoding_and_size_failures_are_redacted(self):
        def network_failure(request, timeout):
            raise URLError("token=upstream-secret")

        network_transport = build_rqdata_http_transport(
            "local-secret",
            opener=network_failure,
        )
        with self.assertRaises(RqdataPocTransportError) as network:
            network_transport("instruments", {}, 20.0)
        self.assertEqual(
            network.exception.reason_code,
            "rqdata_http_network_failed",
        )
        self.assertNotIn("secret", str(network.exception))
        self.assertIsNone(network.exception.__context__)

        invalid_utf8 = build_rqdata_http_transport(
            "local-secret",
            opener=lambda request, timeout: FakeHttpResponse(b"\xff"),
        )
        with self.assertRaises(RqdataPocTransportError) as encoding:
            invalid_utf8("instruments", {}, 20.0)
        self.assertEqual(
            encoding.exception.reason_code,
            "rqdata_http_response_encoding_invalid",
        )

        oversized = build_rqdata_http_transport(
            "local-secret",
            opener=lambda request, timeout: FakeHttpResponse(
                b"x" * 1_000_001
            ),
        )
        with self.assertRaises(RqdataPocTransportError) as size:
            oversized("instruments", {}, 20.0)
        self.assertEqual(
            size.exception.reason_code,
            "rqdata_http_response_too_large",
        )

    def test_poc_stops_after_stable_transport_failure(self):
        calls = []

        def transport(method, payload, timeout):
            calls.append(method)
            raise RqdataPocTransportError(
                "rqdata_http_authentication_failed"
            )

        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=transport,
        )

        self.assertEqual(report.status, RqdataPocStatus.BLOCKED)
        self.assertEqual(calls, ["instruments"])
        self.assertIn(
            "rqdata_http_authentication_failed",
            report.reasons,
        )
        self.assertIn("rqdata_fixture_transport_only", report.reasons)

    def test_untrusted_transport_reason_and_exception_are_redacted(self):
        def forged_transport(method, payload, timeout):
            raise RqdataPocTransportError(
                "token=forged-upstream-secret"
            )

        report = run_rqdata_tradability_poc(
            make_query(),
            fetched_at=AS_OF,
            transport=forged_transport,
        )
        self.assertIn("rqdata_http_transport_failed", report.reasons)
        self.assertIn("rqdata_fixture_transport_only", report.reasons)
        self.assertNotIn("secret", repr(report))

        transport = build_rqdata_http_transport(
            "local-secret",
            opener=lambda request, timeout: (_ for _ in ()).throw(
                RuntimeError("upstream-secret")
            ),
        )
        with self.assertRaises(RqdataPocTransportError) as caught:
            transport("instruments", {}, 20.0)
        self.assertEqual(
            caught.exception.reason_code,
            "rqdata_http_transport_failed",
        )
        self.assertNotIn("secret", str(caught.exception))

    def test_dynamic_snapshot_requires_market_window_and_bounded_sample(self):
        calls = []

        def transport(method, payload, timeout):
            calls.append(method)
            return ""

        noon = run_rqdata_tradability_poc(
            make_query(
                as_of=datetime(
                    2026,
                    8,
                    3,
                    12,
                    0,
                    tzinfo=SHANGHAI_TZ,
                ),
                include_dynamic_snapshot=True,
            ),
            fetched_at=datetime(
                2026,
                8,
                3,
                12,
                0,
                tzinfo=SHANGHAI_TZ,
            ),
            transport=transport,
        )
        self.assertEqual(noon.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_dynamic_snapshot_window_closed",
            noon.reasons,
        )
        self.assertEqual(calls, [])

        oversized = run_rqdata_tradability_poc(
            make_query(
                expected_order_book_ids=(
                    "000001.XSHE",
                    "000002.XSHE",
                    "000003.XSHE",
                    "000004.XSHE",
                    "000005.XSHE",
                    "000006.XSHE",
                    "000007.XSHE",
                    "000008.XSHE",
                    "000009.XSHE",
                )
            ),
            fetched_at=AS_OF,
            transport=transport,
        )
        self.assertEqual(oversized.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_query_sample_limit_exceeded",
            oversized.reasons,
        )
        self.assertEqual(calls, [])

    def test_instrument_special_type_and_st_result_must_agree(self):
        calls = []
        responses = complete_responses()
        responses["is_st_stock"] = {
            "000725.XSHE": (
                "order_book_id,date,is_st_stock\n"
                "000725.XSHE,2026-08-03,0\n"
            ),
            "600000.XSHG": (
                "order_book_id,date,is_st_stock\n"
                "600000.XSHG,2026-08-03,0\n"
            ),
        }
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(responses, calls),
        )

        self.assertEqual(report.status, RqdataPocStatus.BLOCKED)
        self.assertIn("rqdata_st_status_conflict", report.reasons)

    def test_unknown_provider_enums_are_blocked(self):
        calls = []
        responses = complete_responses(
            instruments=(
                "order_book_id,exchange,board_type,listed_date,"
                "de_listed_date,status,special_type\n"
                "000725.XSHE,XSHE,UnknownBoard,2001-01-12,"
                "0000-00-00,Active,Normal\n"
                "600000.XSHG,XSHG,MainBoard,1999-11-10,"
                "0000-00-00,Active,ST\n"
            )
        )
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(responses, calls),
        )

        self.assertEqual(report.status, RqdataPocStatus.BLOCKED)
        self.assertIn(
            "rqdata_instruments_field_contract_unverified",
            report.reasons,
        )

    def test_unmapped_trading_phase_is_partial_not_covered(self):
        calls = []
        report = run_rqdata_tradability_poc(
            make_query(include_dynamic_snapshot=True),
            fetched_at=AS_OF,
            transport=response_transport(
                complete_responses(
                    current_snapshot=(
                        "order_book_id,datetime,trading_phase_code,"
                        "prev_close,limit_up,limit_down\n"
                        "000725.XSHE,2026-08-03 09:59:30,FORGED,"
                        "10.00,11.00,9.00\n"
                        "600000.XSHG,2026-08-03 09:59:30,T,"
                        "8.00,8.40,7.60\n"
                    )
                ),
                calls,
            ),
        )

        self.assertEqual(report.status, RqdataPocStatus.PARTIAL)
        self.assertIn(
            "rqdata_trading_phase_code_unmapped",
            report.reasons,
        )
        self.assertNotIn("trading_phase", report.field_coverage)


if __name__ == "__main__":
    unittest.main()
