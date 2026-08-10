"""阶段6L-C4A供应商可交易性数据交付与批次准入合同。

本模块只审计调用方显式提供的供应商候选数据，不联网、不连接数据库，
也不把供应商数据转换为C3官方来源输入。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from radar.leader_research_features import (
    MAXIMUM_FUTURE_SKEW_SECONDS,
    MAXIMUM_SOURCE_AGE_SECONDS,
)
from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_sources import (
    PriceLimitSpecialSession,
)


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LEADER_TRADABILITY_PROVIDER_DELIVERY_CONTRACT_ID = (
    "radar-leader-tradability-provider-delivery-v1"
)
LEADER_TRADABILITY_PROVIDER_BATCH_CONTRACT_ID = (
    "radar-leader-tradability-provider-batch-v1"
)
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

CONTRACT_UNVERIFIED = "provider_batch_contract_unverified"
ADMISSION_POLICY_UNVERIFIED = (
    "provider_admission_policy_unverified"
)
ADMISSION_POLICY_MISMATCH = "provider_admission_policy_mismatch"
AS_OF_TIMEZONE_MISSING = "provider_batch_as_of_timezone_missing"
TRADING_DATE_MISMATCH = "provider_batch_trading_date_mismatch"
EMPTY_TARGETS = "provider_batch_targets_empty"
DUPLICATE_TARGET = "provider_batch_target_duplicated"
RECORD_CONTRACT_UNVERIFIED = "provider_record_contract_unverified"
RECORD_IDENTITY_UNVERIFIED = "provider_record_identity_unverified"
RECORD_TRADING_DATE_MISMATCH = (
    "provider_record_trading_date_mismatch"
)
RECORD_ENUM_UNVERIFIED = "provider_record_enum_unverified"
PRICE_LIMIT_PAYLOAD_INVALID = (
    "provider_price_limit_payload_invalid"
)
SOURCE_PROVENANCE_UNVERIFIED = (
    "provider_source_provenance_unverified"
)
SOURCE_OBSERVATION_IDENTITY_MISMATCH = (
    "provider_source_observation_identity_mismatch"
)
PROVIDER_IDENTITY_MISMATCH = "provider_identity_mismatch"
DELIVERY_CONTRACT_MISMATCH = "provider_delivery_contract_mismatch"
DELIVERY_BATCH_MISMATCH = "provider_delivery_batch_mismatch"
LICENSE_UNVERIFIED = "provider_license_unverified"
LICENSE_NOT_EFFECTIVE = "provider_license_not_effective"
LICENSE_SCOPE_NOT_PERMITTED = (
    "provider_license_scope_not_permitted"
)
STATIC_TIMESTAMP_TIMEZONE_MISSING = (
    "provider_static_reference_timezone_missing"
)
STATIC_REFERENCE_FROM_FUTURE = (
    "provider_static_reference_from_future"
)
DYNAMIC_TIMESTAMP_TIMEZONE_MISSING = (
    "provider_dynamic_status_timezone_missing"
)
DYNAMIC_STATUS_FROM_FUTURE = "provider_dynamic_status_from_future"
DYNAMIC_STATUS_STALE = "provider_dynamic_status_stale"
DELIVERY_TIMESTAMP_TIMEZONE_MISSING = (
    "provider_delivery_timezone_missing"
)
DELIVERY_BEFORE_SOURCE = "provider_delivery_before_source"
DELIVERY_FROM_FUTURE = "provider_delivery_from_future"
BATCH_COVERAGE_INCOMPLETE = "provider_batch_coverage_incomplete"


class ProviderDeliveryGrade(str, Enum):
    AUTHORIZED_PROVIDER_CANDIDATE = (
        "authorized_provider_candidate"
    )
    UNVERIFIED_PROVIDER = "unverified_provider"


class ProviderLicenseUsageScope(str, Enum):
    PERSONAL_SERVER_PROCESSING = "personal_server_processing"
    RESEARCH_ONLY = "research_only"
    UNVERIFIED = "unverified"


class ProviderTradabilityBatchStatus(str, Enum):
    ADMISSIBLE_CANDIDATE = "admissible_candidate"
    BLOCKED = "blocked"
    MISSING = "missing"


@dataclass(frozen=True, repr=False)
class ProviderAdmissionPolicy:
    policy_id: str
    provider_id: str
    provider_name: str
    provider_origins: Tuple[str, ...]
    delivery_contract_ids: Tuple[str, ...]
    upstream_source_contract_ids: Tuple[str, ...]
    upstream_source_names: Tuple[str, ...]
    upstream_origins: Tuple[str, ...]
    license_document_ids: Tuple[str, ...]
    license_origins: Tuple[str, ...]
    usage_scope: ProviderLicenseUsageScope
    valid_from: date
    valid_until: Optional[date]


@dataclass(frozen=True, repr=False)
class ProviderLicenseEvidence:
    provider_id: str
    license_document_id: str
    license_url: str
    usage_scope: ProviderLicenseUsageScope
    valid_from: date
    valid_until: Optional[date]
    permits_local_processing: bool


@dataclass(frozen=True, repr=False)
class ProviderSourceObservation:
    symbol: str
    trading_date: date
    source_contract_id: str
    source_name: str
    source_url: str
    document_id: str
    source_time: datetime
    content_digest: str


@dataclass(frozen=True, repr=False)
class ProviderSourceProvenance:
    provider_id: str
    provider_name: str
    provider_url: str
    delivery_grade: ProviderDeliveryGrade
    delivery_contract_id: str
    delivery_batch_id: str


@dataclass(frozen=True)
class ProviderTradabilityDeliveryRecord:
    symbol: str
    exchange: str
    board: str
    trading_date: date
    lifecycle_status: SecurityLifecycleStatus
    trading_status: TradingSessionStatus
    special_session: PriceLimitSpecialSession
    price_limit_mode: PriceLimitMode
    upper_limit_price: Optional[float]
    lower_limit_price: Optional[float]
    static_observation: ProviderSourceObservation = field(repr=False)
    dynamic_observation: ProviderSourceObservation = field(repr=False)
    delivered_at: datetime
    provenance: ProviderSourceProvenance = field(repr=False)
    license_evidence: ProviderLicenseEvidence = field(repr=False)


@dataclass(frozen=True)
class ProviderTradabilityBatchInput:
    radar_run_id: str
    as_of: datetime
    trading_date: date
    provider_id: str
    delivery_contract_id: str
    delivery_batch_id: str
    expected_symbols: Tuple[str, ...]
    records: Tuple[ProviderTradabilityDeliveryRecord, ...] = field(
        repr=False
    )


@dataclass(frozen=True)
class ProviderTradabilityBatchResult:
    status: ProviderTradabilityBatchStatus
    admission_policy_id: Optional[str]
    radar_run_id: Optional[str]
    as_of: Optional[datetime]
    trading_date: Optional[date]
    provider_id: Optional[str]
    delivery_contract_id: Optional[str]
    delivery_batch_id: Optional[str]
    expected_count: int
    received_count: int
    missing_symbols: Tuple[str, ...] = field(default_factory=tuple)
    duplicate_symbols: Tuple[str, ...] = field(default_factory=tuple)
    extra_symbols: Tuple[str, ...] = field(default_factory=tuple)
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    batch_id: Optional[str] = None
    batch_contract_id: str = (
        LEADER_TRADABILITY_PROVIDER_BATCH_CONTRACT_ID
    )
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Dict[str, object]:
        return {
            "batchContractId": self.batch_contract_id,
            "admissionPolicyId": self.admission_policy_id,
            "deliveryContractId": self.delivery_contract_id,
            "batchId": self.batch_id,
            "deliveryBatchId": self.delivery_batch_id,
            "radarRunId": self.radar_run_id,
            "providerId": self.provider_id,
            "status": self.status.value,
            "asOf": (
                self.as_of.isoformat()
                if self.as_of is not None
                else None
            ),
            "tradingDate": (
                self.trading_date.isoformat()
                if self.trading_date is not None
                else None
            ),
            "expectedCount": self.expected_count,
            "receivedCount": self.received_count,
            "missingSymbols": list(self.missing_symbols),
            "duplicateSymbols": list(self.duplicate_symbols),
            "extraSymbols": list(self.extra_symbols),
            "reasons": list(self.reasons),
            "gate": {
                "formalScoreReady": self.formal_score_ready,
                "formalGateReady": self.formal_gate_ready,
                "formalUsable": self.formal_usable,
                "stateTransitionAllowed": (
                    self.state_transition_allowed
                ),
            },
        }


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_https_url(value: Any) -> bool:
    if not _required_text(value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return all((
        parsed.scheme == "https",
        bool(parsed.hostname),
        parsed.username is None,
        parsed.password is None,
        not parsed.query,
        not parsed.fragment,
        port is None or 0 < port < 65536,
    ))


def _url_origin(value: Any) -> Optional[str]:
    if not _safe_https_url(value):
        return None
    parsed = urlsplit(value)
    origin = "https://" + str(parsed.hostname).lower()
    if parsed.port is not None:
        origin += ":" + str(parsed.port)
    return origin


def _origin_tuple_valid(values: Any) -> bool:
    return (
        isinstance(values, tuple)
        and bool(values)
        and all(
            _url_origin(item) == item.rstrip("/")
            for item in values
            if _required_text(item)
        )
        and all(_required_text(item) for item in values)
    )


def _text_tuple_valid(values: Any) -> bool:
    return (
        isinstance(values, tuple)
        and bool(values)
        and all(_required_text(item) for item in values)
        and len(set(values)) == len(values)
    )


def _aware_utc(value: Any) -> Optional[datetime]:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _policy_valid(policy: Any) -> bool:
    if not isinstance(policy, ProviderAdmissionPolicy):
        return False
    dates_valid = (
        isinstance(policy.valid_from, date)
        and (
            policy.valid_until is None
            or isinstance(policy.valid_until, date)
        )
    )
    if (
        dates_valid
        and policy.valid_until is not None
        and policy.valid_until < policy.valid_from
    ):
        dates_valid = False
    return all((
        _required_text(policy.policy_id),
        _required_text(policy.provider_id),
        _required_text(policy.provider_name),
        _origin_tuple_valid(policy.provider_origins),
        _text_tuple_valid(policy.delivery_contract_ids),
        _text_tuple_valid(policy.upstream_source_contract_ids),
        _text_tuple_valid(policy.upstream_source_names),
        _origin_tuple_valid(policy.upstream_origins),
        _text_tuple_valid(policy.license_document_ids),
        _origin_tuple_valid(policy.license_origins),
        isinstance(policy.usage_scope, ProviderLicenseUsageScope),
        policy.usage_scope
        == ProviderLicenseUsageScope.PERSONAL_SERVER_PROCESSING,
        dates_valid,
    ))


def _price_payload_valid(
    record: ProviderTradabilityDeliveryRecord,
) -> bool:
    if record.price_limit_mode == PriceLimitMode.NO_LIMIT:
        return (
            record.upper_limit_price is None
            and record.lower_limit_price is None
        )
    if record.price_limit_mode != PriceLimitMode.BOUNDED:
        return False
    if not (
        _finite_positive(record.upper_limit_price)
        and _finite_positive(record.lower_limit_price)
    ):
        return False
    return float(record.lower_limit_price) < float(
        record.upper_limit_price
    )


def _observation_reasons(
    observation: Any,
    *,
    record: ProviderTradabilityDeliveryRecord,
    batch_input: ProviderTradabilityBatchInput,
    admission_policy: ProviderAdmissionPolicy,
    as_of_utc: datetime,
    dynamic: bool,
) -> Tuple[Tuple[str, ...], Optional[datetime]]:
    reasons = []
    if not isinstance(observation, ProviderSourceObservation):
        return (SOURCE_PROVENANCE_UNVERIFIED,), None
    if not all((
        _required_text(observation.symbol),
        isinstance(observation.trading_date, date),
        _required_text(observation.source_contract_id),
        _required_text(observation.source_name),
        _safe_https_url(observation.source_url),
        _required_text(observation.document_id),
        isinstance(observation.content_digest, str),
        bool(SHA256_PATTERN.fullmatch(observation.content_digest)),
    )):
        reasons.append(SOURCE_PROVENANCE_UNVERIFIED)
    if (
        observation.symbol != record.symbol
        or observation.trading_date != record.trading_date
        or observation.trading_date != batch_input.trading_date
    ):
        reasons.append(SOURCE_OBSERVATION_IDENTITY_MISMATCH)
    if (
        observation.source_contract_id
        not in admission_policy.upstream_source_contract_ids
        or observation.source_name
        not in admission_policy.upstream_source_names
        or _url_origin(observation.source_url)
        not in admission_policy.upstream_origins
    ):
        reasons.append(SOURCE_PROVENANCE_UNVERIFIED)

    source_time = _aware_utc(observation.source_time)
    if source_time is None:
        reasons.append(
            DYNAMIC_TIMESTAMP_TIMEZONE_MISSING
            if dynamic
            else STATIC_TIMESTAMP_TIMEZONE_MISSING
        )
        return _dedupe(reasons), None
    source_age = (as_of_utc - source_time).total_seconds()
    if source_age < -MAXIMUM_FUTURE_SKEW_SECONDS:
        reasons.append(
            DYNAMIC_STATUS_FROM_FUTURE
            if dynamic
            else STATIC_REFERENCE_FROM_FUTURE
        )
    elif dynamic and source_age > MAXIMUM_SOURCE_AGE_SECONDS:
        reasons.append(DYNAMIC_STATUS_STALE)
    return _dedupe(reasons), source_time


def _record_reasons(
    record: ProviderTradabilityDeliveryRecord,
    batch_input: ProviderTradabilityBatchInput,
    admission_policy: ProviderAdmissionPolicy,
    as_of_utc: datetime,
) -> Tuple[str, ...]:
    reasons = []
    if not all((
        _required_text(record.symbol),
        _required_text(record.exchange),
        _required_text(record.board),
        isinstance(record.trading_date, date),
    )):
        reasons.append(RECORD_IDENTITY_UNVERIFIED)
    if record.trading_date != batch_input.trading_date:
        reasons.append(RECORD_TRADING_DATE_MISMATCH)

    if not all((
        isinstance(record.lifecycle_status, SecurityLifecycleStatus),
        record.lifecycle_status != SecurityLifecycleStatus.UNKNOWN,
        isinstance(record.trading_status, TradingSessionStatus),
        record.trading_status != TradingSessionStatus.UNKNOWN,
        isinstance(record.special_session, PriceLimitSpecialSession),
        isinstance(record.price_limit_mode, PriceLimitMode),
        record.price_limit_mode != PriceLimitMode.UNKNOWN,
    )):
        reasons.append(RECORD_ENUM_UNVERIFIED)
    if (
        record.special_session
        == PriceLimitSpecialSession.RELISTING_FIRST_DAY
        and record.lifecycle_status != SecurityLifecycleStatus.RELISTED
    ) or (
        record.special_session
        == PriceLimitSpecialSession.DELISTING_FIRST_DAY
        and record.lifecycle_status != SecurityLifecycleStatus.DELISTING
    ):
        reasons.append(RECORD_ENUM_UNVERIFIED)
    if not _price_payload_valid(record):
        reasons.append(PRICE_LIMIT_PAYLOAD_INVALID)

    provenance = record.provenance
    if not isinstance(provenance, ProviderSourceProvenance):
        reasons.append(SOURCE_PROVENANCE_UNVERIFIED)
    else:
        if not all((
            _required_text(provenance.provider_id),
            _required_text(provenance.provider_name),
            _safe_https_url(provenance.provider_url),
            isinstance(
                provenance.delivery_grade,
                ProviderDeliveryGrade,
            ),
            provenance.delivery_grade
            == ProviderDeliveryGrade.AUTHORIZED_PROVIDER_CANDIDATE,
            _required_text(provenance.delivery_contract_id),
            _required_text(provenance.delivery_batch_id),
        )):
            reasons.append(SOURCE_PROVENANCE_UNVERIFIED)
        if (
            provenance.provider_id != batch_input.provider_id
            or provenance.provider_id != admission_policy.provider_id
            or provenance.provider_name != admission_policy.provider_name
            or _url_origin(provenance.provider_url)
            not in admission_policy.provider_origins
        ):
            reasons.append(PROVIDER_IDENTITY_MISMATCH)
        if (
            provenance.delivery_contract_id
            != batch_input.delivery_contract_id
        ):
            reasons.append(DELIVERY_CONTRACT_MISMATCH)
        if (
            provenance.delivery_batch_id
            != batch_input.delivery_batch_id
        ):
            reasons.append(DELIVERY_BATCH_MISMATCH)

    license_evidence = record.license_evidence
    if not isinstance(license_evidence, ProviderLicenseEvidence):
        reasons.append(LICENSE_UNVERIFIED)
    else:
        valid_from_ok = isinstance(license_evidence.valid_from, date)
        valid_until_ok = (
            license_evidence.valid_until is None
            or isinstance(license_evidence.valid_until, date)
        )
        if not all((
            _required_text(license_evidence.provider_id),
            _required_text(license_evidence.license_document_id),
            _safe_https_url(license_evidence.license_url),
            isinstance(
                license_evidence.usage_scope,
                ProviderLicenseUsageScope,
            ),
            valid_from_ok,
            valid_until_ok,
            isinstance(
                license_evidence.permits_local_processing,
                bool,
            ),
        )):
            reasons.append(LICENSE_UNVERIFIED)
        if license_evidence.provider_id != admission_policy.provider_id:
            reasons.append(PROVIDER_IDENTITY_MISMATCH)
        if (
            license_evidence.license_document_id
            not in admission_policy.license_document_ids
            or _url_origin(license_evidence.license_url)
            not in admission_policy.license_origins
        ):
            reasons.append(LICENSE_UNVERIFIED)
        if valid_from_ok and valid_until_ok:
            if (
                batch_input.trading_date
                < license_evidence.valid_from
                or (
                    license_evidence.valid_until is not None
                    and batch_input.trading_date
                    > license_evidence.valid_until
                )
            ):
                reasons.append(LICENSE_NOT_EFFECTIVE)
            if (
                license_evidence.valid_from
                < admission_policy.valid_from
                or (
                    admission_policy.valid_until is not None
                    and (
                        license_evidence.valid_until is None
                        or license_evidence.valid_until
                        > admission_policy.valid_until
                    )
                )
            ):
                reasons.append(LICENSE_UNVERIFIED)
        if (
            license_evidence.usage_scope
            != admission_policy.usage_scope
            or not license_evidence.permits_local_processing
        ):
            reasons.append(LICENSE_SCOPE_NOT_PERMITTED)

    static_reasons, static_source_time = _observation_reasons(
        record.static_observation,
        record=record,
        batch_input=batch_input,
        admission_policy=admission_policy,
        as_of_utc=as_of_utc,
        dynamic=False,
    )
    dynamic_reasons, dynamic_source_time = _observation_reasons(
        record.dynamic_observation,
        record=record,
        batch_input=batch_input,
        admission_policy=admission_policy,
        as_of_utc=as_of_utc,
        dynamic=True,
    )
    reasons.extend(static_reasons)
    reasons.extend(dynamic_reasons)

    delivered_at = _aware_utc(record.delivered_at)
    if delivered_at is None:
        reasons.append(DELIVERY_TIMESTAMP_TIMEZONE_MISSING)
    else:
        if (
            static_source_time is not None
            and delivered_at < static_source_time
        ) or (
            dynamic_source_time is not None
            and delivered_at < dynamic_source_time
        ):
            reasons.append(DELIVERY_BEFORE_SOURCE)
        if (
            delivered_at - as_of_utc
        ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
            reasons.append(DELIVERY_FROM_FUTURE)
    return _dedupe(reasons)


def _observation_payload(
    observation: ProviderSourceObservation,
) -> Dict[str, object]:
    return {
        "symbol": observation.symbol,
        "tradingDate": observation.trading_date.isoformat(),
        "sourceContractId": observation.source_contract_id,
        "sourceName": observation.source_name,
        "sourceUrl": observation.source_url,
        "documentId": observation.document_id,
        "sourceTime": observation.source_time.isoformat(),
        "contentDigest": observation.content_digest,
    }


def _stable_batch_id(
    batch_input: ProviderTradabilityBatchInput,
    admission_policy: ProviderAdmissionPolicy,
) -> str:
    records = []
    for item in batch_input.records:
        records.append({
            "symbol": item.symbol,
            "exchange": item.exchange,
            "board": item.board,
            "tradingDate": item.trading_date.isoformat(),
            "lifecycleStatus": item.lifecycle_status.value,
            "tradingStatus": item.trading_status.value,
            "specialSession": item.special_session.value,
            "priceLimitMode": item.price_limit_mode.value,
            "upperLimitPrice": item.upper_limit_price,
            "lowerLimitPrice": item.lower_limit_price,
            "staticObservation": _observation_payload(
                item.static_observation
            ),
            "dynamicObservation": _observation_payload(
                item.dynamic_observation
            ),
            "deliveredAt": item.delivered_at.isoformat(),
            "provenance": {
                "providerId": item.provenance.provider_id,
                "providerName": item.provenance.provider_name,
                "providerUrl": item.provenance.provider_url,
                "deliveryGrade": (
                    item.provenance.delivery_grade.value
                ),
                "deliveryContractId": (
                    item.provenance.delivery_contract_id
                ),
                "deliveryBatchId": item.provenance.delivery_batch_id,
            },
            "license": {
                "providerId": item.license_evidence.provider_id,
                "licenseDocumentId": (
                    item.license_evidence.license_document_id
                ),
                "licenseUrl": item.license_evidence.license_url,
                "usageScope": item.license_evidence.usage_scope.value,
                "validFrom": (
                    item.license_evidence.valid_from.isoformat()
                ),
                "validUntil": (
                    item.license_evidence.valid_until.isoformat()
                    if item.license_evidence.valid_until is not None
                    else None
                ),
                "permitsLocalProcessing": (
                    item.license_evidence.permits_local_processing
                ),
            },
        })
    payload = {
        "contractId": LEADER_TRADABILITY_PROVIDER_BATCH_CONTRACT_ID,
        "admissionPolicyId": admission_policy.policy_id,
        "radarRunId": batch_input.radar_run_id,
        "asOf": batch_input.as_of.isoformat(),
        "tradingDate": batch_input.trading_date.isoformat(),
        "providerId": batch_input.provider_id,
        "deliveryContractId": batch_input.delivery_contract_id,
        "deliveryBatchId": batch_input.delivery_batch_id,
        "expectedSymbols": list(batch_input.expected_symbols),
        "records": records,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _empty_result(
    *,
    status: ProviderTradabilityBatchStatus,
    reasons: Sequence[str],
    admission_policy_id: Optional[str] = None,
) -> ProviderTradabilityBatchResult:
    return ProviderTradabilityBatchResult(
        status=status,
        admission_policy_id=admission_policy_id,
        radar_run_id=None,
        as_of=None,
        trading_date=None,
        provider_id=None,
        delivery_contract_id=None,
        delivery_batch_id=None,
        expected_count=0,
        received_count=0,
        reasons=_dedupe(reasons),
    )


def audit_provider_tradability_batch(
    batch_input: Any,
    *,
    admission_policy: Any,
) -> ProviderTradabilityBatchResult:
    """审计供应商交付批次，但不把通过结果开放给正式计算。"""

    if not isinstance(batch_input, ProviderTradabilityBatchInput):
        return _empty_result(
            status=ProviderTradabilityBatchStatus.BLOCKED,
            reasons=(CONTRACT_UNVERIFIED,),
        )
    if not _policy_valid(admission_policy):
        return _empty_result(
            status=ProviderTradabilityBatchStatus.BLOCKED,
            reasons=(ADMISSION_POLICY_UNVERIFIED,),
            admission_policy_id=(
                admission_policy.policy_id
                if isinstance(admission_policy, ProviderAdmissionPolicy)
                and _required_text(admission_policy.policy_id)
                else None
            ),
        )

    reasons = []
    if not all((
        _required_text(batch_input.radar_run_id),
        _required_text(batch_input.provider_id),
        _required_text(batch_input.delivery_contract_id),
        _required_text(batch_input.delivery_batch_id),
        isinstance(batch_input.trading_date, date),
        isinstance(batch_input.expected_symbols, tuple),
        isinstance(batch_input.records, tuple),
    )):
        reasons.append(CONTRACT_UNVERIFIED)
    if (
        batch_input.provider_id != admission_policy.provider_id
        or batch_input.delivery_contract_id
        not in admission_policy.delivery_contract_ids
    ):
        reasons.append(ADMISSION_POLICY_MISMATCH)
    if (
        isinstance(batch_input.trading_date, date)
        and (
            batch_input.trading_date < admission_policy.valid_from
            or (
                admission_policy.valid_until is not None
                and batch_input.trading_date
                > admission_policy.valid_until
            )
        )
    ):
        reasons.append(ADMISSION_POLICY_MISMATCH)

    as_of_utc = _aware_utc(batch_input.as_of)
    if as_of_utc is None:
        reasons.append(AS_OF_TIMEZONE_MISSING)
    elif (
        batch_input.as_of.astimezone(SHANGHAI_TZ).date()
        != batch_input.trading_date
    ):
        reasons.append(TRADING_DATE_MISMATCH)

    expected_symbols = (
        batch_input.expected_symbols
        if isinstance(batch_input.expected_symbols, tuple)
        else ()
    )
    if not expected_symbols:
        reasons.append(EMPTY_TARGETS)
    if any(not _required_text(item) for item in expected_symbols):
        reasons.append(CONTRACT_UNVERIFIED)
    expected_counts = Counter(expected_symbols)
    if any(count > 1 for count in expected_counts.values()):
        reasons.append(DUPLICATE_TARGET)

    raw_records = (
        batch_input.records
        if isinstance(batch_input.records, tuple)
        else ()
    )
    valid_records = tuple(
        item
        for item in raw_records
        if isinstance(item, ProviderTradabilityDeliveryRecord)
    )
    if len(valid_records) != len(raw_records):
        reasons.append(RECORD_CONTRACT_UNVERIFIED)

    record_counts = Counter(item.symbol for item in valid_records)
    missing_symbols = tuple(
        symbol
        for symbol in expected_symbols
        if record_counts[symbol] == 0
    )
    duplicate_symbols = tuple(dict.fromkeys(
        item.symbol
        for item in valid_records
        if record_counts[item.symbol] > 1
    ))
    expected_set = set(expected_symbols)
    extra_symbols = tuple(dict.fromkeys(
        item.symbol
        for item in valid_records
        if item.symbol not in expected_set
    ))
    if missing_symbols or duplicate_symbols or extra_symbols:
        reasons.append(BATCH_COVERAGE_INCOMPLETE)

    if as_of_utc is not None:
        for item in valid_records:
            reasons.extend(_record_reasons(
                item,
                batch_input,
                admission_policy,
                as_of_utc,
            ))

    reasons_tuple = _dedupe(reasons)
    if not raw_records and expected_symbols:
        status = ProviderTradabilityBatchStatus.MISSING
    elif reasons_tuple:
        status = ProviderTradabilityBatchStatus.BLOCKED
    else:
        status = (
            ProviderTradabilityBatchStatus.ADMISSIBLE_CANDIDATE
        )
    batch_id = (
        _stable_batch_id(batch_input, admission_policy)
        if status
        == ProviderTradabilityBatchStatus.ADMISSIBLE_CANDIDATE
        else None
    )

    return ProviderTradabilityBatchResult(
        status=status,
        admission_policy_id=admission_policy.policy_id,
        radar_run_id=batch_input.radar_run_id,
        as_of=batch_input.as_of,
        trading_date=batch_input.trading_date,
        provider_id=batch_input.provider_id,
        delivery_contract_id=batch_input.delivery_contract_id,
        delivery_batch_id=batch_input.delivery_batch_id,
        expected_count=len(expected_symbols),
        received_count=len(raw_records),
        missing_symbols=_dedupe(missing_symbols),
        duplicate_symbols=duplicate_symbols,
        extra_symbols=extra_symbols,
        reasons=reasons_tuple,
        batch_id=batch_id,
    )
