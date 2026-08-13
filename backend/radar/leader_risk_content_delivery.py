"""阶段6 D8有限正文快照交付。

入口只消费当前真实D2审核批次中的明确公告，先在内存中完成全部官方PDF
下载与解码，再原子写入正文快照。它不生成事件、人工审核版本或正式状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_review_repository import (
    LeaderRiskReviewRepository,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    fetch_official_risk_document_content,
)


UTC = timezone.utc
MAXIMUM_DELIVERY_DOCUMENT_COUNT = 3


class LeaderRiskContentDeliveryStatus(str, Enum):
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    READY = "ready"


@dataclass(frozen=True)
class LeaderRiskContentSelection:
    document_id: str
    candidate_category: str


@dataclass(frozen=True)
class LeaderRiskContentDeliveryItem:
    document_id: str
    candidate_category: str
    status: str
    content_sha256: Optional[str] = None
    byte_count: Optional[int] = None
    page_count: Optional[int] = None
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LeaderRiskContentDeliveryReport:
    status: LeaderRiskContentDeliveryStatus
    review_batch_id: str
    requested_count: int
    fetched_count: int = 0
    persisted_count: int = 0
    available_count: int = 0
    items: Tuple[LeaderRiskContentDeliveryItem, ...] = field(
        default_factory=tuple
    )
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    formal_usable: bool = False


ContentFetcher = Callable[..., OfficialRiskDocumentContentResult]


def _report(
    status: LeaderRiskContentDeliveryStatus,
    review_batch_id: str,
    requested_count: int,
    reason: str,
) -> LeaderRiskContentDeliveryReport:
    return LeaderRiskContentDeliveryReport(
        status=status,
        review_batch_id=review_batch_id,
        requested_count=requested_count,
        reasons=(reason,),
    )


def deliver_leader_risk_document_contents(
    repository: LeaderRiskReviewRepository,
    review_batch_id: str,
    selections: Sequence[LeaderRiskContentSelection],
    *,
    confirmed: bool,
    fetcher: ContentFetcher = fetch_official_risk_document_content,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> LeaderRiskContentDeliveryReport:
    """下载并原子保存不超过三份明确选择的官方公告正文。"""

    selection_tuple = tuple(selections)
    requested_count = len(selection_tuple)
    if confirmed is not True:
        return _report(
            LeaderRiskContentDeliveryStatus.NOT_RUN,
            review_batch_id,
            requested_count,
            "risk_content_delivery_confirmation_missing",
        )
    if (
        not isinstance(repository, LeaderRiskReviewRepository)
        or not isinstance(review_batch_id, str)
        or not review_batch_id.strip()
        or not 1 <= requested_count <= MAXIMUM_DELIVERY_DOCUMENT_COUNT
        or any(
            not isinstance(item, LeaderRiskContentSelection)
            or not item.document_id.strip()
            or not item.candidate_category.strip()
            for item in selection_tuple
        )
        or len({item.document_id for item in selection_tuple})
        != requested_count
    ):
        return _report(
            LeaderRiskContentDeliveryStatus.BLOCKED,
            review_batch_id,
            requested_count,
            "risk_content_delivery_scope_unverified",
        )

    summary = repository.get_latest_review_batch_summary()
    if (
        summary is None
        or summary["reviewBatchId"] != review_batch_id
        or summary["queryCategoriesComplete"] is not True
        or summary["queryPagesComplete"] is not True
        or summary["queryWindowContinuous"] is not True
    ):
        return _report(
            LeaderRiskContentDeliveryStatus.BLOCKED,
            review_batch_id,
            requested_count,
            "risk_content_delivery_review_batch_unverified",
        )

    fetched_at = clock()
    if (
        not isinstance(fetched_at, datetime)
        or fetched_at.tzinfo is None
        or fetched_at.utcoffset() is None
    ):
        return _report(
            LeaderRiskContentDeliveryStatus.BLOCKED,
            review_batch_id,
            requested_count,
            "risk_content_delivery_clock_unverified",
        )

    verified_selections = []
    for selection in selection_tuple:
        try:
            detail = repository.get_review_batch_document(
                review_batch_id,
                selection.document_id,
                selection.candidate_category,
            )
        except Exception:
            return LeaderRiskContentDeliveryReport(
                status=LeaderRiskContentDeliveryStatus.BLOCKED,
                review_batch_id=review_batch_id,
                requested_count=requested_count,
                fetched_count=0,
                available_count=0,
                items=(),
                reasons=("risk_content_delivery_selection_unverified",),
            )
        verified_selections.append((selection, detail))

    items = []
    contents = []
    available_count = 0
    for selection, detail in verified_selections:
        if detail["hasContentSnapshot"]:
            available_count += 1
            items.append(LeaderRiskContentDeliveryItem(
                document_id=selection.document_id,
                candidate_category=selection.candidate_category,
                status="already_available",
            ))
            continue
        content = fetcher(
            detail["document"],
            fetched_at=fetched_at,
        )
        if (
            not isinstance(content, OfficialRiskDocumentContentResult)
            or content.status != ResearchFeatureStatus.READY
            or content.formal_usable is not False
            or content.reasons
        ):
            reasons = (
                tuple(content.reasons)
                if isinstance(content, OfficialRiskDocumentContentResult)
                else ("risk_content_delivery_fetch_contract_unverified",)
            )
            items.append(LeaderRiskContentDeliveryItem(
                document_id=selection.document_id,
                candidate_category=selection.candidate_category,
                status=(
                    content.status.value
                    if isinstance(content, OfficialRiskDocumentContentResult)
                    else "source_unverified"
                ),
                reasons=reasons,
            ))
            return LeaderRiskContentDeliveryReport(
                status=LeaderRiskContentDeliveryStatus.BLOCKED,
                review_batch_id=review_batch_id,
                requested_count=requested_count,
                fetched_count=len(contents),
                available_count=available_count,
                items=tuple(items),
                reasons=("risk_content_delivery_fetch_incomplete",),
            )
        contents.append((selection.candidate_category, content))
        items.append(LeaderRiskContentDeliveryItem(
            document_id=selection.document_id,
            candidate_category=selection.candidate_category,
            status="ready",
            content_sha256=content.content_sha256,
            byte_count=content.byte_count,
            page_count=content.page_count,
        ))

    try:
        persisted_count = (
            repository.save_document_content_snapshots(
                review_batch_id,
                contents,
            )
            if contents else 0
        )
    except Exception:
        return LeaderRiskContentDeliveryReport(
            status=LeaderRiskContentDeliveryStatus.BLOCKED,
            review_batch_id=review_batch_id,
            requested_count=requested_count,
            fetched_count=len(contents),
            available_count=available_count,
            items=tuple(items),
            reasons=("risk_content_delivery_persistence_failed",),
        )
    return LeaderRiskContentDeliveryReport(
        status=LeaderRiskContentDeliveryStatus.READY,
        review_batch_id=review_batch_id,
        requested_count=requested_count,
        fetched_count=len(contents),
        persisted_count=persisted_count,
        available_count=available_count + len(contents),
        items=tuple(items),
        reasons=("risk_content_delivery_ready",),
    )
