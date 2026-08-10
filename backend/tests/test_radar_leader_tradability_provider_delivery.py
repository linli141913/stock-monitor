import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_provider_delivery import (
    ProviderAdmissionPolicy,
    ProviderDeliveryGrade,
    ProviderLicenseEvidence,
    ProviderLicenseUsageScope,
    ProviderSourceObservation,
    ProviderSourceProvenance,
    ProviderTradabilityBatchInput,
    ProviderTradabilityBatchStatus,
    ProviderTradabilityDeliveryRecord,
    audit_provider_tradability_batch,
)
from radar.leader_tradability_sources import (
    PriceLimitSpecialSession,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ)


def admission_policy(**overrides):
    values = {
        "policy_id": "rqdata-c4-poc-admission-v1",
        "provider_id": "rqdata",
        "provider_name": "RQData",
        "provider_origins": ("https://www.ricequant.com",),
        "delivery_contract_ids": ("rqdata-c4-poc-v1",),
        "upstream_source_contract_ids": (
            "exchange-reference-v1",
        ),
        "upstream_source_names": (
            "上海证券交易所",
            "深圳证券交易所",
        ),
        "upstream_origins": (
            "https://www.sse.com.cn",
            "https://www.szse.cn",
        ),
        "license_document_ids": (
            "rqdata-trial-license-202608",
        ),
        "license_origins": ("https://www.ricequant.com",),
        "usage_scope": (
            ProviderLicenseUsageScope.PERSONAL_SERVER_PROCESSING
        ),
        "valid_from": date(2026, 8, 1),
        "valid_until": date(2026, 8, 31),
    }
    values.update(overrides)
    return ProviderAdmissionPolicy(**values)


def license_evidence(**overrides):
    values = {
        "provider_id": "rqdata",
        "license_document_id": "rqdata-trial-license-202608",
        "license_url": "https://www.ricequant.com/welcome/rqdata",
        "usage_scope": (
            ProviderLicenseUsageScope.PERSONAL_SERVER_PROCESSING
        ),
        "valid_from": date(2026, 8, 1),
        "valid_until": date(2026, 8, 31),
        "permits_local_processing": True,
    }
    values.update(overrides)
    return ProviderLicenseEvidence(**values)


def provenance(**overrides):
    values = {
        "provider_id": "rqdata",
        "provider_name": "RQData",
        "provider_url": "https://www.ricequant.com/welcome/rqdata",
        "delivery_grade": (
            ProviderDeliveryGrade.AUTHORIZED_PROVIDER_CANDIDATE
        ),
        "delivery_contract_id": "rqdata-c4-poc-v1",
        "delivery_batch_id": "rqdata-20260803-100000",
    }
    values.update(overrides)
    return ProviderSourceProvenance(**values)


def observation(symbol, *, dynamic, **overrides):
    is_sse = symbol.startswith("6")
    values = {
        "symbol": symbol,
        "trading_date": AS_OF.date(),
        "source_contract_id": "exchange-reference-v1",
        "source_name": (
            "上海证券交易所" if is_sse else "深圳证券交易所"
        ),
        "source_url": (
            "https://www.sse.com.cn/services/tradingtech/"
            if is_sse
            else "https://www.szse.cn/marketServices/technicalservice/"
        ),
        "document_id": (
            "dynamic-status" if dynamic else "daily-static-reference"
        ) + ":" + symbol + ":20260803",
        "source_time": (
            AS_OF - timedelta(seconds=20)
            if dynamic
            else AS_OF.replace(hour=8, minute=30)
        ),
        "content_digest": "sha256:" + ("d" if dynamic else "a") * 64,
    }
    values.update(overrides)
    return ProviderSourceObservation(**values)


def record(symbol="600000", **overrides):
    exchange = "sse" if symbol.startswith("6") else "szse"
    board = "主板A股" if exchange == "sse" else "主板"
    values = {
        "symbol": symbol,
        "exchange": exchange,
        "board": board,
        "trading_date": AS_OF.date(),
        "lifecycle_status": SecurityLifecycleStatus.NORMAL,
        "trading_status": TradingSessionStatus.TRADING,
        "special_session": PriceLimitSpecialSession.NONE,
        "price_limit_mode": PriceLimitMode.BOUNDED,
        "upper_limit_price": 11.0,
        "lower_limit_price": 9.0,
        "static_observation": observation(
            symbol,
            dynamic=False,
        ),
        "dynamic_observation": observation(
            symbol,
            dynamic=True,
        ),
        "delivered_at": AS_OF - timedelta(seconds=10),
        "provenance": provenance(),
        "license_evidence": license_evidence(),
    }
    values.update(overrides)
    return ProviderTradabilityDeliveryRecord(**values)


def batch(records=None, expected_symbols=None, **overrides):
    expected = expected_symbols or ("600000", "000001")
    rows = records or tuple(record(symbol) for symbol in expected)
    values = {
        "radar_run_id": "radar-run-20260803-100000",
        "as_of": AS_OF,
        "trading_date": AS_OF.date(),
        "provider_id": "rqdata",
        "delivery_contract_id": "rqdata-c4-poc-v1",
        "delivery_batch_id": "rqdata-20260803-100000",
        "expected_symbols": expected,
        "records": rows,
    }
    values.update(overrides)
    return ProviderTradabilityBatchInput(**values)


def audit(batch_input, policy=None):
    return audit_provider_tradability_batch(
        batch_input,
        admission_policy=policy or admission_policy(),
    )


class ProviderTradabilityDeliveryTests(unittest.TestCase):
    def test_complete_batch_is_only_an_admissible_candidate(self):
        result = audit(batch())

        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.ADMISSIBLE_CANDIDATE,
        )
        self.assertEqual(result.expected_count, 2)
        self.assertEqual(result.received_count, 2)
        self.assertEqual(result.missing_symbols, ())
        self.assertEqual(result.duplicate_symbols, ())
        self.assertEqual(result.extra_symbols, ())
        self.assertTrue(result.batch_id.startswith("sha256:"))
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

        evidence = result.to_evidence()
        self.assertEqual(
            evidence["status"],
            "admissible_candidate",
        )
        self.assertNotIn("records", evidence)
        self.assertNotIn("license_document_id", repr(result))

        with self.assertRaises(FrozenInstanceError):
            result.status = ProviderTradabilityBatchStatus.BLOCKED

    def test_static_reference_can_precede_ninety_seconds_but_dynamic_cannot(self):
        old_static = record(
            "600000",
            static_observation=observation(
                "600000",
                dynamic=False,
                source_time=AS_OF - timedelta(hours=2),
            ),
        )
        fresh = audit(
            batch(records=(old_static, record("000001")))
        )
        self.assertEqual(
            fresh.status,
            ProviderTradabilityBatchStatus.ADMISSIBLE_CANDIDATE,
        )

        stale_dynamic = record(
            "600000",
            dynamic_observation=observation(
                "600000",
                dynamic=True,
                source_time=AS_OF - timedelta(seconds=91),
            ),
            delivered_at=AS_OF - timedelta(seconds=5),
        )
        stale = audit(
            batch(records=(stale_dynamic, record("000001")))
        )
        self.assertEqual(
            stale.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn("provider_dynamic_status_stale", stale.reasons)

    def test_missing_duplicate_and_extra_symbols_block_whole_batch(self):
        rows = (
            record("600000"),
            record("600000"),
            record("300001"),
        )
        result = audit(
            batch(records=rows, expected_symbols=("600000", "000001"))
        )

        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertEqual(result.missing_symbols, ("000001",))
        self.assertEqual(result.duplicate_symbols, ("600000",))
        self.assertEqual(result.extra_symbols, ("300001",))
        self.assertIn("provider_batch_coverage_incomplete", result.reasons)

    def test_mixed_date_and_delivery_identity_mismatch_are_rejected(self):
        wrong_date = record(
            "600000",
            trading_date=date(2026, 7, 31),
        )
        wrong_batch = record(
            "000001",
            provenance=provenance(
                delivery_batch_id="rqdata-other-batch"
            ),
        )
        result = audit(
            batch(records=(wrong_date, wrong_batch))
        )

        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn(
            "provider_record_trading_date_mismatch",
            result.reasons,
        )
        self.assertIn(
            "provider_delivery_batch_mismatch",
            result.reasons,
        )

    def test_license_and_provenance_must_be_explicit_and_current(self):
        cases = (
            (
                record(
                    license_evidence=license_evidence(
                        valid_until=date(2026, 8, 2)
                    )
                ),
                "provider_license_not_effective",
            ),
            (
                record(
                    license_evidence=license_evidence(
                        usage_scope=(
                            ProviderLicenseUsageScope.RESEARCH_ONLY
                        )
                    )
                ),
                "provider_license_scope_not_permitted",
            ),
            (
                record(
                    static_observation=observation(
                        "600000",
                        dynamic=False,
                        source_url="http://example.com/source",
                    )
                ),
                "provider_source_provenance_unverified",
            ),
        )
        for invalid, reason in cases:
            with self.subTest(reason=reason):
                result = audit(
                    batch(records=(invalid, record("000001")))
                )
                self.assertEqual(
                    result.status,
                    ProviderTradabilityBatchStatus.BLOCKED,
                )
                self.assertIn(reason, result.reasons)

    def test_price_mode_and_known_statuses_are_required(self):
        cases = (
            (
                record(
                    price_limit_mode=PriceLimitMode.NO_LIMIT,
                    upper_limit_price=11.0,
                    lower_limit_price=None,
                ),
                "provider_price_limit_payload_invalid",
            ),
            (
                record(
                    price_limit_mode=PriceLimitMode.BOUNDED,
                    upper_limit_price=None,
                    lower_limit_price=None,
                ),
                "provider_price_limit_payload_invalid",
            ),
            (
                record(
                    trading_status=TradingSessionStatus.UNKNOWN,
                ),
                "provider_record_enum_unverified",
            ),
            (
                record(
                    lifecycle_status=SecurityLifecycleStatus.UNKNOWN,
                ),
                "provider_record_enum_unverified",
            ),
        )
        for invalid, reason in cases:
            with self.subTest(reason=reason):
                result = audit(
                    batch(records=(invalid, record("000001")))
                )
                self.assertEqual(
                    result.status,
                    ProviderTradabilityBatchStatus.BLOCKED,
                )
                self.assertIn(reason, result.reasons)

    def test_future_and_reversed_timestamps_are_rejected(self):
        cases = (
            (
                record(
                    dynamic_observation=observation(
                        "600000",
                        dynamic=True,
                        source_time=AS_OF + timedelta(seconds=6),
                    ),
                    delivered_at=AS_OF + timedelta(seconds=6),
                ),
                "provider_dynamic_status_from_future",
            ),
            (
                record(
                    delivered_at=AS_OF - timedelta(minutes=10),
                ),
                "provider_delivery_before_source",
            ),
            (
                record(
                    static_observation=observation(
                        "600000",
                        dynamic=False,
                        source_time=AS_OF + timedelta(seconds=6),
                    ),
                    delivered_at=AS_OF + timedelta(seconds=6),
                ),
                "provider_static_reference_from_future",
            ),
        )
        for invalid, reason in cases:
            with self.subTest(reason=reason):
                result = audit(
                    batch(records=(invalid, record("000001")))
                )
                self.assertEqual(
                    result.status,
                    ProviderTradabilityBatchStatus.BLOCKED,
                )
                self.assertIn(reason, result.reasons)

    def test_admission_policy_is_separate_from_provider_claims(self):
        result = audit(
            batch(),
            policy=admission_policy(
                provider_id="unapproved-provider"
            ),
        )
        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn(
            "provider_admission_policy_mismatch",
            result.reasons,
        )

        absent = audit_provider_tradability_batch(
            batch(),
            admission_policy=None,
        )
        self.assertEqual(
            absent.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn(
            "provider_admission_policy_unverified",
            absent.reasons,
        )

    def test_static_and_dynamic_observations_bind_identity_and_date(self):
        invalid = record(
            "600000",
            static_observation=observation(
                "000001",
                dynamic=False,
                trading_date=date(2026, 7, 31),
            ),
        )
        result = audit(
            batch(records=(invalid, record("000001")))
        )
        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn(
            "provider_source_observation_identity_mismatch",
            result.reasons,
        )

    def test_invalid_runtime_types_return_stable_blocked_result(self):
        invalid_license = record(
            "600000",
            license_evidence=license_evidence(
                valid_until="2026-08-31"
            ),
        )
        result = audit(
            batch(records=(invalid_license, record("000001")))
        )
        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn("provider_license_unverified", result.reasons)

        invalid_record = audit(
            batch(records=("not-a-record",))
        )
        self.assertEqual(
            invalid_record.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn(
            "provider_record_contract_unverified",
            invalid_record.reasons,
        )
        self.assertIsNone(invalid_record.batch_id)

    def test_credential_bearing_urls_are_rejected_and_hidden(self):
        evidence = license_evidence(
            license_url=(
                "https://user:secret@www.ricequant.com/license"
            )
        )
        invalid = record("600000", license_evidence=evidence)
        result = audit(
            batch(records=(invalid, record("000001")))
        )
        self.assertEqual(
            result.status,
            ProviderTradabilityBatchStatus.BLOCKED,
        )
        self.assertIn("provider_license_unverified", result.reasons)
        self.assertNotIn("secret", repr(evidence))
        self.assertNotIn("secret", repr(invalid.provenance))

    def test_batch_id_binds_business_and_source_content(self):
        baseline = audit(batch())
        changed_price = audit(batch(records=(
            record("600000", upper_limit_price=12.0),
            record("000001"),
        )))
        changed_source = audit(batch(records=(
            record(
                "600000",
                static_observation=observation(
                    "600000",
                    dynamic=False,
                    content_digest="sha256:" + "b" * 64,
                ),
            ),
            record("000001"),
        )))

        self.assertNotEqual(baseline.batch_id, changed_price.batch_id)
        self.assertNotEqual(baseline.batch_id, changed_source.batch_id)


if __name__ == "__main__":
    unittest.main()
