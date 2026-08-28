"""阶段6官方确定性风险证据。

本模块只重放同轮候选计划的巨潮七类完整查询，并保留可选的官方PDF
正文与现有确定性事实提取结果。窗口内零命中只表示该查询窗口未发现
公告，不表示公司没有风险；有公告但未下载正文时，只交付官方发现元数据，
不产生结构化风险事实。它不生成D8人工版本、不调用AI，也不打开风险过滤
或正式状态门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_candidate_projection_batch import (
    LeaderRiskCandidateProjectionBatchItem,
    LeaderRiskCandidateProjectionBatchResult,
    LeaderRiskCandidateProjectionBatchStatus,
)
from radar.leader_risk_document_facts import (
    OfficialRiskDocumentFactInput,
    RiskDocumentFact,
    extract_official_risk_document_facts,
)
from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleBatchEntry,
    LeaderRiskLifecycleBatchInput,
    LeaderRiskLifecycleBatchResult,
    build_leader_risk_lifecycle_batch,
)
from radar.leader_risk_lifecycle_delivery import (
    LeaderRiskLifecycleDeliveryResult,
    LeaderRiskLifecycleDeliveryStatus,
)
from radar.leader_risk_invalidation_features import (
    ALL_RISK_CATEGORIES,
    MAXIMUM_COVERAGE_AGE_SECONDS,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_risk_candidate_discovery import (
    LeaderRiskOfficialCandidateDiscoveryItem,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    RISK_DOCUMENT_CONTENT_CONTRACT_ID,
)
from radar.sources.leader_risk_official import (
    CNINFO_ISSUER_SCOPE_CONTRACT_ID,
    CNINFO_SOURCE_CONTRACT_ID,
    CninfoRiskIssuerScope,
    OfficialRiskDiscoveryBatch,
    OfficialRiskDocumentMetadata,
    OfficialRiskSourceStatus,
)


UTC = timezone.utc
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LEADER_OFFICIAL_DETERMINISTIC_RISK_CONTRACT_ID = (
    "radar-leader-official-deterministic-risk-batch-v1"
)
LEADER_OFFICIAL_DETERMINISTIC_RISK_PROJECTION_CONTRACT_ID = (
    "radar-leader-official-deterministic-risk-projection-v1"
)
LEADER_OFFICIAL_DETERMINISTIC_RISK_FROZEN_CONTRACT_ID = (
    "radar-leader-official-deterministic-risk-frozen-v1"
)
LEADER_OFFICIAL_DETERMINISTIC_RISK_SOURCE_CONTRACT_ID = (
    "radar-leader-official-deterministic-risk-source-v1"
)
_OFFICIAL_DETERMINISTIC_RISK_PRODUCER_TOKEN = object()


class LeaderOfficialDeterministicRiskStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


class LeaderOfficialDeterministicRiskEvidenceKind(str, Enum):
    BOUNDED_NO_DISCLOSURE = "bounded_no_disclosure_in_window"
    OFFICIAL_DISCOVERY_METADATA = "official_discovery_metadata"
    OFFICIAL_DOCUMENT_EVIDENCE = "official_document_evidence"


@dataclass(frozen=True)
class LeaderOfficialDeterministicRiskProjection:
    projection_id: str
    symbol: str
    issuer_identity: str
    as_of: datetime
    radar_run_id: str
    candidate_plan_id: str
    quote_batch_id: str
    window_from: date
    window_until: date
    evidence_kind: LeaderOfficialDeterministicRiskEvidenceKind
    query_category_count: int
    query_page_count: int
    document_ids: Tuple[str, ...] = ()
    document_titles: Tuple[str, ...] = ()
    document_categories: Tuple[str, ...] = ()
    document_urls: Tuple[str, ...] = ()
    document_published_at: Tuple[datetime, ...] = ()
    content_sha256s: Tuple[str, ...] = ()
    content_fetched_at: Tuple[datetime, ...] = ()
    deterministic_fact_ids: Tuple[str, ...] = ()
    manual_fact_ids: Tuple[str, ...] = ()
    source_contract_ids: Tuple[str, ...] = (
        CNINFO_SOURCE_CONTRACT_ID,
        CNINFO_ISSUER_SCOPE_CONTRACT_ID,
    )
    projection_contract_id: str = (
        LEADER_OFFICIAL_DETERMINISTIC_RISK_PROJECTION_CONTRACT_ID
    )
    risk_filter_passed: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    applied_to_d3: bool = False
    applied_to_d1: bool = False

    @property
    def formal_gate_gaps(self) -> Tuple[str, ...]:
        return (
            "risk_official_open_event_carry_forward_not_proven",
            "risk_official_correction_links_not_proven",
            "risk_official_formal_gate_disabled",
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "projectionContractId": self.projection_contract_id,
            "projectionId": self.projection_id,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "asOf": self.as_of.isoformat(),
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "quoteBatchId": self.quote_batch_id,
            "claimScope": "bounded_official_query_window",
            "windowFrom": self.window_from.isoformat(),
            "windowUntil": self.window_until.isoformat(),
            "evidenceKind": self.evidence_kind.value,
            "queryCategoryCount": self.query_category_count,
            "queryPageCount": self.query_page_count,
            "documentIds": list(self.document_ids),
            "documentTitles": list(self.document_titles),
            "documentCategories": list(self.document_categories),
            "documentUrls": list(self.document_urls),
            "documentPublishedAt": [
                value.isoformat() for value in self.document_published_at
            ],
            "contentSha256s": list(self.content_sha256s),
            "contentFetchedAt": [
                value.isoformat() for value in self.content_fetched_at
            ],
            "deterministicFactIds": list(self.deterministic_fact_ids),
            "manualFactIds": [],
            "sourceContractIds": list(self.source_contract_ids),
            "formalGateGaps": list(self.formal_gate_gaps),
            "gate": {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "appliedToD3": False,
                "appliedToD1": False,
            },
        }


@dataclass(frozen=True)
class LeaderOfficialDeterministicRiskBatchResult:
    status: LeaderOfficialDeterministicRiskStatus
    radar_run_id: Optional[str]
    candidate_plan_id: Optional[str]
    quote_batch_id: Optional[str]
    as_of: Optional[datetime]
    window_from: Optional[date]
    window_until: Optional[date]
    candidate_count: int
    projection_batch: LeaderRiskCandidateProjectionBatchResult = field(
        repr=False
    )
    source_time: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_OFFICIAL_DETERMINISTIC_RISK_CONTRACT_ID
    risk_filter_passed: bool = False
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False
    _producer_token: Any = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def ready_count(self) -> int:
        return self.projection_batch.ready_count

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "quoteBatchId": self.quote_batch_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "claimScope": "bounded_official_query_window",
            "windowFrom": self.window_from.isoformat() if self.window_from else None,
            "windowUntil": self.window_until.isoformat() if self.window_until else None,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "reasons": list(self.reasons),
            "items": [
                {
                    **item.to_evidence(),
                    "projection": (
                        item.projection.to_evidence()
                        if isinstance(
                            item.projection,
                            LeaderOfficialDeterministicRiskProjection,
                        )
                        else None
                    ),
                }
                for item in self.projection_batch.items
            ],
            "gate": {
                "riskFilterPassed": False,
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True, repr=False)
class LeaderOfficialDeterministicRiskFrozenBatch:
    delivery: Any = field(repr=False)
    document_contents: Any = field(repr=False)
    contract_id: str = LEADER_OFFICIAL_DETERMINISTIC_RISK_FROZEN_CONTRACT_ID


def _aware(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _projection_identity(values: Mapping[str, object]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "official-risk:" + hashlib.sha256(encoded).hexdigest()


def _projection_payload(
    *,
    symbol: str,
    issuer_identity: str,
    plan: LeaderRuntimeCandidatePlan,
    delivery: LeaderRiskLifecycleDeliveryResult,
    evidence_kind: LeaderOfficialDeterministicRiskEvidenceKind,
    documents: Sequence[OfficialRiskDocumentMetadata],
    contents: Sequence[OfficialRiskDocumentContentResult],
    facts: Sequence[RiskDocumentFact],
) -> Mapping[str, object]:
    return {
        "symbol": symbol,
        "issuerIdentity": issuer_identity,
        "asOf": plan.as_of.isoformat(),
        "radarRunId": plan.radar_run_id,
        "candidatePlanId": plan.candidate_set_id,
        "quoteBatchId": plan.quote_batch_id,
        "windowFrom": delivery.window_from.isoformat(),
        "windowUntil": delivery.window_until.isoformat(),
        "evidenceKind": evidence_kind.value,
        "queryCategoryCount": delivery.category_count,
        "queryPageCount": delivery.fetched_page_count,
        "documentIds": [value.document_id for value in documents],
        "documentTitles": [value.title for value in documents],
        "documentCategories": [
            value.candidate_category.value for value in documents
        ],
        "documentUrls": [value.source_url for value in documents],
        "documentPublishedAt": [
            value.published_at.astimezone(UTC).isoformat() for value in documents
        ],
        "contentSha256s": [value.content_sha256 for value in contents],
        "contentFetchedAt": [
            value.fetched_at.astimezone(UTC).isoformat() for value in contents
        ],
        "deterministicFactIds": [value.fact_id for value in facts],
    }


def _build_projection(
    *,
    symbol: str,
    issuer_identity: str,
    plan: LeaderRuntimeCandidatePlan,
    delivery: LeaderRiskLifecycleDeliveryResult,
    evidence_kind: LeaderOfficialDeterministicRiskEvidenceKind,
    documents: Sequence[OfficialRiskDocumentMetadata] = (),
    contents: Sequence[OfficialRiskDocumentContentResult] = (),
    facts: Sequence[RiskDocumentFact] = (),
) -> LeaderOfficialDeterministicRiskProjection:
    payload = _projection_payload(
        symbol=symbol,
        issuer_identity=issuer_identity,
        plan=plan,
        delivery=delivery,
        evidence_kind=evidence_kind,
        documents=documents,
        contents=contents,
        facts=facts,
    )
    return LeaderOfficialDeterministicRiskProjection(
        projection_id=_projection_identity(payload),
        symbol=symbol,
        issuer_identity=issuer_identity,
        as_of=plan.as_of,
        radar_run_id=plan.radar_run_id,
        candidate_plan_id=plan.candidate_set_id,
        quote_batch_id=plan.quote_batch_id,
        window_from=delivery.window_from,
        window_until=delivery.window_until,
        evidence_kind=evidence_kind,
        query_category_count=delivery.category_count,
        query_page_count=delivery.fetched_page_count,
        document_ids=tuple(value.document_id for value in documents),
        document_titles=tuple(value.title for value in documents),
        document_categories=tuple(
            value.candidate_category.value for value in documents
        ),
        document_urls=tuple(value.source_url for value in documents),
        document_published_at=tuple(
            value.published_at.astimezone(UTC) for value in documents
        ),
        content_sha256s=tuple(value.content_sha256 for value in contents),
        content_fetched_at=tuple(
            value.fetched_at.astimezone(UTC) for value in contents
        ),
        deterministic_fact_ids=tuple(value.fact_id for value in facts),
    )


def is_leader_official_deterministic_risk_projection_valid(
    value: Any,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    if (
        type(value) is not LeaderOfficialDeterministicRiskProjection
        or value.projection_contract_id
        != LEADER_OFFICIAL_DETERMINISTIC_RISK_PROJECTION_CONTRACT_ID
        or value.symbol != symbol
        or _aware(value.as_of) != _aware(as_of)
        or not value.issuer_identity.startswith("cninfo-org:")
        or value.window_from > value.window_until
        or value.query_category_count != len(ALL_RISK_CATEGORIES)
        or value.query_page_count < len(ALL_RISK_CATEGORIES)
        or value.manual_fact_ids
        or value.source_contract_ids
        != (
            CNINFO_SOURCE_CONTRACT_ID,
            CNINFO_ISSUER_SCOPE_CONTRACT_ID,
        )
        or any((
            value.risk_filter_passed is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.applied_to_d3 is not False,
            value.applied_to_d1 is not False,
        ))
        or len(value.document_ids) != len(set(value.document_ids))
        or not (
            len(value.document_ids)
            == len(value.document_titles)
            == len(value.document_categories)
            == len(value.document_urls)
            == len(value.document_published_at)
        )
        or len(value.content_sha256s) != len(value.content_fetched_at)
        or any(SHA256_PATTERN.fullmatch(value_) is None for value_ in value.content_sha256s)
        or any(_aware(value_) is None or _aware(value_) > _aware(value.as_of) for value_ in value.document_published_at)
        or any(
            _aware(value_) is None
            or (
                _aware(value_) - _aware(value.as_of)
            ).total_seconds() > MAXIMUM_COVERAGE_AGE_SECONDS
            for value_ in value.content_fetched_at
        )
        or (
            value.evidence_kind
            == LeaderOfficialDeterministicRiskEvidenceKind.BOUNDED_NO_DISCLOSURE
            and any((value.document_ids, value.deterministic_fact_ids))
        )
        or (
            value.evidence_kind
            == LeaderOfficialDeterministicRiskEvidenceKind.OFFICIAL_DISCOVERY_METADATA
            and (
                not value.document_ids
                or value.content_sha256s
                or value.deterministic_fact_ids
            )
        )
        or (
            value.evidence_kind
            == LeaderOfficialDeterministicRiskEvidenceKind.OFFICIAL_DOCUMENT_EVIDENCE
            and (
                not value.document_ids
                or len(value.content_sha256s) != len(value.document_ids)
            )
        )
    ):
        return False
    payload = {
        "symbol": value.symbol,
        "issuerIdentity": value.issuer_identity,
        "asOf": value.as_of.isoformat(),
        "radarRunId": value.radar_run_id,
        "candidatePlanId": value.candidate_plan_id,
        "quoteBatchId": value.quote_batch_id,
        "windowFrom": value.window_from.isoformat(),
        "windowUntil": value.window_until.isoformat(),
        "evidenceKind": value.evidence_kind.value,
        "queryCategoryCount": value.query_category_count,
        "queryPageCount": value.query_page_count,
        "documentIds": list(value.document_ids),
        "documentTitles": list(value.document_titles),
        "documentCategories": list(value.document_categories),
        "documentUrls": list(value.document_urls),
        "documentPublishedAt": [item.isoformat() for item in value.document_published_at],
        "contentSha256s": list(value.content_sha256s),
        "contentFetchedAt": [item.isoformat() for item in value.content_fetched_at],
        "deterministicFactIds": list(value.deterministic_fact_ids),
    }
    return value.projection_id == _projection_identity(payload)


def is_leader_official_deterministic_risk_batch_valid(
    value: Any,
    *,
    candidate_plan: Any,
) -> bool:
    if (
        type(value) is not LeaderOfficialDeterministicRiskBatchResult
        or value._producer_token
        is not _OFFICIAL_DETERMINISTIC_RISK_PRODUCER_TOKEN
        or value.contract_id
        != LEADER_OFFICIAL_DETERMINISTIC_RISK_CONTRACT_ID
        or value.status != LeaderOfficialDeterministicRiskStatus.READY
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or value.radar_run_id != candidate_plan.radar_run_id
        or value.candidate_plan_id != candidate_plan.candidate_set_id
        or value.quote_batch_id != candidate_plan.quote_batch_id
        or _aware(value.as_of) != candidate_plan.as_of
        or value.candidate_count != candidate_plan.candidate_count
        or not isinstance(value.window_from, date)
        or not isinstance(value.window_until, date)
        or value.window_from > value.window_until
        or _aware(value.source_time) is None
        or _aware(value.source_time) > candidate_plan.as_of
        or _aware(value.fetched_at) is None
        or _aware(value.fetched_at) < _aware(value.source_time)
        or value.reasons
        or any((
            value.risk_filter_passed is not False,
            value.formal_score_ready is not False,
            value.formal_gate_ready is not False,
            value.formal_usable is not False,
            value.state_transition_allowed is not False,
        ))
        or value.projection_batch.status
        != LeaderRiskCandidateProjectionBatchStatus.READY
        or value.projection_batch.as_of != candidate_plan.as_of
        or value.projection_batch.input_count
        != candidate_plan.candidate_count
        or value.projection_batch.reasons
        or len(value.projection_batch.items)
        != candidate_plan.candidate_count
    ):
        return False
    return all(
        item.index == index
        and item.symbol == plan_item.symbol
        and item.input_symbol == plan_item.symbol
        and item.status == ResearchFeatureStatus.READY
        and not item.reasons
        and is_leader_official_deterministic_risk_projection_valid(
            item.projection,
            symbol=plan_item.symbol,
            as_of=candidate_plan.as_of,
        )
        and item.projection.radar_run_id == candidate_plan.radar_run_id
        and item.projection.candidate_plan_id
        == candidate_plan.candidate_set_id
        and item.projection.quote_batch_id == candidate_plan.quote_batch_id
        for index, (item, plan_item) in enumerate(zip(
            value.projection_batch.items,
            candidate_plan.items,
        ))
    )


def is_supported_leader_risk_projection(
    value: Any,
    *,
    symbol: str,
    as_of: datetime,
) -> bool:
    return is_leader_official_deterministic_risk_projection_valid(
        value,
        symbol=symbol,
        as_of=as_of,
    )


def _empty_projection_batch(
    plan: Optional[LeaderRuntimeCandidatePlan],
    *,
    status: ResearchFeatureStatus,
    reason: str,
) -> LeaderRiskCandidateProjectionBatchResult:
    items = tuple(
        LeaderRiskCandidateProjectionBatchItem(
            index=index,
            symbol=item.symbol,
            input_symbol=item.symbol,
            status=status,
            reasons=(reason,),
        )
        for index, item in enumerate(plan.items if plan is not None else ())
    )
    batch_status = {
        ResearchFeatureStatus.SOURCE_FAILED: LeaderRiskCandidateProjectionBatchStatus.SOURCE_FAILED,
        ResearchFeatureStatus.SOURCE_UNVERIFIED: LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
    }.get(status, LeaderRiskCandidateProjectionBatchStatus.MISSING)
    return LeaderRiskCandidateProjectionBatchResult(
        status=batch_status,
        as_of=plan.as_of if plan is not None else None,
        input_count=len(items),
        items=items,
        reasons=("risk_candidate_projection_batch_no_ready_items",),
    )


def _result(
    *,
    status: LeaderOfficialDeterministicRiskStatus,
    plan: Optional[LeaderRuntimeCandidatePlan],
    delivery: Optional[LeaderRiskLifecycleDeliveryResult],
    projection_batch: LeaderRiskCandidateProjectionBatchResult,
    reasons: Sequence[str],
    source_time: Optional[datetime] = None,
    fetched_at: Optional[datetime] = None,
) -> LeaderOfficialDeterministicRiskBatchResult:
    result = LeaderOfficialDeterministicRiskBatchResult(
        status=status,
        radar_run_id=plan.radar_run_id if plan is not None else None,
        candidate_plan_id=plan.candidate_set_id if plan is not None else None,
        quote_batch_id=plan.quote_batch_id if plan is not None else None,
        as_of=plan.as_of if plan is not None else None,
        window_from=delivery.window_from if delivery is not None else None,
        window_until=delivery.window_until if delivery is not None else None,
        candidate_count=plan.candidate_count if plan is not None else 0,
        projection_batch=projection_batch,
        source_time=source_time,
        fetched_at=fetched_at,
        reasons=_dedupe(reasons),
    )
    object.__setattr__(
        result,
        "_producer_token",
        _OFFICIAL_DETERMINISTIC_RISK_PRODUCER_TOKEN,
    )
    return result


def _failed(
    plan: Optional[LeaderRuntimeCandidatePlan],
    delivery: Optional[LeaderRiskLifecycleDeliveryResult],
    *,
    status: LeaderOfficialDeterministicRiskStatus,
    reason: str,
) -> LeaderOfficialDeterministicRiskBatchResult:
    feature_status = {
        LeaderOfficialDeterministicRiskStatus.SOURCE_FAILED: ResearchFeatureStatus.SOURCE_FAILED,
        LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED: ResearchFeatureStatus.SOURCE_UNVERIFIED,
    }.get(status, ResearchFeatureStatus.MISSING)
    return _result(
        status=status,
        plan=plan,
        delivery=delivery,
        projection_batch=_empty_projection_batch(plan, status=feature_status, reason=reason),
        reasons=(reason,),
    )


def _content_valid(
    content: Any,
    *,
    document: OfficialRiskDocumentMetadata,
    as_of: datetime,
) -> bool:
    fetched_at = _aware(getattr(content, "fetched_at", None))
    published_at = _aware(document.published_at)
    return bool(
        type(content) is OfficialRiskDocumentContentResult
        and content.content_contract_id == RISK_DOCUMENT_CONTENT_CONTRACT_ID
        and content.status == ResearchFeatureStatus.READY
        and not content.reasons
        and content.formal_usable is False
        and content.document_id == document.document_id
        and content.symbol == document.symbol
        and content.issuer_identity == document.issuer_identity
        and isinstance(content.content_sha256, str)
        and SHA256_PATTERN.fullmatch(content.content_sha256) is not None
        and isinstance(content.byte_count, int)
        and not isinstance(content.byte_count, bool)
        and content.byte_count > 0
        and isinstance(content.page_count, int)
        and not isinstance(content.page_count, bool)
        and content.page_count == len(content.pages)
        and content.page_count > 0
        and fetched_at is not None
        and published_at is not None
        and published_at <= fetched_at
        and (
            fetched_at - as_of
        ).total_seconds() <= MAXIMUM_COVERAGE_AGE_SECONDS
    )


def _issuer_by_symbol(
    plan: LeaderRuntimeCandidatePlan,
    delivery: LeaderRiskLifecycleDeliveryResult,
) -> Optional[Mapping[str, str]]:
    scopes = delivery.candidate_scopes
    expected = tuple(item.symbol for item in plan.items)
    delivery_at = _aware(delivery.as_of)
    if (
        not isinstance(scopes, tuple)
        or len(scopes) != len(expected)
        or tuple(getattr(item, "symbol", None) for item in scopes) != expected
        or any(
            type(scope) is not CninfoRiskIssuerScope
            or scope.source_contract_id != CNINFO_ISSUER_SCOPE_CONTRACT_ID
            or not scope.issuer_identity.startswith("cninfo-org:")
            or _aware(scope.resolved_at) is None
            or _aware(scope.resolved_at) < plan.as_of
            or delivery_at is None
            or _aware(scope.resolved_at) > delivery_at
            for scope in scopes
        )
    ):
        return None
    return {scope.symbol: scope.issuer_identity for scope in scopes}


def build_leader_official_deterministic_risk_batch(
    *,
    candidate_plan: Any,
    delivery: Any,
    document_contents: Any,
) -> LeaderOfficialDeterministicRiskBatchResult:
    """把完整官方查询重放成可追溯风险输入，绝不推导正式通过。"""

    plan = candidate_plan if isinstance(candidate_plan, LeaderRuntimeCandidatePlan) else None
    if (
        plan is None
        or not is_leader_runtime_candidate_plan_valid(plan)
        or type(delivery) is not LeaderRiskLifecycleDeliveryResult
        or not isinstance(document_contents, tuple)
    ):
        return _failed(
            plan,
            delivery if isinstance(delivery, LeaderRiskLifecycleDeliveryResult) else None,
            status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
            reason="risk_official_deterministic_contract_unverified",
        )
    lifecycle = delivery.lifecycle_result
    delivery_at = _aware(delivery.as_of)
    if (
        delivery.candidate_plan_id != plan.candidate_set_id
        or delivery.radar_run_id != plan.radar_run_id
        or delivery.quote_batch_id != plan.quote_batch_id
        or delivery_at is None
        or delivery_at < plan.as_of
        or (
            delivery_at - plan.as_of
        ).total_seconds() > MAXIMUM_COVERAGE_AGE_SECONDS
        or delivery.status
        not in {LeaderRiskLifecycleDeliveryStatus.MISSING, LeaderRiskLifecycleDeliveryStatus.PARTIAL}
        or type(lifecycle) is not LeaderRiskLifecycleBatchResult
        or lifecycle.candidate_plan_id != plan.candidate_set_id
        or lifecycle.radar_run_id != plan.radar_run_id
        or lifecycle.quote_batch_id != plan.quote_batch_id
        or _aware(lifecycle.as_of) != plan.as_of
        or not all((
            lifecycle.query_categories_complete,
            lifecycle.query_pages_complete,
            lifecycle.query_window_continuous,
        ))
        or len(lifecycle.items) != plan.candidate_count
        or any(item.version_count != 0 for item in lifecycle.items)
        or not isinstance(delivery.discovery_batches, tuple)
        or not delivery.discovery_batches
        or any(
            type(batch) is not OfficialRiskDiscoveryBatch
            or batch.status != OfficialRiskSourceStatus.PARTIAL
            for batch in delivery.discovery_batches
        )
    ):
        return _failed(
            plan,
            delivery,
            status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
            reason="risk_official_deterministic_query_coverage_unverified",
        )
    issuer_by_symbol = _issuer_by_symbol(plan, delivery)
    discovery = lifecycle.discovery_result
    if issuer_by_symbol is None or discovery is None:
        return _failed(
            plan,
            delivery,
            status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
            reason="risk_official_deterministic_issuer_scope_unverified",
        )
    replayed_lifecycle = build_leader_risk_lifecycle_batch(
        LeaderRiskLifecycleBatchInput(
            candidate_plan=plan,
            discovery_batches=delivery.discovery_batches,
            entries=tuple(
                LeaderRiskLifecycleBatchEntry(
                    symbol=item.symbol,
                    issuer_identity=issuer_by_symbol[item.symbol],
                    versions=(),
                )
                for item in plan.items
            ),
        )
    )
    if replayed_lifecycle != lifecycle:
        return _failed(
            plan,
            delivery,
            status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
            reason="risk_official_deterministic_lifecycle_replay_mismatch",
        )
    contents_by_document = {}
    for content in document_contents:
        document_id = getattr(content, "document_id", None)
        if not isinstance(document_id, str) or document_id in contents_by_document:
            return _failed(
                plan,
                delivery,
                status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
                reason="risk_official_deterministic_content_set_unverified",
            )
        contents_by_document[document_id] = content
    discovered_id_sequence = tuple(
        document.document.document_id
        for item in discovery.items
        for document in item.documents
    )
    discovered_ids = set(discovered_id_sequence)
    if len(discovered_id_sequence) != len(discovered_ids):
        return _failed(
            plan,
            delivery,
            status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
            reason="risk_official_deterministic_document_identity_duplicate",
        )
    if not set(contents_by_document).issubset(discovered_ids):
        return _failed(
            plan,
            delivery,
            status=LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED,
            reason="risk_official_deterministic_content_set_unverified",
        )

    items = []
    all_published = []
    all_fetched = [
        _aware(batch.fetched_at) for batch in delivery.discovery_batches
    ]
    for index, plan_item in enumerate(plan.items):
        discovery_item: LeaderRiskOfficialCandidateDiscoveryItem = discovery.items[index]
        issuer_identity = issuer_by_symbol[plan_item.symbol]
        documents = tuple(value.document for value in discovery_item.documents)
        if not documents:
            projection = _build_projection(
                symbol=plan_item.symbol,
                issuer_identity=issuer_identity,
                plan=plan,
                delivery=delivery,
                evidence_kind=LeaderOfficialDeterministicRiskEvidenceKind.BOUNDED_NO_DISCLOSURE,
            )
            items.append(LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=plan_item.symbol,
                input_symbol=plan_item.symbol,
                status=ResearchFeatureStatus.READY,
                projection=projection,
            ))
            continue
        if any(document.issuer_identity != issuer_identity for document in documents):
            items.append(LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=plan_item.symbol,
                input_symbol=plan_item.symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_official_deterministic_document_identity_unverified",),
            ))
            continue
        if not document_contents:
            projection = _build_projection(
                symbol=plan_item.symbol,
                issuer_identity=issuer_identity,
                plan=plan,
                delivery=delivery,
                evidence_kind=(
                    LeaderOfficialDeterministicRiskEvidenceKind
                    .OFFICIAL_DISCOVERY_METADATA
                ),
                documents=documents,
            )
            items.append(LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=plan_item.symbol,
                input_symbol=plan_item.symbol,
                status=ResearchFeatureStatus.READY,
                projection=projection,
            ))
            all_published.extend(
                _aware(document.published_at) for document in documents
            )
            continue
        missing_ids = tuple(
            document.document_id for document in documents
            if document.document_id not in contents_by_document
        )
        if missing_ids:
            items.append(LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=plan_item.symbol,
                input_symbol=plan_item.symbol,
                status=ResearchFeatureStatus.MISSING,
                reasons=("risk_official_deterministic_document_content_missing",),
            ))
            continue
        contents = tuple(contents_by_document[document.document_id] for document in documents)
        if any(
            not _content_valid(content, document=document, as_of=plan.as_of)
            for document, content in zip(documents, contents)
        ):
            items.append(LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=plan_item.symbol,
                input_symbol=plan_item.symbol,
                status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                reasons=("risk_official_deterministic_document_content_unverified",),
            ))
            continue
        facts = []
        facts_valid = True
        for document, content in zip(documents, contents):
            replay = extract_official_risk_document_facts(
                OfficialRiskDocumentFactInput(
                    as_of=max(plan.as_of, content.fetched_at),
                    document=document,
                    content_sha256=content.content_sha256,
                    pages=content.pages,
                    extracted_at=content.fetched_at,
                    source_status=ResearchFeatureStatus.READY,
                    event_versions=(),
                    reviews=(),
                )
            )
            if replay.status == ResearchFeatureStatus.READY:
                facts.extend(replay.facts)
                continue
            if (
                replay.status == ResearchFeatureStatus.MISSING
                and replay.reasons
                in {
                    ("risk_document_facts_missing",),
                    ("risk_document_text_missing",),
                }
            ):
                continue
            else:
                facts_valid = False
                break
        if not facts_valid:
            items.append(LeaderRiskCandidateProjectionBatchItem(
                index=index,
                symbol=plan_item.symbol,
                input_symbol=plan_item.symbol,
                status=ResearchFeatureStatus.MISSING,
                reasons=("risk_official_deterministic_document_facts_missing",),
            ))
            continue
        projection = _build_projection(
            symbol=plan_item.symbol,
            issuer_identity=issuer_identity,
            plan=plan,
            delivery=delivery,
            evidence_kind=LeaderOfficialDeterministicRiskEvidenceKind.OFFICIAL_DOCUMENT_EVIDENCE,
            documents=documents,
            contents=contents,
            facts=facts,
        )
        items.append(LeaderRiskCandidateProjectionBatchItem(
            index=index,
            symbol=plan_item.symbol,
            input_symbol=plan_item.symbol,
            status=ResearchFeatureStatus.READY,
            projection=projection,
        ))
        all_published.extend(_aware(document.published_at) for document in documents)
        all_fetched.extend(_aware(content.fetched_at) for content in contents)

    ready_count = sum(item.included for item in items)
    if ready_count == len(items):
        batch_status = LeaderRiskCandidateProjectionBatchStatus.READY
        status = LeaderOfficialDeterministicRiskStatus.READY
        reasons = ()
        batch_reasons = ()
    elif ready_count:
        batch_status = LeaderRiskCandidateProjectionBatchStatus.PARTIAL
        status = LeaderOfficialDeterministicRiskStatus.PARTIAL
        reasons = ("risk_official_deterministic_partial",)
        batch_reasons = ("risk_candidate_projection_batch_partial",)
    else:
        statuses = {item.status for item in items}
        if ResearchFeatureStatus.SOURCE_UNVERIFIED in statuses:
            batch_status = LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED
            status = LeaderOfficialDeterministicRiskStatus.SOURCE_UNVERIFIED
        else:
            batch_status = LeaderRiskCandidateProjectionBatchStatus.MISSING
            status = LeaderOfficialDeterministicRiskStatus.MISSING
        reasons = _dedupe(reason for item in items for reason in item.reasons)
        batch_reasons = ("risk_candidate_projection_batch_no_ready_items",)
    projection_batch = LeaderRiskCandidateProjectionBatchResult(
        status=batch_status,
        as_of=plan.as_of,
        input_count=len(items),
        items=tuple(items),
        reasons=batch_reasons,
    )
    normalized_fetched = tuple(value for value in all_fetched if value is not None)
    normalized_published = tuple(value for value in all_published if value is not None)
    fetched_at = max(normalized_fetched) if normalized_fetched else None
    source_time = (
        max(normalized_published)
        if normalized_published
        else plan.as_of
    )
    return _result(
        status=status,
        plan=plan,
        delivery=delivery,
        projection_batch=projection_batch,
        reasons=reasons,
        source_time=source_time,
        fetched_at=fetched_at,
    )


def collect_leader_official_deterministic_risk_source(
    context: Any,
    frozen: Any,
) -> Any:
    """重放官方风险冻结输入，完整候选全集才作为生产来源交付。"""

    from radar.leader_research_runtime_provider import (
        is_leader_research_runtime_source_context_valid,
    )
    from radar.leader_formal_research_production_provider import (
        LeaderFormalResearchProductionCollectedSource,
        LeaderFormalResearchProductionSourceStatus,
    )

    def collected(status, *, payload=None, source_time=None, fetched_at=None, reasons=()):
        return LeaderFormalResearchProductionCollectedSource(
            component_name="risk",
            source_contract_id=LEADER_OFFICIAL_DETERMINISTIC_RISK_SOURCE_CONTRACT_ID,
            status=status,
            source_time=source_time,
            fetched_at=fetched_at,
            symbols=(
                tuple(item.symbol for item in context.candidate_plan.items)
                if is_leader_research_runtime_source_context_valid(context)
                else ()
            ),
            payload=payload,
            reasons=tuple(reasons),
        )

    if not is_leader_research_runtime_source_context_valid(context):
        return collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=("risk_official_deterministic_context_unverified",),
        )
    if (
        type(frozen) is not LeaderOfficialDeterministicRiskFrozenBatch
        or frozen.contract_id != LEADER_OFFICIAL_DETERMINISTIC_RISK_FROZEN_CONTRACT_ID
    ):
        return collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=("risk_official_deterministic_frozen_unverified",),
        )
    result = build_leader_official_deterministic_risk_batch(
        candidate_plan=context.candidate_plan,
        delivery=frozen.delivery,
        document_contents=frozen.document_contents,
    )
    if result.status != LeaderOfficialDeterministicRiskStatus.READY:
        status = (
            LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED
            if result.status == LeaderOfficialDeterministicRiskStatus.SOURCE_FAILED
            else LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
        return collected(status, reasons=result.reasons)
    return collected(
        LeaderFormalResearchProductionSourceStatus.COMPLETED,
        payload=result,
        source_time=result.source_time,
        fetched_at=result.fetched_at,
    )


def build_leader_official_deterministic_risk_loader(
    frozen: LeaderOfficialDeterministicRiskFrozenBatch,
):
    if type(frozen) is not LeaderOfficialDeterministicRiskFrozenBatch:
        raise ValueError("risk_official_deterministic_frozen_unverified")

    def load(context):
        return collect_leader_official_deterministic_risk_source(context, frozen)

    return load
