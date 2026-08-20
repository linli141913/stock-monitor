import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_sources import PriceLimitSpecialSession
from radar.sources.leader_tradability_exchange_official import (
    ExchangeOfficialSourceError,
    build_sse_official_observation,
    build_szse_official_observation,
    collect_exchange_official_observations,
)
from radar.sources.leader_tradability_public_poc import PublicSecurityContext


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TRADING_DATE = date(2026, 8, 18)
FETCHED_AT = datetime(2026, 8, 18, 11, 30, 4, tzinfo=SHANGHAI_TZ)


def context(symbol: str, exchange: str) -> PublicSecurityContext:
    return PublicSecurityContext(
        symbol=symbol,
        exchange=exchange,
        board="科创板" if symbol.startswith("688") else "主板",
        listing_date=date(2001, 1, 12),
        identity_source_contract_id=(
            f"{exchange}-public-security-list-v1"
        ),
        identity_source_name=(
            "上海证券交易所" if exchange == "sse" else "深圳证券交易所"
        ),
        identity_source_url=(
            "https://query.sse.com.cn/sseQuery/commonQuery.do"
            if exchange == "sse"
            else "https://www.szse.cn/api/report/ShowReport"
        ),
        identity_document_id=f"{exchange}-security-list-20260818",
        identity_source_time=FETCHED_AT,
        identity_fetched_at=FETCHED_AT,
        identity_content_sha256="sha256:" + "a" * 64,
    )


def sse_payload(
    *,
    symbol: str = "600000",
    name: str = "浦发银行",
    phase: str = "T111    ",
    limit_type: str = "N",
):
    return {
        "code": symbol,
        "date": 20260818,
        "time": 113003,
        "snap": [
            name,
            phase,
            "   D  F  N          ",
            limit_type,
            9.94,
            8.14,
        ],
    }


def szse_payload(
    *,
    symbol: str = "000725",
    name: str = "京东方Ａ",
    phase1: str = "03",
    phase2: str = "08",
    is_delisting=False,
):
    return {
        "datetime": "2026-08-18 11:30",
        "code": "0",
        "data": {
            "code": symbol,
            "name": name,
            "marketTime": "2026-08-18 11:30:03",
            "tradingPhaseCode1": phase1,
            "tradingPhaseCode2": phase2,
            "isDelisting": is_delisting,
            "change20PerLimit": False,
        },
        "message": "成功",
    }


class ExchangeOfficialObservationTests(unittest.TestCase):
    def test_sse_normal_phase_builds_complete_official_observation(self):
        value = build_sse_official_observation(
            context=context("600000", "sse"),
            trading_date=TRADING_DATE,
            payload=sse_payload(),
            fetched_at=FETCHED_AT,
        )

        self.assertEqual(value.symbol, "600000")
        self.assertEqual(
            value.lifecycle_status,
            SecurityLifecycleStatus.NORMAL,
        )
        self.assertEqual(
            value.trading_status,
            TradingSessionStatus.TRADING,
        )
        self.assertEqual(
            value.special_session,
            PriceLimitSpecialSession.NONE,
        )
        self.assertEqual(value.price_limit_mode, PriceLimitMode.BOUNDED)
        self.assertEqual(value.upper_limit_price, 9.94)
        self.assertEqual(value.lower_limit_price, 8.14)
        self.assertEqual(
            value.source_time,
            datetime(2026, 8, 18, 11, 30, 3, tzinfo=SHANGHAI_TZ),
        )

    def test_sse_suspension_and_unknown_phase_are_not_confused(self):
        suspended = build_sse_official_observation(
            context=context("600000", "sse"),
            trading_date=TRADING_DATE,
            payload=sse_payload(phase="P1"),
            fetched_at=FETCHED_AT,
        )
        self.assertEqual(
            suspended.trading_status,
            TradingSessionStatus.SUSPENDED,
        )

        with self.assertRaisesRegex(
            ExchangeOfficialSourceError,
            "exchange_official_phase_unknown",
        ):
            build_sse_official_observation(
                context=context("600000", "sse"),
                trading_date=TRADING_DATE,
                payload=sse_payload(phase="Z9"),
                fetched_at=FETCHED_AT,
            )

    def test_szse_phase_codes_and_delisting_are_explicit(self):
        normal = build_szse_official_observation(
            context=context("000725", "szse"),
            trading_date=TRADING_DATE,
            payload=szse_payload(),
            fetched_at=FETCHED_AT,
        )
        self.assertEqual(
            normal.trading_status,
            TradingSessionStatus.TRADING,
        )
        self.assertEqual(
            normal.lifecycle_status,
            SecurityLifecycleStatus.NORMAL,
        )
        self.assertEqual(
            normal.special_session,
            PriceLimitSpecialSession.NONE,
        )

        suspended = build_szse_official_observation(
            context=context("000725", "szse"),
            trading_date=TRADING_DATE,
            payload=szse_payload(phase1="04", phase2="04"),
            fetched_at=FETCHED_AT,
        )
        self.assertEqual(
            suspended.trading_status,
            TradingSessionStatus.SUSPENDED,
        )

        delisting = build_szse_official_observation(
            context=context("000725", "szse"),
            trading_date=TRADING_DATE,
            payload=szse_payload(is_delisting=True),
            fetched_at=FETCHED_AT,
        )
        self.assertEqual(
            delisting.lifecycle_status,
            SecurityLifecycleStatus.DELISTING,
        )
        self.assertIsNone(delisting.special_session)

    def test_invalid_symbol_or_source_date_is_rejected(self):
        with self.assertRaisesRegex(
            ExchangeOfficialSourceError,
            "exchange_official_symbol_mismatch",
        ):
            build_sse_official_observation(
                context=context("600000", "sse"),
                trading_date=TRADING_DATE,
                payload=sse_payload(symbol="600001"),
                fetched_at=FETCHED_AT,
            )

        payload = szse_payload()
        payload["data"]["marketTime"] = "2026-08-17 15:00:00"
        with self.assertRaisesRegex(
            ExchangeOfficialSourceError,
            "exchange_official_trading_date_mismatch",
        ):
            build_szse_official_observation(
                context=context("000725", "szse"),
                trading_date=TRADING_DATE,
                payload=payload,
                fetched_at=FETCHED_AT,
            )

    def test_batch_preserves_order_and_fails_closed(self):
        contexts = (
            context("000725", "szse"),
            context("600000", "sse"),
        )
        payloads = {
            "000725": szse_payload(),
            "600000": sse_payload(),
        }

        result = collect_exchange_official_observations(
            contexts=contexts,
            trading_date=TRADING_DATE,
            request_json=lambda item: payloads[item.symbol],
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            tuple(item.symbol for item in result.observations),
            ("000725", "600000"),
        )
        self.assertEqual(result.reasons, ())

        failed = collect_exchange_official_observations(
            contexts=contexts,
            trading_date=TRADING_DATE,
            request_json=lambda item: (
                payloads[item.symbol]
                if item.symbol == "000725"
                else (_ for _ in ()).throw(TimeoutError())
            ),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(failed.status, "source_failed")
        self.assertEqual(failed.observations, ())
        self.assertEqual(
            failed.reasons,
            ("exchange_official_request_failed",),
        )

    def test_batch_retries_one_transient_request_without_partial_output(self):
        attempts = {"600000": 0}

        def fetch(item):
            attempts[item.symbol] += 1
            if attempts[item.symbol] == 1:
                raise TimeoutError()
            return sse_payload()

        result = collect_exchange_official_observations(
            contexts=(context("600000", "sse"),),
            trading_date=TRADING_DATE,
            request_json=fetch,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(attempts["600000"], 2)
        self.assertEqual(
            tuple(item.symbol for item in result.observations),
            ("600000",),
        )

    def test_batch_rejects_invalid_scope_without_requesting(self):
        calls = []
        invalid = context("600000", "sse")
        invalid = PublicSecurityContext(
            **{**invalid.__dict__, "symbol": "60000A"}
        )

        result = collect_exchange_official_observations(
            contexts=(invalid,),
            trading_date=TRADING_DATE,
            request_json=lambda item: calls.append(item),
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(result.status, "source_unverified")
        self.assertEqual(
            result.reasons,
            ("exchange_official_scope_invalid",),
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
