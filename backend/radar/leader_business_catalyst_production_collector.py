"""候选全集官方主营材料与人工复核的生产只读收集器。

调用方显式冻结官方材料和真实人工复核版本，本模块只校验同轮身份、时间、
全集覆盖并重放现有业务桥。不联网、不读写数据库，也不推断主营催化关系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Tuple

from radar.leader_business_catalyst_manual_review import (
    LeaderOfficialBusinessManualReviewArtifact,
    LeaderOfficialBusinessManualReviewBatchEntry,
)
from radar.leader_business_catalyst_official_adapter import (
    LeaderOfficialBusinessMaterialBatchEntry,
    LeaderOfficialBusinessProofArtifact,
    LeaderOfficialCatalystArtifact,
)
from radar.leader_business_catalyst_runtime_bridge import (
    LEADER_BUSINESS_CATALYST_RUNTIME_SOURCE_CONTRACT_ID,
    LeaderBusinessCatalystRuntimeBridgeStatus,
    LeaderBusinessCatalystRuntimeSourceBatch,
    build_leader_business_catalyst_runtime_bridge,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)


LEADER_BUSINESS_CATALYST_PRODUCTION_FROZEN_BATCH_CONTRACT_ID = (
    "radar-leader-business-catalyst-production-frozen-batch-v1"
)
LEADER_BUSINESS_CATALYST_PRODUCTION_SOURCE_CONTRACT_ID = (
    "radar-leader-business-catalyst-production-source-v1"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5


@dataclass(frozen=True, repr=False)
class LeaderBusinessCatalystProductionFrozenBatch:
    source_batch: Any = field(repr=False)
    fetched_at: Optional[datetime]
    source_status: LeaderFormalResearchProductionSourceStatus
    contract_id: str = (
        LEADER_BUSINESS_CATALYST_PRODUCTION_FROZEN_BATCH_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        material_entries = getattr(self.source_batch, "material_entries", ())
        review_entries = getattr(self.source_batch, "review_entries", ())
        return {
            "contractId": self.contract_id,
            "sourceStatus": (
                self.source_status.value
                if isinstance(
                    self.source_status,
                    LeaderFormalResearchProductionSourceStatus,
                )
                else None
            ),
            "fetchedAt": (
                self.fetched_at.isoformat()
                if isinstance(self.fetched_at, datetime)
                else None
            ),
            "materialEntryCount": (
                len(material_entries)
                if isinstance(material_entries, tuple)
                else 0
            ),
            "reviewEntryCount": (
                len(review_entries)
                if isinstance(review_entries, tuple)
                else 0
            ),
        }


def _collected(
    status: LeaderFormalResearchProductionSourceStatus,
    *,
    source_time: Optional[datetime] = None,
    fetched_at: Optional[datetime] = None,
    symbols: Tuple[str, ...] = (),
    payload: Any = None,
) -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name="business_catalyst",
        source_contract_id=(
            LEADER_BUSINESS_CATALYST_PRODUCTION_SOURCE_CONTRACT_ID
        ),
        status=status,
        source_time=source_time,
        fetched_at=fetched_at,
        symbols=symbols,
        payload=payload,
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _source_time(source_batch: Any) -> Optional[datetime]:
    if (
        type(source_batch) is not LeaderBusinessCatalystRuntimeSourceBatch
        or source_batch.contract_id
        != LEADER_BUSINESS_CATALYST_RUNTIME_SOURCE_CONTRACT_ID
        or not isinstance(source_batch.material_entries, tuple)
        or not isinstance(source_batch.review_entries, tuple)
    ):
        return None
    times = []
    for entry in source_batch.material_entries:
        if (
            type(entry) is not LeaderOfficialBusinessMaterialBatchEntry
            or type(entry.catalyst_artifact) is not LeaderOfficialCatalystArtifact
            or not isinstance(entry.proof_artifacts, tuple)
            or not entry.proof_artifacts
            or any(
                type(proof) is not LeaderOfficialBusinessProofArtifact
                for proof in entry.proof_artifacts
            )
        ):
            return None
        artifact_times = (
            entry.catalyst_artifact.published_at,
            *(proof.published_at for proof in entry.proof_artifacts),
        )
        if any(not _aware(value) for value in artifact_times):
            return None
        times.extend(artifact_times)
    for entry in source_batch.review_entries:
        if (
            type(entry) is not LeaderOfficialBusinessManualReviewBatchEntry
            or type(entry.review_artifact)
            is not LeaderOfficialBusinessManualReviewArtifact
            or not _aware(entry.review_artifact.reviewed_at)
        ):
            return None
        times.append(entry.review_artifact.reviewed_at)
    return max(times) if times else None


def collect_leader_business_catalyst_production_source(
    context: Any,
    frozen: Any,
) -> LeaderFormalResearchProductionCollectedSource:
    """重放官方材料和人工复核；不完整批次不交付部分载荷。"""

    if not is_leader_research_runtime_source_context_valid(context):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    if (
        type(frozen) is not LeaderBusinessCatalystProductionFrozenBatch
        or frozen.contract_id
        != LEADER_BUSINESS_CATALYST_PRODUCTION_FROZEN_BATCH_CONTRACT_ID
        or type(frozen.source_status)
        is not LeaderFormalResearchProductionSourceStatus
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    if frozen.source_status != (
        LeaderFormalResearchProductionSourceStatus.COMPLETED
    ):
        if frozen.source_batch is not None or frozen.fetched_at is not None:
            return _collected(
                LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
            )
        return _collected(frozen.source_status)

    source_time = _source_time(frozen.source_batch)
    if (
        source_time is None
        or not _aware(frozen.fetched_at)
        or frozen.fetched_at + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        ) < source_time
        or frozen.fetched_at > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    try:
        bridge = build_leader_business_catalyst_runtime_bridge(
            context,
            source_batch=frozen.source_batch,
        )
    except Exception:
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    expected_symbols = tuple(
        item.symbol for item in context.candidate_plan.items
    )
    ready_symbols = tuple(
        item.symbol
        for item in bridge.items
        if item.status == LeaderBusinessCatalystRuntimeBridgeStatus.READY
    )
    if (
        bridge.status != LeaderBusinessCatalystRuntimeBridgeStatus.READY
        or ready_symbols != expected_symbols
        or bridge.ready_count != len(expected_symbols)
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    return _collected(
        LeaderFormalResearchProductionSourceStatus.COMPLETED,
        source_time=source_time,
        fetched_at=frozen.fetched_at,
        symbols=expected_symbols,
        payload=frozen.source_batch,
    )


def build_leader_business_catalyst_production_loader(
    frozen: LeaderBusinessCatalystProductionFrozenBatch,
):
    """为现有四源生产 provider 构造无网络只读 loader。"""

    if type(frozen) is not LeaderBusinessCatalystProductionFrozenBatch:
        raise ValueError(
            "leader_business_catalyst_production_batch_unverified"
        )

    def load(context: LeaderResearchRuntimeSourceContext):
        return collect_leader_business_catalyst_production_source(
            context,
            frozen,
        )

    return load
