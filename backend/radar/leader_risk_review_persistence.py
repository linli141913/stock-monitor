"""把通过完整性门禁的真实D2交付结果保存为审核批次。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.leader_risk_invalidation_features import ALL_RISK_CATEGORIES
from radar.leader_risk_lifecycle_batch import (
    CANONICAL_DISCOVERY_SEARCH_KEYS,
    LeaderRiskLifecycleBatchEntry,
    LeaderRiskLifecycleBatchResult,
)
from radar.leader_risk_lifecycle_delivery import (
    LEADER_RISK_LIFECYCLE_DELIVERY_CONTRACT_ID,
    LeaderRiskLifecycleDeliveryInput,
    LeaderRiskLifecycleDeliveryResult,
    LeaderRiskLifecycleDeliveryStatus,
    LeaderRiskLifecycleRealPocStatus,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_risk_candidate_discovery import (
    LEADER_RISK_OFFICIAL_CANDIDATE_DISCOVERY_CONTRACT_ID,
    LeaderRiskOfficialCandidateDiscoveryResult,
    LeaderRiskOfficialCandidateDiscoveryStatus,
    LeaderRiskOfficialCandidateDocument,
)
from radar.sources.leader_risk_official import (
    CNINFO_ISSUER_SCOPE_CONTRACT_ID,
    CNINFO_SOURCE_CONTRACT_ID,
    MAXIMUM_CANDIDATE_SCOPE_COUNT,
    CninfoRiskIssuerScope,
    OfficialRiskDocumentMetadata,
    OfficialRiskSourceStatus,
)


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class LeaderRiskReviewPersistenceStatus(str, Enum):
    PERSISTED = "persisted"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderRiskReviewPersistenceResult:
    status: LeaderRiskReviewPersistenceStatus
    review_batch_id: Optional[str]
    candidate_plan_id: Optional[str]
    document_count: int = 0
    document_link_count: int = 0
    reasons: Tuple[str, ...] = ()


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _blocked(
    reason: str,
    *,
    candidate_plan_id: Optional[str] = None,
) -> LeaderRiskReviewPersistenceResult:
    return LeaderRiskReviewPersistenceResult(
        status=LeaderRiskReviewPersistenceStatus.BLOCKED,
        review_batch_id=None,
        candidate_plan_id=candidate_plan_id,
        reasons=(reason,),
    )


def _document_identity(document: OfficialRiskDocumentMetadata) -> Mapping[str, Any]:
    return {
        "sourceContractId": document.source_contract_id,
        "documentId": document.document_id,
        "symbol": document.symbol,
        "issuerIdentity": document.issuer_identity,
        "issuerName": document.issuer_name,
        "title": document.title,
        "publishedAt": document.published_at.astimezone(UTC).isoformat(),
        "sourceName": document.source_name,
        "sourceUrl": document.source_url,
        "candidateCategory": document.candidate_category.value,
        "rawColumnIds": list(document.raw_column_ids),
        "rawAnnouncementTypes": list(document.raw_announcement_types),
        "rawPageColumn": document.raw_page_column,
        "associationReported": document.association_reported,
        "formalUsable": document.formal_usable,
    }


def _review_batch_id(
    *,
    plan: LeaderRuntimeCandidatePlan,
    collected_at: datetime,
    window_from: date,
    window_until: date,
    documents: Sequence[OfficialRiskDocumentMetadata],
) -> str:
    payload = {
        "contractId": "radar-leader-risk-review-persistence-v1",
        "candidatePlanId": plan.candidate_set_id,
        "radarRunId": plan.radar_run_id,
        "quoteBatchId": plan.quote_batch_id,
        "collectedAt": collected_at.isoformat(),
        "windowFrom": window_from.isoformat(),
        "windowUntil": window_until.isoformat(),
        "documents": sorted(
            (_document_identity(document) for document in documents),
            key=lambda item: (
                item["documentId"], item["candidateCategory"]
            ),
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"radar-leader-risk-review-batch-v1:{digest}"


def _validated_documents(
    *,
    plan: LeaderRuntimeCandidatePlan,
    scopes: Tuple[CninfoRiskIssuerScope, ...],
    discovery: LeaderRiskOfficialCandidateDiscoveryResult,
) -> Optional[Tuple[OfficialRiskDocumentMetadata, ...]]:
    if (
        tuple(item.symbol for item in discovery.items)
        != tuple(item.symbol for item in plan.items)
        or len(discovery.items) != len(scopes)
    ):
        return None
    expanded = []
    for index, item in enumerate(discovery.items):
        scope = scopes[index]
        if (
            item.symbol != scope.symbol
            or item.status
            not in {
                LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
                LeaderRiskOfficialCandidateDiscoveryStatus.MISSING,
            }
            or (
                item.documents
                and item.issuer_identity != scope.issuer_identity
            )
        ):
            return None
        for wrapped in item.documents:
            if (
                not isinstance(wrapped, LeaderRiskOfficialCandidateDocument)
                or wrapped.document.symbol != item.symbol
                or wrapped.document.issuer_identity != scope.issuer_identity
                or wrapped.document.source_contract_id
                != CNINFO_SOURCE_CONTRACT_ID
                or wrapped.document.formal_usable is not False
                or not wrapped.candidate_categories
                or not wrapped.source_statuses
                or len(wrapped.candidate_categories)
                != len(set(wrapped.candidate_categories))
                or any(
                    status != OfficialRiskSourceStatus.PARTIAL
                    for status in wrapped.source_statuses
                )
            ):
                return None
            expected_keys = {
                CANONICAL_DISCOVERY_SEARCH_KEYS[category]
                for category in wrapped.candidate_categories
                if category in ALL_RISK_CATEGORIES
            }
            if (
                len(expected_keys) != len(wrapped.candidate_categories)
                or set(wrapped.matched_query_keys) != expected_keys
            ):
                return None
            expanded.extend(
                replace(
                    wrapped.document,
                    candidate_category=category,
                )
                for category in wrapped.candidate_categories
            )
    link_keys = tuple(
        (document.document_id, document.candidate_category)
        for document in expanded
    )
    if len(link_keys) != len(set(link_keys)):
        return None
    return tuple(sorted(
        expanded,
        key=lambda item: (item.document_id, item.candidate_category.value),
    ))


def persist_verified_leader_risk_review_batch(
    input_value: Any,
    delivery: Any,
    repository: Any,
) -> LeaderRiskReviewPersistenceResult:
    """只持久化来源、身份、分页和时间窗均可复验的真实D2批次。"""

    if (
        not isinstance(input_value, LeaderRiskLifecycleDeliveryInput)
        or not isinstance(delivery, LeaderRiskLifecycleDeliveryResult)
        or not isinstance(repository, LeaderRiskReviewRepository)
    ):
        return _blocked("risk_review_persistence_contract_unverified")
    plan = input_value.candidate_plan
    candidate_plan_id = (
        plan.candidate_set_id
        if isinstance(plan, LeaderRuntimeCandidatePlan)
        else None
    )
    if (
        not isinstance(plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(plan)
        or input_value.confirm_live_poc is not True
        or delivery.contract_id
        != LEADER_RISK_LIFECYCLE_DELIVERY_CONTRACT_ID
        or delivery.real_poc_status
        != LeaderRiskLifecycleRealPocStatus.PARTIAL
        or delivery.status
        not in {
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
            LeaderRiskLifecycleDeliveryStatus.MISSING,
            LeaderRiskLifecycleDeliveryStatus.STALE,
        }
        or any((
            delivery.formal_score_ready,
            delivery.formal_gate_ready,
            delivery.formal_usable,
            delivery.state_transition_allowed,
        ))
    ):
        return _blocked(
            "risk_review_persistence_delivery_unverified",
            candidate_plan_id=candidate_plan_id,
        )

    collected_at = _aware_utc(input_value.collected_at)
    plan_as_of = _aware_utc(plan.as_of)
    delivery_as_of = _aware_utc(delivery.as_of)
    entries = input_value.entries
    scopes = input_value.candidate_scopes
    expected_symbols = tuple(item.symbol for item in plan.items)
    if (
        collected_at is None
        or plan_as_of is None
        or delivery_as_of != collected_at
        or delivery.candidate_plan_id != plan.candidate_set_id
        or delivery.radar_run_id != plan.radar_run_id
        or delivery.quote_batch_id != plan.quote_batch_id
        or not isinstance(input_value.window_from, date)
        or isinstance(input_value.window_from, datetime)
        or not isinstance(input_value.window_until, date)
        or isinstance(input_value.window_until, datetime)
        or input_value.window_from > input_value.window_until
        or input_value.window_until
        != plan_as_of.astimezone(SHANGHAI_TZ).date()
        or not isinstance(entries, tuple)
        or tuple(getattr(item, "symbol", None) for item in entries)
        != expected_symbols
        or any(
            not isinstance(item, LeaderRiskLifecycleBatchEntry)
            for item in entries
        )
        or not isinstance(scopes, tuple)
        or len(scopes) != len(expected_symbols)
        or any(
            not isinstance(scope, CninfoRiskIssuerScope)
            or scope.symbol != expected_symbols[index]
            or scope.source_contract_id
            != CNINFO_ISSUER_SCOPE_CONTRACT_ID
            for index, scope in enumerate(scopes)
        )
    ):
        return _blocked(
            "risk_review_persistence_identity_mismatch",
            candidate_plan_id=candidate_plan_id,
        )

    lifecycle = delivery.lifecycle_result
    discovery = (
        lifecycle.discovery_result
        if isinstance(lifecycle, LeaderRiskLifecycleBatchResult)
        else None
    )
    expected_shard_count = (
        len(expected_symbols) + MAXIMUM_CANDIDATE_SCOPE_COUNT - 1
    ) // MAXIMUM_CANDIDATE_SCOPE_COUNT
    if (
        not isinstance(lifecycle, LeaderRiskLifecycleBatchResult)
        or lifecycle.candidate_plan_id != plan.candidate_set_id
        or lifecycle.radar_run_id != plan.radar_run_id
        or lifecycle.quote_batch_id != plan.quote_batch_id
        or _aware_utc(lifecycle.as_of) != plan_as_of
        or lifecycle.candidate_count != len(expected_symbols)
        or tuple(item.symbol for item in lifecycle.items)
        != expected_symbols
        or not isinstance(
            discovery,
            LeaderRiskOfficialCandidateDiscoveryResult,
        )
        or discovery.contract_id
        != LEADER_RISK_OFFICIAL_CANDIDATE_DISCOVERY_CONTRACT_ID
        or discovery.status
        not in {
            LeaderRiskOfficialCandidateDiscoveryStatus.PARTIAL,
            LeaderRiskOfficialCandidateDiscoveryStatus.MISSING,
        }
        or discovery.candidate_plan_id != plan.candidate_set_id
        or discovery.candidate_count != len(expected_symbols)
        or tuple(discovery.queried_categories)
        != tuple(ALL_RISK_CATEGORIES)
        or discovery.missing_query_categories
        or not lifecycle.query_categories_complete
        or not lifecycle.query_pages_complete
        or not lifecycle.query_window_continuous
        or delivery.category_count != len(ALL_RISK_CATEGORIES)
        or delivery.candidate_scope_count != len(expected_symbols)
        or delivery.shard_count != expected_shard_count
        or discovery.query_count != delivery.fetched_page_count
        or delivery.fetched_page_count
        < len(ALL_RISK_CATEGORIES) * expected_shard_count
        or delivery.request_count < delivery.fetched_page_count
    ):
        return _blocked(
            "risk_review_persistence_completeness_unverified",
            candidate_plan_id=candidate_plan_id,
        )

    documents = _validated_documents(
        plan=plan,
        scopes=scopes,
        discovery=discovery,
    )
    if documents is None:
        return _blocked(
            "risk_review_persistence_documents_unverified",
            candidate_plan_id=candidate_plan_id,
        )
    unique_document_count = len({
        document.document_id for document in documents
    })
    review_batch_id = _review_batch_id(
        plan=plan,
        collected_at=collected_at,
        window_from=input_value.window_from,
        window_until=input_value.window_until,
        documents=documents,
    )
    inserted = repository.save_review_batch(
        {
            "reviewBatchId": review_batch_id,
            "candidatePlanId": plan.candidate_set_id,
            "radarRunId": plan.radar_run_id,
            "asOf": collected_at,
            "windowFrom": input_value.window_from,
            "windowUntil": input_value.window_until,
            "candidateCount": len(expected_symbols),
            "shardCount": expected_shard_count,
            "categoryCount": len(ALL_RISK_CATEGORIES),
            "documentCount": unique_document_count,
            "queryCategoriesComplete": True,
            "queryPagesComplete": True,
            "queryWindowContinuous": True,
            "sourceContractId": CNINFO_SOURCE_CONTRACT_ID,
        },
        documents,
    )
    return LeaderRiskReviewPersistenceResult(
        status=(
            LeaderRiskReviewPersistenceStatus.PERSISTED
            if inserted
            else LeaderRiskReviewPersistenceStatus.UNCHANGED
        ),
        review_batch_id=review_batch_id,
        candidate_plan_id=plan.candidate_set_id,
        document_count=unique_document_count,
        document_link_count=len(documents),
    )
