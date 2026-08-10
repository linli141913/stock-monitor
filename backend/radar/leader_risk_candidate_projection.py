"""阶段6L-E1风险研究证据到候选输入的只读投影。

本模块只重算D9并投影当前D8研究包、相邻差异和正式门禁缺口。它不修改
上游对象，也不把研究就绪转换为正式风险过滤通过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    RiskDocumentRelationKind,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
    FormalRiskGateGap,
)
from radar.leader_risk_evidence_bundle_audit import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID,
    RiskEvidenceFieldChange,
    RiskResearchEvidenceBundleAuditInput,
    RiskResearchEvidenceBundleAuditResult,
    RiskResearchEvidenceBundleVersionDiff,
    audit_risk_research_evidence_bundle_versions,
)
from radar.leader_risk_invalidation_features import (
    RiskOfficialStatus,
)


UTC = timezone.utc
LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID = (
    "radar-leader-risk-candidate-input-projection-v1"
)


@dataclass(frozen=True)
class LeaderRiskCandidateProjectionInput:
    symbol: str
    issuer_identity: Optional[str]
    as_of: datetime
    audit_input: RiskResearchEvidenceBundleAuditInput = field(
        repr=False,
    )
    audit_result: RiskResearchEvidenceBundleAuditResult = field(
        repr=False,
    )


@dataclass(frozen=True)
class LeaderRiskCandidateProjection:
    symbol: str
    issuer_identity: str
    as_of: datetime
    bundle_ids: Tuple[str, ...]
    current_bundle_id: str
    current_bundle_built_at: datetime
    document_source_contract_id: str
    document_id: str
    content_contract_id: str
    content_sha256: str
    content_fetched_at: datetime
    deterministic_fact_ids: Tuple[str, ...]
    manual_fact_ids: Tuple[str, ...]
    merged_fact_ids: Tuple[str, ...]
    active_artifact_id: str
    active_artifact_version: str
    artifact_contract_version: str
    replay_version: str
    relation_id: str
    review_id: str
    mapping_version: str
    relation_kind: RiskDocumentRelationKind
    target_event_id: str
    target_event_version: str
    target_document_id: str
    replacement_event_version: Optional[str]
    basis_fact_ids: Tuple[str, ...]
    manual_basis_fact_ids: Tuple[str, ...]
    target_event_official_status: RiskOfficialStatus
    target_event_published_at: datetime
    formal_gate_gaps: Tuple[FormalRiskGateGap, ...]
    version_diffs: Tuple[
        RiskResearchEvidenceBundleVersionDiff,
        ...,
    ]
    projection_contract_id: str = (
        LEADER_RISK_CANDIDATE_PROJECTION_CONTRACT_ID
    )
    audit_contract_id: str = (
        RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID
    )
    bundle_contract_id: str = (
        RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
    )
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "projectionContractId": self.projection_contract_id,
            "auditContractId": self.audit_contract_id,
            "bundleContractId": self.bundle_contract_id,
            "candidate": {
                "symbol": self.symbol,
                "issuerIdentity": self.issuer_identity,
                "asOf": self.as_of.isoformat(),
            },
            "history": {
                "bundleIds": list(self.bundle_ids),
                "versionDiffs": [
                    _version_diff_payload(value)
                    for value in self.version_diffs
                ],
            },
            "current": {
                "bundleId": self.current_bundle_id,
                "builtAt": self.current_bundle_built_at.isoformat(),
                "document": {
                    "sourceContractId": (
                        self.document_source_contract_id
                    ),
                    "documentId": self.document_id,
                },
                "content": {
                    "contractId": self.content_contract_id,
                    "sha256": self.content_sha256,
                    "fetchedAt": self.content_fetched_at.isoformat(),
                },
                "facts": {
                    "deterministicFactIds": list(
                        self.deterministic_fact_ids
                    ),
                    "manualFactIds": list(self.manual_fact_ids),
                    "mergedFactIds": list(self.merged_fact_ids),
                },
                "artifact": {
                    "artifactId": self.active_artifact_id,
                    "artifactVersion": (
                        self.active_artifact_version
                    ),
                    "contractVersion": (
                        self.artifact_contract_version
                    ),
                },
                "relation": {
                    "replayVersion": self.replay_version,
                    "relationId": self.relation_id,
                    "reviewId": self.review_id,
                    "mappingVersion": self.mapping_version,
                    "relationKind": self.relation_kind.value,
                    "targetEventId": self.target_event_id,
                    "targetEventVersion": (
                        self.target_event_version
                    ),
                    "targetDocumentId": self.target_document_id,
                    "replacementEventVersion": (
                        self.replacement_event_version
                    ),
                    "basisFactIds": list(self.basis_fact_ids),
                    "manualBasisFactIds": list(
                        self.manual_basis_fact_ids
                    ),
                    "targetEventOfficialStatus": (
                        self.target_event_official_status.value
                    ),
                    "targetEventPublishedAt": (
                        self.target_event_published_at.isoformat()
                    ),
                },
                "formalGateGaps": [
                    value.value for value in self.formal_gate_gaps
                ],
            },
            "gate": {
                "riskFilterPassed": self.risk_filter_passed,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "appliedToD3": self.applied_to_d3,
                "appliedToD1": self.applied_to_d1,
            },
        }


@dataclass(frozen=True)
class LeaderRiskCandidateProjectionResult:
    status: ResearchFeatureStatus
    projection: Optional[LeaderRiskCandidateProjection] = None
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    projection: Optional[LeaderRiskCandidateProjection] = None,
) -> LeaderRiskCandidateProjectionResult:
    return LeaderRiskCandidateProjectionResult(
        status=status,
        projection=projection,
        reasons=_dedupe(reasons),
    )


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _field_changes_payload(
    values: Tuple[RiskEvidenceFieldChange, ...],
) -> list[Dict[str, Optional[str]]]:
    return [
        {
            "fieldName": value.field_name,
            "previousValue": value.previous_value,
            "currentValue": value.current_value,
        }
        for value in values
    ]


def _version_diff_payload(
    value: RiskResearchEvidenceBundleVersionDiff,
) -> Dict[str, Any]:
    return {
        "previousBundleId": value.previous_bundle_id,
        "currentBundleId": value.current_bundle_id,
        "facts": {
            "deterministicAdded": list(
                value.facts.deterministic_added
            ),
            "deterministicRemoved": list(
                value.facts.deterministic_removed
            ),
            "manualAdded": list(value.facts.manual_added),
            "manualRemoved": list(value.facts.manual_removed),
            "mergedAdded": list(value.facts.merged_added),
            "mergedRemoved": list(value.facts.merged_removed),
        },
        "artifact": {
            "fieldChanges": _field_changes_payload(
                value.artifact.field_changes
            ),
        },
        "relation": {
            "fieldChanges": _field_changes_payload(
                value.relation.field_changes
            ),
            "basisFactIdsAdded": list(
                value.relation.basis_fact_ids_added
            ),
            "basisFactIdsRemoved": list(
                value.relation.basis_fact_ids_removed
            ),
            "manualBasisFactIdsAdded": list(
                value.relation.manual_basis_fact_ids_added
            ),
            "manualBasisFactIdsRemoved": list(
                value.relation.manual_basis_fact_ids_removed
            ),
        },
        "formalGateGaps": {
            "added": [
                item.value
                for item in value.formal_gate_gaps.added
            ],
            "removed": [
                item.value
                for item in value.formal_gate_gaps.removed
            ],
        },
    }


def build_leader_risk_candidate_projection(
    input_value: Any,
) -> LeaderRiskCandidateProjectionResult:
    """重算D9并构建固定不参与正式门禁的候选研究证据。"""

    if not isinstance(
        input_value,
        LeaderRiskCandidateProjectionInput,
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_candidate_projection_contract_unverified",),
        )

    replayed = audit_risk_research_evidence_bundle_versions(
        input_value.audit_input
    )
    if replayed != input_value.audit_result:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_candidate_projection_audit_replay_mismatch",),
        )
    if (
        replayed.status != ResearchFeatureStatus.READY
        or replayed.current_bundle is None
        or replayed.current_bundle_id is None
    ):
        return _result(replayed.status, replayed.reasons)

    as_of = _aware_utc(input_value.as_of)
    if as_of is None:
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_candidate_projection_as_of_timezone_missing",),
        )
    current = replayed.current_bundle
    if (
        not isinstance(input_value.symbol, str)
        or not input_value.symbol.strip()
        or not isinstance(input_value.issuer_identity, str)
        or not input_value.issuer_identity.strip()
        or input_value.symbol != current.symbol
        or input_value.issuer_identity != current.issuer_identity
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            (
                "risk_candidate_projection_"
                "candidate_identity_mismatch",
            ),
        )

    evidence_times = (
        _aware_utc(current.built_at),
        _aware_utc(current.content_fetched_at),
        _aware_utc(current.target_event_published_at),
    )
    if any(
        value is None or value > as_of
        for value in evidence_times
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_candidate_projection_future_evidence",),
        )

    projection = LeaderRiskCandidateProjection(
        symbol=input_value.symbol,
        issuer_identity=input_value.issuer_identity,
        as_of=as_of,
        bundle_ids=replayed.bundle_ids,
        current_bundle_id=replayed.current_bundle_id,
        current_bundle_built_at=current.built_at,
        document_source_contract_id=(
            current.document_source_contract_id
        ),
        document_id=current.document_id,
        content_contract_id=current.content_contract_id,
        content_sha256=current.content_sha256,
        content_fetched_at=current.content_fetched_at,
        deterministic_fact_ids=current.deterministic_fact_ids,
        manual_fact_ids=current.manual_fact_ids,
        merged_fact_ids=current.merged_fact_ids,
        active_artifact_id=current.active_artifact_id,
        active_artifact_version=current.active_artifact_version,
        artifact_contract_version=(
            current.artifact_contract_version
        ),
        replay_version=current.replay_version,
        relation_id=current.relation_id,
        review_id=current.review_id,
        mapping_version=current.mapping_version,
        relation_kind=current.relation_kind,
        target_event_id=current.target_event_id,
        target_event_version=current.target_event_version,
        target_document_id=current.target_document_id,
        replacement_event_version=(
            current.replacement_event_version
        ),
        basis_fact_ids=current.basis_fact_ids,
        manual_basis_fact_ids=current.manual_basis_fact_ids,
        target_event_official_status=(
            current.target_event_official_status
        ),
        target_event_published_at=(
            current.target_event_published_at
        ),
        formal_gate_gaps=current.formal_gate_gaps,
        version_diffs=replayed.diffs,
    )
    return _result(
        ResearchFeatureStatus.READY,
        (),
        projection=projection,
    )
