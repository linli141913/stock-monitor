"""阶段9跨交易日客观结果活动清单。

清单只引用 ``/private/tmp`` 中内容哈希校验的不可变工件，
不访问 SQLite，也不将过期或缺失日终事实补写为已完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Callable, List, Literal, Optional, Sequence

from pydantic import Field, model_validator

from market_calendar import get_calendar_day_kind
from radar.replay_assembly import (
    ASSEMBLY_MANIFEST_ID,
    _private_tmp_child,
    _safe_file,
    _sha256,
    build_replay_assembly,
)
from radar.replay_assembly import assemble_replay_artifacts
from radar.replay_contracts import (
    RadarReplayInput,
    RadarReplayLabelBundle,
    RadarReplayModel,
    RadarReplaySampleRole,
)
from radar.replay_forward_baseline import _atomic_write_json
from radar.replay_objective_outcomes import (
    ObjectiveOutcomeObservation,
    ObjectiveOutcomeBundle,
    ObjectiveOutcomeRequest,
    _output_targets,
    collect_replay_objective_outcomes_from_files,
    load_replay_objective_inputs,
)
from radar.replay_outcome_daily_capture import (
    SAFE_CLOSE_TIME,
    SHANGHAI_TZ,
    _capture_requirements,
    capture_replay_outcome_day_from_files,
)
from radar.replay_outcome_daily_provider import (
    build_daily_snapshot_outcome_providers,
    load_daily_outcome_snapshots,
)
from radar.run_lock import CrossProcessFileLock
from radar.replay_service import (
    RadarReplayQualityReport,
    build_replay_quality_report,
)


CampaignMode = Literal["formal_sequence", "standalone_diagnostic"]
FORMAL_ROLES: tuple[RadarReplaySampleRole, ...] = (
    "development",
    "calibration",
    "holdout",
)
CAMPAIGN_MUTATION_LOCK_NAME = ".campaign-mutation.lock"
_FORMAL_REGISTRATION_TOKEN = object()


class CampaignArtifactRef(RadarReplayModel):
    path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_id: str = Field(alias="semanticId", min_length=1, max_length=240)


class CampaignSampleIdentity(RadarReplayModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=160)
    radar_run_id: str = Field(alias="radarRunId", min_length=1, max_length=240)
    role: RadarReplaySampleRole
    as_of: datetime = Field(alias="asOf")

    @model_validator(mode="after")
    def validate_time(self):
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("replay_campaign_asOf_timezone_required")
        return self


class CampaignDailySnapshot(RadarReplayModel):
    trade_date: date = Field(alias="tradeDate")
    snapshot_id: str = Field(alias="snapshotId", min_length=1, max_length=240)
    captured_at: datetime = Field(alias="capturedAt")
    status: Literal["ready", "partial", "failed"]
    artifact: CampaignArtifactRef

    @model_validator(mode="after")
    def validate_time(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("replay_campaign_capturedAt_timezone_required")
        return self


class CampaignOutcome(RadarReplayModel):
    status: Literal["pending_maturity", "ready", "partial", "failed"]
    collection_id: str = Field(alias="collectionId", min_length=1, max_length=240)
    daily_snapshot_ids: List[str] = Field(alias="dailySnapshotIds", min_length=1)
    outcome_artifact: CampaignArtifactRef = Field(alias="outcomeArtifact")
    label_artifact: Optional[CampaignArtifactRef] = Field(
        default=None,
        alias="labelArtifact",
    )


class CampaignAssembly(RadarReplayModel):
    input_digest: str = Field(
        alias="inputDigest",
        pattern=r"^[0-9a-f]{64}$",
    )
    replay_run_id: str = Field(alias="replayRunId", min_length=1, max_length=240)
    quality_status: Literal["ready", "not_ready"] = Field(alias="qualityStatus")
    replay_artifact: CampaignArtifactRef = Field(alias="replayArtifact")
    quality_artifact: CampaignArtifactRef = Field(alias="qualityArtifact")


class CampaignCohort(RadarReplayModel):
    cohort_id: str = Field(alias="cohortId", min_length=1, max_length=240)
    sample: CampaignSampleIdentity
    task_bundle: CampaignArtifactRef = Field(alias="taskBundle")
    output_bundle: CampaignArtifactRef = Field(alias="outputBundle")
    daily_snapshots: List[CampaignDailySnapshot] = Field(
        default_factory=list,
        alias="dailySnapshots",
    )
    outcome: Optional[CampaignOutcome] = None

    @model_validator(mode="after")
    def validate_uniqueness(self):
        dates = [item.trade_date for item in self.daily_snapshots]
        ids = [item.snapshot_id for item in self.daily_snapshots]
        if len(dates) != len(set(dates)):
            raise ValueError("duplicate_replay_campaign_trade_date")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_replay_campaign_snapshot_id")
        return self


class ReplayCampaignState(RadarReplayModel):
    contract_id: Literal["radar-replay-campaign-v1"] = Field(
        default="radar-replay-campaign-v1",
        alias="contractId",
    )
    campaign_id: str = Field(alias="campaignId", min_length=1, max_length=240)
    mode: CampaignMode
    revision: int = Field(ge=1)
    updated_at: datetime = Field(alias="updatedAt")
    cohorts: List[CampaignCohort] = Field(default_factory=list)
    assembly: Optional[CampaignAssembly] = None

    @model_validator(mode="after")
    def validate_state(self):
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("replay_campaign_updatedAt_timezone_required")
        cohort_ids = [item.cohort_id for item in self.cohorts]
        identities = [
            (item.sample.sample_id, item.sample.radar_run_id, item.sample.as_of)
            for item in self.cohorts
        ]
        if len(cohort_ids) != len(set(cohort_ids)):
            raise ValueError("duplicate_replay_campaign_cohort")
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate_replay_campaign_sample")
        if any(
            item.sample.as_of > self.updated_at for item in self.cohorts
        ):
            raise ValueError("replay_campaign_updatedAt_before_sample")
        if any(
            snapshot.captured_at > self.updated_at
            for item in self.cohorts
            for snapshot in item.daily_snapshots
        ):
            raise ValueError("replay_campaign_updatedAt_before_snapshot")
        if self.mode == "standalone_diagnostic":
            if self.assembly is not None:
                raise ValueError("diagnostic_campaign_assembly_forbidden")
            if len(self.cohorts) > 1:
                raise ValueError("diagnostic_campaign_single_cohort_required")
            return self
        if len(self.cohorts) > len(FORMAL_ROLES):
            raise ValueError("replay_campaign_role_sequence_invalid")
        roles = [item.sample.role for item in self.cohorts]
        if roles != list(FORMAL_ROLES[:len(roles)]):
            raise ValueError("replay_campaign_role_sequence_invalid")
        local_dates = [
            item.sample.as_of.astimezone(SHANGHAI_TZ).date()
            for item in self.cohorts
        ]
        if any(left >= right for left, right in zip(local_dates, local_dates[1:])):
            raise ValueError("replay_campaign_partition_chronology_invalid")
        if self.assembly is not None and len(self.cohorts) != 3:
            raise ValueError("replay_campaign_assembly_partitions_incomplete")
        return self


@dataclass(frozen=True)
class ReplayCampaignResumeReport:
    status: str
    actions: tuple[str, ...]
    state: ReplayCampaignState


def _official_day_kind(day: date) -> str:
    return get_calendar_day_kind("cn", day).kind


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_timezone_required")
    return value


def _reject_campaign_time_regression(
    state: ReplayCampaignState,
    value: datetime,
) -> None:
    if value < state.updated_at:
        raise ValueError("replay_campaign_time_regression")


def _artifact_ref(path: Path, semantic_id: str) -> CampaignArtifactRef:
    resolved = _private_tmp_child(Path(path))
    if not resolved.is_file():
        raise ValueError("replay_campaign_artifact_missing")
    return CampaignArtifactRef(
        path=str(resolved),
        sha256=_sha256(resolved),
        semanticId=semantic_id,
    )


def _verify_ref(value: CampaignArtifactRef) -> Path:
    path = _private_tmp_child(Path(value.path))
    if not path.is_file():
        raise ValueError("replay_campaign_artifact_missing")
    if _sha256(path) != value.sha256:
        raise ValueError("replay_campaign_artifact_hash_mismatch")
    return path


def _write_state(campaign_dir: Path, state: ReplayCampaignState) -> None:
    root = _private_tmp_child(campaign_dir)
    states_dir = root / "states"
    states_dir.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            state.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    state_path = states_dir / f"{digest}.json"
    if state_path.exists():
        if state_path.read_bytes() != encoded:
            raise ValueError("replay_campaign_state_collision")
    else:
        temporary = state_path.with_suffix(".json.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(state_path)
    _atomic_write_json(root / "latest.json", {
        "contractId": "radar-replay-campaign-latest-v1",
        "campaignId": state.campaign_id,
        "revision": state.revision,
        "updatedAt": state.updated_at,
        "statePath": str(state_path.relative_to(root)),
        "sha256": digest,
    })


def create_campaign(
    *,
    campaign_dir: Path,
    campaign_id: str,
    mode: CampaignMode,
    updated_at: datetime,
) -> ReplayCampaignState:
    root = _private_tmp_child(Path(campaign_dir))
    if root.exists():
        raise ValueError("replay_campaign_dir_must_be_new")
    _aware(updated_at, "updatedAt")
    state = ReplayCampaignState(
        campaignId=campaign_id,
        mode=mode,
        revision=1,
        updatedAt=updated_at,
        cohorts=[],
    )
    root.mkdir(parents=True)
    _write_state(root, state)
    return state


def load_campaign(campaign_dir: Path) -> ReplayCampaignState:
    root = _private_tmp_child(Path(campaign_dir))
    latest_path = root / "latest.json"
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        if latest.get("contractId") != "radar-replay-campaign-latest-v1":
            raise ValueError
        latest_updated_at = datetime.fromisoformat(latest["updatedAt"])
        _aware(latest_updated_at, "latestUpdatedAt")
        state_path = (root / latest["statePath"]).resolve()
        state_path.relative_to(root / "states")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError):
        raise ValueError("replay_campaign_latest_unverified") from None
    if not state_path.is_file():
        raise ValueError("replay_campaign_state_missing")
    if _sha256(state_path) != latest.get("sha256"):
        raise ValueError("replay_campaign_state_hash_mismatch")
    try:
        state = ReplayCampaignState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ValueError("replay_campaign_state_unverified") from exc
    if (
        state.campaign_id != latest.get("campaignId")
        or state.revision != latest.get("revision")
        or state.updated_at != latest_updated_at
    ):
        raise ValueError("replay_campaign_state_identity_mismatch")
    return state


def _snapshot_ref(
    path: Path,
    *,
    task_id: str,
    output_id: str,
    task_bundle=None,
    output_bundle=None,
):
    snapshots = load_daily_outcome_snapshots([path])
    snapshot = snapshots[0]
    if (
        snapshot.task_bundle_id != task_id
        or snapshot.output_bundle_id != output_id
    ):
        raise ValueError("replay_campaign_daily_identity_mismatch")
    if task_bundle is not None and output_bundle is not None:
        expected_symbols, expected_memberships = _capture_requirements(
            task_bundle,
            output_bundle,
        )
        observed_symbols = {
            item.symbol for item in snapshot.security_quotes
        }
        missing_symbols = snapshot.missing_symbols
        if (
            snapshot.sector_memberships_by_sample != expected_memberships
            or snapshot.expected_security_count != len(expected_symbols)
            or missing_symbols != sorted(set(missing_symbols))
            or observed_symbols.intersection(missing_symbols)
            or observed_symbols.union(missing_symbols)
            != set(expected_symbols)
        ):
            raise ValueError("daily_outcome_scope_mismatch")
    return CampaignDailySnapshot(
        tradeDate=snapshot.trade_date,
        snapshotId=snapshot.snapshot_id,
        capturedAt=snapshot.captured_at,
        status=snapshot.status,
        artifact=_artifact_ref(path, snapshot.snapshot_id),
    )


def _acquire_campaign_mutation_lock(campaign_dir: Path):
    root = _private_tmp_child(Path(campaign_dir))
    lock = CrossProcessFileLock(root / CAMPAIGN_MUTATION_LOCK_NAME)
    if not lock.acquire(blocking=False):
        raise ValueError("replay_campaign_mutation_locked")
    return root, lock


def register_cohort(
    *,
    campaign_dir: Path,
    task_bundle_path: Path,
    output_bundle_path: Path,
    updated_at: datetime,
    daily_snapshot_paths: Sequence[Path] = (),
    expected_revision: Optional[int] = None,
    _formal_entry_token: object = None,
) -> ReplayCampaignState:
    _, lock = _acquire_campaign_mutation_lock(campaign_dir)
    try:
        return _register_cohort_unlocked(
            campaign_dir=campaign_dir,
            task_bundle_path=task_bundle_path,
            output_bundle_path=output_bundle_path,
            updated_at=updated_at,
            daily_snapshot_paths=daily_snapshot_paths,
            expected_revision=expected_revision,
            _formal_entry_token=_formal_entry_token,
        )
    finally:
        lock.release()


def _register_cohort_unlocked(
    *,
    campaign_dir: Path,
    task_bundle_path: Path,
    output_bundle_path: Path,
    updated_at: datetime,
    daily_snapshot_paths: Sequence[Path] = (),
    expected_revision: Optional[int] = None,
    _formal_entry_token: object = None,
) -> ReplayCampaignState:
    _aware(updated_at, "updatedAt")
    state = load_campaign(campaign_dir)
    _reject_campaign_time_regression(state, updated_at)
    if (
        state.mode == "formal_sequence"
        and _formal_entry_token is not _FORMAL_REGISTRATION_TOKEN
    ):
        raise ValueError(
            "replay_campaign_formal_registration_entrypoint_required"
        )
    if expected_revision is not None and state.revision != expected_revision:
        raise ValueError("replay_campaign_revision_conflict")
    task_bundle, output_bundle = load_replay_objective_inputs(
        task_bundle_path=task_bundle_path,
        output_bundle_path=output_bundle_path,
    )
    if len(task_bundle.samples) != 1 or len(output_bundle.samples) != 1:
        raise ValueError("replay_campaign_single_sample_bundle_required")
    task = task_bundle.samples[0]
    output = output_bundle.samples[0]
    identity = (task.sample_id, task.radar_run_id, task.as_of)
    if identity != (output.sample_id, output.radar_run_id, output.as_of):
        raise ValueError("replay_campaign_sample_identity_mismatch")
    if updated_at < max(
        task_bundle.created_at,
        output_bundle.created_at,
        task.as_of,
        output.as_of,
    ):
        raise ValueError("replay_campaign_registration_time_before_artifact")
    task_ref = _artifact_ref(task_bundle_path, task_bundle.task_bundle_id)
    output_ref = _artifact_ref(output_bundle_path, output_bundle.bundle_id)
    cohort_digest = hashlib.sha256(
        f"{task_ref.sha256}|{output_ref.sha256}".encode("utf-8")
    ).hexdigest()
    cohort_id = f"stage9-cohort-{cohort_digest[:24]}"
    existing = next(
        (item for item in state.cohorts if item.cohort_id == cohort_id),
        None,
    )
    if existing is None and any(
        (item.sample.sample_id, item.sample.radar_run_id, item.sample.as_of)
        == identity
        for item in state.cohorts
    ):
        raise ValueError("replay_campaign_sample_artifact_conflict")
    snapshots = [] if existing is None else list(existing.daily_snapshots)
    known_dates = {item.trade_date for item in snapshots}
    for path in daily_snapshot_paths:
        item = _snapshot_ref(
            Path(path),
            task_id=task_bundle.task_bundle_id,
            output_id=output_bundle.bundle_id,
            task_bundle=task_bundle,
            output_bundle=output_bundle,
        )
        if item.trade_date in known_dates:
            previous = next(
                value for value in snapshots if value.trade_date == item.trade_date
            )
            if previous != item:
                raise ValueError("replay_campaign_daily_artifact_conflict")
            continue
        if item.trade_date < task.as_of.astimezone(SHANGHAI_TZ).date():
            raise ValueError("replay_campaign_daily_before_sample")
        snapshots.append(item)
        known_dates.add(item.trade_date)
    snapshots.sort(key=lambda item: item.trade_date)
    if existing is None:
        cohort = CampaignCohort(
            cohortId=cohort_id,
            sample={
                "sampleId": task.sample_id,
                "radarRunId": task.radar_run_id,
                "role": task.role,
                "asOf": task.as_of,
            },
            taskBundle=task_ref,
            outputBundle=output_ref,
            dailySnapshots=snapshots,
        )
        cohorts = [*state.cohorts, cohort]
    elif snapshots != existing.daily_snapshots:
        cohorts = [
            item.model_copy(update={"daily_snapshots": snapshots})
            if item.cohort_id == cohort_id else item
            for item in state.cohorts
        ]
    else:
        return state
    next_state = ReplayCampaignState(
        campaignId=state.campaign_id,
        mode=state.mode,
        revision=state.revision + 1,
        updatedAt=updated_at,
        cohorts=cohorts,
    )
    _write_state(Path(campaign_dir), next_state)
    return next_state


def _required_dates(
    sample_date: date,
    through_date: date,
    day_kind_provider: Callable[[date], str],
) -> tuple[date, ...]:
    sample_kind = day_kind_provider(sample_date)
    if sample_kind == "unknown":
        raise ValueError("replay_campaign_calendar_unverifiable")
    if sample_kind not in {"full", "half"}:
        raise ValueError("replay_campaign_sample_not_trading_day")
    dates = [sample_date]
    current = sample_date + timedelta(days=1)
    while len(dates) < 6 and current <= through_date:
        kind = day_kind_provider(current)
        if kind == "unknown":
            raise ValueError("replay_campaign_calendar_unverifiable")
        if kind in {"full", "half"}:
            dates.append(current)
        elif kind != "closed":
            raise ValueError("replay_campaign_calendar_kind_invalid")
        current += timedelta(days=1)
    return tuple(dates)


def _artifact_output_dir(root: Path, cohort_id: str, kind: str, revision: int):
    base = root / "artifacts" / cohort_id
    base.mkdir(parents=True, exist_ok=True)
    candidate = base / f"{kind}-r{revision}"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = base / f"{kind}-r{revision}-{suffix}"
    return candidate


def _verify_cohort_artifacts(cohort: CampaignCohort):
    task_path = _verify_ref(cohort.task_bundle)
    output_path = _verify_ref(cohort.output_bundle)
    task, output = load_replay_objective_inputs(
        task_bundle_path=task_path,
        output_bundle_path=output_path,
    )
    if (
        task.task_bundle_id != cohort.task_bundle.semantic_id
        or output.bundle_id != cohort.output_bundle.semantic_id
    ):
        raise ValueError("replay_campaign_bundle_identity_mismatch")
    snapshot_paths = []
    for item in cohort.daily_snapshots:
        path = _verify_ref(item.artifact)
        loaded = _snapshot_ref(
            path,
            task_id=task.task_bundle_id,
            output_id=output.bundle_id,
            task_bundle=task,
            output_bundle=output,
        )
        if loaded != item:
            raise ValueError("replay_campaign_daily_identity_mismatch")
        snapshot_paths.append(path)
    if cohort.outcome is not None:
        _verify_outcome_artifacts(
            cohort=cohort,
            task_bundle=task,
            output_bundle=output,
        )
    return task_path, output_path, snapshot_paths


def _verify_manifest_file(
    *,
    root: Path,
    manifest: dict,
    key: str,
    expected_path: Path,
) -> None:
    files = manifest.get("files")
    entry = files.get(key) if isinstance(files, dict) else None
    if not isinstance(entry, dict):
        raise ValueError("artifact_manifest_unverified")
    manifest_path = _safe_file(root, entry.get("path"))
    if (
        manifest_path != expected_path
        or entry.get("sha256") != _sha256(expected_path)
    ):
        raise ValueError("artifact_hash_mismatch")


def _verify_outcome_artifacts(
    *,
    cohort: CampaignCohort,
    task_bundle,
    output_bundle,
) -> None:
    outcome_state = cohort.outcome
    if outcome_state is None:
        return
    try:
        outcome_path = _verify_ref(outcome_state.outcome_artifact)
        root = outcome_path.parent
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outcome = ObjectiveOutcomeBundle.model_validate_json(
            outcome_path.read_text(encoding="utf-8")
        )
        _verify_manifest_file(
            root=root,
            manifest=manifest,
            key="objectiveOutcomes",
            expected_path=outcome_path,
        )
        task = task_bundle.samples[0]
        sample = outcome.samples[0] if len(outcome.samples) == 1 else None
        if (
            manifest.get("contractId")
            != "radar-replay-objective-outcome-manifest-v1"
            or manifest.get("collectionId") != outcome.collection_id
            or manifest.get("taskBundleId") != outcome.task_bundle_id
            or manifest.get("outputBundleId") != outcome.output_bundle_id
            or manifest.get("evaluatedAt") != outcome.evaluated_at.isoformat()
            or manifest.get("status") != outcome.status
            or outcome.collection_id != outcome_state.collection_id
            or outcome.collection_id
            != outcome_state.outcome_artifact.semantic_id
            or outcome.task_bundle_id != task_bundle.task_bundle_id
            or outcome.output_bundle_id != output_bundle.bundle_id
            or outcome.status != outcome_state.status
            or outcome.daily_snapshot_ids
            != outcome_state.daily_snapshot_ids
            or sample is None
            or (
                sample.sample_id,
                sample.radar_run_id,
                sample.role,
                sample.as_of,
            ) != (
                cohort.sample.sample_id,
                cohort.sample.radar_run_id,
                cohort.sample.role,
                cohort.sample.as_of,
            )
        ):
            raise ValueError("outcome_identity_mismatch")

        ordered_daily = sorted(
            cohort.daily_snapshots,
            key=lambda item: item.trade_date,
        )
        if len(ordered_daily) != 6 or ordered_daily != cohort.daily_snapshots:
            raise ValueError("outcome_daily_sequence_mismatch")
        expected_daily_ids = [item.snapshot_id for item in ordered_daily]
        expected_evaluation_dates = [
            item.trade_date for item in ordered_daily[1:]
        ]
        if (
            outcome.daily_snapshot_ids != expected_daily_ids
            or sample.evaluation_trade_dates != expected_evaluation_dates
            or sample.maturity_date != expected_evaluation_dates[-1]
        ):
            raise ValueError("outcome_daily_sequence_mismatch")

        snapshot_paths = [
            _verify_ref(item.artifact) for item in ordered_daily
        ]
        providers = build_daily_snapshot_outcome_providers(snapshot_paths)
        output_set = output_bundle.samples[0]
        expected_observations = {}
        expected_sample_reasons = []
        for domain in ("market", "sector", "etf", "leader"):
            targets, output_source_ids, output_reason = _output_targets(
                output_set,
                domain,
            )
            if output_reason is not None:
                expected_sample_reasons.append(
                    f"{output_reason}:{domain}"
                )
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
                    maturity_date=expected_evaluation_dates[-1],
                    evaluation_trade_dates=tuple(
                        expected_evaluation_dates
                    ),
                    output_source_ids=output_source_ids,
                )
                expected_observations[(domain, target_id)] = providers[
                    domain
                ](request)
        actual_observations = {
            (item.domain, item.target_id): ObjectiveOutcomeObservation(
                status=item.status,
                observedThrough=item.observed_through,
                sourceIds=item.source_ids,
                outcomeMetrics=item.outcome_metrics,
                reasons=item.reasons,
            )
            for item in sample.observations
        }
        if (
            len(actual_observations) != len(sample.observations)
            or actual_observations != expected_observations
            or sample.reasons != expected_sample_reasons
        ):
            raise ValueError("outcome_daily_recomputation_mismatch")
        observation_statuses = {
            item.status for item in expected_observations.values()
        }
        if "failed" in observation_statuses:
            expected_sample_status = "failed"
        elif "unverifiable" in observation_statuses:
            expected_sample_status = "unverifiable"
        elif "missing" in observation_statuses or expected_sample_reasons:
            expected_sample_status = "partial"
        else:
            expected_sample_status = "ready"
        expected_bundle_status = (
            "failed"
            if expected_sample_status == "failed"
            else "ready"
            if expected_sample_status == "ready"
            else "partial"
        )
        if (
            sample.status != expected_sample_status
            or outcome.status != expected_bundle_status
        ):
            raise ValueError("outcome_recomputed_status_mismatch")

        ready_observations = {
            (item.domain, item.target_id): item
            for item in sample.observations
            if item.status == "ready"
        }
        label_ref = outcome_state.label_artifact
        if label_ref is None:
            if ready_observations or outcome.status == "ready":
                raise ValueError("outcome_label_missing")
            return
        label_path = _verify_ref(label_ref)
        if label_path.parent != root:
            raise ValueError("outcome_label_directory_mismatch")
        _verify_manifest_file(
            root=root,
            manifest=manifest,
            key="labelBundle",
            expected_path=label_path,
        )
        labels = RadarReplayLabelBundle.model_validate_json(
            label_path.read_text(encoding="utf-8")
        )
        if (
            labels.bundle_id != label_ref.semantic_id
            or labels.created_at != outcome.evaluated_at
            or len(labels.samples) != 1
            or (
                labels.samples[0].sample_id,
                labels.samples[0].radar_run_id,
                labels.samples[0].as_of,
            ) != (
                cohort.sample.sample_id,
                cohort.sample.radar_run_id,
                cohort.sample.as_of,
            )
        ):
            raise ValueError("outcome_label_identity_mismatch")
        label_by_target = {
            (item.domain, item.target_id): item
            for item in labels.samples[0].labels
        }
        if set(label_by_target) != set(ready_observations):
            raise ValueError("outcome_label_coverage_mismatch")
        for target, label in label_by_target.items():
            observation = ready_observations[target]
            if (
                label.comparison_mode != "objective_outcome"
                or label.labeled_by != "automatic-objective-outcome-v1"
                or label.labeled_at != outcome.evaluated_at
                or label.review_status != "verified"
                or label.independent_from_rule is not True
                or label.source_ids != observation.source_ids
                or label.outcome_metrics != observation.outcome_metrics
            ):
                raise ValueError("outcome_label_content_mismatch")
    except Exception as exc:
        if (
            isinstance(exc, ValueError)
            and str(exc) == "replay_campaign_outcome_artifact_unverified"
        ):
            raise
        raise ValueError(
            "replay_campaign_outcome_artifact_unverified"
        ) from exc


def _verify_assembly_artifacts(
    assembly: CampaignAssembly,
    cohorts: Optional[Sequence[CampaignCohort]] = None,
) -> None:
    try:
        replay_path = _verify_ref(assembly.replay_artifact)
        quality_path = _verify_ref(assembly.quality_artifact)
        if replay_path.parent != quality_path.parent:
            raise ValueError("assembly_directory_mismatch")
        root = replay_path.parent
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        _verify_manifest_file(
            root=root,
            manifest=manifest,
            key="replayInput",
            expected_path=replay_path,
        )
        _verify_manifest_file(
            root=root,
            manifest=manifest,
            key="qualityReport",
            expected_path=quality_path,
        )
        replay = RadarReplayInput.model_validate_json(
            replay_path.read_text(encoding="utf-8")
        )
        quality = RadarReplayQualityReport.model_validate_json(
            quality_path.read_text(encoding="utf-8")
        )
        recomputed_quality = build_replay_quality_report(replay)
        rebuilt = None
        expected_input_digest = None
        if cohorts is not None:
            assembly_inputs = _assembly_inputs(cohorts)
            if assembly_inputs is None:
                raise ValueError("assembly_cohorts_not_ready")
            (
                input_dirs,
                output_paths,
                label_paths,
                expected_input_digest,
            ) = assembly_inputs
            rebuilt = build_replay_assembly(
                input_dirs=input_dirs,
                label_bundle_paths=label_paths,
                output_bundle_paths=output_paths,
                clock=lambda: replay.created_at,
            )
        if (
            manifest.get("contractId") != ASSEMBLY_MANIFEST_ID
            or manifest.get("replayRunId") != replay.replay_run_id
            or manifest.get("createdAt") != replay.created_at.isoformat()
            or manifest.get("status") != quality.status
            or replay.replay_run_id != assembly.replay_run_id
            or replay.replay_run_id != assembly.replay_artifact.semantic_id
            or quality.replay_run_id != replay.replay_run_id
            or quality.created_at != replay.created_at
            or quality.status != assembly.quality_status
            or quality.model_dump(mode="json", by_alias=True)
            != recomputed_quality.model_dump(mode="json", by_alias=True)
            or assembly.quality_artifact.semantic_id
            != f"{replay.replay_run_id}:quality"
            or (
                rebuilt is not None
                and (
                    assembly.input_digest != expected_input_digest
                    or replay.model_dump(mode="json", by_alias=True)
                    != rebuilt.replay.model_dump(mode="json", by_alias=True)
                    or quality.model_dump(mode="json", by_alias=True)
                    != rebuilt.report.model_dump(mode="json", by_alias=True)
                    or manifest.get("inputArtifacts")
                    != list(rebuilt.input_records)
                    or manifest.get("labelBundles")
                    != list(rebuilt.label_records)
                    or manifest.get("outputBundles")
                    != list(rebuilt.output_records)
                )
            )
        ):
            raise ValueError("assembly_identity_mismatch")
    except Exception as exc:
        if (
            isinstance(exc, ValueError)
            and str(exc) == "replay_campaign_assembly_artifact_unverified"
        ):
            raise
        raise ValueError(
            "replay_campaign_assembly_artifact_unverified"
        ) from exc


def _assembly_inputs(cohorts: Sequence[CampaignCohort]):
    input_dirs = []
    output_paths = []
    label_paths = []
    identity_parts = []
    for cohort in cohorts:
        if (
            cohort.outcome is None
            or cohort.outcome.status != "ready"
            or cohort.outcome.label_artifact is None
        ):
            return None
        task_path = _verify_ref(cohort.task_bundle)
        output_path = _verify_ref(cohort.output_bundle)
        label_path = _verify_ref(cohort.outcome.label_artifact)
        task_bundle, _ = load_replay_objective_inputs(
            task_bundle_path=task_path,
            output_bundle_path=output_path,
        )
        replay = task_bundle.samples[0].replay_input
        replay_path = _private_tmp_child(Path(replay.path))
        if not replay_path.is_file() or _sha256(replay_path) != replay.sha256:
            raise ValueError("replay_campaign_forward_input_hash_mismatch")
        input_dirs.append(replay_path.parent)
        output_paths.append(output_path)
        label_paths.append(label_path)
        identity_parts.append(
            f"{cohort.cohort_id}|{cohort.outcome.collection_id}|"
            f"{cohort.outcome.outcome_artifact.sha256}|"
            f"{cohort.outcome.label_artifact.sha256}"
        )
    digest = hashlib.sha256(
        "|".join(identity_parts).encode("utf-8")
    ).hexdigest()
    return input_dirs, output_paths, label_paths, digest


def _resume_campaign_unlocked(
    *,
    campaign_dir: Path,
    now: datetime,
    confirm_live_close_capture: bool = False,
    day_kind_provider: Callable[[date], str] = _official_day_kind,
    capture: Callable = capture_replay_outcome_day_from_files,
    collect: Callable = collect_replay_objective_outcomes_from_files,
    assemble: Callable = assemble_replay_artifacts,
) -> ReplayCampaignResumeReport:
    _aware(now, "now")
    root = _private_tmp_child(Path(campaign_dir))
    state = load_campaign(root)
    _reject_campaign_time_regression(state, now)
    local_now = now.astimezone(SHANGHAI_TZ)
    actions: List[str] = []
    cohorts = []
    assembly = state.assembly
    changed = False
    if assembly is not None:
        _verify_assembly_artifacts(assembly, state.cohorts)
    for cohort in state.cohorts:
        task_path, output_path, snapshot_paths = _verify_cohort_artifacts(cohort)
        required_dates = _required_dates(
            cohort.sample.as_of.astimezone(SHANGHAI_TZ).date(),
            local_now.date(),
            day_kind_provider,
        )
        by_date = {item.trade_date: item for item in cohort.daily_snapshots}
        unexpected = set(by_date) - set(required_dates)
        if unexpected:
            raise ValueError("replay_campaign_unexpected_daily_snapshot")
        if local_now.date() in by_date:
            actions.append("daily_snapshot_verified")
        elif (
            local_now.date() in required_dates
            and local_now.time().replace(tzinfo=None) >= SAFE_CLOSE_TIME
        ):
            if not confirm_live_close_capture:
                actions.append("daily_capture_confirmation_required")
            else:
                result = capture(
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    output_dir=_artifact_output_dir(
                        root,
                        cohort.cohort_id,
                        f"daily-{local_now.date().isoformat()}",
                        state.revision + 1,
                    ),
                    trade_date=local_now.date(),
                    captured_at=now,
                    day_kind_provider=day_kind_provider,
                )
                item = _snapshot_ref(
                    result.snapshot_path,
                    task_id=cohort.task_bundle.semantic_id,
                    output_id=cohort.output_bundle.semantic_id,
                )
                by_date[item.trade_date] = item
                snapshot_paths.append(Path(item.artifact.path))
                actions.append("daily_snapshot_captured")
                changed = True
        missing_past = [
            day for day in required_dates
            if day < local_now.date() and day not in by_date
        ]
        if missing_past:
            actions.append("historical_daily_snapshot_missing")
        ordered_daily = [by_date[day] for day in sorted(by_date)]
        ordered_paths = [Path(item.artifact.path) for item in ordered_daily]
        outcome = cohort.outcome
        if len(required_dates) == 6 and all(
            day in by_date for day in required_dates
        ):
            complete_ids = [by_date[day].snapshot_id for day in required_dates]
            if (
                outcome is not None
                and outcome.daily_snapshot_ids == complete_ids
            ):
                actions.append("objective_outcomes_verified")
            elif (
                local_now.date() > required_dates[-1]
                or (
                    local_now.date() == required_dates[-1]
                    and local_now.time().replace(tzinfo=None) >= SAFE_CLOSE_TIME
                )
            ):
                providers = build_daily_snapshot_outcome_providers(ordered_paths)
                result = collect(
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    output_dir=_artifact_output_dir(
                        root,
                        cohort.cohort_id,
                        "objective-outcomes",
                        state.revision + 1,
                    ),
                    evaluated_at=now,
                    providers=providers,
                    day_kind_provider=day_kind_provider,
                    daily_snapshot_ids=complete_ids,
                )
                label_ref = (
                    _artifact_ref(
                        result.label_bundle_path,
                        getattr(
                            getattr(result, "label_bundle", None),
                            "bundle_id",
                            "objective-labels",
                        ),
                    )
                    if getattr(result, "label_bundle_path", None) is not None
                    else None
                )
                outcome = CampaignOutcome(
                    status=result.bundle.status,
                    collectionId=result.bundle.collection_id,
                    dailySnapshotIds=complete_ids,
                    outcomeArtifact=_artifact_ref(
                        result.outcome_bundle_path,
                        result.bundle.collection_id,
                    ),
                    labelArtifact=label_ref,
                )
                verified_task, verified_output = load_replay_objective_inputs(
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                )
                _verify_outcome_artifacts(
                    cohort=cohort.model_copy(update={
                        "daily_snapshots": ordered_daily,
                        "outcome": outcome,
                    }),
                    task_bundle=verified_task,
                    output_bundle=verified_output,
                )
                actions.append("objective_outcomes_collected")
                changed = True
        cohorts.append(cohort.model_copy(update={
            "daily_snapshots": ordered_daily,
            "outcome": outcome,
        }))
    if state.mode == "formal_sequence" and len(cohorts) == 3:
        assembly_inputs = _assembly_inputs(cohorts)
        if assembly_inputs is not None:
            input_dirs, output_paths, label_paths, input_digest = assembly_inputs
            if assembly is not None and assembly.input_digest == input_digest:
                actions.append("formal_quality_verified")
            else:
                result = assemble(
                    input_dirs=input_dirs,
                    label_bundle_paths=label_paths,
                    output_bundle_paths=output_paths,
                    output_dir=_artifact_output_dir(
                        root,
                        "formal-quality",
                        "assembly",
                        state.revision + 1,
                    ),
                    clock=lambda: now,
                )
                assembly = CampaignAssembly(
                    inputDigest=input_digest,
                    replayRunId=result.replay.replay_run_id,
                    qualityStatus=result.report.status,
                    replayArtifact=_artifact_ref(
                        result.replay_input_path,
                        result.replay.replay_run_id,
                    ),
                    qualityArtifact=_artifact_ref(
                        result.quality_report_path,
                        f"{result.replay.replay_run_id}:quality",
                    ),
                )
                _verify_assembly_artifacts(assembly, cohorts)
                actions.append("formal_quality_assembled")
                changed = True
    if changed:
        revision_updated_at = max(
            (
                snapshot.captured_at
                for cohort in cohorts
                for snapshot in cohort.daily_snapshots
            ),
            default=now,
        )
        revision_updated_at = max(now, revision_updated_at)
        state = ReplayCampaignState(
            campaignId=state.campaign_id,
            mode=state.mode,
            revision=state.revision + 1,
            updatedAt=revision_updated_at,
            cohorts=cohorts,
            assembly=assembly,
        )
        _write_state(root, state)
    history_incomplete = (
        "historical_daily_snapshot_missing" in actions
        or any(
            snapshot.status != "ready"
            for cohort in state.cohorts
            for snapshot in cohort.daily_snapshots
        )
    )
    if not state.cohorts:
        status = "empty"
    elif history_incomplete:
        status = "history_incomplete"
    elif state.mode == "formal_sequence" and len(state.cohorts) < 3:
        status = "incomplete_partitions"
    elif state.mode == "formal_sequence" and state.assembly is not None:
        status = state.assembly.quality_status
    elif all(
        item.outcome is not None and item.outcome.status == "ready"
        for item in state.cohorts
    ):
        status = "ready"
    elif "daily_capture_confirmation_required" in actions:
        status = "capture_required"
    else:
        status = "pending_maturity"
    return ReplayCampaignResumeReport(
        status=status,
        actions=tuple(actions),
        state=state,
    )


def resume_campaign(
    *,
    campaign_dir: Path,
    now: datetime,
    confirm_live_close_capture: bool = False,
    day_kind_provider: Callable[[date], str] = _official_day_kind,
    capture: Callable = capture_replay_outcome_day_from_files,
    collect: Callable = collect_replay_objective_outcomes_from_files,
    assemble: Callable = assemble_replay_artifacts,
) -> ReplayCampaignResumeReport:
    _, lock = _acquire_campaign_mutation_lock(campaign_dir)
    try:
        return _resume_campaign_unlocked(
            campaign_dir=campaign_dir,
            now=now,
            confirm_live_close_capture=confirm_live_close_capture,
            day_kind_provider=day_kind_provider,
            capture=capture,
            collect=collect,
            assemble=assemble,
        )
    finally:
        lock.release()
