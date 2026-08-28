"""阶段6官方风险查询与正文的真实只读编排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping, Tuple
from zoneinfo import ZoneInfo

from radar.leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewSourcePacketStatus,
    load_leader_business_material_review_source_packet,
)
from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_lifecycle_batch import LeaderRiskLifecycleBatchEntry
from radar.leader_risk_lifecycle_delivery import (
    MAXIMUM_PAGE_REQUESTS,
    LeaderRiskLifecycleDeliveryInput,
    LeaderRiskLifecycleDeliveryResult,
    deliver_leader_risk_lifecycle,
)
from radar.leader_runtime_candidate_plan import LeaderRuntimeCandidatePlan
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    fetch_official_risk_document_content,
)
from radar.sources.leader_risk_official import (
    CninfoIssuerResolutionStatus,
    MAXIMUM_CANDIDATE_SCOPE_COUNT,
    fetch_cninfo_issuer_scopes_from_roster,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, repr=False)
class LeaderRiskOfficialLiveDelivery:
    candidate_plan: LeaderRuntimeCandidatePlan = field(repr=False)
    delivery: LeaderRiskLifecycleDeliveryResult = field(repr=False)
    candidate_source_packet_sha256: str


def build_leader_risk_official_live_delivery(
    candidate_packet: Mapping[str, object],
    *,
    collected_at: datetime,
) -> LeaderRiskOfficialLiveDelivery:
    """从同轮候选来源包解析计划并执行七类巨潮只读发现。"""

    loaded = load_leader_business_material_review_source_packet(
        candidate_packet
    )
    if (
        loaded.status
        is not LeaderBusinessMaterialReviewSourcePacketStatus.READY
        or loaded.candidate_plan is None
        or not isinstance(candidate_packet.get("packetSha256"), str)
    ):
        raise ValueError("risk_official_candidate_source_unverified")
    if (
        not isinstance(collected_at, datetime)
        or collected_at.tzinfo is None
        or collected_at.utcoffset() is None
    ):
        raise ValueError("risk_official_collection_time_unverified")
    plan = loaded.candidate_plan
    collected_at = collected_at.astimezone(plan.as_of.tzinfo)
    symbols = tuple(item.symbol for item in plan.items)
    roster = fetch_cninfo_issuer_scopes_from_roster(
        symbols,
        fetched_at=collected_at,
    )
    if roster.status is not CninfoIssuerResolutionStatus.READY:
        raise ValueError("risk_official_issuer_roster_unverified")
    entries = tuple(
        LeaderRiskLifecycleBatchEntry(
            symbol=item.symbol,
            issuer_identity=roster.scopes[index].issuer_identity,
            versions=(),
        )
        for index, item in enumerate(plan.items)
    )
    shard_count = (
        len(roster.scopes) + MAXIMUM_CANDIDATE_SCOPE_COUNT - 1
    ) // MAXIMUM_CANDIDATE_SCOPE_COUNT
    trade_date = plan.as_of.astimezone(SHANGHAI_TZ).date()
    delivery = deliver_leader_risk_lifecycle(
        LeaderRiskLifecycleDeliveryInput(
            candidate_plan=plan,
            entries=entries,
            candidate_scopes=roster.scopes,
            window_from=date(trade_date.year - 2, 1, 1),
            window_until=trade_date,
            collected_at=collected_at,
            confirm_live_poc=True,
            max_page_requests=MAXIMUM_PAGE_REQUESTS * shard_count,
        )
    )
    return LeaderRiskOfficialLiveDelivery(
        candidate_plan=plan,
        delivery=delivery,
        candidate_source_packet_sha256=candidate_packet["packetSha256"],
    )


def fetch_leader_risk_official_document_contents(
    live_delivery: LeaderRiskOfficialLiveDelivery,
    *,
    clock: Callable[[], datetime],
    fetcher: Callable[..., OfficialRiskDocumentContentResult] = (
        fetch_official_risk_document_content
    ),
) -> Tuple[OfficialRiskDocumentContentResult, ...]:
    """下载D2明确发现的全部官方PDF；任一失败由下游批次失败关闭。"""

    discovery = getattr(
        live_delivery.delivery.lifecycle_result,
        "discovery_result",
        None,
    )
    if discovery is None:
        return ()
    documents = []
    seen = set()
    for item in discovery.items:
        for candidate_document in item.documents:
            document = candidate_document.document
            if document.document_id in seen:
                continue
            seen.add(document.document_id)
            documents.append(document)
    contents = []
    for document in documents:
        fetched_at = clock()
        if (
            not isinstance(fetched_at, datetime)
            or fetched_at.tzinfo is None
            or fetched_at.utcoffset() is None
        ):
            raise ValueError("risk_official_content_clock_unverified")
        content = fetcher(document, fetched_at=fetched_at)
        if (
            type(content) is not OfficialRiskDocumentContentResult
            or content.status != ResearchFeatureStatus.READY
        ):
            return tuple((*contents, content))
        contents.append(content)
    return tuple(contents)
