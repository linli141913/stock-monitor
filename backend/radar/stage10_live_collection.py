"""阶段10单次现场采集的协调器。

该模块是显式调用的薄协调层：它不读取 SQLite、不联网，也不创建正式
executor。阶段9、两个阶段6影子登记和 ETF 采集均由调用方注入；每一个
已完成的独立事实都会立即写进仅含相对引用的检查点，后续恢复只重放尚未
登记的离线输入，绝不回滚之前已经完成的段。
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from radar.formal_shadow_ledger_store import (
    _explicit_root,
    _open_root_directory,
    _verify_directory_entry,
    _verify_root_path,
)
from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
from radar.formal_shadow_input_bundle import (
    CALENDAR_DOCUMENT_FILENAME,
    CALENDAR_INPUT_FILENAME,
    COLLECTION_POLICY_FILENAME,
    ETF_ADMISSION_FILENAME,
    RECEIPT_FILENAME,
    SOURCE_ARTIFACT_FILENAME,
    TREND_SUPPORTING_FILENAME,
)
from radar.formal_shadow_observation_collector import (
    FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID,
    MODULE_SOURCE_CONTRACTS,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
PRIVATE_TMP = Path("/private/tmp")
CHECKPOINT_POINTER_NAME = "latest.json"
CHECKPOINT_STORE_NAME = "checkpoints"
GLOBAL_COLLECTION_LOCK_ROOT_NAME = (
    ".radar-stage10-single-live-collection-lock-root"
)
GLOBAL_COLLECTION_LOCK_NAME = "collection.lock"
ATTEMPT_UNIVERSE_LOCK_ROOT_NAME = ".radar-stage10-attempt-universe-lock-root"
ATTEMPT_UNIVERSE_LOCK_NAME = "universe.lock"
SCHEDULE_STORE_LOCK_ROOT_NAME = ".radar-stage10-schedule-store-lock-root"
SCHEDULE_STORE_LOCK_NAME = "schedule.lock"
PER_ATTEMPT_LOCK_ROOT_PREFIX = ".radar-stage10-per-attempt-lock-"
PER_ATTEMPT_LOCK_NAME = "attempt.lock"
ATTEMPT_OWNER_NAME = ".attempt-owner.json"
CHECKPOINT_CONTRACT_ID = "radar-stage10-live-collection-attempt-v1"
RESULT_CONTRACT_ID = "radar-stage10-single-live-collection-v1"
ATTEMPT_UNIVERSE_ID = "stage10-live-attempt-universe-v1"
_MODULES = ("trendRotation", "leaderObservation", "etfObservation")
_SHA256_LENGTH = 64
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SCHEDULE_MODULES = (
    "stage9", "trendRotation", "leaderObservation", "etfObservation",
)


def _global_collection_lock(path: Path) -> PrivateTmpNoFollowFileLock:
    return PrivateTmpNoFollowFileLock(
        path,
        protect_root=True,
        create_root=True,
    )


def _assert_collection_lock(lock: Any) -> None:
    try:
        checker = getattr(lock, "assert_still_held")
        checker()
    except Exception as error:
        raise ValueError(
            "stage10_live_collection_lock_unverified"
        ) from error


def _per_attempt_lock(path: Path) -> PrivateTmpNoFollowFileLock:
    identity = hashlib.sha256(_private_relative(path).encode("utf-8")).hexdigest()
    return _global_collection_lock(
        PRIVATE_TMP
        / f"{PER_ATTEMPT_LOCK_ROOT_PREFIX}{identity}"
        / PER_ATTEMPT_LOCK_NAME
    )


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        frozen=True,
        validate_default=True,
    )


class Stage10FrozenScheduleSlot(_FrozenStrictModel):
    slot_id: str = Field(
        alias="slotId", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    trade_date: date = Field(alias="tradeDate")
    schedule_slot: datetime = Field(alias="scheduleSlot")
    effective_from: datetime = Field(alias="effectiveFrom")
    effective_until: datetime = Field(alias="effectiveUntil")
    module_scope: Tuple[
        Literal[
            "stage9", "trendRotation", "leaderObservation", "etfObservation",
        ],
        ...,
    ] = Field(alias="moduleScope")

    @field_validator("schedule_slot", "effective_from", "effective_until")
    @classmethod
    def time_is_aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def slot_is_exact(self) -> "Stage10FrozenScheduleSlot":
        if (
            self.slot_id in {".", ".."}
            or self.schedule_slot.date() != self.trade_date
            or not _continuous_session(self.schedule_slot)
            or not (
                self.effective_from
                <= self.schedule_slot
                < self.effective_until
            )
            or self.effective_from.date() != self.trade_date
            or self.effective_until.date() != self.trade_date
            or self.module_scope != _SCHEDULE_MODULES
        ):
            raise ValueError("stage10_frozen_schedule_slot_unverified")
        return self


class Stage10ScheduleSourceEvidence(_FrozenStrictModel):
    year: StrictInt = Field(ge=2000, le=2100)
    source_document_sha256: str = Field(
        alias="sourceDocumentSha256", pattern=r"^[0-9a-f]{64}$"
    )
    observed_through: date = Field(alias="observedThrough")

    @model_validator(mode="after")
    def source_matches_year(self) -> "Stage10ScheduleSourceEvidence":
        if self.observed_through.year != self.year:
            raise ValueError("stage10_frozen_schedule_source_unverified")
        return self


class Stage10FrozenSchedule(_FrozenStrictModel):
    contract_version: Literal["radar-stage10-schedule-manifest-v1"] = Field(
        alias="contractVersion"
    )
    schedule_id: str = Field(
        alias="scheduleId", pattern=r"^[A-Za-z0-9._-]{1,64}$"
    )
    evidence_scope: Literal["live"] = Field(alias="evidenceScope")
    official_market: Literal["cn"] = Field(alias="officialMarket")
    policy_version: Literal["radar-stage10-schedule-policy-v1"] = Field(
        alias="policyVersion"
    )
    source_evidence: Tuple[Stage10ScheduleSourceEvidence, ...] = Field(
        alias="sourceEvidence", min_length=1
    )
    official_trading_dates: Tuple[date, ...] = Field(
        alias="officialTradingDates", min_length=1
    )
    slots: Tuple[Stage10FrozenScheduleSlot, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def schedule_is_exact(self) -> "Stage10FrozenSchedule":
        slot_ids = tuple(item.slot_id for item in self.slots)
        slot_times = tuple(item.schedule_slot for item in self.slots)
        slot_dates = tuple(item.trade_date for item in self.slots)
        dates = tuple(self.official_trading_dates)
        sources = tuple(self.source_evidence)
        source_years = tuple(item.year for item in sources)
        source_by_year = {item.year: item for item in sources}
        if (
            self.schedule_id in {".", ".."}
            or len(dates) != len(set(dates))
            or tuple(sorted(dates)) != dates
            or len(slot_ids) != len(set(slot_ids))
            or len(slot_times) != len(set(slot_times))
            or len(slot_dates) != len(set(slot_dates))
            or any(item.trade_date not in dates for item in self.slots)
            or len(source_years) != len(set(source_years))
            or tuple(sorted(source_years)) != source_years
            or set(source_years) != {day.year for day in dates}
            or any(
                day.year not in source_by_year
                or day > source_by_year[day.year].observed_through
                for day in dates
            )
        ):
            raise ValueError("stage10_frozen_schedule_unverified")
        return self


class Stage10Preflight(_StrictModel):
    status: Literal[
        "dry_confirmation_required",
        "dry_not_trading",
        "dry_calendar_unverified",
        "dry_not_continuous_session",
        "dry_attempt_contended",
        "ready",
    ]
    reason: Optional[str] = None


class Stage10ModuleResult(_StrictModel):
    status: Literal[
        "not",
        "available",
        "unchanged",
        "contended",
        "failed",
        "skipped",
        "not_attempted",
    ]
    reason: Optional[str] = None
    ledger_sha256: Optional[str] = Field(default=None, alias="ledgerSha256")
    ledger_relative_path: Optional[str] = Field(
        default=None,
        alias="ledgerRelativePath",
    )
    input_relative_path: Optional[str] = Field(
        default=None,
        alias="inputRelativePath",
    )
    input_sha256: Optional[str] = Field(default=None, alias="inputSha256")
    input_contract_id: Optional[str] = Field(
        default=None,
        alias="inputContractId",
    )
    input_run_id: Optional[str] = Field(default=None, alias="inputRunId")


class Stage10Stage9Result(Stage10ModuleResult):
    role: Optional[str] = None
    sample_id: Optional[str] = Field(default=None, alias="sampleId")
    radar_run_id: Optional[str] = Field(default=None, alias="radarRunId")
    etf_formal_admission_path: Optional[str] = Field(
        default=None,
        alias="etfFormalAdmissionPath",
    )
    trend_input_relative_path: Optional[str] = Field(
        default=None,
        alias="trendInputRelativePath",
    )
    leader_input_relative_path: Optional[str] = Field(
        default=None,
        alias="leaderInputRelativePath",
    )
    trend_input_sha256: Optional[str] = Field(
        default=None,
        alias="trendInputSha256",
    )
    leader_input_sha256: Optional[str] = Field(
        default=None,
        alias="leaderInputSha256",
    )
    trend_input_contract_id: Optional[str] = Field(
        default=None,
        alias="trendInputContractId",
    )
    leader_input_contract_id: Optional[str] = Field(
        default=None,
        alias="leaderInputContractId",
    )
    manifest_relative_path: Optional[str] = Field(
        default=None,
        alias="manifestRelativePath",
    )
    manifest_sha256: Optional[str] = Field(
        default=None,
        alias="manifestSha256",
    )


class Stage10LiveCollectionResult(_StrictModel):
    contract_id: Literal[RESULT_CONTRACT_ID] = Field(
        default=RESULT_CONTRACT_ID,
        alias="contractId",
    )
    attempt_id: str = Field(alias="attemptId")
    started_at: datetime = Field(alias="startedAt")
    finished_at: datetime = Field(alias="finishedAt")
    trade_date: date = Field(alias="tradeDate")
    preflight: Stage10Preflight
    stage9: Stage10Stage9Result
    trend_rotation: Stage10ModuleResult = Field(alias="trendRotation")
    leader_observation: Stage10ModuleResult = Field(alias="leaderObservation")
    etf_observation: Stage10ModuleResult = Field(alias="etfObservation")
    effects: Tuple[str, ...] = ()
    formal_enabled: Literal[False] = Field(default=False, alias="formalEnabled")

    @field_validator("formal_enabled", mode="before")
    @classmethod
    def formal_enabled_is_literal_false(cls, value: object) -> object:
        if type(value) is not bool or value is not False:
            raise ValueError("stage10_formal_enabled_must_be_false")
        return value


def _aware(now: Any) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("stage10_live_collection_clock_unverified")
    return now.astimezone(SHANGHAI)


def _continuous_session(now: datetime) -> bool:
    value = now.timetz().replace(tzinfo=None)
    return ((value.hour, value.minute, value.second) >= (9, 30, 0)
            and (value.hour, value.minute, value.second) < (11, 30, 0)) or (
        (value.hour, value.minute, value.second) >= (13, 0, 0)
        and (value.hour, value.minute, value.second) < (14, 57, 0)
    )


def _safe_reason(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value or "/" in value or "\\" in value:
        return fallback
    return value


def _empty_result(
    *,
    attempt_id: str,
    now: datetime,
    status: str,
    reason: Optional[str] = None,
) -> Stage10LiveCollectionResult:
    not_attempted = Stage10ModuleResult(status="not_attempted")
    return Stage10LiveCollectionResult(
        attemptId=attempt_id,
        startedAt=now,
        finishedAt=now,
        tradeDate=now.date(),
        preflight=Stage10Preflight(status=status, reason=reason),
        stage9=Stage10Stage9Result(status="not_attempted"),
        trendRotation=not_attempted,
        leaderObservation=not_attempted,
        etfObservation=not_attempted,
        formalEnabled=False,
    )


def _private_tmp_child(raw: Path) -> Path:
    if not isinstance(raw, Path) or not raw.is_absolute():
        raise ValueError("stage10_live_collection_private_tmp_required")
    value = _explicit_root(raw)
    if value == PRIVATE_TMP or PRIVATE_TMP not in value.parents:
        raise ValueError("stage10_live_collection_private_tmp_required")
    return value


def _stage10_preflight(
    *,
    confirmed: StrictBool,
    attempt_root: Path,
    attempt_id: Optional[str],
    observed_at: datetime,
) -> tuple[str, datetime, datetime, Optional[Stage10LiveCollectionResult]]:
    """纯预检：在任何根、锁、真实入口绑定或写入之前完成。"""
    if type(confirmed) is not bool:
        raise ValueError("stage10_live_collection_confirmation_unverified")
    current = _aware(observed_at)
    started_at = current
    resolved_attempt_id = attempt_id or hashlib.sha256(
        (str(attempt_root) + started_at.isoformat()).encode("utf-8")
    ).hexdigest()[:24]
    if (
        not isinstance(resolved_attempt_id, str)
        or _ATTEMPT_ID.fullmatch(resolved_attempt_id) is None
        or resolved_attempt_id in {".", ".."}
    ):
        raise ValueError("stage10_live_collection_attempt_id_unverified")
    status = None
    if not confirmed:
        status = "dry_confirmation_required"
    elif current.weekday() >= 5:
        status = "dry_not_trading"
    elif not _continuous_session(current):
        status = "dry_not_continuous_session"
    dry = (
        _empty_result(
            attempt_id=resolved_attempt_id,
            now=current,
            status=status,
        )
        if status is not None
        else None
    )
    return resolved_attempt_id, current, started_at, dry


def _private_relative(path: Path) -> str:
    verified = _private_tmp_child(path)
    return str(verified.relative_to(PRIVATE_TMP))


def _from_private_relative(value: object) -> Path:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise ValueError("stage10_live_collection_checkpoint_unverified")
    path = PRIVATE_TMP / value
    if ".." in path.parts:
        raise ValueError("stage10_live_collection_checkpoint_unverified")
    return _private_tmp_child(path)


def _prepare_attempt_root(path: Path) -> None:
    root = _private_tmp_child(path)
    descriptor: Optional[int] = None
    try:
        descriptor = _open_root_directory(root, create_missing=True)
        _verify_root_path(root, descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError("stage10_live_collection_attempt_root_unverified")
        _verify_root_path(root, descriptor)
    except ValueError:
        raise
    except (OSError, TypeError) as exc:
        raise ValueError("stage10_live_collection_attempt_root_unverified") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int):
        raise ValueError("stage10_live_collection_secure_flags_unavailable")
    return os.O_RDONLY | nofollow | directory


def _open_directory_at(parent_fd: int, name: str) -> int:
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError
        return descriptor
    except (OSError, TypeError, ValueError) as exc:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise ValueError("stage10_live_collection_store_unverified") from exc


def _ensure_directory_at(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    descriptor = _open_directory_at(parent_fd, name)
    if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
        os.close(descriptor)
        raise ValueError("stage10_live_collection_store_unverified")
    return descriptor


def _read_regular_at(
    parent_fd: int,
    name: str,
    *,
    maximum: int,
    allowed_modes: tuple[int, ...] = (0o600,),
) -> bytes:
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        entry_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(entry_before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or _stable_identity(before) != _stable_identity(entry_before)
            or before.st_size > maximum
        ):
            raise ValueError
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        entry_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            len(payload) > maximum
            or _stable_identity(before) != _stable_identity(after)
            or _stable_identity(after) != _stable_identity(entry_after)
        ):
            raise ValueError
        return payload
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_live_collection_file_unverified") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_regular_snapshot_at(
    parent_fd: int,
    name: str,
    *,
    maximum: int,
) -> tuple[bytes, tuple[int, int, int, int, int]]:
    """读取文件并保留目录项身份，供跨步骤末尾复验。"""
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        raw = _read_regular_at(parent_fd, name, maximum=maximum)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or _stable_identity(before) != _stable_identity(after)
        ):
            raise ValueError
        return raw, _stable_identity(after)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_live_collection_file_unverified") from exc


def _verify_regular_snapshot_at(
    parent_fd: int,
    name: str,
    *,
    expected_raw: bytes,
    expected_identity: tuple[int, int, int, int, int],
    maximum: int,
) -> None:
    """复读同一目录项，拒绝相同内容的 inode 换件。"""
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _stable_identity(before) != expected_identity:
            raise ValueError
        raw = _read_regular_at(parent_fd, name, maximum=maximum)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            raw != expected_raw
            or _stable_identity(after) != expected_identity
        ):
            raise ValueError
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_live_collection_file_unverified") from exc


def _write_new_at(parent_fd: int, name: str, payload: bytes) -> None:
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise ValueError
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_live_collection_checkpoint_write_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _strict_object(raw: bytes, reason: str) -> Dict[str, Any]:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, dict):
        raise ValueError(reason)
    return value


def _snapshot_input(path: Path, module: str) -> tuple[str, str, str]:
    """通过固定目录FD读取完整输入包并返回内容地址和来源合同。"""
    if module not in _MODULES:
        raise ValueError("stage10_live_collection_input_reference_unverified")
    root = _private_tmp_child(path)
    directory: Optional[int] = None
    try:
        directory = _open_root_directory(root, create_missing=False)
        _verify_root_path(root, directory)
        if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
            raise ValueError
        names = [
            RECEIPT_FILENAME,
            SOURCE_ARTIFACT_FILENAME,
            COLLECTION_POLICY_FILENAME,
            CALENDAR_INPUT_FILENAME,
            CALENDAR_DOCUMENT_FILENAME,
        ]
        if module == "trendRotation":
            names.append(TREND_SUPPORTING_FILENAME)
        if module == "etfObservation":
            names.append(ETF_ADMISSION_FILENAME)
        records = []
        receipt_raw = None
        for name in names:
            maximum = 2 * 1024 * 1024 if name == CALENDAR_DOCUMENT_FILENAME else 32 * 1024 * 1024
            payload = _read_regular_at(directory, name, maximum=maximum)
            if name == RECEIPT_FILENAME:
                receipt_raw = payload
            records.append({
                "name": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            })
        _verify_root_path(root, directory)
        receipt = _strict_object(
            receipt_raw or b"",
            "stage10_live_collection_input_reference_unverified",
        )
        contract = receipt.get("sourceContractId")
        run_id = receipt.get("runId")
        if (
            receipt.get("contractId") != FORMAL_SHADOW_RUN_RECEIPT_CONTRACT_ID
            or receipt.get("module") != module
            or contract != MODULE_SOURCE_CONTRACTS[module]
            or not isinstance(run_id, str)
            or not run_id
        ):
            raise ValueError
        canonical = json.dumps(
            records,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest(), contract, run_id
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_live_collection_input_reference_unverified") from exc
    finally:
        if directory is not None:
            os.close(directory)


def _snapshot_manifest(
    *,
    relative_path: str,
    expected_sha: str,
    radar_run_id: str,
    sample_id: Optional[str],
    role: Optional[str],
) -> None:
    path = _from_private_relative(relative_path)
    parent_fd: Optional[int] = None
    try:
        parent_fd = _open_root_directory(path.parent, create_missing=False)
        _verify_root_path(path.parent, parent_fd)
        raw = _read_regular_at(
            parent_fd,
            path.name,
            maximum=1024 * 1024,
            allowed_modes=(0o600, 0o644),
        )
        _verify_root_path(path.parent, parent_fd)
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError
        manifest = _strict_object(
            raw,
            "stage10_live_collection_manifest_reference_unverified",
        )
        if (
            manifest.get("contractId") != "radar-replay-formal-cohort-run-v1"
            or manifest.get("radarRunId") != radar_run_id
            or manifest.get("sampleId") != sample_id
            or manifest.get("role") != role
        ):
            raise ValueError
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(
            "stage10_live_collection_manifest_reference_unverified"
        ) from exc
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _checkpoint_bytes(result: Stage10LiveCollectionResult) -> bytes:
    payload = {
        "contractId": CHECKPOINT_CONTRACT_ID,
        "result": result.model_dump(mode="json", by_alias=True),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_checkpoint(
    root: Path,
    result: Stage10LiveCollectionResult,
    *,
    root_fd: int,
) -> str:
    """不可变内容仓加原子指针；检查点不含任何绝对路径。"""
    encoded = _checkpoint_bytes(result)
    content_sha = hashlib.sha256(encoded).hexdigest()
    store_fd: Optional[int] = None
    temporary_name = ".latest." + secrets.token_hex(12)
    try:
        _verify_root_path(root, root_fd)
        store_fd = _ensure_directory_at(root_fd, CHECKPOINT_STORE_NAME)
        content_name = content_sha + ".json"
        try:
            _write_new_at(store_fd, content_name, encoded)
            os.fsync(store_fd)
        except ValueError:
            existing = _read_regular_at(
                store_fd,
                content_name,
                maximum=256 * 1024,
            )
            if existing != encoded:
                raise ValueError("stage10_live_collection_checkpoint_unverified")
        pointer = json.dumps({
            "contractId": "radar-stage10-live-collection-checkpoint-ref-v1",
            "contentSha256": content_sha,
            "relativePath": CHECKPOINT_STORE_NAME + "/" + content_name,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        _write_new_at(root_fd, temporary_name, pointer)
        _verify_root_path(root, root_fd)
        os.replace(
            temporary_name,
            CHECKPOINT_POINTER_NAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        os.fsync(root_fd)
        return content_sha
    except (OSError, TypeError) as exc:
        raise ValueError("stage10_live_collection_checkpoint_write_failed") from exc
    finally:
        try:
            os.unlink(temporary_name, dir_fd=root_fd)
        except OSError:
            pass
        if store_fd is not None:
            os.close(store_fd)


def _checkpoint_pointer_sha(root: Path, *, root_fd: int) -> str:
    """从固定根 FD 重读已发布指针，不从目录扫描猜测检查点。"""
    _verify_root_path(root, root_fd)
    pointer = _strict_object(
        _read_regular_at(root_fd, CHECKPOINT_POINTER_NAME, maximum=16 * 1024),
        "stage10_live_collection_checkpoint_unverified",
    )
    content_sha = pointer.get("contentSha256")
    if (
        pointer.get("contractId")
        != "radar-stage10-live-collection-checkpoint-ref-v1"
        or not isinstance(content_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", content_sha) is None
        or pointer.get("relativePath")
        != f"{CHECKPOINT_STORE_NAME}/{content_sha}.json"
    ):
        raise ValueError("stage10_live_collection_checkpoint_unverified")
    _verify_root_path(root, root_fd)
    return content_sha


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bind_attempt_owner(
    *,
    root: Path,
    root_fd: int,
    attempt_id: str,
    attempt_lock: PrivateTmpNoFollowFileLock,
) -> bool:
    """把 attempt 根永久绑定到首次 per-attempt 锁身份。

    锁路径被 rename/换 inode 后，新实例即使取得新锁也只能失败
    关闭，不得写同根检查点。
    """
    _assert_collection_lock(attempt_lock)
    identity = attempt_lock.identity
    if identity is None:
        raise ValueError("stage10_attempt_owner_unverified")
    root_identity = hashlib.sha256(
        _private_relative(root).encode("utf-8")
    ).hexdigest()
    lock_identity = hashlib.sha256(
        (
            f"{identity.root_device}:{identity.root_inode}:"
            f"{identity.file_device}:{identity.file_inode}"
        ).encode("ascii")
    ).hexdigest()
    expected = _canonical_json({
        "contractId": "radar-stage10-attempt-owner-v1",
        "attemptId": attempt_id,
        "attemptRootSha256": root_identity,
        "lockIdentitySha256": lock_identity,
    })
    try:
        actual = _read_regular_at(root_fd, ATTEMPT_OWNER_NAME, maximum=16 * 1024)
    except ValueError as exc:
        if not isinstance(exc.__cause__, FileNotFoundError):
            raise ValueError("stage10_attempt_owner_unverified") from exc
        _assert_collection_lock(attempt_lock)
        _verify_root_path(root, root_fd)
        _write_new_at(root_fd, ATTEMPT_OWNER_NAME, expected)
        os.fsync(root_fd)
        actual = _read_regular_at(root_fd, ATTEMPT_OWNER_NAME, maximum=16 * 1024)
    owner = _strict_object(actual, "stage10_attempt_owner_unverified")
    if set(owner) != {
        "contractId", "attemptId", "attemptRootSha256", "lockIdentitySha256",
    }:
        raise ValueError("stage10_attempt_owner_unverified")
    if (
        owner.get("contractId") != "radar-stage10-attempt-owner-v1"
        or owner.get("attemptId") != attempt_id
        or owner.get("attemptRootSha256") != root_identity
    ):
        raise ValueError("stage10_attempt_owner_unverified")
    _verify_root_path(root, root_fd)
    return owner.get("lockIdentitySha256") == lock_identity


def load_stage10_frozen_schedule(
    store_root: Path,
    manifest_sha256: str,
) -> Stage10FrozenSchedule:
    """按调用方给定的不可变 manifest SHA 重放调度清单。"""
    if (
        not isinstance(manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
    ):
        raise ValueError("stage10_frozen_schedule_reference_unverified")
    store = _private_tmp_child(store_root)
    root_fd: Optional[int] = None
    manifests_fd: Optional[int] = None
    try:
        root_fd = _open_root_directory(store, create_missing=False)
        _verify_root_path(store, root_fd)
        latest_raw, latest_identity = _read_regular_snapshot_at(
            root_fd, "latest.json", maximum=16 * 1024
        )
        latest = _strict_object(
            latest_raw,
            "stage10_frozen_schedule_reference_unverified",
        )
        if (
            set(latest) != {"contractId", "contentSha256", "relativePath"}
            or latest.get("contractId")
            != "radar-stage10-schedule-manifest-ref-v1"
            or latest.get("contentSha256") != manifest_sha256
            or latest.get("relativePath")
            != f"manifests/{manifest_sha256}.json"
        ):
            raise ValueError
        manifests_fd = _open_directory_at(root_fd, "manifests")
        manifest_raw, manifest_identity = _read_regular_snapshot_at(
            manifests_fd,
            f"{manifest_sha256}.json",
            maximum=8 * 1024 * 1024,
        )
        if hashlib.sha256(manifest_raw).hexdigest() != manifest_sha256:
            raise ValueError
        schedule = Stage10FrozenSchedule.model_validate(_strict_object(
            manifest_raw,
            "stage10_frozen_schedule_unverified",
        ))
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_regular_snapshot_at(
            manifests_fd,
            f"{manifest_sha256}.json",
            expected_raw=manifest_raw,
            expected_identity=manifest_identity,
            maximum=8 * 1024 * 1024,
        )
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_root_path(store, root_fd)
        _verify_regular_snapshot_at(
            root_fd,
            "latest.json",
            expected_raw=latest_raw,
            expected_identity=latest_identity,
            maximum=16 * 1024,
        )
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_root_path(store, root_fd)
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_root_path(store, root_fd)
        return schedule
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_frozen_schedule_unverified") from exc
    finally:
        if manifests_fd is not None:
            os.close(manifests_fd)
        if root_fd is not None:
            os.close(root_fd)


def _resolve_stage10_schedule_slot(
    *,
    store_root: Path,
    manifest_sha256: str,
    slot_id: str,
    observed_at: datetime,
) -> tuple[Stage10FrozenSchedule, Stage10FrozenScheduleSlot]:
    if (
        not isinstance(slot_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", slot_id) is None
    ):
        raise ValueError("stage10_frozen_schedule_slot_unverified")
    schedule = load_stage10_frozen_schedule(store_root, manifest_sha256)
    matches = tuple(item for item in schedule.slots if item.slot_id == slot_id)
    current = _aware(observed_at)
    if len(matches) != 1:
        raise ValueError("stage10_frozen_schedule_slot_unverified")
    slot = matches[0]
    if (
        slot.trade_date != current.date()
        or not (slot.effective_from <= current < slot.effective_until)
        or slot.trade_date not in schedule.official_trading_dates
    ):
        raise ValueError("stage10_frozen_schedule_slot_unverified")
    return schedule, slot


def _schedule_calendar_is_verified(
    schedule: Stage10FrozenSchedule,
    verifier: Callable[[date], Optional[str]],
) -> bool:
    return all(verifier(day) == "full" for day in schedule.official_trading_dates)


def publish_stage10_frozen_schedule(
    store_root: Path,
    schedule: Stage10FrozenSchedule,
) -> str:
    """独立发布冻结调度清单；现场采集路径不会调用此函数。"""
    if not isinstance(schedule, Stage10FrozenSchedule):
        raise ValueError("stage10_frozen_schedule_unverified")
    store = _private_tmp_child(store_root)
    lock = _global_collection_lock(
        PRIVATE_TMP / SCHEDULE_STORE_LOCK_ROOT_NAME / SCHEDULE_STORE_LOCK_NAME
    )
    acquired = False
    root_fd: Optional[int] = None
    manifests_fd: Optional[int] = None
    temporary_name = ".latest." + secrets.token_hex(12)
    try:
        acquired = lock.acquire(blocking=True) is True
        if not acquired:
            raise ValueError("stage10_frozen_schedule_lock_contended")
        _assert_collection_lock(lock)
        _prepare_attempt_root(store)
        root_fd = _open_root_directory(store, create_missing=False)
        _verify_root_path(store, root_fd)
        manifest = _canonical_json(
            schedule.model_dump(mode="json", by_alias=True)
        )
        manifest_sha = hashlib.sha256(manifest).hexdigest()
        latest = _canonical_json({
            "contractId": "radar-stage10-schedule-manifest-ref-v1",
            "contentSha256": manifest_sha,
            "relativePath": f"manifests/{manifest_sha}.json",
        })
        _assert_collection_lock(lock)
        manifests_fd = _ensure_directory_at(root_fd, "manifests")
        _assert_collection_lock(lock)
        content_name = f"{manifest_sha}.json"
        try:
            _write_new_at(
                manifests_fd, content_name, manifest
            )
            os.fsync(manifests_fd)
        except ValueError:
            if _read_regular_at(
                manifests_fd,
                content_name,
                maximum=8 * 1024 * 1024,
            ) != manifest:
                raise ValueError("stage10_frozen_schedule_unverified")
        _assert_collection_lock(lock)
        _verify_root_path(store, root_fd)
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        manifest_again, manifest_identity = _read_regular_snapshot_at(
            manifests_fd,
            content_name,
            maximum=8 * 1024 * 1024,
        )
        if (
            manifest_again != manifest
            or hashlib.sha256(manifest_again).hexdigest() != manifest_sha
        ):
            raise ValueError("stage10_frozen_schedule_unverified")

        # 写 latest 临时文件前再确认内容仓仍是取得锁后打开的同一目录。
        _assert_collection_lock(lock)
        _verify_root_path(store, root_fd)
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_regular_snapshot_at(
            manifests_fd,
            content_name,
            expected_raw=manifest,
            expected_identity=manifest_identity,
            maximum=8 * 1024 * 1024,
        )
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_root_path(store, root_fd)
        _assert_collection_lock(lock)
        _write_new_at(root_fd, temporary_name, latest)

        # replace 是唯一会改变公开指针的动作；紧邻动作前完成最后一次复验，
        # 任何漂移都只清理临时文件，保留已有 latest。
        _assert_collection_lock(lock)
        _verify_root_path(store, root_fd)
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_regular_snapshot_at(
            manifests_fd,
            content_name,
            expected_raw=manifest,
            expected_identity=manifest_identity,
            maximum=8 * 1024 * 1024,
        )
        _verify_directory_entry(root_fd, "manifests", manifests_fd)
        _verify_root_path(store, root_fd)
        _assert_collection_lock(lock)
        os.replace(
            temporary_name,
            "latest.json",
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        os.fsync(root_fd)
        return manifest_sha
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_frozen_schedule_publish_failed") from exc
    finally:
        if root_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=root_fd)
            except OSError:
                pass
        if manifests_fd is not None:
            os.close(manifests_fd)
        if root_fd is not None:
            os.close(root_fd)
        if acquired:
            lock.release()


def _load_attempt_universe_at(root_fd: int):
    """只沿 latest 公布链读取已有 universe；不扫描内容目录。"""
    from radar.formal_operational_evidence_collector import AttemptUniverse

    manifests_fd: Optional[int] = None
    reports_fd: Optional[int] = None
    try:
        try:
            latest_raw = _read_regular_at(
                root_fd,
                "latest.json",
                maximum=16 * 1024,
            )
        except ValueError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return None
            raise
        latest = _strict_object(
            latest_raw,
            "stage10_attempt_universe_unverified",
        )
        if set(latest) != {
            "contractId", "manifestSha256", "manifestRelativePath",
        }:
            raise ValueError
        manifest_sha = latest.get("manifestSha256")
        if (
            latest.get("contractId")
            != "radar-formal-operational-attempt-universe-ref-v1"
            or not isinstance(manifest_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest_sha) is None
            or latest.get("manifestRelativePath")
            != f"manifests/{manifest_sha}.json"
        ):
            raise ValueError
        manifests_fd = _open_directory_at(root_fd, "manifests")
        manifest_raw = _read_regular_at(
            manifests_fd,
            f"{manifest_sha}.json",
            maximum=64 * 1024,
        )
        if hashlib.sha256(manifest_raw).hexdigest() != manifest_sha:
            raise ValueError
        manifest = _strict_object(
            manifest_raw,
            "stage10_attempt_universe_unverified",
        )
        if set(manifest) != {
            "contractId", "contractVersion", "contentSha256",
            "reportRelativePath",
        }:
            raise ValueError
        report_sha = manifest.get("contentSha256")
        if (
            manifest.get("contractId")
            != "radar-formal-operational-attempt-universe-manifest-v1"
            or manifest.get("contractVersion")
            != "radar-formal-operational-attempt-universe-v1"
            or not isinstance(report_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", report_sha) is None
            or manifest.get("reportRelativePath") != f"reports/{report_sha}.json"
        ):
            raise ValueError
        reports_fd = _open_directory_at(root_fd, "reports")
        report_raw = _read_regular_at(
            reports_fd,
            f"{report_sha}.json",
            maximum=8 * 1024 * 1024,
        )
        if hashlib.sha256(report_raw).hexdigest() != report_sha:
            raise ValueError
        return AttemptUniverse.model_validate(
            _strict_object(report_raw, "stage10_attempt_universe_unverified")
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_attempt_universe_unverified") from exc
    finally:
        if reports_fd is not None:
            os.close(reports_fd)
        if manifests_fd is not None:
            os.close(manifests_fd)


def _attempt_outcome(status: str) -> str:
    if status in {"available", "unchanged"}:
        return "success"
    if status == "contended":
        return "contended"
    if status == "failed":
        return "failed"
    return "not_attempted"


def _publish_attempt_universe(
    *,
    store_root: Path,
    attempt_root: Path,
    checkpoint_sha: str,
    result: Stage10LiveCollectionResult,
    schedule_slot: datetime,
    schedule_manifest_sha256: str,
    slot_id: str,
) -> None:
    """用独立安全锁串行公布，主锁 contended 也可记录且不会重入执行器。"""
    from radar.formal_operational_evidence_collector import (
        AttemptModuleOutcomes,
        AttemptUniverse,
        AttemptUniverseEntry,
        GlobalLockEvidence,
    )

    store = _private_tmp_child(store_root)
    lock = _global_collection_lock(
        PRIVATE_TMP / ATTEMPT_UNIVERSE_LOCK_ROOT_NAME / ATTEMPT_UNIVERSE_LOCK_NAME
    )
    acquired = False
    store_fd: Optional[int] = None
    reports_fd: Optional[int] = None
    manifests_fd: Optional[int] = None
    temporary_name = ".latest." + secrets.token_hex(12)
    try:
        acquired = lock.acquire(blocking=True) is True
        if not acquired:
            raise ValueError("stage10_attempt_universe_lock_contended")
        _assert_collection_lock(lock)
        _prepare_attempt_root(store)
        store_fd = _open_root_directory(store, create_missing=False)
        _verify_root_path(store, store_fd)
        _assert_collection_lock(lock)
        existing = _load_attempt_universe_at(store_fd)
        if existing is not None and (
            existing.universe_id != ATTEMPT_UNIVERSE_ID
            or existing.evidence_scope != "live"
            or existing.schedule_manifest_sha256 != schedule_manifest_sha256
        ):
            raise ValueError("stage10_attempt_universe_identity_unverified")
        entry = AttemptUniverseEntry(
            attemptId=result.attempt_id,
            slotId=slot_id,
            tradeDate=result.trade_date,
            scheduleSlot=schedule_slot,
            attemptRootRelativePath=_private_relative(attempt_root),
            checkpointSha256=checkpoint_sha,
            expectedRunId=result.stage9.radar_run_id,
            expectedLockState=(
                "contended"
                if result.preflight.status == "dry_attempt_contended"
                else "not_attempted"
                if result.preflight.status != "ready"
                else "acquired"
            ),
            moduleOutcomes=AttemptModuleOutcomes(
                stage9=_attempt_outcome(result.stage9.status),
                trendRotation=_attempt_outcome(result.trend_rotation.status),
                leaderObservation=_attempt_outcome(
                    result.leader_observation.status
                ),
                etfObservation=_attempt_outcome(result.etf_observation.status),
            ),
        )
        entries = list(existing.expected_attempts) if existing is not None else []
        matching = [item for item in entries if (
            item.attempt_id == entry.attempt_id
            or item.attempt_root_relative_path == entry.attempt_root_relative_path
        )]
        if matching:
            previous = matching[0]
            if (
                len(matching) != 1
                or previous.attempt_id != entry.attempt_id
                or previous.attempt_root_relative_path
                != entry.attempt_root_relative_path
                or previous.trade_date != entry.trade_date
                or previous.schedule_slot != entry.schedule_slot
                or previous.slot_id != entry.slot_id
            ):
                raise ValueError("stage10_attempt_universe_attempt_conflict")
            entries[entries.index(previous)] = entry
        else:
            entries.append(entry)
        entries.sort(key=lambda item: (item.schedule_slot, item.attempt_id))
        universe = AttemptUniverse(
            contractVersion="radar-formal-operational-attempt-universe-v1",
            universeId=(existing.universe_id if existing is not None else ATTEMPT_UNIVERSE_ID),
            evidenceScope="live",
            scheduleManifestSha256=schedule_manifest_sha256,
            expectedAttempts=tuple(entries),
            globalLockEvidence=GlobalLockEvidence(
                contractVersion="radar-stage10-global-lock-evidence-v1",
                lockRootName=GLOBAL_COLLECTION_LOCK_ROOT_NAME,
                lockName=GLOBAL_COLLECTION_LOCK_NAME,
                acquisitionMode="exclusive_nonblocking",
                identityCheckMode="before_each_external_runner",
                coveredAttemptIds=tuple(item.attempt_id for item in entries),
            ),
        )
        report = _canonical_json(
            universe.model_dump(mode="json", by_alias=True)
        )
        report_sha = hashlib.sha256(report).hexdigest()
        manifest = _canonical_json({
            "contractId": "radar-formal-operational-attempt-universe-manifest-v1",
            "contractVersion": "radar-formal-operational-attempt-universe-v1",
            "contentSha256": report_sha,
            "reportRelativePath": f"reports/{report_sha}.json",
        })
        manifest_sha = hashlib.sha256(manifest).hexdigest()
        latest = _canonical_json({
            "contractId": "radar-formal-operational-attempt-universe-ref-v1",
            "manifestSha256": manifest_sha,
            "manifestRelativePath": f"manifests/{manifest_sha}.json",
        })
        _assert_collection_lock(lock)
        reports_fd = _ensure_directory_at(store_fd, "reports")
        _assert_collection_lock(lock)
        manifests_fd = _ensure_directory_at(store_fd, "manifests")
        for descriptor, name, payload in (
            (reports_fd, f"{report_sha}.json", report),
            (manifests_fd, f"{manifest_sha}.json", manifest),
        ):
            _assert_collection_lock(lock)
            try:
                _write_new_at(descriptor, name, payload)
                os.fsync(descriptor)
            except ValueError:
                if _read_regular_at(descriptor, name, maximum=8 * 1024 * 1024) != payload:
                    raise ValueError("stage10_attempt_universe_unverified")
        _assert_collection_lock(lock)
        _write_new_at(store_fd, temporary_name, latest)
        _assert_collection_lock(lock)
        _verify_root_path(store, store_fd)
        os.replace(
            temporary_name,
            "latest.json",
            src_dir_fd=store_fd,
            dst_dir_fd=store_fd,
        )
        os.fsync(store_fd)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_attempt_universe_publish_failed") from exc
    finally:
        if store_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=store_fd)
            except OSError:
                pass
        if manifests_fd is not None:
            os.close(manifests_fd)
        if reports_fd is not None:
            os.close(reports_fd)
        if store_fd is not None:
            os.close(store_fd)
        if acquired:
            lock.release()


def _load_checkpoint_by_sha(
    root: Path,
    content_sha: str,
    *,
    root_fd: int,
) -> Stage10LiveCollectionResult:
    """直接重放 universe 固定的不可变检查点，不跟随可变 latest。"""
    if (
        not isinstance(content_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", content_sha) is None
    ):
        raise ValueError("stage10_live_collection_checkpoint_unverified")
    store_fd: Optional[int] = None
    try:
        _verify_root_path(root, root_fd)
        store_fd = _open_directory_at(root_fd, CHECKPOINT_STORE_NAME)
        raw = _read_regular_at(
            store_fd,
            content_sha + ".json",
            maximum=256 * 1024,
        )
        if hashlib.sha256(raw).hexdigest() != content_sha:
            raise ValueError
        payload = _strict_object(
            raw,
            "stage10_live_collection_checkpoint_unverified",
        )
        if not isinstance(payload, dict) or payload.get("contractId") != CHECKPOINT_CONTRACT_ID:
            raise ValueError
        result = Stage10LiveCollectionResult.model_validate(payload.get("result"))
        if result.contract_id != RESULT_CONTRACT_ID or result.formal_enabled is not False:
            raise ValueError
        _verify_root_path(root, root_fd)
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("stage10_live_collection_checkpoint_unverified") from exc
    finally:
        if store_fd is not None:
            os.close(store_fd)


def _load_checkpoint(root: Path, *, root_fd: int) -> Optional[Stage10LiveCollectionResult]:
    store_fd: Optional[int] = None
    try:
        _verify_root_path(root, root_fd)
        try:
            pointer_raw = _read_regular_at(
                root_fd,
                CHECKPOINT_POINTER_NAME,
                maximum=16 * 1024,
            )
        except ValueError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return None
            raise
        pointer = _strict_object(
            pointer_raw,
            "stage10_live_collection_checkpoint_unverified",
        )
        content_sha = pointer.get("contentSha256")
        relative = pointer.get("relativePath")
        if (
            pointer.get("contractId")
            != "radar-stage10-live-collection-checkpoint-ref-v1"
            or not isinstance(content_sha, str)
            or len(content_sha) != 64
            or any(value not in "0123456789abcdef" for value in content_sha)
            or relative != CHECKPOINT_STORE_NAME + "/" + content_sha + ".json"
        ):
            raise ValueError
        return _load_checkpoint_by_sha(root, content_sha, root_fd=root_fd)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("stage10_live_collection_checkpoint_unverified") from exc
    finally:
        if store_fd is not None:
            os.close(store_fd)


def _stage9_from_payload(payload: object) -> tuple[Stage10Stage9Result, Dict[str, Path]]:
    if not isinstance(payload, Mapping):
        return Stage10Stage9Result(
            status="failed", reason="stage10_stage9_failed"
        ), {}
    if payload.get("status") != "registered":
        reason = _safe_reason(
            payload.get("reason"),
            "stage10_stage9_failed",
        )
        dirs = payload.get("formalShadowInputDirs")
        radar_run_id = payload.get("radarRunId")
        if dirs is None and radar_run_id is None:
            return Stage10Stage9Result(status="failed", reason=reason), {}
        if (
            not isinstance(radar_run_id, str)
            or not radar_run_id
            or not isinstance(dirs, Mapping)
            or not dirs
            or not set(dirs).issubset(
                {"trendRotation", "leaderObservation"}
            )
        ):
            raise ValueError("stage10_live_collection_stage9_unverified")
        parsed: Dict[str, Path] = {}
        snapshots: Dict[str, tuple[str, str, str]] = {}
        for module in ("trendRotation", "leaderObservation"):
            if module not in dirs:
                continue
            raw = dirs[module]
            if not isinstance(raw, str):
                raise ValueError("stage10_live_collection_stage9_unverified")
            path = _private_tmp_child(Path(raw))
            parsed[module] = path
            snapshots[module] = _snapshot_input(path, module)
        if (
            len(set(parsed.values())) != len(parsed)
            or any(value[2] != radar_run_id for value in snapshots.values())
        ):
            raise ValueError("stage10_live_collection_stage9_unverified")
        return Stage10Stage9Result(
            status="failed",
            reason=reason,
            radarRunId=radar_run_id,
            trendInputRelativePath=(
                _private_relative(parsed["trendRotation"])
                if "trendRotation" in parsed else None
            ),
            leaderInputRelativePath=(
                _private_relative(parsed["leaderObservation"])
                if "leaderObservation" in parsed else None
            ),
            trendInputSha256=(
                snapshots["trendRotation"][0]
                if "trendRotation" in snapshots else None
            ),
            leaderInputSha256=(
                snapshots["leaderObservation"][0]
                if "leaderObservation" in snapshots else None
            ),
            trendInputContractId=(
                snapshots["trendRotation"][1]
                if "trendRotation" in snapshots else None
            ),
            leaderInputContractId=(
                snapshots["leaderObservation"][1]
                if "leaderObservation" in snapshots else None
            ),
        ), parsed
    dirs = payload.get("formalShadowInputDirs")
    radar_run_id = payload.get("radarRunId")
    if not isinstance(radar_run_id, str) or not radar_run_id:
        raise ValueError("stage10_live_collection_stage9_unverified")
    if not isinstance(dirs, Mapping) or set(dirs) != {"trendRotation", "leaderObservation"}:
        raise ValueError("stage10_live_collection_stage9_unverified")
    parsed: Dict[str, Path] = {}
    snapshots: Dict[str, tuple[str, str]] = {}
    for module in ("trendRotation", "leaderObservation"):
        raw = dirs[module]
        if not isinstance(raw, str):
            raise ValueError("stage10_live_collection_stage9_unverified")
        path = _private_tmp_child(Path(raw))
        parsed[module] = path
        snapshots[module] = _snapshot_input(path, module)
    if parsed["trendRotation"] == parsed["leaderObservation"]:
        raise ValueError("stage10_live_collection_stage9_unverified")
    if any(snapshot[2] != radar_run_id for snapshot in snapshots.values()):
        raise ValueError("stage10_live_collection_stage9_unverified")
    etf_path = payload.get("etfFormalAdmissionPath")
    if not isinstance(etf_path, str) or not etf_path or etf_path.startswith("/") or ".." in Path(etf_path).parts:
        raise ValueError("stage10_live_collection_stage9_unverified")
    manifest_path = payload.get("manifestPath")
    manifest_relative = payload.get("manifestRelativePath")
    manifest_sha = payload.get("manifestSha256")
    if (
        not isinstance(manifest_path, str)
        or not manifest_path
        or manifest_path.startswith("/")
        or ".." in Path(manifest_path).parts
        or not isinstance(manifest_relative, str)
        or not manifest_relative
        or manifest_relative.startswith("/")
        or ".." in Path(manifest_relative).parts
        or Path(manifest_relative).name != Path(manifest_path).name
        or not isinstance(manifest_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha) is None
    ):
        raise ValueError("stage10_live_collection_stage9_unverified")
    sample_id = payload.get("sampleId")
    role = payload.get("role")
    _snapshot_manifest(
        relative_path=manifest_relative,
        expected_sha=manifest_sha,
        radar_run_id=radar_run_id,
        sample_id=sample_id if isinstance(sample_id, str) else None,
        role=role if isinstance(role, str) else None,
    )
    return Stage10Stage9Result(
        status="available",
        role=role if isinstance(role, str) else None,
        sampleId=sample_id if isinstance(sample_id, str) else None,
        radarRunId=radar_run_id,
        etfFormalAdmissionPath=etf_path,
        trendInputRelativePath=_private_relative(parsed["trendRotation"]),
        leaderInputRelativePath=_private_relative(parsed["leaderObservation"]),
        trendInputSha256=snapshots["trendRotation"][0],
        leaderInputSha256=snapshots["leaderObservation"][0],
        trendInputContractId=snapshots["trendRotation"][1],
        leaderInputContractId=snapshots["leaderObservation"][1],
        manifestRelativePath=manifest_relative,
        manifestSha256=manifest_sha,
    ), parsed


def _module_from_registration(
    *,
    input_path: Optional[Path],
    payload: object,
) -> Stage10ModuleResult:
    if not isinstance(payload, Mapping):
        raise ValueError("stage10_live_collection_registration_unverified")
    status = payload.get("status")
    input_sha = None
    input_contract = None
    input_run_id = None
    if input_path is not None:
        input_sha, input_contract, input_run_id = _snapshot_input(
            input_path,
            str(payload.get("module") or "")
            if payload.get("module") in _MODULES
            else _module_for_input(input_path),
        )
    if status not in {"available", "unchanged", "contended"}:
        return Stage10ModuleResult(
            status="failed",
            reason=_safe_reason(payload.get("reason"), "stage10_registration_failed"),
            inputRelativePath=_private_relative(input_path) if input_path else None,
            inputSha256=input_sha,
            inputContractId=input_contract,
            inputRunId=input_run_id,
        )
    sha = payload.get("contentSha256")
    relative = payload.get("ledgerRelativePath")
    if status == "contended":
        if sha not in {None, ""} or relative not in {None, ""}:
            raise ValueError("stage10_live_collection_registration_unverified")
        return Stage10ModuleResult(
            status="contended",
            inputRelativePath=_private_relative(input_path) if input_path else None,
            inputSha256=input_sha,
            inputContractId=input_contract,
            inputRunId=input_run_id,
        )
    if (
        not isinstance(sha, str)
        or len(sha) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in sha)
        or not isinstance(relative, str)
        or not relative
        or relative.startswith("/")
        or ".." in Path(relative).parts
    ):
        raise ValueError("stage10_live_collection_registration_unverified")
    return Stage10ModuleResult(
        status=status,
        ledgerSha256=sha,
        ledgerRelativePath=relative,
        inputRelativePath=_private_relative(input_path) if input_path else None,
        inputSha256=input_sha,
        inputContractId=input_contract,
        inputRunId=input_run_id,
    )


def _module_for_input(path: Path) -> str:
    directory: Optional[int] = None
    try:
        directory = _open_root_directory(_private_tmp_child(path), create_missing=False)
        raw = _read_regular_at(directory, RECEIPT_FILENAME, maximum=1024 * 1024)
        receipt = _strict_object(
            raw,
            "stage10_live_collection_input_reference_unverified",
        )
        module = receipt.get("module")
        if module not in _MODULES:
            raise ValueError
        return module
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("stage10_live_collection_input_reference_unverified") from exc
    finally:
        if directory is not None:
            os.close(directory)


def _not_attempted() -> Stage10ModuleResult:
    return Stage10ModuleResult(status="not_attempted")


def _etf_capture_result(payload: object) -> Stage10ModuleResult:
    """ETF 已发布但登记失败时保留其相对输入引用，供离线恢复。"""
    if not isinstance(payload, Mapping):
        raise ValueError("stage10_live_collection_etf_result_unverified")
    input_path = None
    raw_input = payload.get("inputPath")
    if raw_input is not None:
        if not isinstance(raw_input, str):
            raise ValueError("stage10_live_collection_etf_result_unverified")
        input_path = _private_tmp_child(Path(raw_input))
        if not input_path.is_dir():
            raise ValueError("stage10_live_collection_etf_result_unverified")
    if payload.get("status") in {"available", "unchanged"} and input_path is None:
        raise ValueError("stage10_live_collection_etf_result_unverified")
    return _module_from_registration(input_path=input_path, payload=payload)


def _verify_published_input(
    *,
    path: Path,
    module: str,
    expected_sha: Optional[str],
    expected_contract: Optional[str],
    expected_run_id: Optional[str],
) -> None:
    if (
        not isinstance(expected_sha, str)
        or not isinstance(expected_contract, str)
        or not isinstance(expected_run_id, str)
    ):
        raise ValueError("stage10_live_collection_input_reference_unverified")
    actual_sha, actual_contract, actual_run_id = _snapshot_input(path, module)
    if (
        actual_sha != expected_sha
        or actual_contract != expected_contract
        or actual_run_id != expected_run_id
    ):
        raise ValueError("stage10_live_collection_input_reference_unverified")


def _verify_checkpoint_inputs(result: Stage10LiveCollectionResult) -> None:
    """恢复前重验检查点中每一份已发布输入，包含已完成的模块。"""
    has_stage6_inputs = (
        result.stage9.trend_input_relative_path is not None
        or result.stage9.leader_input_relative_path is not None
    )
    if result.stage9.status == "available" or has_stage6_inputs:
        if (
            result.stage9.radar_run_id is None
        ):
            raise ValueError("stage10_live_collection_input_reference_unverified")
        if result.stage9.status == "available":
            if (
                result.stage9.manifest_relative_path is None
                or result.stage9.manifest_sha256 is None
            ):
                raise ValueError("stage10_live_collection_manifest_reference_unverified")
            _snapshot_manifest(
                relative_path=result.stage9.manifest_relative_path,
                expected_sha=result.stage9.manifest_sha256,
                radar_run_id=result.stage9.radar_run_id,
                sample_id=result.stage9.sample_id,
                role=result.stage9.role,
            )
        for relative, module, sha, contract in (
            (
                result.stage9.trend_input_relative_path,
                "trendRotation",
                result.stage9.trend_input_sha256,
                result.stage9.trend_input_contract_id,
            ),
            (
                result.stage9.leader_input_relative_path,
                "leaderObservation",
                result.stage9.leader_input_sha256,
                result.stage9.leader_input_contract_id,
            ),
        ):
            if relative is None:
                continue
            _verify_published_input(
                path=_from_private_relative(relative),
                module=module,
                expected_sha=sha,
                expected_contract=contract,
                expected_run_id=result.stage9.radar_run_id,
            )
    etf = result.etf_observation
    if etf.input_relative_path is not None:
        _verify_published_input(
            path=_from_private_relative(etf.input_relative_path),
            module="etfObservation",
            expected_sha=etf.input_sha256,
            expected_contract=etf.input_contract_id,
            expected_run_id=result.stage9.radar_run_id,
        )
def _checkpoint_result(
    *,
    base: Stage10LiveCollectionResult,
    finished_at: datetime,
    stage9: Optional[Stage10Stage9Result] = None,
    trend: Optional[Stage10ModuleResult] = None,
    leader: Optional[Stage10ModuleResult] = None,
    etf: Optional[Stage10ModuleResult] = None,
    effects: Optional[Tuple[str, ...]] = None,
) -> Stage10LiveCollectionResult:
    return Stage10LiveCollectionResult(
        attemptId=base.attempt_id,
        startedAt=base.started_at,
        finishedAt=finished_at,
        tradeDate=base.trade_date,
        preflight=Stage10Preflight(status="ready"),
        stage9=stage9 or base.stage9,
        trendRotation=trend or base.trend_rotation,
        leaderObservation=leader or base.leader_observation,
        etfObservation=etf or base.etf_observation,
        effects=effects if effects is not None else base.effects,
        formalEnabled=False,
    )


def run_stage10_live_collection(
    *,
    confirmed: StrictBool,
    attempt_root: Path,
    now: Callable[[], datetime],
    local_calendar_verifier: Callable[[date], Optional[str]],
    stage9_runner: Callable[[], Mapping[str, Any]],
    shadow_registrar: Callable[[str, Path], Mapping[str, Any]],
    etf_runner: Callable[[Stage10Stage9Result], Mapping[str, Any]],
    attempt_id: Optional[str] = None,
    schedule_store_root: Optional[Path] = None,
    schedule_manifest_sha256: Optional[str] = None,
    slot_id: Optional[str] = None,
    attempt_universe_root: Optional[Path] = None,
    lock_factory: Callable[[Path], Any] = _global_collection_lock,
) -> Stage10LiveCollectionResult:
    """执行一次已确认的现场链，或安全返回零副作用 dry 状态。"""
    resolved_attempt_id, observed_at, started_at, dry = _stage10_preflight(
        confirmed=confirmed,
        attempt_root=attempt_root,
        attempt_id=attempt_id,
        observed_at=now(),
    )
    if dry is not None:
        return dry
    schedule_arguments = (
        schedule_store_root,
        schedule_manifest_sha256,
        slot_id,
    )
    has_schedule = all(value is not None for value in schedule_arguments)
    schedule_slot = started_at
    if any(value is not None for value in schedule_arguments) and not has_schedule:
        raise ValueError("stage10_attempt_universe_binding_unverified")
    if attempt_universe_root is not None and not has_schedule:
        raise ValueError("stage10_attempt_universe_binding_unverified")
    if has_schedule:
        schedule, selected_slot = _resolve_stage10_schedule_slot(
            store_root=schedule_store_root,
            manifest_sha256=schedule_manifest_sha256,
            slot_id=slot_id,
            observed_at=observed_at,
        )
        schedule_slot = selected_slot.schedule_slot
        if attempt_id is None:
            resolved_attempt_id = hashlib.sha256(
                (
                    str(attempt_root)
                    + schedule_manifest_sha256
                    + selected_slot.slot_id
                ).encode("utf-8")
            ).hexdigest()[:24]
        if not _schedule_calendar_is_verified(
            schedule, local_calendar_verifier
        ):
            return _empty_result(
                attempt_id=resolved_attempt_id,
                now=started_at,
                status="dry_calendar_unverified",
            )
    elif local_calendar_verifier(observed_at.date()) != "full":
        return _empty_result(
            attempt_id=resolved_attempt_id,
            now=observed_at,
            status="dry_calendar_unverified",
        )

    root = _private_tmp_child(attempt_root)
    universe_root = (
        _private_tmp_child(attempt_universe_root)
        if attempt_universe_root is not None
        else None
    )
    if universe_root is not None and universe_root == root:
        raise ValueError("stage10_attempt_universe_binding_unverified")
    _prepare_attempt_root(root)
    root_fd: Optional[int] = None
    lock = None
    lock_acquired = False
    attempt_lock = None
    attempt_lock_acquired = False
    try:
        root_fd = _open_root_directory(root, create_missing=False)
        _verify_root_path(root, root_fd)
        attempt_lock = _per_attempt_lock(root)
        attempt_lock_acquired = attempt_lock.acquire(blocking=False) is True
        if not attempt_lock_acquired:
            return _empty_result(
                attempt_id=resolved_attempt_id,
                now=started_at,
                status="dry_attempt_contended",
            )
        _assert_collection_lock(attempt_lock)
        _verify_root_path(root, root_fd)
        if not _bind_attempt_owner(
            root=root,
            root_fd=root_fd,
            attempt_id=resolved_attempt_id,
            attempt_lock=attempt_lock,
        ):
            return _empty_result(
                attempt_id=resolved_attempt_id,
                now=started_at,
                status="dry_attempt_contended",
            )
        lock = lock_factory(
            PRIVATE_TMP
            / GLOBAL_COLLECTION_LOCK_ROOT_NAME
            / GLOBAL_COLLECTION_LOCK_NAME
        )
        lock_acquired = lock.acquire(blocking=False) is True
        if not lock_acquired:
            current = _empty_result(
                attempt_id=resolved_attempt_id,
                now=started_at,
                status="dry_attempt_contended",
            )
            if universe_root is not None:
                _assert_collection_lock(attempt_lock)
                checkpoint_sha = _write_checkpoint(root, current, root_fd=root_fd)
                _publish_attempt_universe(
                    store_root=universe_root,
                    attempt_root=root,
                    checkpoint_sha=checkpoint_sha,
                    result=current,
                    schedule_slot=schedule_slot,
                    schedule_manifest_sha256=schedule_manifest_sha256,
                    slot_id=slot_id,
                )
            return current
        try:
            _assert_collection_lock(attempt_lock)
            _verify_root_path(root, root_fd)
        except ValueError as exc:
            raise ValueError(
                "stage10_live_collection_attempt_root_unverified"
            ) from exc
        _assert_collection_lock(attempt_lock)
        existing = _load_checkpoint(root, root_fd=root_fd)
        if existing is not None:
            if existing.attempt_id != resolved_attempt_id:
                raise ValueError("stage10_live_collection_attempt_conflict")
            _verify_checkpoint_inputs(existing)
            current = existing
        else:
            current = Stage10LiveCollectionResult(
                attemptId=resolved_attempt_id,
                startedAt=started_at,
                finishedAt=started_at,
                tradeDate=started_at.date(),
                preflight=Stage10Preflight(status="ready"),
                stage9=Stage10Stage9Result(status="not_attempted"),
                trendRotation=_not_attempted(),
                leaderObservation=_not_attempted(),
                etfObservation=_not_attempted(),
                formalEnabled=False,
            )

        # 在任何外部 runner 前先把本次 invocation 作为不可变 pending 事实
        # 发布。即使最终 universe 升级失败，运营证据也不会把这次执行隐藏掉。
        if universe_root is not None and current.stage9.status == "not_attempted":
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            pending_sha = _write_checkpoint(root, current, root_fd=root_fd)
            _publish_attempt_universe(
                store_root=universe_root,
                attempt_root=root,
                checkpoint_sha=pending_sha,
                result=current,
                schedule_slot=schedule_slot,
                schedule_manifest_sha256=schedule_manifest_sha256,
                slot_id=slot_id,
            )

        input_dirs: Dict[str, Path] = {}
        if current.stage9.status == "not_attempted":
            _assert_collection_lock(lock)
            try:
                stage9, input_dirs = _stage9_from_payload(stage9_runner())
            except Exception:
                stage9 = Stage10Stage9Result(
                    status="failed",
                    reason="stage10_stage9_failed",
                )
            current = _checkpoint_result(
                base=current,
                finished_at=_aware(now()),
                stage9=stage9,
                effects=(
                    current.effects + ("stage9",)
                    if stage9.status == "available"
                    else current.effects
                ),
            )
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            _write_checkpoint(root, current, root_fd=root_fd)
        elif current.stage9.status == "available" or (
            current.stage9.trend_input_relative_path is not None
            or current.stage9.leader_input_relative_path is not None
        ):
            if current.stage9.trend_input_relative_path is not None:
                input_dirs["trendRotation"] = _from_private_relative(
                    current.stage9.trend_input_relative_path
                )
            if current.stage9.leader_input_relative_path is not None:
                input_dirs["leaderObservation"] = _from_private_relative(
                    current.stage9.leader_input_relative_path
                )
            for module, state in (
                ("trendRotation", current.trend_rotation),
                ("leaderObservation", current.leader_observation),
            ):
                if state.input_relative_path is not None:
                    input_dirs[module] = _from_private_relative(state.input_relative_path)

        if current.stage9.status != "available" and not input_dirs:
            if universe_root is not None:
                _assert_collection_lock(lock)
                _assert_collection_lock(attempt_lock)
                _publish_attempt_universe(
                    store_root=universe_root,
                    attempt_root=root,
                    checkpoint_sha=_checkpoint_pointer_sha(root, root_fd=root_fd),
                    result=current,
                    schedule_slot=schedule_slot,
                    schedule_manifest_sha256=schedule_manifest_sha256,
                    slot_id=slot_id,
                )
            return current

        for module, existing_state, field in (
            ("trendRotation", current.trend_rotation, "trend"),
            ("leaderObservation", current.leader_observation, "leader"),
        ):
            if existing_state.status != "not_attempted":
                continue
            input_path = input_dirs.get(module)
            if input_path is None:
                state = Stage10ModuleResult(
                    status="skipped",
                    reason="stage10_input_unavailable",
                )
            else:
                expected_sha = (
                    current.stage9.trend_input_sha256
                    if module == "trendRotation"
                    else current.stage9.leader_input_sha256
                )
                expected_contract = (
                    current.stage9.trend_input_contract_id
                    if module == "trendRotation"
                    else current.stage9.leader_input_contract_id
                )
                _verify_published_input(
                    path=input_path,
                    module=module,
                    expected_sha=expected_sha,
                    expected_contract=expected_contract,
                    expected_run_id=current.stage9.radar_run_id,
                )
                _assert_collection_lock(lock)
                try:
                    state = _module_from_registration(
                        input_path=input_path,
                        payload=shadow_registrar(module, input_path),
                    )
                except Exception:
                    state = Stage10ModuleResult(
                        status="failed",
                        reason="stage10_registration_failed",
                        inputRelativePath=_private_relative(input_path),
                        inputSha256=expected_sha,
                        inputContractId=expected_contract,
                        inputRunId=current.stage9.radar_run_id,
                    )
            current = _checkpoint_result(
                base=current,
                finished_at=_aware(now()),
                trend=state if field == "trend" else None,
                leader=state if field == "leader" else None,
                effects=(
                    current.effects + (module,)
                    if state.status in {"available", "unchanged"}
                    else current.effects
                ),
            )
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            _write_checkpoint(root, current, root_fd=root_fd)

        if (
            (
                current.stage9.radar_run_id is None
                or current.stage9.trend_input_relative_path is None
            )
            and current.etf_observation.status == "not_attempted"
        ):
            current = _checkpoint_result(
                base=current,
                finished_at=_aware(now()),
                etf=Stage10ModuleResult(
                    status="skipped",
                    reason="stage10_etf_calendar_input_unavailable",
                ),
            )
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            _write_checkpoint(root, current, root_fd=root_fd)
        elif current.etf_observation.status == "not_attempted":
            _assert_collection_lock(lock)
            try:
                etf = _etf_capture_result(etf_runner(current.stage9))
                if (
                    etf.input_relative_path is not None
                    and etf.input_run_id != current.stage9.radar_run_id
                ):
                    raise ValueError("stage10_live_collection_etf_run_mismatch")
            except Exception:
                etf = Stage10ModuleResult(
                    status="failed",
                    reason="stage10_etf_failed",
                )
            current = _checkpoint_result(
                base=current,
                finished_at=_aware(now()),
                etf=etf,
                effects=(
                    current.effects + ("etfObservation",)
                    if etf.status in {"available", "unchanged"}
                    else current.effects + ("etfObservation_published",)
                    if etf.input_relative_path is not None
                    else current.effects
                ),
            )
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            _write_checkpoint(root, current, root_fd=root_fd)
        elif (
            current.etf_observation.status == "failed"
            and current.etf_observation.input_relative_path is not None
        ):
            etf_input = _from_private_relative(
                current.etf_observation.input_relative_path
            )
            _verify_published_input(
                path=etf_input,
                module="etfObservation",
                expected_sha=current.etf_observation.input_sha256,
                expected_contract=current.etf_observation.input_contract_id,
                expected_run_id=current.stage9.radar_run_id,
            )
            _assert_collection_lock(lock)
            try:
                etf = _module_from_registration(
                    input_path=etf_input,
                    payload=shadow_registrar("etfObservation", etf_input),
                )
            except Exception:
                etf = Stage10ModuleResult(
                    status="failed",
                    reason="stage10_registration_failed",
                    inputRelativePath=_private_relative(etf_input),
                    inputSha256=current.etf_observation.input_sha256,
                    inputContractId=current.etf_observation.input_contract_id,
                    inputRunId=current.etf_observation.input_run_id,
                )
            current = _checkpoint_result(
                base=current,
                finished_at=_aware(now()),
                etf=etf,
                effects=(
                    current.effects + ("etfObservation_replay",)
                    if etf.status in {"available", "unchanged"}
                    else current.effects
                ),
            )
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            _write_checkpoint(root, current, root_fd=root_fd)
        if universe_root is not None:
            _assert_collection_lock(lock)
            _assert_collection_lock(attempt_lock)
            _publish_attempt_universe(
                store_root=universe_root,
                attempt_root=root,
                checkpoint_sha=_checkpoint_pointer_sha(root, root_fd=root_fd),
                result=current,
                schedule_slot=schedule_slot,
                schedule_manifest_sha256=schedule_manifest_sha256,
                slot_id=slot_id,
            )
        return current
    finally:
        if lock is not None and lock_acquired:
            lock.release()
        if attempt_lock is not None and attempt_lock_acquired:
            attempt_lock.release()
        if root_fd is not None:
            os.close(root_fd)
