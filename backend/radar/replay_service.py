"""阶段9时间点回放质量评估。

这里只评估已经通过严格时点合同的显式输入。没有提供的结果指标保持
``None``，不会把缺失或不可验证改写成0。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import re
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from radar.replay_contracts import RadarReplayInput


REQUIRED_HISTORICAL_DOMAINS = frozenset({
    "security_universe",
    "trading_rule",
    "industry",
    "index",
    "etf",
    "corporate_action",
})
REQUIRED_SAMPLE_PARTITIONS = (
    "development",
    "calibration",
    "holdout",
)
REQUIRED_LABEL_DOMAINS = ("market", "sector", "etf", "leader")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class RadarReplayQualityReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    contract_id: Literal["radar-replay-quality-v2"] = Field(
        default="radar-replay-quality-v2",
        alias="contractId",
    )
    replay_run_id: str = Field(alias="replayRunId")
    created_at: datetime = Field(alias="createdAt")
    status: Literal["ready", "not_ready", "failed"]
    pipeline_status: Literal["ready", "not_ready", "failed"] = Field(
        default="not_ready",
        alias="pipelineStatus",
    )
    effectiveness_status: Literal["ready", "collecting"] = Field(
        default="collecting",
        alias="effectivenessStatus",
    )
    sample_counts: Dict[str, int] = Field(alias="sampleCounts")
    included_count: int = Field(alias="includedCount", ge=0)
    excluded_count: int = Field(alias="excludedCount", ge=0)
    scoped_exclusion_count: int = Field(
        default=0,
        alias="scopedExclusionCount",
        ge=0,
    )
    scoped_exclusion_counts: Dict[str, int] = Field(
        default_factory=dict,
        alias="scopedExclusionCounts",
    )
    missing_count: int = Field(alias="missingCount", ge=0)
    unverifiable_count: int = Field(alias="unverifiableCount", ge=0)
    failed_count: int = Field(alias="failedCount", ge=0)
    future_violation_count: int = Field(alias="futureViolationCount", ge=0)
    duplicate_state_violation_count: int = Field(
        alias="duplicateStateViolationCount",
        ge=0,
    )
    multi_state_violation_count: int = Field(
        alias="multiStateViolationCount",
        ge=0,
    )
    label_counts: Dict[str, int] = Field(
        default_factory=dict,
        alias="labelCounts",
    )
    output_counts: Dict[str, int] = Field(
        default_factory=dict,
        alias="outputCounts",
    )
    etf_readiness_counts: Dict[str, int] = Field(
        default_factory=lambda: {
            "formalAdmissionCount": 0,
            "monitoringReadyCount": 0,
            "monitoringMissingCount": 0,
            "rankingPolicyReadyCount": 0,
            "rankingPolicyMissingCount": 0,
        },
        alias="etfReadinessCounts",
    )
    comparable_label_count: int = Field(
        default=0,
        alias="comparableLabelCount",
        ge=0,
    )
    incomparable_label_count: int = Field(
        default=0,
        alias="incomparableLabelCount",
        ge=0,
    )
    disputed_label_count: int = Field(
        default=0,
        alias="disputedLabelCount",
        ge=0,
    )
    unverifiable_label_count: int = Field(
        default=0,
        alias="unverifiableLabelCount",
        ge=0,
    )
    unlabeled_output_target_count: int = Field(
        default=0,
        alias="unlabeledOutputTargetCount",
        ge=0,
    )
    unlabeled_output_target_counts: Dict[str, int] = Field(
        default_factory=dict,
        alias="unlabeledOutputTargetCounts",
    )
    partition_chronology_valid: bool = Field(
        default=False,
        alias="partitionChronologyValid",
    )
    missing_domains: List[str] = Field(default_factory=list, alias="missingDomains")
    missing_partitions: List[str] = Field(
        default_factory=list,
        alias="missingPartitions",
    )
    missing_label_domains: List[str] = Field(
        default_factory=list,
        alias="missingLabelDomains",
    )
    missing_output_domains: List[str] = Field(
        default_factory=list,
        alias="missingOutputDomains",
    )
    unverifiable_output_domains: List[str] = Field(
        default_factory=list,
        alias="unverifiableOutputDomains",
    )
    failed_output_domains: List[str] = Field(
        default_factory=list,
        alias="failedOutputDomains",
    )
    ready_output_domains: List[str] = Field(
        default_factory=list,
        alias="readyOutputDomains",
    )
    missing_label_partitions: List[str] = Field(
        default_factory=list,
        alias="missingLabelPartitions",
    )
    reason_codes: List[str] = Field(default_factory=list, alias="reasonCodes")
    metrics: Dict[str, Optional[Dict[str, float]]] = Field(default_factory=dict)


def _state_violations(replay: RadarReplayInput) -> tuple[int, int]:
    duplicate_count = 0
    multi_state_count = 0
    for sample in replay.samples:
        by_symbol = defaultdict(list)
        for evidence in sample.evidence:
            if evidence.domain != "leader" or evidence.status != "ready":
                continue
            states = evidence.payload.get("states")
            if not isinstance(states, list):
                continue
            for item in states:
                if not isinstance(item, dict):
                    continue
                symbol = str(
                    item.get("targetId") or item.get("symbol") or ""
                ).strip()
                state = str(item.get("state") or "").strip()
                if symbol and state:
                    by_symbol[symbol].append(state)
        for states in by_symbol.values():
            duplicate_count += max(0, len(states) - len(set(states)))
            if len(set(states)) > 1:
                multi_state_count += 1
    return duplicate_count, multi_state_count


def _output_states(sample, domain: str) -> Dict[str, str]:
    output = {}
    for evidence in sample.evidence:
        if evidence.domain != domain or evidence.status != "ready":
            continue
        states = evidence.payload.get("states")
        if not isinstance(states, list):
            continue
        for item in states:
            if not isinstance(item, dict):
                continue
            target_id = next((
                str(item.get(key) or "").strip()
                for key in (
                    "targetId",
                    "symbol",
                    "industryCode",
                    "etfCode",
                    "indexCode",
                )
                if str(item.get(key) or "").strip()
            ), "")
            state = str(item.get("state") or "").strip()
            if target_id and state:
                output[target_id] = state
    return output


def _sample_output_status(sample, domain: str) -> str:
    """只把携带 ``states`` 键的证据识别为规则输出。

    ``etf`` 同时是历史证据域和规则输出域，因此不能仅凭
    domain 把ETF名册来源失败误报为ETF规则生产器失败。
    """

    outputs = [
        evidence
        for evidence in sample.evidence
        if evidence.domain == domain and "states" in evidence.payload
    ]
    if not outputs:
        return "missing"
    if any(evidence.status == "failed" for evidence in outputs):
        return "failed"
    if any(evidence.status == "unverifiable" for evidence in outputs):
        return "unverifiable"
    if any(evidence.status == "missing" for evidence in outputs):
        return "missing"
    if any(
        evidence.status != "ready"
        or not isinstance(evidence.payload.get("states"), list)
        for evidence in outputs
    ):
        return "unverifiable"
    if domain == "etf" and any(
        evidence.source_id.startswith(
            "radar-etf-product-research-output-v2:"
        )
        and _etf_v2_readiness_counts(evidence.payload) is None
        for evidence in outputs
    ):
        return "unverifiable"
    return "ready"


def _etf_v2_readiness_counts(payload: object) -> Optional[Dict[str, int]]:
    if not isinstance(payload, dict):
        return None
    count_fields = (
        "formalAdmissionCount",
        "monitoringReadyCount",
        "monitoringMissingCount",
        "rankingPolicyReadyCount",
        "rankingPolicyMissingCount",
    )
    counts = {field: payload.get(field) for field in count_fields}
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        for value in counts.values()
    ):
        return None
    if any(
        re.fullmatch(r"[0-9a-f]{64}", str(payload.get(field) or ""))
        is None
        for field in (
            "snapshotSha256",
            "formalAdmissionSnapshotSha256",
        )
    ):
        return None
    states = payload.get("states")
    if not isinstance(states, list):
        return None
    formal_states = [
        item for item in states
        if isinstance(item, dict)
        and (
            "monitoringStatus" in item
            or "rankingStatus" in item
        )
    ]
    if len(formal_states) != counts["formalAdmissionCount"]:
        return None
    monitoring = Counter()
    ranking = Counter()
    for item in formal_states:
        monitoring_status = item.get("monitoringStatus")
        ranking_status = item.get("rankingStatus")
        monitoring_reasons = item.get("monitoringReasons")
        ranking_reasons = item.get("rankingReasons")
        if any((
            monitoring_status not in {"ready", "missing"},
            ranking_status not in {"ready", "missing"},
            not isinstance(monitoring_reasons, list),
            not isinstance(ranking_reasons, list),
            isinstance(monitoring_reasons, list)
            and any(
                not isinstance(reason, str) or not reason
                for reason in monitoring_reasons
            ),
            isinstance(ranking_reasons, list)
            and any(
                not isinstance(reason, str) or not reason
                for reason in ranking_reasons
            ),
            monitoring_status == "ready" and bool(monitoring_reasons),
            monitoring_status == "missing" and not monitoring_reasons,
            ranking_status == "ready" and bool(ranking_reasons),
            ranking_status == "missing" and not ranking_reasons,
        )):
            return None
        monitoring[monitoring_status] += 1
        ranking[ranking_status] += 1
    expected = {
        "formalAdmissionCount": len(formal_states),
        "monitoringReadyCount": monitoring["ready"],
        "monitoringMissingCount": monitoring["missing"],
        "rankingPolicyReadyCount": ranking["ready"],
        "rankingPolicyMissingCount": ranking["missing"],
    }
    return expected if counts == expected else None


def _etf_readiness_counts(replay: RadarReplayInput) -> Dict[str, int]:
    total = Counter()
    for sample in replay.samples:
        for evidence in sample.evidence:
            if (
                evidence.domain != "etf"
                or evidence.status != "ready"
                or not evidence.source_id.startswith(
                    "radar-etf-product-research-output-v2:"
                )
            ):
                continue
            counts = _etf_v2_readiness_counts(evidence.payload)
            if counts is not None:
                total.update(counts)
    return {
        "formalAdmissionCount": total["formalAdmissionCount"],
        "monitoringReadyCount": total["monitoringReadyCount"],
        "monitoringMissingCount": total["monitoringMissingCount"],
        "rankingPolicyReadyCount": total["rankingPolicyReadyCount"],
        "rankingPolicyMissingCount": total["rankingPolicyMissingCount"],
    }


def _output_domain_status(replay: RadarReplayInput, domain: str) -> str:
    statuses = {
        _sample_output_status(sample, domain) for sample in replay.samples
    }
    for status in ("failed", "unverifiable", "missing"):
        if status in statuses:
            return status
    return "ready"


def _label_quality(replay: RadarReplayInput):
    label_counts = Counter()
    verified_partitions = set()
    comparable = 0
    incomparable = 0
    disputed = 0
    unverifiable = 0
    metric_values = {
        domain: defaultdict(list) for domain in REQUIRED_LABEL_DOMAINS
    }
    exact = Counter()
    comparable_by_domain = Counter()
    state_comparable_by_domain = Counter()
    objective_outcome_by_domain = Counter()
    required_output_targets = {
        domain: set() for domain in REQUIRED_LABEL_DOMAINS
    }
    labeled_output_targets = {
        domain: set() for domain in REQUIRED_LABEL_DOMAINS
    }

    for sample in replay.samples:
        outputs = {
            domain: _output_states(sample, domain)
            for domain in REQUIRED_LABEL_DOMAINS
        }
        for domain, states in outputs.items():
            required_output_targets[domain].update(
                (sample.sample_id, target_id) for target_id in states
            )
        if (
            not outputs["leader"]
            and _sample_output_status(sample, "leader") == "ready"
        ):
            required_output_targets["leader"].add(
                (sample.sample_id, "__empty__")
            )
        for label in sample.expected_labels:
            label_counts[label.domain] += 1
            if label.review_status == "disputed":
                disputed += 1
                continue
            if label.review_status == "unverifiable":
                unverifiable += 1
                continue
            actual_state = outputs[label.domain].get(label.target_id)
            if (
                actual_state is None
                and label.domain == "leader"
                and label.target_id == "__empty__"
                and not outputs["leader"]
                and _sample_output_status(sample, "leader") == "ready"
                and label.comparison_mode == "objective_outcome"
                and label.outcome_metrics.get("correctEmpty") is not None
            ):
                actual_state = "__empty__"
            if actual_state is None:
                incomparable += 1
                continue
            verified_partitions.add(sample.role)
            comparable += 1
            comparable_by_domain[label.domain] += 1
            labeled_output_targets[label.domain].add(
                (sample.sample_id, label.target_id)
            )
            if label.comparison_mode == "state_exact":
                state_comparable_by_domain[label.domain] += 1
                if actual_state == label.expected_state:
                    exact[label.domain] += 1
            else:
                objective_outcome_by_domain[label.domain] += 1
            for metric_name, value in label.outcome_metrics.items():
                if value is not None:
                    metric_values[label.domain][metric_name].append(float(value))

    metrics: Dict[str, Optional[Dict[str, float]]] = {}
    for domain in REQUIRED_LABEL_DOMAINS:
        count = comparable_by_domain[domain]
        if count == 0:
            metrics[domain] = None
            continue
        values: Dict[str, float] = {
            "comparableLabelCount": float(count),
        }
        objective_count = objective_outcome_by_domain[domain]
        if objective_count:
            values["objectiveOutcomeCount"] = float(objective_count)
        state_count = state_comparable_by_domain[domain]
        if state_count:
            values.update({
                "stateComparableCount": float(state_count),
                "exactMatchCount": float(exact[domain]),
                "exactMatchRate": exact[domain] / state_count,
            })
        for metric_name, items in sorted(metric_values[domain].items()):
            values[f"{metric_name}SampleCount"] = float(len(items))
            values[f"{metric_name}Mean"] = sum(items) / len(items)
        metrics[domain] = values

    missing_domains = [
        domain
        for domain in REQUIRED_LABEL_DOMAINS
        if comparable_by_domain[domain] == 0
    ]
    missing_partitions = [
        partition
        for partition in REQUIRED_SAMPLE_PARTITIONS
        if partition not in verified_partitions
    ]
    unlabeled_output_target_counts = {
        domain: len(
            required_output_targets[domain]
            - labeled_output_targets[domain]
        )
        for domain in REQUIRED_LABEL_DOMAINS
    }
    return {
        "label_counts": {
            domain: label_counts[domain] for domain in REQUIRED_LABEL_DOMAINS
        },
        "comparable": comparable,
        "incomparable": incomparable,
        "disputed": disputed,
        "unverifiable": unverifiable,
        "missing_domains": missing_domains,
        "missing_partitions": missing_partitions,
        "unlabeled_output_target_counts": unlabeled_output_target_counts,
        "unlabeled_output_target_count": sum(
            unlabeled_output_target_counts.values()
        ),
        "metrics": metrics,
    }


def build_replay_quality_report(
    replay: RadarReplayInput,
) -> RadarReplayQualityReport:
    if not isinstance(replay, RadarReplayInput):
        raise TypeError("replay必须是RadarReplayInput")

    sample_counts = Counter(sample.role for sample in replay.samples)
    missing_partitions = [
        partition
        for partition in REQUIRED_SAMPLE_PARTITIONS
        if sample_counts[partition] == 0
    ]
    roles_by_trade_date = defaultdict(set)
    for sample in replay.samples:
        roles_by_trade_date[
            sample.as_of.astimezone(SHANGHAI_TZ).date()
        ].add(sample.role)
    partition_trade_date_overlap = any(
        len(roles) > 1 for roles in roles_by_trade_date.values()
    )
    trade_dates_by_role = {
        role: sorted(
            sample.as_of.astimezone(SHANGHAI_TZ).date()
            for sample in replay.samples
            if sample.role == role
        )
        for role in REQUIRED_SAMPLE_PARTITIONS
    }
    chronology_pairs = [
        (left_role, right_role)
        for left_index, left_role in enumerate(REQUIRED_SAMPLE_PARTITIONS)
        for right_role in REQUIRED_SAMPLE_PARTITIONS[left_index + 1:]
        if trade_dates_by_role[left_role] and trade_dates_by_role[right_role]
    ]
    partition_chronology_ordered = all(
        max(trade_dates_by_role[left_role])
        < min(trade_dates_by_role[right_role])
        for left_role, right_role in chronology_pairs
    )
    partition_chronology_valid = (
        not missing_partitions
        and not partition_trade_date_overlap
        and partition_chronology_ordered
    )
    partition_chronology_violation = (
        not partition_trade_date_overlap
        and not partition_chronology_ordered
    )
    missing_domains = set()
    missing_count = 0
    unverifiable_count = 0
    failed_count = 0
    excluded_count = 0
    scoped_exclusion_counts = Counter()

    for sample in replay.samples:
        present_domains = {item.domain for item in sample.evidence}
        explicit_missing_domains = {
            item.domain for item in sample.evidence if item.status == "missing"
        }
        absent = REQUIRED_HISTORICAL_DOMAINS - present_domains
        sample_missing_domains = (
            absent
            | (REQUIRED_HISTORICAL_DOMAINS & explicit_missing_domains)
        )
        missing_domains.update(sample_missing_domains)
        missing_count += len(sample_missing_domains)
        sample_unverifiable = sum(
            item.status == "unverifiable" for item in sample.evidence
        )
        sample_failed = sum(item.status == "failed" for item in sample.evidence)
        unverifiable_count += sample_unverifiable
        failed_count += sample_failed
        if sample_missing_domains or sample_unverifiable or sample_failed:
            excluded_count += 1
        for item in sample.evidence:
            if item.status != "ready" or item.payload.get(
                "scopedReplayReady"
            ) is not True:
                continue
            count_fields = {
                "industry": ("excludedCurrentMasterCount",),
                "corporate_action": (
                    "excludedDocumentCount",
                    "excludedOutOfScopeCount",
                ),
            }.get(item.domain)
            if count_fields is None:
                continue
            for count_field in count_fields:
                value = item.payload.get(count_field)
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value > 0
                ):
                    scoped_exclusion_counts[item.domain] += value

    duplicate_states, multi_states = _state_violations(replay)
    labels = _label_quality(replay)
    output_counts = {
        domain: sum(
            len(_output_states(sample, domain))
            for sample in replay.samples
        )
        for domain in REQUIRED_LABEL_DOMAINS
    }
    etf_readiness_counts = _etf_readiness_counts(replay)
    output_domain_statuses = {
        domain: _output_domain_status(replay, domain)
        for domain in REQUIRED_LABEL_DOMAINS
    }
    missing_output_domains = [
        domain for domain in REQUIRED_LABEL_DOMAINS
        if output_domain_statuses[domain] == "missing"
    ]
    unverifiable_output_domains = [
        domain for domain in REQUIRED_LABEL_DOMAINS
        if output_domain_statuses[domain] == "unverifiable"
    ]
    failed_output_domains = [
        domain for domain in REQUIRED_LABEL_DOMAINS
        if output_domain_statuses[domain] == "failed"
    ]
    ready_output_domains = [
        domain for domain in REQUIRED_LABEL_DOMAINS
        if output_domain_statuses[domain] == "ready"
    ]
    reasons = []
    if missing_count:
        reasons.append("replay_required_domain_missing")
    if missing_partitions:
        reasons.append("replay_partition_missing")
    if partition_trade_date_overlap:
        reasons.append("replay_partition_trade_date_overlap")
    if partition_chronology_violation:
        reasons.append("replay_partition_chronology_violation")
    if unverifiable_count:
        reasons.append("replay_evidence_unverifiable")
    if failed_count:
        reasons.append("replay_source_failed")
    if duplicate_states:
        reasons.append("replay_duplicate_state_violation")
    if multi_states:
        reasons.append("replay_multi_state_violation")
    if labels["missing_domains"]:
        reasons.append("replay_objective_outcomes_missing")
    if missing_output_domains:
        reasons.append("replay_rule_outputs_missing")
    if unverifiable_output_domains:
        reasons.append("replay_rule_outputs_unverifiable")
    if failed_output_domains:
        reasons.append("replay_rule_outputs_failed")
    if labels["missing_partitions"]:
        reasons.append("replay_label_partition_missing")
    if labels["incomparable"]:
        reasons.append("replay_label_output_incomparable")
    if labels["disputed"]:
        reasons.append("replay_label_disputed")
    if labels["unverifiable"]:
        reasons.append("replay_label_unverifiable")
    if labels["unlabeled_output_target_count"]:
        reasons.append("replay_output_target_outcomes_incomplete")
    if scoped_exclusion_counts:
        reasons.append("replay_scoped_exclusions_present")

    if (
        failed_count
        or failed_output_domains
        or duplicate_states
        or multi_states
    ):
        pipeline_status = "failed"
    elif (
        missing_count
        or unverifiable_count
        or missing_partitions
        or partition_trade_date_overlap
        or partition_chronology_violation
        or missing_output_domains
        or unverifiable_output_domains
    ):
        pipeline_status = "not_ready"
    else:
        pipeline_status = "ready"

    effectiveness_status = (
        "collecting"
        if (
            labels["missing_domains"]
            or labels["missing_partitions"]
            or labels["incomparable"]
            or labels["disputed"]
            or labels["unverifiable"]
            or labels["unlabeled_output_target_count"]
        )
        else "ready"
    )

    if pipeline_status == "failed":
        status = "failed"
    elif (
        pipeline_status != "ready"
        or effectiveness_status != "ready"
        or labels["missing_domains"]
        or labels["missing_partitions"]
        or labels["incomparable"]
        or labels["disputed"]
        or labels["unverifiable"]
        or labels["unlabeled_output_target_count"]
    ):
        status = "not_ready"
    else:
        status = "ready"

    included_count = len(replay.samples) - excluded_count
    return RadarReplayQualityReport(
        replayRunId=replay.replay_run_id,
        createdAt=replay.created_at,
        status=status,
        pipelineStatus=pipeline_status,
        effectivenessStatus=effectiveness_status,
        sampleCounts={
            "development": sample_counts["development"],
            "calibration": sample_counts["calibration"],
            "holdout": sample_counts["holdout"],
        },
        includedCount=included_count,
        excludedCount=excluded_count,
        scopedExclusionCount=sum(scoped_exclusion_counts.values()),
        scopedExclusionCounts=dict(sorted(scoped_exclusion_counts.items())),
        missingCount=missing_count,
        unverifiableCount=unverifiable_count,
        failedCount=failed_count,
        futureViolationCount=0,
        duplicateStateViolationCount=duplicate_states,
        multiStateViolationCount=multi_states,
        labelCounts=labels["label_counts"],
        outputCounts=output_counts,
        etfReadinessCounts=etf_readiness_counts,
        comparableLabelCount=labels["comparable"],
        incomparableLabelCount=labels["incomparable"],
        disputedLabelCount=labels["disputed"],
        unverifiableLabelCount=labels["unverifiable"],
        unlabeledOutputTargetCount=labels["unlabeled_output_target_count"],
        unlabeledOutputTargetCounts=(
            labels["unlabeled_output_target_counts"]
        ),
        partitionChronologyValid=partition_chronology_valid,
        missingDomains=sorted(missing_domains),
        missingPartitions=missing_partitions,
        missingLabelDomains=labels["missing_domains"],
        missingOutputDomains=missing_output_domains,
        unverifiableOutputDomains=unverifiable_output_domains,
        failedOutputDomains=failed_output_domains,
        readyOutputDomains=ready_output_domains,
        missingLabelPartitions=labels["missing_partitions"],
        reasonCodes=reasons,
        metrics=labels["metrics"],
    )
