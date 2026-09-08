"""候选全集可交易性的生产只读冻结与交付。

调用方必须显式提供同轮证券身份、交易日历、行情以及官方和聚合观察。
本模块只校验全集、时间和现有复合规则的可重放性；不联网、不读写
数据库，也不用单一聚合源补位官方状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Tuple

from radar.contracts import QuoteSnapshot
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.leader_research_source_admission import (
    LeaderResearchTradabilityAdmissionBundle,
)
from radar.leader_tradability_runtime_bridge import (
    LeaderTradabilityRuntimeBridgeStatus,
    build_leader_tradability_runtime_bridge,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositeTradabilityQuery,
    PublicSecurityContext,
    PublicSourceKind,
    PublicTradabilityObservation,
    PublicTradingCalendarEvidence,
)


LEADER_TRADABILITY_PRODUCTION_FROZEN_BATCH_CONTRACT_ID = (
    "radar-leader-tradability-production-frozen-batch-v1"
)
LEADER_TRADABILITY_PRODUCTION_SOURCE_CONTRACT_ID = (
    "radar-leader-tradability-production-source-v1"
)
MAXIMUM_FUTURE_SKEW_SECONDS = 5
MAXIMUM_EXCHANGE_SOURCE_CLOCK_SKEW_SECONDS = 10


@dataclass(frozen=True, repr=False)
class LeaderTradabilityProductionFrozenBatch:
    source_bundle: Any = field(repr=False)
    fetched_at: Optional[datetime]
    source_status: LeaderFormalResearchProductionSourceStatus
    contract_id: str = (
        LEADER_TRADABILITY_PRODUCTION_FROZEN_BATCH_CONTRACT_ID
    )

    def to_evidence(self) -> Mapping[str, object]:
        query = getattr(self.source_bundle, "query", None)
        securities = getattr(query, "securities", ())
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
            "expectedCount": (
                len(securities) if isinstance(securities, tuple) else 0
            ),
        }


def _collected(
    status: LeaderFormalResearchProductionSourceStatus,
    *,
    source_time: Optional[datetime] = None,
    fetched_at: Optional[datetime] = None,
    symbols: Tuple[str, ...] = (),
    payload: Any = None,
    reasons: Tuple[str, ...] = (),
) -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name="tradability",
        source_contract_id=(
            LEADER_TRADABILITY_PRODUCTION_SOURCE_CONTRACT_ID
        ),
        status=status,
        source_time=source_time,
        fetched_at=fetched_at,
        symbols=symbols,
        payload=payload,
        reasons=reasons,
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _fetch_precedes_source(
    fetched_at: datetime,
    source_time: datetime,
    *,
    maximum_skew_seconds: int = MAXIMUM_FUTURE_SKEW_SECONDS,
) -> bool:
    return fetched_at + timedelta(
        seconds=maximum_skew_seconds
    ) < source_time


def _bundle_times(bundle: Any) -> Optional[Tuple[datetime, datetime]]:
    if (
        type(bundle) is not LeaderResearchTradabilityAdmissionBundle
        or type(bundle.query) is not PublicCompositeTradabilityQuery
        or not isinstance(bundle.query.securities, tuple)
        or not bundle.query.securities
        or not isinstance(bundle.query.trading_calendars, tuple)
        or not bundle.query.trading_calendars
        or not isinstance(bundle.quotes, tuple)
        or not bundle.quotes
        or not isinstance(bundle.official_observations, tuple)
        or not bundle.official_observations
        or not isinstance(bundle.aggregator_observations, tuple)
        or not bundle.aggregator_observations
    ):
        return None

    source_times = []
    fetched_times = []
    for security in bundle.query.securities:
        if (
            type(security) is not PublicSecurityContext
            or not _aware(security.identity_fetched_at)
            or (
                security.identity_source_time is not None
                and not _aware(security.identity_source_time)
            )
            or (
                security.identity_source_time is not None
                and _fetch_precedes_source(
                    security.identity_fetched_at,
                    security.identity_source_time,
                )
            )
        ):
            return None
        if security.identity_source_time is not None:
            source_times.append(security.identity_source_time)
        fetched_times.append(security.identity_fetched_at)
    for calendar in bundle.query.trading_calendars:
        if (
            type(calendar) is not PublicTradingCalendarEvidence
            or not _aware(calendar.source_time)
            or not _aware(calendar.fetched_at)
            or _fetch_precedes_source(
                calendar.fetched_at,
                calendar.source_time,
            )
        ):
            return None
        source_times.append(calendar.source_time)
        fetched_times.append(calendar.fetched_at)
    for quote in bundle.quotes:
        if (
            not isinstance(quote, QuoteSnapshot)
            or not _aware(quote.source_time)
            or not _aware(quote.fetched_at)
            or _fetch_precedes_source(quote.fetched_at, quote.source_time)
        ):
            return None
        source_times.append(quote.source_time)
        fetched_times.append(quote.fetched_at)
    for observation in (
        *bundle.official_observations,
        *bundle.aggregator_observations,
    ):
        if (
            type(observation) is not PublicTradabilityObservation
            or not _aware(observation.source_time)
            or not _aware(observation.fetched_at)
            or _fetch_precedes_source(
                observation.fetched_at,
                observation.source_time,
                maximum_skew_seconds=(
                    MAXIMUM_EXCHANGE_SOURCE_CLOCK_SKEW_SECONDS
                    if observation.source_kind
                    == PublicSourceKind.EXCHANGE_OFFICIAL
                    else MAXIMUM_FUTURE_SKEW_SECONDS
                ),
            )
        ):
            return None
        source_times.append(observation.source_time)
        fetched_times.append(observation.fetched_at)
        if observation.upstream_source_time is not None:
            if not _aware(observation.upstream_source_time):
                return None
            source_times.append(observation.upstream_source_time)
    return max(source_times), max(fetched_times)


def _bundle_time_reason(bundle: Any) -> str:
    if (
        type(bundle) is not LeaderResearchTradabilityAdmissionBundle
        or type(bundle.query) is not PublicCompositeTradabilityQuery
        or not isinstance(bundle.query.securities, tuple)
        or not bundle.query.securities
        or not isinstance(bundle.query.trading_calendars, tuple)
        or not bundle.query.trading_calendars
        or not isinstance(bundle.quotes, tuple)
        or not bundle.quotes
        or not isinstance(bundle.official_observations, tuple)
        or not bundle.official_observations
        or not isinstance(bundle.aggregator_observations, tuple)
        or not bundle.aggregator_observations
    ):
        return "tradability_bundle_shape_unverified"
    for security in bundle.query.securities:
        if (
            type(security) is not PublicSecurityContext
            or not _aware(security.identity_fetched_at)
            or (
                security.identity_source_time is not None
                and not _aware(security.identity_source_time)
            )
            or (
                security.identity_source_time is not None
                and _fetch_precedes_source(
                    security.identity_fetched_at,
                    security.identity_source_time,
                )
            )
        ):
            return "tradability_security_time_unverified"
    for calendar in bundle.query.trading_calendars:
        if (
            type(calendar) is not PublicTradingCalendarEvidence
            or not _aware(calendar.source_time)
            or not _aware(calendar.fetched_at)
            or _fetch_precedes_source(
                calendar.fetched_at,
                calendar.source_time,
            )
        ):
            return "tradability_calendar_time_unverified"
    for quote in bundle.quotes:
        if (
            not isinstance(quote, QuoteSnapshot)
            or not _aware(quote.source_time)
            or not _aware(quote.fetched_at)
            or _fetch_precedes_source(quote.fetched_at, quote.source_time)
        ):
            return "tradability_quote_time_unverified"
    for observation in (
        *bundle.official_observations,
        *bundle.aggregator_observations,
    ):
        if (
            type(observation) is not PublicTradabilityObservation
            or not _aware(observation.source_time)
            or not _aware(observation.fetched_at)
            or _fetch_precedes_source(
                observation.fetched_at,
                observation.source_time,
            )
        ):
            return "tradability_observation_time_unverified"
        if (
            observation.upstream_source_time is not None
            and not _aware(observation.upstream_source_time)
        ):
            return "tradability_upstream_time_unverified"
    return "tradability_bundle_times_unverified"


def collect_leader_tradability_production_source(
    context: Any,
    frozen: Any,
) -> LeaderFormalResearchProductionCollectedSource:
    """重放候选全集可交易性；任一缺口不交付部分载荷。"""

    if not is_leader_research_runtime_source_context_valid(context):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=("tradability_context_unverified",),
        )
    if (
        type(frozen) is not LeaderTradabilityProductionFrozenBatch
        or frozen.contract_id
        != LEADER_TRADABILITY_PRODUCTION_FROZEN_BATCH_CONTRACT_ID
        or type(frozen.source_status)
        is not LeaderFormalResearchProductionSourceStatus
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=("tradability_frozen_batch_unverified",),
        )
    if frozen.source_status != (
        LeaderFormalResearchProductionSourceStatus.COMPLETED
    ):
        if frozen.source_bundle is not None or frozen.fetched_at is not None:
            return _collected(
                LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
                reasons=("tradability_noncompleted_payload_present",),
            )
        return _collected(frozen.source_status)

    times = _bundle_times(frozen.source_bundle)
    if times is None or not _aware(frozen.fetched_at):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=(_bundle_time_reason(frozen.source_bundle),),
        )
    source_time, embedded_fetched_at = times
    if (
        frozen.fetched_at < embedded_fetched_at
        or _fetch_precedes_source(
            frozen.fetched_at,
            source_time,
            maximum_skew_seconds=(
                MAXIMUM_EXCHANGE_SOURCE_CLOCK_SKEW_SECONDS
            ),
        )
        or source_time > context.as_of + timedelta(
            seconds=MAXIMUM_EXCHANGE_SOURCE_CLOCK_SKEW_SECONDS
        )
        or frozen.fetched_at > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=("tradability_bundle_time_window_unverified",),
        )
    try:
        bridge = build_leader_tradability_runtime_bridge(
            context,
            tradability_bundle=frozen.source_bundle,
        )
    except Exception:
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=("tradability_runtime_bridge_failed",),
        )
    expected_symbols = tuple(
        item.symbol for item in context.candidate_plan.items
    )
    ready_symbols = tuple(
        item.symbol
        for item in bridge.items
        if item.status == LeaderTradabilityRuntimeBridgeStatus.READY
    )
    if (
        bridge.status != LeaderTradabilityRuntimeBridgeStatus.READY
        or ready_symbols != expected_symbols
        or bridge.ready_count != len(expected_symbols)
    ):
        return _collected(
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
            reasons=(
                *(bridge.reasons or ()),
                "tradability_runtime_bridge_not_ready",
            ),
        )
    return _collected(
        LeaderFormalResearchProductionSourceStatus.COMPLETED,
        source_time=source_time,
        fetched_at=frozen.fetched_at,
        symbols=expected_symbols,
        payload=frozen.source_bundle,
    )


def build_leader_tradability_production_loader(
    frozen: LeaderTradabilityProductionFrozenBatch,
):
    """为现有四源生产 provider 构造无网络只读 loader。"""

    if type(frozen) is not LeaderTradabilityProductionFrozenBatch:
        raise ValueError("leader_tradability_production_batch_unverified")

    def load(context: LeaderResearchRuntimeSourceContext):
        return collect_leader_tradability_production_source(
            context,
            frozen,
        )

    return load
