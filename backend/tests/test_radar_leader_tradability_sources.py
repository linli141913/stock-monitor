import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import QuoteSnapshot
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_tradability_features import (
    LeaderSecurityLifecycleEvidence,
    OnePriceLimitState,
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
    build_leader_tradability_features,
)
from radar.leader_tradability_sources import (
    OfficialDailyTradabilityReference,
    PriceLimitSpecialSession,
    TradabilitySourceGrade,
    build_leader_tradability_source_input,
    resolve_trading_rule_catalog,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 27, 10, 0, tzinfo=SHANGHAI_TZ)


def quote(**overrides):
    values = {
        "symbol": "600000",
        "name": "浦发银行",
        "sourceTime": AS_OF - timedelta(seconds=10),
        "fetchedAt": AS_OF - timedelta(seconds=5),
        "price": 9.94,
        "previousClose": 9.04,
        "openPrice": 9.94,
        "highPrice": 9.94,
        "lowPrice": 9.94,
        "changePercent": 9.96,
        "upperLimitPriceSource": 9.94,
        "lowerLimitPriceSource": 8.14,
    }
    values.update(overrides)
    return QuoteSnapshot(**values)


def lifecycle(**overrides):
    values = {
        "symbol": "600000",
        "exchange": "sse",
        "board": "主板A股",
        "lifecycle_status": SecurityLifecycleStatus.NORMAL,
        "listed_trading_day_count": 100,
        "source_contract_id": (
            "sse-security-lifecycle-v1:600000"
        ),
        "source_name": "上海证券交易所",
        "source_url": (
            "https://www.sse.com.cn/assortment/stock/list/"
        ),
        "document_id": "sse-security-lifecycle-600000",
        "published_at": AS_OF - timedelta(days=1),
        "effective_from": AS_OF - timedelta(days=1),
        "effective_until": None,
        "fetched_at": AS_OF - timedelta(minutes=5),
    }
    values.update(overrides)
    return LeaderSecurityLifecycleEvidence(**values)


def official_reference(**overrides):
    values = {
        "symbol": "600000",
        "exchange": "sse",
        "board": "主板A股",
        "trading_date": AS_OF.date(),
        "lifecycle_status": SecurityLifecycleStatus.NORMAL,
        "trading_status": TradingSessionStatus.TRADING,
        "special_session": PriceLimitSpecialSession.NONE,
        "price_limit_mode": PriceLimitMode.BOUNDED,
        "upper_limit_price": 9.94,
        "lower_limit_price": 8.14,
        "source_grade": TradabilitySourceGrade.OFFICIAL_PRIMARY,
        "source_contract_id": (
            "sse-daily-tradability-v1:600000:20260727"
        ),
        "source_name": "上海证券交易所",
        "source_url": (
            "https://www.sse.com.cn/services/tradingtech/data/"
        ),
        "document_id": "cpxx0201-20260727",
        "source_time": AS_OF - timedelta(seconds=10),
        "fetched_at": AS_OF - timedelta(seconds=5),
    }
    values.update(overrides)
    return OfficialDailyTradabilityReference(**values)


class TradingRuleCatalogTests(unittest.TestCase):
    def test_main_board_risk_warning_switches_from_five_to_ten_percent(self):
        for exchange, board in (
            ("sse", "主板A股"),
            ("szse", "主板"),
        ):
            with self.subTest(exchange=exchange, version="2023"):
                historical = resolve_trading_rule_catalog(
                    exchange=exchange,
                    board=board,
                    lifecycle_status=SecurityLifecycleStatus.STAR_ST,
                    trading_date=date(2026, 7, 5),
                    listed_trading_day_count=100,
                    special_session=PriceLimitSpecialSession.NONE,
                    previous_close=6.58,
                )
                self.assertEqual(historical.limit_rate, 0.05)
                self.assertTrue(
                    historical.rule_version.endswith("-2023")
                )
                self.assertEqual(
                    historical.expected_upper_limit_price,
                    6.91,
                )
                self.assertEqual(
                    historical.expected_lower_limit_price,
                    6.25,
                )

            with self.subTest(exchange=exchange, version="2026"):
                current = resolve_trading_rule_catalog(
                    exchange=exchange,
                    board=board,
                    lifecycle_status=SecurityLifecycleStatus.STAR_ST,
                    trading_date=date(2026, 7, 6),
                    listed_trading_day_count=100,
                    special_session=PriceLimitSpecialSession.NONE,
                    previous_close=6.58,
                )
                self.assertEqual(current.limit_rate, 0.10)
                self.assertTrue(
                    current.rule_version.endswith("-2026")
                )
                self.assertEqual(
                    current.expected_upper_limit_price,
                    7.24,
                )
                self.assertEqual(
                    current.expected_lower_limit_price,
                    5.92,
                )

    def test_star_and_chinext_preserve_twenty_percent(self):
        for exchange, board in (
            ("sse", "科创板"),
            ("szse", "创业板"),
        ):
            for trading_date in (
                date(2026, 7, 5),
                date(2026, 7, 6),
            ):
                with self.subTest(
                    exchange=exchange,
                    trading_date=trading_date,
                ):
                    resolved = resolve_trading_rule_catalog(
                        exchange=exchange,
                        board=board,
                        lifecycle_status=(
                            SecurityLifecycleStatus.ST
                        ),
                        trading_date=trading_date,
                        listed_trading_day_count=100,
                        special_session=(
                            PriceLimitSpecialSession.NONE
                        ),
                        previous_close=10.0,
                    )
                    self.assertEqual(resolved.limit_rate, 0.20)
                    self.assertEqual(
                        resolved.price_limit_mode,
                        PriceLimitMode.BOUNDED,
                    )
                    self.assertEqual(
                        resolved.expected_upper_limit_price,
                        12.0,
                    )
                    self.assertEqual(
                        resolved.expected_lower_limit_price,
                        8.0,
                    )

    def test_listing_and_special_first_days_have_no_price_limit(self):
        cases = (
            (
                5,
                SecurityLifecycleStatus.NORMAL,
                PriceLimitSpecialSession.NONE,
            ),
            (
                100,
                SecurityLifecycleStatus.RELISTED,
                PriceLimitSpecialSession.RELISTING_FIRST_DAY,
            ),
            (
                100,
                SecurityLifecycleStatus.DELISTING,
                PriceLimitSpecialSession.DELISTING_FIRST_DAY,
            ),
        )
        for listed_days, lifecycle_status, special_session in cases:
            with self.subTest(
                lifecycle_status=lifecycle_status,
                special_session=special_session,
            ):
                resolved = resolve_trading_rule_catalog(
                    exchange="szse",
                    board="主板",
                    lifecycle_status=lifecycle_status,
                    trading_date=date(2026, 7, 27),
                    listed_trading_day_count=listed_days,
                    special_session=special_session,
                    previous_close=10.0,
                )
                self.assertEqual(
                    resolved.price_limit_mode,
                    PriceLimitMode.NO_LIMIT,
                )
                self.assertIsNone(resolved.limit_rate)
                self.assertIsNone(
                    resolved.expected_upper_limit_price
                )
                self.assertIsNone(
                    resolved.expected_lower_limit_price
                )

    def test_unknown_board_and_unsupported_history_are_rejected(self):
        cases = (
            {"board": "未知板块", "trading_date": date(2026, 7, 27)},
            {"board": "主板", "trading_date": date(2023, 4, 9)},
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    resolve_trading_rule_catalog(
                        exchange="szse",
                        board=values["board"],
                        lifecycle_status=(
                            SecurityLifecycleStatus.NORMAL
                        ),
                        trading_date=values["trading_date"],
                        listed_trading_day_count=100,
                        special_session=(
                            PriceLimitSpecialSession.NONE
                        ),
                        previous_close=10.0,
                    )


class TradabilitySourceAssemblyTests(unittest.TestCase):
    def build_resolution(self, **overrides):
        values = {
            "as_of": AS_OF,
            "quote": quote(),
            "quote_source_contract_id": (
                "tencent-full-market-quote-v1:batch-1"
            ),
            "quote_source_status": ResearchFeatureStatus.READY,
            "lifecycle": lifecycle(),
            "official_reference": official_reference(),
        }
        values.update(overrides)
        return build_leader_tradability_source_input(**values)

    def test_official_reference_unavailable_stays_missing(self):
        result = self.build_resolution(official_reference=None)
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "official_reference_source_unavailable",
            result.reasons,
        )
        self.assertFalse(evidence["scoreReady"])
        self.assertFalse(evidence["formalUsable"])

    def test_consistent_official_reference_builds_c1_input(self):
        result = self.build_resolution()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.feature_input)
        self.assertEqual(
            result.feature_input.trading_rule.rule_version,
            "sse-trading-rule-2026",
        )
        self.assertEqual(
            result.feature_input.trading_rule.upper_limit_price,
            9.94,
        )
        self.assertEqual(
            result.feature_input.trading_rule.lower_limit_price,
            8.14,
        )

        feature = build_leader_tradability_features(
            result.feature_input
        )
        self.assertEqual(feature.status, ResearchFeatureStatus.READY)
        self.assertEqual(
            feature.one_price_limit_state,
            OnePriceLimitState.ONE_PRICE_LIMIT_UP,
        )
        self.assertFalse(feature.research_eligible)
        self.assertFalse(feature.to_evidence()["scoreReady"])
        self.assertFalse(feature.to_evidence()["formalUsable"])

    def test_official_clock_skew_within_five_seconds_builds_input(self):
        result = self.build_resolution(
            official_reference=official_reference(
                source_time=AS_OF + timedelta(seconds=1),
                fetched_at=AS_OF,
            ),
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.feature_input)

    def test_quote_clock_skew_within_five_seconds_builds_input(self):
        result = self.build_resolution(
            quote=quote(
                sourceTime=AS_OF + timedelta(seconds=1),
                fetchedAt=AS_OF,
            ),
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertIsNotNone(result.feature_input)

    def test_non_primary_source_grade_cannot_build_input(self):
        result = self.build_resolution(
            official_reference=official_reference(
                source_grade=(
                    TradabilitySourceGrade.
                    OFFICIAL_RESTRICTED_CANDIDATE
                ),
            ),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "official_reference_grade_not_accepted",
            result.reasons,
        )

    def test_identity_and_lifecycle_conflicts_are_degraded(self):
        cases = (
            (
                official_reference(symbol="600001"),
                "official_reference_symbol_mismatch",
            ),
            (
                official_reference(
                    lifecycle_status=SecurityLifecycleStatus.ST
                ),
                "official_lifecycle_conflict",
            ),
            (
                official_reference(
                    trading_date=date(2026, 7, 26)
                ),
                "official_reference_trading_date_mismatch",
            ),
        )
        for reference, reason in cases:
            with self.subTest(reason=reason):
                result = self.build_resolution(
                    official_reference=reference,
                )
                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.feature_input)
                self.assertIn(reason, result.reasons)

    def test_official_rule_conflict_is_degraded(self):
        result = self.build_resolution(
            quote=quote(
                upperLimitPriceSource=None,
                lowerLimitPriceSource=None,
            ),
            official_reference=official_reference(
                upper_limit_price=9.95,
            ),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "official_rule_catalog_conflict",
            result.reasons,
        )

    def test_secondary_price_limit_conflict_is_degraded(self):
        result = self.build_resolution(
            quote=quote(upperLimitPriceSource=9.95),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "secondary_price_limit_conflict",
            result.reasons,
        )

    def test_unknown_trading_status_cannot_be_inferred(self):
        result = self.build_resolution(
            official_reference=official_reference(
                trading_status=TradingSessionStatus.UNKNOWN,
            ),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn("trading_status_unknown", result.reasons)

    def test_no_limit_reference_rejects_boundaries(self):
        result = self.build_resolution(
            lifecycle=lifecycle(listed_trading_day_count=5),
            quote=quote(
                upperLimitPriceSource=None,
                lowerLimitPriceSource=None,
            ),
            official_reference=official_reference(
                price_limit_mode=PriceLimitMode.NO_LIMIT,
                upper_limit_price=9.94,
                lower_limit_price=None,
            ),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "official_no_limit_boundary_present",
            result.reasons,
        )

    def test_invalid_source_url_is_rejected(self):
        result = self.build_resolution(
            official_reference=official_reference(
                source_url="http://example.invalid/reference",
            ),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "official_reference_source_url_unverified",
            result.reasons,
        )

    def test_c1_preflight_rejects_invalid_lifecycle_evidence(self):
        result = self.build_resolution(
            lifecycle=lifecycle(
                source_url="http://example.invalid/lifecycle",
            ),
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.feature_input)
        self.assertIn(
            "lifecycle_source_url_unverified",
            result.reasons,
        )


if __name__ == "__main__":
    unittest.main()
