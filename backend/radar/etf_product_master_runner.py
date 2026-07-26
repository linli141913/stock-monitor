"""阶段5 ETF官方产品主档低频执行器。

执行器只接受显式仓储和来源函数，不创建数据库连接、不注册调度任务，
也不会把首次抓取时间冒充为官方历史生效时间。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Tuple

from radar.config import RadarSettings
from radar.contracts import EtfProductMasterRecord, SourceBatch
from radar.etf_repository import (
    EtfProductMasterBatchWriteResult,
    EtfProductProfileTransition,
    EtfRepository,
)
from radar.repository import RadarRepositoryError


UTC = timezone.utc
PRODUCT_MASTER_SOURCE = "official_exchange_listed_fund_product_master"
PRODUCT_MASTER_BATCH_SUFFIX = "etf-product-master"
PRODUCT_SOURCE_CONTRACTS = {
    "sse": "exchange-listed-fund-product-master-sse-v1",
    "szse": "exchange-listed-fund-product-master-szse-v1",
}
PRODUCT_EVIDENCE_URLS = {
    "sse": "https://etf.sse.com.cn/fundlist/",
    "szse": "https://fund.szse.cn/marketdata/fundslist/index.html",
}
REQUIRED_COVERAGE_FIELDS = frozenset({
    "official_name",
    "product_type_known",
    "listing_date",
    "etf_asset_class_confirmed",
    "etf_management_style_confirmed",
})

ProductMasterFetcher = Callable[
    [str, str, datetime],
    SourceBatch[EtfProductMasterRecord],
]


class EtfProductMasterRunnerError(RuntimeError):
    """阶段5 ETF产品主档执行器基础错误。"""


class EtfProductMasterDisabledError(EtfProductMasterRunnerError):
    """阶段5 ETF开关未开启。"""


class EtfProductMasterExecutionError(EtfProductMasterRunnerError):
    """产品主档批次身份或仓储写入不满足合同。"""


@dataclass(frozen=True)
class EtfProductMasterRunResult:
    product_master_run_id: str
    as_of: datetime
    status: str
    gate_passed: bool
    gate_reasons: Tuple[str, ...]
    returned_count: int
    inserted_count: int
    unchanged_count: int

    @property
    def item_count(self) -> int:
        return self.returned_count


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _profile_id(
    record: EtfProductMasterRecord,
    *,
    source_contract_id: str,
    first_observed_at: datetime,
) -> str:
    payload = record.model_dump(mode="json", by_alias=True)
    payload.pop("fetchedAt", None)
    payload["sourceContractId"] = source_contract_id
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    observed_token = _aware_utc(
        first_observed_at,
        "first_observed_at",
    ).strftime("%Y%m%dT%H%M%S%fZ")
    return f"etf-profile-{record.symbol}-{observed_token}-{digest}"


class EtfProductMasterRunner:
    """校验官方完整批次并原子推进首次观察产品版本。"""

    def __init__(
        self,
        repository: EtfRepository,
        *,
        settings: RadarSettings,
        fetcher: ProductMasterFetcher,
        clock: Callable[[], datetime],
    ):
        self._repository = repository
        self._settings = settings
        self._fetcher = fetcher
        self._clock = clock

    def run_once(
        self,
        product_master_run_id: str,
        as_of: datetime,
    ) -> EtfProductMasterRunResult:
        if (
            not self._settings.enabled
            or not self._settings.shadow_mode
            or not self._settings.etf_stage5_enabled
        ):
            raise EtfProductMasterDisabledError(
                "RADAR_ENABLED、RADAR_SHADOW_MODE和"
                "RADAR_ETF_STAGE5_ENABLED必须同时开启"
            )
        product_master_run_id = product_master_run_id.strip()
        if not product_master_run_id:
            raise ValueError("product_master_run_id不能为空")
        as_of = _aware_utc(as_of, "as_of")
        batch_id = f"{product_master_run_id}:{PRODUCT_MASTER_BATCH_SUFFIX}"
        batch = self._fetcher(product_master_run_id, batch_id, as_of)
        self._validate_batch(
            product_master_run_id,
            batch_id,
            as_of,
            batch,
        )

        status, gate_reasons = self._status_and_reasons(batch)
        transitions = (
            tuple(self._transition(record, batch.meta.fetched_at)
                  for record in batch.items)
            if status == "succeeded"
            else ()
        )
        try:
            write_result = self._repository.sync_product_master_batch(
                product_master_run_id=product_master_run_id,
                as_of=as_of,
                source=batch.meta.source,
                source_time=batch.meta.source_time,
                fetched_at=batch.meta.fetched_at,
                status=status,
                expected_count=batch.meta.expected_count,
                returned_count=batch.meta.returned_count,
                row_coverage=batch.meta.row_coverage,
                required_field_coverage=(
                    batch.meta.required_field_coverage
                ),
                issues=[
                    issue.model_dump(mode="json", by_alias=True)
                    for issue in batch.meta.issues
                ],
                transitions=transitions,
            )
        except RadarRepositoryError as exc:
            raise EtfProductMasterExecutionError(
                f"ETF产品主档仓储写入失败：{type(exc).__name__}"
            ) from exc

        return self._result(
            product_master_run_id,
            as_of,
            status,
            gate_reasons,
            batch.meta.returned_count,
            write_result,
        )

    @staticmethod
    def _validate_batch(
        product_master_run_id: str,
        batch_id: str,
        as_of: datetime,
        batch: SourceBatch[EtfProductMasterRecord],
    ) -> None:
        meta = batch.meta
        if (
            meta.radar_run_id != product_master_run_id
            or meta.batch_id != batch_id
            or _aware_utc(meta.as_of, "产品主档批次as_of") != as_of
            or meta.source != PRODUCT_MASTER_SOURCE
        ):
            raise EtfProductMasterExecutionError(
                "ETF产品主档批次身份、来源或asOf不一致"
            )
        if meta.source_time is not None:
            raise EtfProductMasterExecutionError(
                "官方产品主档未提供源版本时间，sourceTime必须为空"
            )
        fetched_at = _aware_utc(meta.fetched_at, "产品主档fetched_at")
        if fetched_at < as_of:
            raise EtfProductMasterExecutionError(
                "ETF产品主档fetchedAt不能早于asOf"
            )
        if meta.returned_count != len(batch.items):
            raise EtfProductMasterExecutionError(
                "ETF产品主档returnedCount与明细数量不一致"
            )
        symbols = [item.symbol for item in batch.items]
        if len(symbols) != len(set(symbols)):
            raise EtfProductMasterExecutionError(
                "ETF产品主档批次包含重复代码"
            )
        if not REQUIRED_COVERAGE_FIELDS.issubset(
            meta.required_field_coverage
        ):
            raise EtfProductMasterExecutionError(
                "ETF产品主档批次缺少必需覆盖率字段"
            )
        for item in batch.items:
            expected_source = {
                "sse": "sse_official_fund_list",
                "szse": "szse_official_fund_list",
            }.get(item.exchange)
            if (
                expected_source is None
                or item.source != expected_source
                or _aware_utc(
                    item.fetched_at,
                    "产品记录fetched_at",
                ) != fetched_at
            ):
                raise EtfProductMasterExecutionError(
                    f"ETF产品{item.symbol}交易所、来源或抓取时间不一致"
                )

    @staticmethod
    def _status_and_reasons(
        batch: SourceBatch[EtfProductMasterRecord],
    ) -> Tuple[str, Tuple[str, ...]]:
        meta = batch.meta
        issue_codes = tuple(
            dict.fromkeys(issue.code for issue in meta.issues)
        )
        if meta.expected_count is None:
            return "failed", issue_codes or ("source_batch_failed",)
        if meta.expected_count == 0:
            return "failed", (*issue_codes, "empty_product_master")
        if (
            meta.returned_count != meta.expected_count
            or meta.row_coverage != 1.0
            or issue_codes
        ):
            return "degraded", issue_codes or ("incomplete_product_master",)
        return "succeeded", ()

    @staticmethod
    def _transition(
        record: EtfProductMasterRecord,
        first_observed_at: datetime,
    ) -> EtfProductProfileTransition:
        source_contract_id = PRODUCT_SOURCE_CONTRACTS[record.exchange]
        return EtfProductProfileTransition(
            record=record,
            profile_id=_profile_id(
                record,
                source_contract_id=source_contract_id,
                first_observed_at=first_observed_at,
            ),
            source_contract_id=source_contract_id,
            first_observed_at=first_observed_at,
            official_effective_from=None,
            source_time=None,
            evidence_url=PRODUCT_EVIDENCE_URLS[record.exchange],
            evidence_sha256=None,
        )

    @staticmethod
    def _result(
        product_master_run_id: str,
        as_of: datetime,
        status: str,
        gate_reasons: Tuple[str, ...],
        returned_count: int,
        write_result: EtfProductMasterBatchWriteResult,
    ) -> EtfProductMasterRunResult:
        return EtfProductMasterRunResult(
            product_master_run_id=product_master_run_id,
            as_of=as_of,
            status=status,
            gate_passed=status == "succeeded",
            gate_reasons=gate_reasons,
            returned_count=returned_count,
            inserted_count=write_result.inserted,
            unchanged_count=write_result.unchanged,
        )
