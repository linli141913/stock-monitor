"""阶段5 ETF正式准入、规则禁用和保留策略合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfProductMasterRecord,
    EtfRankingInputAudit,
    ListedFundProductType,
)


ETF_RULE_VERSION = "radar-etf-rule-v1"
REQUIRED_RANKING_FIELDS: Tuple[str, ...] = (
    "fundSize",
    "averageTurnover20d",
    "trackingDifference",
    "trackingError",
    "indexCorrelation",
)
SUPPORTED_ETF_HISTORY_WINDOWS: Tuple[int, ...] = (20, 60)
REQUIRED_ETF_EXCEPTION_SCENARIOS: Tuple[str, ...] = (
    "source_failed",
    "stale_data",
    "incomplete_required_fields",
    "duplicate_schedule",
    "identity_drift",
    "non_trading_session",
)


@dataclass(frozen=True)
class EtfRulePolicy:
    version: str = ETF_RULE_VERSION
    ranking_enabled: bool = False
    required_fields: Tuple[str, ...] = REQUIRED_RANKING_FIELDS
    weights: Tuple[Tuple[str, float], ...] = ()
    thresholds: Tuple[Tuple[str, float], ...] = ()
    disabled_reasons: Tuple[str, ...] = (
        "formal_source_inputs_incomplete",
        "ranking_calibration_sample_missing",
    )

    def __post_init__(self):
        if not self.version.strip():
            raise ValueError("ETF规则版本不能为空")
        if not self.required_fields:
            raise ValueError("ETF规则必须声明正式排名字段")
        if len(self.required_fields) != len(set(self.required_fields)):
            raise ValueError("ETF规则正式排名字段不得重复")
        if len(self.disabled_reasons) != len(set(self.disabled_reasons)):
            raise ValueError("ETF规则禁用原因不得重复")
        weight_fields = [field_name for field_name, _ in self.weights]
        threshold_names = [name for name, _ in self.thresholds]
        if len(weight_fields) != len(set(weight_fields)):
            raise ValueError("ETF规则权重字段不得重复")
        if len(threshold_names) != len(set(threshold_names)):
            raise ValueError("ETF规则阈值名称不得重复")
        if self.ranking_enabled:
            if not self.weights or not self.thresholds:
                raise ValueError("启用ETF排名前必须冻结权重和阈值")
            if self.disabled_reasons:
                raise ValueError("已启用ETF规则不能保留禁用原因")
            if set(weight_fields) != set(self.required_fields):
                raise ValueError("ETF规则权重必须完整覆盖正式排名字段")
            if any(weight < 0 for _, weight in self.weights):
                raise ValueError("ETF规则权重不得为负数")
            if abs(sum(weight for _, weight in self.weights) - 1.0) > 1e-9:
                raise ValueError("ETF规则权重合计必须等于1")
        elif self.weights or self.thresholds:
            raise ValueError("ETF排名禁用时不得预填未经验证的权重或阈值")


@dataclass(frozen=True)
class EtfRetentionPolicy:
    intraday_detail_trading_days: int = 60
    keep_daily_facts: bool = True
    keep_candidate_summaries: bool = True
    keep_raw_upstream_payloads: bool = False
    automatic_cleanup_enabled: bool = False

    def __post_init__(self):
        if self.intraday_detail_trading_days < 20:
            raise ValueError("ETF盘中特征保留期不得短于20个交易日")
        if not self.keep_daily_facts or not self.keep_candidate_summaries:
            raise ValueError("ETF日频事实和候选摘要必须长期保留")
        if self.keep_raw_upstream_payloads:
            raise ValueError("阶段5第一版不得长期保存上游原始响应")
        if self.automatic_cleanup_enabled:
            raise ValueError("生产清理任务未获授权，不能自动启用")


@dataclass(frozen=True)
class EtfObservationPolicy:
    supported_history_windows: Tuple[int, ...] = (
        SUPPORTED_ETF_HISTORY_WINDOWS
    )
    minimum_live_shadow_trading_days: int = 5
    required_exception_scenarios: Tuple[str, ...] = (
        REQUIRED_ETF_EXCEPTION_SCENARIOS
    )

    def __post_init__(self):
        if (
            not self.supported_history_windows
            or len(self.supported_history_windows)
            != len(set(self.supported_history_windows))
            or tuple(sorted(self.supported_history_windows))
            != self.supported_history_windows
            or self.supported_history_windows[0] < 20
        ):
            raise ValueError("ETF历史窗口必须是不短于20日的递增唯一集合")
        if self.minimum_live_shadow_trading_days < 5:
            raise ValueError("ETF现场影子验收不得少于5个交易日")
        if (
            not self.required_exception_scenarios
            or len(self.required_exception_scenarios)
            != len(set(self.required_exception_scenarios))
        ):
            raise ValueError("ETF异常场景合同必须完整且不得重复")


@dataclass(frozen=True)
class EtfObservationEvidence:
    available_history_trading_days: int
    required_history_trading_days: int
    point_in_time_history_verified: bool
    live_shadow_trading_days: int
    live_scheduled_runs_complete: bool
    passed_exception_scenarios: Tuple[str, ...]

    def __post_init__(self):
        if self.available_history_trading_days < 0:
            raise ValueError("ETF历史覆盖交易日不能为负数")
        if self.required_history_trading_days <= 0:
            raise ValueError("ETF规则历史窗口必须为正数")
        if self.live_shadow_trading_days < 0:
            raise ValueError("ETF现场影子交易日不能为负数")
        if len(self.passed_exception_scenarios) != len(
            set(self.passed_exception_scenarios)
        ):
            raise ValueError("ETF已通过异常场景不得重复")


@dataclass(frozen=True)
class EtfObservationDecision:
    ready: bool
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class EtfFormalGateEvidence:
    product: EtfProductMasterRecord
    lifecycle_active: bool
    index_relation_ready: bool
    methodology_ready: bool
    constituent_set_ready: bool
    industry_exposure_ready: bool
    industry_mapping_coverage: float
    ranking_input: EtfRankingInputAudit

    def __post_init__(self):
        if not 0 <= self.industry_mapping_coverage <= 1:
            raise ValueError("ETF行业映射覆盖率必须在0到1之间")


@dataclass(frozen=True)
class EtfFormalGateDecision:
    formal_ready: bool
    reasons: Tuple[str, ...]
    rule_version: str


@dataclass(frozen=True)
class EtfLowFrequencyReadiness:
    product_effective_time_available: bool
    product_version_transition_supported: bool
    full_index_refresh_available: bool
    daily_fact_universe_available: bool
    retention_policy_approved: bool


@dataclass(frozen=True)
class EtfLowFrequencyDecision:
    ready: bool
    reasons: Tuple[str, ...]


DEFAULT_ETF_RULE_POLICY = EtfRulePolicy()
DEFAULT_ETF_RETENTION_POLICY = EtfRetentionPolicy()
DEFAULT_ETF_OBSERVATION_POLICY = EtfObservationPolicy()


def evaluate_etf_formal_gate(
    evidence: EtfFormalGateEvidence,
    *,
    policy: EtfRulePolicy = DEFAULT_ETF_RULE_POLICY,
) -> EtfFormalGateDecision:
    reasons = []
    product = evidence.product
    if product.product_type != ListedFundProductType.ETF:
        reasons.append("product_type_not_etf")
    if product.management_style != EtfManagementStyle.PASSIVE_INDEX:
        reasons.append("management_style_not_verified_passive")
    if product.asset_class != EtfAssetClass.DOMESTIC_EQUITY:
        reasons.append("asset_class_not_domestic_equity")
    if not evidence.lifecycle_active:
        reasons.append("product_lifecycle_not_active")
    if not evidence.index_relation_ready:
        reasons.append("index_relation_not_ready")
    if not evidence.methodology_ready:
        reasons.append("index_methodology_not_ready")
    if not evidence.constituent_set_ready:
        reasons.append("index_constituent_set_not_ready")
    if not evidence.industry_exposure_ready:
        reasons.append("industry_exposure_not_ready")
    if evidence.industry_mapping_coverage < 1.0:
        reasons.append("industry_mapping_coverage_below_100_percent")
    if not evidence.ranking_input.formal_ready:
        reasons.append("ranking_input_not_ready")
    missing_ranking_fields = [
        field_name
        for field_name in policy.required_fields
        if field_name not in evidence.ranking_input.rankable_fields
    ]
    if missing_ranking_fields:
        reasons.extend(
            f"ranking_field_not_verified:{field_name}"
            for field_name in missing_ranking_fields
        )
    if not policy.ranking_enabled:
        reasons.append("etf_rule_not_frozen")
        reasons.extend(policy.disabled_reasons)
    reasons = tuple(dict.fromkeys(reasons))
    return EtfFormalGateDecision(
        formal_ready=not reasons,
        reasons=reasons,
        rule_version=policy.version,
    )


def evaluate_low_frequency_readiness(
    readiness: EtfLowFrequencyReadiness,
) -> EtfLowFrequencyDecision:
    reasons = []
    checks = (
        (
            readiness.product_effective_time_available,
            "product_effective_time_unavailable",
        ),
        (
            readiness.product_version_transition_supported,
            "product_version_transition_not_supported",
        ),
        (
            readiness.full_index_refresh_available,
            "full_index_refresh_unavailable",
        ),
        (
            readiness.daily_fact_universe_available,
            "daily_fact_universe_unavailable",
        ),
        (
            readiness.retention_policy_approved,
            "retention_policy_not_approved",
        ),
    )
    for passed, reason in checks:
        if not passed:
            reasons.append(reason)
    return EtfLowFrequencyDecision(
        ready=not reasons,
        reasons=tuple(reasons),
    )


def evaluate_observation_readiness(
    evidence: EtfObservationEvidence,
    *,
    policy: EtfObservationPolicy = DEFAULT_ETF_OBSERVATION_POLICY,
) -> EtfObservationDecision:
    reasons = []
    if (
        evidence.required_history_trading_days
        not in policy.supported_history_windows
    ):
        reasons.append("etf_history_window_not_supported")
    if not evidence.point_in_time_history_verified:
        reasons.append("etf_point_in_time_history_unverified")
    if (
        evidence.available_history_trading_days
        < evidence.required_history_trading_days
    ):
        reasons.append("etf_history_coverage_insufficient")
    if (
        evidence.live_shadow_trading_days
        < policy.minimum_live_shadow_trading_days
    ):
        reasons.append("etf_live_shadow_trading_days_insufficient")
    if not evidence.live_scheduled_runs_complete:
        reasons.append("etf_live_scheduled_runs_incomplete")
    passed_scenarios = set(evidence.passed_exception_scenarios)
    reasons.extend(
        f"etf_exception_scenario_not_passed:{scenario}"
        for scenario in policy.required_exception_scenarios
        if scenario not in passed_scenarios
    )
    return EtfObservationDecision(
        ready=not reasons,
        reasons=tuple(reasons),
    )
