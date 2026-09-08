"""从不可变前向工件导出独立、盲化的阶段9标签任务。

任务只引用采集时冻结的官方来源快照，不包含确定性规则输出，也不生成
任何标签。它不是用户审批流程，而是用于之后独立评测的数据任务清单。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Callable, List, Literal, Sequence
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from radar.replay_assembly import (
    _load_forward_artifact,
    _private_tmp_child,
    _safe_file,
    _sha256,
)
from radar.replay_contracts import RadarReplayModel, RadarReplaySampleRole
from radar.replay_forward_baseline import _atomic_write_json, _validate_output_dir


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
REQUIRED_LABEL_DOMAINS = ["market", "sector", "etf", "leader"]


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


class RadarReplayLabelTaskFile(RadarReplayModel):
    path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RadarReplayLabelTaskSample(RadarReplayModel):
    sample_id: str = Field(alias="sampleId", min_length=1, max_length=160)
    radar_run_id: str = Field(alias="radarRunId", min_length=1, max_length=240)
    role: RadarReplaySampleRole
    as_of: datetime = Field(alias="asOf")
    source_snapshots: RadarReplayLabelTaskFile = Field(alias="sourceSnapshots")
    replay_input: RadarReplayLabelTaskFile = Field(alias="replayInput")
    required_label_domains: List[Literal["market", "sector", "etf", "leader"]] = (
        Field(alias="requiredLabelDomains")
    )
    rule_output_included: Literal[False] = Field(alias="ruleOutputIncluded")
    evaluation_mode: Literal["automatic_objective_outcome"] = Field(
        default="automatic_objective_outcome",
        alias="evaluationMode",
    )
    manual_approval_required: Literal[False] = Field(
        default=False,
        alias="manualApprovalRequired",
    )
    minimum_maturity_trading_days: Literal[5] = Field(
        default=5,
        alias="minimumMaturityTradingDays",
    )

    @model_validator(mode="after")
    def validate_task(self):
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("asOf_timezone_required")
        if self.required_label_domains != REQUIRED_LABEL_DOMAINS:
            raise ValueError("label_task_required_domains_unverified")
        return self


class RadarReplayLabelTaskBundle(RadarReplayModel):
    contract_id: Literal["radar-replay-label-task-v1"] = Field(
        default="radar-replay-label-task-v1",
        alias="contractId",
    )
    task_bundle_id: str = Field(alias="taskBundleId", min_length=1, max_length=240)
    created_at: datetime = Field(alias="createdAt")
    samples: List[RadarReplayLabelTaskSample] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bundle(self):
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("createdAt_timezone_required")
        identities = [
            (item.sample_id, item.radar_run_id, item.as_of)
            for item in self.samples
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate_label_task_sample_identity")
        if any(item.as_of > self.created_at for item in self.samples):
            raise ValueError("label_task_createdAt_future")
        return self


@dataclass(frozen=True)
class RadarReplayLabelTaskResult:
    output_dir: Path
    task_bundle_path: Path
    manifest_path: Path
    bundle: RadarReplayLabelTaskBundle


def _source_snapshot_record(root: Path):
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("label_task_manifest_unverified") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    source_entry = files.get("sourceSnapshots") if isinstance(files, dict) else None
    if not isinstance(source_entry, dict):
        raise ValueError("label_task_manifest_unverified")
    source_path = _safe_file(root, source_entry.get("path"))
    source_digest = _sha256(source_path)
    if source_digest != source_entry.get("sha256"):
        raise ValueError("label_task_source_snapshot_hash_mismatch")
    return source_path, source_digest


def export_replay_label_tasks(
    *,
    input_dirs: Sequence[Path],
    output_dir: Path,
    clock: Callable[[], datetime] = _now,
) -> RadarReplayLabelTaskResult:
    if not input_dirs:
        raise ValueError("input_artifacts_required")
    roots = [_private_tmp_child(Path(value)) for value in input_dirs]
    if len(roots) != len(set(roots)):
        raise ValueError("duplicate_input_artifact")
    resolved_output = _validate_output_dir(Path(output_dir))
    if resolved_output in roots:
        raise ValueError("output_dir_conflicts_with_input")

    tasks = []
    identity_parts = []
    input_records = []
    for root in roots:
        replay, replay_record = _load_forward_artifact(root)
        sample = replay.samples[0]
        if sample.expected_labels:
            raise ValueError("label_task_source_already_labeled")
        source_path, source_digest = _source_snapshot_record(root)
        replay_path = root / "replay-input.json"
        tasks.append(RadarReplayLabelTaskSample(
            sampleId=sample.sample_id,
            radarRunId=sample.radar_run_id,
            role=sample.role,
            asOf=sample.as_of,
            sourceSnapshots={
                "path": str(source_path),
                "sha256": source_digest,
            },
            replayInput={
                "path": str(replay_path),
                "sha256": replay_record["replayInputSha256"],
            },
            requiredLabelDomains=REQUIRED_LABEL_DOMAINS,
            ruleOutputIncluded=False,
        ))
        identity_parts.append(
            f"{replay_record['replayInputSha256']}:{source_digest}"
        )
        input_records.append({
            **replay_record,
            "sourceSnapshotsSha256": source_digest,
        })
    tasks.sort(key=lambda item: (item.as_of, item.sample_id))

    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("createdAt_timezone_required")
    digest = hashlib.sha256(
        "|".join(sorted(identity_parts)).encode("utf-8")
    ).hexdigest()
    bundle = RadarReplayLabelTaskBundle(
        taskBundleId=f"stage9-label-tasks-{digest[:24]}",
        createdAt=created_at,
        samples=tasks,
    )

    resolved_output.mkdir(parents=True, exist_ok=False)
    bundle_path = resolved_output / "label-tasks.json"
    manifest_path = resolved_output / "manifest.json"
    bundle_sha = _atomic_write_json(bundle_path, bundle)
    _atomic_write_json(manifest_path, {
        "contractId": "radar-replay-label-task-manifest-v1",
        "taskBundleId": bundle.task_bundle_id,
        "createdAt": bundle.created_at,
        "inputArtifacts": input_records,
        "files": {
            "labelTasks": {
                "path": bundle_path.name,
                "sha256": bundle_sha,
            },
        },
    })
    return RadarReplayLabelTaskResult(
        output_dir=resolved_output,
        task_bundle_path=bundle_path,
        manifest_path=manifest_path,
        bundle=bundle,
    )
