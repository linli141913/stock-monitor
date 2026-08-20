import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import QuoteSnapshot
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_tradability_features import (
    LeaderSecurityLifecycleEvidence,
    LeaderTradabilityFeatureInput,
    LeaderTradingRuleEvidence,
    LeaderTradingStatusEvidence,
    OnePriceLimitState,
    PriceLimitMode,
    PriceLimitState,
    SecurityLifecycleStatus,
    TradingSessionStatus,
    build_leader_tradability_features,
    missing_leader_tradability_features,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 27, 10, 0, tzinfo=SHANGHAI_TZ)


def quote(**overrides):
    values = {
        "symbol": "000001",
        "name": "测试证券",
        "sourceTime": AS_OF - timedelta(seconds=10),
        "fetchedAt": AS_OF - timedelta(seconds=5),
        "price": 10.0,
        "previousClose": 9.5,
        "openPrice": 9.8,
        "highPrice": 10.2,
        "lowPrice": 9.7,
        "changePercent": 5.26,
    }
    values.update(overrides)
    return QuoteSnapshot(**values)


def ready_input():
    return LeaderTradabilityFeatureInput(
        as_of=AS_OF,
        quote=quote(),
        quote_source_contract_id=(
            "tencent-full-market-quote-v1:batch-1"
        ),
        quote_source_status=ResearchFeatureStatus.READY,
        lifecycle=LeaderSecurityLifecycleEvidence(
            symbol="000001",
            exchange="szse",
            board="主板",
            lifecycle_status=SecurityLifecycleStatus.NORMAL,
            listed_trading_day_count=100,
            source_contract_id=(
                "exchange-security-lifecycle-v1:000001"
            ),
            source_name="深圳证券交易所",
            source_url=(
                "https://www.szse.cn/market/product/stock/list/"
            ),
            document_id="security-lifecycle-000001",
            published_at=AS_OF - timedelta(days=1),
            effective_from=AS_OF - timedelta(days=1),
            effective_until=None,
            fetched_at=AS_OF - timedelta(minutes=5),
        ),
        trading_status=LeaderTradingStatusEvidence(
            symbol="000001",
            trading_date=AS_OF.date(),
            status=TradingSessionStatus.TRADING,
            source_contract_id=(
                "exchange-trading-status-v1:000001:20260727"
            ),
            source_name="深圳证券交易所",
            source_time=AS_OF - timedelta(seconds=10),
            fetched_at=AS_OF - timedelta(seconds=5),
        ),
        trading_rule=LeaderTradingRuleEvidence(
            symbol="000001",
            trading_date=AS_OF.date(),
            rule_version="cn-equity-price-limit-rule-v1",
            price_limit_mode=PriceLimitMode.BOUNDED,
            upper_limit_price=10.45,
            lower_limit_price=8.55,
            source_contract_id=(
                "exchange-price-limit-v1:000001:20260727"
            ),
            source_name="深圳证券交易所",
            source_url="https://www.szse.cn/lawrules/rule/stock/",
            published_at=AS_OF - timedelta(days=30),
            effective_from=AS_OF - timedelta(days=20),
            effective_until=None,
        ),
    )


class LeaderTradabilityFeatureTests(unittest.TestCase):
    def test_normal_security_is_ready_research_only(self):
        result = build_leader_tradability_features(ready_input())
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertTrue(result.research_eligible)
        self.assertFalse(result.preliminary_only)
        self.assertEqual(
            result.price_limit_state,
            PriceLimitState.NORMAL,
        )
        self.assertEqual(
            result.one_price_limit_state,
            OnePriceLimitState.NONE,
        )
        self.assertFalse(evidence["scoreReady"])
        self.assertFalse(evidence["formalUsable"])
        self.assertIsNone(evidence["researchScore"])

    def test_explicit_lifecycle_exclusions_are_not_missing(self):
        excluded_statuses = (
            SecurityLifecycleStatus.ST,
            SecurityLifecycleStatus.STAR_ST,
            SecurityLifecycleStatus.DELISTING,
            SecurityLifecycleStatus.ABNORMAL,
        )
        for lifecycle_status in excluded_statuses:
            with self.subTest(lifecycle_status=lifecycle_status):
                value = ready_input()
                result = build_leader_tradability_features(
                    replace(
                        value,
                        lifecycle=replace(
                            value.lifecycle,
                            lifecycle_status=lifecycle_status,
                        ),
                    )
                )

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.READY,
                )
                self.assertFalse(result.research_eligible)
                self.assertIn(
                    f"lifecycle_excluded:{lifecycle_status.value}",
                    result.reasons,
                )

    def test_suspended_or_abnormal_session_is_explicit_exclusion(self):
        excluded_statuses = (
            TradingSessionStatus.SUSPENDED,
            TradingSessionStatus.ABNORMAL,
        )
        for session_status in excluded_statuses:
            with self.subTest(session_status=session_status):
                value = ready_input()
                result = build_leader_tradability_features(
                    replace(
                        value,
                        trading_status=replace(
                            value.trading_status,
                            status=session_status,
                        ),
                    )
                )

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.READY,
                )
                self.assertFalse(result.research_eligible)
                self.assertIn(
                    f"trading_status_excluded:{session_status.value}",
                    result.reasons,
                )

    def test_listing_trading_day_buckets_are_distinct(self):
        for count, eligible, preliminary_only in (
            (5, False, False),
            (6, True, True),
            (10, True, True),
            (11, True, False),
        ):
            with self.subTest(listed_trading_day_count=count):
                value = ready_input()
                result = build_leader_tradability_features(
                    replace(
                        value,
                        lifecycle=replace(
                            value.lifecycle,
                            listed_trading_day_count=count,
                        ),
                    )
                )

                self.assertEqual(result.research_eligible, eligible)
                self.assertEqual(
                    result.preliminary_only,
                    preliminary_only,
                )

    def test_bounded_limit_states_and_one_price_exclusions(self):
        cases = (
            (
                {
                    "price": 10.45,
                    "openPrice": 9.80,
                    "highPrice": 10.45,
                    "lowPrice": 9.70,
                },
                PriceLimitState.LIMIT_UP,
                OnePriceLimitState.NONE,
                True,
            ),
            (
                {
                    "price": 8.55,
                    "openPrice": 9.20,
                    "highPrice": 9.30,
                    "lowPrice": 8.55,
                },
                PriceLimitState.LIMIT_DOWN,
                OnePriceLimitState.NONE,
                True,
            ),
            (
                {
                    "price": 10.45,
                    "openPrice": 10.45,
                    "highPrice": 10.45,
                    "lowPrice": 10.45,
                },
                PriceLimitState.LIMIT_UP,
                OnePriceLimitState.ONE_PRICE_LIMIT_UP,
                False,
            ),
            (
                {
                    "price": 8.55,
                    "openPrice": 8.55,
                    "highPrice": 8.55,
                    "lowPrice": 8.55,
                },
                PriceLimitState.LIMIT_DOWN,
                OnePriceLimitState.ONE_PRICE_LIMIT_DOWN,
                False,
            ),
        )
        for quote_fields, limit_state, one_price_state, eligible in cases:
            with self.subTest(
                limit_state=limit_state,
                one_price_state=one_price_state,
            ):
                value = ready_input()
                result = build_leader_tradability_features(
                    replace(
                        value,
                        quote=quote(**quote_fields),
                    )
                )

                self.assertEqual(
                    result.price_limit_state,
                    limit_state,
                )
                self.assertEqual(
                    result.one_price_limit_state,
                    one_price_state,
                )
                self.assertEqual(result.research_eligible, eligible)

    def test_no_limit_day_is_real_rule_state(self):
        value = ready_input()
        result = build_leader_tradability_features(
            replace(
                value,
                trading_rule=replace(
                    value.trading_rule,
                    price_limit_mode=PriceLimitMode.NO_LIMIT,
                    upper_limit_price=None,
                    lower_limit_price=None,
                ),
            )
        )

        self.assertEqual(result.status, ResearchFeatureStatus.READY)
        self.assertTrue(result.research_eligible)
        self.assertEqual(
            result.price_limit_state,
            PriceLimitState.NO_LIMIT,
        )
        self.assertEqual(
            result.one_price_limit_state,
            OnePriceLimitState.NOT_APPLICABLE,
        )

    def test_source_failure_has_priority(self):
        value = ready_input()
        result = build_leader_tradability_features(
            replace(
                value,
                quote_source_status=(
                    ResearchFeatureStatus.SOURCE_FAILED
                ),
                lifecycle=replace(
                    value.lifecycle,
                    source_url="http://invalid.example/lifecycle",
                ),
            )
        )

        self.assertEqual(
            result.status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.reasons[0], "quote_source_failed")

    def test_invalid_evidence_has_stable_status_and_reason(self):
        value = ready_input()
        cases = (
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        symbol="000002",
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_symbol_mismatch",
            ),
            (
                replace(
                    value,
                    trading_status=replace(
                        value.trading_status,
                        trading_date=AS_OF.date() - timedelta(days=1),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_date_mismatch",
            ),
            (
                replace(
                    value,
                    trading_rule=replace(
                        value.trading_rule,
                        symbol="000002",
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_rule_symbol_mismatch",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        published_at=AS_OF + timedelta(seconds=6),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_published_in_future",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        effective_until=AS_OF - timedelta(seconds=1),
                    ),
                ),
                ResearchFeatureStatus.STALE,
                "lifecycle_evidence_expired",
            ),
            (
                replace(
                    value,
                    trading_rule=replace(
                        value.trading_rule,
                        effective_until=AS_OF - timedelta(seconds=1),
                    ),
                ),
                ResearchFeatureStatus.STALE,
                "trading_rule_expired",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        source_url="http://invalid.example/lifecycle",
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_source_url_unverified",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        source_url="https://",
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_source_url_unverified",
            ),
            (
                replace(
                    value,
                    trading_rule=replace(
                        value.trading_rule,
                        source_url="http://invalid.example/rule",
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_rule_source_url_unverified",
            ),
            (
                replace(
                    value,
                    quote_source_contract_id="",
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "tradability_source_identity_missing",
            ),
            (
                replace(
                    value,
                    trading_rule=replace(
                        value.trading_rule,
                        upper_limit_price=None,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "bounded_price_limit_missing",
            ),
            (
                replace(
                    value,
                    trading_rule=replace(
                        value.trading_rule,
                        price_limit_mode=PriceLimitMode.NO_LIMIT,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "no_limit_price_boundary_present",
            ),
            (
                replace(
                    value,
                    trading_status=replace(
                        value.trading_status,
                        source_time=AS_OF - timedelta(seconds=91),
                        fetched_at=AS_OF - timedelta(seconds=90),
                    ),
                ),
                ResearchFeatureStatus.STALE,
                "trading_status_stale",
            ),
            (
                replace(
                    value,
                    quote=quote(
                        sourceTime=AS_OF - timedelta(seconds=91),
                        fetchedAt=AS_OF - timedelta(seconds=90),
                    ),
                ),
                ResearchFeatureStatus.STALE,
                "quote_source_stale",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        effective_from=AS_OF + timedelta(days=1),
                        effective_until=AS_OF,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_effective_interval_invalid",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        listed_trading_day_count=-1,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "listed_trading_day_count_invalid",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        lifecycle_status=(
                            SecurityLifecycleStatus.UNKNOWN
                        ),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_status_unknown",
            ),
            (
                replace(
                    value,
                    trading_status=replace(
                        value.trading_status,
                        status=TradingSessionStatus.UNKNOWN,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_status_unknown",
            ),
            (
                replace(
                    value,
                    trading_rule=replace(
                        value.trading_rule,
                        price_limit_mode=PriceLimitMode.UNKNOWN,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "price_limit_mode_unknown",
            ),
            (
                replace(
                    value,
                    quote=quote(price=0),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_quote_zero_conflict",
            ),
            (
                replace(
                    value,
                    quote=quote(openPrice=None),
                ),
                ResearchFeatureStatus.MISSING,
                "quote_ohlc_missing",
            ),
            (
                replace(
                    value,
                    trading_status=replace(
                        value.trading_status,
                        source_time=AS_OF - timedelta(seconds=5),
                        fetched_at=AS_OF - timedelta(seconds=11),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_status_fetched_before_source",
            ),
            (
                replace(
                    value,
                    lifecycle=replace(
                        value.lifecycle,
                        fetched_at=AS_OF + timedelta(seconds=6),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "lifecycle_fetched_in_future",
            ),
            (
                replace(
                    value,
                    trading_status=replace(
                        value.trading_status,
                        fetched_at=AS_OF + timedelta(seconds=6),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "trading_status_fetched_in_future",
            ),
            (
                replace(
                    value,
                    quote=quote(
                        fetchedAt=AS_OF + timedelta(seconds=6),
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "quote_fetched_in_future",
            ),
            (
                replace(
                    value,
                    quote=quote(
                        highPrice=9.0,
                        lowPrice=10.0,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "quote_ohlc_inconsistent",
            ),
            (
                replace(
                    value,
                    quote=quote(
                        price=10.3,
                        highPrice=10.2,
                    ),
                ),
                ResearchFeatureStatus.SOURCE_UNVERIFIED,
                "quote_ohlc_inconsistent",
            ),
        )
        for item, expected_status, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_leader_tradability_features(item)

                self.assertEqual(result.status, expected_status)
                self.assertIn(expected_reason, result.reasons)
                self.assertIsNone(result.research_eligible)

    def test_missing_helper_preserves_unknown_semantics(self):
        result = missing_leader_tradability_features()
        evidence = result.to_evidence()

        self.assertEqual(result.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(result.research_eligible)
        self.assertEqual(
            result.price_limit_state,
            PriceLimitState.UNKNOWN,
        )
        self.assertFalse(evidence["formalUsable"])

    def test_ready_evidence_is_json_serializable(self):
        evidence = build_leader_tradability_features(
            ready_input()
        ).to_evidence()

        encoded = json.dumps(evidence, ensure_ascii=False)

        self.assertIn("security_lifecycle", encoded)
        self.assertNotIn("sourceFields", encoded)

    def test_raw_string_enums_are_rejected(self):
        value = ready_input()
        cases = (
            replace(value, quote_source_status="ready"),
            replace(
                value,
                lifecycle=replace(
                    value.lifecycle,
                    lifecycle_status="normal",
                ),
            ),
            replace(
                value,
                trading_status=replace(
                    value.trading_status,
                    status="trading",
                ),
            ),
            replace(
                value,
                trading_rule=replace(
                    value.trading_rule,
                    price_limit_mode="bounded",
                ),
            ),
        )
        for item in cases:
            with self.subTest(item=item):
                result = build_leader_tradability_features(item)

                self.assertEqual(
                    result.status,
                    ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
                self.assertEqual(
                    result.reasons,
                    ("tradability_enum_invalid",),
                )
                self.assertIsNone(result.research_eligible)


if __name__ == "__main__":
    unittest.main()
