"""阶段6官方风险覆盖证明的失败关闭就绪合同。

现有 D2 七关键词只提供官方文档发现能力，不能证明七类风险从发行人上市日
起完整覆盖。本模块只审计已有证据结构和可信生命周期；正式覆盖查询政策尚未
批准，因此任何当前结果都不得成为 D1 覆盖证明或打开正式门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from radar.leader_risk_invalidation_features import ALL_RISK_CATEGORIES
from radar.leader_risk_official_lifecycle import (
    LeaderOfficialRiskLifecycleResult,
    is_leader_official_risk_lifecycle_valid,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)


UTC = timezone.utc
LEADER_OFFICIAL_RISK_COVERAGE_CONTRACT_ID = (
    "radar-leader-official-risk-coverage-readiness-v1"
)
REQUIRED_RISK_CATEGORIES = tuple(ALL_RISK_CATEGORIES)
SAFE_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,160}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
TRUSTED_LISTING_DOMAINS = frozenset({"sse.com.cn", "szse.cn"})
_COVERAGE_PRODUCER_TOKEN = object()


class LeaderOfficialRiskCoverageStatus(str, Enum):
    POLICY_UNAPPROVED = "policy_unapproved"
    MISSING = "missing"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class LeaderOfficialRiskCoveragePolicyApproval:
    policy_id: str
    policy_version: str
    approved_by: str
    approved_at: datetime
    source_contract_ids: Tuple[str, ...]
    category_query_versions: Tuple[Tuple[Any, str], ...]


# 项目当前没有明文批准的正式覆盖查询政策。调用方对象不能扩充本注册表。
_APPROVED_COVERAGE_POLICIES: Mapping[
    Tuple[str, str],
    LeaderOfficialRiskCoveragePolicyApproval,
] = MappingProxyType({})


@dataclass(frozen=True)
class LeaderOfficialRiskIssuerListingEvidence:
    symbol: str
    issuer_identity: str
    issuer_listed_at: datetime
    source_contract_id: str
    source_name: str
    source_url: str
    fetched_at: datetime
    record_checksum: str


@dataclass(frozen=True)
class LeaderOfficialRiskCoverageItem:
    index: int
    symbol: str
    issuer_identity: Optional[str]
    status: LeaderOfficialRiskCoverageStatus
    reasons: Tuple[str, ...] = ()
    listing_evidence_structurally_valid: bool = False
    lifecycle_relationships_complete: bool = False
    policy_approved: bool = False
    issuer_listing_evidence_approved: bool = False
    coverage_complete: bool = False
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "listingEvidence": {
                "structurallyValid": (
                    self.listing_evidence_structurally_valid
                ),
                "approved": False,
            },
            "lifecycleRelationshipsComplete": (
                self.lifecycle_relationships_complete
            ),
            "policyApproved": False,
            "coverageComplete": False,
            "gate": {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True)
class LeaderOfficialRiskCoverageReadinessResult:
    status: LeaderOfficialRiskCoverageStatus
    radar_run_id: Optional[str]
    candidate_plan_id: Optional[str]
    as_of: Optional[datetime]
    items: Tuple[LeaderOfficialRiskCoverageItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    listing_evidence_structurally_valid: bool = False
    lifecycle_relationships_complete: bool = False
    policy_approved: bool = False
    issuer_listing_evidence_approved: bool = False
    coverage_complete: bool = False
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False
    contract_id: str = LEADER_OFFICIAL_RISK_COVERAGE_CONTRACT_ID
    _producer_token: Any = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
            "listingEvidence": {
                "structurallyValid": (
                    self.listing_evidence_structurally_valid
                ),
                "approved": False,
            },
            "lifecycleRelationshipsComplete": (
                self.lifecycle_relationships_complete
            ),
            "policy": {
                "approved": False,
                "registeredPolicyCount": 0,
            },
            "coverageComplete": False,
            "gate": {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _safe_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SAFE_ID_PATTERN.fullmatch(value) is not None
    )


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _trusted_listing_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
    ):
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in TRUSTED_LISTING_DOMAINS
    )


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _policy_is_approved(value: Any) -> bool:
    if (
        type(value) is not LeaderOfficialRiskCoveragePolicyApproval
        or not _safe_id(value.policy_id)
        or not _safe_id(value.policy_version)
        or not _safe_id(value.approved_by)
        or _aware(value.approved_at) is None
        or not isinstance(value.source_contract_ids, tuple)
        or not value.source_contract_ids
        or any(
            not _safe_id(contract_id)
            for contract_id in value.source_contract_ids
        )
        or len(value.source_contract_ids)
        != len(set(value.source_contract_ids))
        or not isinstance(value.category_query_versions, tuple)
        or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or item[0] not in REQUIRED_RISK_CATEGORIES
            or not _safe_id(item[1])
            for item in value.category_query_versions
        )
        or tuple(item[0] for item in value.category_query_versions)
        != REQUIRED_RISK_CATEGORIES
    ):
        return False
    registered = _APPROVED_COVERAGE_POLICIES.get((
        value.policy_id,
        value.policy_version,
    ))
    return registered is not None and value == registered


def _listing_reasons(
    evidence: LeaderOfficialRiskIssuerListingEvidence,
    *,
    symbol: str,
    issuer_identity: Optional[str],
    as_of: datetime,
) -> Tuple[str, ...]:
    if type(evidence) is not LeaderOfficialRiskIssuerListingEvidence:
        return ("risk_official_coverage_listing_contract_unverified",)
    reasons = []
    if (
        evidence.symbol != symbol
        or evidence.issuer_identity != issuer_identity
        or not _safe_id(evidence.symbol)
        or not _safe_id(evidence.issuer_identity)
    ):
        reasons.append(
            "risk_official_coverage_listing_identity_unverified"
        )
    if (
        not _safe_id(evidence.source_contract_id)
        or not _required_text(evidence.source_name)
        or not _trusted_listing_url(evidence.source_url)
    ):
        reasons.append("risk_official_coverage_listing_source_unverified")
    listed_at = _aware(evidence.issuer_listed_at)
    fetched_at = _aware(evidence.fetched_at)
    if (
        listed_at is None
        or fetched_at is None
        or listed_at > as_of
        or fetched_at > as_of
        or fetched_at < listed_at
    ):
        reasons.append("risk_official_coverage_listing_time_unverified")
    if (
        not isinstance(evidence.record_checksum, str)
        or SHA256_PATTERN.fullmatch(evidence.record_checksum) is None
    ):
        reasons.append(
            "risk_official_coverage_listing_checksum_unverified"
        )
    return _dedupe(reasons)


def _result(
    *,
    status: LeaderOfficialRiskCoverageStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    reasons: Sequence[str],
    items: Sequence[LeaderOfficialRiskCoverageItem] = (),
) -> LeaderOfficialRiskCoverageReadinessResult:
    item_values = tuple(items)
    result = LeaderOfficialRiskCoverageReadinessResult(
        status=status,
        radar_run_id=plan.radar_run_id if plan is not None else None,
        candidate_plan_id=(
            plan.candidate_set_id if plan is not None else None
        ),
        as_of=plan.as_of if plan is not None else None,
        items=item_values,
        reasons=_dedupe(reasons),
        listing_evidence_structurally_valid=bool(item_values) and all(
            item.listing_evidence_structurally_valid
            for item in item_values
        ),
        lifecycle_relationships_complete=bool(item_values) and all(
            item.lifecycle_relationships_complete for item in item_values
        ),
    )
    object.__setattr__(
        result,
        "_producer_token",
        _COVERAGE_PRODUCER_TOKEN,
    )
    return result


def build_leader_official_risk_coverage_readiness(
    *,
    candidate_plan: Any,
    lifecycle: Any,
    listing_evidence: Any,
    policy_approval: Any = None,
) -> LeaderOfficialRiskCoverageReadinessResult:
    """审计覆盖证明前置证据；当前正式政策未批准，始终失败关闭。"""

    plan = (
        candidate_plan
        if isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        else None
    )
    if not is_leader_runtime_candidate_plan_valid(candidate_plan):
        return _result(
            status=LeaderOfficialRiskCoverageStatus.SOURCE_UNVERIFIED,
            plan=plan,
            reasons=(
                "risk_official_coverage_candidate_plan_unverified",
            ),
        )
    if not is_leader_official_risk_lifecycle_valid(
        lifecycle,
        candidate_plan=candidate_plan,
    ):
        return _result(
            status=LeaderOfficialRiskCoverageStatus.SOURCE_UNVERIFIED,
            plan=candidate_plan,
            reasons=(
                "risk_official_coverage_lifecycle_unverified",
                "risk_official_coverage_policy_unapproved",
                "risk_official_keyword_discovery_not_coverage_proof",
            ),
        )
    assert type(lifecycle) is LeaderOfficialRiskLifecycleResult
    if not isinstance(listing_evidence, tuple):
        return _result(
            status=LeaderOfficialRiskCoverageStatus.SOURCE_UNVERIFIED,
            plan=candidate_plan,
            reasons=(
                "risk_official_coverage_listing_contract_unverified",
                "risk_official_coverage_policy_unapproved",
                "risk_official_keyword_discovery_not_coverage_proof",
            ),
        )
    if not listing_evidence:
        return _result(
            status=LeaderOfficialRiskCoverageStatus.MISSING,
            plan=candidate_plan,
            reasons=(
                "risk_official_coverage_listing_evidence_missing",
                "risk_official_coverage_policy_unapproved",
                "risk_official_keyword_discovery_not_coverage_proof",
            ),
        )
    if (
        len(listing_evidence) != len(lifecycle.items)
        or len({
            getattr(value, "symbol", None) for value in listing_evidence
        }) != len(listing_evidence)
    ):
        return _result(
            status=LeaderOfficialRiskCoverageStatus.SOURCE_UNVERIFIED,
            plan=candidate_plan,
            reasons=(
                "risk_official_coverage_listing_identity_unverified",
                "risk_official_coverage_policy_unapproved",
                "risk_official_keyword_discovery_not_coverage_proof",
            ),
        )

    policy_approved = _policy_is_approved(policy_approval)
    items = []
    batch_reasons = []
    invalid_listing = False
    relationships_complete = True
    for index, (plan_item, lifecycle_item, evidence) in enumerate(zip(
        candidate_plan.items,
        lifecycle.items,
        listing_evidence,
    )):
        evidence_reasons = _listing_reasons(
            evidence,
            symbol=plan_item.symbol,
            issuer_identity=lifecycle_item.issuer_identity,
            as_of=candidate_plan.as_of,
        )
        evidence_valid = not evidence_reasons
        relationship_complete = all((
            lifecycle_item.open_event_carry_forward_complete,
            lifecycle_item.correction_links_complete,
            lifecycle_item.resolution_links_complete,
        ))
        invalid_listing = invalid_listing or not evidence_valid
        relationships_complete = (
            relationships_complete and relationship_complete
        )
        item_reasons = list(evidence_reasons)
        if not relationship_complete:
            item_reasons.append(
                "risk_official_coverage_lifecycle_relationships_incomplete"
            )
        item_reasons.extend((
            "risk_official_coverage_policy_unapproved",
            "risk_official_keyword_discovery_not_coverage_proof",
        ))
        items.append(LeaderOfficialRiskCoverageItem(
            index=index,
            symbol=plan_item.symbol,
            issuer_identity=lifecycle_item.issuer_identity,
            status=(
                LeaderOfficialRiskCoverageStatus.SOURCE_UNVERIFIED
                if evidence_reasons
                else LeaderOfficialRiskCoverageStatus.POLICY_UNAPPROVED
            ),
            reasons=_dedupe(item_reasons),
            listing_evidence_structurally_valid=evidence_valid,
            lifecycle_relationships_complete=relationship_complete,
            policy_approved=policy_approved,
        ))
        batch_reasons.extend(evidence_reasons)

    if not relationships_complete:
        batch_reasons.append(
            "risk_official_coverage_lifecycle_relationships_incomplete"
        )
    batch_reasons.extend((
        "risk_official_coverage_policy_unapproved",
        "risk_official_keyword_discovery_not_coverage_proof",
    ))
    return _result(
        status=(
            LeaderOfficialRiskCoverageStatus.SOURCE_UNVERIFIED
            if invalid_listing
            else LeaderOfficialRiskCoverageStatus.POLICY_UNAPPROVED
        ),
        plan=candidate_plan,
        reasons=batch_reasons,
        items=items,
    )


def is_leader_official_risk_coverage_readiness_valid(
    value: Any,
    *,
    candidate_plan: Any,
) -> bool:
    """验证结果确由本模块生成，且所有正式字段继续失败关闭。"""

    if (
        type(value) is not LeaderOfficialRiskCoverageReadinessResult
        or value._producer_token is not _COVERAGE_PRODUCER_TOKEN
        or value.contract_id != LEADER_OFFICIAL_RISK_COVERAGE_CONTRACT_ID
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or value.radar_run_id != candidate_plan.radar_run_id
        or value.candidate_plan_id != candidate_plan.candidate_set_id
        or _aware(value.as_of) != candidate_plan.as_of
        or len(value.items) != candidate_plan.candidate_count
        or any((
            value.policy_approved is not False,
            value.issuer_listing_evidence_approved is not False,
            value.coverage_complete is not False,
            value.risk_filter_passed is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
    ):
        return False
    for index, (item, plan_item) in enumerate(zip(
        value.items,
        candidate_plan.items,
    )):
        if (
            type(item) is not LeaderOfficialRiskCoverageItem
            or item.index != index
            or item.symbol != plan_item.symbol
            or not isinstance(item.status, LeaderOfficialRiskCoverageStatus)
            or not isinstance(item.reasons, tuple)
            or any(not isinstance(reason, str) or not reason for reason in item.reasons)
            or any((
                item.policy_approved is not False,
                item.issuer_listing_evidence_approved is not False,
                item.coverage_complete is not False,
                item.risk_filter_passed is not False,
                item.formal_gate_ready is not False,
                item.formal_usable is not False,
                item.state_transition_allowed is not False,
            ))
        ):
            return False
    return bool(
        value.items
        and "risk_official_coverage_policy_unapproved" in value.reasons
        and "risk_official_keyword_discovery_not_coverage_proof"
        in value.reasons
    )
