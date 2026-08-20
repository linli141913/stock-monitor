"""阶段6正式研究真实来源交付合同。

交付件把来源载荷与同轮身份、时间和全集覆盖证明绑定。模块只验证调用方
显式传入的内存对象，不联网、不读写数据库，也不生成正式评分或状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)


LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_PROOF_CONTRACT_ID = (
    "radar-leader-formal-research-production-source-proof-v1"
)
LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_DELIVERY_CONTRACT_ID = (
    "radar-leader-formal-research-production-source-delivery-v1"
)
LEADER_FORMAL_RESEARCH_PRODUCTION_COLLECTED_SOURCE_CONTRACT_ID = (
    "radar-leader-formal-research-production-collected-source-v1"
)
LEADER_FORMAL_RESEARCH_PRODUCTION_PROVIDER_SET_CONTRACT_ID = (
    "radar-leader-formal-research-production-provider-set-v1"
)
DELIVERY_CONTRACT_UNVERIFIED = (
    "leader_formal_research_production_delivery_contract_unverified"
)
DELIVERY_IDENTITY_UNVERIFIED = (
    "leader_formal_research_production_delivery_identity_unverified"
)
DELIVERY_TIME_UNVERIFIED = (
    "leader_formal_research_production_delivery_time_unverified"
)
DELIVERY_COVERAGE_UNVERIFIED = (
    "leader_formal_research_production_delivery_coverage_unverified"
)
DELIVERY_PAYLOAD_UNVERIFIED = (
    "leader_formal_research_production_delivery_payload_unverified"
)
DELIVERY_NOT_RUN = (
    "leader_formal_research_production_delivery_not_run"
)
DELIVERY_SOURCE_FAILED = (
    "leader_formal_research_production_delivery_source_failed"
)
DELIVERY_SOURCE_UNVERIFIED = (
    "leader_formal_research_production_delivery_source_unverified"
)
PRODUCTION_COMPONENT_NAMES = (
    "sector_rule",
    "history",
    "business_catalyst",
    "tradability",
    "risk",
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5
UTC = timezone.utc


class LeaderFormalResearchProductionSourceStatus(str, Enum):
    COMPLETED = "completed"
    NOT_RUN = "not_run"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


class LeaderFormalResearchProductionDeliveryResolutionStatus(str, Enum):
    READY = "ready"
    NOT_RUN = "not_run"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class LeaderFormalResearchProductionSourceProof:
    component_name: str
    source_contract_id: str
    status: LeaderFormalResearchProductionSourceStatus
    radar_run_id: str
    candidate_plan_id: str
    quote_batch_id: str
    as_of: datetime
    source_time: Optional[datetime]
    fetched_at: Optional[datetime]
    expected_count: int
    returned_count: int
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_PROOF_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "componentName": self.component_name,
            "sourceContractId": self.source_contract_id,
            "status": self.status.value,
            "asOf": self.as_of.isoformat(),
            "sourceTime": (
                self.source_time.isoformat()
                if self.source_time is not None
                else None
            ),
            "fetchedAt": (
                self.fetched_at.isoformat()
                if self.fetched_at is not None
                else None
            ),
            "expectedCount": self.expected_count,
            "returnedCount": self.returned_count,
        }


@dataclass(frozen=True)
class LeaderFormalResearchProductionSourceDelivery:
    proof: Any = field(repr=False)
    payload: Any = field(repr=False)
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_DELIVERY_CONTRACT_ID
    )


@dataclass(frozen=True)
class LeaderFormalResearchProductionDeliveryResolution:
    status: LeaderFormalResearchProductionDeliveryResolutionStatus
    proof: Optional[LeaderFormalResearchProductionSourceProof] = field(
        default=None,
        repr=False,
    )
    payload: Any = field(default=None, repr=False)
    reasons: Tuple[str, ...] = ()
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_DELIVERY_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "componentName": (
                self.proof.component_name
                if self.proof is not None
                else None
            ),
            "sourceContractId": (
                self.proof.source_contract_id
                if self.proof is not None
                else None
            ),
            "expectedCount": (
                self.proof.expected_count
                if self.proof is not None
                else 0
            ),
            "returnedCount": (
                self.proof.returned_count
                if self.proof is not None
                else 0
            ),
            "reasons": list(self.reasons),
        }


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _valid_count(value: Any) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _unverified(
    *reasons: str,
    proof: Optional[LeaderFormalResearchProductionSourceProof] = None,
) -> LeaderFormalResearchProductionDeliveryResolution:
    return LeaderFormalResearchProductionDeliveryResolution(
        status=(
            LeaderFormalResearchProductionDeliveryResolutionStatus
            .SOURCE_UNVERIFIED
        ),
        proof=proof,
        reasons=_dedupe(reasons),
    )


def resolve_leader_formal_research_production_delivery(
    context: Any,
    *,
    component_name: str,
    value: Any,
) -> LeaderFormalResearchProductionDeliveryResolution:
    """把一个真实来源交付件绑定到当前候选计划。"""

    if (
        not is_leader_research_runtime_source_context_valid(context)
        or component_name not in PRODUCTION_COMPONENT_NAMES
        or type(value) is not LeaderFormalResearchProductionSourceDelivery
        or value.contract_id
        != LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_DELIVERY_CONTRACT_ID
        or type(value.proof)
        is not LeaderFormalResearchProductionSourceProof
    ):
        return _unverified(DELIVERY_CONTRACT_UNVERIFIED)
    proof = value.proof
    if (
        proof.contract_id
        != LEADER_FORMAL_RESEARCH_PRODUCTION_SOURCE_PROOF_CONTRACT_ID
        or not isinstance(proof.source_contract_id, str)
        or not proof.source_contract_id.strip()
        or not isinstance(proof.status, LeaderFormalResearchProductionSourceStatus)
    ):
        return _unverified(DELIVERY_CONTRACT_UNVERIFIED, proof=proof)

    normalized_as_of = _aware_utc(proof.as_of)
    if any((
        proof.component_name != component_name,
        proof.radar_run_id != context.radar_run_id,
        proof.candidate_plan_id
        != context.candidate_plan.candidate_set_id,
        proof.quote_batch_id != context.quote_batch_id,
        normalized_as_of != _aware_utc(context.as_of),
    )):
        return _unverified(DELIVERY_IDENTITY_UNVERIFIED, proof=proof)
    candidate_count = context.candidate_plan.candidate_count
    if (
        not _valid_count(proof.expected_count)
        or not _valid_count(proof.returned_count)
        or proof.expected_count != candidate_count
        or proof.returned_count > proof.expected_count
    ):
        return _unverified(DELIVERY_COVERAGE_UNVERIFIED, proof=proof)

    if proof.status == LeaderFormalResearchProductionSourceStatus.COMPLETED:
        source_time = _aware_utc(proof.source_time)
        fetched_at = _aware_utc(proof.fetched_at)
        reasons = []
        if proof.returned_count != candidate_count:
            reasons.append(DELIVERY_COVERAGE_UNVERIFIED)
        if (
            source_time is None
            or fetched_at is None
            or source_time
            > context.as_of + timedelta(seconds=MAXIMUM_FUTURE_SKEW_SECONDS)
            or fetched_at + timedelta(
                seconds=MAXIMUM_FUTURE_SKEW_SECONDS
            ) < source_time
        ):
            reasons.append(DELIVERY_TIME_UNVERIFIED)
        if value.payload is None:
            reasons.append(DELIVERY_PAYLOAD_UNVERIFIED)
        if reasons:
            return _unverified(*reasons, proof=proof)
        return LeaderFormalResearchProductionDeliveryResolution(
            status=(
                LeaderFormalResearchProductionDeliveryResolutionStatus.READY
            ),
            proof=proof,
            payload=value.payload,
        )

    if value.payload is not None:
        return _unverified(DELIVERY_PAYLOAD_UNVERIFIED, proof=proof)
    status_map = {
        LeaderFormalResearchProductionSourceStatus.NOT_RUN: (
            LeaderFormalResearchProductionDeliveryResolutionStatus.NOT_RUN,
            DELIVERY_NOT_RUN,
        ),
        LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED: (
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_FAILED,
            DELIVERY_SOURCE_FAILED,
        ),
        LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED: (
            LeaderFormalResearchProductionDeliveryResolutionStatus
            .SOURCE_UNVERIFIED,
            DELIVERY_SOURCE_UNVERIFIED,
        ),
    }
    resolution_status, reason = status_map[proof.status]
    return LeaderFormalResearchProductionDeliveryResolution(
        status=resolution_status,
        proof=proof,
        reasons=(reason,),
    )


def is_leader_formal_research_production_delivery(value: Any) -> bool:
    return type(value) is LeaderFormalResearchProductionSourceDelivery


@dataclass(frozen=True)
class LeaderFormalResearchProductionCollectedSource:
    component_name: str
    source_contract_id: str
    status: LeaderFormalResearchProductionSourceStatus
    source_time: Optional[datetime]
    fetched_at: Optional[datetime]
    symbols: Tuple[str, ...]
    payload: Any = field(repr=False)
    reasons: Tuple[str, ...] = ()
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_COLLECTED_SOURCE_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "componentName": self.component_name,
            "sourceContractId": self.source_contract_id,
            "status": self.status.value,
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
            "returnedCount": len(self.symbols),
            "reasons": list(self.reasons),
        }


ProductionCollector = Callable[
    [LeaderResearchRuntimeSourceContext],
    LeaderFormalResearchProductionCollectedSource,
]


def _provider_source_contract_id(component_name: str) -> str:
    return (
        "radar-leader-"
        f"{component_name.replace('_', '-')}-production-source-v1"
    )


def _provider_delivery(
    context: LeaderResearchRuntimeSourceContext,
    *,
    component_name: str,
    collector: Optional[ProductionCollector],
) -> LeaderFormalResearchProductionSourceDelivery:
    if not is_leader_research_runtime_source_context_valid(context):
        raise ValueError(DELIVERY_IDENTITY_UNVERIFIED)
    expected_symbols = tuple(
        item.symbol for item in context.candidate_plan.items
    )
    fallback_contract_id = _provider_source_contract_id(component_name)
    if collector is None:
        source = LeaderFormalResearchProductionCollectedSource(
            component_name=component_name,
            source_contract_id=fallback_contract_id,
            status=LeaderFormalResearchProductionSourceStatus.NOT_RUN,
            source_time=None,
            fetched_at=None,
            symbols=(),
            payload=None,
        )
    else:
        try:
            source = collector(context)
        except Exception:
            source = LeaderFormalResearchProductionCollectedSource(
                component_name=component_name,
                source_contract_id=fallback_contract_id,
                status=(
                    LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED
                ),
                source_time=None,
                fetched_at=None,
                symbols=(),
                payload=None,
            )

    valid_source = bool(
        type(source) is LeaderFormalResearchProductionCollectedSource
        and source.contract_id
        == LEADER_FORMAL_RESEARCH_PRODUCTION_COLLECTED_SOURCE_CONTRACT_ID
        and source.component_name == component_name
        and isinstance(source.source_contract_id, str)
        and bool(source.source_contract_id.strip())
        and isinstance(
            source.status,
            LeaderFormalResearchProductionSourceStatus,
        )
        and isinstance(source.symbols, tuple)
        and all(isinstance(symbol, str) for symbol in source.symbols)
        and len(source.symbols) == len(set(source.symbols))
        and all(symbol in expected_symbols for symbol in source.symbols)
    )
    source_contract_id = (
        source.source_contract_id
        if valid_source
        else fallback_contract_id
    )
    symbols = source.symbols if valid_source else ()
    source_time = source.source_time if valid_source else None
    fetched_at = source.fetched_at if valid_source else None
    status = (
        source.status
        if valid_source
        else LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
    )
    payload = source.payload if valid_source else None
    if status == LeaderFormalResearchProductionSourceStatus.COMPLETED:
        if symbols != expected_symbols or payload is None:
            status = (
                LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
            )
            payload = None
    elif payload is not None:
        status = LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        payload = None

    proof = LeaderFormalResearchProductionSourceProof(
        component_name=component_name,
        source_contract_id=source_contract_id,
        status=status,
        radar_run_id=context.radar_run_id,
        candidate_plan_id=context.candidate_plan.candidate_set_id,
        quote_batch_id=context.quote_batch_id,
        as_of=context.as_of,
        source_time=source_time,
        fetched_at=fetched_at,
        expected_count=len(expected_symbols),
        returned_count=len(symbols),
    )
    return LeaderFormalResearchProductionSourceDelivery(
        proof=proof,
        payload=payload,
    )


@dataclass(frozen=True)
class LeaderFormalResearchProductionProviderSet:
    history_collector: Optional[ProductionCollector] = field(
        default=None,
        repr=False,
    )
    business_catalyst_collector: Optional[ProductionCollector] = field(
        default=None,
        repr=False,
    )
    tradability_collector: Optional[ProductionCollector] = field(
        default=None,
        repr=False,
    )
    sector_rule_collector: Optional[ProductionCollector] = field(
        default=None,
        repr=False,
    )
    contract_id: str = (
        LEADER_FORMAL_RESEARCH_PRODUCTION_PROVIDER_SET_CONTRACT_ID
    )

    def _delivery(
        self,
        context: LeaderResearchRuntimeSourceContext,
        component_name: str,
        collector: Optional[ProductionCollector],
    ) -> LeaderFormalResearchProductionSourceDelivery:
        return _provider_delivery(
            context,
            component_name=component_name,
            collector=collector,
        )

    def history_provider(
        self,
        context: LeaderResearchRuntimeSourceContext,
    ) -> LeaderFormalResearchProductionSourceDelivery:
        return self._delivery(context, "history", self.history_collector)

    def business_catalyst_provider(
        self,
        context: LeaderResearchRuntimeSourceContext,
    ) -> LeaderFormalResearchProductionSourceDelivery:
        return self._delivery(
            context,
            "business_catalyst",
            self.business_catalyst_collector,
        )

    def tradability_provider(
        self,
        context: LeaderResearchRuntimeSourceContext,
    ) -> LeaderFormalResearchProductionSourceDelivery:
        return self._delivery(
            context,
            "tradability",
            self.tradability_collector,
        )

    def sector_rule_provider(
        self,
        context: LeaderResearchRuntimeSourceContext,
    ) -> LeaderFormalResearchProductionSourceDelivery:
        return self._delivery(
            context,
            "sector_rule",
            self.sector_rule_collector,
        )

    def to_runtime_provider_kwargs(self) -> Mapping[str, Callable]:
        return {
            "leader_history_input_provider": self.history_provider,
            "leader_business_catalyst_input_provider": (
                self.business_catalyst_provider
            ),
            "leader_tradability_input_provider": self.tradability_provider,
            "leader_sector_rule_input_provider": self.sector_rule_provider,
        }

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "configuredComponents": [
                name
                for name, collector in (
                    ("history", self.history_collector),
                    (
                        "business_catalyst",
                        self.business_catalyst_collector,
                    ),
                    ("tradability", self.tradability_collector),
                    ("sector_rule", self.sector_rule_collector),
                )
                if collector is not None
            ],
        }


def build_leader_formal_research_production_provider_set(
    *,
    history_collector: Optional[ProductionCollector] = None,
    business_catalyst_collector: Optional[ProductionCollector] = None,
    tradability_collector: Optional[ProductionCollector] = None,
    sector_rule_collector: Optional[ProductionCollector] = None,
) -> LeaderFormalResearchProductionProviderSet:
    return LeaderFormalResearchProductionProviderSet(
        history_collector=history_collector,
        business_catalyst_collector=business_catalyst_collector,
        tradability_collector=tradability_collector,
        sector_rule_collector=sector_rule_collector,
    )


RISK_REVIEW_SOURCE_CONTRACT_ID = (
    "radar-leader-d8-reviewed-risk-chain-v1"
)
RISK_REVIEW_REPOSITORY_UNAVAILABLE = (
    "leader_formal_research_bridge_repository_unavailable"
)
RISK_REVIEW_CHAIN_UNVERIFIED = (
    "leader_formal_research_bridge_chain_unverified"
)
RISK_REVIEW_VERSIONS_MISSING = (
    "leader_formal_research_bridge_review_versions_missing"
)


def _max_aware_datetimes(values: Sequence[Any]) -> Optional[datetime]:
    normalized = tuple(
        value.astimezone(UTC)
        for value in values
        if isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )
    return max(normalized) if normalized else None


def build_leader_formal_research_risk_source_proof(
    context: LeaderResearchRuntimeSourceContext,
    *,
    bridge: Any,
) -> LeaderFormalResearchProductionSourceProof:
    """从只读 D8 版本链生成风险来源证明，不暴露审核原文。"""

    candidate_count = context.candidate_plan.candidate_count
    chains = getattr(bridge, "review_chains", ())
    bridge_reasons = tuple(getattr(bridge, "reasons", ()))
    ready_count = getattr(bridge, "ready_count", 0)
    if not isinstance(ready_count, int) or isinstance(ready_count, bool):
        ready_count = 0

    status = LeaderFormalResearchProductionSourceStatus.NOT_RUN
    source_time = None
    fetched_at = None
    if RISK_REVIEW_REPOSITORY_UNAVAILABLE in bridge_reasons:
        status = LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED
    elif chains:
        symbols = tuple(item.symbol for item in context.candidate_plan.items)
        chain_by_symbol = {}
        valid_chain = isinstance(chains, tuple)
        if valid_chain:
            for chain in chains:
                symbol = getattr(chain, "symbol", None)
                versions = getattr(chain, "versions", ())
                if (
                    symbol not in symbols
                    or symbol in chain_by_symbol
                    or not isinstance(versions, tuple)
                    or not versions
                ):
                    valid_chain = False
                    break
                chain_by_symbol[symbol] = chain
        if (
            not valid_chain
            or set(chain_by_symbol) != set(symbols)
            or ready_count != candidate_count
            or RISK_REVIEW_CHAIN_UNVERIFIED in bridge_reasons
        ):
            status = LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        else:
            all_published = []
            all_fetched = []
            for chain in chain_by_symbol.values():
                for version in chain.versions:
                    published_at = getattr(
                        getattr(version, "document", None),
                        "published_at",
                        None,
                    )
                    fetched_at = getattr(
                        getattr(version, "content", None),
                        "fetched_at",
                        None,
                    )
                    published = _max_aware_datetimes((published_at,))
                    fetched = _max_aware_datetimes((fetched_at,))
                    if published is None or fetched is None:
                        valid_chain = False
                        break
                    if published > context.as_of or fetched < published:
                        valid_chain = False
                        break
                    all_published.append(published)
                    all_fetched.append(fetched)
                if not valid_chain:
                    break
            status = (
                LeaderFormalResearchProductionSourceStatus.COMPLETED
                if valid_chain
                else LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
            )
            if valid_chain:
                source_time = _max_aware_datetimes(all_published)
                fetched_at = _max_aware_datetimes(all_fetched)
    else:
        if RISK_REVIEW_CHAIN_UNVERIFIED in bridge_reasons:
            status = LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED
        elif RISK_REVIEW_VERSIONS_MISSING not in bridge_reasons:
            status = LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED

    returned_count = min(max(ready_count, 0), candidate_count)
    return LeaderFormalResearchProductionSourceProof(
        component_name="risk",
        source_contract_id=RISK_REVIEW_SOURCE_CONTRACT_ID,
        status=status,
        radar_run_id=context.radar_run_id,
        candidate_plan_id=context.candidate_plan.candidate_set_id,
        quote_batch_id=context.quote_batch_id,
        as_of=context.as_of,
        source_time=source_time,
        fetched_at=fetched_at,
        expected_count=candidate_count,
        returned_count=returned_count,
    )
