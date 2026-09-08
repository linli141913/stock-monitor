"""阶段6真实五源与阶段9正式cohort的单命令盘中入口。"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
from typing import Callable, Optional, Sequence, TextIO

from market_calendar import get_calendar_day_kind
from radar.replay_assembly import _private_tmp_child
from radar.replay_formal_cohort import (
    _normalize_formal_etf_symbols,
    plan_formal_cohort_collection,
    prepare_formal_cohort,
)
from radar.replay_etf_research_store import publish_replay_etf_research
from radar.replay_store import DEFAULT_RADAR_REPLAY_STORE_DIR
from radar.run_lock import CrossProcessFileLock
from radar.strict_json import strict_json_loads
from run_leader_phase6_live_five_source_acceptance import (
    run_cli as run_stage6_five_source_cli,
)


FORMAL_LIVE_SEQUENCE_LOCK_NAME = ".formal-live-sequence.lock"


def _now() -> datetime:
    from radar.replay_campaign import SHANGHAI_TZ

    return datetime.now(SHANGHAI_TZ)


def _official_day_kind(day: date) -> str:
    return get_calendar_day_kind("cn", day).kind


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "先生成同会话阶段6真实工件，再生成并登记下一个"
            "阶段9正式回放cohort"
        ),
    )
    parser.add_argument("--confirm-live-formal-sequence", action="store_true")
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--stage6-output-dir", required=True, type=Path)
    parser.add_argument("--cohort-output-dir", required=True, type=Path)
    lineage = parser.add_mutually_exclusive_group(required=True)
    lineage.add_argument("--initialize-sector-state", action="store_true")
    lineage.add_argument("--previous-sector-state", type=Path)
    parser.add_argument("--cninfo-pdf-cache-dir", type=Path)
    parser.add_argument("--formal-shadow-input-root", type=Path)
    parser.add_argument(
        "--formal-etf",
        action="append",
        default=[],
        help="本轮必须生成真实监测准入证据的ETF；可重复指定",
    )
    return parser


def _print(payload: object, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False))
    stdout.write("\n")


def _validate_dirs(
    *,
    campaign_dir: Path,
    stage6_output_dir: Path,
    cohort_output_dir: Path,
    cninfo_pdf_cache_dir: Optional[Path] = None,
    formal_shadow_input_root: Optional[Path] = None,
) -> tuple[Path, Path, Path, Optional[Path], Optional[Path]]:
    campaign = _private_tmp_child(campaign_dir)
    stage6 = _private_tmp_child(stage6_output_dir)
    cohort = _private_tmp_child(cohort_output_dir)
    cache = (
        _private_tmp_child(cninfo_pdf_cache_dir)
        if cninfo_pdf_cache_dir is not None
        else None
    )
    formal_shadow_root = (
        _private_tmp_child(formal_shadow_input_root)
        if formal_shadow_input_root is not None
        else None
    )
    if not campaign.is_dir():
        raise ValueError("replay_formal_live_campaign_missing")
    if cohort.exists():
        raise ValueError("replay_formal_live_cohort_output_must_be_new")
    if cache is not None and cache.exists() and not cache.is_dir():
        raise ValueError("replay_formal_live_cninfo_cache_invalid")
    paths = [campaign, stage6, cohort]
    if cache is not None:
        paths.append(cache)
    if formal_shadow_root is not None:
        paths.append(formal_shadow_root)
    pairs = (
        (left, right)
        for index, left in enumerate(paths)
        for right in paths[index + 1:]
    )
    if any(
        left == right or left in right.parents or right in left.parents
        for left, right in pairs
    ):
        raise ValueError("replay_formal_live_output_dirs_overlap")
    return campaign, stage6, cohort, cache, formal_shadow_root


def _stage6_formal_shadow_input_dirs(
    payload: object,
    *,
    root: Path,
    allow_partial: bool = False,
) -> dict[str, str]:
    """只接受同轮阶段6明确发布的输入目录。"""
    allowed = {"trendRotation", "leaderObservation"}
    if (
        not isinstance(payload, dict)
        or not payload
        or not set(payload).issubset(allowed)
        or (not allow_partial and set(payload) != allowed)
    ):
        raise ValueError("replay_formal_live_shadow_inputs_unverified")
    verified: dict[str, str] = {}
    for module, raw_path in payload.items():
        if not isinstance(raw_path, str):
            raise ValueError("replay_formal_live_shadow_inputs_unverified")
        path = _private_tmp_child(Path(raw_path))
        if path == root or root not in path.parents:
            raise ValueError("replay_formal_live_shadow_inputs_unverified")
        verified[module] = str(path)
    if len(set(verified.values())) != len(verified):
        raise ValueError("replay_formal_live_shadow_inputs_unverified")
    return verified


def _stage6_artifact_radar_run_id(path: Path) -> str:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > 8 * 1024 * 1024:
            raise ValueError
        payload = strict_json_loads(
            raw,
            error_code="replay_formal_live_stage6_artifact_unverified",
        )
        prepared = payload.get("prepared") if isinstance(payload, dict) else None
        if not isinstance(prepared, dict):
            raise ValueError
        run_id = prepared.get("radarRunId")
        if (
            prepared.get("contractId")
            != "radar-leader-phase6-prepared-historical-inputs-v1"
            or not isinstance(run_id, str)
            or not run_id
        ):
            raise ValueError
        return run_id
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(
            "replay_formal_live_stage6_artifact_unverified"
        ) from exc


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    now_provider: Callable[[], datetime] = _now,
    day_kind_provider: Callable[[date], str] = _official_day_kind,
    planner: Callable[..., object] = plan_formal_cohort_collection,
    stage6_runner: Callable[..., int] = run_stage6_five_source_cli,
    formal_runner: Callable[..., object] = prepare_formal_cohort,
) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.confirm_live_formal_sequence:
        _print({"status": "rejected", "reason": "confirmation_required"}, stdout)
        return 2
    stage6_status = None
    stage6_artifact = None
    formal_shadow_input_dirs = None
    stage6_radar_run_id = None
    etf_research_evidence_sha256 = None
    try:
        formal_etfs = _normalize_formal_etf_symbols(arguments.formal_etf)
        (
            campaign,
            stage6_output,
            cohort_output,
            cninfo_pdf_cache,
            formal_shadow_input_root,
        ) = _validate_dirs(
            campaign_dir=arguments.campaign_dir,
            stage6_output_dir=arguments.stage6_output_dir,
            cohort_output_dir=arguments.cohort_output_dir,
            cninfo_pdf_cache_dir=arguments.cninfo_pdf_cache_dir,
            formal_shadow_input_root=arguments.formal_shadow_input_root,
        )
        started_at = now_provider()
        if (
            not isinstance(started_at, datetime)
            or started_at.tzinfo is None
            or started_at.utcoffset() is None
        ):
            raise ValueError("replay_formal_live_clock_unverified")
        sequence_lock = CrossProcessFileLock(
            campaign / FORMAL_LIVE_SEQUENCE_LOCK_NAME
        )
        if not sequence_lock.acquire(blocking=False):
            raise ValueError("replay_formal_live_sequence_locked")
        try:
            plan = planner(
                campaign_dir=campaign,
                now=started_at,
                day_kind_provider=day_kind_provider,
            )
            stage6_argv = [
                "--confirm-live-five-source",
                "--output-dir", str(stage6_output),
            ]
            if arguments.initialize_sector_state:
                stage6_argv.append("--initialize-sector-state")
            else:
                stage6_argv.extend([
                    "--previous-sector-state",
                    str(arguments.previous_sector_state),
                ])
            if formal_shadow_input_root is not None:
                stage6_argv.extend([
                    "--formal-shadow-input-root",
                    str(formal_shadow_input_root),
                ])
            existing_stage6_artifacts = {
                path.resolve()
                for path in stage6_output.glob(
                    "stage6-live-five-source-*.json"
                )
                if path.is_file()
            }
            stage6_stdout = StringIO()
            stage6_code = stage6_runner(
                stage6_argv,
                stdout=stage6_stdout,
                now_provider=now_provider,
            )
            stage6_payload = strict_json_loads(
                stage6_stdout.getvalue(),
                error_code="replay_formal_live_stage6_result_unverified",
            )
            if not isinstance(stage6_payload, dict):
                raise ValueError("replay_formal_live_stage6_result_unverified")
            stage6_status = stage6_payload.get("status")
            if stage6_code != 0:
                stage6_reason = stage6_payload.get("reason")
                if not isinstance(stage6_reason, str) or not stage6_reason:
                    stage6_reasons = stage6_payload.get("reasons")
                    stage6_reason = (
                        stage6_reasons[0]
                        if isinstance(stage6_reasons, list)
                        and stage6_reasons
                        and isinstance(stage6_reasons[0], str)
                        and stage6_reasons[0]
                        else "stage6_not_ready"
                    )
                failure = {
                    "status": "stage6_not_ready",
                    "stage6Status": stage6_status,
                    "reason": stage6_reason,
                }
                partial_dirs = stage6_payload.get("formalShadowInputDirs")
                if (
                    formal_shadow_input_root is not None
                    and isinstance(partial_dirs, dict)
                    and partial_dirs
                ):
                    stage6_artifact = _private_tmp_child(
                        Path(stage6_payload["artifactPath"])
                    )
                    if (
                        not stage6_artifact.is_file()
                        or stage6_artifact.parent != stage6_output
                        or not stage6_artifact.name.startswith(
                            "stage6-live-five-source-"
                        )
                        or stage6_artifact.suffix != ".json"
                        or stage6_artifact in existing_stage6_artifacts
                    ):
                        raise ValueError(
                            "replay_formal_live_stage6_artifact_not_new"
                        )
                    formal_shadow_input_dirs = (
                        _stage6_formal_shadow_input_dirs(
                            partial_dirs,
                            root=formal_shadow_input_root,
                            allow_partial=True,
                        )
                    )
                    stage6_radar_run_id = _stage6_artifact_radar_run_id(
                        stage6_artifact
                    )
                    failure.update({
                        "stage6ArtifactPath": str(stage6_artifact),
                        "radarRunId": stage6_radar_run_id,
                        "formalShadowInputDirs": formal_shadow_input_dirs,
                    })
                _print(failure, stdout)
                return 2
            if stage6_status not in {"ready_for_review", "empty"}:
                raise ValueError("replay_formal_live_stage6_result_unverified")
            if formal_shadow_input_root is not None:
                formal_shadow_input_dirs = _stage6_formal_shadow_input_dirs(
                    stage6_payload.get("formalShadowInputDirs"),
                    root=formal_shadow_input_root,
                )
            stage6_artifact = _private_tmp_child(
                Path(stage6_payload["artifactPath"])
            )
            if (
                not stage6_artifact.is_file()
                or stage6_artifact.parent != stage6_output
                or not stage6_artifact.name.startswith(
                    "stage6-live-five-source-"
                )
                or stage6_artifact.suffix != ".json"
                or stage6_artifact in existing_stage6_artifacts
            ):
                raise ValueError("replay_formal_live_stage6_artifact_not_new")
            prepared_summary = stage6_payload.get("prepared")
            if isinstance(prepared_summary, dict):
                candidate_run_id = prepared_summary.get("radarRunId")
                if isinstance(candidate_run_id, str) and candidate_run_id:
                    stage6_radar_run_id = candidate_run_id
            if formal_shadow_input_dirs is not None:
                artifact_run_id = _stage6_artifact_radar_run_id(
                    stage6_artifact
                )
                if (
                    stage6_radar_run_id is not None
                    and stage6_radar_run_id != artifact_run_id
                ):
                    raise ValueError(
                        "replay_formal_live_stage6_artifact_unverified"
                    )
                stage6_radar_run_id = artifact_run_id
            result = formal_runner(
                confirm_live_cohort=True,
                campaign_dir=campaign,
                output_dir=cohort_output,
                stage6_artifact_path=stage6_artifact,
                formal_etf_symbols=formal_etfs,
                cninfo_pdf_cache_dir=cninfo_pdf_cache,
                clock=now_provider,
                day_kind_provider=day_kind_provider,
            )
            if result.plan.role != plan.role:
                raise ValueError("replay_formal_live_plan_changed")
            if formal_runner is prepare_formal_cohort:
                published = publish_replay_etf_research(
                    result.output.bundle,
                    DEFAULT_RADAR_REPLAY_STORE_DIR,
                )
                etf_research_evidence_sha256 = published.evidence_sha256
        finally:
            sequence_lock.release()
    except Exception as exc:
        failure = {
            "status": "failed",
            "errorType": type(exc).__name__,
            "reason": str(exc),
        }
        if (
            stage6_status in {"ready_for_review", "empty"}
            and isinstance(stage6_artifact, Path)
            and formal_shadow_input_dirs is not None
            and stage6_radar_run_id is not None
        ):
            failure.update({
                "stage6Status": stage6_status,
                "stage6ArtifactPath": str(stage6_artifact),
                "radarRunId": stage6_radar_run_id,
                "formalShadowInputDirs": formal_shadow_input_dirs,
            })
        _print(failure, stdout)
        return 1

    sample = result.baseline.replay.samples[0]
    payload = {
        "status": "registered",
        "stage6Status": stage6_status,
        "stage6ArtifactPath": str(stage6_artifact),
        "role": result.plan.role,
        "sampleId": sample.sample_id,
        "radarRunId": sample.radar_run_id,
        "sampleAsOf": str(sample.as_of),
        "campaignId": result.campaign_state.campaign_id,
        "campaignRevision": result.campaign_state.revision,
        "cohortCount": len(result.campaign_state.cohorts),
        "manifestPath": str(result.manifest_path),
        "etfResearchEvidenceSha256": etf_research_evidence_sha256,
    }
    if formal_shadow_input_root is not None:
        etf_path = getattr(result.baseline, "etf_formal_admission_path", None)
        if not isinstance(etf_path, Path):
            raise ValueError("replay_formal_live_etf_admission_unverified")
        try:
            relative_etf_path = etf_path.resolve().relative_to(cohort_output)
        except ValueError as exc:
            raise ValueError(
                "replay_formal_live_etf_admission_unverified"
            ) from exc
        payload["formalShadowInputDirs"] = formal_shadow_input_dirs
        payload["etfFormalAdmissionPath"] = str(relative_etf_path)
        manifest_path = _private_tmp_child(Path(result.manifest_path))
        try:
            relative_manifest_path = manifest_path.relative_to(cohort_output)
            private_manifest_path = manifest_path.relative_to(Path("/private/tmp"))
            manifest_bytes = manifest_path.read_bytes()
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(
                "replay_formal_live_manifest_unverified"
            ) from exc
        if not manifest_bytes or len(manifest_bytes) > 1024 * 1024:
            raise ValueError("replay_formal_live_manifest_unverified")
        payload["manifestPath"] = str(relative_manifest_path)
        payload["manifestRelativePath"] = str(private_manifest_path)
        payload["manifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    _print(payload, stdout)
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
