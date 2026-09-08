"""阶段9正式前向样本的一次性安全编排。

该模块只使用调用方显式提供的 ``/private/tmp`` 阶段6工件，并把既有
前向来源采集、确定性输出、盲化标签任务和正式活动登记串成一个原子顺序。
活动清单只在前三段全部成功后才追加，不访问 SQLite，也不自动补历史事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import wraps
import json
from pathlib import Path
from typing import Callable, Optional, Sequence

from market_calendar import get_calendar_day_kind
from radar.etf_formal_admission import (
    EtfFormalAdmissionStatus,
    load_etf_formal_admission_bundle,
)
from radar.replay_assembly import (
    _load_forward_artifact,
    _private_tmp_child,
    _safe_file,
    _sha256,
)
from radar.replay_campaign import (
    FORMAL_ROLES,
    SHANGHAI_TZ,
    _FORMAL_REGISTRATION_TOKEN,
    ReplayCampaignState,
    _verify_cohort_artifacts,
    load_campaign,
    register_cohort,
)
from radar.replay_forward_baseline import (
    ForwardBaselineResult,
    _atomic_write_json,
    _validate_output_dir,
    collect_forward_replay_baseline,
)
from radar.replay_label_tasks import (
    RadarReplayLabelTaskBundle,
    RadarReplayLabelTaskResult,
    export_replay_label_tasks,
)
from radar.replay_output_bridge import (
    RadarReplayOutputBridgeResult,
    export_sector_replay_output,
)
from radar.replay_contracts import RadarReplayInput, RadarReplayOutputBundle
from radar.replay_service import RadarReplayQualityReport
from radar.run_lock import CrossProcessFileLock
from radar.sector_state_producer import SectorLifecycleState


FORMAL_COLLECTION_LOCK_NAME = ".formal-cohort-collection.lock"


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _official_day_kind(day: date) -> str:
    return get_calendar_day_kind("cn", day).kind


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_timezone_required")
    return value


def _continuous_session(value: datetime) -> Optional[str]:
    local_time = value.astimezone(SHANGHAI_TZ).time().replace(tzinfo=None)
    if time(9, 30) <= local_time <= time(11, 30):
        return "morning"
    if time(13, 0) <= local_time < time(14, 57):
        return "afternoon"
    return None


def _normalize_formal_etf_symbols(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(value or "").strip() for value in values)
    if not normalized:
        raise ValueError("replay_formal_cohort_formal_etf_required")
    if (
        len(normalized) != len(set(normalized))
        or len(normalized) > 10
        or any(len(symbol) != 6 or not symbol.isdigit() for symbol in normalized)
    ):
        raise ValueError("replay_formal_cohort_formal_etf_invalid")
    return normalized


@dataclass(frozen=True)
class FormalCohortCollectionPlan:
    role: str
    trade_date: date
    campaign_id: str
    campaign_revision: int


@dataclass(frozen=True)
class FormalCohortPreparationResult:
    plan: FormalCohortCollectionPlan
    baseline: ForwardBaselineResult
    output: RadarReplayOutputBridgeResult
    tasks: RadarReplayLabelTaskResult
    campaign_state: ReplayCampaignState
    manifest_path: Path


def plan_formal_cohort_collection(
    *,
    campaign_dir: Path,
    now: datetime,
    day_kind_provider: Callable[[date], str] = _official_day_kind,
) -> FormalCohortCollectionPlan:
    current = _aware(now, "now").astimezone(SHANGHAI_TZ)
    state = load_campaign(Path(campaign_dir))
    if state.mode != "formal_sequence":
        raise ValueError("replay_formal_cohort_campaign_mode_invalid")
    if len(state.cohorts) >= len(FORMAL_ROLES):
        raise ValueError("replay_formal_cohort_sequence_complete")
    day_kind = day_kind_provider(current.date())
    if day_kind == "unknown":
        raise ValueError("replay_formal_cohort_calendar_unverifiable")
    if day_kind != "full":
        raise ValueError("replay_formal_cohort_trading_day_required")
    if _continuous_session(current) is None:
        raise ValueError("replay_formal_cohort_continuous_session_required")
    if state.cohorts:
        previous_date = state.cohorts[-1].sample.as_of.astimezone(
            SHANGHAI_TZ
        ).date()
        if current.date() <= previous_date:
            raise ValueError("replay_formal_cohort_new_trade_date_required")
        for cohort in state.cohorts:
            _verify_cohort_artifacts(cohort)
            sample_date = cohort.sample.as_of.astimezone(SHANGHAI_TZ).date()
            baselines = [
                item for item in cohort.daily_snapshots
                if item.trade_date == sample_date
            ]
            if len(baselines) != 1 or baselines[0].status != "ready":
                raise ValueError(
                    "replay_formal_cohort_previous_baseline_not_ready"
                )
    return FormalCohortCollectionPlan(
        role=FORMAL_ROLES[len(state.cohorts)],
        trade_date=current.date(),
        campaign_id=state.campaign_id,
        campaign_revision=state.revision,
    )


def _load_live_stage6_identity(
    *,
    stage6_artifact_path: Path,
    now: datetime,
) -> tuple[Path, Path, str, datetime]:
    stage6_path = _private_tmp_child(Path(stage6_artifact_path))
    if not stage6_path.is_file():
        raise ValueError("replay_formal_cohort_stage6_artifact_missing")
    try:
        payload = json.loads(stage6_path.read_text(encoding="utf-8"))
        market_state = payload["marketResearchState"]
        radar_run_id = str(market_state["radarRunId"]).strip()
        observed_at = datetime.fromisoformat(market_state["asOf"])
        sector_path = _private_tmp_child(
            Path(payload["sectorStateSnapshotPath"])
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "replay_formal_cohort_stage6_artifact_unverified"
        ) from exc
    observed_at = _aware(observed_at, "stage6ObservedAt").astimezone(
        SHANGHAI_TZ
    )
    current = _aware(now, "now").astimezone(SHANGHAI_TZ)
    if (
        market_state.get("contractId")
        != "radar-market-research-state-v1"
        or market_state.get("status") != "ready"
        or market_state.get("researchUsable") is not True
        or market_state.get("formalUsable") is not False
        or not radar_run_id
    ):
        raise ValueError("replay_formal_cohort_stage6_artifact_unverified")
    if not sector_path.is_file():
        raise ValueError("replay_formal_cohort_sector_snapshot_missing")
    if observed_at > current:
        raise ValueError("replay_formal_cohort_stage6_from_future")
    if (
        observed_at.date() != current.date()
        or _continuous_session(observed_at) != _continuous_session(current)
    ):
        raise ValueError("replay_formal_cohort_stage6_session_mismatch")
    return stage6_path, sector_path, radar_run_id, observed_at


def _validate_forward_source_quality(report: object, role: str) -> None:
    expected_counts = {
        "development": int(role == "development"),
        "calibration": int(role == "calibration"),
        "holdout": int(role == "holdout"),
    }
    if (
        getattr(report, "sample_counts", None) != expected_counts
        or getattr(report, "missing_domains", None)
        or getattr(report, "missing_count", 0) != 0
        or getattr(report, "unverifiable_count", 0) != 0
        or getattr(report, "failed_count", 0) != 0
        or getattr(report, "future_violation_count", 0) != 0
        or getattr(report, "duplicate_state_violation_count", 0) != 0
        or getattr(report, "multi_state_violation_count", 0) != 0
    ):
        raise ValueError("replay_formal_cohort_source_quality_not_ready")
    # 逐文档具名排除是质量报告必须披露的真实样本组成，不等于来源失败；
    # 这里不删除、不改写，也不把它伪装成已覆盖。


def _validate_forward_baseline_artifacts(
    baseline: ForwardBaselineResult,
) -> tuple[RadarReplayInput, RadarReplayQualityReport]:
    try:
        root = _private_tmp_child(Path(baseline.output_dir))
        manifest_path = _private_tmp_child(Path(baseline.manifest_path))
        if (
            not root.is_dir()
            or not manifest_path.is_file()
            or manifest_path.parent != root
        ):
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        source_entry = files["sourceSnapshots"]
        replay_entry = files["replayInput"]
        quality_entry = files["qualityReport"]
        if not all(
            isinstance(item, dict)
            for item in (source_entry, replay_entry, quality_entry)
        ):
            raise ValueError
        source_path = _safe_file(root, source_entry.get("path"))
        replay_path = _safe_file(root, replay_entry.get("path"))
        quality_path = _safe_file(root, quality_entry.get("path"))
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
        source_started_at = datetime.fromisoformat(source_payload["startedAt"])
        source_sample_as_of = datetime.fromisoformat(
            source_payload["sampleAsOf"]
        )
        source_domains = source_payload["sources"]
        replay, _ = _load_forward_artifact(root)
        report = RadarReplayQualityReport.model_validate_json(
            quality_path.read_text(encoding="utf-8")
        )
        sample_as_of = datetime.fromisoformat(manifest["sampleAsOf"])
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "replay_formal_cohort_baseline_artifact_unverified"
        ) from exc
    if len(replay.samples) != 1:
        raise ValueError("replay_formal_cohort_baseline_artifact_unverified")
    sample = replay.samples[0]
    required_source_domains = {
        "security_universe",
        "industry",
        "index",
        "etf",
        "corporate_action",
        "etf_formal_admission_collection",
    }
    if (
        manifest.get("contractId")
        != "radar-replay-forward-baseline-manifest-v1"
        or manifest.get("replayRunId") != replay.replay_run_id
        or manifest.get("radarRunId") != sample.radar_run_id
        or manifest.get("sampleRole") != sample.role
        or _aware(sample_as_of, "baselineSampleAsOf") != sample.as_of
        or manifest.get("status") != report.status
        or source_path != Path(baseline.source_snapshots_path).resolve()
        or replay_path != Path(baseline.replay_input_path).resolve()
        or quality_path != Path(baseline.quality_report_path).resolve()
        or _sha256(source_path) != source_entry.get("sha256")
        or _sha256(replay_path) != replay_entry.get("sha256")
        or _sha256(quality_path) != quality_entry.get("sha256")
        or replay != baseline.replay
        or report != baseline.report
        or not isinstance(source_payload, dict)
        or source_payload.get("radarRunId") != sample.radar_run_id
        or source_payload.get("sampleRole") != sample.role
        or _aware(source_sample_as_of, "sourceSampleAsOf") != sample.as_of
        or _aware(source_started_at, "sourceStartedAt") > sample.as_of
        or not isinstance(source_domains, dict)
        or set(source_domains) != required_source_domains
        or any(
            not isinstance(source_domains[domain], dict)
            for domain in required_source_domains
        )
    ):
        raise ValueError("replay_formal_cohort_baseline_artifact_unverified")
    etf_entry = files.get("etfFormalAdmission")
    if baseline.etf_formal_admission_path is None:
        if etf_entry is not None:
            raise ValueError(
                "replay_formal_cohort_baseline_artifact_unverified"
            )
    else:
        try:
            if not isinstance(etf_entry, dict):
                raise ValueError
            etf_path = _safe_file(root, etf_entry.get("path"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "replay_formal_cohort_baseline_artifact_unverified"
            ) from exc
        if (
            etf_path != Path(baseline.etf_formal_admission_path).resolve()
            or _sha256(etf_path) != etf_entry.get("sha256")
        ):
            raise ValueError(
                "replay_formal_cohort_baseline_artifact_unverified"
            )
    return replay, report


def _validate_etf_monitoring_bundle(
    *,
    path: Path,
    sample_id: str,
    radar_run_id: str,
    sample_as_of: datetime,
    requested_symbols: Sequence[str],
) -> None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        bundle = load_etf_formal_admission_bundle(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "replay_formal_cohort_etf_admission_unverified"
        ) from exc
    if (
        bundle.sample_id != sample_id
        or bundle.radar_run_id != radar_run_id
        or bundle.as_of != sample_as_of
    ):
        raise ValueError("replay_formal_cohort_etf_admission_identity_mismatch")
    actual_symbols = tuple(sorted(item.symbol for item in bundle.admissions))
    if actual_symbols != tuple(sorted(requested_symbols)):
        raise ValueError("replay_formal_cohort_etf_admission_coverage_mismatch")
    if any(
        item.monitoring_status != EtfFormalAdmissionStatus.READY
        for item in bundle.admissions
    ):
        raise ValueError("replay_formal_cohort_etf_monitoring_not_ready")


def _validate_rule_output_bundle(
    *,
    output: RadarReplayOutputBridgeResult,
    sample_id: str,
    radar_run_id: str,
    sample_as_of: datetime,
    requested_etf_symbols: Sequence[str],
) -> None:
    try:
        bundle_path = _private_tmp_child(Path(output.output_bundle_path))
        manifest_path = _private_tmp_child(Path(output.manifest_path))
        if (
            not bundle_path.is_file()
            or not manifest_path.is_file()
            or bundle_path.parent != manifest_path.parent
        ):
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = RadarReplayOutputBundle.model_validate_json(
            bundle_path.read_text(encoding="utf-8")
        )
        output_ref = manifest["files"]["outputBundle"]
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "replay_formal_cohort_output_artifact_unverified"
        ) from exc
    if (
        manifest.get("contractId")
        != "radar-replay-output-bridge-manifest-v1"
        or manifest.get("bundleId") != bundle.bundle_id
        or not isinstance(output_ref, dict)
        or output_ref.get("path") != bundle_path.name
        or output_ref.get("sha256") != _sha256(bundle_path)
        or bundle != output.bundle
    ):
        raise ValueError("replay_formal_cohort_output_artifact_unverified")
    if len(bundle.samples) != 1:
        raise ValueError("replay_formal_cohort_output_identity_mismatch")
    sample = bundle.samples[0]
    if (
        sample.sample_id != sample_id
        or sample.radar_run_id != radar_run_id
        or sample.as_of != sample_as_of
    ):
        raise ValueError("replay_formal_cohort_output_identity_mismatch")
    required_domains = {"market", "sector", "etf", "leader"}
    by_domain = {}
    for item in sample.evidence:
        if item.domain in by_domain:
            raise ValueError("replay_formal_cohort_output_domains_not_ready")
        by_domain[item.domain] = item
    if set(by_domain) != required_domains:
        raise ValueError("replay_formal_cohort_output_domains_not_ready")
    allowed_states = {
        "market": {"strong", "oscillation", "retreat", "risk"},
        "sector": {item.value for item in SectorLifecycleState},
        "etf": {
            "active_product_separate_track",
            "product_ready_for_index_research",
            "out_of_scope_asset",
            "product_evidence_incomplete",
        },
        "leader": {"research_qualified"},
    }
    for domain, item in by_domain.items():
        states = item.payload.get("states")
        if item.status != "ready" or not isinstance(states, list):
            raise ValueError("replay_formal_cohort_output_domains_not_ready")
        if domain != "leader" and not states:
            raise ValueError("replay_formal_cohort_output_domains_not_ready")
        targets = []
        for state in states:
            target_id = (
                str(state.get("targetId") or "").strip()
                if isinstance(state, dict)
                else ""
            )
            state_name = (
                str(state.get("state") or "").strip()
                if isinstance(state, dict)
                else ""
            )
            if (
                not target_id
                or state_name not in allowed_states[domain]
                or (
                    domain == "leader"
                    and (len(target_id) != 6 or not target_id.isdigit())
                )
            ):
                raise ValueError("replay_formal_cohort_output_domains_not_ready")
            targets.append(target_id)
        if len(targets) != len(set(targets)):
            raise ValueError("replay_formal_cohort_output_domains_not_ready")
        if domain == "market" and targets != ["a-share"]:
            raise ValueError("replay_formal_cohort_output_domains_not_ready")

    etf_output = by_domain["etf"]
    requested = tuple(sorted(requested_etf_symbols))
    formal_keys = {
        "monitoringStatus",
        "rankingStatus",
        "monitoringReasons",
        "rankingReasons",
    }
    formal_states = {
        item["targetId"]: item
        for item in etf_output.payload["states"]
        if isinstance(item, dict) and formal_keys.intersection(item)
    }
    if set(formal_states) != set(requested):
        raise ValueError("replay_formal_cohort_etf_output_not_ready")
    ranking_ready_count = 0
    ranking_missing_count = 0
    for item in formal_states.values():
        monitoring_reasons = item.get("monitoringReasons")
        ranking_reasons = item.get("rankingReasons")
        if (
            item.get("state") != "product_ready_for_index_research"
            or item.get("monitoringStatus") != "ready"
            or monitoring_reasons != []
            or not isinstance(ranking_reasons, list)
        ):
            raise ValueError("replay_formal_cohort_etf_output_not_ready")
        if item.get("rankingStatus") == "ready" and ranking_reasons == []:
            ranking_ready_count += 1
        elif item.get("rankingStatus") == "missing" and ranking_reasons:
            ranking_missing_count += 1
        else:
            raise ValueError("replay_formal_cohort_etf_output_not_ready")
    if (
        not etf_output.source_id.startswith(
            "radar-etf-product-research-output-v2:"
        )
        or etf_output.payload.get("formalAdmissionCount") != len(requested)
        or etf_output.payload.get("monitoringReadyCount") != len(requested)
        or etf_output.payload.get("monitoringMissingCount") != 0
        or etf_output.payload.get("rankingPolicyReadyCount")
        != ranking_ready_count
        or etf_output.payload.get("rankingPolicyMissingCount")
        != ranking_missing_count
    ):
        raise ValueError("replay_formal_cohort_etf_output_not_ready")


def _formal_run_artifact_ref(path: Path) -> dict[str, str]:
    try:
        resolved = _private_tmp_child(Path(path))
        if not resolved.is_file():
            raise ValueError
        digest = _sha256(resolved)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(
            "replay_formal_cohort_run_artifact_unverified"
        ) from exc
    return {"path": str(resolved), "sha256": digest}


def _validate_unchanged_formal_run_artifact(
    *,
    path: Path,
    expected: dict[str, str],
) -> None:
    try:
        actual = _formal_run_artifact_ref(path)
    except ValueError as exc:
        raise ValueError(
            "replay_formal_cohort_source_artifact_changed"
        ) from exc
    if actual != expected:
        raise ValueError("replay_formal_cohort_source_artifact_changed")


def _validate_label_task_artifacts(
    *,
    tasks: RadarReplayLabelTaskResult,
    baseline: ForwardBaselineResult,
    sample: object,
) -> None:
    try:
        root = _private_tmp_child(Path(tasks.output_dir))
        bundle_path = _private_tmp_child(Path(tasks.task_bundle_path))
        manifest_path = _private_tmp_child(Path(tasks.manifest_path))
        if (
            not root.is_dir()
            or not bundle_path.is_file()
            or not manifest_path.is_file()
            or bundle_path.parent != root
            or manifest_path.parent != root
        ):
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = RadarReplayLabelTaskBundle.model_validate_json(
            bundle_path.read_text(encoding="utf-8")
        )
        label_ref = manifest["files"]["labelTasks"]
        input_records = manifest["inputArtifacts"]
        manifest_created_at = datetime.fromisoformat(manifest["createdAt"])
        task = bundle.samples[0]
        input_record = input_records[0]
        baseline_root = _private_tmp_child(Path(baseline.output_dir))
        baseline_manifest = _private_tmp_child(Path(baseline.manifest_path))
        source_path = _private_tmp_child(Path(baseline.source_snapshots_path))
        replay_path = _private_tmp_child(Path(baseline.replay_input_path))
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "replay_formal_cohort_label_task_artifact_unverified"
        ) from exc
    if (
        manifest.get("contractId")
        != "radar-replay-label-task-manifest-v1"
        or manifest.get("taskBundleId") != bundle.task_bundle_id
        or _aware(manifest_created_at, "labelTaskCreatedAt")
        != bundle.created_at
        or not isinstance(label_ref, dict)
        or label_ref.get("path") != bundle_path.name
        or label_ref.get("sha256") != _sha256(bundle_path)
        or not isinstance(input_records, list)
        or len(input_records) != 1
        or not isinstance(input_record, dict)
        or bundle != tasks.bundle
        or len(bundle.samples) != 1
        or task.sample_id != sample.sample_id
        or task.radar_run_id != sample.radar_run_id
        or task.role != sample.role
        or task.as_of != sample.as_of
        or Path(task.source_snapshots.path).resolve() != source_path
        or task.source_snapshots.sha256 != _sha256(source_path)
        or Path(task.replay_input.path).resolve() != replay_path
        or task.replay_input.sha256 != _sha256(replay_path)
        or input_record.get("directory") != str(baseline_root)
        or input_record.get("manifestSha256") != _sha256(baseline_manifest)
        or input_record.get("replayInputSha256") != _sha256(replay_path)
        or input_record.get("replayRunId") != baseline.replay.replay_run_id
        or input_record.get("sampleId") != sample.sample_id
        or input_record.get("sampleRole") != sample.role
        or input_record.get("sourceSnapshotsSha256") != _sha256(source_path)
    ):
        raise ValueError(
            "replay_formal_cohort_label_task_artifact_unverified"
        )


def _validate_registered_campaign_state(
    *,
    state: ReplayCampaignState,
    plan: FormalCohortCollectionPlan,
    sample: object,
    task_ref: dict[str, str],
    output_ref: dict[str, str],
    updated_at: datetime,
) -> None:
    try:
        expected_count = FORMAL_ROLES.index(plan.role) + 1
        cohort = state.cohorts[-1]
        registered_sample = cohort.sample
        registered_task = cohort.task_bundle
        registered_output = cohort.output_bundle
    except (AttributeError, IndexError, ValueError) as exc:
        raise ValueError(
            "replay_formal_cohort_registration_unverified"
        ) from exc
    if (
        state.campaign_id != plan.campaign_id
        or state.mode != "formal_sequence"
        or state.revision != plan.campaign_revision + 1
        or state.updated_at != updated_at
        or len(state.cohorts) != expected_count
        or registered_sample.sample_id != sample.sample_id
        or registered_sample.radar_run_id != sample.radar_run_id
        or registered_sample.role != plan.role
        or registered_sample.as_of != sample.as_of
        or registered_task.path != task_ref["path"]
        or registered_task.sha256 != task_ref["sha256"]
        or registered_output.path != output_ref["path"]
        or registered_output.sha256 != output_ref["sha256"]
        or cohort.daily_snapshots
        or cohort.outcome is not None
    ):
        raise ValueError("replay_formal_cohort_registration_unverified")


def _serialize_formal_collection(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if kwargs.get("confirm_live_cohort") is not True:
            return function(*args, **kwargs)
        campaign_dir = kwargs.get("campaign_dir")
        if campaign_dir is None:
            return function(*args, **kwargs)
        root = _private_tmp_child(Path(campaign_dir))
        lock = CrossProcessFileLock(root / FORMAL_COLLECTION_LOCK_NAME)
        if not lock.acquire(blocking=False):
            raise ValueError("replay_formal_cohort_collection_locked")
        try:
            return function(*args, **kwargs)
        finally:
            lock.release()

    return wrapped


@_serialize_formal_collection
def prepare_formal_cohort(
    *,
    confirm_live_cohort: bool,
    campaign_dir: Path,
    output_dir: Path,
    stage6_artifact_path: Path,
    formal_etf_symbols: Sequence[str] = (),
    cninfo_pdf_cache_dir: Optional[Path] = None,
    clock: Callable[[], datetime] = _now,
    day_kind_provider: Callable[[date], str] = _official_day_kind,
    collect_baseline: Callable[..., ForwardBaselineResult] = (
        collect_forward_replay_baseline
    ),
    export_output: Callable[..., RadarReplayOutputBridgeResult] = (
        export_sector_replay_output
    ),
    export_tasks: Callable[..., RadarReplayLabelTaskResult] = (
        export_replay_label_tasks
    ),
    register: Callable[..., ReplayCampaignState] = register_cohort,
) -> FormalCohortPreparationResult:
    if confirm_live_cohort is not True:
        raise ValueError("confirmation_required")
    started_at = _aware(clock(), "startedAt")
    plan = plan_formal_cohort_collection(
        campaign_dir=campaign_dir,
        now=started_at,
        day_kind_provider=day_kind_provider,
    )
    stage6_path, sector_path, radar_run_id, stage6_observed_at = (
        _load_live_stage6_identity(
            stage6_artifact_path=stage6_artifact_path,
            now=started_at,
        )
    )
    root = _validate_output_dir(Path(output_dir))
    source_stage6_ref = _formal_run_artifact_ref(stage6_path)
    source_sector_ref = _formal_run_artifact_ref(sector_path)
    normalized_etfs = _normalize_formal_etf_symbols(formal_etf_symbols)
    baseline = collect_baseline(
        confirm_live_baseline=True,
        output_dir=root / "baseline",
        sample_role=plan.role,
        cninfo_pdf_cache_dir=cninfo_pdf_cache_dir,
        radar_run_id=radar_run_id,
        formal_etf_symbols=normalized_etfs,
        clock=clock,
    )
    verified_replay, verified_report = _validate_forward_baseline_artifacts(
        baseline
    )
    if len(verified_replay.samples) != 1:
        raise ValueError("replay_formal_cohort_single_sample_required")
    sample = verified_replay.samples[0]
    sample_as_of = _aware(sample.as_of, "sampleAsOf").astimezone(SHANGHAI_TZ)
    if (
        sample.role != plan.role
        or sample.radar_run_id != radar_run_id
        or sample_as_of.date() != plan.trade_date
    ):
        raise ValueError("replay_formal_cohort_sample_identity_mismatch")
    if _continuous_session(sample_as_of) != _continuous_session(stage6_observed_at):
        raise ValueError("replay_formal_cohort_sample_session_mismatch")
    _validate_forward_source_quality(verified_report, plan.role)
    if baseline.etf_formal_admission_path is None:
        raise ValueError("replay_formal_cohort_etf_admission_missing")
    _validate_etf_monitoring_bundle(
        path=baseline.etf_formal_admission_path,
        sample_id=sample.sample_id,
        radar_run_id=sample.radar_run_id,
        sample_as_of=sample.as_of,
        requested_symbols=normalized_etfs,
    )

    output = export_output(
        sample_id=sample.sample_id,
        radar_run_id=sample.radar_run_id,
        sample_as_of=sample.as_of,
        sector_snapshot_path=sector_path,
        stage6_artifact_path=stage6_path,
        etf_replay_input_path=baseline.replay_input_path,
        etf_formal_admission_path=baseline.etf_formal_admission_path,
        output_dir=root / "outputs",
        clock=clock,
    )
    _validate_rule_output_bundle(
        output=output,
        sample_id=sample.sample_id,
        radar_run_id=sample.radar_run_id,
        sample_as_of=sample.as_of,
        requested_etf_symbols=normalized_etfs,
    )
    tasks = export_tasks(
        input_dirs=[root / "baseline"],
        output_dir=root / "tasks",
        clock=clock,
    )
    _validate_label_task_artifacts(
        tasks=tasks,
        baseline=baseline,
        sample=sample,
    )
    _validate_unchanged_formal_run_artifact(
        path=stage6_path,
        expected=source_stage6_ref,
    )
    _validate_unchanged_formal_run_artifact(
        path=sector_path,
        expected=source_sector_ref,
    )
    run_files = {
        "sourceSnapshots": _formal_run_artifact_ref(
            baseline.source_snapshots_path
        ),
        "replayInput": _formal_run_artifact_ref(baseline.replay_input_path),
        "qualityReport": _formal_run_artifact_ref(
            baseline.quality_report_path
        ),
        "etfFormalAdmission": _formal_run_artifact_ref(
            baseline.etf_formal_admission_path
        ),
        "baselineManifest": _formal_run_artifact_ref(baseline.manifest_path),
        "outputBundle": _formal_run_artifact_ref(output.output_bundle_path),
        "outputManifest": _formal_run_artifact_ref(output.manifest_path),
        "labelTasks": _formal_run_artifact_ref(tasks.task_bundle_path),
        "labelTaskManifest": _formal_run_artifact_ref(tasks.manifest_path),
    }
    updated_at = _aware(clock(), "updatedAt")
    manifest_path = root / "formal-cohort-manifest.json"
    _atomic_write_json(manifest_path, {
        "contractId": "radar-replay-formal-cohort-run-v1",
        "createdAt": updated_at,
        "role": plan.role,
        "tradeDate": plan.trade_date,
        "sampleId": sample.sample_id,
        "radarRunId": sample.radar_run_id,
        "sampleAsOf": sample.as_of,
        "formalEtfSymbols": list(normalized_etfs),
        "sourceStage6Artifact": source_stage6_ref,
        "sourceSectorSnapshot": source_sector_ref,
        "files": run_files,
        "campaign": {
            "campaignId": plan.campaign_id,
            "revision": plan.campaign_revision + 1,
            "cohortCount": FORMAL_ROLES.index(plan.role) + 1,
        },
    })
    register_kwargs = {
        "campaign_dir": campaign_dir,
        "task_bundle_path": tasks.task_bundle_path,
        "output_bundle_path": output.output_bundle_path,
        "updated_at": updated_at,
        "expected_revision": plan.campaign_revision,
    }
    if register is register_cohort:
        register_kwargs["_formal_entry_token"] = _FORMAL_REGISTRATION_TOKEN
    campaign_state = register(
        **register_kwargs,
    )
    _validate_registered_campaign_state(
        state=campaign_state,
        plan=plan,
        sample=sample,
        task_ref=run_files["labelTasks"],
        output_ref=run_files["outputBundle"],
        updated_at=updated_at,
    )
    return FormalCohortPreparationResult(
        plan=plan,
        baseline=baseline,
        output=output,
        tasks=tasks,
        campaign_state=campaign_state,
        manifest_path=manifest_path,
    )
