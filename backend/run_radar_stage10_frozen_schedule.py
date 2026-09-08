"""从固定离线输入发布阶段10冻结调度清单的显式 CLI。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Literal, Optional, Sequence, TextIO, Tuple

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from radar.formal_shadow_calendar import (
    FormalShadowCalendarEvidence,
    OfficialSseCalendarProvider,
    load_official_sse_calendar_evidence,
)
from radar.formal_shadow_ledger_store import _open_root_directory, _verify_root_path
from radar.stage10_live_collection import (
    Stage10FrozenSchedule,
    Stage10FrozenScheduleSlot,
    Stage10ScheduleSourceEvidence,
    _private_tmp_child,
    _read_regular_at,
    load_stage10_frozen_schedule,
    publish_stage10_frozen_schedule,
)
from radar.strict_json import strict_json_loads


INPUT_FILENAME = "schedule-input.json"
INPUT_CONTRACT_VERSION = "radar-stage10-frozen-schedule-input-v1"
MAX_INPUT_BYTES = 1024 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _FrozenInputModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class FrozenScheduleCalendarSource(_FrozenInputModel):
    year: StrictInt = Field(ge=2000, le=2100)
    envelope_file: str = Field(alias="envelopeFile")
    envelope_sha256: str = Field(alias="envelopeSha256", pattern=r"^[0-9a-f]{64}$")
    source_document_file: str = Field(alias="sourceDocumentFile")
    source_document_sha256: str = Field(
        alias="sourceDocumentSha256", pattern=r"^[0-9a-f]{64}$"
    )

    @field_validator("envelope_file", "source_document_file")
    @classmethod
    def filename_is_direct_and_safe(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or value in {".", "..", INPUT_FILENAME}
            or _SAFE_FILENAME.fullmatch(value) is None
        ):
            raise ValueError("stage10_frozen_schedule_input_path_unverified")
        return value

    @model_validator(mode="after")
    def files_are_distinct(self) -> "FrozenScheduleCalendarSource":
        if self.envelope_file == self.source_document_file:
            raise ValueError("stage10_frozen_schedule_input_path_unverified")
        return self


class FrozenScheduleInput(_FrozenInputModel):
    contract_version: Literal[INPUT_CONTRACT_VERSION] = Field(alias="contractVersion")
    schedule_id: str = Field(alias="scheduleId", pattern=r"^[A-Za-z0-9._-]{1,64}$")
    policy_version: Literal["radar-stage10-schedule-policy-v1"] = Field(
        alias="policyVersion"
    )
    evidence_scope: Literal["live"] = Field(alias="evidenceScope")
    official_market: Literal["cn"] = Field(alias="officialMarket")
    calendar_sources: Tuple[FrozenScheduleCalendarSource, ...] = Field(
        alias="calendarSources", min_length=1
    )
    slots: Tuple[Stage10FrozenScheduleSlot, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def source_and_slot_order_is_frozen(self) -> "FrozenScheduleInput":
        years = tuple(item.year for item in self.calendar_sources)
        files = tuple(
            name
            for item in self.calendar_sources
            for name in (item.envelope_file, item.source_document_file)
        )
        slot_times = tuple(item.schedule_slot for item in self.slots)
        if (
            tuple(sorted(years)) != years
            or len(years) != len(set(years))
            or len(files) != len(set(files))
            or tuple(sorted(slot_times)) != slot_times
            or set(years) != {item.trade_date.year for item in self.slots}
        ):
            raise ValueError("stage10_frozen_schedule_input_unverified")
        return self


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="发布阶段10冻结调度清单")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--output-store-root", required=True, type=Path)
    return parser


def _print(payload: object, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    stdout.write("\n")


def _validated_roots(input_dir: Path, output_store_root: Path) -> tuple[Path, Path]:
    source = _private_tmp_child(input_dir)
    output = _private_tmp_child(output_store_root)
    if (
        source == output
        or source in output.parents
        or output in source.parents
    ):
        raise ValueError("stage10_frozen_schedule_paths_overlap")
    return source, output


def _load_input(
    input_dir: Path,
    expected_sha256: str,
) -> tuple[FrozenScheduleInput, tuple[FormalShadowCalendarEvidence, ...]]:
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("stage10_frozen_schedule_input_unverified")
    root_fd: Optional[int] = None
    try:
        root_fd = _open_root_directory(input_dir, create_missing=False)
        _verify_root_path(input_dir, root_fd)
        if stat.S_IMODE(os.fstat(root_fd).st_mode) != 0o700:
            raise ValueError("stage10_frozen_schedule_input_unverified")
        raw = _read_regular_at(root_fd, INPUT_FILENAME, maximum=MAX_INPUT_BYTES)
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("stage10_frozen_schedule_input_unverified")
        payload = strict_json_loads(
            raw,
            error_code="stage10_frozen_schedule_input_unverified",
        )
        if not isinstance(payload, dict):
            raise ValueError("stage10_frozen_schedule_input_unverified")
        schedule_input = FrozenScheduleInput.model_validate(payload)
        evidence = []
        for source in schedule_input.calendar_sources:
            envelope = _read_regular_at(
                root_fd, source.envelope_file, maximum=MAX_ENVELOPE_BYTES
            )
            document = _read_regular_at(
                root_fd,
                source.source_document_file,
                maximum=MAX_DOCUMENT_BYTES,
            )
            if (
                hashlib.sha256(envelope).hexdigest() != source.envelope_sha256
                or hashlib.sha256(document).hexdigest()
                != source.source_document_sha256
            ):
                raise ValueError("stage10_frozen_schedule_input_unverified")
            verified = load_official_sse_calendar_evidence(envelope, document)
            if (
                verified.year != source.year
                or verified.source_document_sha256
                != source.source_document_sha256
            ):
                raise ValueError("stage10_frozen_schedule_input_unverified")
            evidence.append(verified)
        _verify_root_path(input_dir, root_fd)
        return schedule_input, tuple(evidence)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_frozen_schedule_input_unverified") from exc
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _build_schedule(
    schedule_input: FrozenScheduleInput,
    evidence: tuple[FormalShadowCalendarEvidence, ...],
) -> Stage10FrozenSchedule:
    provider = OfficialSseCalendarProvider(evidence)
    trading_dates = tuple(item.trade_date for item in schedule_input.slots)
    if any(provider.is_trading_day(day) is not True for day in trading_dates):
        raise ValueError("stage10_frozen_schedule_calendar_unverified")
    return Stage10FrozenSchedule(
        contractVersion="radar-stage10-schedule-manifest-v1",
        scheduleId=schedule_input.schedule_id,
        policyVersion=schedule_input.policy_version,
        evidenceScope=schedule_input.evidence_scope,
        officialMarket=schedule_input.official_market,
        sourceEvidence=tuple(
            Stage10ScheduleSourceEvidence(
                year=item.year,
                sourceDocumentSha256=item.source_document_sha256,
                observedThrough=item.observed_through,
            )
            for item in evidence
        ),
        officialTradingDates=trading_dates,
        slots=schedule_input.slots,
    )


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        input_dir, output_root = _validated_roots(
            arguments.input_dir, arguments.output_store_root
        )
        schedule_input, evidence = _load_input(
            input_dir, arguments.input_sha256
        )
        schedule = _build_schedule(schedule_input, evidence)
        manifest_sha = publish_stage10_frozen_schedule(output_root, schedule)
        if load_stage10_frozen_schedule(output_root, manifest_sha) != schedule:
            raise ValueError("stage10_frozen_schedule_replay_unverified")
    except (OSError, TypeError, ValueError):
        _print({
            "status": "failed",
            "reason": "stage10_frozen_schedule_input_unverified",
        }, stdout)
        return 2
    slot_ids = [item.slot_id for item in schedule.slots]
    payload = {
        "status": "published",
        "manifestSha256": manifest_sha,
        "slotIds": slot_ids,
    }
    if len(slot_ids) == 1:
        payload["slotId"] = slot_ids[0]
    _print(payload, stdout)
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
