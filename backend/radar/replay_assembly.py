"""阶段9不可变前向工件聚合。

只读取调用方显式提供的 ``/private/tmp`` 工件，逐个复核内容哈希和
样本内已冻结分区；不访问网络或SQLite，也不允许事后改写样本角色。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from radar.replay_contracts import (
    RadarReplayInput,
    RadarReplayLabelBundle,
    RadarReplayOutputBundle,
    RadarReplaySample,
)
from radar.replay_forward_baseline import _atomic_write_json, _validate_output_dir
from radar.replay_service import RadarReplayQualityReport, build_replay_quality_report


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FORWARD_MANIFEST_ID = "radar-replay-forward-baseline-manifest-v1"
ASSEMBLY_MANIFEST_ID = "radar-replay-assembly-manifest-v1"


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _private_tmp_child(value: Path) -> Path:
    resolved = value.expanduser().resolve()
    try:
        resolved.relative_to(Path("/private/tmp").resolve())
    except ValueError as exc:
        raise ValueError("input_dir_must_be_private_tmp") from exc
    if resolved == Path("/private/tmp").resolve():
        raise ValueError("input_dir_must_be_private_tmp_child")
    return resolved


def _safe_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("artifact_manifest_unverified")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact_manifest_unverified") from exc
    if not path.is_file():
        raise ValueError("artifact_file_missing")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_forward_artifact(root: Path):
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("artifact_manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact_manifest_unverified") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("contractId") != FORWARD_MANIFEST_ID
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ValueError("artifact_manifest_unverified")
    replay_entry = manifest["files"].get("replayInput")
    if not isinstance(replay_entry, dict):
        raise ValueError("artifact_manifest_unverified")
    replay_path = _safe_file(root, replay_entry.get("path"))
    digest = _sha256(replay_path)
    if digest != replay_entry.get("sha256"):
        raise ValueError("artifact_hash_mismatch")
    try:
        replay = RadarReplayInput.model_validate_json(
            replay_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ValueError("artifact_replay_unverified") from exc
    if manifest.get("replayRunId") != replay.replay_run_id:
        raise ValueError("artifact_identity_mismatch")
    if len(replay.samples) != 1:
        raise ValueError("forward_artifact_sample_count_invalid")
    manifest_role = manifest.get("sampleRole")
    if manifest_role is not None and manifest_role != replay.samples[0].role:
        raise ValueError("artifact_sample_role_mismatch")
    return replay, {
        "directory": str(root),
        "manifestSha256": _sha256(manifest_path),
        "replayInputSha256": digest,
        "replayRunId": replay.replay_run_id,
        "sampleId": replay.samples[0].sample_id,
        "sampleRole": replay.samples[0].role,
    }


def _load_label_bundle(path: Path):
    resolved = _private_tmp_child(path)
    if not resolved.is_file():
        raise ValueError("label_bundle_missing")
    try:
        bundle = RadarReplayLabelBundle.model_validate_json(
            resolved.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ValueError("label_bundle_unverified") from exc
    return bundle, {
        "path": str(resolved),
        "sha256": _sha256(resolved),
        "bundleId": bundle.bundle_id,
        "createdAt": bundle.created_at.isoformat(),
    }


def _load_output_bundle(path: Path):
    resolved = _private_tmp_child(path)
    if not resolved.is_file():
        raise ValueError("output_bundle_missing")
    try:
        bundle = RadarReplayOutputBundle.model_validate_json(
            resolved.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ValueError("output_bundle_unverified") from exc
    return bundle, {
        "path": str(resolved),
        "sha256": _sha256(resolved),
        "bundleId": bundle.bundle_id,
        "createdAt": bundle.created_at.isoformat(),
    }


@dataclass(frozen=True)
class RadarReplayAssemblyResult:
    output_dir: Path
    replay_input_path: Path
    quality_report_path: Path
    manifest_path: Path
    replay: RadarReplayInput
    report: RadarReplayQualityReport


@dataclass(frozen=True)
class RadarReplayAssemblyBuild:
    replay: RadarReplayInput
    report: RadarReplayQualityReport
    input_records: tuple[dict, ...]
    label_records: tuple[dict, ...]
    output_records: tuple[dict, ...]


def build_replay_assembly(
    *,
    input_dirs: Sequence[Path],
    label_bundle_paths: Sequence[Path] = (),
    output_bundle_paths: Sequence[Path] = (),
    clock: Callable[[], datetime] = _now,
) -> RadarReplayAssemblyBuild:
    """从不可变来源纯构建最终回放内容，不写入任何工件。"""
    if not input_dirs:
        raise ValueError("input_artifacts_required")
    roots = [_private_tmp_child(Path(value)) for value in input_dirs]
    if len(roots) != len(set(roots)):
        raise ValueError("duplicate_input_artifact")

    loaded = [_load_forward_artifact(root) for root in roots]
    samples = [item for replay, _ in loaded for item in replay.samples]
    samples.sort(key=lambda item: (item.as_of, item.sample_id))
    input_records = [record for _, record in loaded]
    loaded_bundles = [
        _load_label_bundle(Path(path)) for path in label_bundle_paths
    ]
    bundle_ids = [bundle.bundle_id for bundle, _ in loaded_bundles]
    if len(bundle_ids) != len(set(bundle_ids)):
        raise ValueError("duplicate_label_bundle")
    labels_by_identity = {}
    sample_identities = {
        (sample.sample_id, sample.radar_run_id, sample.as_of)
        for sample in samples
    }
    for bundle, _ in loaded_bundles:
        for label_set in bundle.samples:
            identity = (
                label_set.sample_id,
                label_set.radar_run_id,
                label_set.as_of,
            )
            if identity not in sample_identities:
                raise ValueError("label_sample_identity_missing")
            labels_by_identity.setdefault(identity, []).extend(label_set.labels)
    loaded_outputs = [
        _load_output_bundle(Path(path)) for path in output_bundle_paths
    ]
    output_bundle_ids = [bundle.bundle_id for bundle, _ in loaded_outputs]
    if len(output_bundle_ids) != len(set(output_bundle_ids)):
        raise ValueError("duplicate_output_bundle")
    outputs_by_identity = {}
    for bundle, _ in loaded_outputs:
        for output_set in bundle.samples:
            identity = (
                output_set.sample_id,
                output_set.radar_run_id,
                output_set.as_of,
            )
            if identity not in sample_identities:
                raise ValueError("output_sample_identity_missing")
            outputs_by_identity.setdefault(identity, []).extend(
                output_set.evidence
            )
    if labels_by_identity or outputs_by_identity:
        rebuilt = []
        for sample in samples:
            identity = (sample.sample_id, sample.radar_run_id, sample.as_of)
            payload = sample.model_dump(mode="python", by_alias=True)
            payload["evidence"] = [
                item.model_dump(mode="python", by_alias=True)
                for item in (
                    *sample.evidence,
                    *outputs_by_identity.get(identity, ()),
                )
            ]
            payload["expectedLabels"] = [
                item.model_dump(mode="python", by_alias=True)
                for item in (
                    *sample.expected_labels,
                    *labels_by_identity.get(identity, ()),
                )
            ]
            rebuilt.append(RadarReplaySample.model_validate(payload))
        samples = rebuilt
    identity_parts = [
        f"input:{record['replayInputSha256']}" for record in input_records
    ]
    identity_parts.extend(
        f"label:{record['sha256']}" for _, record in loaded_bundles
    )
    identity_parts.extend(
        f"output:{record['sha256']}" for _, record in loaded_outputs
    )
    identity_material = "|".join(sorted(identity_parts)).encode("utf-8")
    digest = hashlib.sha256(identity_material).hexdigest()
    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("createdAt_timezone_required")
    replay = RadarReplayInput(
        replayRunId=f"replay-assembly-{digest[:24]}",
        createdAt=created_at,
        samples=samples,
    )
    report = build_replay_quality_report(replay)
    return RadarReplayAssemblyBuild(
        replay=replay,
        report=report,
        input_records=tuple(input_records),
        label_records=tuple(record for _, record in loaded_bundles),
        output_records=tuple(record for _, record in loaded_outputs),
    )


def assemble_replay_artifacts(
    *,
    input_dirs: Sequence[Path],
    label_bundle_paths: Sequence[Path] = (),
    output_bundle_paths: Sequence[Path] = (),
    output_dir: Path,
    clock: Callable[[], datetime] = _now,
) -> RadarReplayAssemblyResult:
    roots = [_private_tmp_child(Path(value)) for value in input_dirs]
    resolved_output = _validate_output_dir(Path(output_dir))
    if resolved_output in roots:
        raise ValueError("output_dir_conflicts_with_input")
    built = build_replay_assembly(
        input_dirs=roots,
        label_bundle_paths=label_bundle_paths,
        output_bundle_paths=output_bundle_paths,
        clock=clock,
    )
    replay = built.replay
    report = built.report
    created_at = replay.created_at

    resolved_output.mkdir(parents=True, exist_ok=False)
    replay_path = resolved_output / "replay-input.json"
    report_path = resolved_output / "quality-report.json"
    manifest_path = resolved_output / "manifest.json"
    replay_sha = _atomic_write_json(replay_path, replay)
    report_sha = _atomic_write_json(report_path, report)
    manifest = {
        "contractId": ASSEMBLY_MANIFEST_ID,
        "replayRunId": replay.replay_run_id,
        "createdAt": created_at,
        "status": report.status,
        "inputArtifacts": list(built.input_records),
        "labelBundles": list(built.label_records),
        "outputBundles": list(built.output_records),
        "files": {
            "replayInput": {"path": replay_path.name, "sha256": replay_sha},
            "qualityReport": {"path": report_path.name, "sha256": report_sha},
        },
    }
    _atomic_write_json(manifest_path, manifest)
    return RadarReplayAssemblyResult(
        output_dir=resolved_output,
        replay_input_path=replay_path,
        quality_report_path=report_path,
        manifest_path=manifest_path,
        replay=replay,
        report=report,
    )
