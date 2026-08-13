"""阶段6L-D9风险研究证据包版本链与差异审计。

本模块只消费按旧到新排列的D8冻结证据包，验证同一来源快照的线性研究
版本链并输出相邻差异。它不会重排、合并或写回任何上游对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    RiskDocumentRelationKind,
)
from radar.leader_risk_evidence_bundle import (
    RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID,
    FormalRiskGateGap,
    RiskResearchEvidenceBundle,
)
from radar.leader_risk_invalidation_features import (
    RiskOfficialStatus,
)


RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID = (
    "radar-leader-risk-research-evidence-bundle-audit-v1"
)


@dataclass(frozen=True)
class RiskEvidenceFieldChange:
    field_name: str
    previous_value: Optional[str]
    current_value: Optional[str]


@dataclass(frozen=True)
class RiskEvidenceFactDiff:
    deterministic_added: Tuple[str, ...] = ()
    deterministic_removed: Tuple[str, ...] = ()
    manual_added: Tuple[str, ...] = ()
    manual_removed: Tuple[str, ...] = ()
    merged_added: Tuple[str, ...] = ()
    merged_removed: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskEvidenceArtifactDiff:
    field_changes: Tuple[RiskEvidenceFieldChange, ...] = ()


@dataclass(frozen=True)
class RiskEvidenceRelationDiff:
    field_changes: Tuple[RiskEvidenceFieldChange, ...] = ()
    basis_fact_ids_added: Tuple[str, ...] = ()
    basis_fact_ids_removed: Tuple[str, ...] = ()
    manual_basis_fact_ids_added: Tuple[str, ...] = ()
    manual_basis_fact_ids_removed: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskEvidenceFormalGateGapDiff:
    added: Tuple[FormalRiskGateGap, ...] = ()
    removed: Tuple[FormalRiskGateGap, ...] = ()


@dataclass(frozen=True)
class RiskResearchEvidenceBundleVersionDiff:
    previous_bundle_id: str
    current_bundle_id: str
    facts: RiskEvidenceFactDiff
    artifact: RiskEvidenceArtifactDiff
    relation: RiskEvidenceRelationDiff
    formal_gate_gaps: RiskEvidenceFormalGateGapDiff


@dataclass(frozen=True)
class RiskResearchEvidenceBundleAuditInput:
    bundles: Tuple[RiskResearchEvidenceBundle, ...] = field(
        repr=False,
    )


@dataclass(frozen=True)
class RiskResearchEvidenceBundleAuditResult:
    status: ResearchFeatureStatus
    bundle_ids: Tuple[str, ...] = ()
    current_bundle_id: Optional[str] = None
    current_bundle: Optional[RiskResearchEvidenceBundle] = field(
        default=None,
        repr=False,
    )
    diffs: Tuple[RiskResearchEvidenceBundleVersionDiff, ...] = ()
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    audit_contract_id: str = (
        RISK_RESEARCH_EVIDENCE_BUNDLE_AUDIT_CONTRACT_ID
    )
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False


_IDENTITY_FIELDS = (
    "document_source_contract_id",
    "document_id",
    "symbol",
    "issuer_identity",
    "content_contract_id",
    "content_sha256",
    "content_fetched_at",
    "target_event_id",
    "target_document_id",
)

_ARTIFACT_FIELDS = (
    "active_artifact_id",
    "active_artifact_version",
    "artifact_contract_version",
)

_RELATION_FIELDS = (
    "replay_version",
    "relation_id",
    "review_id",
    "mapping_version",
    "relation_kind",
    "target_event_version",
    "replacement_event_version",
    "target_event_official_status",
    "target_event_published_at",
)

_MATERIAL_RELATION_FIELDS = frozenset({
    "relation_kind",
    "replacement_event_version",
    "target_event_official_status",
    "target_event_published_at",
})

_ID_TUPLE_FIELDS = (
    "deterministic_fact_ids",
    "manual_fact_ids",
    "merged_fact_ids",
    "basis_fact_ids",
    "manual_basis_fact_ids",
)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _result(
    status: ResearchFeatureStatus,
    reasons: Sequence[str],
    *,
    bundle_ids: Sequence[str] = (),
    current_bundle: Optional[RiskResearchEvidenceBundle] = None,
    diffs: Sequence[RiskResearchEvidenceBundleVersionDiff] = (),
) -> RiskResearchEvidenceBundleAuditResult:
    return RiskResearchEvidenceBundleAuditResult(
        status=status,
        bundle_ids=tuple(bundle_ids),
        current_bundle_id=(
            current_bundle.bundle_id
            if current_bundle is not None
            else None
        ),
        current_bundle=current_bundle,
        diffs=tuple(diffs),
        reasons=_dedupe(reasons),
    )


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(timezone.utc)


def _identifier_tuple_valid(value: Any) -> bool:
    return (
        isinstance(value, tuple)
        and all(
            isinstance(item, str) and bool(item)
            for item in value
        )
        and len(value) == len(set(value))
    )


def _bundle_valid(bundle: Any) -> bool:
    return (
        isinstance(bundle, RiskResearchEvidenceBundle)
        and bundle.bundle_contract_id
        == RISK_RESEARCH_EVIDENCE_BUNDLE_CONTRACT_ID
        and isinstance(bundle.bundle_id, str)
        and bundle.bundle_id.startswith(
            "risk-research-evidence-bundle:"
        )
        and _aware_utc(bundle.built_at) is not None
        and _aware_utc(bundle.content_fetched_at) is not None
        and _aware_utc(bundle.target_event_published_at)
        is not None
        and all(
            isinstance(getattr(bundle, field_name), str)
            and bool(getattr(bundle, field_name))
            for field_name in (
                "document_source_contract_id",
                "document_id",
                "symbol",
                "issuer_identity",
                "content_contract_id",
                "content_sha256",
                "active_artifact_id",
                "active_artifact_version",
                "artifact_contract_version",
                "replay_version",
                "relation_id",
                "review_id",
                "mapping_version",
                "target_event_id",
                "target_event_version",
                "target_document_id",
            )
        )
        and all(
            _identifier_tuple_valid(
                getattr(bundle, field_name)
            )
            for field_name in _ID_TUPLE_FIELDS
        )
        and isinstance(
            bundle.relation_kind,
            RiskDocumentRelationKind,
        )
        and isinstance(
            bundle.target_event_official_status,
            RiskOfficialStatus,
        )
        and isinstance(bundle.formal_gate_gaps, tuple)
        and bool(bundle.formal_gate_gaps)
        and all(
            isinstance(gap, FormalRiskGateGap)
            for gap in bundle.formal_gate_gaps
        )
        and len(bundle.formal_gate_gaps)
        == len(set(bundle.formal_gate_gaps))
        and bundle.formal_gate_ready is False
        and bundle.formal_usable is False
        and bundle.applied_to_d3 is False
        and bundle.applied_to_d1 is False
    )


def _stable_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        aware = _aware_utc(value)
        return aware.isoformat() if aware is not None else None
    return str(value)


def _field_changes(
    previous: RiskResearchEvidenceBundle,
    current: RiskResearchEvidenceBundle,
    field_names: Sequence[str],
) -> Tuple[RiskEvidenceFieldChange, ...]:
    changes = []
    for field_name in field_names:
        previous_value = getattr(previous, field_name)
        current_value = getattr(current, field_name)
        if previous_value != current_value:
            changes.append(
                RiskEvidenceFieldChange(
                    field_name=field_name,
                    previous_value=_stable_value(previous_value),
                    current_value=_stable_value(current_value),
                )
            )
    return tuple(changes)


def _added(
    previous: Sequence[Any],
    current: Sequence[Any],
) -> Tuple[Any, ...]:
    previous_values = set(previous)
    return tuple(
        value for value in current if value not in previous_values
    )


def _removed(
    previous: Sequence[Any],
    current: Sequence[Any],
) -> Tuple[Any, ...]:
    current_values = set(current)
    return tuple(
        value for value in previous if value not in current_values
    )


def _version_diff(
    previous: RiskResearchEvidenceBundle,
    current: RiskResearchEvidenceBundle,
) -> RiskResearchEvidenceBundleVersionDiff:
    return RiskResearchEvidenceBundleVersionDiff(
        previous_bundle_id=previous.bundle_id,
        current_bundle_id=current.bundle_id,
        facts=RiskEvidenceFactDiff(
            deterministic_added=_added(
                previous.deterministic_fact_ids,
                current.deterministic_fact_ids,
            ),
            deterministic_removed=_removed(
                previous.deterministic_fact_ids,
                current.deterministic_fact_ids,
            ),
            manual_added=_added(
                previous.manual_fact_ids,
                current.manual_fact_ids,
            ),
            manual_removed=_removed(
                previous.manual_fact_ids,
                current.manual_fact_ids,
            ),
            merged_added=_added(
                previous.merged_fact_ids,
                current.merged_fact_ids,
            ),
            merged_removed=_removed(
                previous.merged_fact_ids,
                current.merged_fact_ids,
            ),
        ),
        artifact=RiskEvidenceArtifactDiff(
            field_changes=_field_changes(
                previous,
                current,
                _ARTIFACT_FIELDS,
            ),
        ),
        relation=RiskEvidenceRelationDiff(
            field_changes=_field_changes(
                previous,
                current,
                _RELATION_FIELDS,
            ),
            basis_fact_ids_added=_added(
                previous.basis_fact_ids,
                current.basis_fact_ids,
            ),
            basis_fact_ids_removed=_removed(
                previous.basis_fact_ids,
                current.basis_fact_ids,
            ),
            manual_basis_fact_ids_added=_added(
                previous.manual_basis_fact_ids,
                current.manual_basis_fact_ids,
            ),
            manual_basis_fact_ids_removed=_removed(
                previous.manual_basis_fact_ids,
                current.manual_basis_fact_ids,
            ),
        ),
        formal_gate_gaps=RiskEvidenceFormalGateGapDiff(
            added=_added(
                previous.formal_gate_gaps,
                current.formal_gate_gaps,
            ),
            removed=_removed(
                previous.formal_gate_gaps,
                current.formal_gate_gaps,
            ),
        ),
    )


def _material_change_present(
    value: RiskResearchEvidenceBundleVersionDiff,
) -> bool:
    return any((
        value.facts.deterministic_added,
        value.facts.deterministic_removed,
        value.facts.manual_added,
        value.facts.manual_removed,
        value.facts.merged_added,
        value.facts.merged_removed,
        any(
            change.field_name in _MATERIAL_RELATION_FIELDS
            for change in value.relation.field_changes
        ),
        value.relation.basis_fact_ids_added,
        value.relation.basis_fact_ids_removed,
        value.relation.manual_basis_fact_ids_added,
        value.relation.manual_basis_fact_ids_removed,
        value.formal_gate_gaps.added,
        value.formal_gate_gaps.removed,
    ))


def audit_risk_research_evidence_bundle_versions(
    input_value: Any,
) -> RiskResearchEvidenceBundleAuditResult:
    """验证有序D8证据包链并输出相邻研究差异。"""

    if (
        not isinstance(
            input_value,
            RiskResearchEvidenceBundleAuditInput,
        )
        or not isinstance(input_value.bundles, tuple)
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_audit_contract_unverified",),
        )

    bundles = input_value.bundles
    if len(bundles) < 2:
        return _result(
            ResearchFeatureStatus.MISSING,
            ("risk_evidence_bundle_audit_history_insufficient",),
        )
    if any(not _bundle_valid(bundle) for bundle in bundles):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_audit_bundle_unverified",),
        )

    bundle_ids = tuple(bundle.bundle_id for bundle in bundles)
    if len(bundle_ids) != len(set(bundle_ids)):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_audit_bundle_duplicate",),
        )

    identity = tuple(
        getattr(bundles[0], field_name)
        for field_name in _IDENTITY_FIELDS
    )
    if any(
        tuple(
            getattr(bundle, field_name)
            for field_name in _IDENTITY_FIELDS
        )
        != identity
        for bundle in bundles[1:]
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_audit_identity_mismatch",),
        )

    built_at_values = tuple(
        _aware_utc(bundle.built_at) for bundle in bundles
    )
    if any(
        current is None
        or previous is None
        or current <= previous
        for previous, current in zip(
            built_at_values,
            built_at_values[1:],
        )
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            ("risk_evidence_bundle_audit_order_unverified",),
        )

    diffs = tuple(
        _version_diff(previous, current)
        for previous, current in zip(bundles, bundles[1:])
    )
    if any(
        not _material_change_present(diff)
        for diff in diffs
    ):
        return _result(
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
            (
                "risk_evidence_bundle_audit_"
                "material_change_missing",
            ),
        )

    return _result(
        ResearchFeatureStatus.READY,
        (),
        bundle_ids=bundle_ids,
        current_bundle=bundles[-1],
        diffs=diffs,
    )
