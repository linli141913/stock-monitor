"""按产品模块独立累计的正式启用影子观察台账。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, Literal, Mapping, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from radar.formal_readiness_contracts import (
    FORMAL_MODULE_REQUIRED_TRADING_DAYS,
    FormalModuleName,
)
from radar.formal_shadow_calendar import (
    FormalShadowCalendarEvidence,
    OfficialSseCalendarProvider,
)


FormalObservationStatus = Literal[
    "pending",
    "ready",
    "missing",
    "failed",
    "stale",
    "non_trading_day",
]
FormalLockState = Literal["acquired", "contended", "failed"]
REQUIRED_TRADING_DAYS: Mapping[FormalModuleName, int] = (
    FORMAL_MODULE_REQUIRED_TRADING_DAYS
)
_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))


class _FrozenDict(dict):
    """可被Pydantic按普通dict序列化的浅层不可变映射。"""

    def __init__(self, *args, **kwargs) -> None:
        dict.__init__(self, *args, **kwargs)

    def _immutable(self, *args, **kwargs) -> None:
        raise TypeError("formal_shadow_mapping_immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class TradingCalendarProvider(Protocol):
    def is_trading_day(self, value: date) -> Optional[bool]:
        """返回A股交易日真值；未知日必须返回None。"""


class _LedgerModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class FormalShadowObservation(_LedgerModel):
    module: FormalModuleName
    run_id: str = Field(alias="runId", min_length=1)
    observed_at: datetime = Field(alias="observedAt")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    coverage: float = Field(ge=0, le=1)
    missing_count: int = Field(alias="missingCount", ge=0)
    failed_count: int = Field(alias="failedCount", ge=0)
    stale_count: int = Field(alias="staleCount", ge=0)
    lock_state: FormalLockState = Field(alias="lockState")
    duration_ms: int = Field(alias="durationMs", ge=0)
    source_ready: Optional[bool] = Field(default=None, alias="sourceReady")
    evidence_sha256: str = Field(alias="evidenceSha256", pattern=r"^[0-9a-f]{64}$")
    observation_status: FormalObservationStatus = Field(
        default="pending",
        alias="observationStatus",
    )

    @field_validator("observed_at", "source_time", "fetched_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("formal_shadow_time_timezone_required")
        return value

    @model_validator(mode="after")
    def validate_temporal_order(self) -> "FormalShadowObservation":
        if self.source_time is not None and self.source_time > self.fetched_at:
            raise ValueError("sourceTime_after_fetchedAt")
        if self.fetched_at > self.observed_at:
            raise ValueError("fetchedAt_after_observedAt")
        if self.observation_status not in {"pending", "non_trading_day"}:
            expected_status: FormalObservationStatus
            if self.lock_state != "acquired" or self.failed_count:
                expected_status = "failed"
            elif self.missing_count or self.coverage == 0:
                expected_status = "missing"
            elif self.stale_count:
                expected_status = "stale"
            elif self.source_ready is False:
                expected_status = "failed"
            else:
                expected_status = "ready"
            if self.observation_status != expected_status:
                raise ValueError("observation_status_fields_conflict")
        return self

    @property
    def observed_date(self) -> date:
        return self.observed_at.astimezone(_SHANGHAI_TIMEZONE).date()


class FormalShadowLedger(_LedgerModel):
    """仅供旧读取方兼容的 v1 台账；不能证明 ETF 连续观察。"""

    contract_version: Literal["radar-formal-shadow-ledger-v1"] = Field(
        default="radar-formal-shadow-ledger-v1",
        alias="contractVersion",
    )
    observations: Tuple[FormalShadowObservation, ...] = Field(default_factory=tuple)
    ready_trading_days_by_module: Mapping[FormalModuleName, int] = Field(
        alias="readyTradingDaysByModule",
    )
    required_trading_days_by_module: Mapping[FormalModuleName, int] = Field(
        default_factory=lambda: dict(REQUIRED_TRADING_DAYS),
        alias="requiredTradingDaysByModule",
    )

    @field_validator("ready_trading_days_by_module")
    @classmethod
    def validate_ready_counts(
        cls,
        value: Dict[FormalModuleName, int],
    ) -> Mapping[FormalModuleName, int]:
        if set(value) != set(REQUIRED_TRADING_DAYS):
            raise ValueError("ready_trading_days_modules_incomplete")
        if any(count < 0 for count in value.values()):
            raise ValueError("ready_trading_days_negative")
        return _FrozenDict(value)

    @field_validator("required_trading_days_by_module")
    @classmethod
    def validate_required_counts(
        cls,
        value: Dict[FormalModuleName, int],
    ) -> Mapping[FormalModuleName, int]:
        if value != REQUIRED_TRADING_DAYS:
            raise ValueError("required_trading_days_contract_conflict")
        return _FrozenDict(value)

    @model_validator(mode="after")
    def validate_observation_summary(self) -> "FormalShadowLedger":
        ready_counts: Dict[FormalModuleName, int] = {
            module: 0 for module in REQUIRED_TRADING_DAYS
        }
        identities = set()
        for observation in self.observations:
            observation = FormalShadowObservation.model_validate(
                observation.model_dump(mode="python", by_alias=True),
            )
            identity = (observation.module, observation.observed_date)
            if identity in identities:
                raise ValueError("duplicate_shadow_observation")
            identities.add(identity)
            if observation.observation_status == "ready":
                ready_counts[observation.module] += 1
        if dict(self.ready_trading_days_by_module) != ready_counts:
            raise ValueError("ready_trading_days_observations_conflict")
        return self


class FormalShadowLedgerV2(_LedgerModel):
    """由完整观察全集和交易日历派生的正式影子台账。"""

    contract_version: Literal["radar-formal-shadow-ledger-v2"] = Field(
        default="radar-formal-shadow-ledger-v2",
        alias="contractVersion",
    )
    observations: Tuple[FormalShadowObservation, ...] = Field(default_factory=tuple)
    ready_trading_days_by_module: Mapping[FormalModuleName, int] = Field(
        alias="readyTradingDaysByModule",
    )
    latest_ready_trading_date_by_module: Mapping[
        FormalModuleName,
        Optional[date],
    ] = Field(alias="latestReadyTradingDateByModule")
    latest_ready_streak_by_module: Mapping[FormalModuleName, int] = Field(
        alias="latestReadyStreakByModule",
    )
    observed_trading_date_range_by_module: Mapping[
        FormalModuleName,
        Tuple[Optional[date], Optional[date]],
    ] = Field(alias="observedTradingDateRangeByModule")
    verified_trading_dates_by_module: Mapping[
        FormalModuleName,
        Tuple[date, ...],
    ] = Field(alias="verifiedTradingDatesByModule")
    calendar_evidence: Tuple[FormalShadowCalendarEvidence, ...] = Field(
        default_factory=tuple,
        alias="calendarEvidence",
    )
    calendar_observed_through: Optional[date] = Field(
        default=None,
        alias="calendarObservedThrough",
    )
    required_trading_days_by_module: Mapping[FormalModuleName, int] = Field(
        default_factory=lambda: dict(REQUIRED_TRADING_DAYS),
        alias="requiredTradingDaysByModule",
    )

    @field_validator(
        "ready_trading_days_by_module",
        "latest_ready_streak_by_module",
    )
    @classmethod
    def validate_nonnegative_module_counts(
        cls,
        value: Dict[FormalModuleName, int],
    ) -> Mapping[FormalModuleName, int]:
        if set(value) != set(REQUIRED_TRADING_DAYS):
            raise ValueError("shadow_ledger_v2_modules_incomplete")
        if any(count < 0 for count in value.values()):
            raise ValueError("shadow_ledger_v2_counts_negative")
        return _FrozenDict(value)

    @field_validator("latest_ready_trading_date_by_module")
    @classmethod
    def validate_latest_ready_dates(
        cls,
        value: Dict[FormalModuleName, Optional[date]],
    ) -> Mapping[FormalModuleName, Optional[date]]:
        if set(value) != set(REQUIRED_TRADING_DAYS):
            raise ValueError("shadow_ledger_v2_modules_incomplete")
        return _FrozenDict(value)

    @field_validator("observed_trading_date_range_by_module")
    @classmethod
    def validate_observed_date_ranges(
        cls,
        value: Dict[FormalModuleName, Tuple[Optional[date], Optional[date]]],
    ) -> Mapping[FormalModuleName, Tuple[Optional[date], Optional[date]]]:
        if set(value) != set(REQUIRED_TRADING_DAYS):
            raise ValueError("shadow_ledger_v2_modules_incomplete")
        for start, end in value.values():
            if (start is None) != (end is None) or (
                start is not None and start > end
            ):
                raise ValueError("observed_trading_date_range_invalid")
        return _FrozenDict(value)

    @field_validator("verified_trading_dates_by_module")
    @classmethod
    def validate_verified_trading_dates(
        cls,
        value: Dict[FormalModuleName, Tuple[date, ...]],
    ) -> Mapping[FormalModuleName, Tuple[date, ...]]:
        if set(value) != set(REQUIRED_TRADING_DAYS):
            raise ValueError("shadow_ledger_v2_modules_incomplete")
        for dates in value.values():
            if tuple(sorted(set(dates))) != tuple(dates):
                raise ValueError("verified_trading_dates_invalid")
        return _FrozenDict(value)

    @field_validator("required_trading_days_by_module")
    @classmethod
    def validate_v2_required_counts(
        cls,
        value: Dict[FormalModuleName, int],
    ) -> Mapping[FormalModuleName, int]:
        if value != REQUIRED_TRADING_DAYS:
            raise ValueError("required_trading_days_contract_conflict")
        return _FrozenDict(value)

    @model_validator(mode="after")
    def validate_derived_observation_summary(self) -> "FormalShadowLedgerV2":
        if tuple(sorted(self.calendar_evidence, key=lambda item: item.year)) != self.calendar_evidence:
            raise ValueError("formal_shadow_calendar_evidence_order_invalid")
        if len({item.year for item in self.calendar_evidence}) != len(self.calendar_evidence):
            raise ValueError("formal_shadow_calendar_evidence_year_duplicate")
        expected_calendar_cutoff = (
            max(item.observed_through for item in self.calendar_evidence)
            if self.calendar_evidence else None
        )
        if self.calendar_observed_through != expected_calendar_cutoff:
            raise ValueError("formal_shadow_calendar_cutoff_conflict")
        embedded_calendar = (
            OfficialSseCalendarProvider(self.calendar_evidence)
            if self.calendar_evidence else None
        )
        ready_counts: Dict[FormalModuleName, int] = {
            module: 0 for module in REQUIRED_TRADING_DAYS
        }
        latest_dates: Dict[FormalModuleName, Optional[date]] = {
            module: None for module in REQUIRED_TRADING_DAYS
        }
        observed_ranges: Dict[
            FormalModuleName,
            Tuple[Optional[date], Optional[date]],
        ] = {module: (None, None) for module in REQUIRED_TRADING_DAYS}
        observations_by_module_date = {}
        identities = set()
        for observation in self.observations:
            observation = FormalShadowObservation.model_validate(
                observation.model_dump(mode="python", by_alias=True),
            )
            identity = (observation.module, observation.observed_date)
            if identity in identities:
                raise ValueError("duplicate_shadow_observation")
            identities.add(identity)
            observations_by_module_date[identity] = observation
            if embedded_calendar is not None:
                embedded_result = embedded_calendar.is_trading_day(
                    observation.observed_date,
                )
                if embedded_result is None:
                    raise ValueError("formal_shadow_calendar_observation_uncovered")
                if (
                    observation.observation_status == "non_trading_day"
                ) == embedded_result:
                    raise ValueError("formal_shadow_calendar_observation_conflict")
            if observation.observation_status != "non_trading_day":
                start, end = observed_ranges[observation.module]
                observed_ranges[observation.module] = (
                    observation.observed_date
                    if start is None or observation.observed_date < start else start,
                    observation.observed_date
                    if end is None or observation.observed_date > end else end,
                )
            if observation.observation_status == "ready":
                ready_counts[observation.module] += 1
                current = latest_dates[observation.module]
                if current is None or observation.observed_date > current:
                    latest_dates[observation.module] = observation.observed_date
        if dict(self.ready_trading_days_by_module) != ready_counts:
            raise ValueError("ready_trading_days_observations_conflict")
        if dict(self.latest_ready_trading_date_by_module) != latest_dates:
            raise ValueError("latest_ready_trading_dates_observations_conflict")
        if dict(self.observed_trading_date_range_by_module) != observed_ranges:
            raise ValueError("observed_trading_date_ranges_observations_conflict")
        expected_streaks: Dict[FormalModuleName, int] = {
            module: 0 for module in REQUIRED_TRADING_DAYS
        }
        for module in REQUIRED_TRADING_DAYS:
            verified_dates = self.verified_trading_dates_by_module[module]
            start, end = observed_ranges[module]
            if start is None:
                if verified_dates:
                    raise ValueError("verified_trading_dates_observations_conflict")
                continue
            if not verified_dates or verified_dates[0] != start or verified_dates[-1] != end:
                raise ValueError("verified_trading_dates_observations_conflict")
            if embedded_calendar is not None:
                cursor = start
                embedded_dates = []
                while cursor <= end:
                    result = embedded_calendar.is_trading_day(cursor)
                    if result is None:
                        raise ValueError("formal_shadow_calendar_observation_uncovered")
                    if result:
                        embedded_dates.append(cursor)
                    cursor += timedelta(days=1)
                if tuple(embedded_dates) != verified_dates:
                    raise ValueError("formal_shadow_calendar_verified_dates_conflict")
            for observation in (
                item for item in self.observations if item.module == module
            ):
                is_verified_trading_date = observation.observed_date in verified_dates
                if (observation.observation_status == "non_trading_day") == is_verified_trading_date:
                    raise ValueError("verified_trading_dates_observations_conflict")
            latest_observed = end
            latest_observation = observations_by_module_date[(module, latest_observed)]
            if latest_observation.observation_status != "ready":
                continue
            for value in reversed(verified_dates):
                observation = observations_by_module_date.get((module, value))
                if observation is None or observation.observation_status != "ready":
                    break
                expected_streaks[module] += 1
        if dict(self.latest_ready_streak_by_module) != expected_streaks:
            raise ValueError("latest_ready_streak_observations_conflict")
        return self


def _observation_status(
    observation: FormalShadowObservation,
    is_trading_day: bool,
) -> FormalObservationStatus:
    if not is_trading_day:
        return "non_trading_day"
    if observation.lock_state != "acquired":
        return "failed"
    if observation.failed_count:
        return "failed"
    if observation.missing_count or observation.coverage == 0:
        return "missing"
    if observation.stale_count:
        return "stale"
    if observation.source_ready is False:
        return "failed"
    return "ready"


def _calendar_result(
    calendar_provider: TradingCalendarProvider,
    value: date,
) -> bool:
    method = getattr(calendar_provider, "is_trading_day", None)
    if not callable(method):
        raise ValueError("trading_calendar_provider_invalid")
    result = method(value)
    if result is None:
        raise ValueError("trading_day_unknown")
    if type(result) is not bool:
        raise ValueError("trading_day_invalid")
    return result


def build_shadow_ledger(
    observations: Iterable[FormalShadowObservation | dict],
    *,
    calendar_provider: TradingCalendarProvider,
    calendar_evidence: Iterable[FormalShadowCalendarEvidence] = (),
) -> FormalShadowLedgerV2:
    """验证日历并按模块/日期保留首条不可变观察记录，生成 v2 派生值。"""

    retained = []
    bound_calendar_evidence = tuple(calendar_evidence)
    if bound_calendar_evidence:
        official_provider = OfficialSseCalendarProvider(bound_calendar_evidence)
        for item in bound_calendar_evidence:
            if item not in official_provider.evidence:
                raise ValueError("formal_shadow_calendar_evidence_invalid")
    seen: Dict[tuple[FormalModuleName, date], FormalShadowObservation] = {}
    ready_counts: Dict[FormalModuleName, int] = {
        module: 0 for module in REQUIRED_TRADING_DAYS
    }
    for raw_observation in observations:
        if isinstance(raw_observation, FormalShadowObservation):
            raw_observation = raw_observation.model_dump(
                mode="python",
                by_alias=True,
            )
        observation = FormalShadowObservation.model_validate(raw_observation)
        identity = (observation.module, observation.observed_date)
        is_trading_day = _calendar_result(
            calendar_provider,
            observation.observed_date,
        )
        status = _observation_status(observation, is_trading_day)
        completed_payload = observation.model_dump(mode="python", by_alias=True)
        completed_payload["observationStatus"] = status
        completed = FormalShadowObservation.model_validate(
            completed_payload,
        )
        prior = seen.get(identity)
        if prior is not None:
            if prior.model_dump(mode="json", by_alias=True) != completed.model_dump(
                mode="json",
                by_alias=True,
            ):
                raise ValueError("shadow_observation_identity_conflict")
            continue
        seen[identity] = completed
        retained.append(completed)
        if status == "ready":
            ready_counts[observation.module] += 1
    latest_ready_dates: Dict[FormalModuleName, Optional[date]] = {
        module: None for module in REQUIRED_TRADING_DAYS
    }
    latest_ready_streaks: Dict[FormalModuleName, int] = {
        module: 0 for module in REQUIRED_TRADING_DAYS
    }
    observed_ranges: Dict[
        FormalModuleName,
        Tuple[Optional[date], Optional[date]],
    ] = {module: (None, None) for module in REQUIRED_TRADING_DAYS}
    verified_trading_dates: Dict[FormalModuleName, Tuple[date, ...]] = {
        module: () for module in REQUIRED_TRADING_DAYS
    }
    observations_by_module_date = {
        (item.module, item.observed_date): item for item in retained
    }
    for module in REQUIRED_TRADING_DAYS:
        module_observations = tuple(
            item for item in retained
            if item.module == module and item.observation_status != "non_trading_day"
        )
        if not module_observations:
            continue
        earliest = min(item.observed_date for item in module_observations)
        latest_observed = max(item.observed_date for item in module_observations)
        observed_ranges[module] = (earliest, latest_observed)
        verified_dates = []
        cursor = earliest
        while cursor <= latest_observed:
            if _calendar_result(calendar_provider, cursor):
                verified_dates.append(cursor)
            cursor += timedelta(days=1)
        verified_trading_dates[module] = tuple(verified_dates)
        ready_dates = tuple(
            item.observed_date for item in module_observations
            if item.observation_status == "ready"
        )
        if ready_dates:
            latest_ready_dates[module] = max(ready_dates)
        latest_observation = observations_by_module_date[(module, latest_observed)]
        if latest_observation.observation_status != "ready":
            continue
        for value in reversed(verified_trading_dates[module]):
            item = observations_by_module_date.get((module, value))
            if item is None or item.observation_status != "ready":
                break
            latest_ready_streaks[module] += 1
    return FormalShadowLedgerV2(
        observations=tuple(retained),
        readyTradingDaysByModule=ready_counts,
        latestReadyTradingDateByModule=latest_ready_dates,
        latestReadyStreakByModule=latest_ready_streaks,
        observedTradingDateRangeByModule=observed_ranges,
        verifiedTradingDatesByModule=verified_trading_dates,
        calendarEvidence=bound_calendar_evidence,
        calendarObservedThrough=(
            max(item.observed_through for item in bound_calendar_evidence)
            if bound_calendar_evidence else None
        ),
    )
