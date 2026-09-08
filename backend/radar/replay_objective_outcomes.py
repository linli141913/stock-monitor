"""阶段9到期客观结果的只读回收与标签转换。

本模块不读取数据库，不把规则状态交给结果提供方，也不推断缺失值。样本
只有在官方A股日历确认至少五个完整交易日已经收盘后才会请求结果来源；
只有带独立来源和允许指标的 ``ready`` 观察才会进入标签包。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
from typing import Callable, Dict, List, Literal, Mapping, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from market_calendar import get_calendar_day_kind
from radar.replay_contracts import (
    ALLOWED_OUTCOME_METRICS,
    RadarReplayExpectedLabel,
    RadarReplayLabelBundle,
    RadarReplayModel,
    RadarReplayOutputBundle,
    RadarReplaySampleLabelSet,
    RadarReplaySampleRole,
)
from radar.replay_forward_baseline import _atomic_write_json, _validate_output_dir
from radar.replay_label_tasks import RadarReplayLabelTaskBundle
from radar.replay_assembly import _private_tmp_child, _safe_file, _sha256


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
REQUIRED_DOMAINS = ("market", "sector", "etf", "leader")
MINIMUM_MATURITY_TRADING_DAYS = 5
SAFE_CLOSE_TIME = time(15, 5)
OutcomeStatus = Literal["ready", "missing", "unverifiable", "failed"]


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_timezone_required")
    return value


class ObjectiveOutcomeObservation(RadarReplayModel):
    status: OutcomeStatus
    observed_through: datetime = Field(alias="observedThrough")
    source_ids: List[str] = Field(default_factory=list, alias="sourceIds")
    outcome_metrics: Dict[str, Optional[Union[bool, float]]] = Field(
        default_factory=dict,
        alias="outcomeMetrics",
    )
    reasons: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_observation(self):
        _aware(self.observed_through, "observedThrough")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate_objective_outcome_source_id")
        if any(not item.strip() for item in self.source_ids):
            raise ValueError("objective_outcome_source_id_empty")
        if any(not item.strip() for item in self.reasons):
            raise ValueError("objective_outcome_reason_empty")
        if self.status == "ready":
            if not self.source_ids:
                raise ValueError("objective_outcome_source_required")
            if self.reasons:
                raise ValueError("ready_objective_outcome_reason_forbidden")
            if not any(value is not None for value in self.outcome_metrics.values()):
                raise ValueError("objective_outcome_metric_required")
        else:
            if not self.reasons:
                raise ValueError("objective_outcome_reason_required")
            if any(value is not None for value in self.outcome_metrics.values()):
                raise ValueError("unready_objective_outcome_metric_forbidden")
        return self


class ObjectiveOutcomeRecord(ObjectiveOutcomeObservation):
    domain: Literal["market", "sector", "etf", "leader"]
    target_id: str = Field(alias="targetId", min_length=1, max_length=160)


class ObjectiveOutcomeSample(RadarReplayModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=160)
    radar_run_id: str = Field(alias="radarRunId", min_length=1, max_length=240)
    role: RadarReplaySampleRole
    as_of: datetime = Field(alias="asOf")
    status: Literal[
        "pending_maturity", "ready", "partial", "unverifiable", "failed"
    ]
    maturity_date: Optional[date] = Field(default=None, alias="maturityDate")
    evaluation_trade_dates: List[date] = Field(
        default_factory=list,
        alias="evaluationTradeDates",
    )
    observations: List[ObjectiveOutcomeRecord] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)


class ObjectiveOutcomeBundle(RadarReplayModel):
    contract_id: Literal["radar-replay-objective-outcome-v1"] = Field(
        default="radar-replay-objective-outcome-v1",
        alias="contractId",
    )
    collection_id: str = Field(alias="collectionId", min_length=1, max_length=240)
    task_bundle_id: str = Field(alias="taskBundleId", min_length=1, max_length=240)
    output_bundle_id: str = Field(alias="outputBundleId", min_length=1, max_length=240)
    evaluated_at: datetime = Field(alias="evaluatedAt")
    status: Literal["pending_maturity", "ready", "partial", "failed"]
    sample_count: int = Field(alias="sampleCount", ge=1)
    pending_sample_count: int = Field(alias="pendingSampleCount", ge=0)
    ready_observation_count: int = Field(alias="readyObservationCount", ge=0)
    missing_observation_count: int = Field(alias="missingObservationCount", ge=0)
    unverifiable_observation_count: int = Field(
        alias="unverifiableObservationCount",
        ge=0,
    )
    failed_observation_count: int = Field(alias="failedObservationCount", ge=0)
    daily_snapshot_ids: List[str] = Field(
        default_factory=list,
        alias="dailySnapshotIds",
    )
    samples: List[ObjectiveOutcomeSample] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self):
        _aware(self.evaluated_at, "evaluatedAt")
        if self.sample_count != len(self.samples):
            raise ValueError("objective_outcome_sample_count_mismatch")
        expected_pending = sum(
            item.status == "pending_maturity" for item in self.samples
        )
        if self.pending_sample_count != expected_pending:
            raise ValueError("objective_outcome_pending_count_mismatch")
        counts = Counter(
            observation.status
            for sample in self.samples
            for observation in sample.observations
        )
        if any((
            self.ready_observation_count != counts["ready"],
            self.missing_observation_count != counts["missing"],
            self.unverifiable_observation_count != counts["unverifiable"],
            self.failed_observation_count != counts["failed"],
        )):
            raise ValueError("objective_outcome_observation_count_mismatch")
        return self


@dataclass(frozen=True)
class ObjectiveOutcomeRequest:
    task_bundle_id: str
    output_bundle_id: str
    sample_id: str
    radar_run_id: str
    role: RadarReplaySampleRole
    as_of: datetime
    domain: str
    target_id: str
    maturity_date: date
    evaluation_trade_dates: Tuple[date, ...]
    output_source_ids: Tuple[str, ...]


OutcomeProvider = Callable[[ObjectiveOutcomeRequest], ObjectiveOutcomeObservation]


@dataclass(frozen=True)
class ObjectiveOutcomeCollectionResult:
    output_dir: Path
    outcome_bundle_path: Path
    label_bundle_path: Optional[Path]
    manifest_path: Path
    bundle: ObjectiveOutcomeBundle
    label_bundle: Optional[RadarReplayLabelBundle]


def _official_cn_day_kind(day: date) -> str:
    return get_calendar_day_kind("cn", day).kind


def _completed_trade_dates(
    *,
    as_of: datetime,
    evaluated_at: datetime,
    day_kind_provider: Callable[[date], str],
) -> Tuple[Tuple[date, ...], bool]:
    local_as_of = as_of.astimezone(SHANGHAI_TZ)
    local_evaluated = evaluated_at.astimezone(SHANGHAI_TZ)
    cutoff = local_evaluated.date()
    if local_evaluated.time().replace(tzinfo=None) < SAFE_CLOSE_TIME:
        cutoff -= timedelta(days=1)
    current = local_as_of.date() + timedelta(days=1)
    trading_dates = []
    calendar_verified = True
    while current <= cutoff and len(trading_dates) < MINIMUM_MATURITY_TRADING_DAYS:
        kind = day_kind_provider(current)
        if kind == "unknown":
            calendar_verified = False
            break
        if kind in {"full", "half"}:
            trading_dates.append(current)
        elif kind != "closed":
            raise ValueError("objective_outcome_calendar_kind_invalid")
        current += timedelta(days=1)
    return tuple(trading_dates), calendar_verified


def _output_targets(output_set, domain: str):
    targets = set()
    source_ids = set()
    outputs_seen = False
    for evidence in output_set.evidence:
        if evidence.domain != domain or "states" not in evidence.payload:
            continue
        outputs_seen = True
        source_ids.add(evidence.source_id)
        if evidence.status != "ready":
            continue
        states = evidence.payload.get("states")
        if not isinstance(states, list):
            raise ValueError("objective_outcome_output_states_unverified")
        for item in states:
            if not isinstance(item, dict):
                raise ValueError("objective_outcome_output_states_unverified")
            target_id = str(
                item.get("targetId")
                or item.get("symbol")
                or item.get("industryCode")
                or item.get("etfCode")
                or ""
            ).strip()
            if not target_id:
                raise ValueError("objective_outcome_target_id_missing")
            if target_id in targets:
                raise ValueError("objective_outcome_duplicate_target")
            targets.add(target_id)
    if not outputs_seen:
        return (), tuple(sorted(source_ids)), "objective_outcome_rule_output_missing"
    if not targets and domain == "leader":
        targets.add("__empty__")
    reason = None if targets else "objective_outcome_target_empty"
    return tuple(sorted(targets)), tuple(sorted(source_ids)), reason


def _observation_record(
    *,
    request: ObjectiveOutcomeRequest,
    observation: ObjectiveOutcomeObservation,
    evaluated_at: datetime,
) -> ObjectiveOutcomeRecord:
    if not isinstance(observation, ObjectiveOutcomeObservation):
        raise ValueError("objective_outcome_observation_unverified")
    if observation.observed_through > evaluated_at:
        raise ValueError("objective_outcome_observation_from_future")
    unknown = set(observation.outcome_metrics) - ALLOWED_OUTCOME_METRICS[
        request.domain
    ]
    if unknown:
        raise ValueError("objective_outcome_metric_unverified")
    if set(observation.source_ids).intersection(request.output_source_ids):
        raise ValueError("objective_outcome_rule_source_overlap")
    if observation.status == "ready":
        local_observed = observation.observed_through.astimezone(SHANGHAI_TZ)
        if (
            local_observed.date() < request.maturity_date
            or (
                local_observed.date() == request.maturity_date
                and local_observed.time().replace(tzinfo=None) < SAFE_CLOSE_TIME
            )
        ):
            raise ValueError("objective_outcome_observation_before_maturity")
    return ObjectiveOutcomeRecord(
        domain=request.domain,
        targetId=request.target_id,
        **observation.model_dump(mode="python", by_alias=True),
    )


def collect_replay_objective_outcomes(
    *,
    task_bundle: RadarReplayLabelTaskBundle,
    output_bundle: RadarReplayOutputBundle,
    output_dir: Path,
    evaluated_at: datetime,
    providers: Mapping[str, OutcomeProvider],
    day_kind_provider: Callable[[date], str] = _official_cn_day_kind,
    daily_snapshot_ids: Sequence[str] = (),
) -> ObjectiveOutcomeCollectionResult:
    if not isinstance(task_bundle, RadarReplayLabelTaskBundle):
        raise TypeError("task_bundle必须是RadarReplayLabelTaskBundle")
    if not isinstance(output_bundle, RadarReplayOutputBundle):
        raise TypeError("output_bundle必须是RadarReplayOutputBundle")
    _aware(evaluated_at, "evaluatedAt")
    if len(daily_snapshot_ids) != len(set(daily_snapshot_ids)):
        raise ValueError("duplicate_objective_daily_snapshot_id")
    resolved_output = _validate_output_dir(Path(output_dir))

    tasks = {
        (item.sample_id, item.radar_run_id, item.as_of): item
        for item in task_bundle.samples
    }
    outputs = {
        (item.sample_id, item.radar_run_id, item.as_of): item
        for item in output_bundle.samples
    }
    if set(tasks) != set(outputs):
        raise ValueError("objective_outcome_sample_identity_mismatch")

    sample_results = []
    label_sets = []
    for identity, task in sorted(tasks.items(), key=lambda item: item[0][2]):
        output_set = outputs[identity]
        trade_dates, calendar_verified = _completed_trade_dates(
            as_of=task.as_of,
            evaluated_at=evaluated_at,
            day_kind_provider=day_kind_provider,
        )
        if not calendar_verified:
            sample_results.append(ObjectiveOutcomeSample(
                sampleId=task.sample_id,
                radarRunId=task.radar_run_id,
                role=task.role,
                asOf=task.as_of,
                status="unverifiable",
                maturityDate=None,
                evaluationTradeDates=list(trade_dates),
                observations=[],
                reasons=["objective_outcome_calendar_unverifiable"],
            ))
            continue
        if len(trade_dates) < task.minimum_maturity_trading_days:
            sample_results.append(ObjectiveOutcomeSample(
                sampleId=task.sample_id,
                radarRunId=task.radar_run_id,
                role=task.role,
                asOf=task.as_of,
                status="pending_maturity",
                maturityDate=None,
                evaluationTradeDates=list(trade_dates),
                observations=[],
                reasons=["objective_outcome_minimum_maturity_not_reached"],
            ))
            continue

        maturity_date = trade_dates[task.minimum_maturity_trading_days - 1]
        observations = []
        sample_reasons = []
        for domain in REQUIRED_DOMAINS:
            targets, output_source_ids, output_reason = _output_targets(
                output_set,
                domain,
            )
            if output_reason is not None:
                sample_reasons.append(f"{output_reason}:{domain}")
            for target_id in targets:
                request = ObjectiveOutcomeRequest(
                    task_bundle_id=task_bundle.task_bundle_id,
                    output_bundle_id=output_bundle.bundle_id,
                    sample_id=task.sample_id,
                    radar_run_id=task.radar_run_id,
                    role=task.role,
                    as_of=task.as_of,
                    domain=domain,
                    target_id=target_id,
                    maturity_date=maturity_date,
                    evaluation_trade_dates=trade_dates,
                    output_source_ids=output_source_ids,
                )
                provider = providers.get(domain)
                if provider is None:
                    observation = ObjectiveOutcomeObservation(
                        status="missing",
                        observedThrough=evaluated_at,
                        sourceIds=[],
                        outcomeMetrics={},
                        reasons=["objective_outcome_provider_missing"],
                    )
                else:
                    try:
                        observation = provider(request)
                    except Exception as exc:
                        observation = ObjectiveOutcomeObservation(
                            status="failed",
                            observedThrough=evaluated_at,
                            sourceIds=[],
                            outcomeMetrics={},
                            reasons=[
                                "objective_outcome_source_failed:"
                                f"{type(exc).__name__}"
                            ],
                        )
                observations.append(_observation_record(
                    request=request,
                    observation=observation,
                    evaluated_at=evaluated_at,
                ))

        counts = Counter(item.status for item in observations)
        if counts["failed"]:
            sample_status = "failed"
        elif counts["unverifiable"]:
            sample_status = "unverifiable"
        elif counts["missing"] or sample_reasons:
            sample_status = "partial"
        else:
            sample_status = "ready"
        sample_results.append(ObjectiveOutcomeSample(
            sampleId=task.sample_id,
            radarRunId=task.radar_run_id,
            role=task.role,
            asOf=task.as_of,
            status=sample_status,
            maturityDate=maturity_date,
            evaluationTradeDates=list(trade_dates),
            observations=observations,
            reasons=sample_reasons,
        ))

        labels = []
        for item in observations:
            if item.status != "ready":
                continue
            label_digest = hashlib.sha256(
                f"{task.sample_id}|{item.domain}|{item.target_id}|"
                f"{item.observed_through.isoformat()}|"
                f"{'|'.join(item.source_ids)}".encode("utf-8")
            ).hexdigest()
            labels.append(RadarReplayExpectedLabel(
                labelId=f"objective-{label_digest[:40]}",
                domain=item.domain,
                targetId=item.target_id,
                comparisonMode="objective_outcome",
                expectedState=None,
                labeledBy="automatic-objective-outcome-v1",
                labeledAt=evaluated_at,
                sourceIds=item.source_ids,
                reviewStatus="verified",
                independentFromRule=True,
                outcomeMetrics=item.outcome_metrics,
            ))
        if labels:
            label_sets.append(RadarReplaySampleLabelSet(
                sampleId=task.sample_id,
                radarRunId=task.radar_run_id,
                asOf=task.as_of,
                labels=labels,
            ))

    observation_counts = Counter(
        observation.status
        for sample in sample_results
        for observation in sample.observations
    )
    sample_statuses = {sample.status for sample in sample_results}
    if sample_statuses == {"pending_maturity"}:
        bundle_status = "pending_maturity"
    elif "failed" in sample_statuses:
        bundle_status = "failed"
    elif sample_statuses == {"ready"}:
        bundle_status = "ready"
    else:
        bundle_status = "partial"
    identity = hashlib.sha256(
        f"{task_bundle.task_bundle_id}|{output_bundle.bundle_id}|"
        f"{evaluated_at.isoformat()}|{'|'.join(daily_snapshot_ids)}".encode(
            "utf-8"
        )
    ).hexdigest()
    bundle = ObjectiveOutcomeBundle(
        collectionId=f"stage9-objective-outcome-{identity[:24]}",
        taskBundleId=task_bundle.task_bundle_id,
        outputBundleId=output_bundle.bundle_id,
        evaluatedAt=evaluated_at,
        status=bundle_status,
        sampleCount=len(sample_results),
        pendingSampleCount=sum(
            sample.status == "pending_maturity" for sample in sample_results
        ),
        readyObservationCount=observation_counts["ready"],
        missingObservationCount=observation_counts["missing"],
        unverifiableObservationCount=observation_counts["unverifiable"],
        failedObservationCount=observation_counts["failed"],
        dailySnapshotIds=list(daily_snapshot_ids),
        samples=sample_results,
    )
    label_bundle = None
    if label_sets:
        label_bundle = RadarReplayLabelBundle(
            bundleId=f"stage9-objective-labels-{identity[:24]}",
            createdAt=evaluated_at,
            samples=label_sets,
        )

    resolved_output.mkdir(parents=True, exist_ok=False)
    outcome_path = resolved_output / "objective-outcomes.json"
    outcome_sha = _atomic_write_json(outcome_path, bundle)
    label_path = None
    files = {
        "objectiveOutcomes": {
            "path": outcome_path.name,
            "sha256": outcome_sha,
        },
    }
    if label_bundle is not None:
        label_path = resolved_output / "label-bundle.json"
        label_sha = _atomic_write_json(label_path, label_bundle)
        files["labelBundle"] = {
            "path": label_path.name,
            "sha256": label_sha,
        }
    manifest_path = resolved_output / "manifest.json"
    _atomic_write_json(manifest_path, {
        "contractId": "radar-replay-objective-outcome-manifest-v1",
        "collectionId": bundle.collection_id,
        "taskBundleId": task_bundle.task_bundle_id,
        "outputBundleId": output_bundle.bundle_id,
        "evaluatedAt": evaluated_at,
        "status": bundle.status,
        "files": files,
    })
    return ObjectiveOutcomeCollectionResult(
        output_dir=resolved_output,
        outcome_bundle_path=outcome_path,
        label_bundle_path=label_path,
        manifest_path=manifest_path,
        bundle=bundle,
        label_bundle=label_bundle,
    )


def _load_manifest_bound_model(path: Path, *, manifest_key: str, model_type):
    resolved = _private_tmp_child(Path(path))
    manifest_path = resolved.parent / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = manifest["files"][manifest_key]
        manifest_file = _safe_file(resolved.parent, entry["path"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise ValueError("objective_outcome_input_manifest_unverified") from None
    if manifest_file != resolved or _sha256(resolved) != entry.get("sha256"):
        raise ValueError("objective_outcome_input_hash_mismatch")
    try:
        return model_type.model_validate_json(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("objective_outcome_input_unverified") from exc


def collect_replay_objective_outcomes_from_files(
    *,
    task_bundle_path: Path,
    output_bundle_path: Path,
    output_dir: Path,
    evaluated_at: datetime,
    providers: Mapping[str, OutcomeProvider],
    day_kind_provider: Callable[[date], str] = _official_cn_day_kind,
    daily_snapshot_ids: Sequence[str] = (),
) -> ObjectiveOutcomeCollectionResult:
    task_bundle, output_bundle = load_replay_objective_inputs(
        task_bundle_path=task_bundle_path,
        output_bundle_path=output_bundle_path,
    )
    return collect_replay_objective_outcomes(
        task_bundle=task_bundle,
        output_bundle=output_bundle,
        output_dir=output_dir,
        evaluated_at=evaluated_at,
        providers=providers,
        day_kind_provider=day_kind_provider,
        daily_snapshot_ids=daily_snapshot_ids,
    )


def load_replay_objective_inputs(
    *,
    task_bundle_path: Path,
    output_bundle_path: Path,
) -> Tuple[RadarReplayLabelTaskBundle, RadarReplayOutputBundle]:
    task_bundle = _load_manifest_bound_model(
        task_bundle_path,
        manifest_key="labelTasks",
        model_type=RadarReplayLabelTaskBundle,
    )
    output_bundle = _load_manifest_bound_model(
        output_bundle_path,
        manifest_key="outputBundle",
        model_type=RadarReplayOutputBundle,
    )
    return task_bundle, output_bundle
