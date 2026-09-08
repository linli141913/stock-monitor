"""阶段10单次现场采集的显式命令行入口。

命令行本身只负责严格预检和输出；真正的阶段9、台账和 ETF 调用由部署前
再绑定的调用方传入 ``run_cli``，因此该暗入口不会自行启动网络采集。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from market_calendar import get_calendar_day_kind
from radar.formal_shadow_input_bundle import (
    CALENDAR_DOCUMENT_FILENAME,
    CALENDAR_INPUT_FILENAME,
    COLLECTION_POLICY_FILENAME,
)
from radar.etf_formal_admission import (
    EtfFormalAdmissionStatus,
    load_etf_formal_admission_bundle,
)
from radar.replay_forward_baseline import collect_forward_replay_baseline
from radar.strict_json import strict_json_loads
from radar.stage10_live_collection import (
    PRIVATE_TMP,
    Stage10Stage9Result,
    _empty_result,
    _resolve_stage10_schedule_slot,
    _schedule_calendar_is_verified,
    _stage10_preflight,
    run_stage10_live_collection,
)
from run_radar_etf_live_shadow_capture import (
    EtfCaptureCliHooks,
    run_cli as run_etf_cli,
)
from run_radar_formal_shadow_observation import run as run_shadow_observation
from run_radar_replay_formal_live_acceptance import run_cli as run_stage9_cli


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _calendar(day):
    return get_calendar_day_kind("cn", day).kind


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="阶段10单次现场采集协调入口")
    parser.add_argument("--confirm-live-stage10-collection", action="store_true")
    parser.add_argument("--attempt-root", required=True, type=Path)
    parser.add_argument("--attempt-id")
    parser.add_argument("--schedule-store-root", type=Path)
    parser.add_argument("--schedule-manifest-sha256")
    parser.add_argument("--slot-id")
    parser.add_argument("--attempt-universe-root", type=Path)
    parser.add_argument("--campaign-dir", type=Path)
    parser.add_argument("--stage6-output-dir", type=Path)
    parser.add_argument("--cohort-output-dir", type=Path)
    parser.add_argument("--formal-shadow-input-root", type=Path)
    lineage = parser.add_mutually_exclusive_group()
    lineage.add_argument("--initialize-sector-state", action="store_true")
    lineage.add_argument("--previous-sector-state", type=Path)
    parser.add_argument("--cninfo-pdf-cache-dir", type=Path)
    parser.add_argument("--formal-etf", action="append", default=[])
    parser.add_argument("--ledger-dir", type=Path)
    parser.add_argument("--etf-output-root", type=Path)
    return parser


def _default_bindings(arguments, *, now, local_calendar_verifier):
    required = (
        arguments.campaign_dir,
        arguments.stage6_output_dir,
        arguments.cohort_output_dir,
        arguments.formal_shadow_input_root,
        arguments.ledger_dir,
        arguments.etf_output_root,
    )
    if (
        any(value is None for value in required)
        or arguments.schedule_store_root is None
        or arguments.schedule_manifest_sha256 is None
        or arguments.slot_id is None
        or arguments.attempt_universe_root is None
        or bool(arguments.initialize_sector_state)
        == bool(arguments.previous_sector_state)
        or not arguments.formal_etf
    ):
        raise ValueError("stage10_live_collection_bindings_unverified")

    def stage9():
        argv = [
            "--confirm-live-formal-sequence",
            "--campaign-dir", str(arguments.campaign_dir),
            "--stage6-output-dir", str(arguments.stage6_output_dir),
            "--cohort-output-dir", str(arguments.cohort_output_dir),
            "--formal-shadow-input-root", str(arguments.formal_shadow_input_root),
        ]
        if arguments.initialize_sector_state:
            argv.append("--initialize-sector-state")
        else:
            argv.extend(["--previous-sector-state", str(arguments.previous_sector_state)])
        if arguments.cninfo_pdf_cache_dir is not None:
            argv.extend(["--cninfo-pdf-cache-dir", str(arguments.cninfo_pdf_cache_dir)])
        for symbol in arguments.formal_etf:
            argv.extend(["--formal-etf", symbol])
        output = StringIO()
        code = run_stage9_cli(
            argv,
            stdout=output,
            now_provider=now,
            day_kind_provider=local_calendar_verifier,
        )
        payload = strict_json_loads(
            output.getvalue(),
            error_code="stage10_live_collection_stage9_unverified",
        )
        if not isinstance(payload, dict):
            raise ValueError("stage10_live_collection_stage9_unverified")
        if code != 0 and payload.get("status") == "registered":
            raise ValueError("stage10_live_collection_stage9_unverified")
        return payload

    def register(module_name, input_dir):
        result = run_shadow_observation(
            module_name,
            input_dir,
            arguments.ledger_dir,
            evaluated_at=now(),
        )
        return {
            "module": module_name,
            "status": result.status,
            "contentSha256": result.content_sha256,
            "ledgerRelativePath": result.relative_path,
        }

    def etf(stage9_result: Stage10Stage9Result):
        if (
            stage9_result.radar_run_id is None
            or stage9_result.trend_input_relative_path is None
        ):
            raise ValueError("stage10_live_collection_etf_inputs_unverified")
        trend_input = PRIVATE_TMP / stage9_result.trend_input_relative_path
        if stage9_result.etf_formal_admission_path is not None:
            admission = (
                arguments.cohort_output_dir
                / stage9_result.etf_formal_admission_path
            )
        else:
            independent_root = Path(
                str(arguments.etf_output_root) + "-admission"
            )
            baseline = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=independent_root,
                sample_role="development",
                cninfo_pdf_cache_dir=arguments.cninfo_pdf_cache_dir,
                radar_run_id=stage9_result.radar_run_id,
                formal_etf_symbols=tuple(sorted(arguments.formal_etf)),
                clock=now,
            )
            admission = baseline.etf_formal_admission_path
            if admission is None:
                return {
                    "status": "skipped",
                    "reason": "stage10_etf_admission_unavailable",
                }
            admission_payload = strict_json_loads(
                admission.read_text(encoding="utf-8"),
                error_code="stage10_etf_admission_unverified",
            )
            if not isinstance(admission_payload, Mapping):
                raise ValueError("stage10_etf_admission_unverified")
            bundle = load_etf_formal_admission_bundle(admission_payload)
            if (
                bundle.radar_run_id != stage9_result.radar_run_id
                or tuple(item.symbol for item in bundle.admissions)
                != tuple(sorted(arguments.formal_etf))
            ):
                raise ValueError("stage10_etf_admission_identity_mismatch")
            if any(
                item.monitoring_status is not EtfFormalAdmissionStatus.READY
                for item in bundle.admissions
            ):
                return {
                    "status": "skipped",
                    "reason": "stage10_etf_admission_not_ready",
                }
        argv = [
            "--confirm-live-etf-shadow",
            "--symbols", *sorted(arguments.formal_etf),
            "--run-id", stage9_result.radar_run_id,
            "--admission-file", str(admission),
            "--calendar-envelope-file", str(trend_input / CALENDAR_INPUT_FILENAME),
            "--calendar-document-file", str(trend_input / CALENDAR_DOCUMENT_FILENAME),
            "--collection-policy-file", str(trend_input / COLLECTION_POLICY_FILENAME),
            "--output-root", str(arguments.etf_output_root),
            "--ledger-dir", str(arguments.ledger_dir),
        ]
        published = {}

        def register_inside(module_name, input_dir, ledger_dir, *, evaluated_at):
            published["inputPath"] = str(input_dir)
            return run_shadow_observation(
                module_name,
                input_dir,
                ledger_dir,
                evaluated_at=evaluated_at,
            )

        try:
            result = dict(run_etf_cli(
                argv,
                hooks=EtfCaptureCliHooks(register=register_inside, clock=now),
            ))
        except Exception:
            if "inputPath" in published:
                return {
                    "status": "failed",
                    "reason": "stage10_etf_registration_failed",
                    "inputPath": published["inputPath"],
                }
            raise
        if "inputPath" in published:
            result["inputPath"] = published["inputPath"]
        return result

    return stage9, register, etf


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout=sys.stdout,
    now: Callable[[], datetime] = _now,
    local_calendar_verifier: Callable[..., Optional[str]] = _calendar,
    stage9_runner: Optional[Callable[[], Mapping[str, Any]]] = None,
    shadow_registrar: Optional[Callable[..., Mapping[str, Any]]] = None,
    etf_runner: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        observed_at = now()
        resolved_attempt_id, _, _, dry = _stage10_preflight(
            confirmed=arguments.confirm_live_stage10_collection,
            attempt_root=arguments.attempt_root,
            attempt_id=arguments.attempt_id,
            observed_at=observed_at,
        )
    except (OSError, TypeError, ValueError):
        stdout.write(json.dumps({
            "status": "failed",
            "reason": "stage10_live_collection_failed",
        }) + "\n")
        return 2
    if dry is not None:
        stdout.write(json.dumps(
            dry.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
        ) + "\n")
        return 2
    if any(value is None for value in (
        arguments.schedule_store_root,
        arguments.schedule_manifest_sha256,
        arguments.slot_id,
    )):
        stdout.write(json.dumps({
            "status": "failed",
            "reason": "stage10_live_collection_bindings_unverified",
        }) + "\n")
        return 2
    try:
        schedule, selected_slot = _resolve_stage10_schedule_slot(
            store_root=arguments.schedule_store_root,
            manifest_sha256=arguments.schedule_manifest_sha256,
            slot_id=arguments.slot_id,
            observed_at=observed_at,
        )
        calendar_verified = _schedule_calendar_is_verified(
            schedule, local_calendar_verifier
        )
    except (OSError, TypeError, ValueError):
        stdout.write(json.dumps({
            "status": "failed",
            "reason": "stage10_frozen_schedule_unverified",
        }) + "\n")
        return 2
    if not calendar_verified:
        calendar_dry = _empty_result(
            attempt_id=resolved_attempt_id,
            now=selected_slot.schedule_slot,
            status="dry_calendar_unverified",
        )
        stdout.write(json.dumps(
            calendar_dry.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
        ) + "\n")
        return 2
    if stage9_runner is None or shadow_registrar is None or etf_runner is None:
        if arguments.confirm_live_stage10_collection:
            try:
                defaults = _default_bindings(
                    arguments,
                    now=now,
                    local_calendar_verifier=local_calendar_verifier,
                )
            except (TypeError, ValueError):
                stdout.write(json.dumps({
                    "status": "failed",
                    "reason": "stage10_live_collection_bindings_unverified",
                }) + "\n")
                return 2
            stage9_runner = stage9_runner or defaults[0]
            shadow_registrar = shadow_registrar or defaults[1]
            etf_runner = etf_runner or defaults[2]
        else:
            stage9_runner = stage9_runner or (lambda: (_ for _ in ()).throw(RuntimeError()))
            shadow_registrar = shadow_registrar or (lambda *_: (_ for _ in ()).throw(RuntimeError()))
            etf_runner = etf_runner or (lambda *_: (_ for _ in ()).throw(RuntimeError()))
    try:
        result = run_stage10_live_collection(
            confirmed=arguments.confirm_live_stage10_collection,
            attempt_root=arguments.attempt_root,
            attempt_id=arguments.attempt_id,
            schedule_store_root=arguments.schedule_store_root,
            schedule_manifest_sha256=arguments.schedule_manifest_sha256,
            slot_id=arguments.slot_id,
            attempt_universe_root=arguments.attempt_universe_root,
            now=now,
            local_calendar_verifier=lambda _day: "full",
            stage9_runner=stage9_runner,
            shadow_registrar=shadow_registrar,
            etf_runner=etf_runner,
        )
    except (OSError, TypeError, ValueError):
        stdout.write(json.dumps({"status": "failed", "reason": "stage10_live_collection_failed"}) + "\n")
        return 2
    stdout.write(json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n")
    successful = (
        result.preflight.status == "ready"
        and result.stage9.status == "available"
        and all(item.status in {"available", "unchanged"} for item in (
            result.trend_rotation,
            result.leader_observation,
            result.etf_observation,
        ))
    )
    return 0 if successful else 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
