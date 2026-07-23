"""行业当期聚合的单轮影子执行器。

本模块不创建数据库连接、不读取环境变量、不注册调度任务，也不保存逐证券行情。
调用方必须显式提供仓储、冻结时点、运行标识和行情来源。
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryClassificationGap,
    IndustryClassificationSnapshot,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SectorFeatureBatch,
    SourceBatch,
    SourceHealthResult,
    SourceIssue,
    SourceStatus,
)
from radar.repository import (
    RadarRepository,
    RadarRepositoryError,
    RepositoryConflictError,
)
from radar.sector_features import build_sector_features
from radar.source_health import SourceHealthPolicy, evaluate_source_health
from radar.sources.tencent_quotes import fetch_tencent_quotes


UTC = timezone.utc


class SectorShadowRunError(RuntimeError):
    """行业影子执行器基础错误。"""


class SectorShadowRunInProgressError(SectorShadowRunError):
    """当前进程已有行业影子运行。"""


class SectorShadowRunAlreadyExistsError(SectorShadowRunError):
    """相同运行标识已经存在。"""


class SectorShadowRunExecutionError(SectorShadowRunError):
    """批次合同或仓储错误导致执行失败。"""


class _BatchValidationError(SectorShadowRunError):
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class _GateRejected(SectorShadowRunError):
    def __init__(self, *reasons: str):
        normalized = tuple(dict.fromkeys(reason for reason in reasons if reason))
        super().__init__("、".join(normalized))
        self.reasons = normalized


QuoteFetcher = Callable[
    [Sequence[str], str, str, datetime],
    SourceBatch[QuoteSnapshot],
]


@dataclass(frozen=True)
class SectorShadowPolicy:
    classification_system: str = "capco_listed_company_industry"
    release_period: str = "2025H2"
    minimum_quote_row_coverage: float = 0.995
    minimum_required_field_coverage: float = 0.99
    maximum_quote_age_seconds: int = 90
    maximum_future_skew_seconds: int = 5
    required_quote_fields: Tuple[str, ...] = (
        "price",
        "source_time",
        "change_percent",
        "turnover_amount_source",
        "market_cap_source",
    )

    def __post_init__(self):
        if not self.classification_system.strip():
            raise ValueError("classification_system不能为空")
        if not self.release_period.strip():
            raise ValueError("release_period不能为空")
        if not 0 <= self.minimum_quote_row_coverage <= 1:
            raise ValueError("minimum_quote_row_coverage必须在0到1之间")
        if not 0 <= self.minimum_required_field_coverage <= 1:
            raise ValueError("minimum_required_field_coverage必须在0到1之间")
        if self.maximum_quote_age_seconds <= 0:
            raise ValueError("maximum_quote_age_seconds必须大于0")
        if self.maximum_future_skew_seconds < 0:
            raise ValueError("maximum_future_skew_seconds不得小于0")
        if not self.required_quote_fields:
            raise ValueError("required_quote_fields不能为空")


@dataclass(frozen=True)
class SectorShadowRunResult:
    radar_run_id: str
    as_of: datetime
    status: str
    gate_passed: bool
    gate_reasons: Tuple[str, ...]
    quote_health: Optional[SourceHealthResult]
    stock_count: int
    sector_count: int
    eligible_sector_count: int
    shadow_usable_sector_count: int
    persisted_sector_count: int

    @property
    def item_count(self) -> int:
        """为通用调度监控提供已落库的行业聚合数。"""
        return self.persisted_sector_count


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def build_default_sector_quote_fetcher(
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> QuoteFetcher:
    """构造真实腾讯行情来源，但不立即发出网络请求。"""

    def quotes(
        symbols: Sequence[str],
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> SourceBatch[QuoteSnapshot]:
        return fetch_tencent_quotes(
            symbols,
            radar_run_id=radar_run_id,
            batch_id=batch_id,
            as_of=as_of,
            clock=clock,
        )

    return quotes


class SectorShadowRunner:
    """从冻结行业版本和A股池生成并保存一轮行业聚合影子特征。"""

    def __init__(
        self,
        repository: RadarRepository,
        *,
        quote_fetcher: QuoteFetcher,
        policy: SectorShadowPolicy = SectorShadowPolicy(),
        clock: Callable[[], datetime] = _utc_now,
        run_lock=None,
    ):
        self._repository = repository
        self._quote_fetcher = quote_fetcher
        self._policy = policy
        self._clock = clock
        self._run_lock = run_lock or threading.Lock()

    def run_once(
        self,
        radar_run_id: str,
        as_of: datetime,
    ) -> SectorShadowRunResult:
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        as_of = _aware_utc(as_of, "as_of")
        started_at = _aware_utc(self._clock(), "started_at")
        run_lag_seconds = (started_at - as_of).total_seconds()
        if run_lag_seconds < 0:
            raise ValueError("as_of不能晚于执行器当前时间")
        if run_lag_seconds > self._policy.maximum_quote_age_seconds:
            raise ValueError("as_of已超过实时门禁，不允许补写历史轮次")
        if not self._run_lock.acquire(blocking=False):
            raise SectorShadowRunInProgressError(
                "当前进程已有行业影子运行"
            )

        run_started = False
        stock_count = 0
        try:
            try:
                created = self._repository.start_run(
                    radar_run_id,
                    as_of,
                    started_at=started_at,
                    shadow_mode=True,
                )
            except RepositoryConflictError as exc:
                raise SectorShadowRunAlreadyExistsError(
                    f"雷达运行{radar_run_id}已经存在"
                ) from exc
            if not created:
                raise SectorShadowRunAlreadyExistsError(
                    f"雷达运行{radar_run_id}已经存在"
                )
            run_started = True

            master_records = self._repository.list_security_master_records(as_of)
            stock_count = len(master_records)
            if not master_records:
                raise _GateRejected("security_master_empty")
            symbols = tuple(record.symbol for record in master_records)
            if len(symbols) != len(set(symbols)):
                raise _GateRejected("security_master_identity_not_unique")

            classification_batch_id = (
                f"{radar_run_id}:stored-industry-classification"
            )
            classification = self._load_classification(
                radar_run_id=radar_run_id,
                batch_id=classification_batch_id,
                as_of=as_of,
                loaded_at=started_at,
                master_records=master_records,
            )

            quote_batch_id = f"{radar_run_id}:tencent-quotes"
            quote_batch = self._fetch_quotes(
                symbols=symbols,
                radar_run_id=radar_run_id,
                batch_id=quote_batch_id,
                as_of=as_of,
            )
            self._validate_quote_batch(
                quote_batch,
                symbols=symbols,
                radar_run_id=radar_run_id,
                batch_id=quote_batch_id,
                as_of=as_of,
            )
            quote_health = evaluate_source_health(
                quote_batch.meta,
                SourceHealthPolicy(
                    minimum_row_coverage=(
                        self._policy.minimum_quote_row_coverage
                    ),
                    minimum_required_field_coverage=(
                        self._policy.minimum_required_field_coverage
                    ),
                    maximum_age_seconds=(
                        self._policy.maximum_quote_age_seconds
                    ),
                    maximum_future_skew_seconds=(
                        self._policy.maximum_future_skew_seconds
                    ),
                    required_fields=self._policy.required_quote_fields,
                ),
                now=as_of,
            )

            try:
                features = build_sector_features(
                    classification,
                    quote_batch,
                    stock_symbols=symbols,
                    etf_symbols=(),
                    maximum_age_seconds=(
                        self._policy.maximum_quote_age_seconds
                    ),
                    maximum_future_skew_seconds=(
                        self._policy.maximum_future_skew_seconds
                    ),
                )
            except ValueError as exc:
                gate_reasons = ("sector_feature_contract_rejected",)
                self._repository.record_source_status(
                    quote_batch.meta,
                    quote_health,
                    diagnostics=self._gate_diagnostics(
                        symbols=symbols,
                        quote_batch=quote_batch,
                        features=None,
                        gate_reasons=gate_reasons,
                    ),
                )
                raise _GateRejected("sector_feature_contract_rejected") from exc

            gate_reasons = self._gate_reasons(
                symbols=symbols,
                classification=classification,
                quote_batch=quote_batch,
                quote_health=quote_health,
                features=features,
            )
            eligible_sector_count = sum(
                sector.completeness.expected_count > 1
                for sector in features.sectors
            )
            usable_sector_count = sum(
                sector.shadow_usable
                for sector in features.sectors
            )
            self._repository.record_source_status(
                quote_batch.meta,
                quote_health,
                diagnostics=self._gate_diagnostics(
                    symbols=symbols,
                    quote_batch=quote_batch,
                    features=features,
                    gate_reasons=gate_reasons,
                ),
            )
            if gate_reasons:
                self._complete_degraded(
                    radar_run_id,
                    expected_stock_count=len(symbols),
                    returned_stock_count=quote_batch.meta.returned_count,
                    error_code="sector_feature_gate_rejected",
                )
                return SectorShadowRunResult(
                    radar_run_id=radar_run_id,
                    as_of=as_of,
                    status="degraded",
                    gate_passed=False,
                    gate_reasons=gate_reasons,
                    quote_health=quote_health,
                    stock_count=len(symbols),
                    sector_count=len(features.sectors),
                    eligible_sector_count=eligible_sector_count,
                    shadow_usable_sector_count=usable_sector_count,
                    persisted_sector_count=0,
                )

            write_result = self._repository.record_sector_feature_batch(features)
            self._complete_degraded(
                radar_run_id,
                expected_stock_count=len(symbols),
                returned_stock_count=quote_batch.meta.returned_count,
                error_code="sector_features_shadow_partial",
            )
            return SectorShadowRunResult(
                radar_run_id=radar_run_id,
                as_of=as_of,
                status="degraded",
                gate_passed=True,
                gate_reasons=(),
                quote_health=quote_health,
                stock_count=len(symbols),
                sector_count=len(features.sectors),
                eligible_sector_count=eligible_sector_count,
                shadow_usable_sector_count=usable_sector_count,
                persisted_sector_count=write_result.inserted,
            )
        except (SectorShadowRunAlreadyExistsError, SectorShadowRunInProgressError):
            raise
        except _GateRejected as exc:
            if run_started:
                self._complete_degraded(
                    radar_run_id,
                    expected_stock_count=None,
                    returned_stock_count=None,
                    error_code="sector_feature_gate_rejected",
                )
            return SectorShadowRunResult(
                radar_run_id=radar_run_id,
                as_of=as_of,
                status="degraded",
                gate_passed=False,
                gate_reasons=exc.reasons,
                quote_health=None,
                stock_count=stock_count,
                sector_count=0,
                eligible_sector_count=0,
                shadow_usable_sector_count=0,
                persisted_sector_count=0,
            )
        except _BatchValidationError as exc:
            if run_started:
                self._mark_failed(radar_run_id, exc.error_code)
            raise SectorShadowRunExecutionError(
                "行业影子行情批次合同不一致，整轮已标记失败"
            ) from exc
        except Exception as exc:
            if run_started:
                self._mark_failed(radar_run_id, "sector_shadow_internal_error")
            raise SectorShadowRunExecutionError(
                f"行业影子执行失败：{type(exc).__name__}"
            ) from exc
        finally:
            self._run_lock.release()

    def _load_classification(
        self,
        *,
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
        loaded_at: datetime,
        master_records: Sequence[SecurityMasterRecord],
    ) -> IndustryClassificationSnapshot:
        release = self._repository.get_industry_classification_release(
            self._policy.classification_system,
            self._policy.release_period,
        )
        if release is None:
            raise _GateRejected("classification_release_missing")
        if as_of < _aware_utc(
            release.knowledge_effective_from,
            "knowledge_effective_from",
        ):
            raise _GateRejected("classification_knowledge_in_future")
        if (
            release.knowledge_effective_to is not None
            and as_of >= _aware_utc(
                release.knowledge_effective_to,
                "knowledge_effective_to",
            )
        ):
            raise _GateRejected("classification_knowledge_expired")

        records = self._repository.list_industry_classification_records(
            self._policy.classification_system,
            self._policy.release_period,
        )
        if not records:
            raise _GateRejected("classification_records_missing")
        mapped_records = tuple(
            record
            for record in records
            if (
                record.record_status == IndustryRecordStatus.ACCEPTED
                and record.identity_status != IndustryIdentityStatus.UNRESOLVED
                and record.security_identity is not None
            )
        )
        mapped_identities = tuple(
            record.security_identity
            for record in mapped_records
        )
        if len(mapped_identities) != len(set(mapped_identities)):
            raise _GateRejected("classification_identity_not_unique")
        current_symbols = {record.symbol for record in master_records}
        if not set(mapped_identities).issubset(current_symbols):
            raise _GateRejected(
                "classification_identity_outside_current_stock_pool"
            )

        gaps = []
        mapped_set = set(mapped_identities)
        for record in master_records:
            if record.symbol in mapped_set:
                continue
            if record.listing_date is None:
                issue_codes = ("listing_date_missing",)
            elif record.listing_date >= release.classification_start_date:
                issue_codes = ("listed_on_or_after_classification_start",)
            else:
                issue_codes = ("current_master_mapping_gap",)
            gaps.append(IndustryClassificationGap(
                securityIdentity=record.symbol,
                symbol=record.symbol,
                name=record.name,
                listingDate=record.listing_date,
                issueCodes=issue_codes,
            ))

        excluded_source_count = len(records) - len(mapped_records)
        issues = []
        reasons = []
        if gaps:
            issues.append(SourceIssue(
                code="current_master_mapping_incomplete",
                source="capco_industry_classification_stored",
                message=f"当前证券主档有{len(gaps)}只行业未确认",
                symbols=[gap.symbol for gap in gaps],
            ))
            reasons.append("current_master_mapping_incomplete")
        if excluded_source_count:
            issues.append(SourceIssue(
                code="source_records_excluded",
                source="capco_industry_classification_stored",
                message=(
                    f"已保存行业版本有{excluded_source_count}条记录"
                    "未映射当前A股"
                ),
            ))
            reasons.append("source_records_excluded")
        reasons.append("formal_use_not_approved")
        master_count = len(master_records)
        mapped_count = len(mapped_records)
        return IndustryClassificationSnapshot(
            meta=RadarBatchMeta(
                radarRunId=radar_run_id,
                batchId=batch_id,
                source="capco_industry_classification_stored",
                asOf=as_of,
                sourceTime=None,
                fetchedAt=loaded_at,
                expectedCount=len(records),
                returnedCount=len(records),
                rowCoverage=1.0,
                requiredFieldCoverage=release.required_field_coverage,
                issues=issues,
            ),
            status=(SourceStatus.DEGRADED if issues else SourceStatus.HEALTHY),
            release=release,
            records=list(records),
            currentMasterGaps=gaps,
            completeness=IndustryClassificationCompleteness(
                sourceRecordCount=len(records),
                uniqueSourceSymbolCount=len({r.source_symbol for r in records}),
                currentMasterCount=master_count,
                mappedCount=mapped_count,
                unconfirmedCount=len(gaps),
                excludedSourceCount=excluded_source_count,
                mappingCoverage=(mapped_count / master_count),
                requiredFieldCoverage=release.required_field_coverage,
                shadowUsable=True,
                formalUsable=False,
                reasons=tuple(reasons),
            ),
            issues=issues,
        )

    def _fetch_quotes(
        self,
        *,
        symbols: Sequence[str],
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> SourceBatch[QuoteSnapshot]:
        try:
            return self._quote_fetcher(
                symbols,
                radar_run_id,
                batch_id,
                as_of,
            )
        except Exception as exc:
            fetched_at = _aware_utc(self._clock(), "fetched_at")
            return SourceBatch(
                meta=RadarBatchMeta(
                    radarRunId=radar_run_id,
                    batchId=batch_id,
                    source="tencent_finance",
                    asOf=as_of,
                    sourceTime=None,
                    fetchedAt=fetched_at,
                    expectedCount=len(symbols),
                    returnedCount=0,
                    rowCoverage=0.0,
                    requiredFieldCoverage={},
                    issues=[SourceIssue(
                        code="source_exception",
                        source="tencent_finance",
                        message=(
                            "腾讯行情采集异常："
                            f"{type(exc).__name__}"
                        ),
                    )],
                ),
                items=[],
            )

    @staticmethod
    def _validate_quote_batch(
        quote_batch: SourceBatch[QuoteSnapshot],
        *,
        symbols: Sequence[str],
        radar_run_id: str,
        batch_id: str,
        as_of: datetime,
    ) -> None:
        meta = quote_batch.meta
        if (
            meta.radar_run_id != radar_run_id
            or meta.batch_id != batch_id
            or meta.source != "tencent_finance"
            or _aware_utc(meta.as_of, "行情批次as_of") != as_of
        ):
            raise _BatchValidationError(
                "行情批次身份、来源或asOf不一致",
                "sector_shadow_batch_identity",
            )
        if meta.expected_count != len(symbols):
            raise _BatchValidationError(
                "行情批次expectedCount与A股池不一致",
                "sector_shadow_batch_expected_count",
            )
        if meta.returned_count != len(quote_batch.items):
            raise _BatchValidationError(
                "行情批次returnedCount与实际记录数不一致",
                "sector_shadow_batch_returned_count",
            )
        if _aware_utc(meta.fetched_at, "行情批次fetched_at") < as_of:
            raise _BatchValidationError(
                "行情批次抓取时间早于本轮asOf",
                "sector_shadow_batch_time",
            )

    def _gate_reasons(
        self,
        *,
        symbols: Sequence[str],
        classification: IndustryClassificationSnapshot,
        quote_batch: SourceBatch[QuoteSnapshot],
        quote_health: SourceHealthResult,
        features: SectorFeatureBatch,
    ) -> Tuple[str, ...]:
        reasons = [
            f"quote_health:{reason}"
            for reason in quote_health.reasons
        ]
        if quote_health.status != SourceStatus.HEALTHY and not reasons:
            reasons.append("quote_health_not_healthy")
        if quote_batch.meta.returned_count != len(symbols):
            reasons.append("quote_row_count_incomplete")

        quote_symbols = tuple(item.symbol for item in quote_batch.items)
        if len(quote_symbols) != len(set(quote_symbols)):
            reasons.append("quote_symbols_not_unique")
        if set(quote_symbols) != set(symbols):
            reasons.append("quote_symbol_set_mismatch")

        for item in quote_batch.items:
            if item.source_time is None:
                reasons.append("quote_item_source_time_missing")
                continue
            signed_age = (
                features.as_of - _aware_utc(
                    item.source_time,
                    "行情source_time",
                )
            ).total_seconds()
            if signed_age > self._policy.maximum_quote_age_seconds:
                reasons.append("quote_item_source_time_stale")
            if signed_age < -self._policy.maximum_future_skew_seconds:
                reasons.append("quote_item_source_time_in_future")

        if features.duplicate_quote_symbols:
            reasons.append("duplicate_quote_symbols")
        if features.unknown_quote_symbols:
            reasons.append("unknown_quote_symbols")

        expected_sector_count = len({
            record.division_code
            for record in classification.records
            if (
                record.record_status == IndustryRecordStatus.ACCEPTED
                and record.identity_status != IndustryIdentityStatus.UNRESOLVED
            )
        })
        if len(features.sectors) != expected_sector_count:
            reasons.append("sector_count_mismatch")
        eligible_sectors = [
            sector
            for sector in features.sectors
            if sector.completeness.expected_count > 1
        ]
        if any(not sector.shadow_usable for sector in eligible_sectors):
            reasons.append("eligible_sector_features_incomplete")
        if features.formal_usable:
            reasons.append("formal_use_must_remain_false")
        return _dedupe(reasons)

    def _gate_diagnostics(
        self,
        *,
        symbols: Sequence[str],
        quote_batch: SourceBatch[QuoteSnapshot],
        features: Optional[SectorFeatureBatch],
        gate_reasons: Sequence[str],
    ) -> dict:
        missing_count = 0
        stale_count = 0
        future_count = 0
        for item in quote_batch.items:
            if item.source_time is None:
                missing_count += 1
                continue
            signed_age = (
                quote_batch.meta.as_of
                - _aware_utc(item.source_time, "行情source_time")
            ).total_seconds()
            if signed_age > self._policy.maximum_quote_age_seconds:
                stale_count += 1
            if signed_age < -self._policy.maximum_future_skew_seconds:
                future_count += 1

        sectors = tuple(features.sectors) if features is not None else ()
        eligible_sectors = tuple(
            sector
            for sector in sectors
            if sector.completeness.expected_count > 1
        )
        incomplete_reason_counts = Counter(
            reason
            for sector in eligible_sectors
            if not sector.shadow_usable
            for reason in sector.reasons
        )
        return {
            "gatePassed": not gate_reasons,
            "gateReasons": list(gate_reasons),
            "stockCount": len(symbols),
            "quoteCount": len(quote_batch.items),
            "sectorCount": len(sectors),
            "eligibleSectorCount": len(eligible_sectors),
            "shadowUsableSectorCount": sum(
                sector.shadow_usable
                for sector in sectors
            ),
            "quoteItemTimeSummary": {
                "missingCount": missing_count,
                "staleCount": stale_count,
                "futureCount": future_count,
            },
            "incompleteEligibleSectorReasonCounts": dict(sorted(
                incomplete_reason_counts.items()
            )),
        }

    def _complete_degraded(
        self,
        radar_run_id: str,
        *,
        expected_stock_count: Optional[int],
        returned_stock_count: Optional[int],
        error_code: str,
    ) -> None:
        self._repository.complete_run(
            radar_run_id,
            status="degraded",
            completed_at=_aware_utc(self._clock(), "completed_at"),
            expected_stock_count=expected_stock_count,
            returned_stock_count=returned_stock_count,
            error_code=error_code,
        )

    def _mark_failed(self, radar_run_id: str, error_code: str) -> None:
        try:
            self._repository.complete_run(
                radar_run_id,
                status="failed",
                completed_at=_aware_utc(self._clock(), "completed_at"),
                error_code=error_code,
            )
        except RadarRepositoryError:
            return
