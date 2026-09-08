"""阶段10真实影子运行回执与三模块纯数据适配。

本模块只处理调用方已安全读取的 JSON bytes。它不解析路径、
不打开文件、不访问网络或 SQLite，也不修改任何服务状态。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from radar.formal_readiness_contracts import FormalModuleName
from radar.formal_shadow_ledger import (
    FormalLockState,
    FormalObservationStatus,
    FormalShadowObservation,
)
from radar.contracts import MarketFeatureSnapshot
from radar.market_research_state import (
    DEFAULT_MARKET_RESEARCH_STATE_POLICY,
    MARKET_RESEARCH_STATE_CONTRACT_ID,
    MarketResearchStateStatus,
    produce_market_research_state,
)
from radar.sector_rule_readiness import SECTOR_RULE_VERSION
from radar.sector_state_producer import (
    SECTOR_STATE_SNAPSHOT_CONTRACT_ID,
    SECTOR_STATE_TRANSITION_POLICY_VERSION,
)


FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID = (
    "radar-formal-shadow-run-receipt-v1"
)
FORMAL_SHADOW_COLLECTION_POLICY_CONTRACT_ID = (
    "radar-formal-shadow-collection-policy-v1"
)
FORMAL_SHADOW_COLLECTION_POLICY_ID = "stage10-shadow-collection-policy-v1"
FORMAL_SHADOW_COLLECTION_POLICY_SHA256 = (
    "d8e02f3ec075ed8c82a195ccd7f93ec80760e0a83cb98a3b709f9eaf182640a4"
)
FORMAL_SHADOW_MAXIMUM_SOURCE_AGE_SECONDS = 90
FORMAL_SHADOW_MAXIMUM_COLLECTION_DELAY_SECONDS = 300
ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID = (
    "radar-etf-live-shadow-observation-v1"
)
MARKET_FEATURE_SNAPSHOT_EVIDENCE_CONTRACT_ID = (
    "radar-market-feature-snapshot-evidence-v1"
)
ETF_TRUSTED_QUOTE_SOURCE_CONTRACT_ID = "tencent-quote-snapshot-v1"
MODULE_SOURCE_CONTRACTS: Mapping[FormalModuleName, str] = {
    "trendRotation": MARKET_RESEARCH_STATE_CONTRACT_ID,
    "leaderObservation": (
        "radar-leader-phase6-live-source-collection-v1"
    ),
    "etfObservation": ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID,
}
MODULE_COVERAGE_SCOPES: Mapping[FormalModuleName, str] = {
    "trendRotation": "stage6_market_and_sector_state",
    "leaderObservation": "stage6_parent_candidate_partition",
    "etfObservation": "live_requested_etf_quotes",
}
_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_PATTERN = re.compile(r"^\d{6}$")
_CLOSED_GATE_KEYS = {
    "formalScoreReady",
    "formalGateReady",
    "formalUsable",
    "stateTransitionAllowed",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class _FrozenDict(dict):
    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("formal_shadow_collection_policy_immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name}_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_timezone_required")
    return value


def _require_json_bytes(value: Any, reason: str) -> bytes:
    if type(value) is not bytes:
        raise ValueError(reason)
    return value


def _require_evaluated_at(value: Any) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("formal_shadow_evaluated_at_invalid")
    return value


def _strict_json_bytes(raw: bytes, reason: str) -> Dict[str, Any]:
    if type(raw) is not bytes or not raw:
        raise ValueError(reason)

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("non_finite_json_number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, dict):
        raise ValueError(reason)
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: object) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("formal_shadow_evidence_unverified") from exc
    return _sha256(raw)


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_closed_gate(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _CLOSED_GATE_KEYS
        and all(value[key] is False for key in _CLOSED_GATE_KEYS)
    )


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


class FormalShadowCollectionPolicy(_FrozenModel):
    """由收集入口独立冻结的新鲜度策略，不信任回执自报门槛。"""

    contract_id: Literal[FORMAL_SHADOW_COLLECTION_POLICY_CONTRACT_ID] = Field(
        default=FORMAL_SHADOW_COLLECTION_POLICY_CONTRACT_ID,
        alias="contractId",
    )
    policy_id: Literal[FORMAL_SHADOW_COLLECTION_POLICY_ID] = Field(
        default=FORMAL_SHADOW_COLLECTION_POLICY_ID,
        alias="policyId",
    )
    policy_sha256: Literal[
        FORMAL_SHADOW_COLLECTION_POLICY_SHA256
    ] = Field(
        default=FORMAL_SHADOW_COLLECTION_POLICY_SHA256,
        alias="policySha256",
    )
    receipt_contract_id: Literal[FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID] = Field(
        default=FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
        alias="receiptContractId",
    )
    maximum_source_age_seconds_by_module: Dict[
        FormalModuleName,
        StrictInt,
    ] = Field(alias="maximumSourceAgeSecondsByModule")
    maximum_collection_delay_seconds_by_module: Dict[
        FormalModuleName,
        StrictInt,
    ] = Field(alias="maximumCollectionDelaySecondsByModule")

    @field_validator(
        "maximum_source_age_seconds_by_module",
        "maximum_collection_delay_seconds_by_module",
    )
    @classmethod
    def validate_module_limits(
        cls,
        value: Dict[FormalModuleName, int],
        info: ValidationInfo,
    ) -> Dict[FormalModuleName, int]:
        if set(value) != set(MODULE_SOURCE_CONTRACTS):
            raise ValueError("formal_shadow_collection_policy_modules_incomplete")
        expected = (
            FORMAL_SHADOW_MAXIMUM_SOURCE_AGE_SECONDS
            if info.field_name == "maximum_source_age_seconds_by_module"
            else FORMAL_SHADOW_MAXIMUM_COLLECTION_DELAY_SECONDS
        )
        if any(limit != expected for limit in value.values()):
            raise ValueError("formal_shadow_collection_policy_limit_invalid")
        return _FrozenDict(value)

    @model_validator(mode="after")
    def validate_canonical_policy(self) -> "FormalShadowCollectionPolicy":
        source_limits = self.maximum_source_age_seconds_by_module
        collection_limits = self.maximum_collection_delay_seconds_by_module
        if any(
            value != FORMAL_SHADOW_MAXIMUM_SOURCE_AGE_SECONDS
            for value in source_limits.values()
        ) or any(
            value != FORMAL_SHADOW_MAXIMUM_COLLECTION_DELAY_SECONDS
            for value in collection_limits.values()
        ):
            raise ValueError("formal_shadow_collection_policy_limit_invalid")
        semantic = self.model_dump(mode="json", by_alias=True)
        semantic.pop("policySha256")
        if _canonical_sha256(semantic) != self.policy_sha256:
            raise ValueError("formal_shadow_collection_policy_sha_mismatch")
        return self

    def model_copy(
        self,
        *,
        update: Optional[Mapping[str, Any]] = None,
        deep: bool = False,
    ) -> "FormalShadowCollectionPolicy":
        del deep
        payload = self.model_dump(mode="python")
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class FormalShadowRunReceipt(_FrozenModel):
    """现场运行器产生的不可变回执；覆盖率只能由整数计数派生。"""

    contract_id: Literal[FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID] = Field(
        default=FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
        alias="contractId",
    )
    module: FormalModuleName
    run_id: str = Field(alias="runId", min_length=1)
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    observed_at: datetime = Field(alias="observedAt")
    source_contract_id: str = Field(alias="sourceContractId", min_length=1)
    source_artifact_sha256: str = Field(
        alias="sourceArtifactSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    coverage_scope: str = Field(alias="coverageScope", min_length=1)
    expected_count: StrictInt = Field(alias="expectedCount", gt=0)
    observed_count: StrictInt = Field(alias="observedCount", ge=0)
    missing_count: StrictInt = Field(alias="missingCount", ge=0)
    failed_count: StrictInt = Field(alias="failedCount", ge=0)
    stale_count: StrictInt = Field(alias="staleCount", ge=0)
    coverage: StrictFloat = Field(ge=0, le=1)
    lock_state: FormalLockState = Field(alias="lockState")
    duration_ms: StrictInt = Field(alias="durationMs", ge=0)
    source_ready: StrictBool = Field(alias="sourceReady")
    source_health_status: Optional[
        Literal["healthy", "degraded", "stale", "failed"]
    ] = Field(default=None, alias="sourceHealthStatus")
    source_health_reasons: Tuple[str, ...] = Field(
        default_factory=tuple,
        alias="sourceHealthReasons",
    )

    @field_validator("run_id", "source_contract_id", "coverage_scope")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("formal_shadow_receipt_blank_identity")
        return value

    @field_validator("as_of", "source_time", "fetched_at", "observed_at")
    @classmethod
    def require_aware_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        return _aware(value, "formal_shadow_receipt_time")

    @model_validator(mode="after")
    def validate_receipt(self) -> "FormalShadowRunReceipt":
        if self.source_contract_id != MODULE_SOURCE_CONTRACTS[self.module]:
            raise ValueError("formal_shadow_receipt_source_contract_mismatch")
        if self.coverage_scope != MODULE_COVERAGE_SCOPES[self.module]:
            raise ValueError("formal_shadow_receipt_coverage_scope_mismatch")
        if self.module == "etfObservation":
            if (
                self.source_health_status is None
                or "source_health_status" not in self.model_fields_set
                or "source_health_reasons" not in self.model_fields_set
                or (
                    self.source_health_status == "healthy"
                    and self.source_health_reasons != ()
                )
                or (
                    self.source_health_status != "healthy"
                    and not self.source_health_reasons
                )
            ):
                raise ValueError("formal_shadow_receipt_source_health_invalid")
            if not self.as_of <= self.fetched_at <= self.observed_at:
                raise ValueError("formal_shadow_receipt_time_order_invalid")
            if (
                self.source_time is not None
                and self.source_time > self.fetched_at
            ):
                raise ValueError("formal_shadow_receipt_time_order_invalid")
            if self.source_time is None and any((
                self.observed_count != 0,
                self.failed_count != self.expected_count,
                self.source_ready,
            )):
                raise ValueError("formal_shadow_receipt_source_time_missing")
        elif self.source_time is None:
            if any((
                self.observed_count != 0,
                self.failed_count != self.expected_count,
                self.source_ready,
            )):
                raise ValueError("formal_shadow_receipt_source_time_missing")
        elif not (
            self.source_time <= self.fetched_at <= self.as_of <= self.observed_at
        ):
            raise ValueError("formal_shadow_receipt_time_order_invalid")
        local_values = (
            (self.source_time,) if self.source_time is not None else ()
        ) + (
            self.fetched_at,
            self.as_of,
            self.observed_at,
        )
        local_dates = {
            value.astimezone(_SHANGHAI_TIMEZONE).date()
            for value in local_values
        }
        if len(local_dates) != 1:
            raise ValueError("formal_shadow_receipt_same_shanghai_day_required")
        if (
            self.observed_count + self.missing_count + self.failed_count
            != self.expected_count
            or self.stale_count > self.observed_count
        ):
            raise ValueError("formal_shadow_receipt_counts_conflict")
        expected_coverage = self.observed_count / self.expected_count
        if self.coverage != expected_coverage:
            raise ValueError("formal_shadow_receipt_coverage_conflict")
        expected_ready = (
            self.lock_state == "acquired"
            and self.observed_count == self.expected_count
            and self.missing_count == 0
            and self.failed_count == 0
            and self.stale_count == 0
            and (
                self.module != "etfObservation"
                or self.source_health_status in {None, "healthy"}
            )
        )
        if self.source_ready is not expected_ready:
            raise ValueError("formal_shadow_receipt_ready_conflict")
        return self


EtfLiveItemStatus = Literal["ready", "stale"]
EtfLiveArtifactStatus = Literal["ready", "missing", "failed", "stale"]
EtfLiveSourceHealthStatus = Literal["healthy", "degraded", "stale", "failed"]


class EtfLiveShadowRecord(_FrozenModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    source_time: datetime = Field(alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    last_price: StrictFloat = Field(alias="lastPrice", gt=0)
    change_percent: StrictFloat = Field(alias="changePercent")
    turnover_amount_cny: StrictFloat = Field(alias="turnoverAmountCny", ge=0)
    quote_source_contract_id: Literal[
        ETF_TRUSTED_QUOTE_SOURCE_CONTRACT_ID
    ] = Field(
        alias="quoteSourceContractId",
        min_length=1,
    )
    status: EtfLiveItemStatus

    @field_validator("source_time", "fetched_at")
    @classmethod
    def require_record_time(cls, value: datetime) -> datetime:
        return _aware(value, "etf_live_record_time")

    @field_validator("last_price", "change_percent", "turnover_amount_cny")
    @classmethod
    def require_finite_number(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("etf_live_number_non_finite")
        return value

    @model_validator(mode="after")
    def validate_record_time(self) -> "EtfLiveShadowRecord":
        if self.source_time > self.fetched_at:
            raise ValueError("etf_live_record_time_order_invalid")
        return self


class EtfLiveShadowObservationArtifact(_FrozenModel):
    contract_id: Literal[ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID] = Field(
        default=ETF_LIVE_SHADOW_OBSERVATION_CONTRACT_ID,
        alias="contractId",
    )
    status: EtfLiveArtifactStatus
    run_id: str = Field(alias="runId", min_length=1)
    as_of: datetime = Field(alias="asOf")
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    requested_count: StrictInt = Field(alias="requestedCount", gt=0)
    returned_count: StrictInt = Field(alias="returnedCount", ge=0)
    missing_count: StrictInt = Field(alias="missingCount", ge=0)
    failed_count: StrictInt = Field(alias="failedCount", ge=0)
    stale_count: StrictInt = Field(alias="staleCount", ge=0)
    lock_state: FormalLockState = Field(alias="lockState")
    duration_ms: StrictInt = Field(alias="durationMs", ge=0)
    source_health_status: EtfLiveSourceHealthStatus = Field(
        alias="sourceHealthStatus",
    )
    source_health_reasons: Tuple[str, ...] = Field(
        alias="sourceHealthReasons",
    )
    requested_symbols: Tuple[str, ...] = Field(alias="requestedSymbols")
    missing_symbols: Tuple[str, ...] = Field(alias="missingSymbols")
    failed_symbols: Tuple[str, ...] = Field(alias="failedSymbols")
    stale_symbols: Tuple[str, ...] = Field(alias="staleSymbols")
    records: Tuple[EtfLiveShadowRecord, ...]

    @field_validator("as_of", "source_time", "fetched_at")
    @classmethod
    def require_artifact_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        return _aware(value, "etf_live_artifact_time")

    @model_validator(mode="after")
    def validate_artifact(self) -> "EtfLiveShadowObservationArtifact":
        requested = self.requested_symbols
        returned = tuple(item.symbol for item in self.records)
        groups = (
            requested,
            returned,
            self.missing_symbols,
            self.failed_symbols,
            self.stale_symbols,
        )
        if any(
            len(values) != len(set(values))
            or any(_SYMBOL_PATTERN.fullmatch(value) is None for value in values)
            for values in groups
        ):
            raise ValueError("etf_live_symbol_scope_invalid")
        if any((
            len(requested) != self.requested_count,
            len(returned) != self.returned_count,
            len(self.missing_symbols) != self.missing_count,
            len(self.failed_symbols) != self.failed_count,
            len(self.stale_symbols) != self.stale_count,
            self.returned_count + self.missing_count + self.failed_count
            != self.requested_count,
            set(returned) & set(self.missing_symbols),
            set(returned) & set(self.failed_symbols),
            set(self.missing_symbols) & set(self.failed_symbols),
            set(requested)
            != set(returned) | set(self.missing_symbols) | set(self.failed_symbols),
            set(self.stale_symbols) != {
                item.symbol for item in self.records if item.status == "stale"
            },
        )):
            raise ValueError("etf_live_counts_or_symbols_conflict")
        if len(self.source_health_reasons) != len(set(self.source_health_reasons)):
            raise ValueError("etf_live_source_health_reasons_duplicated")
        if (
            self.source_health_status == "healthy"
            and self.source_health_reasons != ()
        ) or (
            self.source_health_status != "healthy"
            and not self.source_health_reasons
        ):
            raise ValueError("etf_live_source_health_conflict")
        if self.source_time is None:
            if any((
                self.records,
                self.returned_count != 0,
                self.failed_count != self.requested_count,
            )):
                raise ValueError("etf_live_artifact_source_time_missing")
        elif self.source_time > self.fetched_at:
            raise ValueError("etf_live_artifact_time_order_invalid")
        if self.as_of > self.fetched_at:
            raise ValueError("etf_live_artifact_time_order_invalid")
        local_date = self.as_of.astimezone(_SHANGHAI_TIMEZONE).date()
        if (
            self.fetched_at.astimezone(_SHANGHAI_TIMEZONE).date() != local_date
            or (
                self.source_time is not None
                and self.source_time.astimezone(_SHANGHAI_TIMEZONE).date()
                != local_date
            )
        ):
            raise ValueError("etf_live_artifact_time_scope_invalid")
        if any(
            item.source_time.astimezone(_SHANGHAI_TIMEZONE).date() != local_date
            or item.fetched_at > self.fetched_at
            or item.source_time > self.source_time
            for item in self.records
        ):
            raise ValueError("etf_live_record_time_scope_invalid")
        # 工件没有权威完成时点，不在此使用请求 asOf 推断新鲜度。
        # 精确±5秒/90秒分类在 receipt.observedAt 绑定后逐条复算。
        expected_status = _quality_status(
            lock_state=self.lock_state,
            coverage=(self.returned_count / self.requested_count),
            missing_count=self.missing_count,
            failed_count=self.failed_count,
            stale_count=self.stale_count,
            source_ready=(self.source_health_status == "healthy"),
        )
        if self.status != expected_status:
            raise ValueError("etf_live_status_conflict")
        return self


def load_formal_shadow_run_receipt(raw: bytes) -> FormalShadowRunReceipt:
    payload = _strict_json_bytes(raw, "formal_shadow_receipt_invalid")
    try:
        return FormalShadowRunReceipt.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("formal_shadow_receipt_invalid") from exc


def load_formal_shadow_collection_policy(
    raw: bytes,
) -> FormalShadowCollectionPolicy:
    payload = _strict_json_bytes(
        raw,
        "formal_shadow_collection_policy_invalid",
    )
    try:
        return FormalShadowCollectionPolicy.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("formal_shadow_collection_policy_invalid") from exc


def _quality_status(
    *,
    lock_state: FormalLockState,
    coverage: float,
    missing_count: int,
    failed_count: int,
    stale_count: int,
    source_ready: Optional[bool] = None,
) -> FormalObservationStatus:
    if lock_state != "acquired" or failed_count:
        return "failed"
    if missing_count or coverage == 0:
        return "missing"
    if stale_count:
        return "stale"
    if source_ready is False:
        return "failed"
    return "ready"


def _validate_receipt_source(
    receipt_raw: bytes,
    source_raw: bytes,
    *,
    module: FormalModuleName,
    evaluated_at: datetime,
    collection_policy_raw: Optional[bytes],
) -> tuple[FormalShadowRunReceipt, FormalShadowCollectionPolicy]:
    _require_json_bytes(
        receipt_raw,
        "formal_shadow_receipt_json_bytes_required",
    )
    _require_json_bytes(
        source_raw,
        "formal_shadow_source_json_bytes_required",
    )
    if collection_policy_raw is None:
        raise ValueError("formal_shadow_collection_policy_required")
    _require_json_bytes(
        collection_policy_raw,
        "formal_shadow_collection_policy_json_bytes_required",
    )
    _require_evaluated_at(evaluated_at)
    receipt = load_formal_shadow_run_receipt(receipt_raw)
    policy = load_formal_shadow_collection_policy(collection_policy_raw)
    if receipt.module != module:
        raise ValueError("formal_shadow_receipt_module_mismatch")
    if receipt.observed_at > evaluated_at:
        raise ValueError("formal_shadow_receipt_from_future")
    if (
        evaluated_at - receipt.observed_at
        > timedelta(
            seconds=policy.maximum_collection_delay_seconds_by_module[module]
        )
    ):
        raise ValueError("formal_shadow_receipt_expired")
    freshness_reference = (
        receipt.observed_at
        if receipt.module == "etfObservation"
        else receipt.as_of
    )
    if (
        receipt.source_time is not None
        and receipt.source_ready
        and freshness_reference - receipt.source_time
        > timedelta(
            seconds=policy.maximum_source_age_seconds_by_module[module]
        )
    ):
        raise ValueError("formal_shadow_source_expired")
    if receipt.source_artifact_sha256 != _sha256(source_raw):
        raise ValueError("formal_shadow_source_artifact_sha_mismatch")
    return receipt, policy


def _observation(
    receipt: FormalShadowRunReceipt,
    *,
    receipt_raw: bytes,
    source_raw: bytes,
    semantic: Mapping[str, Any],
    supporting_raw: Optional[bytes] = None,
    admission_raw: Optional[bytes] = None,
    collection_policy_raw: bytes,
    collection_policy: FormalShadowCollectionPolicy,
) -> FormalShadowObservation:
    evidence = {
        "receiptContractId": receipt.contract_id,
        "receiptRawSha256": _sha256(receipt_raw),
        "receipt": receipt.model_dump(mode="json", by_alias=True),
        "collectionPolicyRawSha256": _sha256(collection_policy_raw),
        "collectionPolicy": collection_policy.model_dump(
            mode="json",
            by_alias=True,
        ),
        "sourceArtifactRawSha256": _sha256(source_raw),
        "supportingArtifactRawSha256": (
            _sha256(supporting_raw) if supporting_raw is not None else None
        ),
        "admissionArtifactRawSha256": (
            _sha256(admission_raw) if admission_raw is not None else None
        ),
        "authoritativeSemantic": semantic,
    }
    status = _quality_status(
        lock_state=receipt.lock_state,
        coverage=receipt.coverage,
        missing_count=receipt.missing_count,
        failed_count=receipt.failed_count,
        stale_count=receipt.stale_count,
        source_ready=receipt.source_ready,
    )
    return FormalShadowObservation(
        module=receipt.module,
        runId=receipt.run_id,
        observedAt=receipt.observed_at,
        sourceTime=receipt.source_time,
        fetchedAt=receipt.fetched_at,
        coverage=receipt.coverage,
        missingCount=receipt.missing_count,
        failedCount=receipt.failed_count,
        staleCount=receipt.stale_count,
        lockState=receipt.lock_state,
        durationMs=receipt.duration_ms,
        sourceReady=receipt.source_ready,
        evidenceSha256=_canonical_sha256(evidence),
        observationStatus=status,
    )


def _validate_common_source_time(
    receipt: FormalShadowRunReceipt,
    *,
    run_id: Any,
    as_of_raw: Any,
    reason: str,
) -> datetime:
    try:
        as_of = datetime.fromisoformat(as_of_raw)
        _aware(as_of, "formal_shadow_source_as_of")
    except (TypeError, ValueError) as exc:
        raise ValueError(reason) from exc
    if run_id != receipt.run_id or as_of != receipt.as_of:
        raise ValueError(reason)
    if as_of > receipt.observed_at:
        raise ValueError(reason)
    return as_of


def _validate_sector_snapshot(
    raw: bytes,
    *,
    receipt: FormalShadowRunReceipt,
) -> Mapping[str, Any]:
    payload = _strict_json_bytes(raw, "trend_sector_snapshot_unverified")
    expected_hash = payload.get("snapshotSha256")
    semantic = dict(payload)
    semantic.pop("snapshotSha256", None)
    industry_codes = payload.get("industryCodes")
    records = payload.get("records")
    try:
        observed_at = datetime.fromisoformat(payload["observedAt"])
        _aware(observed_at, "trend_sector_observed_at")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("trend_sector_snapshot_unverified") from exc
    if (
        not isinstance(industry_codes, list)
        or not industry_codes
        or any(not _nonblank(value) for value in industry_codes)
        or len(industry_codes) != len(set(industry_codes))
        or not isinstance(records, list)
        or len(records) != len(industry_codes)
    ):
        raise ValueError("trend_sector_snapshot_unverified")
    if any((
        payload.get("contractId") != SECTOR_STATE_SNAPSHOT_CONTRACT_ID,
        not _SHA256_PATTERN.fullmatch(
            str(payload.get("classificationDocumentSha256") or "")
        ),
        payload.get("ruleVersion") != SECTOR_RULE_VERSION,
        payload.get("transitionPolicyVersion")
        != SECTOR_STATE_TRANSITION_POLICY_VERSION,
        not _nonblank(payload.get("thresholdSetId")),
        not _nonblank(payload.get("approvalId")),
        observed_at != receipt.as_of,
        payload.get("lastQuoteBatchId") != f"{receipt.run_id}-quotes",
        not isinstance(expected_hash, str),
        expected_hash != _canonical_sha256(semantic),
    )):
        raise ValueError("trend_sector_snapshot_unverified")
    allowed_states = {
        "unclassified", "observe", "startup", "confirmed", "accelerating",
        "divergence", "reflow", "retreat", "invalid",
    }
    for index, record in enumerate(records):
        if any((
            not isinstance(record, dict),
            isinstance(record, dict)
            and record.get("industryCode") != industry_codes[index],
            isinstance(record, dict)
            and record.get("state") not in allowed_states,
            isinstance(record, dict)
            and not _is_strict_int(record.get("pendingCount")),
            isinstance(record, dict)
            and not isinstance(record.get("cooldowns"), dict),
        )):
            raise ValueError("trend_sector_snapshot_unverified")
    return {
        "contractId": payload["contractId"],
        "classificationDocumentSha256": payload[
            "classificationDocumentSha256"
        ],
        "snapshotSha256": expected_hash,
        "industryCodes": industry_codes,
        "ruleVersion": payload["ruleVersion"],
        "transitionPolicyVersion": payload["transitionPolicyVersion"],
        "thresholdSetId": payload["thresholdSetId"],
        "approvalId": payload["approvalId"],
        "observedAt": observed_at.isoformat(),
        "lastQuoteBatchId": payload["lastQuoteBatchId"],
        "records": records,
    }


def _market_feature_snapshot_sha256(value: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("trend_market_snapshot_unverified") from exc
    return _sha256(raw)


def _replay_market_research_state(
    value: Any,
    *,
    receipt: FormalShadowRunReceipt,
    snapshot_evidence: Any,
    sector_quote_batch_id: str,
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    required_keys = {
        "contractId", "status", "radarRunId", "asOf", "state", "metrics",
        "reasons", "snapshotSha256", "ruleVersion", "researchUsable",
        "formalUsable",
    }
    if not isinstance(value, dict) or set(value) != required_keys:
        raise ValueError("trend_market_state_unverified")
    snapshot_evidence_keys = {
        "contractId", "snapshotSha256", "snapshot",
    }
    if (
        not isinstance(snapshot_evidence, dict)
        or set(snapshot_evidence) != snapshot_evidence_keys
        or snapshot_evidence.get("contractId")
        != MARKET_FEATURE_SNAPSHOT_EVIDENCE_CONTRACT_ID
        or not isinstance(snapshot_evidence.get("snapshot"), dict)
        or not _SHA256_PATTERN.fullmatch(
            str(snapshot_evidence.get("snapshotSha256") or "")
        )
        or snapshot_evidence.get("snapshotSha256")
        != _market_feature_snapshot_sha256(snapshot_evidence["snapshot"])
    ):
        raise ValueError("trend_market_snapshot_unverified")
    try:
        snapshot = MarketFeatureSnapshot.model_validate(
            snapshot_evidence["snapshot"]
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("trend_market_snapshot_unverified") from exc
    normalized_snapshot = snapshot.model_dump(mode="json", by_alias=True)
    if (
        _market_feature_snapshot_sha256(normalized_snapshot)
        != snapshot_evidence["snapshotSha256"]
        or snapshot.radar_run_id != receipt.run_id
        or snapshot.as_of != receipt.as_of
        or snapshot.source_time != receipt.source_time
        or snapshot.fetched_at != receipt.fetched_at
        or snapshot.quote_batch_id != sector_quote_batch_id
    ):
        raise ValueError("trend_market_snapshot_unverified")
    replayed = produce_market_research_state(
        snapshot,
        policy=DEFAULT_MARKET_RESEARCH_STATE_POLICY,
    )
    if (
        replayed.status is not MarketResearchStateStatus.READY
        or _canonical_sha256(dict(replayed.to_evidence()))
        != _canonical_sha256(value)
    ):
        raise ValueError("trend_market_state_unverified")
    return dict(replayed.to_evidence()), {
        "contractId": snapshot_evidence["contractId"],
        "snapshotSha256": snapshot_evidence["snapshotSha256"],
        "snapshot": normalized_snapshot,
    }


def adapt_trend_rotation_observation(
    receipt_json_bytes: bytes,
    source_artifact_json_bytes: bytes,
    sector_snapshot_json_bytes: bytes,
    *,
    evaluated_at: datetime,
    collection_policy_json_bytes: Optional[bytes] = None,
) -> FormalShadowObservation:
    _require_json_bytes(
        sector_snapshot_json_bytes,
        "formal_shadow_supporting_json_bytes_required",
    )
    receipt, collection_policy = _validate_receipt_source(
        receipt_json_bytes,
        source_artifact_json_bytes,
        module="trendRotation",
        evaluated_at=evaluated_at,
        collection_policy_raw=collection_policy_json_bytes,
    )
    artifact = _strict_json_bytes(
        source_artifact_json_bytes,
        "trend_stage6_artifact_unverified",
    )
    try:
        state = artifact["marketResearchState"]
        prepared = artifact["prepared"]
        tradability = artifact["tradability"]
    except KeyError as exc:
        raise ValueError("trend_stage6_artifact_unverified") from exc
    if not all(isinstance(value, dict) for value in (state, prepared, tradability)):
        raise ValueError("trend_stage6_artifact_unverified")
    as_of = _validate_common_source_time(
        receipt,
        run_id=state.get("radarRunId"),
        as_of_raw=state.get("asOf"),
        reason="trend_stage6_identity_mismatch",
    )
    if any((
        prepared.get("contractId")
        != "radar-leader-phase6-prepared-historical-inputs-v1",
        prepared.get("radarRunId") != receipt.run_id,
        tradability.get("contractId")
        != "radar-leader-tradability-live-acceptance-v1",
        tradability.get("status") != "completed",
        tradability.get("radarRunId") != receipt.run_id,
        tradability.get("asOf") != state.get("asOf"),
        not _is_closed_gate(tradability.get("gate")),
        not _nonblank(artifact.get("sectorStateSnapshotPath")),
        not _is_closed_gate(artifact.get("gate")),
    )):
        raise ValueError("trend_stage6_artifact_unverified")
    sector = _validate_sector_snapshot(
        sector_snapshot_json_bytes,
        receipt=receipt,
    )
    market_state, market_snapshot_evidence = _replay_market_research_state(
        state,
        receipt=receipt,
        snapshot_evidence=artifact.get("marketFeatureSnapshotEvidence"),
        sector_quote_batch_id=sector["lastQuoteBatchId"],
    )
    if (
        prepared.get("classificationDocumentSha256")
        != sector["classificationDocumentSha256"]
    ):
        raise ValueError("trend_classification_identity_mismatch")
    if (
        receipt.expected_count != len(sector["industryCodes"])
        or receipt.observed_count != len(sector["records"])
        or receipt.missing_count != 0
        or receipt.failed_count != 0
    ):
        raise ValueError("trend_receipt_coverage_mismatch")
    semantic = {
        "module": receipt.module,
        "runId": receipt.run_id,
        "asOf": as_of.isoformat(),
        "marketResearchState": market_state,
        "marketFeatureSnapshotEvidence": market_snapshot_evidence,
        "sectorStateSnapshot": sector,
    }
    return _observation(
        receipt,
        receipt_raw=receipt_json_bytes,
        source_raw=source_artifact_json_bytes,
        supporting_raw=sector_snapshot_json_bytes,
        semantic=semantic,
        collection_policy_raw=collection_policy_json_bytes,
        collection_policy=collection_policy,
    )


def _leader_reasons(value: Any) -> Tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not _nonblank(reason) for reason in value)
    ):
        raise ValueError("leader_stage6_nested_unverified")
    if any("source_failed" in reason.lower() for reason in value):
        raise ValueError("leader_source_failed_nested")
    return tuple(value)


def _validate_leader_business_summary(
    value: Any,
    *,
    plan_id: str,
    candidate_count: int,
) -> Mapping[str, Any]:
    expected_keys = {
        "contractId", "status", "candidatePlanId", "candidateCount",
        "readyCount", "missingCount", "sourceFailedCount",
        "sourceUnverifiedCount", "reusedCount", "reasons", "packetPath",
        "deliveryPacketPath", "productionFrozenInputsReady", "gate",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("leader_stage6_nested_unverified")
    count_keys = (
        "readyCount", "missingCount", "sourceFailedCount",
        "sourceUnverifiedCount",
    )
    counts = tuple(value.get(key) for key in count_keys)
    reused_count = value.get("reusedCount")
    reasons = _leader_reasons(value.get("reasons"))
    if (
        any(not _is_strict_int(count) or count < 0 for count in counts)
        or not _is_strict_int(reused_count)
    ):
        raise ValueError("leader_stage6_nested_unverified")
    if any((
        value.get("contractId")
        != "radar-leader-business-automatic-evidence-v1",
        value.get("candidatePlanId") != plan_id,
        value.get("candidateCount") != candidate_count,
        not 0 <= reused_count <= counts[0],
        sum(counts) != candidate_count,
        not _is_closed_gate(value.get("gate")),
        any(
            path is not None and not _nonblank(path)
            for path in (
                value.get("packetPath"), value.get("deliveryPacketPath")
            )
        ),
    )):
        raise ValueError("leader_stage6_nested_unverified")
    ready_count, missing_count, failed_count, unverified_count = counts
    if failed_count > 0 or value.get("status") == "source_failed":
        raise ValueError("leader_source_failed_nested")
    expected_status = (
        "source_unverified" if unverified_count > 0
        else "missing" if missing_count > 0
        else "ready"
    )
    if any((
        value.get("status") != expected_status,
        value.get("productionFrozenInputsReady")
        is not (expected_status == "ready"),
        expected_status == "ready" and reasons != (),
    )):
        raise ValueError("leader_stage6_nested_unverified")
    return value


def _validate_leader_material_summary(
    value: Any,
    *,
    receipt: FormalShadowRunReceipt,
    plan_id: str,
    candidate_count: int,
    require_all_pending: bool,
) -> Mapping[str, Any]:
    expected_keys = {
        "contractId", "status", "radarRunId", "asOf", "candidatePlanId",
        "candidateCount", "issuerStatus", "queueStatus", "reviewSummary",
        "reasons", "gate",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("leader_stage6_nested_unverified")
    _validate_common_source_time(
        receipt,
        run_id=value.get("radarRunId"),
        as_of_raw=value.get("asOf"),
        reason="leader_stage6_nested_unverified",
    )
    summary = value.get("reviewSummary")
    summary_keys = {
        "pendingReview", "missing", "sourceFailed", "sourceUnverified",
    }
    if not isinstance(summary, dict) or set(summary) != summary_keys:
        raise ValueError("leader_stage6_nested_unverified")
    counts = tuple(summary.get(key) for key in summary_keys)
    reasons = _leader_reasons(value.get("reasons"))
    if summary.get("sourceFailed") != 0:
        raise ValueError("leader_source_failed_nested")
    if any((
        value.get("contractId")
        != "radar-leader-business-material-live-acceptance-v1",
        value.get("status") != "completed",
        value.get("candidatePlanId") != plan_id,
        value.get("candidateCount") != candidate_count,
        value.get("issuerStatus") == "source_failed",
        value.get("queueStatus") == "source_failed",
        any(not _is_strict_int(count) or count < 0 for count in counts),
        all(_is_strict_int(count) for count in counts)
        and sum(counts) != candidate_count,
        not _is_closed_gate(value.get("gate")),
        require_all_pending and summary.get("pendingReview") != candidate_count,
        require_all_pending and any(
            summary.get(key) != 0
            for key in ("missing", "sourceFailed", "sourceUnverified")
        ),
        require_all_pending and reasons != (),
    )):
        raise ValueError("leader_stage6_nested_unverified")
    return value


def _validate_leader_readiness(
    value: Any,
    *,
    receipt: FormalShadowRunReceipt,
    plan_id: str,
    candidate_count: int,
) -> Mapping[str, Any]:
    try:
        readiness = value["readiness"]
        assembly = readiness["assembly"]
        acceptance = readiness["acceptance"]
        components = assembly["components"]
        component_statuses = acceptance["componentStatuses"]
    except (KeyError, TypeError) as exc:
        raise ValueError("leader_stage6_nested_unverified") from exc
    if not all(isinstance(item, dict) for item in (
        value, readiness, assembly, acceptance,
    )) or not isinstance(components, list) or not isinstance(
        component_statuses, list
    ):
        raise ValueError("leader_stage6_nested_unverified")
    if any((
        set(value) != {
            "contractId", "status", "radarRunId", "candidatePlanId", "asOf",
            "candidateCount", "validEmptyResult", "reasons", "readiness",
            "gate",
        },
        set(readiness) != {"contractId", "assembly", "acceptance", "gate"},
        set(assembly) != {
            "contractId", "status", "radarRunId", "candidatePlanId",
            "candidateCount", "reasons", "healthReasons", "components", "gate",
        },
        set(acceptance) != {
            "contractId", "status", "candidateCount", "missingComponents",
            "reasons", "componentStatuses", "gate",
        },
    )):
        raise ValueError("leader_stage6_nested_unverified")
    _validate_common_source_time(
        receipt,
        run_id=value.get("radarRunId"),
        as_of_raw=value.get("asOf"),
        reason="leader_stage6_nested_unverified",
    )
    names = (
        "sector_rule", "history", "business_catalyst", "tradability", "risk",
    )
    if any((
        value.get("contractId")
        != "radar-leader-phase6-live-source-readiness-v1",
        value.get("status") != "ready_for_review",
        value.get("candidatePlanId") != plan_id,
        value.get("candidateCount") != candidate_count,
        value.get("validEmptyResult") is not False,
        value.get("reasons") != [],
        not _is_closed_gate(value.get("gate")),
        readiness.get("contractId")
        != "radar-leader-phase6-production-readiness-v1",
        not _is_closed_gate(readiness.get("gate")),
        assembly.get("contractId")
        != "radar-leader-formal-research-runtime-assembly-v1",
        assembly.get("status") != "ready",
        assembly.get("radarRunId") != receipt.run_id,
        assembly.get("candidatePlanId") != plan_id,
        assembly.get("candidateCount") != candidate_count,
        assembly.get("reasons") != [],
        assembly.get("healthReasons") != [],
        not _is_closed_gate(assembly.get("gate")),
        acceptance.get("contractId")
        != "radar-leader-formal-research-production-acceptance-v1",
        acceptance.get("status") != "ready_for_review",
        acceptance.get("candidateCount") != candidate_count,
        acceptance.get("missingComponents") != [],
        acceptance.get("reasons") != [],
        not _is_closed_gate(acceptance.get("gate")),
        len(components) != len(names),
        len(component_statuses) != len(names),
    )):
        raise ValueError("leader_stage6_nested_unverified")
    for index, name in enumerate(names):
        component = components[index]
        status = component_statuses[index]
        if not isinstance(component, dict) or not isinstance(status, dict):
            raise ValueError("leader_stage6_nested_unverified")
        if (
            set(component) != {
                "name", "status", "candidateCount", "readyCount", "reasons",
            }
            or set(status) != {"name", "status"}
        ):
            raise ValueError("leader_stage6_nested_unverified")
        _leader_reasons(component.get("reasons"))
        if component.get("status") == "source_failed":
            raise ValueError("leader_source_failed_nested")
        if any((
            component.get("name") != name,
            component.get("status") != "ready",
            component.get("candidateCount") != candidate_count,
            component.get("readyCount") != candidate_count,
            component.get("reasons") != [],
            status != {"name": name, "status": "ready"},
        )):
            raise ValueError("leader_stage6_nested_unverified")
    return value


def _validate_leader_industry_gate(
    value: Any,
    *,
    receipt: FormalShadowRunReceipt,
    parent_plan_id: str,
    qualified_plan_id: str,
    industry_scope_id: str,
    qualified_scope: Tuple[Tuple[str, str], ...],
) -> Mapping[str, Any]:
    expected_keys = {
        "contractId", "status", "radarRunId", "asOf",
        "parentCandidatePlanId", "candidatePlanId", "candidateCount",
        "industryScopeSnapshotId", "evidenceReadyCount", "policyAudit",
        "items", "reasons", "gate",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("leader_stage6_nested_unverified")
    _validate_common_source_time(
        receipt,
        run_id=value.get("radarRunId"),
        as_of_raw=value.get("asOf"),
        reason="leader_stage6_nested_unverified",
    )
    reasons = _leader_reasons(value.get("reasons"))
    audit = value.get("policyAudit")
    items = value.get("items")
    if not isinstance(audit, dict) or not isinstance(items, list):
        raise ValueError("leader_stage6_nested_unverified")
    if set(audit) != {
        "contractId", "status", "qualitativeRequirements",
        "requirementSourceReferences", "requiredPolicyFields",
        "observedApprovalContractId", "observedApprovalId", "reasons",
        "approved",
    }:
        raise ValueError("leader_stage6_nested_unverified")
    audit_reasons = _leader_reasons(audit.get("reasons"))
    audit_lists = tuple(
        audit.get(key) for key in (
            "qualitativeRequirements", "requirementSourceReferences",
            "requiredPolicyFields",
        )
    )
    if any((
        value.get("contractId")
        != "radar-leader-formal-industry-gate-evidence-v1",
        value.get("status") != "ready",
        value.get("parentCandidatePlanId") != parent_plan_id,
        value.get("candidatePlanId") != qualified_plan_id,
        value.get("candidateCount") != len(qualified_scope),
        value.get("industryScopeSnapshotId") != industry_scope_id,
        value.get("evidenceReadyCount") != len(qualified_scope),
        not _is_closed_gate(value.get("gate")),
        audit.get("contractId")
        != "radar-leader-formal-industry-gate-policy-audit-v1",
        audit.get("status") != "unapproved",
        audit.get("approved") is not False,
        audit.get("observedApprovalContractId") is not None,
        audit.get("observedApprovalId") is not None,
        any(
            not isinstance(values, list)
            or not values
            or any(not _nonblank(item) for item in values)
            for values in audit_lists
        ),
        not audit_reasons,
        reasons != audit_reasons,
        len(items) != len(qualified_scope),
    )):
        raise ValueError("leader_stage6_nested_unverified")
    allowed_states = {
        "unclassified", "observe", "startup", "confirmed", "accelerating",
        "divergence", "reflow", "retreat", "invalid",
    }
    for index, (item, identity) in enumerate(zip(items, qualified_scope)):
        if not isinstance(item, dict) or set(item) != {
            "index", "symbol", "industryCode", "industryState",
            "sectorRuleEvidenceReady", "crossSectionEvidenceReady",
            "evidenceReady", "reasons", "formalGatePassed",
        }:
            raise ValueError("leader_stage6_nested_unverified")
        item_reasons = _leader_reasons(item.get("reasons"))
        if any((
            item.get("index") != index,
            (item.get("symbol"), item.get("industryCode")) != identity,
            item.get("industryState") not in allowed_states,
            item.get("sectorRuleEvidenceReady") is not True,
            item.get("crossSectionEvidenceReady") is not True,
            item.get("evidenceReady") is not True,
            item.get("formalGatePassed") is not False,
            item_reasons != audit_reasons,
        )):
            raise ValueError("leader_stage6_nested_unverified")
    return value


def _validate_leader_risk_delivery(
    value: Any,
    *,
    receipt: FormalShadowRunReceipt,
    plan_id: str,
    candidate_count: int,
) -> Mapping[str, Any]:
    expected_keys = {
        "contractId", "status", "realPocStatus", "candidatePlanId",
        "radarRunId", "quoteBatchId", "asOf", "windowFrom", "windowUntil",
        "requestCount", "fetchedPageCount", "categoryCount",
        "candidateScopeCount", "shardCount", "reasons", "lifecycleStatus",
        "projectionStatus", "readyProjectionCount", "gate",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("leader_stage6_nested_unverified")
    try:
        as_of = datetime.fromisoformat(value["asOf"])
        _aware(as_of, "leader_risk_delivery_as_of")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("leader_stage6_nested_unverified") from exc
    reasons = _leader_reasons(value.get("reasons"))
    count_keys = (
        "requestCount", "fetchedPageCount", "categoryCount",
        "candidateScopeCount", "shardCount", "readyProjectionCount",
    )
    counts = {key: value.get(key) for key in count_keys}
    status_values = tuple(
        value.get(key) for key in (
            "status", "realPocStatus", "lifecycleStatus", "projectionStatus",
        )
    )
    if any(status == "source_failed" for status in status_values):
        raise ValueError("leader_source_failed_nested")
    if any((
        value.get("contractId") != "radar-leader-risk-lifecycle-delivery-v1",
        value.get("candidatePlanId") != plan_id,
        value.get("radarRunId") != receipt.run_id,
        value.get("quoteBatchId") != f"{receipt.run_id}-quotes",
        as_of.astimezone(_SHANGHAI_TIMEZONE).date()
        != receipt.as_of.astimezone(_SHANGHAI_TIMEZONE).date(),
        not _nonblank(value.get("windowFrom")),
        not _nonblank(value.get("windowUntil")),
        any(not _is_strict_int(count) or count < 0 for count in counts.values()),
        counts["candidateScopeCount"] != candidate_count,
        _is_strict_int(counts["fetchedPageCount"])
        and _is_strict_int(counts["requestCount"])
        and counts["fetchedPageCount"] > counts["requestCount"],
        _is_strict_int(counts["readyProjectionCount"])
        and counts["readyProjectionCount"] > candidate_count,
        any(not _nonblank(status) for status in status_values),
        value.get("status") == "ready" and reasons != (),
        value.get("status") != "ready" and not reasons,
        not _is_closed_gate(value.get("gate")),
    )):
        raise ValueError("leader_stage6_nested_unverified")
    return value


def _validate_leader_nested_evidence(
    *,
    collection: Mapping[str, Any],
    qualification: Mapping[str, Any],
    receipt: FormalShadowRunReceipt,
    parent_plan_id: str,
    qualified_plan_id: str,
    parent_count: int,
    qualified_count: int,
    excluded_items: list[Any],
    industry_gate: Any,
    industry_scope_id: str,
    qualified_scope: Tuple[Tuple[str, str], ...],
) -> Mapping[str, Any]:
    qualified_business = _validate_leader_business_summary(
        qualification.get("businessAutomatic"),
        plan_id=qualified_plan_id,
        candidate_count=qualified_count,
    )
    collection_business = _validate_leader_business_summary(
        collection.get("businessAutomatic"),
        plan_id=qualified_plan_id,
        candidate_count=qualified_count,
    )
    if qualified_business != collection_business:
        raise ValueError("leader_stage6_nested_unverified")
    if any((
        qualified_business.get("readyCount") != qualified_count,
        qualified_business.get("missingCount") != 0,
        qualified_business.get("sourceUnverifiedCount") != 0,
    )):
        raise ValueError("leader_stage6_nested_unverified")

    verification_business = _validate_leader_business_summary(
        collection.get("verificationBusinessAutomatic"),
        plan_id=parent_plan_id,
        candidate_count=parent_count,
    )
    status_counts = {
        "ready": qualified_count,
        "missing": sum(item.get("status") == "missing" for item in excluded_items),
        "source_unverified": sum(
            item.get("status") == "source_unverified" for item in excluded_items
        ),
    }
    expected_reasons = tuple(dict.fromkeys(
        reason
        for item in excluded_items
        for reason in item.get("reasons", [])
    ))
    if any((
        verification_business.get("readyCount") != status_counts["ready"],
        verification_business.get("missingCount") != status_counts["missing"],
        verification_business.get("sourceUnverifiedCount")
        != status_counts["source_unverified"],
        tuple(verification_business.get("reasons", ())) != expected_reasons,
    )):
        raise ValueError("leader_stage6_nested_unverified")

    material = _validate_leader_material_summary(
        collection.get("materialAcceptance"),
        receipt=receipt,
        plan_id=qualified_plan_id,
        candidate_count=qualified_count,
        require_all_pending=True,
    )
    verification_material = _validate_leader_material_summary(
        collection.get("verificationMaterialAcceptance"),
        receipt=receipt,
        plan_id=parent_plan_id,
        candidate_count=parent_count,
        require_all_pending=False,
    )
    readiness = _validate_leader_readiness(
        collection.get("readiness"),
        receipt=receipt,
        plan_id=qualified_plan_id,
        candidate_count=qualified_count,
    )
    validated_industry_gate = _validate_leader_industry_gate(
        industry_gate,
        receipt=receipt,
        parent_plan_id=parent_plan_id,
        qualified_plan_id=qualified_plan_id,
        industry_scope_id=industry_scope_id,
        qualified_scope=qualified_scope,
    )
    risk_delivery = _validate_leader_risk_delivery(
        collection.get("riskDelivery"),
        receipt=receipt,
        plan_id=qualified_plan_id,
        candidate_count=qualified_count,
    )
    return {
        "qualificationBusinessAutomatic": qualified_business,
        "verificationBusinessAutomatic": verification_business,
        "materialAcceptance": material,
        "verificationMaterialAcceptance": verification_material,
        "readiness": readiness,
        "industryGateEvidence": validated_industry_gate,
        "riskDelivery": risk_delivery,
    }


def _validate_leader_partition(
    artifact: Mapping[str, Any],
    receipt: FormalShadowRunReceipt,
) -> Mapping[str, Any]:
    try:
        selection = artifact["evidenceCandidateSelection"]
        plan = selection["evidencePlan"]
        collection = artifact["collection"]
        qualification = collection["qualification"]
        review = collection["stateDecisionReview"]
        parent_count = review["parentCandidateCount"]
        qualified_count = review["qualifiedCandidateCount"]
        excluded_count = review["excludedCandidateCount"]
        plan_items = plan["items"]
        review_items = review["items"]
        excluded_items = qualification["excludedItems"]
        industry_gate = review["industryGateEvidence"]
    except (KeyError, TypeError) as exc:
        raise ValueError("leader_stage6_artifact_unverified") from exc
    values = (parent_count, qualified_count, excluded_count)
    if not all(isinstance(value, dict) for value in (
        selection, plan, collection, qualification, review,
    )):
        raise ValueError("leader_stage6_artifact_unverified")
    if any((
        set(selection) != {
            "contractId", "status", "preliminaryCandidatePlanId",
            "preliminaryCandidateCount", "candidatePlanId", "candidateCount",
            "industryScopeSnapshotId", "previousStateSnapshotId", "reasons",
            "evidencePlan", "gate",
        },
        set(plan) != {
            "contractId", "status", "preliminaryCandidatePlanId",
            "preliminaryCandidateCount", "candidatePlanId", "candidateCount",
            "selectionPolicyId", "reasons", "items", "gate",
        },
        set(collection) != {
            "contractId", "status", "validEmptyResult",
            "candidateSourcePacketSha256", "reasons", "qualification",
            "verificationCandidateSourcePacketSha256",
            "verificationMaterialAcceptance", "verificationBusinessAutomatic",
            "stateDecisionReview", "materialAcceptance", "businessAutomatic",
            "riskDelivery", "readiness", "gate",
        },
        set(qualification) != {
            "contractId", "status", "qualificationId",
            "parentCandidatePlanId", "parentCandidateCount",
            "qualifiedCandidatePlanId", "qualifiedCandidateCount",
            "excludedCandidateCount", "excludedItems", "reasons",
            "businessAutomatic", "gate",
        },
        set(review) != {
            "contractId", "status", "radarRunId", "asOf",
            "parentCandidatePlanId", "parentCandidateCount",
            "qualifiedCandidatePlanId", "qualifiedCandidateCount",
            "excludedCandidateCount", "qualificationId",
            "industryGateEvidence", "reasons", "items", "gate",
        },
    )):
        raise ValueError("leader_stage6_artifact_unverified")
    if any(
        value.get("reasons") != []
        for value in (selection, plan, collection)
    ):
        raise ValueError("leader_stage6_reasons_unverified")
    if (
        not all(_is_strict_int(value) and value >= 0 for value in values)
        or parent_count <= 0
        or qualified_count + excluded_count != parent_count
        or not isinstance(plan_items, list)
        or not isinstance(review_items, list)
        or not isinstance(excluded_items, list)
        or len(plan_items) != parent_count
        or len(review_items) != parent_count
        or len(excluded_items) != excluded_count
    ):
        raise ValueError("leader_stage6_artifact_unverified")
    if any((
        selection.get("contractId")
        != "radar-leader-evidence-candidate-acceptance-v1",
        selection.get("status") != "ready",
        plan.get("contractId") != "radar-leader-evidence-candidate-plan-v1",
        plan.get("status") != "ready",
        collection.get("contractId")
        != "radar-leader-phase6-live-source-collection-v1",
        collection.get("status") != "ready_for_review",
        collection.get("validEmptyResult") is not False,
        qualification.get("contractId")
        != "radar-leader-evidence-qualification-v1",
        qualification.get("status") != "ready",
        review.get("contractId")
        != "radar-leader-phase6-state-decision-review-v1",
        review.get("status") != "ready_for_review",
        not all(_is_closed_gate(value.get("gate")) for value in (
            artifact, selection, plan, collection, qualification, review,
        )),
    )):
        raise ValueError("leader_stage6_artifact_unverified")
    _validate_common_source_time(
        receipt,
        run_id=review.get("radarRunId"),
        as_of_raw=review.get("asOf"),
        reason="leader_stage6_identity_mismatch",
    )
    parent_plan_id = review.get("parentCandidatePlanId")
    qualified_plan_id = review.get("qualifiedCandidatePlanId")
    qualification_id = review.get("qualificationId")
    if any((
        not _nonblank(parent_plan_id),
        not _nonblank(qualified_plan_id),
        not _SHA256_PATTERN.fullmatch(str(qualification_id or "")),
        selection.get("candidatePlanId") != parent_plan_id,
        selection.get("candidateCount") != parent_count,
        not _nonblank(selection.get("preliminaryCandidatePlanId")),
        not _is_strict_int(selection.get("preliminaryCandidateCount")),
        _is_strict_int(selection.get("preliminaryCandidateCount"))
        and selection.get("preliminaryCandidateCount") < parent_count,
        plan.get("preliminaryCandidatePlanId")
        != selection.get("preliminaryCandidatePlanId"),
        plan.get("preliminaryCandidateCount")
        != selection.get("preliminaryCandidateCount"),
        not _nonblank(selection.get("industryScopeSnapshotId")),
        not _nonblank(selection.get("previousStateSnapshotId")),
        plan.get("candidatePlanId") != parent_plan_id,
        plan.get("candidateCount") != parent_count,
        not _nonblank(plan.get("selectionPolicyId")),
        qualification.get("qualificationId") != qualification_id,
        qualification.get("parentCandidatePlanId") != parent_plan_id,
        qualification.get("parentCandidateCount") != parent_count,
        qualification.get("qualifiedCandidatePlanId") != qualified_plan_id,
        qualification.get("qualifiedCandidateCount") != qualified_count,
        qualification.get("excludedCandidateCount") != excluded_count,
        review.get("qualifiedCandidatePlanId") != qualified_plan_id,
        qualification.get("reasons") != [],
        review.get("reasons") != [],
    )):
        raise ValueError("leader_stage6_identity_mismatch")
    qualified_indexes = set()
    qualified_scope_by_index: Dict[int, Tuple[str, str]] = {}
    excluded_by_index: Dict[int, Mapping[str, Any]] = {}
    symbols = set()
    for index, (plan_item, review_item) in enumerate(zip(plan_items, review_items)):
        if not isinstance(plan_item, dict) or not isinstance(review_item, dict):
            raise ValueError("leader_stage6_partition_unverified")
        symbol = plan_item.get("symbol")
        industry_code = plan_item.get("industryCode")
        status = review_item.get("qualificationStatus")
        candidate_index = review_item.get("qualifiedCandidateIndex")
        reasons = review_item.get("reasons")
        if (
            not _is_strict_int(plan_item.get("index"))
            or not _is_strict_int(review_item.get("index"))
            or not isinstance(symbol, str)
            or _SYMBOL_PATTERN.fullmatch(symbol) is None
            or not _nonblank(industry_code)
        ):
            raise ValueError("leader_stage6_partition_unverified")
        if set(plan_item) != {
            "index", "symbol", "industryCode", "parentIndex",
            "selectionKind", "researchPartialScore", "previousState",
        } or any((
            not _is_strict_int(plan_item.get("parentIndex")),
            _is_strict_int(plan_item.get("parentIndex"))
            and plan_item.get("parentIndex") < 0,
            plan_item.get("selectionKind")
            not in {"new_candidate", "incumbent"},
            isinstance(plan_item.get("researchPartialScore"), bool),
            not isinstance(plan_item.get("researchPartialScore"), (int, float)),
            isinstance(plan_item.get("researchPartialScore"), (int, float))
            and not math.isfinite(float(plan_item["researchPartialScore"])),
            plan_item.get("previousState") is not None
            and not isinstance(plan_item.get("previousState"), dict),
        )):
            raise ValueError("leader_stage6_partition_unverified")
        if set(review_item) != {
            "index", "symbol", "industryCode", "qualificationStatus",
            "qualifiedCandidateIndex", "reviewEligible",
            "firstRejectionReason", "reasons",
        }:
            raise ValueError("leader_stage6_partition_unverified")
        if any((
            plan_item.get("index") != index,
            review_item.get("index") != index,
            symbol in symbols,
            review_item.get("symbol") != symbol,
            review_item.get("industryCode") != industry_code,
            status not in {"qualified", "excluded"},
            not isinstance(reasons, list),
            not reasons,
            isinstance(reasons, list)
            and any(not _nonblank(reason) for reason in reasons),
            review_item.get("firstRejectionReason")
            != (reasons[0] if isinstance(reasons, list) and reasons else None),
        )):
            raise ValueError("leader_stage6_partition_unverified")
        _leader_reasons(reasons)
        symbols.add(symbol)
        if status == "qualified":
            if not _is_strict_int(candidate_index):
                raise ValueError("leader_stage6_partition_unverified")
            if any((
                review_item.get("reviewEligible") is not True,
                not 0 <= candidate_index < qualified_count,
                candidate_index in qualified_indexes,
            )):
                raise ValueError("leader_stage6_partition_unverified")
            qualified_indexes.add(candidate_index)
            qualified_scope_by_index[candidate_index] = (symbol, industry_code)
        else:
            if (
                review_item.get("reviewEligible") is not False
                or candidate_index is not None
            ):
                raise ValueError("leader_stage6_partition_unverified")
            excluded_by_index[index] = review_item
    if qualified_indexes != set(range(qualified_count)):
        raise ValueError("leader_stage6_partition_unverified")
    seen_excluded = set()
    for item in excluded_items:
        if not isinstance(item, dict):
            raise ValueError("leader_stage6_partition_unverified")
        if set(item) != {"index", "symbol", "status", "reasons"}:
            raise ValueError("leader_stage6_partition_unverified")
        index = item.get("index")
        if not _is_strict_int(index):
            raise ValueError("leader_stage6_partition_unverified")
        if item.get("status") == "source_failed":
            raise ValueError("leader_source_failed_exclusion")
        _leader_reasons(item.get("reasons"))
        reviewed = excluded_by_index.get(index)
        if any((
            index in seen_excluded,
            reviewed is None,
            reviewed is not None and item.get("symbol") != reviewed.get("symbol"),
            item.get("status")
            not in {"missing", "source_failed", "source_unverified"},
            reviewed is not None and item.get("reasons") != reviewed.get("reasons"),
        )):
            raise ValueError("leader_stage6_partition_unverified")
        seen_excluded.add(index)
    if seen_excluded != set(excluded_by_index):
        raise ValueError("leader_stage6_partition_unverified")
    nested_evidence = _validate_leader_nested_evidence(
        collection=collection,
        qualification=qualification,
        receipt=receipt,
        parent_plan_id=parent_plan_id,
        qualified_plan_id=qualified_plan_id,
        parent_count=parent_count,
        qualified_count=qualified_count,
        excluded_items=excluded_items,
        industry_gate=industry_gate,
        industry_scope_id=selection["industryScopeSnapshotId"],
        qualified_scope=tuple(
            qualified_scope_by_index[index]
            for index in range(qualified_count)
        ),
    )
    if any((
        receipt.expected_count != parent_count,
        receipt.observed_count != parent_count,
        receipt.missing_count != 0,
        receipt.failed_count != 0,
    )):
        raise ValueError("leader_receipt_coverage_mismatch")
    return {
        "collectionContractId": collection["contractId"],
        "reviewContractId": review["contractId"],
        "runId": receipt.run_id,
        "asOf": receipt.as_of.isoformat(),
        "parentCandidatePlanId": parent_plan_id,
        "qualifiedCandidatePlanId": qualified_plan_id,
        "qualificationId": qualification_id,
        "parentCandidateCount": parent_count,
        "qualifiedCandidateCount": qualified_count,
        "excludedCandidateCount": excluded_count,
        "parentPlanItems": plan_items,
        "reviewItems": review_items,
        "excludedItems": excluded_items,
        "nestedEvidence": nested_evidence,
        "gates": {
            "artifact": artifact["gate"],
            "selection": selection["gate"],
            "plan": plan["gate"],
            "collection": collection["gate"],
            "qualification": qualification["gate"],
            "review": review["gate"],
        },
    }


def adapt_leader_observation(
    receipt_json_bytes: bytes,
    source_artifact_json_bytes: bytes,
    *,
    evaluated_at: datetime,
    collection_policy_json_bytes: Optional[bytes] = None,
) -> FormalShadowObservation:
    receipt, collection_policy = _validate_receipt_source(
        receipt_json_bytes,
        source_artifact_json_bytes,
        module="leaderObservation",
        evaluated_at=evaluated_at,
        collection_policy_raw=collection_policy_json_bytes,
    )
    artifact = _strict_json_bytes(
        source_artifact_json_bytes,
        "leader_stage6_artifact_unverified",
    )
    semantic = _validate_leader_partition(artifact, receipt)
    return _observation(
        receipt,
        receipt_raw=receipt_json_bytes,
        source_raw=source_artifact_json_bytes,
        semantic=semantic,
        collection_policy_raw=collection_policy_json_bytes,
        collection_policy=collection_policy,
    )


def _load_etf_live_artifact(
    raw: bytes,
) -> EtfLiveShadowObservationArtifact:
    payload = _strict_json_bytes(raw, "etf_live_artifact_unverified")
    try:
        return EtfLiveShadowObservationArtifact.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("etf_live_artifact_unverified") from exc


def _validate_etf_admission(
    raw: bytes,
    *,
    artifact: EtfLiveShadowObservationArtifact,
    observed_at: datetime,
) -> Mapping[str, Any]:
    from radar.etf_formal_admission import (
        ETF_FORMAL_ADMISSION_CONTRACT_ID,
        EtfFormalAdmissionStatus,
        load_etf_formal_admission_bundle,
    )

    payload = _strict_json_bytes(raw, "etf_admission_artifact_unverified")
    try:
        bundle = load_etf_formal_admission_bundle(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("etf_admission_artifact_unverified") from exc
    admission_symbols = tuple(item.symbol for item in bundle.admissions)
    if any((
        bundle.radar_run_id != artifact.run_id,
        bundle.as_of.astimezone(_SHANGHAI_TIMEZONE).date()
        != artifact.as_of.astimezone(_SHANGHAI_TIMEZONE).date(),
        bundle.as_of > observed_at,
        admission_symbols != artifact.requested_symbols,
    )):
        raise ValueError("etf_admission_identity_mismatch")
    required_identity_items = (
        ("product_identity", "etf_admission_product_identity_unverified"),
        ("product_lifecycle", "etf_admission_product_lifecycle_unverified"),
    )
    identity_semantics = []
    for expected_symbol, admission in zip(
        artifact.requested_symbols,
        bundle.admissions,
    ):
        if any((
            admission.contract_id != ETF_FORMAL_ADMISSION_CONTRACT_ID,
            admission.symbol != expected_symbol,
            admission.as_of != bundle.as_of,
        )):
            raise ValueError("etf_admission_identity_mismatch")
        verified_items = {}
        for key, reason in required_identity_items:
            matches = tuple(
                item for item in admission.items if item.key == key
            )
            if len(matches) != 1:
                raise ValueError(reason)
            item = matches[0]
            if (
                item.status != EtfFormalAdmissionStatus.READY
                or item.reasons != ()
            ):
                raise ValueError(reason)
            verified_items[key] = item.to_evidence()
        identity_semantics.append({
            "contractId": admission.contract_id,
            "symbol": admission.symbol,
            "asOf": admission.as_of.isoformat(),
            "ruleVersion": admission.rule_version,
            **verified_items,
        })
    return {
        "contractId": bundle.contract_id,
        "sampleId": bundle.sample_id,
        "radarRunId": bundle.radar_run_id,
        "asOf": bundle.as_of.isoformat(),
        "symbols": admission_symbols,
        "snapshotSha256": bundle.snapshot_sha256,
        "verifiedProductIdentities": identity_semantics,
    }


def adapt_etf_observation(
    receipt_json_bytes: bytes,
    source_artifact_json_bytes: bytes,
    formal_admission_json_bytes: Optional[bytes] = None,
    *,
    evaluated_at: datetime,
    collection_policy_json_bytes: Optional[bytes] = None,
) -> FormalShadowObservation:
    if formal_admission_json_bytes is not None:
        _require_json_bytes(
            formal_admission_json_bytes,
            "formal_shadow_admission_json_bytes_required",
        )
    receipt, collection_policy = _validate_receipt_source(
        receipt_json_bytes,
        source_artifact_json_bytes,
        module="etfObservation",
        evaluated_at=evaluated_at,
        collection_policy_raw=collection_policy_json_bytes,
    )
    artifact = _load_etf_live_artifact(source_artifact_json_bytes)
    if any((
        artifact.run_id != receipt.run_id,
        artifact.as_of != receipt.as_of,
        artifact.source_time != receipt.source_time,
        artifact.fetched_at != receipt.fetched_at,
        artifact.requested_count != receipt.expected_count,
        artifact.returned_count != receipt.observed_count,
        artifact.missing_count != receipt.missing_count,
        artifact.failed_count != receipt.failed_count,
        artifact.stale_count != receipt.stale_count,
        artifact.lock_state != receipt.lock_state,
        artifact.duration_ms != receipt.duration_ms,
        (
            receipt.source_health_status or "healthy"
        ) != artifact.source_health_status,
        receipt.source_health_reasons != artifact.source_health_reasons,
        artifact.status
        != _quality_status(
            lock_state=receipt.lock_state,
            coverage=receipt.coverage,
            missing_count=receipt.missing_count,
            failed_count=receipt.failed_count,
            stale_count=receipt.stale_count,
            source_ready=receipt.source_ready,
        ),
        any(item.fetched_at > receipt.observed_at for item in artifact.records),
    )):
        raise ValueError("etf_receipt_artifact_mismatch")
    freshness_limit = (
        collection_policy.maximum_source_age_seconds_by_module[
            "etfObservation"
        ]
    )
    observed_date = receipt.observed_at.astimezone(
        _SHANGHAI_TIMEZONE
    ).date()
    for item in artifact.records:
        age_seconds = (receipt.observed_at - item.source_time).total_seconds()
        expected_status = (
            "stale"
            if (
                item.source_time.astimezone(_SHANGHAI_TIMEZONE).date()
                != observed_date
                or age_seconds < -5
                or age_seconds > freshness_limit
                or artifact.source_health_status == "stale"
            )
            else "ready"
        )
        if item.status != expected_status:
            raise ValueError("etf_record_freshness_mismatch")
    if formal_admission_json_bytes is None:
        raise ValueError("etf_admission_required")
    admission = _validate_etf_admission(
        formal_admission_json_bytes,
        artifact=artifact,
        observed_at=receipt.observed_at,
    )
    semantic = {
        "contractId": artifact.contract_id,
        "runId": artifact.run_id,
        "asOf": artifact.as_of.isoformat(),
        "sourceTime": (
            artifact.source_time.isoformat()
            if artifact.source_time is not None else None
        ),
        "fetchedAt": artifact.fetched_at.isoformat(),
        "sourceHealthStatus": artifact.source_health_status,
        "sourceHealthReasons": artifact.source_health_reasons,
        "requestedSymbols": artifact.requested_symbols,
        "missingSymbols": artifact.missing_symbols,
        "failedSymbols": artifact.failed_symbols,
        "staleSymbols": artifact.stale_symbols,
        "records": [
            item.model_dump(mode="json", by_alias=True)
            for item in artifact.records
        ],
        "admissionIdentity": admission,
    }
    return _observation(
        receipt,
        receipt_raw=receipt_json_bytes,
        source_raw=source_artifact_json_bytes,
        admission_raw=formal_admission_json_bytes,
        semantic=semantic,
        collection_policy_raw=collection_policy_json_bytes,
        collection_policy=collection_policy,
    )


def adapt_formal_shadow_observation(
    receipt_json_bytes: bytes,
    source_artifact_json_bytes: bytes,
    *,
    evaluated_at: datetime,
    collection_policy_json_bytes: Optional[bytes] = None,
    supporting_artifact_json_bytes: Optional[bytes] = None,
    formal_admission_json_bytes: Optional[bytes] = None,
) -> FormalShadowObservation:
    """按回执模块分派；Task 3 可在安全 FD 读取后只调用此入口。"""

    _require_json_bytes(
        source_artifact_json_bytes,
        "formal_shadow_source_json_bytes_required",
    )
    if supporting_artifact_json_bytes is not None:
        _require_json_bytes(
            supporting_artifact_json_bytes,
            "formal_shadow_supporting_json_bytes_required",
        )
    if formal_admission_json_bytes is not None:
        _require_json_bytes(
            formal_admission_json_bytes,
            "formal_shadow_admission_json_bytes_required",
        )
    _require_evaluated_at(evaluated_at)

    receipt = load_formal_shadow_run_receipt(receipt_json_bytes)
    if receipt.module == "trendRotation":
        if supporting_artifact_json_bytes is None:
            raise ValueError("trend_sector_snapshot_required")
        if formal_admission_json_bytes is not None:
            raise ValueError("formal_shadow_adapter_argument_conflict")
        return adapt_trend_rotation_observation(
            receipt_json_bytes,
            source_artifact_json_bytes,
            supporting_artifact_json_bytes,
            evaluated_at=evaluated_at,
            collection_policy_json_bytes=collection_policy_json_bytes,
        )
    if receipt.module == "leaderObservation":
        if (
            supporting_artifact_json_bytes is not None
            or formal_admission_json_bytes is not None
        ):
            raise ValueError("formal_shadow_adapter_argument_conflict")
        return adapt_leader_observation(
            receipt_json_bytes,
            source_artifact_json_bytes,
            evaluated_at=evaluated_at,
            collection_policy_json_bytes=collection_policy_json_bytes,
        )
    if supporting_artifact_json_bytes is not None:
        raise ValueError("formal_shadow_adapter_argument_conflict")
    return adapt_etf_observation(
        receipt_json_bytes,
        source_artifact_json_bytes,
        formal_admission_json_bytes,
        evaluated_at=evaluated_at,
        collection_policy_json_bytes=collection_policy_json_bytes,
    )
