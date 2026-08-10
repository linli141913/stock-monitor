import dataclasses
import unittest
from datetime import date, datetime, timezone

from radar.leader_business_catalyst_features import (
    BusinessCatalystRelation,
    LeaderBusinessCatalystFeatureResult,
)
from radar.leader_history_features import LeaderHistoryFeatureResult
from radar.leader_liquidity_features import LeaderLiquidityFeatureResult
from radar.leader_research_features import (
    LeaderResearchDimension,
    LeaderResearchFeatureResult,
    ResearchFeatureStatus,
)
from radar.leader_research_readiness_audit import (
    LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID,
    LeaderResearchReadinessAuditInput,
    LeaderResearchReadinessAuditStatus,
    build_leader_research_readiness_audit,
)
from radar.leader_risk_candidate_projection import (
    LeaderRiskCandidateProjection,
)
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchItem,
)
from radar.leader_risk_document_facts import RiskDocumentRelationKind
from radar.leader_risk_evidence_bundle import FormalRiskGateGap
from radar.leader_risk_invalidation_features import RiskOfficialStatus
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureResult,
    OnePriceLimitState,
    PriceLimitState,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)


AS_OF = datetime(2026, 7, 30, 6, 30, tzinfo=timezone.utc)
ITEM_KEYS = (
    "industry_strength",
    "security_tradability",
    "market_leadership",
    "liquidity",
    "history_continuity",
    "business_catalyst",
    "risk_projection",
)


def research_dimension(
    field_name,
    *,
    status=ResearchFeatureStatus.READY,
    score=0.0,
    reasons=(),
):
    return LeaderResearchDimension(
        field_name=field_name,
        maximum_score=25.0,
        research_score=score if status == ResearchFeatureStatus.READY else None,
        status=status,
        source_contract_ids=(f"{field_name}-source-v1",),
        reasons=reasons,
    )


def cross_sectional_features(
    *,
    industry_status=ResearchFeatureStatus.READY,
    market_status=ResearchFeatureStatus.READY,
):
    return LeaderResearchFeatureResult(dimensions=(
        research_dimension(
            "industry_strength",
            status=industry_status,
            reasons=(
                ()
                if industry_status == ResearchFeatureStatus.READY
                else ("industry_source_unavailable",)
            ),
        ),
        research_dimension(
            "market_leadership",
            status=market_status,
            reasons=(
                ()
                if market_status == ResearchFeatureStatus.READY
                else ("market_source_unavailable",)
            ),
        ),
    ))


def history_features(
    *,
    status=ResearchFeatureStatus.READY,
    reasons=(),
):
    return LeaderHistoryFeatureResult(
        status=status,
        reasons=reasons,
        source_contract_ids=("history-source-v1",),
        history_end_date=date(2026, 7, 30),
        observation_count=21,
        metrics={"excessReturns": {"vsIndustry": {"last5": 0.0}}},
    )


def liquidity_features(
    *,
    status=ResearchFeatureStatus.READY,
    reasons=(),
):
    return LeaderLiquidityFeatureResult(
        status=status,
        reasons=reasons,
        source_contract_ids=("liquidity-source-v1",),
    )


def business_features(
    *,
    status=ResearchFeatureStatus.READY,
    relation=BusinessCatalystRelation.DIRECT,
    reasons=(),
):
    return LeaderBusinessCatalystFeatureResult(
        status=status,
        relation=relation,
        reasons=reasons,
    )


def tradability_features(
    *,
    status=ResearchFeatureStatus.READY,
    research_eligible=True,
    preliminary_only=False,
    reasons=(),
):
    return LeaderTradabilityFeatureResult(
        status=status,
        lifecycle_status=SecurityLifecycleStatus.NORMAL,
        trading_status=TradingSessionStatus.TRADING,
        price_limit_state=PriceLimitState.NORMAL,
        one_price_limit_state=OnePriceLimitState.NONE,
        research_eligible=research_eligible,
        preliminary_only=preliminary_only,
        reasons=reasons,
    )


def risk_projection(
    *,
    symbol="000725",
    as_of=AS_OF,
    formal_gate_gaps=(
        FormalRiskGateGap.SOURCE_DOCUMENT_NOT_FORMAL,
    ),
):
    return LeaderRiskCandidateProjection(
        symbol=symbol,
        issuer_identity="issuer-000725",
        as_of=as_of,
        bundle_ids=("bundle-v1", "bundle-v2"),
        current_bundle_id="bundle-v2",
        current_bundle_built_at=as_of,
        document_source_contract_id="document-source-v1",
        document_id="document-1",
        content_contract_id="content-v1",
        content_sha256="a" * 64,
        content_fetched_at=as_of,
        deterministic_fact_ids=("fact-1",),
        manual_fact_ids=(),
        merged_fact_ids=("fact-1",),
        active_artifact_id="artifact-1",
        active_artifact_version="v1",
        artifact_contract_version="artifact-contract-v1",
        replay_version="replay-v1",
        relation_id="relation-1",
        review_id="review-1",
        mapping_version="mapping-v1",
        relation_kind=RiskDocumentRelationKind.RESOLVES,
        target_event_id="event-1",
        target_event_version="event-v1",
        target_document_id="target-document-1",
        replacement_event_version=None,
        basis_fact_ids=("fact-1",),
        manual_basis_fact_ids=(),
        target_event_official_status=RiskOfficialStatus.ACTIVE,
        target_event_published_at=as_of,
        formal_gate_gaps=formal_gate_gaps,
        version_diffs=(),
    )


def risk_item(
    *,
    symbol="000725",
    status=ResearchFeatureStatus.READY,
    reasons=(),
    projection=None,
):
    if projection is None and status == ResearchFeatureStatus.READY:
        projection = risk_projection(symbol=symbol)
    return LeaderRiskCandidateProjectionBatchItem(
        index=0,
        symbol=symbol,
        input_symbol=symbol,
        status=status,
        reasons=reasons,
        projection=projection,
    )


def audit_input(**overrides):
    values = {
        "symbol": "000725",
        "as_of": AS_OF,
        "cross_sectional_features": cross_sectional_features(),
        "history_features": history_features(),
        "liquidity_features": liquidity_features(),
        "business_catalyst_features": business_features(),
        "tradability_features": tradability_features(),
        "risk_projection_item": risk_item(),
    }
    values.update(overrides)
    return LeaderResearchReadinessAuditInput(**values)


class LeaderResearchReadinessAuditTests(unittest.TestCase):
    def test_ready_audit_keeps_fixed_order_and_formal_gates_closed(self):
        result = build_leader_research_readiness_audit(audit_input())

        self.assertEqual(result.status, LeaderResearchReadinessAuditStatus.READY)
        self.assertEqual(tuple(item.key for item in result.items), ITEM_KEYS)
        self.assertEqual(result.required_item_count, 7)
        self.assertEqual(result.evidence_available_count, 7)
        self.assertEqual(result.satisfied_count, 6)
        self.assertEqual(result.missing_items, ())
        self.assertEqual(result.blocked_items, ("risk_projection",))
        self.assertEqual(
            result.first_veto_reason,
            "leader_formal_industry_gate_unavailable",
        )
        self.assertEqual(
            result.first_research_blocker_reason,
            "leader_formal_risk_gate_unavailable",
        )
        self.assertEqual(
            result.ordered_blocker_codes[0],
            "leader_formal_industry_gate_unavailable",
        )
        self.assertNotIn(
            "risk_filter_failed",
            result.ordered_blocker_codes,
        )
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

        evidence = result.to_evidence()
        self.assertEqual(
            evidence["contractId"],
            LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID,
        )
        self.assertNotIn("researchScore", evidence)
        self.assertEqual(
            evidence["gate"],
            {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        )

    def test_true_zero_cross_section_score_is_available(self):
        result = build_leader_research_readiness_audit(audit_input())

        industry = result.item("industry_strength")
        market = result.item("market_leadership")
        self.assertTrue(industry.evidence_available)
        self.assertTrue(market.evidence_available)
        self.assertNotIn("industry_strength", result.missing_items)
        self.assertNotIn("market_leadership", result.missing_items)

    def test_industry_gap_precedes_later_research_gaps(self):
        result = build_leader_research_readiness_audit(audit_input(
            cross_sectional_features=cross_sectional_features(
                industry_status=ResearchFeatureStatus.STALE,
            ),
            liquidity_features=liquidity_features(
                status=ResearchFeatureStatus.SOURCE_FAILED,
                reasons=("liquidity_source_failed",),
            ),
        ))

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditStatus.PARTIAL,
        )
        self.assertEqual(
            result.first_research_blocker_reason,
            "leader_cross_section_evidence_unavailable",
        )
        self.assertEqual(result.missing_items[0], "industry_strength")

    def test_explicit_tradability_exclusion_is_complete_negative_evidence(self):
        result = build_leader_research_readiness_audit(audit_input(
            tradability_features=tradability_features(
                research_eligible=False,
                reasons=("lifecycle_excluded:st",),
            ),
        ))

        self.assertEqual(result.status, LeaderResearchReadinessAuditStatus.READY)
        self.assertEqual(result.evidence_available_count, 7)
        self.assertEqual(
            result.first_research_blocker_reason,
            "leader_formal_stock_gate_failed",
        )
        self.assertIn("security_tradability", result.blocked_items)

    def test_preliminary_only_is_a_stock_gate_limitation(self):
        result = build_leader_research_readiness_audit(audit_input(
            tradability_features=tradability_features(
                preliminary_only=True,
                reasons=("new_listing_six_to_ten_trading_days",),
            ),
        ))

        self.assertEqual(result.status, LeaderResearchReadinessAuditStatus.READY)
        self.assertEqual(
            result.first_research_blocker_reason,
            "leader_formal_stock_gate_failed",
        )

    def test_market_liquidity_and_history_use_fixed_priority(self):
        market_result = build_leader_research_readiness_audit(audit_input(
            cross_sectional_features=cross_sectional_features(
                market_status=ResearchFeatureStatus.STALE,
            ),
            liquidity_features=liquidity_features(
                status=ResearchFeatureStatus.SOURCE_FAILED,
            ),
            history_features=history_features(
                status=ResearchFeatureStatus.MISSING,
            ),
        ))
        self.assertEqual(
            market_result.first_research_blocker_reason,
            "leader_cross_section_evidence_unavailable",
        )

        liquidity_result = build_leader_research_readiness_audit(audit_input(
            liquidity_features=liquidity_features(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("same_time_turnover_history_missing",),
            ),
            history_features=history_features(
                status=ResearchFeatureStatus.MISSING,
            ),
        ))
        self.assertEqual(
            liquidity_result.first_research_blocker_reason,
            "leader_liquidity_evidence_unavailable",
        )

        history_result = build_leader_research_readiness_audit(audit_input(
            history_features=history_features(
                status=ResearchFeatureStatus.MISSING,
                reasons=("continuity_history_missing",),
            ),
        ))
        self.assertEqual(
            history_result.first_research_blocker_reason,
            "leader_history_evidence_unavailable",
        )

    def test_recovery_absence_is_complete_but_blocks_research_progression(self):
        result = build_leader_research_readiness_audit(audit_input(
            history_features=history_features(
                reasons=("recovery_event_absent",),
            ),
        ))

        self.assertEqual(result.status, LeaderResearchReadinessAuditStatus.READY)
        self.assertEqual(
            result.first_research_blocker_reason,
            "leader_history_recovery_evidence_absent",
        )
        self.assertIn("history_continuity", result.blocked_items)

    def test_business_unconfirmed_and_disproved_are_distinct(self):
        unconfirmed = build_leader_research_readiness_audit(audit_input(
            business_catalyst_features=business_features(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                relation=BusinessCatalystRelation.UNCONFIRMED,
                reasons=("business_relation_unconfirmed",),
            ),
        ))
        self.assertEqual(
            unconfirmed.status,
            LeaderResearchReadinessAuditStatus.PARTIAL,
        )
        self.assertEqual(
            unconfirmed.first_research_blocker_reason,
            "leader_business_exposure_unconfirmed",
        )

        disproved = build_leader_research_readiness_audit(audit_input(
            business_catalyst_features=business_features(
                relation=BusinessCatalystRelation.DISPROVED,
            ),
        ))
        self.assertEqual(
            disproved.status,
            LeaderResearchReadinessAuditStatus.READY,
        )
        self.assertEqual(
            disproved.first_research_blocker_reason,
            "leader_business_exposure_disproved",
        )
        self.assertIn("business_catalyst", disproved.blocked_items)

    def test_missing_risk_item_is_partial_and_preserves_source_reason(self):
        result = build_leader_research_readiness_audit(audit_input(
            risk_projection_item=risk_item(
                status=ResearchFeatureStatus.SOURCE_FAILED,
                reasons=("risk_projection_recompute_failed",),
            ),
        ))

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditStatus.PARTIAL,
        )
        self.assertEqual(
            result.first_research_blocker_reason,
            "leader_risk_evidence_unavailable",
        )
        self.assertEqual(
            result.item("risk_projection").reasons,
            ("risk_projection_recompute_failed",),
        )

    def test_risk_identity_as_of_contract_and_formal_flag_forgery_block_audit(self):
        identity = build_leader_research_readiness_audit(audit_input(
            risk_projection_item=risk_item(symbol="000519"),
        ))
        self.assertEqual(
            identity.status,
            LeaderResearchReadinessAuditStatus.BLOCKED,
        )
        self.assertEqual(
            identity.first_veto_reason,
            "leader_research_audit_identity_mismatch",
        )

        as_of = build_leader_research_readiness_audit(audit_input(
            risk_projection_item=risk_item(
                projection=risk_projection(
                    as_of=AS_OF.replace(hour=7),
                ),
            ),
        ))
        self.assertEqual(
            as_of.first_veto_reason,
            "leader_research_audit_as_of_mismatch",
        )

        projection = dataclasses.replace(
            risk_projection(),
            projection_contract_id="forged-contract",
        )
        contract = build_leader_research_readiness_audit(audit_input(
            risk_projection_item=risk_item(projection=projection),
        ))
        self.assertEqual(
            contract.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

        forged_item = dataclasses.replace(
            risk_item(),
            formal_gate_ready=True,
        )
        formal_flag = build_leader_research_readiness_audit(audit_input(
            risk_projection_item=forged_item,
        ))
        self.assertEqual(
            formal_flag.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

    def test_outer_contract_and_required_dimensions_are_blocked(self):
        malformed = build_leader_research_readiness_audit(object())
        self.assertEqual(
            malformed.status,
            LeaderResearchReadinessAuditStatus.BLOCKED,
        )
        self.assertEqual(
            malformed.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

        naive = build_leader_research_readiness_audit(audit_input(
            as_of=AS_OF.replace(tzinfo=None),
        ))
        self.assertEqual(
            naive.first_veto_reason,
            "leader_research_audit_as_of_mismatch",
        )

        dimensions = LeaderResearchFeatureResult(dimensions=(
            research_dimension("market_leadership"),
        ))
        missing_dimension = build_leader_research_readiness_audit(
            audit_input(cross_sectional_features=dimensions)
        )
        self.assertEqual(
            missing_dimension.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

    def test_forged_nested_types_and_ready_risk_reasons_are_blocked(self):
        malformed_symbol = dataclasses.replace(
            audit_input(),
            symbol=None,
        )
        symbol_result = build_leader_research_readiness_audit(
            malformed_symbol
        )
        self.assertEqual(
            symbol_result.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

        malformed_tradability = dataclasses.replace(
            tradability_features(),
            lifecycle_status="normal",
        )
        tradability_result = build_leader_research_readiness_audit(
            audit_input(
                tradability_features=malformed_tradability,
            )
        )
        self.assertEqual(
            tradability_result.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

        malformed_business = business_features(
            status=ResearchFeatureStatus.MISSING,
            relation=BusinessCatalystRelation.DISPROVED,
        )
        business_result = build_leader_research_readiness_audit(
            audit_input(
                business_catalyst_features=malformed_business,
            )
        )
        self.assertEqual(
            business_result.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

        history_without_source = dataclasses.replace(
            history_features(),
            source_contract_ids=(),
        )
        history_result = build_leader_research_readiness_audit(audit_input(
            history_features=history_without_source,
        ))
        self.assertEqual(
            history_result.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

        ready_with_reasons = dataclasses.replace(
            risk_item(),
            reasons=("forged_ready_reason",),
        )
        risk_result = build_leader_research_readiness_audit(audit_input(
            risk_projection_item=ready_with_reasons,
        ))
        self.assertEqual(
            risk_result.first_veto_reason,
            "leader_research_audit_contract_unverified",
        )

    def test_contracts_are_frozen_and_sensitive_inputs_are_hidden(self):
        input_value = audit_input()
        result = build_leader_research_readiness_audit(input_value)

        with self.assertRaises(dataclasses.FrozenInstanceError):
            input_value.symbol = "000519"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.status = LeaderResearchReadinessAuditStatus.PARTIAL
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.items[0].key = "forged"
        self.assertNotIn("document-source-v1", repr(input_value))


if __name__ == "__main__":
    unittest.main()
