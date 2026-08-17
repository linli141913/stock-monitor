"""阶段6四类正式研究来源的生产重放适配器。

本模块只重放调用方显式加载的内存载荷。它不联网、不读写数据库，也不把
来源声明的 completed 直接当作业务就绪。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Tuple

from radar.leader_business_catalyst_runtime_bridge import (
    build_leader_business_catalyst_runtime_bridge,
)
from radar.leader_formal_research_production_provider import (
    LEADER_FORMAL_RESEARCH_PRODUCTION_COLLECTED_SOURCE_CONTRACT_ID,
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionProviderSet,
    LeaderFormalResearchProductionSourceStatus,
    ProductionCollector,
    build_leader_formal_research_production_provider_set,
)
from radar.leader_history_runtime_bridge import (
    build_leader_history_runtime_bridge,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
)
from radar.leader_tradability_runtime_bridge import (
    build_leader_tradability_runtime_bridge,
)
from radar.sector_rule_runtime_bridge import (
    build_sector_rule_runtime_bridge,
)


LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_LOADERS_CONTRACT_ID = (
    "radar-leader-formal-research-production-source-loaders-v1"
)
VALIDATED_COLLECTOR_UNVERIFIED = (
    "leader_formal_research_validated_collector_unverified"
)
SourceLoader = Callable[
    [LeaderResearchRuntimeSourceContext],
    LeaderFormalResearchProductionCollectedSource,
]


@dataclass(frozen=True)
class LeaderFormalResearchProductionSourceLoaders:
    history_loader: Optional[SourceLoader] = field(
        default=None,
        repr=False,
    )
    business_catalyst_loader: Optional[SourceLoader] = field(
        default=None,
        repr=False,
    )
    tradability_loader: Optional[SourceLoader] = field(
        default=None,
        repr=False,
    )
    sector_rule_loader: Optional[SourceLoader] = field(
        default=None,
        repr=False,
    )
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_LOADERS_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "configuredComponents": [
                name
                for name, loader in (
                    ("history", self.history_loader),
                    (
                        "business_catalyst",
                        self.business_catalyst_loader,
                    ),
                    ("tradability", self.tradability_loader),
                    ("sector_rule", self.sector_rule_loader),
                )
                if loader is not None
            ],
        }


def _ready_symbols(
    bridge: Any,
    expected_symbols: Tuple[str, ...],
) -> Tuple[str, ...]:
    items = getattr(bridge, "items", None)
    if isinstance(items, tuple):
        return tuple(
            item.symbol
            for item in items
            if (
                isinstance(getattr(item, "symbol", None), str)
                and getattr(getattr(item, "status", None), "value", None)
                == "ready"
            )
        )
    if (
        getattr(getattr(bridge, "status", None), "value", None) == "ready"
        and getattr(bridge, "candidate_count", None)
        == len(expected_symbols)
    ):
        return expected_symbols
    return ()


def _validated_collector(
    *,
    component_name: str,
    loader: Optional[SourceLoader],
    bridge_builder: Callable[..., Any],
    value_key: str,
) -> Optional[ProductionCollector]:
    if loader is None:
        return None

    def collect(
        context: LeaderResearchRuntimeSourceContext,
    ) -> LeaderFormalResearchProductionCollectedSource:
        source = loader(context)
        if (
            type(source)
            is not LeaderFormalResearchProductionCollectedSource
            or source.contract_id
            != LEADER_FORMAL_RESEARCH_PRODUCTION_COLLECTED_SOURCE_CONTRACT_ID
            or source.component_name != component_name
            or source.status
            != LeaderFormalResearchProductionSourceStatus.COMPLETED
            or source.payload is None
        ):
            return source
        expected_symbols = tuple(
            item.symbol for item in context.candidate_plan.items
        )
        try:
            bridge = bridge_builder(
                context,
                **{value_key: source.payload},
            )
        except Exception:
            return replace(
                source,
                status=(
                    LeaderFormalResearchProductionSourceStatus
                    .SOURCE_UNVERIFIED
                ),
                symbols=(),
                payload=None,
            )
        ready_symbols = _ready_symbols(bridge, expected_symbols)
        if ready_symbols != expected_symbols:
            return replace(
                source,
                status=(
                    LeaderFormalResearchProductionSourceStatus
                    .SOURCE_UNVERIFIED
                ),
                symbols=ready_symbols,
                payload=None,
            )
        return source

    return collect


def build_leader_formal_research_validated_provider_set(
    loaders: Any,
) -> LeaderFormalResearchProductionProviderSet:
    """构造四个先重放后交付的生产回调。"""

    if (
        type(loaders) is not LeaderFormalResearchProductionSourceLoaders
        or loaders.contract_id
        != LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_LOADERS_CONTRACT_ID
    ):
        raise ValueError(VALIDATED_COLLECTOR_UNVERIFIED)
    return build_leader_formal_research_production_provider_set(
        history_collector=_validated_collector(
            component_name="history",
            loader=loaders.history_loader,
            bridge_builder=build_leader_history_runtime_bridge,
            value_key="history_entries",
        ),
        business_catalyst_collector=_validated_collector(
            component_name="business_catalyst",
            loader=loaders.business_catalyst_loader,
            bridge_builder=build_leader_business_catalyst_runtime_bridge,
            value_key="source_batch",
        ),
        tradability_collector=_validated_collector(
            component_name="tradability",
            loader=loaders.tradability_loader,
            bridge_builder=build_leader_tradability_runtime_bridge,
            value_key="tradability_bundle",
        ),
        sector_rule_collector=_validated_collector(
            component_name="sector_rule",
            loader=loaders.sector_rule_loader,
            bridge_builder=build_sector_rule_runtime_bridge,
            value_key="source_batch",
        ),
    )
