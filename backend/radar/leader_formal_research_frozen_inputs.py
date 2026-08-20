"""阶段6正式研究四源冻结输入包。

本模块只装载调用方显式提供的内存工件，并把它们绑定到同一候选计划、
行情批次和时点。它不联网、不读写数据库，也不补齐缺失证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Tuple

from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)


LEADER_FORMAL_RESEARCH_FROZEN_INPUT_COMPONENT_CONTRACT_ID = (
    "radar-leader-formal-research-frozen-input-component-v1"
)
LEADER_FORMAL_RESEARCH_FROZEN_INPUT_PACKAGE_CONTRACT_ID = (
    "radar-leader-formal-research-frozen-input-package-v1"
)
FROZEN_INPUT_PACKAGE_UNVERIFIED = (
    "leader_formal_research_frozen_input_package_unverified"
)
FROZEN_INPUT_COMPONENT_NAMES = (
    "history",
    "business_catalyst",
    "tradability",
    "sector_rule",
)
UTC = timezone.utc


@dataclass(frozen=True)
class LeaderFormalResearchFrozenInputComponent:
    component_name: str
    source_contract_id: str
    status: LeaderFormalResearchProductionSourceStatus
    source_time: Optional[datetime]
    fetched_at: Optional[datetime]
    symbols: Tuple[str, ...]
    payload: Any = field(repr=False)
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_FROZEN_INPUT_COMPONENT_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "componentName": self.component_name,
            "sourceContractId": self.source_contract_id,
            "status": (
                self.status.value
                if isinstance(
                    self.status,
                    LeaderFormalResearchProductionSourceStatus,
                )
                else None
            ),
            "sourceTime": (
                self.source_time.isoformat()
                if isinstance(self.source_time, datetime)
                else None
            ),
            "fetchedAt": (
                self.fetched_at.isoformat()
                if isinstance(self.fetched_at, datetime)
                else None
            ),
            "returnedCount": (
                len(self.symbols)
                if isinstance(self.symbols, tuple)
                else 0
            ),
        }


@dataclass(frozen=True)
class LeaderFormalResearchFrozenInputPackage:
    radar_run_id: str
    candidate_plan_id: str
    quote_batch_id: str
    as_of: datetime
    components: Tuple[LeaderFormalResearchFrozenInputComponent, ...] = field(
        repr=False
    )
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_FROZEN_INPUT_PACKAGE_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        components = (
            self.components
            if isinstance(self.components, tuple)
            else ()
        )
        return {
            "contractId": self.contract_id,
            "asOf": (
                self.as_of.isoformat()
                if isinstance(self.as_of, datetime)
                else None
            ),
            "componentCount": len(components),
            "components": [
                item.to_evidence()
                for item in components
                if type(item) is LeaderFormalResearchFrozenInputComponent
            ],
        }


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _fallback_contract_id(component_name: str) -> str:
    return (
        "radar-leader-"
        f"{component_name.replace('_', '-')}-frozen-input-v1"
    )


def _collected(
    *,
    component_name: str,
    source_contract_id: str,
    status: LeaderFormalResearchProductionSourceStatus,
    source_time: Optional[datetime] = None,
    fetched_at: Optional[datetime] = None,
    symbols: Tuple[str, ...] = (),
    payload: Any = None,
) -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name=component_name,
        source_contract_id=source_contract_id,
        status=status,
        source_time=source_time,
        fetched_at=fetched_at,
        symbols=symbols,
        payload=payload,
    )


def _source_contract_id(
    component_name: str,
    component: Any,
) -> str:
    value = getattr(component, "source_contract_id", None)
    if isinstance(value, str) and value.strip():
        return value
    return _fallback_contract_id(component_name)


def _package_structure_valid(package: Any) -> bool:
    return bool(
        type(package) is LeaderFormalResearchFrozenInputPackage
        and package.contract_id
        == LEADER_FORMAL_RESEARCH_FROZEN_INPUT_PACKAGE_CONTRACT_ID
        and isinstance(package.radar_run_id, str)
        and bool(package.radar_run_id.strip())
        and isinstance(package.candidate_plan_id, str)
        and bool(package.candidate_plan_id.strip())
        and isinstance(package.quote_batch_id, str)
        and bool(package.quote_batch_id.strip())
        and _aware_utc(package.as_of) is not None
        and isinstance(package.components, tuple)
        and all(
            type(item) is LeaderFormalResearchFrozenInputComponent
            and item.component_name in FROZEN_INPUT_COMPONENT_NAMES
            for item in package.components
        )
    )


def _package_bound(
    package: LeaderFormalResearchFrozenInputPackage,
    context: Any,
) -> bool:
    return bool(
        is_leader_research_runtime_source_context_valid(context)
        and package.radar_run_id == context.radar_run_id
        and package.candidate_plan_id
        == context.candidate_plan.candidate_set_id
        and package.quote_batch_id == context.quote_batch_id
        and _aware_utc(package.as_of) == _aware_utc(context.as_of)
    )


def _symbols_valid(
    symbols: Any,
    expected_symbols: Tuple[str, ...],
    *,
    require_complete: bool,
) -> bool:
    if (
        not isinstance(symbols, tuple)
        or any(not isinstance(symbol, str) for symbol in symbols)
        or len(symbols) != len(set(symbols))
        or any(symbol not in expected_symbols for symbol in symbols)
    ):
        return False
    if require_complete:
        return symbols == expected_symbols
    included = set(symbols)
    return tuple(
        symbol for symbol in expected_symbols if symbol in included
    ) == symbols


def _load_component(
    package: LeaderFormalResearchFrozenInputPackage,
    context: LeaderResearchRuntimeSourceContext,
    component_name: str,
) -> LeaderFormalResearchProductionCollectedSource:
    fallback_contract_id = _fallback_contract_id(component_name)
    if not _package_structure_valid(package) or not _package_bound(
        package,
        context,
    ):
        return _collected(
            component_name=component_name,
            source_contract_id=fallback_contract_id,
            status=(
                LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
            ),
        )

    matches = tuple(
        item
        for item in package.components
        if item.component_name == component_name
    )
    if not matches:
        return _collected(
            component_name=component_name,
            source_contract_id=fallback_contract_id,
            status=LeaderFormalResearchProductionSourceStatus.NOT_RUN,
        )
    if len(matches) != 1:
        return _collected(
            component_name=component_name,
            source_contract_id=_source_contract_id(
                component_name,
                matches[0],
            ),
            status=(
                LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
            ),
        )

    component = matches[0]
    source_contract_id = _source_contract_id(component_name, component)
    expected_symbols = tuple(
        item.symbol for item in context.candidate_plan.items
    )
    valid_contract = bool(
        component.contract_id
        == LEADER_FORMAL_RESEARCH_FROZEN_INPUT_COMPONENT_CONTRACT_ID
        and isinstance(component.source_contract_id, str)
        and bool(component.source_contract_id.strip())
        and isinstance(
            component.status,
            LeaderFormalResearchProductionSourceStatus,
        )
    )
    complete = (
        component.status
        == LeaderFormalResearchProductionSourceStatus.COMPLETED
    )
    valid_symbols = _symbols_valid(
        component.symbols,
        expected_symbols,
        require_complete=complete,
    )
    valid_payload = (
        component.payload is not None
        if complete
        else component.payload is None
    )
    if not valid_contract or not valid_symbols or not valid_payload:
        return _collected(
            component_name=component_name,
            source_contract_id=source_contract_id,
            status=(
                LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
            ),
            source_time=component.source_time,
            fetched_at=component.fetched_at,
            symbols=(component.symbols if valid_symbols else ()),
        )
    return _collected(
        component_name=component_name,
        source_contract_id=component.source_contract_id,
        status=component.status,
        source_time=component.source_time,
        fetched_at=component.fetched_at,
        symbols=component.symbols,
        payload=component.payload,
    )


def build_leader_formal_research_frozen_source_loaders(
    package: Any,
) -> LeaderFormalResearchProductionSourceLoaders:
    """从一个显式冻结包构造四个只读来源加载器。"""

    if (
        type(package) is not LeaderFormalResearchFrozenInputPackage
        or package.contract_id
        != LEADER_FORMAL_RESEARCH_FROZEN_INPUT_PACKAGE_CONTRACT_ID
    ):
        raise ValueError(FROZEN_INPUT_PACKAGE_UNVERIFIED)

    def loader(component_name: str):
        def load(
            context: LeaderResearchRuntimeSourceContext,
        ) -> LeaderFormalResearchProductionCollectedSource:
            return _load_component(package, context, component_name)

        return load

    return LeaderFormalResearchProductionSourceLoaders(
        history_loader=loader("history"),
        business_catalyst_loader=loader("business_catalyst"),
        tradability_loader=loader("tradability"),
        sector_rule_loader=loader("sector_rule"),
    )
