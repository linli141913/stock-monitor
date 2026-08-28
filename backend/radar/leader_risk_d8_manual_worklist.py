"""D2官方发现到真实人工D8处理的离线待办清单。

本模块只消费包含完整内存D2发现队列的交付结果，不接受脱敏汇总反推。
它不预填人工结论、审核人、审核时间或版本，也不构建D8证据包。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleBatchResult,
)
from radar.leader_risk_lifecycle_delivery import (
    LeaderRiskLifecycleDeliveryResult,
    LeaderRiskLifecycleDeliveryStatus,
    LeaderRiskLifecycleRealPocStatus,
)
from radar.sources.leader_risk_candidate_discovery import (
    LeaderRiskOfficialCandidateDiscoveryResult,
)


LEADER_RISK_D8_MANUAL_WORKLIST_CONTRACT_ID = (
    "radar-leader-risk-d8-manual-worklist-v2"
)
LEADER_RISK_D8_MANUAL_SOURCE_PACKET_CONTRACT_ID = (
    "radar-leader-risk-d8-manual-source-packet-v2"
)
LEADER_RISK_D8_MANUAL_REVIEW_PACKET_CONTRACT_ID = (
    "radar-leader-risk-d8-manual-review-packet-v2"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class LeaderRiskD8ManualWorklistStatus(str, Enum):
    PENDING_HUMAN_REVIEW = "pending_human_review"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderRiskD8ManualWorklistDocument:
    document_id: str
    issuer_name: str
    title: str
    source_name: str
    source_url: str
    published_at: datetime
    raw_column_ids: Tuple[str, ...]
    raw_announcement_types: Tuple[str, ...]
    raw_page_column: Optional[str]
    association_reported: bool
    candidate_categories: Tuple[str, ...]
    matched_query_keys: Tuple[str, ...]
    source_statuses: Tuple[str, ...]

    def to_packet(self) -> Mapping[str, object]:
        return {
            "documentId": self.document_id,
            "issuerName": self.issuer_name,
            "title": self.title,
            "sourceName": self.source_name,
            "sourceUrl": self.source_url,
            "publishedAt": self.published_at.isoformat(),
            "rawColumnIds": list(self.raw_column_ids),
            "rawAnnouncementTypes": list(self.raw_announcement_types),
            "rawPageColumn": self.raw_page_column,
            "associationReported": self.association_reported,
            "formalUsable": False,
            "candidateCategories": list(self.candidate_categories),
            "matchedQueryKeys": list(self.matched_query_keys),
            "sourceStatuses": list(self.source_statuses),
        }


@dataclass(frozen=True, repr=False)
class LeaderRiskD8ManualWorklistItem:
    index: int
    symbol: str
    issuer_identity: Optional[str]
    source_status: str
    source_reasons: Tuple[str, ...]
    documents: Tuple[LeaderRiskD8ManualWorklistDocument, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    review: None = field(default=None, repr=False)

    def to_source_packet(self) -> Mapping[str, object]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "sourceStatus": self.source_status,
            "sourceReasons": list(self.source_reasons),
            "documentCount": len(self.documents),
            "documents": [item.to_packet() for item in self.documents],
        }

    def to_review_packet(self) -> Mapping[str, object]:
        return {
            **self.to_source_packet(),
            "review": None,
            "d8SubmissionReady": False,
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


@dataclass(frozen=True, repr=False)
class LeaderRiskD8ManualWorklistResult:
    status: LeaderRiskD8ManualWorklistStatus
    candidate_plan_id: Optional[str]
    radar_run_id: Optional[str]
    quote_batch_id: Optional[str]
    as_of: Optional[datetime]
    d2_collected_at: Optional[datetime]
    d2_window_from: Optional[date]
    d2_window_until: Optional[date]
    created_at: Optional[datetime]
    candidate_source_packet_sha256: Optional[str]
    d2_delivery_status: Optional[str]
    d2_real_poc_status: Optional[str]
    d2_request_count: int = 0
    d2_fetched_page_count: int = 0
    d2_category_count: int = 0
    d2_shard_count: int = 0
    d2_query_count: int = 0
    d2_ignored_document_count: int = 0
    items: Tuple[LeaderRiskD8ManualWorklistItem, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_RISK_D8_MANUAL_WORKLIST_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def candidate_count(self) -> int:
        return len(self.items)

    def _d2_audit(self) -> Mapping[str, object]:
        return {
            "deliveryStatus": self.d2_delivery_status,
            "realPocStatus": self.d2_real_poc_status,
            "requestCount": self.d2_request_count,
            "fetchedPageCount": self.d2_fetched_page_count,
            "categoryCount": self.d2_category_count,
            "shardCount": self.d2_shard_count,
            "queryCount": self.d2_query_count,
            "ignoredDocumentCount": self.d2_ignored_document_count,
            "queryCategoriesComplete": self.status is (
                LeaderRiskD8ManualWorklistStatus.PENDING_HUMAN_REVIEW
            ),
            "queryPagesComplete": self.status is (
                LeaderRiskD8ManualWorklistStatus.PENDING_HUMAN_REVIEW
            ),
            "queryWindowContinuous": self.status is (
                LeaderRiskD8ManualWorklistStatus.PENDING_HUMAN_REVIEW
            ),
        }

    def _source_payload(self) -> Mapping[str, object]:
        return {
            "contractId": LEADER_RISK_D8_MANUAL_SOURCE_PACKET_CONTRACT_ID,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "radarRunId": self.radar_run_id,
            "quoteBatchId": self.quote_batch_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "d2CollectedAt": (
                self.d2_collected_at.isoformat()
                if self.d2_collected_at else None
            ),
            "d2WindowFrom": (
                self.d2_window_from.isoformat()
                if self.d2_window_from else None
            ),
            "d2WindowUntil": (
                self.d2_window_until.isoformat()
                if self.d2_window_until else None
            ),
            "candidateSourcePacketSha256": (
                self.candidate_source_packet_sha256
            ),
            "d2Audit": self._d2_audit(),
            "createdAt": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "candidateCount": self.candidate_count,
            "reasons": list(self.reasons),
            "items": [item.to_source_packet() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }

    def to_source_packet(self) -> Mapping[str, object]:
        payload = self._source_payload()
        return {**payload, "packetSha256": _digest(payload)}

    def to_review_packet(self) -> Mapping[str, object]:
        source = self.to_source_packet()
        return {
            "contractId": LEADER_RISK_D8_MANUAL_REVIEW_PACKET_CONTRACT_ID,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "radarRunId": self.radar_run_id,
            "quoteBatchId": self.quote_batch_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "d2CollectedAt": (
                self.d2_collected_at.isoformat()
                if self.d2_collected_at else None
            ),
            "d2WindowFrom": (
                self.d2_window_from.isoformat()
                if self.d2_window_from else None
            ),
            "d2WindowUntil": (
                self.d2_window_until.isoformat()
                if self.d2_window_until else None
            ),
            "candidateSourcePacketSha256": (
                self.candidate_source_packet_sha256
            ),
            "d2Audit": self._d2_audit(),
            "createdAt": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "sourcePacketSha256": source["packetSha256"],
            "candidateCount": self.candidate_count,
            "d8VersionCount": 0,
            "d8SubmissionReady": False,
            "reasons": list(self.reasons),
            "items": [item.to_review_packet() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _blocked(reason: str) -> LeaderRiskD8ManualWorklistResult:
    return LeaderRiskD8ManualWorklistResult(
        status=LeaderRiskD8ManualWorklistStatus.BLOCKED,
        candidate_plan_id=None,
        radar_run_id=None,
        quote_batch_id=None,
        as_of=None,
        d2_collected_at=None,
        d2_window_from=None,
        d2_window_until=None,
        created_at=None,
        candidate_source_packet_sha256=None,
        d2_delivery_status=None,
        d2_real_poc_status=None,
        reasons=(reason,),
    )


def _delivery_valid(value: Any, created_at: Any) -> bool:
    lifecycle = getattr(value, "lifecycle_result", None)
    discovery = getattr(lifecycle, "discovery_result", None)
    lifecycle_items = getattr(lifecycle, "items", ())
    projection = getattr(lifecycle, "projection_batch", None)
    return bool(
        type(value) is LeaderRiskLifecycleDeliveryResult
        and value.status in {
            LeaderRiskLifecycleDeliveryStatus.MISSING,
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        }
        and value.real_poc_status
        is LeaderRiskLifecycleRealPocStatus.PARTIAL
        and type(lifecycle) is LeaderRiskLifecycleBatchResult
        and type(discovery) is LeaderRiskOfficialCandidateDiscoveryResult
        and lifecycle.candidate_plan_id == value.candidate_plan_id
        and lifecycle.radar_run_id == value.radar_run_id
        and lifecycle.quote_batch_id == value.quote_batch_id
        and _aware(lifecycle.as_of)
        and _aware(value.as_of)
        and isinstance(value.window_from, date)
        and not isinstance(value.window_from, datetime)
        and isinstance(value.window_until, date)
        and not isinstance(value.window_until, datetime)
        and value.window_from <= value.window_until
        and value.window_until
        == lifecycle.as_of.astimezone(_SHANGHAI_TZ).date()
        and value.as_of >= lifecycle.as_of
        and lifecycle.query_categories_complete
        and lifecycle.query_pages_complete
        and lifecycle.query_window_continuous
        and value.category_count == 7
        and isinstance(value.request_count, int)
        and not isinstance(value.request_count, bool)
        and isinstance(value.fetched_page_count, int)
        and not isinstance(value.fetched_page_count, bool)
        and value.request_count >= value.fetched_page_count > 0
        and isinstance(value.shard_count, int)
        and not isinstance(value.shard_count, bool)
        and value.shard_count > 0
        and value.candidate_scope_count == lifecycle.candidate_count
        and lifecycle.candidate_count == discovery.candidate_count
        and discovery.query_count == value.fetched_page_count
        and isinstance(discovery.ignored_document_count, int)
        and not isinstance(discovery.ignored_document_count, bool)
        and discovery.ignored_document_count >= 0
        and len(discovery.items) == lifecycle.candidate_count
        and len(lifecycle_items) == lifecycle.candidate_count
        and tuple(item.index for item in discovery.items)
        == tuple(range(lifecycle.candidate_count))
        and tuple(
            (item.index, item.symbol)
            for item in lifecycle_items
        ) == tuple(
            (item.index, item.symbol)
            for item in discovery.items
        )
        and all(
            source_item.issuer_identity
            in {None, lifecycle_item.issuer_identity}
            and (
                not source_item.documents
                or source_item.issuer_identity
                == lifecycle_item.issuer_identity
            )
            for lifecycle_item, source_item
            in zip(lifecycle_items, discovery.items)
        )
        and all(
            item.version_count == 0
            and item.bundle_ids == ()
            and not item.formal_usable
            and not item.state_transition_allowed
            for item in lifecycle_items
        )
        and (
            projection is None
            or (
                projection.input_count == lifecycle.candidate_count
                and len(projection.items) == lifecycle.candidate_count
                and tuple(
                    (item.index, item.symbol)
                    for item in projection.items
                ) == tuple(
                    (item.index, item.symbol)
                    for item in lifecycle_items
                )
                and all(
                    item.projection is None
                    and not item.risk_filter_passed
                    and not item.formal_gate_ready
                    and not item.formal_usable
                    and not item.applied_to_d3
                    and not item.applied_to_d1
                    for item in projection.items
                )
                and projection.ready_count == 0
                and not projection.risk_filter_passed
                and not projection.formal_gate_ready
                and not projection.formal_usable
                and not projection.applied_to_d3
                and not projection.applied_to_d1
            )
        )
        and _aware(created_at)
        and created_at >= value.as_of
    )


def build_leader_risk_d8_manual_worklist(
    delivery: Any,
    *,
    created_at: datetime,
    candidate_source_packet_sha256: str,
) -> LeaderRiskD8ManualWorklistResult:
    """从完整D2内存发现队列生成全候选人工待办。"""

    if (
        not isinstance(candidate_source_packet_sha256, str)
        or _SHA256_PATTERN.fullmatch(
            candidate_source_packet_sha256
        ) is None
        or not _delivery_valid(delivery, created_at)
    ):
        return _blocked("risk_d8_manual_worklist_d2_source_unverified")
    discovery = delivery.lifecycle_result.discovery_result
    items = []
    for source_item, lifecycle_item in zip(
        discovery.items,
        delivery.lifecycle_result.items,
    ):
        documents = tuple(
            LeaderRiskD8ManualWorklistDocument(
                document_id=item.document.document_id,
                issuer_name=item.document.issuer_name,
                title=item.document.title,
                source_name=item.document.source_name,
                source_url=item.document.source_url,
                published_at=item.document.published_at,
                raw_column_ids=item.document.raw_column_ids,
                raw_announcement_types=(
                    item.document.raw_announcement_types
                ),
                raw_page_column=item.document.raw_page_column,
                association_reported=(
                    item.document.association_reported
                ),
                candidate_categories=tuple(
                    value.value for value in item.candidate_categories
                ),
                matched_query_keys=item.matched_query_keys,
                source_statuses=tuple(
                    value.value for value in item.source_statuses
                ),
            )
            for item in source_item.documents
        )
        items.append(LeaderRiskD8ManualWorklistItem(
            index=source_item.index,
            symbol=source_item.symbol,
            issuer_identity=lifecycle_item.issuer_identity,
            source_status=source_item.status.value,
            source_reasons=source_item.reasons,
            documents=documents,
        ))
    return LeaderRiskD8ManualWorklistResult(
        status=LeaderRiskD8ManualWorklistStatus.PENDING_HUMAN_REVIEW,
        candidate_plan_id=delivery.candidate_plan_id,
        radar_run_id=delivery.radar_run_id,
        quote_batch_id=delivery.quote_batch_id,
        as_of=delivery.lifecycle_result.as_of,
        d2_collected_at=delivery.as_of,
        d2_window_from=delivery.window_from,
        d2_window_until=delivery.window_until,
        created_at=created_at,
        candidate_source_packet_sha256=(
            candidate_source_packet_sha256
        ),
        d2_delivery_status=delivery.status.value,
        d2_real_poc_status=delivery.real_poc_status.value,
        d2_request_count=delivery.request_count,
        d2_fetched_page_count=delivery.fetched_page_count,
        d2_category_count=delivery.category_count,
        d2_shard_count=delivery.shard_count,
        d2_query_count=discovery.query_count,
        d2_ignored_document_count=discovery.ignored_document_count,
        items=tuple(items),
    )
