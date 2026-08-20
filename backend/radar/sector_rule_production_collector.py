"""行业正式规则证据的生产只读冻结与交付。

本模块只校验同轮行业特征、分类发布、历史覆盖、市场基准和阈值批准
并重放现有就绪度规则。不联网、不读写数据库、不自动拍阈值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Tuple

from radar.contracts import IndustryClassificationRelease, SectorFeatureBatch
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.sector_rule_readiness import (
    SectorHistoryCoverageEvidence,
    SectorMarketBaselineEvidence,
    SectorThresholdApprovalEvidence,
)
from radar.sector_rule_runtime_bridge import (
    SECTOR_RULE_RUNTIME_SOURCE_CONTRACT_ID,
    SectorRuleRuntimeBridgeStatus,
    SectorRuleRuntimeSourceBatch,
    build_sector_rule_runtime_bridge,
)


SECTOR_RULE_PRODUCTION_FROZEN_BATCH_CONTRACT_ID = (
    "radar-sector-rule-production-frozen-batch-v1"
)
SECTOR_RULE_PRODUCTION_SOURCE_CONTRACT_ID = (
    "radar-sector-rule-production-source-v1"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5


@dataclass(frozen=True, repr=False)
class SectorRuleProductionFrozenBatch:
    source_batch: Any = field(repr=False)
    fetched_at: Optional[datetime]
    source_status: LeaderFormalResearchProductionSourceStatus
    contract_id: str = SECTOR_RULE_PRODUCTION_FROZEN_BATCH_CONTRACT_ID

    def to_evidence(self) -> Mapping[str, object]:
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
            "ruleVersion": getattr(self.source_batch, "rule_version", None),
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
        component_name="sector_rule",
        source_contract_id=SECTOR_RULE_PRODUCTION_SOURCE_CONTRACT_ID,
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


def _batch_times(batch: Any) -> Optional[Tuple[datetime, datetime]]:
    if (
        type(batch) is not SectorRuleRuntimeSourceBatch
        or batch.contract_id != SECTOR_RULE_RUNTIME_SOURCE_CONTRACT_ID
        or not isinstance(batch.feature_batch, SectorFeatureBatch)
        or not isinstance(
            batch.classification_release,
            IndustryClassificationRelease,
        )
        or type(batch.history_evidence) is not SectorHistoryCoverageEvidence
        or type(batch.market_baseline_evidence)
        is not SectorMarketBaselineEvidence
        or type(batch.threshold_approval_evidence)
        is not SectorThresholdApprovalEvidence
    ):
        return None
    feature = batch.feature_batch
    release = batch.classification_release
    source_times = (
        feature.source_time,
        release.first_observed_at,
        batch.history_evidence.as_of,
        batch.market_baseline_evidence.as_of,
        batch.threshold_approval_evidence.approved_at,
    )
    fetched_times = (feature.fetched_at, release.fetched_at)
    if any(not _aware(value) for value in (*source_times, *fetched_times)):
        return None
    if feature.fetched_at + timedelta(
        seconds=MAXIMUM_FUTURE_SKEW_SECONDS
    ) < feature.source_time:
        return None
    return max(source_times), max(fetched_times)


def collect_sector_rule_production_source(
    context: Any,
    frozen: Any,
) -> LeaderFormalResearchProductionCollectedSource:
    """重放行业正式规则；九项任一缺失都不交付部分载荷。"""

    if not is_leader_research_runtime_source_context_valid(context):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    if (
        type(frozen) is not SectorRuleProductionFrozenBatch
        or frozen.contract_id
        != SECTOR_RULE_PRODUCTION_FROZEN_BATCH_CONTRACT_ID
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

    times = _batch_times(frozen.source_batch)
    if times is None or not _aware(frozen.fetched_at):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    source_time, embedded_fetched_at = times
    if (
        frozen.fetched_at < embedded_fetched_at
        or frozen.fetched_at + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        ) < source_time
        or source_time > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
        or frozen.fetched_at > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    try:
        bridge = build_sector_rule_runtime_bridge(
            context,
            source_batch=frozen.source_batch,
        )
    except Exception:
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    if (
        bridge.status != SectorRuleRuntimeBridgeStatus.READY
        or bridge.readiness_result is None
        or bridge.reasons
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        )
    symbols = tuple(item.symbol for item in context.candidate_plan.items)
    return _collected(
        LeaderFormalResearchProductionSourceStatus.COMPLETED,
        source_time=source_time,
        fetched_at=frozen.fetched_at,
        symbols=symbols,
        payload=frozen.source_batch,
    )


def build_sector_rule_production_loader(
    frozen: SectorRuleProductionFrozenBatch,
):
    """为现有四源生产 provider 构造无网络只读 loader。"""

    if type(frozen) is not SectorRuleProductionFrozenBatch:
        raise ValueError("sector_rule_production_batch_unverified")

    def load(context: LeaderResearchRuntimeSourceContext):
        return collect_sector_rule_production_source(context, frozen)

    return load
