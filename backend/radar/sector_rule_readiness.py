"""行业正式规则输入就绪度合同。

本模块只审计已经冻结的当前行业特征及外部证据是否齐备，不查询数据库、
不重算历史、不生成行业分数、排名或八阶段状态。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Optional, Sequence, Tuple

from radar.contracts import (
    IndustryClassificationRelease,
    IndustryHistoryStatus,
    SectorFeatureBatch,
    SectorFeatureSnapshot,
    UnitVerificationStatus,
)


SECTOR_RULE_READINESS_CONTRACT_ID = (
    "radar-sector-rule-readiness-v1"
)
SECTOR_RULE_VERSION = "radar-sector-v0-shadow"
SECTOR_HISTORY_COVERAGE_CONTRACT_ID = (
    "radar-sector-history-coverage-v1"
)
SECTOR_MARKET_BASELINE_CONTRACT_ID = (
    "radar-sector-market-baseline-v1"
)
SECTOR_THRESHOLD_APPROVAL_CONTRACT_ID = (
    "radar-sector-threshold-approval-v1"
)
ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID = (
    "radar-abnormal-trading-day-filter-v1"
)
MINIMUM_SAME_MINUTE_SAMPLE_COUNT = 20
MINIMUM_PERSISTENCE_DAY_COUNT = 5
MINIMUM_COMPARABLE_INDUSTRY_COUNT = 20

REQUIRED_STATE_IDS = frozenset((
    "observe",
    "startup",
    "confirmed",
    "accelerating",
    "divergence",
    "reflow",
    "retreat",
    "invalid",
))
REQUIRED_THRESHOLD_POLICY_FIELDS = frozenset((
    "entry",
    "hold",
    "exit",
    "consecutive_observations",
    "minimum_hold_time",
    "cooldown",
    "data_failure_behavior",
))


class SectorRuleReadinessStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}不能为空")


def _require_unique_strings(
    values: Tuple[str, ...],
    field_name: str,
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name}必须是不可变元组")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{field_name}不能包含空值")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name}不能重复")


def _require_unique_dates(
    values: Tuple[date, ...],
    field_name: str,
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name}必须是不可变元组")
    if any(type(value) is not date for value in values):
        raise ValueError(f"{field_name}只能包含日期")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name}不能重复")


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True)
class SectorHistoryCoverage:
    division_code: str
    same_minute_trading_dates: Tuple[date, ...]
    persistence_trading_dates: Tuple[date, ...]

    def __post_init__(self) -> None:
        if (
            len(self.division_code) != 2
            or not self.division_code.isdigit()
        ):
            raise ValueError("行业大类代码必须为2位数字")
        _require_unique_dates(
            self.same_minute_trading_dates,
            "同分钟历史交易日",
        )
        _require_unique_dates(
            self.persistence_trading_dates,
            "持续性历史交易日",
        )


@dataclass(frozen=True)
class SectorHistoryCoverageEvidence:
    radar_run_id: str
    rule_version: str
    classification_document_sha256: str
    as_of: datetime
    rows: Tuple[SectorHistoryCoverage, ...]
    abnormal_day_filter_contract_id: Optional[str] = None
    contract_id: str = SECTOR_HISTORY_COVERAGE_CONTRACT_ID

    def __post_init__(self) -> None:
        _require_nonempty(self.radar_run_id, "历史证据radarRunId")
        _require_nonempty(self.rule_version, "历史证据规则版本")
        if (
            len(self.classification_document_sha256) != 64
            or any(
                value not in "0123456789abcdef"
                for value in self.classification_document_sha256
            )
        ):
            raise ValueError("历史证据行业文档摘要必须为64位小写十六进制")
        _require_aware_datetime(self.as_of, "历史证据asOf")
        if not isinstance(self.rows, tuple):
            raise ValueError("历史证据rows必须是不可变元组")
        division_codes = tuple(row.division_code for row in self.rows)
        if len(division_codes) != len(set(division_codes)):
            raise ValueError("历史证据行业大类代码不能重复")
        for row in self.rows:
            if any(
                value >= self.as_of.date()
                for value in (
                    row.same_minute_trading_dates
                    + row.persistence_trading_dates
                )
            ):
                raise ValueError("历史证据只能使用asOf之前的完整交易日")


@dataclass(frozen=True)
class SectorMarketBaselineEvidence:
    radar_run_id: str
    as_of: datetime
    equal_weighted_return: Optional[float]
    market_cap_weighted_return: Optional[float]
    source_batch_ids: Tuple[str, ...]
    market_cap_basis: str = "total_market_cap_source"
    contract_id: str = SECTOR_MARKET_BASELINE_CONTRACT_ID

    def __post_init__(self) -> None:
        _require_nonempty(self.radar_run_id, "市场基准radarRunId")
        _require_aware_datetime(self.as_of, "市场基准asOf")
        for value in (
            self.equal_weighted_return,
            self.market_cap_weighted_return,
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError("市场基准收益必须是有限数值")
        _require_unique_strings(self.source_batch_ids, "市场基准来源批次")


@dataclass(frozen=True)
class SectorThresholdApprovalEvidence:
    rule_version: str
    threshold_set_id: str
    approval_id: str
    approved_at: datetime
    state_ids: Tuple[str, ...]
    policy_fields: Tuple[str, ...]
    contract_id: str = SECTOR_THRESHOLD_APPROVAL_CONTRACT_ID

    def __post_init__(self) -> None:
        _require_nonempty(self.rule_version, "阈值规则版本")
        _require_nonempty(self.threshold_set_id, "阈值集合版本")
        _require_nonempty(self.approval_id, "阈值批准记录")
        _require_aware_datetime(self.approved_at, "阈值批准时间")
        _require_unique_strings(self.state_ids, "阈值覆盖状态")
        _require_unique_strings(self.policy_fields, "阈值策略字段")


@dataclass(frozen=True)
class SectorRuleReadinessItem:
    key: str
    status: SectorRuleReadinessStatus
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class SectorRuleReadinessResult:
    status: SectorRuleReadinessStatus
    radar_run_id: str
    rule_version: str
    as_of: datetime
    items: Tuple[SectorRuleReadinessItem, ...]
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = SECTOR_RULE_READINESS_CONTRACT_ID

    def item(self, key: str) -> SectorRuleReadinessItem:
        for value in self.items:
            if value.key == key:
                return value
        raise KeyError(key)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "contractId": self.contract_id,
            "ruleVersion": self.rule_version,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat(),
            "status": self.status.value,
            "reasons": list(self.reasons),
            "items": [item.to_evidence() for item in self.items],
        }


def _item(
    key: str,
    reasons: Sequence[str] = (),
) -> SectorRuleReadinessItem:
    normalized = _dedupe(reasons)
    return SectorRuleReadinessItem(
        key=key,
        status=(
            SectorRuleReadinessStatus.MISSING
            if normalized
            else SectorRuleReadinessStatus.READY
        ),
        reasons=normalized,
    )


def _finite_metric(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _current_sector_ready(sector: SectorFeatureSnapshot) -> bool:
    metrics = (
        sector.returns.equal_return,
        sector.returns.cap_weighted_return,
        sector.returns.ex_top_return,
        sector.breadth.up_ratio,
    )
    return all((
        sector.shadow_usable,
        sector.completeness.is_complete,
        sector.completeness.expected_count > 1,
        all(
            metric.available and _finite_metric(metric.raw_value)
            for metric in metrics
        ),
        (
            sector.returns.market_cap_unit_status
            == UnitVerificationStatus.VERIFIED
        ),
        sector.turnover.available,
        _finite_metric(sector.turnover.raw_value),
        (
            sector.turnover.unit_status
            == UnitVerificationStatus.VERIFIED
        ),
    ))


def evaluate_sector_rule_readiness(
    *,
    feature_batch: SectorFeatureBatch,
    classification_release: Optional[IndustryClassificationRelease],
    history_evidence: Optional[SectorHistoryCoverageEvidence] = None,
    market_baseline_evidence: Optional[
        SectorMarketBaselineEvidence
    ] = None,
    threshold_approval_evidence: Optional[
        SectorThresholdApprovalEvidence
    ] = None,
    required_division_codes: Optional[Tuple[str, ...]] = None,
    rule_version: str = SECTOR_RULE_VERSION,
) -> SectorRuleReadinessResult:
    """审计行业正式规则输入，任何证据缺口均封闭返回missing。"""

    _require_nonempty(rule_version, "行业规则版本")
    if required_division_codes is not None:
        _require_unique_strings(required_division_codes, "必需行业大类")
        if not required_division_codes or any(
            len(code) != 2 or not code.isdigit()
            for code in required_division_codes
        ):
            raise ValueError("必需行业大类必须为非空2位数字代码")
    required_codes = (
        set(required_division_codes)
        if required_division_codes is not None
        else None
    )
    feature_codes = {
        sector.division_code for sector in feature_batch.sectors
    }
    scoped_sectors = tuple(
        sector
        for sector in feature_batch.sectors
        if required_codes is None or sector.division_code in required_codes
    )

    classification_history_reasons = []
    if classification_release is None:
        classification_history_reasons.append(
            "sector_classification_release_missing"
        )
    else:
        if any((
            classification_release.classification_system
            != feature_batch.classification_system,
            classification_release.release_period
            != feature_batch.release_period,
            classification_release.document_sha256
            != feature_batch.classification_document_sha256,
        )):
            classification_history_reasons.append(
                "sector_classification_identity_mismatch"
            )
        if (
            classification_release.history_status not in {
                IndustryHistoryStatus.FORWARD_OBSERVED,
                IndustryHistoryStatus.OFFICIAL_ARCHIVE_VERIFIED,
            }
        ):
            classification_history_reasons.append(
                "sector_classification_history_unverified"
            )
        if (
            classification_release.knowledge_effective_from
            > feature_batch.as_of
            or (
                classification_release.knowledge_effective_to
                is not None
                and classification_release.knowledge_effective_to
                <= feature_batch.as_of
            )
        ):
            classification_history_reasons.append(
                "sector_classification_not_effective_at_as_of"
            )
    classification_history_item = _item(
        "classification_history",
        classification_history_reasons,
    )

    mapping_reasons = []
    if required_codes is None and (
        feature_batch.classification_mapping_coverage != 1.0
        or feature_batch.unconfirmed_stock_count != 0
    ):
        mapping_reasons.append(
            "sector_classification_mapping_incomplete"
        )
    elif required_codes is not None and not required_codes <= feature_codes:
        mapping_reasons.append(
            "sector_classification_mapping_incomplete"
        )
    classification_mapping_item = _item(
        "classification_mapping",
        mapping_reasons,
    )

    current_ready_codes = tuple(
        sector.division_code
        for sector in feature_batch.sectors
        if _current_sector_ready(sector)
    )
    scoped_ready_codes = tuple(
        code
        for code in current_ready_codes
        if required_codes is None or code in required_codes
    )
    allowed_batch_reasons = {"formal_use_not_approved"}
    if required_codes is not None:
        allowed_batch_reasons.update({
            "classification_source_degraded",
            "classification_mapping_incomplete",
            "sector_features_incomplete",
        })
    current_feature_reasons = []
    if any((
        required_codes is None and not feature_batch.shadow_usable,
        not scoped_sectors,
        len(scoped_ready_codes) != len(scoped_sectors),
        bool(feature_batch.duplicate_quote_symbols),
        bool(feature_batch.unknown_quote_symbols),
        bool(set(feature_batch.reasons) - allowed_batch_reasons),
    )):
        current_feature_reasons.append(
            "sector_current_feature_batch_incomplete"
        )
    current_features_item = _item(
        "current_sector_features",
        current_feature_reasons,
    )

    market_baseline_reasons = []
    if market_baseline_evidence is None:
        market_baseline_reasons.append(
            "sector_market_baseline_missing"
        )
    elif (
        market_baseline_evidence.contract_id
        != SECTOR_MARKET_BASELINE_CONTRACT_ID
    ):
        market_baseline_reasons.append(
            "sector_market_baseline_contract_unverified"
        )
    elif any((
        market_baseline_evidence.radar_run_id
        != feature_batch.radar_run_id,
        market_baseline_evidence.as_of != feature_batch.as_of,
    )):
        market_baseline_reasons.append(
            "sector_market_baseline_identity_mismatch"
        )
    elif any((
        not _finite_metric(
            market_baseline_evidence.equal_weighted_return
        ),
        not _finite_metric(
            market_baseline_evidence.market_cap_weighted_return
        ),
        not market_baseline_evidence.source_batch_ids,
        market_baseline_evidence.market_cap_basis
        != "total_market_cap_source",
    )):
        market_baseline_reasons.append(
            "sector_market_baseline_incomplete"
        )
    market_baseline_item = _item(
        "market_baseline",
        market_baseline_reasons,
    )

    history_contract_reasons = []
    history_rows = {}
    if history_evidence is None:
        history_contract_reasons.append(
            "sector_history_coverage_missing"
        )
    else:
        history_rows = {
            row.division_code: row
            for row in history_evidence.rows
        }
        if (
            history_evidence.contract_id
            != SECTOR_HISTORY_COVERAGE_CONTRACT_ID
        ):
            history_contract_reasons.append(
                "sector_history_contract_unverified"
            )
        if any((
            history_evidence.radar_run_id
            != feature_batch.radar_run_id,
            history_evidence.as_of != feature_batch.as_of,
            history_evidence.classification_document_sha256
            != feature_batch.classification_document_sha256,
        )):
            history_contract_reasons.append(
                "sector_history_identity_mismatch"
            )
        if history_evidence.rule_version != rule_version:
            history_contract_reasons.append(
                "sector_history_rule_version_mismatch"
            )
        if (
            history_evidence.abnormal_day_filter_contract_id
            != ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID
        ):
            history_contract_reasons.append(
                "sector_history_abnormal_day_filter_unverified"
            )
        if classification_release is not None and any(
            value < classification_release.knowledge_effective_from.date()
            for row in history_evidence.rows
            for value in (
                row.same_minute_trading_dates
                + row.persistence_trading_dates
            )
        ):
            history_contract_reasons.append(
                "sector_history_predates_classification_knowledge"
            )
    history_coverage_item = _item(
        "history_coverage",
        history_contract_reasons,
    )

    same_minute_reasons = []
    persistence_reasons = []
    if history_evidence is None:
        same_minute_reasons.append(
            "sector_same_minute_turnover_history_missing"
        )
        persistence_reasons.append(
            "sector_persistence_history_missing"
        )
    elif history_contract_reasons:
        same_minute_reasons.extend(history_contract_reasons)
        persistence_reasons.extend(history_contract_reasons)
    else:
        same_minute_ready_codes = {
            code
            for code in current_ready_codes
            if code in history_rows
            and len(
                history_rows[code].same_minute_trading_dates
            ) >= MINIMUM_SAME_MINUTE_SAMPLE_COUNT
        }
        persistence_ready_codes = {
            code
            for code in current_ready_codes
            if code in history_rows
            and len(
                history_rows[code].persistence_trading_dates
            ) >= MINIMUM_PERSISTENCE_DAY_COUNT
        }
        if (
            len(same_minute_ready_codes)
            < MINIMUM_COMPARABLE_INDUSTRY_COUNT
        ):
            same_minute_reasons.append(
                "sector_same_minute_turnover_samples_below_20"
            )
        if (
            len(persistence_ready_codes)
            < MINIMUM_COMPARABLE_INDUSTRY_COUNT
        ):
            persistence_reasons.append(
                "sector_persistence_completed_days_below_5"
            )
    same_minute_item = _item(
        "same_minute_turnover_history",
        same_minute_reasons,
    )
    persistence_item = _item(
        "persistence_history",
        persistence_reasons,
    )

    comparable_reasons = []
    comparable_prerequisites = (
        classification_history_item,
        classification_mapping_item,
        current_features_item,
        market_baseline_item,
        history_coverage_item,
        same_minute_item,
        persistence_item,
    )
    if any(
        item.status != SectorRuleReadinessStatus.READY
        for item in comparable_prerequisites
    ):
        comparable_reasons.append(
            "sector_comparable_industry_count_below_20"
        )
    else:
        comparable_codes = {
            code
            for code in current_ready_codes
            if (
                len(history_rows[code].same_minute_trading_dates)
                >= MINIMUM_SAME_MINUTE_SAMPLE_COUNT
                and len(
                    history_rows[code].persistence_trading_dates
                ) >= MINIMUM_PERSISTENCE_DAY_COUNT
            )
        }
        if len(comparable_codes) < MINIMUM_COMPARABLE_INDUSTRY_COUNT:
            comparable_reasons.append(
                "sector_comparable_industry_count_below_20"
            )
    comparable_item = _item(
        "comparable_industries",
        comparable_reasons,
    )

    threshold_reasons = []
    if threshold_approval_evidence is None:
        threshold_reasons.append(
            "sector_state_threshold_approval_missing"
        )
    elif (
        threshold_approval_evidence.contract_id
        != SECTOR_THRESHOLD_APPROVAL_CONTRACT_ID
    ):
        threshold_reasons.append(
            "sector_state_threshold_contract_unverified"
        )
    elif threshold_approval_evidence.rule_version != rule_version:
        threshold_reasons.append(
            "sector_state_threshold_rule_version_mismatch"
        )
    elif threshold_approval_evidence.approved_at > feature_batch.as_of:
        threshold_reasons.append(
            "sector_state_threshold_approval_after_as_of"
        )
    elif any((
        set(threshold_approval_evidence.state_ids)
        != REQUIRED_STATE_IDS,
        set(threshold_approval_evidence.policy_fields)
        != REQUIRED_THRESHOLD_POLICY_FIELDS,
    )):
        threshold_reasons.append(
            "sector_state_threshold_scope_incomplete"
        )
    threshold_item = _item(
        "threshold_approval",
        threshold_reasons,
    )

    items = (
        classification_history_item,
        classification_mapping_item,
        current_features_item,
        market_baseline_item,
        history_coverage_item,
        same_minute_item,
        persistence_item,
        comparable_item,
        threshold_item,
    )
    reasons = _dedupe(tuple(
        reason
        for item in items
        for reason in item.reasons
    ))
    return SectorRuleReadinessResult(
        status=(
            SectorRuleReadinessStatus.MISSING
            if reasons
            else SectorRuleReadinessStatus.READY
        ),
        radar_run_id=feature_batch.radar_run_id,
        rule_version=rule_version,
        as_of=feature_batch.as_of,
        items=items,
        reasons=reasons,
    )
