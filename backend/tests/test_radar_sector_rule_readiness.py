import math
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import (
    IndustryClassificationRelease,
    IndustryHistoryStatus,
    SectorBreadthSnapshot,
    SectorConstituentCompleteness,
    SectorFeatureBatch,
    SectorFeatureSnapshot,
    SectorMetricValue,
    SectorReturnSnapshot,
    SectorTurnoverSnapshot,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.sector_rule_readiness import (
    SECTOR_RULE_VERSION,
    SectorHistoryCoverage,
    SectorHistoryCoverageEvidence,
    SectorMarketBaselineEvidence,
    SectorRuleReadinessStatus,
    SectorThresholdApprovalEvidence,
    evaluate_sector_rule_readiness,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 15, 10, 30, tzinfo=SHANGHAI_TZ)
DOCUMENT_SHA256 = "a" * 64


def trading_dates(count):
    values = []
    current = date(2026, 7, 1)
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def release(*, history_status=IndustryHistoryStatus.FORWARD_OBSERVED):
    return IndustryClassificationRelease(
        schemeVersion="capco-guideline-2023-shadow",
        releasePeriod="2025H2",
        sourcePageTitle="2025年下半年上市公司行业分类结果",
        publicationPageUrl="https://www.capco.org.cn/result.html",
        documentUrl="https://sp.capco.org.cn:82/result.pdf",
        documentSha256=DOCUMENT_SHA256,
        publishedDate=date(2026, 4, 3),
        firstObservedAt=datetime(
            2026,
            6,
            1,
            tzinfo=SHANGHAI_TZ,
        ),
        fetchedAt=datetime(2026, 6, 1, tzinfo=SHANGHAI_TZ),
        knowledgeEffectiveFrom=datetime(
            2026,
            6,
            1,
            tzinfo=SHANGHAI_TZ,
        ),
        classificationStartDate=date(2025, 12, 20),
        historyStatus=history_status,
        sourceRecordCount=40,
        uniqueSourceSymbolCount=40,
        requiredFieldCoverage={"division_code": 1.0},
    )


def metric(value):
    return SectorMetricValue(
        rawValue=value,
        available=True,
        formalUsable=False,
        reasons=("formal_use_not_approved",),
    )


def sector(index):
    division_code = f"{index + 10:02d}"
    symbols = (f"{index * 2 + 1:06d}", f"{index * 2 + 2:06d}")
    return SectorFeatureSnapshot(
        releasePeriod="2025H2",
        categoryCode=chr(ord("A") + index),
        categoryName=f"门类{index}",
        divisionCode=division_code,
        divisionName=f"行业{division_code}",
        constituentSymbols=symbols,
        completeness=SectorConstituentCompleteness(
            expectedCount=2,
            returnedCount=2,
            freshCount=2,
            validReturnCount=2,
            validMarketCapCount=2,
            validTurnoverCount=2,
            rowCoverage=1.0,
            requiredFieldCoverage={
                "change_percent": 1.0,
                "market_cap_source": 1.0,
                "turnover_amount_source": 1.0,
            },
            isComplete=True,
            reasons=(),
        ),
        returns=SectorReturnSnapshot(
            equalReturn=metric(1.0),
            capWeightedReturn=metric(1.1),
            exTopReturn=metric(0.9),
            topContributorSymbol=symbols[0],
            topContributionPercentPoints=0.6,
            marketCapUnitStatus=UnitVerificationStatus.VERIFIED,
            formalUsable=False,
            reasons=("formal_use_not_approved",),
        ),
        breadth=SectorBreadthSnapshot(
            advancers=1,
            decliners=1,
            flat=0,
            unavailable=0,
            upRatio=metric(0.5),
            formalUsable=False,
            reasons=("formal_use_not_approved",),
        ),
        turnover=SectorTurnoverSnapshot(
            rawValue=1_000_000.0 + index,
            contributingCount=2,
            unitStatus=UnitVerificationStatus.VERIFIED,
            available=True,
            formalUsable=False,
            reasons=("formal_use_not_approved",),
        ),
        shadowUsable=True,
        formalUsable=False,
        reasons=("formal_use_not_approved",),
    )


def feature_batch(*, mapping_coverage=1.0, unconfirmed_count=0):
    sectors = [sector(index) for index in range(20)]
    return SectorFeatureBatch(
        radarRunId="run-sector-readiness-1",
        classificationBatchId="classification-batch-1",
        quoteBatchId="quote-batch-1",
        releasePeriod="2025H2",
        classificationDocumentSha256=DOCUMENT_SHA256,
        asOf=AS_OF,
        sourceTime=AS_OF - timedelta(seconds=2),
        fetchedAt=AS_OF + timedelta(seconds=2),
        classificationMappingCoverage=mapping_coverage,
        mappedConstituentCount=40 - unconfirmed_count,
        unconfirmedStockCount=unconfirmed_count,
        sectors=sectors,
        excludedEtfCount=0,
        status=SourceStatus.DEGRADED,
        shadowUsable=True,
        formalUsable=False,
        reasons=("formal_use_not_approved",),
    )


def history_evidence(*, same_minute_count=20, persistence_count=5):
    rows = tuple(
        SectorHistoryCoverage(
            division_code=f"{index + 10:02d}",
            same_minute_trading_dates=trading_dates(same_minute_count),
            persistence_trading_dates=trading_dates(persistence_count),
        )
        for index in range(20)
    )
    return SectorHistoryCoverageEvidence(
        radar_run_id="run-sector-readiness-1",
        rule_version=SECTOR_RULE_VERSION,
        classification_document_sha256=DOCUMENT_SHA256,
        as_of=AS_OF,
        rows=rows,
        abnormal_day_filter_contract_id=(
            "radar-abnormal-trading-day-filter-v1"
        ),
    )


def market_baseline():
    return SectorMarketBaselineEvidence(
        radar_run_id="run-sector-readiness-1",
        as_of=AS_OF,
        equal_weighted_return=0.4,
        market_cap_weighted_return=0.3,
        source_batch_ids=("market-quote-batch-1",),
    )


def threshold_approval():
    return SectorThresholdApprovalEvidence(
        rule_version=SECTOR_RULE_VERSION,
        threshold_set_id="sector-threshold-set-reviewed-v1",
        approval_id="user-approval-20260814-1",
        approved_at=AS_OF - timedelta(days=1),
        state_ids=(
            "observe",
            "startup",
            "confirmed",
            "accelerating",
            "divergence",
            "reflow",
            "retreat",
            "invalid",
        ),
        policy_fields=(
            "entry",
            "hold",
            "exit",
            "consecutive_observations",
            "minimum_hold_time",
            "cooldown",
            "data_failure_behavior",
        ),
    )


class SectorRuleReadinessTests(unittest.TestCase):
    def test_all_required_inputs_return_only_ready_contract(self):
        result = evaluate_sector_rule_readiness(
            feature_batch=feature_batch(),
            classification_release=release(),
            history_evidence=history_evidence(),
            market_baseline_evidence=market_baseline(),
            threshold_approval_evidence=threshold_approval(),
        )

        self.assertEqual(result.status, SectorRuleReadinessStatus.READY)
        self.assertEqual(result.reasons, ())
        self.assertTrue(all(
            item.status == SectorRuleReadinessStatus.READY
            for item in result.items
        ))
        evidence = result.to_evidence()
        self.assertEqual(
            evidence["contractId"],
            "radar-sector-rule-readiness-v1",
        )
        self.assertEqual(evidence["status"], "ready")
        self.assertNotIn("score", evidence)
        self.assertNotIn("state", evidence)
        self.assertNotIn("formalUsable", evidence)

    def test_current_known_gaps_remain_missing_with_stable_reasons(self):
        result = evaluate_sector_rule_readiness(
            feature_batch=feature_batch(
                mapping_coverage=39 / 40,
                unconfirmed_count=1,
            ),
            classification_release=release(
                history_status=(
                    IndustryHistoryStatus.RETROSPECTIVE_UNVERIFIED
                ),
            ),
        )

        self.assertEqual(result.status, SectorRuleReadinessStatus.MISSING)
        self.assertEqual(
            result.reasons,
            (
                "sector_classification_history_unverified",
                "sector_classification_mapping_incomplete",
                "sector_market_baseline_missing",
                "sector_history_coverage_missing",
                "sector_same_minute_turnover_history_missing",
                "sector_persistence_history_missing",
                "sector_comparable_industry_count_below_20",
                "sector_state_threshold_approval_missing",
            ),
        )

    def test_history_below_minimum_blocks_both_core_dimensions(self):
        result = evaluate_sector_rule_readiness(
            feature_batch=feature_batch(),
            classification_release=release(),
            history_evidence=history_evidence(
                same_minute_count=19,
                persistence_count=4,
            ),
            market_baseline_evidence=market_baseline(),
            threshold_approval_evidence=threshold_approval(),
        )

        self.assertEqual(result.status, SectorRuleReadinessStatus.MISSING)
        self.assertEqual(
            result.item("same_minute_turnover_history").reasons,
            ("sector_same_minute_turnover_samples_below_20",),
        )
        self.assertEqual(
            result.item("persistence_history").reasons,
            ("sector_persistence_completed_days_below_5",),
        )
        self.assertEqual(
            result.item("comparable_industries").reasons,
            ("sector_comparable_industry_count_below_20",),
        )

    def test_cross_batch_or_rule_evidence_never_becomes_ready(self):
        result = evaluate_sector_rule_readiness(
            feature_batch=feature_batch(),
            classification_release=release(),
            history_evidence=replace(
                history_evidence(),
                classification_document_sha256="b" * 64,
            ),
            market_baseline_evidence=replace(
                market_baseline(),
                radar_run_id="different-run",
            ),
            threshold_approval_evidence=replace(
                threshold_approval(),
                rule_version="different-rule",
            ),
        )

        self.assertEqual(result.status, SectorRuleReadinessStatus.MISSING)
        self.assertEqual(
            result.item("market_baseline").reasons,
            ("sector_market_baseline_identity_mismatch",),
        )
        self.assertEqual(
            result.item("same_minute_turnover_history").reasons,
            ("sector_history_identity_mismatch",),
        )
        self.assertEqual(
            result.item("threshold_approval").reasons,
            ("sector_state_threshold_rule_version_mismatch",),
        )

    def test_illegal_evidence_is_rejected_before_evaluation(self):
        with self.assertRaisesRegex(ValueError, "不能重复"):
            SectorHistoryCoverage(
                division_code="10",
                same_minute_trading_dates=(date(2026, 7, 1),) * 2,
                persistence_trading_dates=trading_dates(5),
            )
        with self.assertRaisesRegex(ValueError, "有限数值"):
            SectorMarketBaselineEvidence(
                radar_run_id="run-sector-readiness-1",
                as_of=AS_OF,
                equal_weighted_return=math.nan,
                market_cap_weighted_return=0.3,
                source_batch_ids=("market-quote-batch-1",),
            )
        with self.assertRaisesRegex(ValueError, "时区"):
            SectorThresholdApprovalEvidence(
                rule_version=SECTOR_RULE_VERSION,
                threshold_set_id="set-1",
                approval_id="approval-1",
                approved_at=datetime(2026, 8, 14),
                state_ids=(
                    "observe",
                    "startup",
                    "confirmed",
                    "accelerating",
                    "divergence",
                    "reflow",
                    "retreat",
                    "invalid",
                ),
                policy_fields=(
                    "entry",
                    "hold",
                    "exit",
                    "consecutive_observations",
                    "minimum_hold_time",
                    "cooldown",
                    "data_failure_behavior",
                ),
            )


if __name__ == "__main__":
    unittest.main()
