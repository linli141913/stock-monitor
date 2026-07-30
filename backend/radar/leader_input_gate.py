"""阶段6E龙头真实输入证据门禁。

本模块把调用方提供的市场、行业、证券行情、ETF和业务暴露证据
转换为LeaderScoringInput。模块不抓取数据、不读取数据库、不推断缺失
分数；来源时间、覆盖率或证据身份不合格时，只返回缺失/过期/未验证状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from radar.leader_scoring import (
    LeaderDimensionInput,
    LeaderGateInput,
    LeaderMetricStatus,
    LeaderScoringInput,
)
from radar.leader_state_machine import (
    BusinessExposureStatus,
    LeaderStateMachinePolicy,
    DEFAULT_LEADER_STATE_MACHINE_POLICY,
)


UTC = timezone.utc
def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _symbol(value: Any) -> str:
    text = _required_text(value, "symbol")
    if len(text) != 6 or not text.isdigit():
        raise ValueError("symbol必须是6位数字")
    return text


def _dedupe(values) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class LeaderSourceKind(str, Enum):
    MARKET = "market"
    SECTOR = "sector"
    QUOTE = "quote"
    ETF = "etf"
    BUSINESS_EXPOSURE = "business_exposure"
    OTHER = "other"


@dataclass(frozen=True)
class LeaderInputGatePolicy:
    maximum_source_age_seconds: int = 90
    maximum_future_skew_seconds: int = 5
    minimum_row_coverage: float = 0.995
    minimum_required_field_coverage: float = 0.99

    def __post_init__(self):
        if self.maximum_source_age_seconds <= 0:
            raise ValueError("maximum_source_age_seconds必须大于0")
        if self.maximum_future_skew_seconds < 0:
            raise ValueError("maximum_future_skew_seconds不能小于0")
        if not 0 <= self.minimum_row_coverage <= 1:
            raise ValueError("minimum_row_coverage必须在0到1之间")
        if not 0 <= self.minimum_required_field_coverage <= 1:
            raise ValueError(
                "minimum_required_field_coverage必须在0到1之间"
            )


@dataclass(frozen=True)
class LeaderSourceEvidence:
    source_contract_id: str
    source_kind: LeaderSourceKind
    source_name: str
    source_time: Optional[datetime]
    fetched_at: datetime
    status: LeaderMetricStatus = LeaderMetricStatus.VERIFIED
    row_coverage: float = 1.0
    required_field_coverage: float = 1.0
    reasons: Tuple[str, ...] = ()

    def __post_init__(self):
        _required_text(self.source_contract_id, "source_contract_id")
        _required_text(self.source_name, "source_name")
        if self.source_time is not None:
            _aware_utc(self.source_time, "source_time")
        fetched_at = _aware_utc(self.fetched_at, "fetched_at")
        if self.source_time is not None and (
            fetched_at < _aware_utc(self.source_time, "source_time")
        ):
            raise ValueError("fetched_at不能早于source_time")
        if not 0 <= self.row_coverage <= 1:
            raise ValueError("row_coverage必须在0到1之间")
        if not 0 <= self.required_field_coverage <= 1:
            raise ValueError(
                "required_field_coverage必须在0到1之间"
            )
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("来源原因不得重复")


@dataclass(frozen=True)
class LeaderDimensionEvidence:
    field_name: str
    score: Optional[float]
    source_contract_id: Optional[str]
    status: LeaderMetricStatus = LeaderMetricStatus.VERIFIED
    reasons: Tuple[str, ...] = ()

    def __post_init__(self):
        _required_text(self.field_name, "field_name")
        if self.score is not None and not 0 <= self.score <= 100:
            raise ValueError("score必须在0到100之间")
        if self.status == LeaderMetricStatus.VERIFIED and self.score is None:
            raise ValueError("已验证维度必须保留分数，真实0不能写成缺失")
        if self.status != LeaderMetricStatus.VERIFIED and self.score is not None:
            raise ValueError("未验证维度不得携带可用分数")
        if self.source_contract_id is not None:
            _required_text(self.source_contract_id, "source_contract_id")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("维度原因不得重复")


@dataclass(frozen=True)
class LeaderInputEvidence:
    symbol: str
    name: str
    as_of: datetime
    dimensions: Tuple[LeaderDimensionEvidence, ...]
    gates: LeaderGateInput
    sources: Tuple[LeaderSourceEvidence, ...]
    industry_code: Optional[str] = None
    industry_name: Optional[str] = None
    business_exposure_source_contract_id: Optional[str] = None
    consecutive_signal_periods: int = 1
    evidence: Mapping[str, Any] = field(default_factory=dict)
    invalidation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _symbol(self.symbol)
        _required_text(self.name, "name")
        _aware_utc(self.as_of, "as_of")
        if self.consecutive_signal_periods < 0:
            raise ValueError("consecutive_signal_periods不能小于0")
        source_ids = [
            _required_text(item.source_contract_id, "source_contract_id")
            for item in self.sources
        ]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("来源合同ID不得重复")
        if self.business_exposure_source_contract_id is not None:
            _required_text(
                self.business_exposure_source_contract_id,
                "business_exposure_source_contract_id",
            )


@dataclass(frozen=True)
class LeaderInputGateResult:
    scoring_input: LeaderScoringInput
    name: str
    industry_code: Optional[str]
    industry_name: Optional[str]
    input_ready: bool
    reasons: Tuple[str, ...]
    evidence: Mapping[str, Any]
    invalidation: Mapping[str, Any]

    def to_shadow_candidate(self):
        from radar.leader_shadow_runner import LeaderShadowCandidate

        return LeaderShadowCandidate(
            scoring_input=self.scoring_input,
            name=self.name,
            industry_code=self.industry_code,
            industry_name=self.industry_name,
            evidence=self.evidence,
            invalidation=self.invalidation,
        )


def _source_payload(source: LeaderSourceEvidence) -> Dict[str, Any]:
    return {
        "sourceContractId": source.source_contract_id,
        "sourceKind": source.source_kind.value,
        "sourceName": source.source_name,
        "status": source.status.value,
        "sourceTime": source.source_time,
        "fetchedAt": source.fetched_at,
        "rowCoverage": source.row_coverage,
        "requiredFieldCoverage": source.required_field_coverage,
        "reasons": list(source.reasons),
    }


def _status_for_source(
    source: Optional[LeaderSourceEvidence],
    *,
    as_of: datetime,
    policy: LeaderInputGatePolicy,
    field_name: str,
) -> Tuple[LeaderMetricStatus, Tuple[str, ...]]:
    if source is None:
        return (
            LeaderMetricStatus.SOURCE_UNVERIFIED,
            (f"source_contract_missing:{field_name}",),
        )
    reasons = list(source.reasons)
    if source.status == LeaderMetricStatus.SOURCE_FAILED:
        reasons.append(
            f"source_failed:{source.source_contract_id}"
        )
        return LeaderMetricStatus.SOURCE_FAILED, _dedupe(reasons)
    if source.status == LeaderMetricStatus.STALE:
        reasons.append(f"source_stale:{source.source_contract_id}")
        return LeaderMetricStatus.STALE, _dedupe(reasons)
    if source.status in {
        LeaderMetricStatus.MISSING,
        LeaderMetricStatus.SOURCE_UNVERIFIED,
        LeaderMetricStatus.NOT_APPLICABLE,
    }:
        reasons.append(
            f"source_unavailable:{source.source_contract_id}"
        )
        return source.status, _dedupe(reasons)
    if source.source_time is None:
        reasons.append(f"source_time_missing:{source.source_contract_id}")
        return LeaderMetricStatus.SOURCE_UNVERIFIED, _dedupe(reasons)

    source_time = _aware_utc(source.source_time, "source_time")
    as_of = _aware_utc(as_of, "as_of")
    age_seconds = (as_of - source_time).total_seconds()
    if age_seconds < -policy.maximum_future_skew_seconds:
        reasons.append(f"source_time_future:{source.source_contract_id}")
        return LeaderMetricStatus.SOURCE_UNVERIFIED, _dedupe(reasons)
    if age_seconds > policy.maximum_source_age_seconds:
        reasons.append(f"source_age_over_limit:{source.source_contract_id}")
        return LeaderMetricStatus.STALE, _dedupe(reasons)
    if source.row_coverage < policy.minimum_row_coverage:
        reasons.append(
            f"row_coverage_below_limit:{source.source_contract_id}"
        )
        return LeaderMetricStatus.SOURCE_UNVERIFIED, _dedupe(reasons)
    if (
        source.required_field_coverage
        < policy.minimum_required_field_coverage
    ):
        reasons.append(
            f"field_coverage_below_limit:{source.source_contract_id}"
        )
        return LeaderMetricStatus.SOURCE_UNVERIFIED, _dedupe(reasons)
    return LeaderMetricStatus.VERIFIED, _dedupe(reasons)


def _normalized_business_exposure_status(
    bundle: LeaderInputEvidence,
    source_by_id: Mapping[str, LeaderSourceEvidence],
    *,
    as_of: datetime,
    policy: LeaderInputGatePolicy,
) -> Tuple[BusinessExposureStatus, Tuple[str, ...]]:
    status = bundle.gates.business_exposure_status
    if status == BusinessExposureStatus.DISPROVED:
        return status, ()
    source_id = bundle.business_exposure_source_contract_id
    if source_id is None:
        if status == BusinessExposureStatus.VERIFIED:
            return (
                BusinessExposureStatus.UNCONFIRMED,
                ("business_exposure_source_unverified",),
            )
        return status, ()
    source = source_by_id.get(source_id)
    source_status, source_reasons = _status_for_source(
        source,
        as_of=as_of,
        policy=policy,
        field_name="business_exposure",
    )
    if source_status == LeaderMetricStatus.VERIFIED:
        return status, source_reasons
    if source_status == LeaderMetricStatus.SOURCE_FAILED:
        return (
            BusinessExposureStatus.MISSING,
            ("business_exposure_source_failed", *source_reasons),
        )
    if source_status == LeaderMetricStatus.STALE:
        return (
            BusinessExposureStatus.UNCONFIRMED,
            ("business_exposure_source_stale", *source_reasons),
        )
    return (
        BusinessExposureStatus.UNCONFIRMED,
        ("business_exposure_source_unverified", *source_reasons),
    )


def build_leader_input_gate(
    bundle: LeaderInputEvidence,
    *,
    policy: LeaderInputGatePolicy = LeaderInputGatePolicy(),
    scoring_policy: LeaderStateMachinePolicy = (
        DEFAULT_LEADER_STATE_MACHINE_POLICY
    ),
) -> LeaderInputGateResult:
    source_by_id = {
        source.source_contract_id: source
        for source in bundle.sources
    }
    expected_fields = tuple(
        field_name for field_name, _ in scoring_policy.score_weights
    )
    dimensions_by_field: Dict[str, LeaderDimensionEvidence] = {}
    for dimension in bundle.dimensions:
        if dimension.field_name in dimensions_by_field:
            raise ValueError(f"龙头输入维度重复: {dimension.field_name}")
        if dimension.field_name not in expected_fields:
            raise ValueError(f"未知龙头输入维度: {dimension.field_name}")
        dimensions_by_field[dimension.field_name] = dimension

    reasons = []
    normalized_dimensions = []
    dimension_sources = {}
    for field_name in expected_fields:
        dimension = dimensions_by_field.get(field_name)
        if dimension is None:
            normalized_dimensions.append(
                LeaderDimensionInput(
                    field_name=field_name,
                    score=None,
                    status=LeaderMetricStatus.MISSING,
                    reasons=(f"dimension_missing:{field_name}",),
                )
            )
            reasons.append(f"dimension_missing:{field_name}")
            continue
        source = (
            source_by_id.get(dimension.source_contract_id)
            if dimension.source_contract_id is not None
            else None
        )
        source_status, source_reasons = _status_for_source(
            source,
            as_of=bundle.as_of,
            policy=policy,
            field_name=field_name,
        )
        effective_status = dimension.status
        if effective_status == LeaderMetricStatus.VERIFIED:
            effective_status = source_status
        elif source_status == LeaderMetricStatus.SOURCE_FAILED:
            effective_status = LeaderMetricStatus.SOURCE_FAILED
        elif (
            effective_status == LeaderMetricStatus.MISSING
            and source_status == LeaderMetricStatus.STALE
        ):
            effective_status = LeaderMetricStatus.STALE
        dimension_reasons = _dedupe(
            (*dimension.reasons, *source_reasons)
        )
        normalized_dimensions.append(
            LeaderDimensionInput(
                field_name=field_name,
                score=(
                    dimension.score
                    if effective_status == LeaderMetricStatus.VERIFIED
                    else None
                ),
                status=effective_status,
                reasons=dimension_reasons,
            )
        )
        dimension_sources[field_name] = (
            dimension.source_contract_id
        )
        reasons.extend(dimension_reasons)

    business_status, business_reasons = _normalized_business_exposure_status(
        bundle,
        source_by_id,
        as_of=bundle.as_of,
        policy=policy,
    )
    reasons.extend(business_reasons)
    gates = bundle.gates
    normalized_gates = LeaderGateInput(
        industry_gate_passed=gates.industry_gate_passed,
        stock_gate_passed=gates.stock_gate_passed,
        market_leadership_passed=gates.market_leadership_passed,
        industry_contribution_passed=gates.industry_contribution_passed,
        liquidity_passed=gates.liquidity_passed,
        tradability_passed=gates.tradability_passed,
        continuity_passed=gates.continuity_passed,
        recovery_passed=gates.recovery_passed,
        risk_filter_passed=gates.risk_filter_passed,
        business_exposure_status=business_status,
    )
    scoring_input = LeaderScoringInput(
        symbol=bundle.symbol,
        as_of=bundle.as_of,
        dimensions=tuple(normalized_dimensions),
        gates=normalized_gates,
        consecutive_signal_periods=bundle.consecutive_signal_periods,
    )
    reasons = _dedupe(reasons)
    input_ready = not reasons
    source_payloads = {
        source_id: _source_payload(source)
        for source_id, source in sorted(source_by_id.items())
    }
    evidence = {
        **dict(bundle.evidence),
        "inputGatePassed": input_ready,
        "inputGateReasons": list(reasons),
        "dimensionSourceContracts": dimension_sources,
        "sourceContracts": source_payloads,
    }
    invalidation = {
        **dict(bundle.invalidation),
        "inputGateReasons": list(reasons),
        "businessExposureStatus": business_status.value,
    }
    return LeaderInputGateResult(
        scoring_input=scoring_input,
        name=bundle.name,
        industry_code=bundle.industry_code,
        industry_name=bundle.industry_name,
        input_ready=input_ready,
        reasons=reasons,
        evidence=evidence,
        invalidation=invalidation,
    )
