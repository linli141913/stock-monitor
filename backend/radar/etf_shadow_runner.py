"""阶段5 ETF候选影子执行器。

执行器只消费现有ETF行情任务已经取得并通过健康判定的不可变批次，
不会创建数据库连接、注册调度任务或再次请求行情来源。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from radar.config import RadarSettings
from radar.contracts import (
    QuoteSnapshot,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.etf_repository import EtfRepository
from radar.etf_stage5_policy import DEFAULT_ETF_RULE_POLICY
from radar.repository import RadarRepositoryError
from radar.run_lock import CrossProcessFileLock


UTC = timezone.utc
PathLike = Union[str, Path]
QUOTE_SOURCE = "tencent_finance"


class EtfStage5ShadowError(RuntimeError):
    """阶段5 ETF影子执行器基础错误。"""


class EtfStage5ShadowDisabledError(EtfStage5ShadowError):
    """阶段5 ETF影子开关未开启。"""


class EtfStage5ShadowInProgressError(EtfStage5ShadowError):
    """另一个进程正在处理阶段5 ETF影子批次。"""


class EtfStage5ShadowExecutionError(EtfStage5ShadowError):
    """输入批次或仓储写入不满足阶段5合同。"""


@dataclass(frozen=True)
class EtfStage5ShadowRunResult:
    radar_run_id: str
    as_of: datetime
    status: str
    gate_passed: bool
    gate_reasons: Tuple[str, ...]
    quote_status: SourceStatus
    quote_count: int
    persisted_feature_count: int
    candidate_group_count: int

    @property
    def item_count(self) -> int:
        return self.persisted_feature_count


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _field_state(value, *, verified: bool = True) -> str:
    if value is None:
        return "missing"
    return "verified" if verified else "source_unverified"


class EtfStage5ShadowRunner:
    """把同轮ETF行情转换为阶段5特征和真实空候选快照。"""

    def __init__(
        self,
        repository: EtfRepository,
        *,
        settings: RadarSettings,
        lock_path: PathLike,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._repository = repository
        self._settings = settings
        self._lock_path = Path(lock_path)
        self._clock = clock

    def run_once(
        self,
        radar_run_id: str,
        as_of: datetime,
        quote_batch: SourceBatch[QuoteSnapshot],
        quote_health: SourceHealthResult,
    ) -> EtfStage5ShadowRunResult:
        if (
            not self._settings.enabled
            or not self._settings.shadow_mode
            or not self._settings.etf_stage5_enabled
        ):
            raise EtfStage5ShadowDisabledError(
                "RADAR_ENABLED、RADAR_SHADOW_MODE和"
                "RADAR_ETF_STAGE5_ENABLED必须同时开启"
            )
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        as_of = _aware_utc(as_of, "as_of")
        started_at = _aware_utc(self._clock(), "started_at")
        run_lag_seconds = (started_at - as_of).total_seconds()
        if run_lag_seconds < 0:
            raise ValueError("as_of不能晚于阶段5执行时间")
        if run_lag_seconds > self._settings.maximum_quote_age_seconds:
            raise ValueError("as_of已超过阶段5实时门禁，不允许补写历史轮次")
        self._validate_batch(radar_run_id, as_of, quote_batch)

        lock = CrossProcessFileLock(self._lock_path)
        if not lock.acquire(blocking=False):
            raise EtfStage5ShadowInProgressError(
                "当前已有阶段5 ETF影子批次运行"
            )
        try:
            return self._persist_batch(
                radar_run_id,
                as_of,
                quote_batch,
                quote_health,
            )
        finally:
            lock.release()

    def _persist_batch(
        self,
        radar_run_id: str,
        as_of: datetime,
        quote_batch: SourceBatch[QuoteSnapshot],
        quote_health: SourceHealthResult,
    ) -> EtfStage5ShadowRunResult:
        try:
            if quote_health.allows_new_state:
                feature_snapshots = [
                    self._feature_snapshot(as_of, quote)
                    for quote in quote_batch.items
                ]
                persisted_feature_count = (
                    self._repository.insert_feature_snapshot_batch(
                        radar_run_id,
                        feature_snapshots,
                    )
                )
            else:
                persisted_feature_count = 0

            gate_reasons = self._gate_reasons(quote_health, quote_batch)
            candidate_snapshot = self._candidate_snapshot(
                radar_run_id,
                as_of,
                quote_batch,
                quote_health,
                gate_reasons,
            )
            self._repository.save_candidate_snapshot(
                candidate_snapshot,
                (),
            )
        except RadarRepositoryError as exc:
            raise EtfStage5ShadowExecutionError(
                f"阶段5 ETF影子仓储写入失败：{type(exc).__name__}"
            ) from exc

        status = (
            quote_health.status.value
            if not quote_health.allows_new_state
            else "not_ready"
        )
        return EtfStage5ShadowRunResult(
            radar_run_id=radar_run_id,
            as_of=as_of,
            status=status,
            gate_passed=False,
            gate_reasons=gate_reasons,
            quote_status=quote_health.status,
            quote_count=len(quote_batch.items),
            persisted_feature_count=persisted_feature_count,
            candidate_group_count=0,
        )

    @staticmethod
    def _validate_batch(
        radar_run_id: str,
        as_of: datetime,
        quote_batch: SourceBatch[QuoteSnapshot],
    ) -> None:
        meta = quote_batch.meta
        if (
            meta.radar_run_id != radar_run_id
            or _aware_utc(meta.as_of, "行情批次as_of") != as_of
            or meta.source != QUOTE_SOURCE
        ):
            raise EtfStage5ShadowExecutionError(
                "阶段5 ETF行情批次身份、来源或asOf不一致"
            )
        if meta.returned_count != len(quote_batch.items):
            raise EtfStage5ShadowExecutionError(
                "阶段5 ETF行情returnedCount与明细数量不一致"
            )
        symbols = [item.symbol for item in quote_batch.items]
        if len(symbols) != len(set(symbols)):
            raise EtfStage5ShadowExecutionError(
                "阶段5 ETF行情批次包含重复代码"
            )

    @staticmethod
    def _feature_snapshot(
        as_of: datetime,
        quote: QuoteSnapshot,
    ) -> dict:
        missing_fields = quote.missing_fields()
        turnover_verified = (
            quote.turnover_amount_unit_status
            == UnitVerificationStatus.VERIFIED
            and quote.turnover_amount_cny is not None
        )
        return {
            "symbol": quote.symbol,
            "asOf": as_of,
            "sourceTime": quote.source_time,
            "fetchedAt": quote.fetched_at,
            "price": quote.price,
            "changePercent": quote.change_percent,
            "turnoverVolume": None,
            "turnoverAmount": (
                quote.turnover_amount_cny
                if turnover_verified
                else quote.turnover_amount_source
            ),
            "bid1": None,
            "ask1": None,
            "spreadBps": None,
            "iopv": None,
            "premiumDiscountRate": None,
            "fieldStates": {
                "price": _field_state(quote.price),
                "changePercent": _field_state(quote.change_percent),
                "turnoverAmount": _field_state(
                    (
                        quote.turnover_amount_cny
                        if turnover_verified
                        else quote.turnover_amount_source
                    ),
                    verified=turnover_verified,
                ),
                "bid1": "source_unverified",
                "ask1": "source_unverified",
                "spreadBps": "source_unverified",
                "iopv": "source_unverified",
                "premiumDiscountRate": "source_unverified",
            },
            "formalUsable": False,
            "reasonCodes": list(_dedupe([
                *(
                    f"quote_field_missing:{field_name}"
                    for field_name in missing_fields
                ),
                *(
                    ()
                    if turnover_verified
                    else ("turnover_amount_unit_unverified",)
                ),
                "etf_product_evidence_not_ready",
                "etf_rule_not_frozen",
                *DEFAULT_ETF_RULE_POLICY.disabled_reasons,
            ])),
        }

    @staticmethod
    def _gate_reasons(
        quote_health: SourceHealthResult,
        quote_batch: SourceBatch[QuoteSnapshot],
    ) -> Tuple[str, ...]:
        if not quote_health.allows_new_state:
            return _dedupe([
                f"quote_source_{quote_health.status.value}",
                *quote_health.reasons,
            ])
        reasons = [
            "etf_product_evidence_not_ready",
            "etf_rule_not_frozen",
            *DEFAULT_ETF_RULE_POLICY.disabled_reasons,
        ]
        if not quote_batch.items:
            reasons.append("etf_quote_batch_empty")
        return _dedupe(reasons)

    @staticmethod
    def _candidate_snapshot(
        radar_run_id: str,
        as_of: datetime,
        quote_batch: SourceBatch[QuoteSnapshot],
        quote_health: SourceHealthResult,
        gate_reasons: Tuple[str, ...],
    ) -> dict:
        meta = quote_batch.meta
        expected_count = meta.expected_count or 0
        returned_count = meta.returned_count
        missing_count = max(expected_count - returned_count, 0)
        if quote_health.allows_new_state:
            missing_count += returned_count
        stale_count = (
            returned_count
            if quote_health.status == SourceStatus.STALE
            else 0
        )
        reason_counts = Counter(gate_reasons)
        return {
            "radarRunId": radar_run_id,
            "asOf": as_of,
            "ruleVersionId": None,
            "registryCount": expected_count,
            "etfCount": returned_count,
            "eligibleProductCount": 0,
            "industryThemeCount": 0,
            "computedCount": 0,
            "staleCount": stale_count,
            "missingCount": missing_count,
            "excludedCount": 0,
            "candidateGroupCount": 0,
            "coverage": 0.0,
            "quality": "unavailable",
            "reasonCounts": dict(reason_counts),
        }
