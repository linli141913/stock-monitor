"""候选全集主营事实人工提取的可编辑 JSON 合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from radar.leader_business_catalyst_features import BusinessProofType
from radar.leader_business_catalyst_official_adapter import (
    LeaderOfficialCatalystArtifact,
    OfficialDisclosurePlatform,
)
from radar.leader_business_material_human_extraction import (
    LeaderBusinessMaterialHumanExtractionBatch,
    LeaderBusinessMaterialHumanExtractionEntry,
    LeaderBusinessMaterialHumanExtractionStatus,
    build_leader_business_material_human_extraction_batch,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewQueueItem,
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    LeaderRuntimeCandidatePlanItem,
    LeaderRuntimeCandidatePlanStatus,
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)


LEADER_BUSINESS_MATERIAL_REVIEW_SUBMISSION_CONTRACT_ID = (
    "radar-leader-business-material-review-submission-v1"
)
LEADER_BUSINESS_MATERIAL_REVIEW_SOURCE_PACKET_CONTRACT_ID = (
    "radar-leader-business-material-review-source-packet-v1"
)
CNINFO_OFFICIAL_BUSINESS_CONTRACT_ID = (
    "radar-leader-business-official-cninfo-v1"
)
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
ROOT_KEYS = {
    "contractId",
    "candidatePlanId",
    "candidateCount",
    "asOf",
    "reviewBatchId",
    "reviewVersion",
    "supersedesReviewVersion",
    "reviewerKey",
    "reviewedAt",
    "entries",
}
ENTRY_KEYS = {
    "index",
    "symbol",
    "industryCode",
    "industryReleaseId",
    "materialStatus",
    "documents",
    "review",
}
REVIEW_KEYS = {
    "decision",
    "decisionSummary",
    "proofDocumentId",
    "proofType",
    "factSummary",
    "catalyst",
}
CATALYST_KEYS = {
    "catalystId",
    "documentId",
    "documentVersion",
    "sourceUrl",
    "publishedAt",
    "effectiveFrom",
    "effectiveUntil",
    "summary",
}


class LeaderBusinessMaterialReviewDecisionStatus(str, Enum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"


class LeaderBusinessMaterialReviewSubmissionStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    BLOCKED = "blocked"


class LeaderBusinessMaterialReviewSourcePacketStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LeaderBusinessMaterialReviewDecision:
    symbol: str
    status: LeaderBusinessMaterialReviewDecisionStatus
    decision_summary: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class LeaderBusinessMaterialReviewSubmissionResult:
    status: LeaderBusinessMaterialReviewSubmissionStatus
    candidate_plan_id: Optional[str]
    candidate_count: int
    review_batch_id: Optional[str] = None
    review_version: Optional[str] = None
    supersedes_review_version: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    confirmed_count: int = 0
    not_confirmed_count: int = 0
    decisions: Tuple[LeaderBusinessMaterialReviewDecision, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    extraction_batch: Optional[
        LeaderBusinessMaterialHumanExtractionBatch
    ] = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()
    contract_id: str = LEADER_BUSINESS_MATERIAL_REVIEW_SUBMISSION_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def __repr__(self) -> str:
        return (
            "LeaderBusinessMaterialReviewSubmissionResult("
            f"status={self.status.value!r}, "
            f"candidate_count={self.candidate_count!r})"
        )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "reviewBatchId": self.review_batch_id,
            "reviewVersion": self.review_version,
            "supersedesReviewVersion": self.supersedes_review_version,
            "reviewedAt": (
                self.reviewed_at.isoformat() if self.reviewed_at else None
            ),
            "confirmedCount": self.confirmed_count,
            "notConfirmedCount": self.not_confirmed_count,
            "extractionStatus": (
                self.extraction_batch.status.value
                if self.extraction_batch is not None
                else None
            ),
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


@dataclass(frozen=True, repr=False)
class LeaderBusinessMaterialReviewSourcePacketResult:
    status: LeaderBusinessMaterialReviewSourcePacketStatus
    candidate_plan: Optional[LeaderRuntimeCandidatePlan] = field(
        default=None,
        repr=False,
    )
    review_queue: Optional[LeaderBusinessMaterialReviewQueue] = field(
        default=None,
        repr=False,
    )
    reasons: Tuple[str, ...] = ()
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": (
                LEADER_BUSINESS_MATERIAL_REVIEW_SOURCE_PACKET_CONTRACT_ID
            ),
            "status": self.status.value,
            "candidateCount": (
                self.candidate_plan.candidate_count
                if self.candidate_plan is not None
                else 0
            ),
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _blank_review() -> Mapping[str, object]:
    return {
        "decision": None,
        "decisionSummary": None,
        "proofDocumentId": None,
        "proofType": None,
        "factSummary": None,
        "catalyst": None,
    }


def _queue_valid(
    candidate_plan: Any,
    review_queue: Any,
) -> bool:
    if (
        not isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        or not is_leader_runtime_candidate_plan_valid(candidate_plan)
        or type(review_queue) is not LeaderBusinessMaterialReviewQueue
        or review_queue.status is LeaderBusinessMaterialReviewQueueStatus.BLOCKED
        or review_queue.candidate_plan_id != candidate_plan.candidate_set_id
        or review_queue.candidate_count != candidate_plan.candidate_count
        or len(review_queue.items) != candidate_plan.candidate_count
    ):
        return False
    return all(
        item.index == index
        and item.symbol == plan_item.symbol
        and item.industry_code == plan_item.industry_code
        and item.industry_release_id == plan_item.industry_release_id
        for index, (item, plan_item) in enumerate(zip(
            review_queue.items,
            candidate_plan.items,
        ))
    )


def build_leader_business_material_review_template(
    candidate_plan: Any,
    review_queue: Any,
) -> Mapping[str, object]:
    if not _queue_valid(candidate_plan, review_queue):
        raise ValueError("business_material_review_template_contract_unverified")
    return {
        "contractId": LEADER_BUSINESS_MATERIAL_REVIEW_SUBMISSION_CONTRACT_ID,
        "candidatePlanId": candidate_plan.candidate_set_id,
        "candidateCount": candidate_plan.candidate_count,
        "asOf": candidate_plan.as_of.isoformat(),
        "reviewBatchId": None,
        "reviewVersion": None,
        "supersedesReviewVersion": None,
        "reviewerKey": None,
        "reviewedAt": None,
        "entries": [
            {
                "index": item.index,
                "symbol": item.symbol,
                "industryCode": item.industry_code,
                "industryReleaseId": item.industry_release_id,
                "materialStatus": item.status.value,
                "documents": [
                    {
                        "documentId": document.document_id,
                        "documentVersion": document.document_version,
                        "title": document.title,
                        "publishedAt": document.published_at.isoformat(),
                        "sourceUrl": document.source_url,
                        "sourceName": document.source_name,
                    }
                    for document in item.documents
                ],
                "review": _blank_review(),
            }
            for item in review_queue.items
        ],
    }


def _source_packet_payload(
    candidate_plan: LeaderRuntimeCandidatePlan,
    review_queue: LeaderBusinessMaterialReviewQueue,
) -> Mapping[str, object]:
    return {
        "candidatePlan": {
            "status": candidate_plan.status.value,
            "asOf": candidate_plan.as_of.isoformat(),
            "radarRunId": candidate_plan.radar_run_id,
            "quoteBatchId": candidate_plan.quote_batch_id,
            "marketSourceContractId": candidate_plan.market_source_contract_id,
            "scannedCount": candidate_plan.scanned_count,
            "mappedCount": candidate_plan.mapped_count,
            "candidateSetId": candidate_plan.candidate_set_id,
            "items": [
                {
                    "index": item.index,
                    "symbol": item.symbol,
                    "asOf": item.as_of.isoformat(),
                    "industryCode": item.industry_code,
                    "industryName": item.industry_name,
                    "industryReleaseId": item.industry_release_id,
                    "withinIndustryRank": item.within_industry_rank,
                    "quoteSourceContractId": item.quote_source_contract_id,
                    "sectorSourceContractId": item.sector_source_contract_id,
                    "contractId": item.contract_id,
                }
                for item in candidate_plan.items
            ],
        },
        "reviewQueue": {
            "status": review_queue.status.value,
            "candidatePlanId": review_queue.candidate_plan_id,
            "candidateCount": review_queue.candidate_count,
            "reasons": list(review_queue.reasons),
            "items": [
                {
                    "index": item.index,
                    "symbol": item.symbol,
                    "industryCode": item.industry_code,
                    "industryReleaseId": item.industry_release_id,
                    "status": item.status.value,
                    "reasons": list(item.reasons),
                    "documents": [
                        {
                            "documentId": document.document_id,
                            "documentVersion": document.document_version,
                            "symbol": document.symbol,
                            "issuerIdentity": document.issuer_identity,
                            "title": document.title,
                            "publishedAt": document.published_at.isoformat(),
                            "sourceUrl": document.source_url,
                            "sourceName": document.source_name,
                            "sourceContractId": document.source_contract_id,
                        }
                        for document in item.documents
                    ],
                }
                for item in review_queue.items
            ],
        },
    }


def _packet_digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def build_leader_business_material_review_source_packet(
    candidate_plan: Any,
    review_queue: Any,
) -> Mapping[str, object]:
    if not _queue_valid(candidate_plan, review_queue):
        raise ValueError("business_material_review_source_packet_unverified")
    payload = _source_packet_payload(candidate_plan, review_queue)
    return {
        "contractId": (
            LEADER_BUSINESS_MATERIAL_REVIEW_SOURCE_PACKET_CONTRACT_ID
        ),
        "packetSha256": _packet_digest(payload),
        "payload": payload,
    }


def _packet_blocked(reason: str):
    return LeaderBusinessMaterialReviewSourcePacketResult(
        status=LeaderBusinessMaterialReviewSourcePacketStatus.BLOCKED,
        reasons=(reason,),
    )


def load_leader_business_material_review_source_packet(
    packet: Any,
) -> LeaderBusinessMaterialReviewSourcePacketResult:
    if (
        not isinstance(packet, Mapping)
        or set(packet) != {"contractId", "packetSha256", "payload"}
        or packet.get("contractId")
        != LEADER_BUSINESS_MATERIAL_REVIEW_SOURCE_PACKET_CONTRACT_ID
        or not isinstance(packet.get("payload"), Mapping)
        or not isinstance(packet.get("packetSha256"), str)
        or packet["packetSha256"] != _packet_digest(packet["payload"])
    ):
        return _packet_blocked(
            "business_material_review_source_packet_digest_unverified"
        )
    try:
        payload = packet["payload"]
        if set(payload) != {"candidatePlan", "reviewQueue"}:
            raise ValueError
        raw_plan = payload["candidatePlan"]
        raw_queue = payload["reviewQueue"]
        if (
            not isinstance(raw_plan, Mapping)
            or not isinstance(raw_queue, Mapping)
            or set(raw_plan) != {
                "status", "asOf", "radarRunId", "quoteBatchId",
                "marketSourceContractId", "scannedCount", "mappedCount",
                "candidateSetId", "items",
            }
            or set(raw_queue) != {
                "status", "candidatePlanId", "candidateCount", "reasons",
                "items",
            }
            or not isinstance(raw_plan["items"], list)
            or not isinstance(raw_queue["items"], list)
            or not isinstance(raw_queue["reasons"], list)
        ):
            raise ValueError
        plan_as_of = _aware_datetime(raw_plan["asOf"])
        if plan_as_of is None:
            raise ValueError
        plan_items = []
        for raw in raw_plan["items"]:
            if not isinstance(raw, Mapping) or set(raw) != {
                "index", "symbol", "asOf", "industryCode", "industryName",
                "industryReleaseId", "withinIndustryRank",
                "quoteSourceContractId", "sectorSourceContractId",
                "contractId",
            }:
                raise ValueError
            item_as_of = _aware_datetime(raw["asOf"])
            if item_as_of is None:
                raise ValueError
            plan_items.append(LeaderRuntimeCandidatePlanItem(
                index=raw["index"],
                symbol=raw["symbol"],
                as_of=item_as_of,
                industry_code=raw["industryCode"],
                industry_name=raw["industryName"],
                industry_release_id=raw["industryReleaseId"],
                within_industry_rank=raw["withinIndustryRank"],
                quote_source_contract_id=raw["quoteSourceContractId"],
                sector_source_contract_id=raw["sectorSourceContractId"],
                contract_id=raw["contractId"],
            ))
        candidate_plan = LeaderRuntimeCandidatePlan(
            status=LeaderRuntimeCandidatePlanStatus(raw_plan["status"]),
            as_of=plan_as_of,
            radar_run_id=raw_plan["radarRunId"],
            quote_batch_id=raw_plan["quoteBatchId"],
            market_source_contract_id=raw_plan["marketSourceContractId"],
            items=tuple(plan_items),
            scanned_count=raw_plan["scannedCount"],
            mapped_count=raw_plan["mappedCount"],
            candidate_set_id=raw_plan["candidateSetId"],
        )
        queue_items = []
        for raw in raw_queue["items"]:
            if not isinstance(raw, Mapping) or set(raw) != {
                "index", "symbol", "industryCode", "industryReleaseId",
                "status", "reasons", "documents",
            } or not isinstance(raw["documents"], list) or not isinstance(
                raw["reasons"], list
            ):
                raise ValueError
            documents = []
            for document in raw["documents"]:
                if not isinstance(document, Mapping) or set(document) != {
                    "documentId", "documentVersion", "symbol",
                    "issuerIdentity", "title", "publishedAt", "sourceUrl",
                    "sourceName", "sourceContractId",
                }:
                    raise ValueError
                published_at = _aware_datetime(document["publishedAt"])
                if published_at is None:
                    raise ValueError
                documents.append(OfficialBusinessMaterialDocument(
                    document_id=document["documentId"],
                    document_version=document["documentVersion"],
                    symbol=document["symbol"],
                    issuer_identity=document["issuerIdentity"],
                    title=document["title"],
                    published_at=published_at,
                    source_url=document["sourceUrl"],
                    source_name=document["sourceName"],
                    source_contract_id=document["sourceContractId"],
                ))
            queue_items.append(LeaderBusinessMaterialReviewQueueItem(
                index=raw["index"],
                symbol=raw["symbol"],
                industry_code=raw["industryCode"],
                industry_release_id=raw["industryReleaseId"],
                status=LeaderBusinessMaterialReviewItemStatus(raw["status"]),
                documents=tuple(documents),
                reasons=tuple(raw["reasons"]),
            ))
        review_queue = LeaderBusinessMaterialReviewQueue(
            status=LeaderBusinessMaterialReviewQueueStatus(
                raw_queue["status"]
            ),
            candidate_plan_id=raw_queue["candidatePlanId"],
            candidate_count=raw_queue["candidateCount"],
            items=tuple(queue_items),
            reasons=tuple(raw_queue["reasons"]),
        )
    except (KeyError, TypeError, ValueError):
        return _packet_blocked(
            "business_material_review_source_packet_contract_unverified"
        )
    if not _queue_valid(candidate_plan, review_queue):
        return _packet_blocked(
            "business_material_review_source_packet_scope_unverified"
        )
    return LeaderBusinessMaterialReviewSourcePacketResult(
        status=LeaderBusinessMaterialReviewSourcePacketStatus.READY,
        candidate_plan=candidate_plan,
        review_queue=review_queue,
    )


def _blocked(plan_id: Optional[str], count: int, reason: str):
    return LeaderBusinessMaterialReviewSubmissionResult(
        status=LeaderBusinessMaterialReviewSubmissionStatus.BLOCKED,
        candidate_plan_id=plan_id,
        candidate_count=count,
        reasons=(reason,),
    )


def _required_id(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value == value.strip()
        and SAFE_ID_PATTERN.fullmatch(value) is not None
    )


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _official_cninfo_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "static.cninfo.com.cn"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.casefold().endswith(".pdf")
    )


def parse_leader_business_material_review_submission(
    candidate_plan: Any,
    review_queue: Any,
    payload: Any,
    *,
    clock: Callable[[], datetime],
) -> LeaderBusinessMaterialReviewSubmissionResult:
    plan_id = (
        candidate_plan.candidate_set_id
        if isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        else None
    )
    count = (
        candidate_plan.candidate_count
        if isinstance(candidate_plan, LeaderRuntimeCandidatePlan)
        else 0
    )
    if (
        not _queue_valid(candidate_plan, review_queue)
        or not isinstance(payload, Mapping)
        or set(payload) != ROOT_KEYS
        or not callable(clock)
    ):
        return _blocked(plan_id, count, "business_material_review_submission_contract_unverified")
    expected = build_leader_business_material_review_template(
        candidate_plan,
        review_queue,
    )
    if any(
        payload.get(key) != expected[key]
        for key in (
            "contractId",
            "candidatePlanId",
            "candidateCount",
            "asOf",
        )
    ):
        return _blocked(plan_id, count, "business_material_review_submission_scope_unverified")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != count:
        return _blocked(plan_id, count, "business_material_review_submission_scope_unverified")
    for submitted, frozen in zip(entries, expected["entries"]):
        if (
            not isinstance(submitted, Mapping)
            or set(submitted) != ENTRY_KEYS
            or any(
                submitted.get(key) != frozen[key]
                for key in ENTRY_KEYS - {"review"}
            )
        ):
            return _blocked(plan_id, count, "business_material_review_submission_scope_unverified")
    try:
        imported_at = clock()
    except Exception:
        imported_at = None
    if (
        not isinstance(imported_at, datetime)
        or imported_at.tzinfo is None
        or imported_at.utcoffset() is None
        or not _required_id(payload.get("reviewBatchId"))
        or not _required_id(payload.get("reviewVersion"))
        or (
            payload.get("supersedesReviewVersion") is not None
            and not _required_id(payload.get("supersedesReviewVersion"))
        )
        or payload.get("supersedesReviewVersion") == payload.get("reviewVersion")
        or not _required_id(payload.get("reviewerKey"))
    ):
        return _blocked(plan_id, count, "business_material_review_submission_identity_unverified")
    reviewed_at = _aware_datetime(payload.get("reviewedAt"))
    if reviewed_at is None or reviewed_at > imported_at:
        return _blocked(plan_id, count, "business_material_review_submission_time_unverified")

    extractions = []
    decisions = []
    for submitted, queue_item, plan_item in zip(
        entries,
        review_queue.items,
        candidate_plan.items,
    ):
        review = submitted.get("review")
        if not isinstance(review, Mapping) or set(review) != REVIEW_KEYS:
            return _blocked(plan_id, count, "business_material_review_submission_review_unverified")
        try:
            decision_status = LeaderBusinessMaterialReviewDecisionStatus(
                review.get("decision")
            )
        except (TypeError, ValueError):
            return _blocked(plan_id, count, "business_material_review_submission_review_unverified")
        if not _required_text(review.get("decisionSummary")):
            return _blocked(plan_id, count, "business_material_review_submission_review_unverified")
        decisions.append(LeaderBusinessMaterialReviewDecision(
            symbol=plan_item.symbol,
            status=decision_status,
            decision_summary=review["decisionSummary"].strip(),
        ))
        if decision_status is LeaderBusinessMaterialReviewDecisionStatus.NOT_CONFIRMED:
            if any(
                review.get(key) is not None
                for key in REVIEW_KEYS - {"decision", "decisionSummary"}
            ):
                return _blocked(plan_id, count, "business_material_review_submission_review_unverified")
            if queue_item.documents and reviewed_at < max(
                document.published_at for document in queue_item.documents
            ):
                return _blocked(plan_id, count, "business_material_review_submission_time_unverified")
            continue
        if (
            queue_item.status
            is not LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
            or not _required_id(review.get("proofDocumentId"))
            or not _required_text(review.get("factSummary"))
            or not isinstance(review.get("catalyst"), Mapping)
            or set(review["catalyst"]) != CATALYST_KEYS
        ):
            return _blocked(plan_id, count, "business_material_review_submission_review_unverified")
        document = next((
            document
            for document in queue_item.documents
            if document.document_id == review["proofDocumentId"]
        ), None)
        try:
            proof_type = BusinessProofType(review.get("proofType"))
        except (TypeError, ValueError):
            proof_type = None
        catalyst = review["catalyst"]
        published_at = _aware_datetime(catalyst.get("publishedAt"))
        effective_from = _aware_datetime(catalyst.get("effectiveFrom"))
        effective_until = (
            _aware_datetime(catalyst.get("effectiveUntil"))
            if catalyst.get("effectiveUntil") is not None
            else None
        )
        if (
            document is None
            or proof_type is None
            or not _required_id(catalyst.get("catalystId"))
            or not _required_id(catalyst.get("documentId"))
            or not _required_id(catalyst.get("documentVersion"))
            or not _official_cninfo_url(catalyst.get("sourceUrl"))
            or published_at is None
            or effective_from is None
            or not _required_text(catalyst.get("summary"))
            or published_at > effective_from
            or effective_from > reviewed_at
            or (
                catalyst.get("effectiveUntil") is not None
                and effective_until is None
            )
            or (
                effective_until is not None
                and effective_until < effective_from
            )
            or reviewed_at < document.published_at
        ):
            return _blocked(plan_id, count, "business_material_review_submission_source_unverified")
        catalyst_artifact = LeaderOfficialCatalystArtifact(
            platform=OfficialDisclosurePlatform.CNINFO,
            source_contract_id=CNINFO_OFFICIAL_BUSINESS_CONTRACT_ID,
            catalyst_id=catalyst["catalystId"],
            industry_code=plan_item.industry_code,
            industry_release_id=plan_item.industry_release_id,
            document_id=catalyst["documentId"],
            document_version=catalyst["documentVersion"],
            source_url=catalyst["sourceUrl"],
            published_at=published_at,
            effective_from=effective_from,
            effective_until=effective_until,
            summary=catalyst["summary"].strip(),
        )
        extractions.append(LeaderBusinessMaterialHumanExtractionEntry(
            symbol=plan_item.symbol,
            reviewer_key=payload["reviewerKey"].strip(),
            reviewed_at=reviewed_at,
            catalyst_artifact=catalyst_artifact,
            proof_document_id=document.document_id,
            proof_type=proof_type,
            fact_summary=review["factSummary"].strip(),
        ))
    extraction_batch = build_leader_business_material_human_extraction_batch(
        candidate_plan,
        review_queue,
        tuple(extractions),
        validated_at=imported_at,
    )
    if extraction_batch.status is LeaderBusinessMaterialHumanExtractionStatus.BLOCKED:
        return _blocked(plan_id, count, "business_material_review_submission_extraction_unverified")
    confirmed_count = len(extractions)
    not_confirmed_count = len(decisions) - confirmed_count
    return LeaderBusinessMaterialReviewSubmissionResult(
        status=(
            LeaderBusinessMaterialReviewSubmissionStatus.READY
            if extraction_batch.status
            is LeaderBusinessMaterialHumanExtractionStatus.READY
            else LeaderBusinessMaterialReviewSubmissionStatus.MISSING
        ),
        candidate_plan_id=plan_id,
        candidate_count=count,
        review_batch_id=payload["reviewBatchId"],
        review_version=payload["reviewVersion"],
        supersedes_review_version=payload["supersedesReviewVersion"],
        reviewed_at=reviewed_at,
        confirmed_count=confirmed_count,
        not_confirmed_count=not_confirmed_count,
        decisions=tuple(decisions),
        extraction_batch=extraction_batch,
    )
