"""市场环境的确定性研究状态生产器。

这个模块只对同一轮、已通过完整度和成交额单位校验的真实特征
做版本化分类。输出用于阶段9回放与标签验证，不打开正式市场状态，
也不表示该分类已被证明有效。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping, Optional, Tuple

from radar.contracts import (
    MarketFeatureSnapshot,
    MarketIndexKey,
    UnitVerificationStatus,
)


MARKET_RESEARCH_STATE_CONTRACT_ID = "radar-market-research-state-v1"
MARKET_RESEARCH_RULE_VERSION = "radar-market-research-rule-v1"


class MarketResearchState(str, Enum):
    STRONG = "strong"
    OSCILLATION = "oscillation"
    RETREAT = "retreat"
    RISK = "risk"


class MarketResearchStateStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MarketResearchStatePolicy:
    strong_min_advancer_ratio: float = 0.60
    strong_min_mean_index_change_percent: float = 0.50
    strong_min_positive_index_count: int = 3
    retreat_max_advancer_ratio: float = 0.40
    retreat_max_mean_index_change_percent: float = 0.0
    retreat_min_negative_index_count: int = 3
    risk_max_advancer_ratio: float = 0.25
    risk_max_mean_index_change_percent: float = -1.0
    risk_min_negative_index_count: int = 3
    version: str = MARKET_RESEARCH_RULE_VERSION

    def __post_init__(self) -> None:
        ratios = (
            self.strong_min_advancer_ratio,
            self.retreat_max_advancer_ratio,
            self.risk_max_advancer_ratio,
        )
        counts = (
            self.strong_min_positive_index_count,
            self.retreat_min_negative_index_count,
            self.risk_min_negative_index_count,
        )
        if (
            not self.version.strip()
            or any(not 0 <= value <= 1 for value in ratios)
            or any(not 1 <= value <= 4 for value in counts)
            or self.risk_max_advancer_ratio
            > self.retreat_max_advancer_ratio
            or self.retreat_max_advancer_ratio
            >= self.strong_min_advancer_ratio
            or self.risk_max_mean_index_change_percent
            >= self.retreat_max_mean_index_change_percent
            or self.retreat_max_mean_index_change_percent
            >= self.strong_min_mean_index_change_percent
        ):
            raise ValueError("market_research_policy_unverified")

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "version": self.version,
            "strong": {
                "minimumAdvancerRatio": self.strong_min_advancer_ratio,
                "minimumMeanIndexChangePercent": (
                    self.strong_min_mean_index_change_percent
                ),
                "minimumPositiveIndexCount": (
                    self.strong_min_positive_index_count
                ),
            },
            "retreat": {
                "maximumAdvancerRatio": self.retreat_max_advancer_ratio,
                "maximumMeanIndexChangePercent": (
                    self.retreat_max_mean_index_change_percent
                ),
                "minimumNegativeIndexCount": (
                    self.retreat_min_negative_index_count
                ),
            },
            "risk": {
                "maximumAdvancerRatio": self.risk_max_advancer_ratio,
                "maximumMeanIndexChangePercent": (
                    self.risk_max_mean_index_change_percent
                ),
                "minimumNegativeIndexCount": (
                    self.risk_min_negative_index_count
                ),
            },
        }


DEFAULT_MARKET_RESEARCH_STATE_POLICY = MarketResearchStatePolicy()


@dataclass(frozen=True)
class MarketResearchStateMetrics:
    advancer_ratio: float
    decliner_ratio: float
    positive_index_count: int
    negative_index_count: int
    flat_index_count: int
    mean_index_change_percent: float

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "advancerRatio": self.advancer_ratio,
            "declinerRatio": self.decliner_ratio,
            "positiveIndexCount": self.positive_index_count,
            "negativeIndexCount": self.negative_index_count,
            "flatIndexCount": self.flat_index_count,
            "meanIndexChangePercent": self.mean_index_change_percent,
        }


@dataclass(frozen=True)
class MarketResearchStateResult:
    status: MarketResearchStateStatus
    radar_run_id: Optional[str]
    as_of: Any
    state: Optional[MarketResearchState]
    metrics: Optional[MarketResearchStateMetrics]
    reasons: Tuple[str, ...]
    snapshot_sha256: Optional[str]
    rule_version: str = MARKET_RESEARCH_RULE_VERSION
    contract_id: str = MARKET_RESEARCH_STATE_CONTRACT_ID
    research_usable: bool = False
    formal_usable: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "radarRunId": self.radar_run_id,
            "asOf": self.as_of.isoformat() if self.as_of else None,
            "state": self.state.value if self.state else None,
            "metrics": self.metrics.to_evidence() if self.metrics else None,
            "reasons": list(self.reasons),
            "snapshotSha256": self.snapshot_sha256,
            "ruleVersion": self.rule_version,
            "researchUsable": self.research_usable,
            "formalUsable": False,
        }


def _blocked(value: Any, *reasons: str) -> MarketResearchStateResult:
    return MarketResearchStateResult(
        status=MarketResearchStateStatus.BLOCKED,
        radar_run_id=getattr(value, "radar_run_id", None),
        as_of=getattr(value, "as_of", None),
        state=None,
        metrics=None,
        reasons=tuple(dict.fromkeys(reasons)),
        snapshot_sha256=None,
    )


def _canonical_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def produce_market_research_state(
    snapshot: Any,
    *,
    policy: MarketResearchStatePolicy = DEFAULT_MARKET_RESEARCH_STATE_POLICY,
) -> MarketResearchStateResult:
    if type(snapshot) is not MarketFeatureSnapshot:
        return _blocked(snapshot, "market_feature_contract_unverified")
    if type(policy) is not MarketResearchStatePolicy:
        return _blocked(snapshot, "market_research_policy_unverified")

    reasons = []
    for completeness in (
        snapshot.index_completeness,
        snapshot.breadth.completeness,
        snapshot.turnover.completeness,
    ):
        if not completeness.is_complete:
            reasons.append("market_features_incomplete")
            reasons.extend(completeness.reasons)
    if snapshot.turnover.unit_status is not UnitVerificationStatus.VERIFIED:
        reasons.append("market_turnover_unit_unverified")
    if not snapshot.turnover.formal_usable:
        reasons.append("market_turnover_not_usable")
    required_keys = set(MarketIndexKey)
    returned_keys = [item.index_key for item in snapshot.indices]
    if len(returned_keys) != len(set(returned_keys)) or set(returned_keys) != required_keys:
        reasons.append("market_index_scope_unverified")
    changes = [item.change_percent for item in snapshot.indices]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in changes
    ):
        reasons.append("market_index_change_unverified")
    valid_breadth_count = (
        snapshot.breadth.advancers
        + snapshot.breadth.decliners
        + snapshot.breadth.flat
    )
    if valid_breadth_count <= 0:
        reasons.append("market_breadth_empty")
    if reasons:
        return _blocked(snapshot, *reasons)

    numeric_changes = tuple(float(value) for value in changes)
    metrics = MarketResearchStateMetrics(
        advancer_ratio=(
            snapshot.breadth.advancers / valid_breadth_count
        ),
        decliner_ratio=(
            snapshot.breadth.decliners / valid_breadth_count
        ),
        positive_index_count=sum(value > 0 for value in numeric_changes),
        negative_index_count=sum(value < 0 for value in numeric_changes),
        flat_index_count=sum(value == 0 for value in numeric_changes),
        mean_index_change_percent=(sum(numeric_changes) / len(numeric_changes)),
    )
    if (
        metrics.advancer_ratio <= policy.risk_max_advancer_ratio
        and metrics.mean_index_change_percent
        <= policy.risk_max_mean_index_change_percent
        and metrics.negative_index_count >= policy.risk_min_negative_index_count
    ):
        state = MarketResearchState.RISK
    elif (
        metrics.advancer_ratio >= policy.strong_min_advancer_ratio
        and metrics.mean_index_change_percent
        >= policy.strong_min_mean_index_change_percent
        and metrics.positive_index_count >= policy.strong_min_positive_index_count
    ):
        state = MarketResearchState.STRONG
    elif (
        metrics.advancer_ratio <= policy.retreat_max_advancer_ratio
        and metrics.mean_index_change_percent
        < policy.retreat_max_mean_index_change_percent
        and metrics.negative_index_count
        >= policy.retreat_min_negative_index_count
    ):
        state = MarketResearchState.RETREAT
    else:
        state = MarketResearchState.OSCILLATION

    evidence = {
        "contractId": MARKET_RESEARCH_STATE_CONTRACT_ID,
        "radarRunId": snapshot.radar_run_id,
        "asOf": snapshot.as_of.isoformat(),
        "indexBatchId": snapshot.index_batch_id,
        "quoteBatchId": snapshot.quote_batch_id,
        "state": state.value,
        "metrics": metrics.to_evidence(),
        "policy": policy.to_evidence(),
        "researchUsable": True,
        "formalUsable": False,
    }
    return MarketResearchStateResult(
        status=MarketResearchStateStatus.READY,
        radar_run_id=snapshot.radar_run_id,
        as_of=snapshot.as_of,
        state=state,
        metrics=metrics,
        reasons=(),
        snapshot_sha256=_canonical_sha256(evidence),
        rule_version=policy.version,
        research_usable=True,
        formal_usable=False,
    )
