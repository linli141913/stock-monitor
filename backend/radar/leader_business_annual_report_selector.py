"""从候选的巨潮材料中确定性选择最新有效中文完整版年报。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Optional, Sequence, Tuple

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueueItem,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlanItem,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)


ANNUAL_REPORT_SELECTOR_CONTRACT_ID = (
    "radar-leader-business-annual-report-selector-v1"
)
LeaderBusinessAnnualReportSelectionStatus = AutomaticBusinessEvidenceStatus
REPORT_TITLE_PATTERN = re.compile(
    r"(?<!\d)(20\d{2})年年度报告"
    r"(?:[（(](?:修订版|更新后|修正版)[）)])?$"
)
EXCLUDED_TITLE_MARKERS = (
    "摘要",
    "英文版",
    "外文版",
    "xbrl",
    "取消",
    "更正说明",
)
REVISION_TITLE_MARKERS = ("修订版", "更新后", "修正版")


@dataclass(frozen=True, repr=False)
class LeaderBusinessAnnualReportSelectionResult:
    status: LeaderBusinessAnnualReportSelectionStatus
    symbol: Optional[str]
    report_year: Optional[int] = None
    document: Optional[OfficialBusinessMaterialDocument] = field(
        default=None,
        repr=False,
    )
    replaced_document_ids: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    contract_id: str = ANNUAL_REPORT_SELECTOR_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


@dataclass(frozen=True)
class _ParsedAnnualReport:
    document: OfficialBusinessMaterialDocument = field(repr=False)
    report_year: int
    revised: bool


def _result(
    status: LeaderBusinessAnnualReportSelectionStatus,
    *,
    symbol: Optional[str],
    report_year: Optional[int] = None,
    document: Optional[OfficialBusinessMaterialDocument] = None,
    replaced_document_ids: Sequence[str] = (),
    reasons: Sequence[str] = (),
) -> LeaderBusinessAnnualReportSelectionResult:
    return LeaderBusinessAnnualReportSelectionResult(
        status=status,
        symbol=symbol,
        report_year=report_year,
        document=document,
        replaced_document_ids=tuple(replaced_document_ids),
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _parse_document(
    document: OfficialBusinessMaterialDocument,
) -> Optional[_ParsedAnnualReport]:
    normalized_title = re.sub(r"\s+", "", document.title).casefold()
    if any(marker in normalized_title for marker in EXCLUDED_TITLE_MARKERS):
        return None
    match = REPORT_TITLE_PATTERN.search(normalized_title)
    if match is None:
        return None
    return _ParsedAnnualReport(
        document=document,
        report_year=int(match.group(1)),
        revised=any(
            marker in normalized_title
            for marker in REVISION_TITLE_MARKERS
        ),
    )


def _scope_valid(
    plan_item: Any,
    queue_item: Any,
) -> bool:
    if (
        type(plan_item) is not LeaderRuntimeCandidatePlanItem
        or type(queue_item) is not LeaderBusinessMaterialReviewQueueItem
        or plan_item.index != queue_item.index
        or plan_item.symbol != queue_item.symbol
        or plan_item.industry_code != queue_item.industry_code
        or plan_item.industry_release_id != queue_item.industry_release_id
        or not _aware(plan_item.as_of)
        or not isinstance(queue_item.documents, tuple)
    ):
        return False
    seen_ids = set()
    seen_versions = set()
    for document in queue_item.documents:
        if (
            type(document) is not OfficialBusinessMaterialDocument
            or document.symbol != plan_item.symbol
            or not _aware(document.published_at)
            or document.published_at > plan_item.as_of
            or document.document_id in seen_ids
            or document.document_version in seen_versions
        ):
            return False
        seen_ids.add(document.document_id)
        seen_versions.add(document.document_version)
    return True


def select_latest_official_annual_report(
    plan_item: Any,
    queue_item: Any,
) -> LeaderBusinessAnnualReportSelectionResult:
    """选择唯一最新完整年报；来源和身份异常时不猜测。"""

    symbol = (
        plan_item.symbol
        if type(plan_item) is LeaderRuntimeCandidatePlanItem
        else None
    )
    if not _scope_valid(plan_item, queue_item):
        return _result(
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            reasons=("annual_report_scope_unverified",),
        )
    assert isinstance(queue_item, LeaderBusinessMaterialReviewQueueItem)
    status_map = {
        LeaderBusinessMaterialReviewItemStatus.MISSING: (
            LeaderBusinessAnnualReportSelectionStatus.MISSING
        ),
        LeaderBusinessMaterialReviewItemStatus.SOURCE_FAILED: (
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_FAILED
        ),
        LeaderBusinessMaterialReviewItemStatus.SOURCE_UNVERIFIED: (
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_UNVERIFIED
        ),
    }
    if queue_item.status in status_map:
        return _result(
            status_map[queue_item.status],
            symbol=symbol,
            reasons=queue_item.reasons or (
                f"annual_report_{queue_item.status.value}",
            ),
        )
    if queue_item.status is not (
        LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW
    ):
        return _result(
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            reasons=("annual_report_scope_unverified",),
        )

    parsed = tuple(
        candidate
        for document in queue_item.documents
        if (candidate := _parse_document(document)) is not None
    )
    if not parsed:
        return _result(
            LeaderBusinessAnnualReportSelectionStatus.MISSING,
            symbol=symbol,
            reasons=("annual_report_missing",),
        )
    latest_year = max(candidate.report_year for candidate in parsed)
    latest_year_documents = tuple(
        candidate
        for candidate in parsed
        if candidate.report_year == latest_year
    )
    revised = tuple(
        candidate
        for candidate in latest_year_documents
        if candidate.revised
    )
    selection_pool = revised or latest_year_documents
    latest_published_at = max(
        candidate.document.published_at
        for candidate in selection_pool
    )
    winners = tuple(
        candidate
        for candidate in selection_pool
        if candidate.document.published_at == latest_published_at
    )
    if len(winners) != 1:
        return _result(
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_UNVERIFIED,
            symbol=symbol,
            report_year=latest_year,
            reasons=("annual_report_selection_ambiguous",),
        )
    winner = winners[0]
    replaced = tuple(
        candidate.document.document_id
        for candidate in latest_year_documents
        if candidate.document.document_id != winner.document.document_id
    )
    return _result(
        LeaderBusinessAnnualReportSelectionStatus.READY,
        symbol=symbol,
        report_year=latest_year,
        document=winner.document,
        replaced_document_ids=replaced,
    )
