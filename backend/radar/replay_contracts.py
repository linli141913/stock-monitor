"""阶段9严格时间点回放输入合同。

本模块只校验调用方显式提供的历史证据，不读取当前证券名册、数据库或
网络。任何晚于样本 ``asOf`` 的来源、抓取或生效时间都会失败关闭。
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


RadarReplaySampleRole = Literal["development", "calibration", "holdout"]
RadarReplayEvidenceDomain = Literal[
    "security_universe",
    "trading_rule",
    "industry",
    "index",
    "etf",
    "corporate_action",
    "market",
    "sector",
    "leader",
]
RadarReplayEvidenceStatus = Literal[
    "ready",
    "missing",
    "unverifiable",
    "failed",
]
RadarReplayLabelDomain = Literal["market", "sector", "etf", "leader"]
RadarReplayLabelReviewStatus = Literal[
    "verified",
    "disputed",
    "unverifiable",
]
RadarReplayComparisonMode = Literal["state_exact", "objective_outcome"]

ALLOWED_OUTCOME_METRICS = {
    "market": frozenset({
        "environmentCorrect",
        "meanIndexReturn5d",
        "marketBreadthReturn5d",
        "maxDrawdown5d",
    }),
    "sector": frozenset({
        "mainlineDurationDays",
        "oneDayFalsePositive",
        "refluxIdentified",
        "ebbRecognitionDelayMinutes",
        "relativeMarketReturn3d",
        "relativeMarketReturn5d",
        "positiveReturnDays5d",
        "constituentCoverage",
    }),
    "leader": frozenset({
        "advancedFromPreliminary",
        "confirmedFromCandidate",
        "confirmedFailed",
        "falseLeader",
        "highLevelFalsePositive",
        "relativeSectorReturn3d",
        "relativeSectorReturn5d",
        "maxAdverseExcursion",
        "stateStable",
        "correctEmpty",
    }),
    "etf": frozenset({
        "return5d",
        "return10d",
        "return20d",
        "return1m",
        "return3m",
        "return6m",
        "relativeBenchmark",
        "maxDrawdown",
        "turnover",
        "estimatedCost",
    }),
}


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_timezone_required")
    return value


class RadarReplayModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class RadarReplayEvidence(RadarReplayModel):
    evidence_id: str = Field(alias="evidenceId", min_length=1, max_length=160)
    domain: RadarReplayEvidenceDomain
    source_id: str = Field(alias="sourceId", min_length=1, max_length=240)
    source: str = Field(min_length=1, max_length=240)
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    effective_from: Optional[datetime] = Field(
        default=None,
        alias="effectiveFrom",
    )
    status: RadarReplayEvidenceStatus
    payload: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_times(self):
        _aware(self.fetched_at, "fetchedAt")
        if self.source_time is not None:
            _aware(self.source_time, "sourceTime")
            if self.source_time > self.fetched_at:
                raise ValueError("sourceTime_after_fetchedAt")
        if self.effective_from is not None:
            _aware(self.effective_from, "effectiveFrom")
        return self


class RadarReplayExpectedLabel(RadarReplayModel):
    """独立比较结果；可来自状态审核或自动客观结果。

    结果允许在 ``asOf`` 之后形成，但不得晚于报告创建。
    """

    label_id: str = Field(alias="labelId", min_length=1, max_length=160)
    domain: RadarReplayLabelDomain
    target_id: str = Field(alias="targetId", min_length=1, max_length=160)
    comparison_mode: RadarReplayComparisonMode = Field(
        default="state_exact",
        alias="comparisonMode",
    )
    expected_state: Optional[str] = Field(
        default=None,
        alias="expectedState",
        min_length=1,
        max_length=120,
    )
    labeled_by: str = Field(alias="labeledBy", min_length=1, max_length=160)
    labeled_at: datetime = Field(alias="labeledAt")
    source_ids: List[str] = Field(alias="sourceIds", min_length=1)
    review_status: RadarReplayLabelReviewStatus = Field(alias="reviewStatus")
    independent_from_rule: Literal[True] = Field(alias="independentFromRule")
    outcome_metrics: Dict[str, Optional[Union[bool, float]]] = Field(
        default_factory=dict,
        alias="outcomeMetrics",
    )

    @model_validator(mode="after")
    def validate_label(self):
        _aware(self.labeled_at, "labeledAt")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate_label_source_id")
        if any(not value.strip() for value in self.source_ids):
            raise ValueError("label_source_id_empty")
        unknown = set(self.outcome_metrics) - ALLOWED_OUTCOME_METRICS[self.domain]
        if unknown:
            raise ValueError("label_outcome_metric_unverified")
        for value in self.outcome_metrics.values():
            if value is not None and not isinstance(value, (bool, int, float)):
                raise ValueError("label_outcome_metric_unverified")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(float(value)):
                    raise ValueError("label_outcome_metric_unverified")
        if self.comparison_mode == "state_exact":
            if self.expected_state is None:
                raise ValueError("expected_state_required")
        else:
            if self.expected_state is not None:
                raise ValueError("objective_outcome_state_forbidden")
            if not any(
                value is not None for value in self.outcome_metrics.values()
            ):
                raise ValueError("objective_outcome_metric_required")
        return self


class RadarReplaySample(RadarReplayModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=160)
    role: RadarReplaySampleRole
    as_of: datetime = Field(alias="asOf")
    radar_run_id: str = Field(alias="radarRunId", min_length=1, max_length=240)
    rule_version: str = Field(alias="ruleVersion", min_length=1, max_length=160)
    evidence: List[RadarReplayEvidence] = Field(min_length=1)
    expected_labels: List[RadarReplayExpectedLabel] = Field(
        default_factory=list,
        alias="expectedLabels",
    )

    @model_validator(mode="after")
    def validate_historical_boundary(self):
        as_of = _aware(self.as_of, "asOf")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate_evidence_id")
        for item in self.evidence:
            for field_name, value in (
                ("sourceTime", item.source_time),
                ("fetchedAt", item.fetched_at),
                ("effectiveFrom", item.effective_from),
            ):
                if value is not None and value > as_of:
                    raise ValueError(f"future_{field_name}")
        label_ids = [item.label_id for item in self.expected_labels]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("duplicate_label_id")
        label_targets = [
            (item.domain, item.target_id) for item in self.expected_labels
        ]
        if len(label_targets) != len(set(label_targets)):
            raise ValueError("duplicate_label_target")
        if any(
            item.labeled_at < as_of for item in self.expected_labels
        ):
            raise ValueError("label_before_sample_asOf")
        output_source_ids = {
            item.source_id
            for item in self.evidence
            if item.domain in {"market", "sector", "etf", "leader"}
        }
        if any(
            output_source_ids.intersection(label.source_ids)
            for label in self.expected_labels
        ):
            raise ValueError("label_rule_source_overlap")
        return self


class RadarReplayInput(RadarReplayModel):
    replay_run_id: str = Field(alias="replayRunId", min_length=1, max_length=240)
    created_at: datetime = Field(alias="createdAt")
    samples: List[RadarReplaySample] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_partitions(self):
        created_at = _aware(self.created_at, "createdAt")
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("duplicate_sample_id")
        identities = {}
        for sample in self.samples:
            if sample.as_of > created_at:
                raise ValueError("future_sample_asOf")
            identity = (sample.radar_run_id, sample.as_of)
            existing = identities.get(identity)
            if existing is not None and existing != sample.role:
                raise ValueError("sample_partition_overlap")
            identities[identity] = sample.role
            for label in sample.expected_labels:
                if label.labeled_at > created_at:
                    raise ValueError("future_labeledAt")
        return self


class RadarReplaySampleLabelSet(RadarReplayModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=160)
    radar_run_id: str = Field(alias="radarRunId", min_length=1, max_length=240)
    as_of: datetime = Field(alias="asOf")
    labels: List[RadarReplayExpectedLabel] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self):
        as_of = _aware(self.as_of, "asOf")
        label_ids = [item.label_id for item in self.labels]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("duplicate_label_id")
        label_targets = [
            (item.domain, item.target_id) for item in self.labels
        ]
        if len(label_targets) != len(set(label_targets)):
            raise ValueError("duplicate_label_target")
        if any(item.labeled_at < as_of for item in self.labels):
            raise ValueError("label_before_sample_asOf")
        return self


class RadarReplayLabelBundle(RadarReplayModel):
    contract_id: Literal["radar-replay-label-bundle-v1"] = Field(
        default="radar-replay-label-bundle-v1",
        alias="contractId",
    )
    bundle_id: str = Field(alias="bundleId", min_length=1, max_length=240)
    created_at: datetime = Field(alias="createdAt")
    samples: List[RadarReplaySampleLabelSet] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bundle(self):
        created_at = _aware(self.created_at, "createdAt")
        identities = [
            (item.sample_id, item.radar_run_id, item.as_of)
            for item in self.samples
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate_label_sample_identity")
        if any(item.as_of > created_at for item in self.samples):
            raise ValueError("future_label_sample_asOf")
        label_ids = [
            label.label_id
            for item in self.samples
            for label in item.labels
        ]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("duplicate_label_id")
        if any(
            label.labeled_at > created_at
            for item in self.samples
            for label in item.labels
        ):
            raise ValueError("future_labeledAt")
        return self


class RadarReplaySampleOutputSet(RadarReplayModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=160)
    radar_run_id: str = Field(alias="radarRunId", min_length=1, max_length=240)
    as_of: datetime = Field(alias="asOf")
    evidence: List[RadarReplayEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_outputs(self):
        as_of = _aware(self.as_of, "asOf")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate_evidence_id")
        for item in self.evidence:
            if item.domain not in {"market", "sector", "etf", "leader"}:
                raise ValueError("replay_output_domain_invalid")
            for field_name, value in (
                ("sourceTime", item.source_time),
                ("fetchedAt", item.fetched_at),
                ("effectiveFrom", item.effective_from),
            ):
                if value is not None and value > as_of:
                    raise ValueError(f"future_{field_name}")
        return self


class RadarReplayOutputBundle(RadarReplayModel):
    contract_id: Literal["radar-replay-output-bundle-v1"] = Field(
        default="radar-replay-output-bundle-v1",
        alias="contractId",
    )
    bundle_id: str = Field(alias="bundleId", min_length=1, max_length=240)
    created_at: datetime = Field(alias="createdAt")
    samples: List[RadarReplaySampleOutputSet] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bundle(self):
        created_at = _aware(self.created_at, "createdAt")
        identities = [
            (item.sample_id, item.radar_run_id, item.as_of)
            for item in self.samples
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate_output_sample_identity")
        if any(item.as_of > created_at for item in self.samples):
            raise ValueError("future_output_sample_asOf")
        if any(
            evidence.fetched_at > created_at
            for item in self.samples
            for evidence in item.evidence
        ):
            raise ValueError("future_output_fetchedAt")
        return self
