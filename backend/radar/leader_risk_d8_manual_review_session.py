"""D8真实人工审核的离线临时库处理会话。

本模块只从强校验的D2人工待办包选取最多三份官方公告，复用
现有正文交付、事实提取和D8表单编排。它不生成人工结论、不保存
审核版本，也不连接默认数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_content_delivery import (
    LeaderRiskContentDeliveryStatus,
    LeaderRiskContentSelection,
    deliver_leader_risk_document_contents,
)
from radar.leader_risk_d8_manual_worklist import (
    LEADER_RISK_D8_MANUAL_REVIEW_PACKET_CONTRACT_ID,
    LEADER_RISK_D8_MANUAL_SOURCE_PACKET_CONTRACT_ID,
)
from radar.leader_risk_invalidation_features import (
    ALL_RISK_CATEGORIES,
    RiskCategory,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_risk_review_service import build_review_form_data
from radar.leader_risk_lifecycle_batch import CANONICAL_DISCOVERY_SEARCH_KEYS
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    fetch_official_risk_document_content,
)
from radar.sources.leader_risk_official import (
    CNINFO_SOURCE_CONTRACT_ID,
    OfficialRiskDocumentMetadata,
)


UTC = timezone.utc
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYMBOL_PATTERN = re.compile(r"^(?:00|30|60|68)\d{4}$")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
LEADER_RISK_D8_MANUAL_REVIEW_SESSION_CONTRACT_ID = (
    "radar-leader-risk-d8-manual-review-session-v1"
)


class LeaderRiskD8ManualReviewSessionStatus(str, Enum):
    PENDING_HUMAN_REVIEW = "pending_human_review"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderRiskD8ManualReviewSelection:
    document_id: str
    candidate_category: str


@dataclass(frozen=True, repr=False)
class LoadedLeaderRiskD8ManualReviewPacket:
    candidate_plan_id: str
    radar_run_id: str
    quote_batch_id: str
    as_of: datetime
    d2_collected_at: datetime
    d2_window_from: date
    d2_window_until: date
    created_at: datetime
    source_packet_sha256: str
    candidate_count: int
    shard_count: int
    documents: Tuple[OfficialRiskDocumentMetadata, ...] = field(
        repr=False
    )

    @property
    def review_batch_id(self) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "candidatePlanId": self.candidate_plan_id,
                    "sourcePacketSha256": self.source_packet_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"radar-leader-risk-d8-offline-review-batch-v1:{digest}"


@dataclass(frozen=True, repr=False)
class LeaderRiskD8ManualReviewSessionPage:
    page_number: int
    text: str = field(repr=False)

    def to_packet(self) -> Mapping[str, object]:
        return {"pageNumber": self.page_number, "text": self.text}


@dataclass(frozen=True, repr=False)
class LeaderRiskD8ManualReviewSessionItem:
    document_id: str
    candidate_category: str
    symbol: str
    issuer_identity: str
    issuer_name: str
    title: str
    source_url: str
    published_at: datetime
    content_sha256: str
    candidate_id: str
    required_fact_kinds: Tuple[str, ...]
    pages: Tuple[LeaderRiskD8ManualReviewSessionPage, ...] = field(
        repr=False
    )
    review: None = field(default=None, repr=False)

    def to_packet(self) -> Mapping[str, object]:
        return {
            "documentId": self.document_id,
            "candidateCategory": self.candidate_category,
            "symbol": self.symbol,
            "issuerIdentity": self.issuer_identity,
            "issuerName": self.issuer_name,
            "title": self.title,
            "sourceUrl": self.source_url,
            "publishedAt": self.published_at.isoformat(),
            "contentSha256": self.content_sha256,
            "candidateId": self.candidate_id,
            "requiredFactKinds": list(self.required_fact_kinds),
            "pageCount": len(self.pages),
            "pages": [page.to_packet() for page in self.pages],
            "submissionRequirements": {
                "reviewerKeyRequired": True,
                "factSupplementsRequired": True,
                "targetEventRequired": True,
                "relationDecisionRequired": True,
                "confirmOfficialEvidenceRequired": True,
            },
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


@dataclass(frozen=True, repr=False)
class LeaderRiskD8ManualReviewSessionResult:
    status: LeaderRiskD8ManualReviewSessionStatus
    review_batch_id: Optional[str] = None
    candidate_plan_id: Optional[str] = None
    radar_run_id: Optional[str] = None
    as_of: Optional[datetime] = None
    prepared_at: Optional[datetime] = None
    source_packet_sha256: Optional[str] = None
    items: Tuple[LeaderRiskD8ManualReviewSessionItem, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_RISK_D8_MANUAL_REVIEW_SESSION_CONTRACT_ID
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def _payload(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "reviewBatchId": self.review_batch_id,
            "candidatePlanId": self.candidate_plan_id,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "preparedAt": (
                self.prepared_at.isoformat() if self.prepared_at else None
            ),
            "sourcePacketSha256": self.source_packet_sha256,
            "selectedCount": len(self.items),
            "d8VersionCount": 0,
            "d8SubmissionReady": False,
            "reasons": list(self.reasons),
            "items": [item.to_packet() for item in self.items],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }

    def to_packet(self) -> Mapping[str, object]:
        payload = self._payload()
        return {**payload, "packetSha256": _digest(payload)}


def _blocked(reason: str) -> LeaderRiskD8ManualReviewSessionResult:
    return LeaderRiskD8ManualReviewSessionResult(
        status=LeaderRiskD8ManualReviewSessionStatus.BLOCKED,
        reasons=(reason,),
    )


def _datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and SAFE_ID_PATTERN.fullmatch(value) is not None


def _false_gate(value: Any) -> bool:
    return value == {
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def _official_url(document_id: str, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "static.cninfo.com.cn"
        and parsed.netloc == "static.cninfo.com.cn"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.lower().endswith(".pdf")
        and parsed.path.rsplit("/", 1)[-1][:-4]
        == document_id.removeprefix("cninfo:")
    )


def _document(
    value: Any,
    *,
    symbol: str,
    issuer_identity: str,
    window_from: date,
    window_until: date,
) -> Optional[Tuple[OfficialRiskDocumentMetadata, ...]]:
    expected_keys = {
        "documentId", "issuerName", "title", "sourceName", "sourceUrl",
        "publishedAt", "rawColumnIds", "rawAnnouncementTypes",
        "rawPageColumn", "associationReported", "formalUsable",
        "candidateCategories", "matchedQueryKeys", "sourceStatuses",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        return None
    document_id = value.get("documentId")
    published_at = _datetime(value.get("publishedAt"))
    categories = value.get("candidateCategories")
    matched_keys = value.get("matchedQueryKeys")
    source_statuses = value.get("sourceStatuses")
    raw_column_ids = value.get("rawColumnIds")
    raw_announcement_types = value.get("rawAnnouncementTypes")
    raw_page_column = value.get("rawPageColumn")
    if (
        not _safe_id(document_id)
        or not str(document_id).startswith("cninfo:")
        or not str(document_id).removeprefix("cninfo:").isdigit()
        or not _official_url(str(document_id), value.get("sourceUrl"))
        or not isinstance(value.get("issuerName"), str)
        or not value["issuerName"].strip()
        or not isinstance(value.get("title"), str)
        or not value["title"].strip()
        or value.get("sourceName") != "巨潮资讯"
        or published_at is None
        or not window_from <= published_at.date() <= window_until
        or not isinstance(raw_column_ids, list)
        or any(not _safe_id(item) for item in raw_column_ids)
        or not isinstance(raw_announcement_types, list)
        or any(not _safe_id(item) for item in raw_announcement_types)
        or (raw_page_column is not None and not _safe_id(raw_page_column))
        or not isinstance(value.get("associationReported"), bool)
        or value.get("formalUsable") is not False
        or not isinstance(categories, list)
        or not categories
        or len(categories) != len(set(categories))
        or not isinstance(matched_keys, list)
        or len(matched_keys) != len(set(matched_keys))
        or not isinstance(source_statuses, list)
        or len(source_statuses) != len(categories)
        or any(status != "partial" for status in source_statuses)
    ):
        return None
    try:
        risk_categories = tuple(RiskCategory(item) for item in categories)
    except (TypeError, ValueError):
        return None
    if (
        set(matched_keys)
        != {CANONICAL_DISCOVERY_SEARCH_KEYS[item] for item in risk_categories}
    ):
        return None
    return tuple(
        OfficialRiskDocumentMetadata(
            source_contract_id=CNINFO_SOURCE_CONTRACT_ID,
            document_id=str(document_id),
            symbol=symbol,
            issuer_identity=issuer_identity,
            issuer_name=value["issuerName"],
            title=value["title"],
            published_at=published_at,
            source_name=value["sourceName"],
            source_url=value["sourceUrl"],
            candidate_category=category,
            raw_column_ids=tuple(raw_column_ids),
            raw_announcement_types=tuple(raw_announcement_types),
            raw_page_column=raw_page_column,
            association_reported=value["associationReported"],
            formal_usable=False,
        )
        for category in risk_categories
    )


def load_leader_risk_d8_manual_review_packet(
    packet: Any,
) -> Optional[LoadedLeaderRiskD8ManualReviewPacket]:
    """强校验待办包并还原完整官方公告目录。"""

    expected_top_keys = {
        "contractId", "status", "candidatePlanId", "radarRunId",
        "quoteBatchId", "asOf", "d2CollectedAt", "d2WindowFrom",
        "d2WindowUntil", "candidateSourcePacketSha256", "d2Audit",
        "createdAt", "sourcePacketSha256", "candidateCount",
        "d8VersionCount", "d8SubmissionReady", "reasons", "items", "gate",
    }
    if not isinstance(packet, Mapping) or set(packet) != expected_top_keys:
        return None
    as_of = _datetime(packet.get("asOf"))
    d2_collected_at = _datetime(packet.get("d2CollectedAt"))
    created_at = _datetime(packet.get("createdAt"))
    window_from = _date(packet.get("d2WindowFrom"))
    window_until = _date(packet.get("d2WindowUntil"))
    source_sha = packet.get("sourcePacketSha256")
    candidate_source_sha = packet.get("candidateSourcePacketSha256")
    d2_audit = packet.get("d2Audit")
    items = packet.get("items")
    candidate_count = packet.get("candidateCount")
    if (
        packet.get("contractId")
        != LEADER_RISK_D8_MANUAL_REVIEW_PACKET_CONTRACT_ID
        or packet.get("status") != "pending_human_review"
        or not _safe_id(packet.get("candidatePlanId"))
        or not _safe_id(packet.get("radarRunId"))
        or not _safe_id(packet.get("quoteBatchId"))
        or as_of is None
        or d2_collected_at is None
        or created_at is None
        or not as_of <= d2_collected_at <= created_at
        or window_from is None
        or window_until is None
        or window_from > window_until
        or not isinstance(source_sha, str)
        or SHA256_PATTERN.fullmatch(source_sha) is None
        or not isinstance(candidate_source_sha, str)
        or SHA256_PATTERN.fullmatch(candidate_source_sha) is None
        or not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count <= 0
        or not isinstance(items, list)
        or len(items) != candidate_count
        or packet.get("d8VersionCount") != 0
        or packet.get("d8SubmissionReady") is not False
        or packet.get("reasons") != []
        or not _false_gate(packet.get("gate"))
        or not isinstance(d2_audit, Mapping)
        or set(d2_audit) != {
            "deliveryStatus", "realPocStatus", "requestCount",
            "fetchedPageCount", "categoryCount", "shardCount",
            "queryCount", "ignoredDocumentCount", "queryCategoriesComplete",
            "queryPagesComplete", "queryWindowContinuous",
        }
        or d2_audit.get("deliveryStatus") not in {"missing", "partial"}
        or d2_audit.get("realPocStatus") != "partial"
        or d2_audit.get("categoryCount") != len(ALL_RISK_CATEGORIES)
        or not isinstance(d2_audit.get("requestCount"), int)
        or isinstance(d2_audit.get("requestCount"), bool)
        or not isinstance(d2_audit.get("fetchedPageCount"), int)
        or isinstance(d2_audit.get("fetchedPageCount"), bool)
        or d2_audit["requestCount"] < d2_audit["fetchedPageCount"]
        or d2_audit["fetchedPageCount"] <= 0
        or d2_audit.get("queryCount") != d2_audit["fetchedPageCount"]
        or not isinstance(d2_audit.get("shardCount"), int)
        or isinstance(d2_audit.get("shardCount"), bool)
        or d2_audit["shardCount"] <= 0
        or not isinstance(d2_audit.get("ignoredDocumentCount"), int)
        or isinstance(d2_audit.get("ignoredDocumentCount"), bool)
        or d2_audit["ignoredDocumentCount"] < 0
        or any(
            d2_audit.get(name) is not True
            for name in (
                "queryCategoriesComplete", "queryPagesComplete",
                "queryWindowContinuous",
            )
        )
    ):
        return None

    documents = []
    document_owner: dict[str, Tuple[str, str]] = {}
    source_items = []
    expected_item_keys = {
        "index", "symbol", "issuerIdentity", "sourceStatus",
        "sourceReasons", "documentCount", "documents", "review",
        "d8SubmissionReady",
    }
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != expected_item_keys:
            return None
        symbol = item.get("symbol")
        issuer_identity = item.get("issuerIdentity")
        raw_documents = item.get("documents")
        if (
            item.get("index") != index
            or not isinstance(symbol, str)
            or SYMBOL_PATTERN.fullmatch(symbol) is None
            or (
                issuer_identity is not None
                and (
                    not _safe_id(issuer_identity)
                    or not str(issuer_identity).startswith("cninfo-org:")
                )
            )
            or item.get("sourceStatus") not in {"partial", "missing"}
            or not isinstance(item.get("sourceReasons"), list)
            or any(not isinstance(reason, str) for reason in item["sourceReasons"])
            or not isinstance(raw_documents, list)
            or item.get("documentCount") != len(raw_documents)
            or item.get("review") is not None
            or item.get("d8SubmissionReady") is not False
            or (raw_documents and item.get("sourceStatus") != "partial")
            or (
                raw_documents
                and not isinstance(issuer_identity, str)
            )
            or (
                issuer_identity is None
                and item.get("sourceStatus") != "missing"
            )
        ):
            return None
        for raw_document in raw_documents:
            expanded = _document(
                raw_document,
                symbol=symbol,
                issuer_identity=issuer_identity,
                window_from=window_from,
                window_until=window_until,
            )
            if expanded is None:
                return None
            document_id = expanded[0].document_id
            owner = (symbol, issuer_identity)
            if document_id in document_owner:
                return None
            document_owner[document_id] = owner
            documents.extend(expanded)
        source_items.append({
            key: item[key]
            for key in (
                "index", "symbol", "issuerIdentity", "sourceStatus",
                "sourceReasons", "documentCount", "documents",
            )
        })

    source_payload = {
        "contractId": LEADER_RISK_D8_MANUAL_SOURCE_PACKET_CONTRACT_ID,
        "status": packet["status"],
        "candidatePlanId": packet["candidatePlanId"],
        "radarRunId": packet["radarRunId"],
        "quoteBatchId": packet["quoteBatchId"],
        "asOf": packet["asOf"],
        "d2CollectedAt": packet["d2CollectedAt"],
        "d2WindowFrom": packet["d2WindowFrom"],
        "d2WindowUntil": packet["d2WindowUntil"],
        "candidateSourcePacketSha256": candidate_source_sha,
        "d2Audit": d2_audit,
        "createdAt": packet["createdAt"],
        "candidateCount": candidate_count,
        "reasons": packet["reasons"],
        "items": source_items,
        "gate": packet["gate"],
    }
    if _digest(source_payload) != source_sha:
        return None
    return LoadedLeaderRiskD8ManualReviewPacket(
        candidate_plan_id=packet["candidatePlanId"],
        radar_run_id=packet["radarRunId"],
        quote_batch_id=packet["quoteBatchId"],
        as_of=as_of,
        d2_collected_at=d2_collected_at,
        d2_window_from=window_from,
        d2_window_until=window_until,
        created_at=created_at,
        source_packet_sha256=source_sha,
        candidate_count=candidate_count,
        shard_count=d2_audit["shardCount"],
        documents=tuple(documents),
    )


def prepare_leader_risk_d8_manual_review_session(
    packet: Any,
    selections: Sequence[LeaderRiskD8ManualReviewSelection],
    *,
    repository: Any,
    prepared_at: datetime,
    confirmed: bool,
    fetcher: Callable[..., OfficialRiskDocumentContentResult] = (
        fetch_official_risk_document_content
    ),
) -> LeaderRiskD8ManualReviewSessionResult:
    """真实下载正文并生成未填结论的人工处理材料。"""

    selection_tuple = tuple(selections)
    if confirmed is not True:
        return _blocked("risk_d8_manual_review_session_confirmation_missing")
    if (
        not isinstance(repository, LeaderRiskReviewRepository)
        or not isinstance(prepared_at, datetime)
        or prepared_at.tzinfo is None
        or prepared_at.utcoffset() is None
        or not 1 <= len(selection_tuple) <= 3
        or any(
            not isinstance(item, LeaderRiskD8ManualReviewSelection)
            or not _safe_id(item.document_id)
            or not isinstance(item.candidate_category, str)
            for item in selection_tuple
        )
        or len({item.document_id for item in selection_tuple})
        != len(selection_tuple)
    ):
        return _blocked("risk_d8_manual_review_session_scope_unverified")
    loaded = load_leader_risk_d8_manual_review_packet(packet)
    if loaded is None:
        return _blocked("risk_d8_manual_review_session_packet_unverified")
    prepared_at = prepared_at.astimezone(UTC)
    if prepared_at < loaded.created_at:
        return _blocked("risk_d8_manual_review_session_time_unverified")

    by_key = {
        (document.document_id, document.candidate_category.value): document
        for document in loaded.documents
    }
    selection_keys = tuple(
        (item.document_id, item.candidate_category)
        for item in selection_tuple
    )
    if any(key not in by_key for key in selection_keys):
        return _blocked("risk_d8_manual_review_session_selection_unverified")

    unique_document_count = len({
        document.document_id for document in loaded.documents
    })
    try:
        repository.save_review_batch(
            {
                "reviewBatchId": loaded.review_batch_id,
                "candidatePlanId": loaded.candidate_plan_id,
                "radarRunId": loaded.radar_run_id,
                "asOf": loaded.as_of,
                "windowFrom": loaded.d2_window_from,
                "windowUntil": loaded.d2_window_until,
                "candidateCount": loaded.candidate_count,
                "shardCount": loaded.shard_count,
                "categoryCount": len(ALL_RISK_CATEGORIES),
                "documentCount": unique_document_count,
                "queryCategoriesComplete": True,
                "queryPagesComplete": True,
                "queryWindowContinuous": True,
                "sourceContractId": CNINFO_SOURCE_CONTRACT_ID,
            },
            loaded.documents,
        )
    except Exception:
        return _blocked("risk_d8_manual_review_session_batch_persistence_failed")

    delivery = deliver_leader_risk_document_contents(
        repository,
        loaded.review_batch_id,
        tuple(
            LeaderRiskContentSelection(
                document_id=item.document_id,
                candidate_category=item.candidate_category,
            )
            for item in selection_tuple
        ),
        confirmed=True,
        fetcher=fetcher,
        clock=lambda: prepared_at,
    )
    if delivery.status is not LeaderRiskContentDeliveryStatus.READY:
        return _blocked("risk_d8_manual_review_session_content_unverified")

    session_items = []
    try:
        for selection in selection_tuple:
            form = build_review_form_data(
                repository,
                review_batch_id=loaded.review_batch_id,
                document_id=selection.document_id,
                candidate_category=selection.candidate_category,
                as_of=prepared_at,
                write_enabled=False,
            )
            document = form["document"]
            content = form["content"]
            candidate = form["candidate"]
            if (
                content.status is not ResearchFeatureStatus.READY
                or not isinstance(content.content_sha256, str)
                or SHA256_PATTERN.fullmatch(content.content_sha256) is None
                or candidate.manual_review_required is not True
                or form["writeEnabled"] is not False
                or form["versions"] != ()
            ):
                raise ValueError("D8人工材料合同无效")
            session_items.append(LeaderRiskD8ManualReviewSessionItem(
                document_id=document.document_id,
                candidate_category=document.candidate_category.value,
                symbol=document.symbol,
                issuer_identity=document.issuer_identity,
                issuer_name=document.issuer_name,
                title=document.title,
                source_url=document.source_url,
                published_at=document.published_at,
                content_sha256=content.content_sha256,
                candidate_id=candidate.candidate_id,
                required_fact_kinds=tuple(form["requiredFactKinds"]),
                pages=tuple(
                    LeaderRiskD8ManualReviewSessionPage(
                        page_number=page.page_number,
                        text=page.text,
                    )
                    for page in content.pages
                ),
            ))
    except Exception:
        return _blocked("risk_d8_manual_review_session_form_unverified")

    return LeaderRiskD8ManualReviewSessionResult(
        status=(
            LeaderRiskD8ManualReviewSessionStatus.PENDING_HUMAN_REVIEW
        ),
        review_batch_id=loaded.review_batch_id,
        candidate_plan_id=loaded.candidate_plan_id,
        radar_run_id=loaded.radar_run_id,
        as_of=loaded.as_of,
        prepared_at=prepared_at,
        source_packet_sha256=loaded.source_packet_sha256,
        items=tuple(session_items),
    )
