"""阶段6K运行时龙头证据组装。

本模块只消费调用方已经冻结的全市场行情批次、市场/行业聚合和证券行业
映射。它不抓取数据、不连接数据库，也不为尚未冻结公式的维度补分。
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryClassificationRecord,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    QuoteSnapshot,
    SecurityMasterRecord,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
)
from radar.leader_input_gate import (
    LeaderDimensionEvidence,
    LeaderInputEvidence,
    LeaderSourceEvidence,
    LeaderSourceKind,
)
from radar.leader_business_catalyst_features import (
    LeaderBusinessCatalystFeatureInput,
    LeaderBusinessCatalystFeatureResult,
    build_leader_business_catalyst_features,
    missing_leader_business_catalyst_features,
)
from radar.leader_history_features import (
    LeaderHistoryFeatureInput,
    LeaderHistoryFeatureResult,
    build_leader_history_features,
    missing_leader_history_features,
)
from radar.leader_liquidity_features import (
    LeaderLiquidityFeatureResult,
    build_leader_liquidity_features,
)
from radar.leader_research_features import (
    LeaderResearchFeatureResult,
    ResearchFeatureStatus,
    build_leader_research_features,
    build_leader_research_market_context,
    is_research_quote_eligible,
)
from radar.leader_research_readiness_audit import (
    LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID,
    LeaderResearchReadinessAuditResult,
    LeaderResearchReadinessAuditStatus,
)
from radar.leader_research_readiness_audit_batch import (
    is_leader_research_readiness_audit_valid,
)
from radar.leader_risk_candidate_projection import (
    LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID,
    LeaderRiskCandidateProjection,
)
from radar.leader_risk_official_deterministic import (
    LeaderOfficialDeterministicRiskProjection,
    is_leader_official_deterministic_risk_projection_valid,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
)
from radar.leader_risk_evidence_bundle_audit import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
)
from radar.leader_scoring import LeaderGateInput, LeaderMetricStatus
from radar.leader_state_machine import (
    BusinessExposureStatus,
    LeaderStateRecord,
)
from radar.leader_tradability_features import (
    LeaderTradabilityFeatureInput,
    LeaderTradabilityFeatureResult,
    build_leader_tradability_features,
    missing_leader_tradability_features,
)


UTC = timezone.utc
MAX_CANDIDATES_PER_INDUSTRY = 5
LEADER_RUNTIME_INPUT_VERSION = "radar-leader-runtime-input-v3"
LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID = (
    "radar-leader-research-component-batch-item-v2"
)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def build_leader_research_component_set_id(
    *,
    symbol: str,
    as_of: datetime,
    radar_run_id: str,
    quote_batch_id: str,
    quote_source_contract_id: str,
    industry_code: str,
    industry_release_id: str,
) -> str:
    normalized_as_of = _aware_utc(as_of, "component.as_of")
    return ":".join((
        LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID,
        radar_run_id,
        quote_batch_id,
        quote_source_contract_id,
        symbol,
        normalized_as_of.isoformat(),
        industry_code,
        industry_release_id,
    ))


def _freeze_research_value(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return MappingProxyType({
            key: _freeze_research_value(item)
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_research_value(item) for item in value)
    return value


def _minimum_coverage(values: Mapping[str, float]) -> float:
    numeric = tuple(float(value) for value in values.values())
    return min(numeric) if numeric else 0.0


def _source_status(
    status: SourceStatus,
) -> LeaderMetricStatus:
    return {
        SourceStatus.HEALTHY: LeaderMetricStatus.VERIFIED,
        SourceStatus.DEGRADED: LeaderMetricStatus.SOURCE_UNVERIFIED,
        SourceStatus.STALE: LeaderMetricStatus.STALE,
        SourceStatus.FAILED: LeaderMetricStatus.SOURCE_FAILED,
    }[status]


def _research_source_status(
    status: LeaderMetricStatus,
) -> ResearchFeatureStatus:
    return {
        LeaderMetricStatus.VERIFIED: ResearchFeatureStatus.READY,
        LeaderMetricStatus.MISSING: ResearchFeatureStatus.MISSING,
        LeaderMetricStatus.STALE: ResearchFeatureStatus.STALE,
        LeaderMetricStatus.SOURCE_FAILED: (
            ResearchFeatureStatus.SOURCE_FAILED
        ),
        LeaderMetricStatus.SOURCE_UNVERIFIED: (
            ResearchFeatureStatus.SOURCE_UNVERIFIED
        ),
        LeaderMetricStatus.NOT_APPLICABLE: (
            ResearchFeatureStatus.MISSING
        ),
    }[status]


def _research_dimension_status(
    status: ResearchFeatureStatus,
) -> LeaderMetricStatus:
    return {
        ResearchFeatureStatus.READY: (
            LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
        ResearchFeatureStatus.MISSING: LeaderMetricStatus.MISSING,
        ResearchFeatureStatus.STALE: LeaderMetricStatus.STALE,
        ResearchFeatureStatus.SOURCE_FAILED: (
            LeaderMetricStatus.SOURCE_FAILED
        ),
        ResearchFeatureStatus.SOURCE_UNVERIFIED: (
            LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
    }[status]


def _risk_candidate_projection_result(
    *,
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    projection: Optional[LeaderRiskCandidateProjection] = None,
) -> Mapping[str, object]:
    return {
        "status": status.value,
        "reasons": list(_dedupe(reasons)),
        "scoreReady": False,
        "researchScore": None,
        "riskFilterPassed": False,
        "formalGateReady": False,
        "formalUsable": False,
        "appliedToD3": False,
        "appliedToD1": False,
        "projection": (
            projection.to_evidence()
            if projection is not None
            else None
        ),
    }


def _risk_candidate_projection_evidence(
    projection: Optional[LeaderRiskCandidateProjection],
    *,
    symbol: str,
    as_of: datetime,
) -> Mapping[str, object]:
    if projection is None:
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.MISSING,
            reasons=("risk_candidate_projection_missing",),
        )
    if isinstance(projection, LeaderOfficialDeterministicRiskProjection):
        if not is_leader_official_deterministic_risk_projection_valid(
            projection,
            symbol=symbol,
            as_of=as_of,
        ):
            return _risk_candidate_projection_result(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=(
                    "risk_candidate_projection_contract_unverified",
                ),
            )
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.READY,
            reasons=(),
            projection=projection,
        )
    if not isinstance(projection, LeaderRiskCandidateProjection):
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=(
                "risk_candidate_projection_contract_unverified",
            ),
        )
    if projection.symbol != symbol:
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=(
                "risk_candidate_projection_identity_mismatch",
            ),
        )
    projection_as_of = projection.as_of
    if (
        not isinstance(projection_as_of, datetime)
        or projection_as_of.tzinfo is None
        or projection_as_of.utcoffset() is None
        or projection_as_of.astimezone(UTC) != as_of
    ):
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=("risk_candidate_projection_as_of_mismatch",),
        )
    if (
        projection.projection_contract_id
        != LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID
        or (
            projection.audit_contract_id
            != RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID
        )
        or (
            projection.bundle_contract_id
            != RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
        )
    ):
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=(
                "risk_candidate_projection_contract_unverified",
            ),
        )
    if any((
        projection.risk_filter_passed is not False,
        projection.formal_gate_ready is not False,
        projection.formal_usable is not False,
        projection.applied_to_d3 is not False,
        projection.applied_to_d1 is not False,
    )):
        return _risk_candidate_projection_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
            reasons=(
                "risk_candidate_projection_formal_flag_invalid",
            ),
        )
    return _risk_candidate_projection_result(
        status=ResearchFeatureStatus.READY,
        reasons=(),
        projection=projection,
    )


def _research_readiness_audit_result(
    *,
    status: str,
    reasons: Sequence[str],
    audit: Optional[
        LeaderResearchReadinessAuditResult
    ] = None,
) -> Mapping[str, object]:
    return {
        "status": status,
        "reasons": list(_dedupe(reasons)),
        "scoreReady": False,
        "researchScore": None,
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
        "audit": (
            _research_readiness_audit_summary(audit)
            if audit is not None
            else None
        ),
    }


def _research_readiness_audit_summary(
    audit: LeaderResearchReadinessAuditResult,
) -> Mapping[str, object]:
    return {
        "contractId": audit.contract_id,
        "auditStatus": audit.status.value,
        "candidate": {
            "symbol": audit.symbol,
            "asOf": audit.as_of.isoformat(),
        },
        "completeness": {
            "requiredItemCount": audit.required_item_count,
            "evidenceAvailableCount": (
                audit.evidence_available_count
            ),
            "satisfiedCount": audit.satisfied_count,
            "missingItems": list(audit.missing_items),
            "blockedItems": list(audit.blocked_items),
        },
        "firstVetoReason": audit.first_veto_reason,
        "firstResearchBlockerReason": (
            audit.first_research_blocker_reason
        ),
    }


def _research_readiness_audit_evidence(
    audit: Optional[LeaderResearchReadinessAuditResult],
    *,
    symbol: str,
    as_of: datetime,
) -> Mapping[str, object]:
    if audit is None:
        return _research_readiness_audit_result(
            status="missing",
            reasons=("leader_research_readiness_audit_missing",),
        )
    if not isinstance(audit, LeaderResearchReadinessAuditResult):
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_"
                "audit_contract_unverified",
            ),
        )
    if audit.symbol != symbol:
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_"
                "audit_identity_mismatch",
            ),
        )
    audit_as_of = audit.as_of
    if (
        not isinstance(audit_as_of, datetime)
        or audit_as_of.tzinfo is None
        or audit_as_of.utcoffset() is None
        or audit_as_of.astimezone(UTC) != as_of
    ):
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_audit_as_of_mismatch",
            ),
        )
    if (
        audit.contract_id
        != LEADER_RESEARCH_READINESS_AUDIT_CONTRACT_ID
    ):
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_"
                "audit_contract_unverified",
            ),
        )
    if any((
        audit.formal_score_ready is not False,
        audit.formal_gate_ready is not False,
        audit.formal_usable is not False,
        audit.state_transition_allowed is not False,
    )):
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_"
                "audit_formal_flag_invalid",
            ),
        )
    if audit.status == LeaderResearchReadinessAuditStatus.BLOCKED:
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_"
                "audit_not_consumable",
            ),
        )
    if not is_leader_research_readiness_audit_valid(
        audit,
        symbol=symbol,
        as_of=as_of,
    ):
        return _research_readiness_audit_result(
            status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
            reasons=(
                "leader_research_readiness_"
                "audit_result_unverified",
            ),
        )
    return _research_readiness_audit_result(
        status=audit.status.value,
        reasons=(),
        audit=audit,
    )


@dataclass(frozen=True)
class LeaderResearchComponentBatchItem:
    index: int
    symbol: str
    as_of: datetime
    industry_code: str
    industry_release_id: str
    radar_run_id: str
    quote_batch_id: str
    quote_source_contract_id: str
    component_set_id: str
    cross_sectional_features: LeaderResearchFeatureResult = field(
        repr=False
    )
    history_features: LeaderHistoryFeatureResult = field(repr=False)
    liquidity_features: LeaderLiquidityFeatureResult = field(
        repr=False
    )
    business_catalyst_features: (
        LeaderBusinessCatalystFeatureResult
    ) = field(repr=False)
    tradability_features: LeaderTradabilityFeatureResult = field(
        repr=False
    )
    contract_id: str = (
        LEADER_RESEARCH_COMPONENT_BATCH_ITEM_CONTRACT_ID
    )


@dataclass(frozen=True)
class LeaderRuntimeAssembly:
    status: str
    as_of: datetime
    evidence_items: Tuple[LeaderInputEvidence, ...]
    gate_reasons: Tuple[str, ...]
    scanned_count: int
    mapped_count: int
    research_component_items: Tuple[
        LeaderResearchComponentBatchItem,
        ...,
    ] = field(default_factory=tuple, repr=False)
    radar_run_id: Optional[str] = None
    quote_batch_id: Optional[str] = None

    @property
    def item_count(self) -> int:
        return len(self.evidence_items)


def attach_leader_research_readiness_audits(
    assembly: LeaderRuntimeAssembly,
    *,
    audits_by_symbol: Optional[
        Mapping[str, LeaderResearchReadinessAuditResult]
    ],
) -> LeaderRuntimeAssembly:
    """将F2可信审计回挂到既有组装结果，不重算研究组件。"""

    if not isinstance(assembly, LeaderRuntimeAssembly):
        raise TypeError("assembly必须是LeaderRuntimeAssembly")
    mapping_unverified = (
        audits_by_symbol is not None
        and not isinstance(audits_by_symbol, MappingABC)
    )
    audit_mapping = (
        audits_by_symbol
        if isinstance(audits_by_symbol, MappingABC)
        else {}
    )
    updated_items = []
    for item in assembly.evidence_items:
        evidence = item.evidence
        research_features = (
            evidence.get("researchFeatures")
            if isinstance(evidence, MappingABC)
            else None
        )
        if not isinstance(research_features, MappingABC):
            readiness = _research_readiness_audit_result(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
                reasons=(
                    "leader_research_readiness_"
                    "runtime_evidence_contract_unverified",
                ),
            )
            updated_evidence = dict(evidence) if isinstance(
                evidence,
                MappingABC,
            ) else {}
            updated_research_features = {}
        else:
            readiness = (
                _research_readiness_audit_result(
                    status=(
                        ResearchFeatureStatus.SOURCE_UNVERIFIED.value
                    ),
                    reasons=(
                        "leader_research_readiness_"
                        "audit_mapping_unverified",
                    ),
                )
                if mapping_unverified
                else _research_readiness_audit_evidence(
                    audit_mapping.get(item.symbol),
                    symbol=item.symbol,
                    as_of=assembly.as_of,
                )
            )
            updated_evidence = dict(evidence)
            updated_research_features = dict(research_features)
        updated_research_features[
            "researchReadinessAudit"
        ] = readiness
        updated_evidence["researchFeatures"] = (
            updated_research_features
        )
        updated_items.append(replace(item, evidence=updated_evidence))
    return replace(assembly, evidence_items=tuple(updated_items))


def attach_leader_risk_candidate_projections(
    assembly: LeaderRuntimeAssembly,
    *,
    projections_by_symbol: Optional[
        Mapping[str, LeaderRiskCandidateProjection]
    ],
) -> LeaderRuntimeAssembly:
    """将E3风险投影挂到既有组装结果，不重算研究组件。"""

    if not isinstance(assembly, LeaderRuntimeAssembly):
        raise TypeError("assembly必须是LeaderRuntimeAssembly")
    mapping_unverified = (
        projections_by_symbol is not None
        and not isinstance(projections_by_symbol, MappingABC)
    )
    projection_mapping = (
        projections_by_symbol
        if isinstance(projections_by_symbol, MappingABC)
        else {}
    )
    updated_items = []
    for item in assembly.evidence_items:
        evidence = item.evidence
        research_features = (
            evidence.get("researchFeatures")
            if isinstance(evidence, MappingABC)
            else None
        )
        if not isinstance(research_features, MappingABC):
            projection = _risk_candidate_projection_result(
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED.value,
                reasons=(
                    "risk_candidate_projection_"
                    "runtime_evidence_contract_unverified",
                ),
            )
            updated_evidence = dict(evidence) if isinstance(
                evidence,
                MappingABC,
            ) else {}
            updated_research_features = {}
        else:
            projection = (
                _risk_candidate_projection_result(
                    status=(
                        ResearchFeatureStatus.SOURCE_UNVERIFIED.value
                    ),
                    reasons=(
                        "risk_candidate_projection_mapping_unverified",
                    ),
                )
                if mapping_unverified
                else _risk_candidate_projection_evidence(
                    projection_mapping.get(item.symbol),
                    symbol=item.symbol,
                    as_of=assembly.as_of,
                )
            )
            updated_evidence = dict(evidence)
            updated_research_features = dict(research_features)
        updated_research_features[
            "riskCandidateProjection"
        ] = projection
        updated_evidence["researchFeatures"] = (
            updated_research_features
        )
        updated_items.append(replace(item, evidence=updated_evidence))
    return replace(assembly, evidence_items=tuple(updated_items))


def _not_ready(
    *,
    as_of: datetime,
    reasons: Sequence[str],
    scanned_count: int,
    mapped_count: int = 0,
) -> LeaderRuntimeAssembly:
    return LeaderRuntimeAssembly(
        status="not_ready",
        as_of=as_of,
        evidence_items=(),
        gate_reasons=_dedupe(reasons),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
    )


def _accepted_industry_map(
    records: Sequence[IndustryClassificationRecord],
) -> Tuple[
    Mapping[str, IndustryClassificationRecord],
    Tuple[str, ...],
]:
    accepted = {}
    conflicts = set()
    for record in records:
        if (
            record.record_status != IndustryRecordStatus.ACCEPTED
            or record.identity_status == IndustryIdentityStatus.UNRESOLVED
            or record.security_identity is None
        ):
            continue
        current = accepted.get(record.security_identity)
        if (
            current is not None
            and current.division_code != record.division_code
        ):
            conflicts.add(record.security_identity)
            continue
        accepted[record.security_identity] = record
    for symbol in conflicts:
        accepted.pop(symbol, None)
    return accepted, tuple(sorted(conflicts))


def _market_source(
    market_snapshot: Mapping,
) -> LeaderSourceEvidence:
    index_completeness = market_snapshot["indexCompleteness"]
    breadth_completeness = market_snapshot["breadth"]["completeness"]
    duplicate_symbol_count = market_snapshot.get("duplicateSymbolCount")
    if duplicate_symbol_count is None:
        duplicate_symbols = market_snapshot.get("duplicateSymbols")
        duplicate_symbol_count = (
            len(duplicate_symbols)
            if isinstance(duplicate_symbols, (tuple, list))
            else None
        )
    unknown_symbol_count = market_snapshot.get("unknownSymbolCount")
    if unknown_symbol_count is None:
        unknown_symbols = market_snapshot.get("unknownSymbols")
        unknown_symbol_count = (
            len(unknown_symbols)
            if isinstance(unknown_symbols, (tuple, list))
            else None
        )
    complete = bool(
        index_completeness["isComplete"]
        and breadth_completeness["isComplete"]
        and duplicate_symbol_count == 0
        and unknown_symbol_count == 0
    )
    reasons = _dedupe((
        *index_completeness.get("reasons", ()),
        *breadth_completeness.get("reasons", ()),
        (
            "market_identity_scope_unverified"
            if duplicate_symbol_count is None
            or unknown_symbol_count is None
            else None
        ),
        "market_aggregate_incomplete" if not complete else None,
    ))
    return LeaderSourceEvidence(
        source_contract_id=(
            "radar-market-aggregate-v1:"
            f"{market_snapshot['radarRunId']}"
        ),
        source_kind=LeaderSourceKind.MARKET,
        source_name="radar_market_environment",
        source_time=market_snapshot.get("sourceTime"),
        fetched_at=market_snapshot["fetchedAt"],
        status=(
            LeaderMetricStatus.VERIFIED
            if complete
            else LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
        row_coverage=min(
            float(index_completeness["rowCoverage"]),
            float(breadth_completeness["rowCoverage"]),
        ),
        required_field_coverage=min(
            _minimum_coverage(
                index_completeness["requiredFieldCoverage"]
            ),
            _minimum_coverage(
                breadth_completeness["requiredFieldCoverage"]
            ),
        ),
        reasons=reasons,
    )


def _sector_source(
    sector: Mapping,
) -> LeaderSourceEvidence:
    complete = bool(sector["isComplete"] and sector["shadowUsable"])
    reasons = _dedupe((
        *sector.get("reasons", ()),
        "sector_aggregate_incomplete" if not complete else None,
    ))
    return LeaderSourceEvidence(
        source_contract_id=(
            "radar-sector-aggregate-v1:"
            f"{sector['radarRunId']}:{sector['divisionCode']}"
        ),
        source_kind=LeaderSourceKind.SECTOR,
        source_name="radar_sector_features",
        source_time=sector.get("sourceTime"),
        fetched_at=sector["fetchedAt"],
        status=(
            LeaderMetricStatus.VERIFIED
            if complete
            else LeaderMetricStatus.SOURCE_UNVERIFIED
        ),
        row_coverage=float(sector["rowCoverage"]),
        required_field_coverage=_minimum_coverage(
            sector["requiredFieldCoverage"]
        ),
        reasons=reasons,
    )


def _quote_source(
    quote_batch: SourceBatch[QuoteSnapshot],
    quote_health: SourceHealthResult,
    quote: QuoteSnapshot,
) -> LeaderSourceEvidence:
    return LeaderSourceEvidence(
        source_contract_id=(
            "tencent-full-market-quote-v1:"
            f"{quote_batch.meta.batch_id}"
        ),
        source_kind=LeaderSourceKind.QUOTE,
        source_name=quote.source,
        source_time=quote.source_time,
        fetched_at=quote.fetched_at,
        status=_source_status(quote_health.status),
        row_coverage=float(quote_batch.meta.row_coverage or 0),
        required_field_coverage=_minimum_coverage(
            quote_batch.meta.required_field_coverage
        ),
        reasons=tuple(quote_health.reasons),
    )


def _dimensions(
    *,
    market_source_id: str,
    sector_source_id: str,
    quote_source_id: str,
    history_result: LeaderHistoryFeatureResult,
    liquidity_result: LeaderLiquidityFeatureResult,
) -> Tuple[LeaderDimensionEvidence, ...]:
    if history_result.status == ResearchFeatureStatus.READY:
        continuity_status = LeaderMetricStatus.SOURCE_UNVERIFIED
        continuity_reasons = ("continuity_research_only",)
    else:
        continuity_status = {
            ResearchFeatureStatus.MISSING: LeaderMetricStatus.MISSING,
            ResearchFeatureStatus.SOURCE_UNVERIFIED: (
                LeaderMetricStatus.SOURCE_UNVERIFIED
            ),
            ResearchFeatureStatus.STALE: LeaderMetricStatus.STALE,
            ResearchFeatureStatus.SOURCE_FAILED: (
                LeaderMetricStatus.SOURCE_FAILED
            ),
        }[history_result.status]
        continuity_reasons = (
            history_result.reasons
            or ("continuity_history_missing",)
        )
    continuity_source_id = (
        history_result.source_contract_ids[0]
        if history_result.source_contract_ids
        else quote_source_id
    )
    return (
        LeaderDimensionEvidence(
            field_name="industry_strength",
            score=None,
            source_contract_id=sector_source_id,
            status=LeaderMetricStatus.SOURCE_UNVERIFIED,
            reasons=("formal_sector_state_missing",),
        ),
        LeaderDimensionEvidence(
            field_name="market_leadership",
            score=None,
            source_contract_id=market_source_id,
            status=LeaderMetricStatus.SOURCE_UNVERIFIED,
            reasons=("formal_leader_score_incomplete",),
        ),
        LeaderDimensionEvidence(
            field_name="relative_strength_continuity",
            score=None,
            source_contract_id=continuity_source_id,
            status=continuity_status,
            reasons=continuity_reasons,
        ),
        LeaderDimensionEvidence(
            field_name="liquidity_tradability",
            score=None,
            source_contract_id=(
                liquidity_result.source_contract_ids[0]
                if liquidity_result.source_contract_ids
                else quote_source_id
            ),
            status=_research_dimension_status(
                liquidity_result.status
            ),
            reasons=(
                liquidity_result.reasons
                or ("liquidity_evidence_missing",)
            ),
        ),
        LeaderDimensionEvidence(
            field_name="business_exposure",
            score=None,
            source_contract_id=None,
            status=LeaderMetricStatus.MISSING,
            reasons=("business_exposure_evidence_missing",),
        ),
        LeaderDimensionEvidence(
            field_name="auxiliary",
            score=None,
            source_contract_id=None,
            status=LeaderMetricStatus.NOT_APPLICABLE,
            reasons=("auxiliary_rule_disabled",),
        ),
    )


def build_leader_runtime_evidence(
    *,
    as_of: datetime,
    quote_batch: SourceBatch[QuoteSnapshot],
    quote_health: SourceHealthResult,
    market_snapshot: Optional[Mapping],
    sector_rows: Sequence[Mapping],
    industry_records: Sequence[IndustryClassificationRecord],
    security_records: Sequence[SecurityMasterRecord],
    previous_states: Optional[Mapping[str, LeaderStateRecord]] = None,
    history_inputs_by_symbol: Optional[
        Mapping[str, LeaderHistoryFeatureInput]
    ] = None,
    business_catalyst_inputs_by_symbol: Optional[
        Mapping[str, LeaderBusinessCatalystFeatureInput]
    ] = None,
    tradability_inputs_by_symbol: Optional[
        Mapping[str, LeaderTradabilityFeatureInput]
    ] = None,
    risk_candidate_projections_by_symbol: Optional[
        Mapping[str, LeaderRiskCandidateProjection]
    ] = None,
    research_readiness_audits_by_symbol: Optional[
        Mapping[str, LeaderResearchReadinessAuditResult]
    ] = None,
    candidate_symbols: Optional[Sequence[str]] = None,
) -> LeaderRuntimeAssembly:
    """组装每个行业涨跌幅前5名的研究性输入，不生成正式分数。"""

    as_of = _aware_utc(as_of, "as_of")
    if _aware_utc(quote_batch.meta.as_of, "quote_batch.as_of") != as_of:
        raise ValueError("行情批次as_of必须与龙头批次一致")
    candidate_symbol_order = (
        tuple(candidate_symbols)
        if candidate_symbols is not None
        else None
    )
    quote_symbols = tuple(quote.symbol for quote in quote_batch.items)
    if candidate_symbol_order is not None and (
        not candidate_symbol_order
        or len(candidate_symbol_order) != len(set(candidate_symbol_order))
        or any(
            not isinstance(symbol, str) or symbol not in quote_symbols
            for symbol in candidate_symbol_order
        )
    ):
        raise ValueError("候选子计划不属于行情比较全集")
    candidate_symbol_set = (
        set(candidate_symbol_order)
        if candidate_symbol_order is not None
        else None
    )
    scanned_count = len(quote_batch.items)
    if (
        quote_health.status != SourceStatus.HEALTHY
        or not quote_health.allows_new_state
    ):
        return _not_ready(
            as_of=as_of,
            reasons=("quote_source_not_healthy", *quote_health.reasons),
            scanned_count=scanned_count,
        )
    if market_snapshot is None:
        return _not_ready(
            as_of=as_of,
            reasons=("market_snapshot_missing",),
            scanned_count=scanned_count,
        )
    market_as_of = market_snapshot.get("asOf")
    if not isinstance(market_as_of, datetime):
        return _not_ready(
            as_of=as_of,
            reasons=("market_snapshot_as_of_missing",),
            scanned_count=scanned_count,
        )
    if _aware_utc(market_as_of, "market_snapshot.asOf") > as_of:
        return _not_ready(
            as_of=as_of,
            reasons=("market_snapshot_from_future",),
            scanned_count=scanned_count,
        )
    if not sector_rows:
        return _not_ready(
            as_of=as_of,
            reasons=("sector_snapshot_missing",),
            scanned_count=scanned_count,
        )
    if any(
        _aware_utc(row["asOf"], "sector_snapshot.asOf") > as_of
        for row in sector_rows
    ):
        return _not_ready(
            as_of=as_of,
            reasons=("sector_snapshot_from_future",),
            scanned_count=scanned_count,
        )

    industry_by_symbol, mapping_conflicts = _accepted_industry_map(
        industry_records
    )
    if not industry_by_symbol:
        return _not_ready(
            as_of=as_of,
            reasons=(
                "industry_mapping_missing",
                "industry_mapping_conflict" if mapping_conflicts else None,
            ),
            scanned_count=scanned_count,
        )

    security_by_symbol = {
        record.symbol: record
        for record in security_records
        if record.exchange in {"sse", "szse"}
    }
    quote_by_symbol = {
        quote.symbol: quote
        for quote in quote_batch.items
        if quote.symbol in security_by_symbol
    }
    sector_by_code = {
        str(row["divisionCode"]): row
        for row in sector_rows
    }
    grouped = {}
    for symbol, industry in industry_by_symbol.items():
        quote = quote_by_symbol.get(symbol)
        if (
            quote is None
            or not is_research_quote_eligible(quote, as_of)
            or industry.division_code not in sector_by_code
        ):
            continue
        grouped.setdefault(industry.division_code, []).append(quote)

    market_source = _market_source(market_snapshot)
    market_quotes = tuple(quote_by_symbol.values())
    research_market_context = (
        build_leader_research_market_context(
            as_of=as_of,
            market_quotes=market_quotes,
            security_by_symbol=security_by_symbol,
            market_snapshot=market_snapshot,
        )
    )
    previous_states = previous_states or {}
    history_inputs_by_symbol = history_inputs_by_symbol or {}
    business_catalyst_inputs_by_symbol = (
        business_catalyst_inputs_by_symbol or {}
    )
    tradability_inputs_by_symbol = tradability_inputs_by_symbol or {}
    risk_candidate_projections_by_symbol = (
        risk_candidate_projections_by_symbol or {}
    )
    research_readiness_mapping_unverified = (
        research_readiness_audits_by_symbol is not None
        and not isinstance(
            research_readiness_audits_by_symbol,
            MappingABC,
        )
    )
    if research_readiness_mapping_unverified:
        research_readiness_audits_by_symbol = {}
    else:
        research_readiness_audits_by_symbol = (
            research_readiness_audits_by_symbol or {}
        )
    evidence_items = []
    research_component_items = []
    mapped_count = sum(len(items) for items in grouped.values())
    for division_code in sorted(grouped):
        sector = sector_by_code[division_code]
        sector_source = _sector_source(sector)
        ordered_quotes = sorted(
            grouped[division_code],
            key=lambda quote: (-float(quote.change_percent), quote.symbol),
        )[:MAX_CANDIDATES_PER_INDUSTRY]
        for rank, quote in enumerate(ordered_quotes, start=1):
            if (
                candidate_symbol_set is not None
                and quote.symbol not in candidate_symbol_set
            ):
                continue
            security = security_by_symbol[quote.symbol]
            industry = industry_by_symbol[quote.symbol]
            quote_source = _quote_source(
                quote_batch,
                quote_health,
                quote,
            )
            previous = previous_states.get(quote.symbol)
            history_input = history_inputs_by_symbol.get(quote.symbol)
            if history_input is None:
                history_result = missing_leader_history_features()
            elif history_input.as_of != as_of:
                history_result = missing_leader_history_features(
                    ("continuity_history_as_of_mismatch",),
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
            elif history_input.candidate.symbol != quote.symbol:
                history_result = missing_leader_history_features(
                    ("continuity_candidate_identity_mismatch",),
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
            elif (
                history_input.industry_benchmark.symbol
                != industry.division_code
            ):
                history_result = missing_leader_history_features(
                    ("continuity_industry_identity_mismatch",),
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                )
            else:
                history_result = build_leader_history_features(
                    history_input
                )
            business_input = business_catalyst_inputs_by_symbol.get(
                quote.symbol
            )
            if business_input is None:
                business_result = (
                    missing_leader_business_catalyst_features()
                )
            elif business_input.as_of != as_of:
                business_result = (
                    missing_leader_business_catalyst_features(
                        ("business_evidence_as_of_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            elif (
                business_input.symbol != quote.symbol
                or business_input.industry_code
                != industry.division_code
                or business_input.industry_release_id
                != str(sector["industryReleaseId"])
            ):
                business_result = (
                    missing_leader_business_catalyst_features(
                        ("business_evidence_identity_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            else:
                business_result = (
                    build_leader_business_catalyst_features(
                        business_input
                    )
                )
            liquidity_result = build_leader_liquidity_features(
                as_of=as_of,
                quote=quote,
                source_contract_id=quote_source.source_contract_id,
                source_status=_research_source_status(
                    quote_source.status
                ),
            )
            tradability_input = tradability_inputs_by_symbol.get(
                quote.symbol
            )
            if tradability_input is None:
                tradability_result = (
                    missing_leader_tradability_features()
                )
            elif tradability_input.as_of != as_of:
                tradability_result = (
                    missing_leader_tradability_features(
                        ("tradability_evidence_as_of_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            elif (
                tradability_input.quote.symbol != quote.symbol
                or (
                    tradability_input.quote_source_contract_id
                    != quote_source.source_contract_id
                )
            ):
                tradability_result = (
                    missing_leader_tradability_features(
                        ("tradability_evidence_identity_mismatch",),
                        status=(
                            ResearchFeatureStatus.SOURCE_UNVERIFIED
                        ),
                    )
                )
            else:
                tradability_result = (
                    build_leader_tradability_features(
                        tradability_input
                    )
                )
            risk_candidate_projection = (
                _risk_candidate_projection_evidence(
                    risk_candidate_projections_by_symbol.get(
                        quote.symbol
                    ),
                    symbol=quote.symbol,
                    as_of=as_of,
                )
            )
            research_readiness_audit = (
                _research_readiness_audit_result(
                    status=(
                        ResearchFeatureStatus
                        .SOURCE_UNVERIFIED.value
                    ),
                    reasons=(
                        "leader_research_readiness_"
                        "audit_mapping_unverified",
                    ),
                )
                if research_readiness_mapping_unverified
                else _research_readiness_audit_evidence(
                    research_readiness_audits_by_symbol.get(
                        quote.symbol
                    ),
                    symbol=quote.symbol,
                    as_of=as_of,
                )
            )
            research_features = build_leader_research_features(
                as_of=as_of,
                candidate_quote=quote,
                candidate_security=security,
                industry_quotes=tuple(grouped[division_code]),
                market_quotes=market_quotes,
                security_by_symbol=security_by_symbol,
                sector=sector,
                sector_rows=sector_rows,
                market_snapshot=market_snapshot,
                sector_source_contract_id=(
                    sector_source.source_contract_id
                ),
                market_source_contract_id=(
                    market_source.source_contract_id
                ),
                quote_source_contract_id=(
                    quote_source.source_contract_id
                ),
                sector_source_status=_research_source_status(
                    sector_source.status
                ),
                market_source_status=_research_source_status(
                    market_source.status
                ),
                quote_source_status=_research_source_status(
                    quote_source.status
                ),
                market_context=research_market_context,
            )
            research_component_items.append(
                LeaderResearchComponentBatchItem(
                    index=len(research_component_items),
                    symbol=quote.symbol,
                    as_of=as_of,
                    industry_code=industry.division_code,
                    industry_release_id=str(
                        sector["industryReleaseId"]
                    ),
                    radar_run_id=quote_batch.meta.radar_run_id,
                    quote_batch_id=quote_batch.meta.batch_id,
                    quote_source_contract_id=(
                        quote_source.source_contract_id
                    ),
                    component_set_id=(
                        build_leader_research_component_set_id(
                            symbol=quote.symbol,
                            as_of=as_of,
                            radar_run_id=(
                                quote_batch.meta.radar_run_id
                            ),
                            quote_batch_id=quote_batch.meta.batch_id,
                            quote_source_contract_id=(
                                quote_source.source_contract_id
                            ),
                            industry_code=industry.division_code,
                            industry_release_id=str(
                                sector["industryReleaseId"]
                            ),
                        )
                    ),
                    cross_sectional_features=research_features,
                    history_features=replace(
                        history_result,
                        metrics=_freeze_research_value(
                            history_result.metrics
                        ),
                    ),
                    liquidity_features=replace(
                        liquidity_result,
                        metrics=_freeze_research_value(
                            liquidity_result.metrics
                        ),
                    ),
                    business_catalyst_features=replace(
                        business_result,
                        references=_freeze_research_value(
                            business_result.references
                        ),
                    ),
                    tradability_features=replace(
                        tradability_result,
                        references=_freeze_research_value(
                            tradability_result.references
                        ),
                    ),
                )
            )
            evidence_items.append(LeaderInputEvidence(
                symbol=quote.symbol,
                name=security.name,
                as_of=as_of,
                industry_code=industry.division_code,
                industry_name=industry.division_name,
                dimensions=_dimensions(
                    market_source_id=market_source.source_contract_id,
                    sector_source_id=sector_source.source_contract_id,
                    quote_source_id=quote_source.source_contract_id,
                    history_result=history_result,
                    liquidity_result=liquidity_result,
                ),
                gates=LeaderGateInput(
                    industry_gate_passed=False,
                    stock_gate_passed=False,
                    market_leadership_passed=False,
                    industry_contribution_passed=False,
                    liquidity_passed=False,
                    tradability_passed=False,
                    continuity_passed=False,
                    recovery_passed=False,
                    risk_filter_passed=False,
                    business_exposure_status=BusinessExposureStatus.MISSING,
                ),
                sources=(
                    market_source,
                    sector_source,
                    quote_source,
                ),
                consecutive_signal_periods=(
                    previous.state_age_periods + 1
                    if previous is not None
                    else 1
                ),
                evidence={
                    "runtimeInputVersion": LEADER_RUNTIME_INPUT_VERSION,
                    "researchFeatures": {
                        **research_features.to_evidence(),
                        "historyContinuity": (
                            history_result.to_evidence()
                        ),
                        "liquidityTradability": (
                            liquidity_result.to_evidence()
                        ),
                        "businessCatalyst": (
                            business_result.to_evidence()
                        ),
                        "securityTradability": (
                            tradability_result.to_evidence()
                        ),
                        "riskCandidateProjection": (
                            risk_candidate_projection
                        ),
                        "researchReadinessAudit": (
                            research_readiness_audit
                        ),
                    },
                    "withinIndustryRankByChangePercent": rank,
                    "quote": {
                        "price": quote.price,
                        "changePercent": quote.change_percent,
                        "turnoverAmountSource": (
                            quote.turnover_amount_source
                        ),
                        "turnoverRatePercent": (
                            quote.turnover_rate_percent
                        ),
                        "volumeRatio": quote.volume_ratio,
                        "marketCapSource": quote.market_cap_source,
                    },
                    "sector": {
                        "equalReturn": sector.get("equalReturn"),
                        "capWeightedReturn": (
                            sector.get("capWeightedReturn")
                        ),
                        "exTopReturn": sector.get("exTopReturn"),
                        "upRatio": sector.get("upRatio"),
                        "topContributorSymbol": (
                            sector.get("topContributorSymbol")
                        ),
                        "topContributionPercentPoints": (
                            sector.get("topContributionPercentPoints")
                        ),
                    },
                    "market": {
                        "indices": tuple({
                            "indexKey": item["indexKey"],
                            "changePercent": item["changePercent"],
                        } for item in market_snapshot.get("indices", ())),
                    },
                },
                invalidation={
                    "missingPrerequisites": [
                        "formal_sector_state",
                        "stock_hard_filter",
                        (
                            "continuity_formal_rule"
                            if history_result.status
                            == ResearchFeatureStatus.READY
                            else "continuity_history"
                        ),
                        "business_exposure_evidence",
                        "tradability_fields",
                        "risk_evidence",
                    ],
                },
            ))

    if candidate_symbol_order is not None:
        evidence_by_symbol = {
            item.symbol: item for item in evidence_items
        }
        component_by_symbol = {
            item.symbol: item for item in research_component_items
        }
        if (
            set(evidence_by_symbol) != candidate_symbol_set
            or set(component_by_symbol) != candidate_symbol_set
        ):
            return _not_ready(
                as_of=as_of,
                reasons=("leader_candidate_subset_not_in_research_universe",),
                scanned_count=scanned_count,
                mapped_count=mapped_count,
            )
        evidence_items = [
            evidence_by_symbol[symbol]
            for symbol in candidate_symbol_order
        ]
        research_component_items = [
            replace(component_by_symbol[symbol], index=index)
            for index, symbol in enumerate(candidate_symbol_order)
        ]
    if not evidence_items:
        return _not_ready(
            as_of=as_of,
            reasons=(
                "leader_candidate_input_empty",
                "industry_mapping_conflict" if mapping_conflicts else None,
            ),
            scanned_count=scanned_count,
            mapped_count=mapped_count,
        )
    return LeaderRuntimeAssembly(
        status="ready",
        as_of=as_of,
        evidence_items=tuple(evidence_items),
        gate_reasons=(
            ("industry_mapping_conflict",)
            if mapping_conflicts
            else ()
        ),
        scanned_count=scanned_count,
        mapped_count=mapped_count,
        research_component_items=tuple(research_component_items),
        radar_run_id=quote_batch.meta.radar_run_id,
        quote_batch_id=quote_batch.meta.batch_id,
    )
