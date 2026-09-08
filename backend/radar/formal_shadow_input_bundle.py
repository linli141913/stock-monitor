"""阶段6真实运行输出到阶段10离线影子采集输入包的安全桥接。

本模块不联网、不读写 SQLite，也不自行补造阶段6事实。它只接收同一进程刚刚
写出的原始阶段6工件字节和调用方显式提供的官方日历原文/封套，派生不可变运行
回执，并以一次目录重命名原子发布到 ``/private/tmp``。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Dict, Mapping, Optional

from radar.formal_shadow_calendar import load_official_sse_calendar_evidence
from radar.formal_shadow_calendar import SSE_CALENDAR_URL
from radar.formal_shadow_ledger_store import (
    _MAX_CALENDAR_DOCUMENT_BYTES,
    _entry_kind,
    _explicit_root,
    _open_directory,
    _open_root_directory,
    _verify_root_path,
)
from radar.formal_shadow_observation_collector import (
    MODULE_COVERAGE_SCOPES,
    MODULE_SOURCE_CONTRACTS,
    EtfLiveShadowObservationArtifact,
    FormalShadowRunReceipt,
    FormalShadowCollectionPolicy,
    adapt_etf_observation,
    adapt_leader_observation,
    adapt_trend_rotation_observation,
    load_formal_shadow_collection_policy,
    load_formal_shadow_run_receipt,
)
from radar.etf_live_shadow_capture import EtfLiveShadowCapture


RECEIPT_FILENAME = "radar-formal-shadow-run-receipt-v1.json"
SOURCE_ARTIFACT_FILENAME = "radar-formal-shadow-source-artifact-v1.json"
COLLECTION_POLICY_FILENAME = "radar-formal-shadow-collection-policy-v1.json"
CALENDAR_INPUT_FILENAME = "radar-formal-shadow-calendar-input-v1.json"
CALENDAR_DOCUMENT_FILENAME = "sse-official-calendar.html"
TREND_SUPPORTING_FILENAME = "radar-formal-shadow-trend-supporting-v1.json"
ETF_ADMISSION_FILENAME = "radar-etf-formal-admission-v2.json"

_PRIVATE_TMP = Path("/private/tmp")
_MODULE_ORDER = ("trendRotation", "leaderObservation")
_SHANGHAI = timezone(timedelta(hours=8))
_CLOSED_GATE = {
    "formalScoreReady": False,
    "formalGateReady": False,
    "formalUsable": False,
    "stateTransitionAllowed": False,
}


class Stage6FormalShadowInputBundleError(ValueError):
    """阶段6影子输入包无法被真实、安全地发布。"""


@dataclass(frozen=True)
class Stage6FormalShadowInputBundlePublication:
    """只含已完整发布模块的输入目录；空映射表示未发布任何包。"""

    input_dirs: Mapping[str, Path]


@dataclass(frozen=True)
class Stage6FormalShadowInputBundleInputs:
    """调用方显式交付的非阶段6来源文件；缺任一项时不得发布。"""

    output_root: Path
    collection_policy_json_bytes: bytes
    calendar_envelope_json_bytes: bytes
    calendar_document_bytes: bytes


@dataclass(frozen=True)
class EtfFormalShadowInputBundlePublication:
    """可直接交给正式影子离线入口的单个 ETF 输入目录。"""

    module: str
    input_dir: Path


@dataclass(frozen=True)
class PrivateTmpFileLockIdentity:
    """锁路径、固定根和目录项指向文件的不可混用身份。"""

    normalized_path: str
    root_device: int
    root_inode: int
    file_device: int
    file_inode: int


def _set_user_immutable(descriptor: int) -> None:
    immutable = getattr(stat, "UF_IMMUTABLE", None)
    if not isinstance(immutable, int):
        raise ValueError("formal_shadow_input_bundle_lock_unverified")
    try:
        function = ctypes.CDLL(None, use_errno=True).fchflags
        function.argtypes = (ctypes.c_int, ctypes.c_uint)
        function.restype = ctypes.c_int
        if function(descriptor, immutable) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise ValueError(
            "formal_shadow_input_bundle_lock_unverified"
        ) from error


class PrivateTmpNoFollowFileLock:
    """不跟随链接的临时文件锁，可选封闭单入口私有锁根。

    ``protect_root=True`` 要求锁的父目录仅有该锁文件，首次
    获锁后将文件和根目录设为用户不可变，并保留两个 fd。
    调用方在每个外部副作用前调用 ``assert_still_held``，以
    同时验证 flock 所在 fd、路径目录项和 nlink 仍是同一 inode。
    """

    def __init__(
        self,
        path: Path,
        *,
        protect_root: bool = False,
        create_root: bool = False,
        expected_identity: Optional[PrivateTmpFileLockIdentity] = None,
    ):
        if not isinstance(path, Path) or not path.name or path.name in {".", ".."}:
            raise ValueError("formal_shadow_input_bundle_lock_unverified")
        if type(protect_root) is not bool or type(create_root) is not bool:
            raise ValueError("formal_shadow_input_bundle_lock_unverified")
        if expected_identity is not None and not isinstance(
            expected_identity,
            PrivateTmpFileLockIdentity,
        ):
            raise ValueError("formal_shadow_input_bundle_lock_unverified")
        self._path = _private_tmp_root(path.parent) / path.name
        self._protect_root = protect_root
        self._create_root = create_root
        self._expected_identity = expected_identity
        self._root_fd: Optional[int] = None
        self._file_fd: Optional[int] = None
        self.identity: Optional[PrivateTmpFileLockIdentity] = None

    @staticmethod
    def _metadata_is_immutable(metadata: os.stat_result) -> bool:
        immutable = getattr(stat, "UF_IMMUTABLE", None)
        flags = getattr(metadata, "st_flags", None)
        return (
            isinstance(immutable, int)
            and isinstance(flags, int)
            and bool(flags & immutable)
        )

    def _identity_from_descriptors(
        self,
        root_fd: int,
        file_fd: int,
    ) -> PrivateTmpFileLockIdentity:
        root_metadata = os.fstat(root_fd)
        file_metadata = os.fstat(file_fd)
        return PrivateTmpFileLockIdentity(
            normalized_path=os.fspath(self._path),
            root_device=root_metadata.st_dev,
            root_inode=root_metadata.st_ino,
            file_device=file_metadata.st_dev,
            file_inode=file_metadata.st_ino,
        )

    def _verify_descriptors(self, root_fd: int, file_fd: int) -> bool:
        try:
            _verify_root_path(self._path.parent, root_fd)
            root_metadata = os.fstat(root_fd)
            file_metadata = os.fstat(file_fd)
            entry_metadata = os.stat(
                self._path.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != os.geteuid()
                or not stat.S_ISREG(file_metadata.st_mode)
                or file_metadata.st_uid != os.geteuid()
                or file_metadata.st_nlink != 1
                or stat.S_IMODE(file_metadata.st_mode) != 0o600
                or not stat.S_ISREG(entry_metadata.st_mode)
                or (entry_metadata.st_dev, entry_metadata.st_ino)
                != (file_metadata.st_dev, file_metadata.st_ino)
            ):
                return False
            if self._protect_root and (
                stat.S_IMODE(root_metadata.st_mode) != 0o500
                or not self._metadata_is_immutable(root_metadata)
                or tuple(os.listdir(root_fd)) != (self._path.name,)
                or not self._metadata_is_immutable(file_metadata)
            ):
                return False
            identity = self._identity_from_descriptors(root_fd, file_fd)
            return (
                self._expected_identity is None
                or identity == self._expected_identity
            )
        except (OSError, TypeError, ValueError):
            return False

    def acquire(self, blocking: bool = True) -> bool:
        if (
            self._file_fd is not None
            or self._root_fd is not None
            or type(blocking) is not bool
        ):
            return False
        root_fd: Optional[int] = None
        file_fd: Optional[int] = None
        acquired = False
        try:
            root_fd = _open_root_directory(
                self._path.parent,
                create_missing=self._create_root,
            )
            _verify_root_path(self._path.parent, root_fd)
            root_metadata = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != os.geteuid()
            ):
                raise ValueError("formal_shadow_input_bundle_lock_unverified")
            kind = _entry_kind(root_fd, self._path.name)
            sealed_root = (
                self._protect_root
                and self._metadata_is_immutable(root_metadata)
            )
            if self._protect_root:
                entries = tuple(os.listdir(root_fd))
                if sealed_root:
                    if (
                        stat.S_IMODE(root_metadata.st_mode) != 0o500
                        or entries != (self._path.name,)
                        or kind != "regular"
                    ):
                        raise ValueError(
                            "formal_shadow_input_bundle_lock_unverified"
                        )
                elif (
                    stat.S_IMODE(root_metadata.st_mode) != 0o700
                    or entries
                    or kind != "missing"
                ):
                    raise ValueError(
                        "formal_shadow_input_bundle_lock_unverified"
                    )
            elif kind not in {"missing", "regular"}:
                raise ValueError("formal_shadow_input_bundle_lock_unverified")

            nofollow = getattr(os, "O_NOFOLLOW", None)
            if not isinstance(nofollow, int):
                raise ValueError("formal_shadow_input_bundle_lock_unverified")
            created = kind == "missing"
            flags = (
                os.O_RDONLY
                if sealed_root
                else os.O_RDWR
            ) | nofollow
            if created:
                flags |= os.O_CREAT | os.O_EXCL
            file_fd = os.open(
                self._path.name,
                flags,
                0o600,
                dir_fd=root_fd,
            )
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("formal_shadow_input_bundle_lock_unverified")
            if created:
                os.fchmod(file_fd, 0o600)
                if os.fstat(file_fd).st_nlink != 1:
                    raise ValueError("formal_shadow_input_bundle_lock_unverified")
            elif stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ValueError("formal_shadow_input_bundle_lock_unverified")
            operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(file_fd, operation)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    return False
                raise
            acquired = True
            if self._protect_root and not sealed_root:
                _set_user_immutable(file_fd)
                os.fchmod(root_fd, 0o500)
                _set_user_immutable(root_fd)
                os.fsync(file_fd)
                os.fsync(root_fd)
            elif created:
                os.fsync(file_fd)
                os.fsync(root_fd)
            if not self._verify_descriptors(root_fd, file_fd):
                raise ValueError("formal_shadow_input_bundle_lock_unverified")
            identity = self._identity_from_descriptors(root_fd, file_fd)
            self._root_fd = root_fd
            self._file_fd = file_fd
            self.identity = identity
            root_fd = None
            file_fd = None
            return True
        except ValueError:
            raise
        except (OSError, TypeError) as error:
            raise ValueError("formal_shadow_input_bundle_lock_unverified") from error
        finally:
            if file_fd is not None:
                if acquired:
                    fcntl.flock(file_fd, fcntl.LOCK_UN)
                os.close(file_fd)
            if root_fd is not None:
                os.close(root_fd)

    def assert_still_held(self) -> None:
        if (
            self._root_fd is None
            or self._file_fd is None
            or not self._verify_descriptors(self._root_fd, self._file_fd)
        ):
            raise ValueError("formal_shadow_input_bundle_lock_unverified")

    def release(self) -> None:
        file_fd, self._file_fd = self._file_fd, None
        root_fd, self._root_fd = self._root_fd, None
        self.identity = None
        try:
            if file_fd is not None:
                fcntl.flock(file_fd, fcntl.LOCK_UN)
                os.close(file_fd)
        finally:
            if root_fd is not None:
                os.close(root_fd)


def formal_shadow_collection_policy_json_bytes() -> bytes:
    """返回合同层唯一冻结的阶段10采集策略字节。"""

    policy = FormalShadowCollectionPolicy.model_validate({
        "maximumSourceAgeSecondsByModule": {
            module: 90 for module in (
                "trendRotation", "leaderObservation", "etfObservation"
            )
        },
        "maximumCollectionDelaySecondsByModule": {
            module: 300 for module in (
                "trendRotation", "leaderObservation", "etfObservation"
            )
        },
    })
    return _canonical_json_bytes(policy.model_dump(mode="json", by_alias=True))


def build_stage6_formal_shadow_input_context(
    *,
    output_root: Path,
    prepared: Any,
) -> Stage6FormalShadowInputBundleInputs:
    """从本轮内存 prepared 对象提取原始官方日历，绝不从旧文件回填。"""

    raw = getattr(prepared, "calendar_document_raw_content", None)
    raw_sha = getattr(prepared, "calendar_document_raw_content_sha256", None)
    calendar = getattr(prepared, "calendar_evidence", None)
    evidence = prepared.to_evidence() if hasattr(prepared, "to_evidence") else None
    actual = "sha256:" + hashlib.sha256(raw).hexdigest() if type(raw) is bytes else None
    fetched_at = getattr(calendar, "fetched_at", None)
    if (
        type(raw) is not bytes
        or not raw
        or raw_sha != actual
        or getattr(calendar, "content_sha256", None) != actual
        or getattr(calendar, "source_url", None) != SSE_CALENDAR_URL
        or not isinstance(fetched_at, datetime)
        or fetched_at.tzinfo is None
        or fetched_at.utcoffset() is None
        or not isinstance(evidence, Mapping)
        or evidence.get("calendarDocumentRawContentAvailable") is not True
        or evidence.get("calendarDocumentRawContentSha256") != actual
    ):
        raise Stage6FormalShadowInputBundleError(
            "leader_phase6_formal_shadow_calendar_raw_unavailable"
        )
    local_day = fetched_at.astimezone(_SHANGHAI).date()
    envelope = {
        "contractVersion": "radar-formal-shadow-calendar-input-v1",
        "market": "cn",
        "sourceName": "上海证券交易所",
        "sourceUrl": SSE_CALENDAR_URL,
        "year": local_day.year,
        "fetchedAt": fetched_at.isoformat(),
        "observedThrough": local_day.isoformat(),
        "sourceDocumentSha256": actual.removeprefix("sha256:"),
    }
    return Stage6FormalShadowInputBundleInputs(
        output_root=output_root,
        collection_policy_json_bytes=formal_shadow_collection_policy_json_bytes(),
        calendar_envelope_json_bytes=_canonical_json_bytes(envelope),
        calendar_document_bytes=raw,
    )


def _strict_json_object(raw: bytes, reason: str) -> Dict[str, Any]:
    if type(raw) is not bytes or not raw:
        raise Stage6FormalShadowInputBundleError(reason)

    def no_duplicate(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicate,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non_finite_json_number")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise Stage6FormalShadowInputBundleError(reason) from error
    if not isinstance(value, dict):
        raise Stage6FormalShadowInputBundleError(reason)
    return value


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_receipt_unverified"
        ) from error


def _as_mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Stage6FormalShadowInputBundleError(reason)
    return value


def _parse_aware_datetime(value: Any, reason: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Stage6FormalShadowInputBundleError(reason)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise Stage6FormalShadowInputBundleError(reason) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Stage6FormalShadowInputBundleError(reason)
    return parsed


def _positive_int(value: Any, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Stage6FormalShadowInputBundleError(reason)
    return value


def _nonnegative_int(value: Any, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Stage6FormalShadowInputBundleError(reason)
    return value


def _extract_common_stage6_times(
    artifact: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    state = _as_mapping(
        artifact.get("marketResearchState"),
        "formal_shadow_stage6_artifact_unverified",
    )
    evidence = _as_mapping(
        artifact.get("marketFeatureSnapshotEvidence"),
        "formal_shadow_stage6_artifact_unverified",
    )
    snapshot = _as_mapping(
        evidence.get("snapshot"),
        "formal_shadow_stage6_artifact_unverified",
    )
    run_id = state.get("radarRunId")
    as_of_time = _parse_aware_datetime(
        state.get("asOf"), "formal_shadow_stage6_artifact_unverified"
    )
    snapshot_as_of = _parse_aware_datetime(
        snapshot.get("asOf"), "formal_shadow_stage6_artifact_unverified"
    )
    source_time = _parse_aware_datetime(
        snapshot.get("sourceTime"), "formal_shadow_stage6_artifact_unverified"
    )
    fetched_at = _parse_aware_datetime(
        snapshot.get("fetchedAt"), "formal_shadow_stage6_artifact_unverified"
    )
    times = (as_of_time, snapshot_as_of, source_time, fetched_at)
    if (
        not isinstance(run_id, str)
        or not run_id.strip()
        or any(value.tzinfo is None or value.utcoffset() is None for value in times)
        or snapshot.get("radarRunId") != run_id
        or snapshot_as_of != as_of_time
    ):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_artifact_unverified"
        )
    return (
        run_id,
        as_of_time.isoformat(),
        source_time.isoformat(),
        fetched_at.isoformat(),
    )


def _receipt_bytes(
    *,
    module: str,
    run_id: str,
    as_of: str,
    source_time: str,
    fetched_at: str,
    observed_at: datetime,
    expected_count: int,
    lock_acquired: bool,
    duration_ms: int,
    source_artifact_json_bytes: bytes,
) -> bytes:
    if not isinstance(lock_acquired, bool):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_lock_state_unverified"
        )
    _positive_int(expected_count, "formal_shadow_stage6_coverage_unverified")
    _nonnegative_int(duration_ms, "formal_shadow_stage6_duration_unverified")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_observed_at_unverified"
        )
    payload = {
        "contractId": "radar-formal-shadow-run-receipt-v1",
        "module": module,
        "runId": run_id,
        "asOf": as_of,
        "sourceTime": source_time,
        "fetchedAt": fetched_at,
        "observedAt": observed_at.isoformat(),
        "sourceContractId": MODULE_SOURCE_CONTRACTS[module],
        "sourceArtifactSha256": hashlib.sha256(
            source_artifact_json_bytes
        ).hexdigest(),
        "coverageScope": MODULE_COVERAGE_SCOPES[module],
        "expectedCount": expected_count,
        "observedCount": expected_count,
        "missingCount": 0,
        "failedCount": 0,
        "staleCount": 0,
        "coverage": 1.0,
        "lockState": "acquired" if lock_acquired else "contended",
        "durationMs": duration_ms,
        "sourceReady": lock_acquired,
    }
    try:
        verified = FormalShadowRunReceipt.model_validate(payload)
    except (TypeError, ValueError) as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_receipt_unverified"
        ) from error
    return _canonical_json_bytes(verified.model_dump(mode="json", by_alias=True))


def derive_stage6_run_receipts(
    *,
    source_artifact_json_bytes: bytes,
    sector_snapshot_json_bytes: bytes,
    observed_at: datetime,
    duration_ms: int,
    lock_acquired: bool,
    collection_policy_json_bytes: Optional[bytes] = None,
) -> Mapping[str, bytes]:
    """从本次阶段6原始工件派生趋势/龙头回执，不接受旧结果拼接。"""

    artifact = _strict_json_object(
        source_artifact_json_bytes,
        "formal_shadow_stage6_artifact_unverified",
    )
    sector = _strict_json_object(
        sector_snapshot_json_bytes,
        "formal_shadow_stage6_sector_snapshot_unverified",
    )
    run_id, as_of, source_time, fetched_at = _extract_common_stage6_times(
        artifact
    )
    prepared = _as_mapping(
        artifact.get("prepared"),
        "formal_shadow_stage6_artifact_unverified",
    )
    tradability = _as_mapping(
        artifact.get("tradability"),
        "formal_shadow_stage6_artifact_unverified",
    )
    if any((
        prepared.get("contractId")
        != "radar-leader-phase6-prepared-historical-inputs-v1",
        prepared.get("radarRunId") != run_id,
        tradability.get("contractId")
        != "radar-leader-tradability-live-acceptance-v1",
        tradability.get("status") != "completed",
        tradability.get("radarRunId") != run_id,
        _parse_aware_datetime(
            tradability.get("asOf"),
            "formal_shadow_stage6_artifact_unverified",
        ) != _parse_aware_datetime(
            as_of, "formal_shadow_stage6_artifact_unverified"
        ),
        tradability.get("gate") != _CLOSED_GATE,
        artifact.get("gate") != _CLOSED_GATE,
    )):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_artifact_unverified"
        )
    policy_raw = (
        collection_policy_json_bytes
        if collection_policy_json_bytes is not None
        else formal_shadow_collection_policy_json_bytes()
    )
    try:
        load_formal_shadow_collection_policy(policy_raw)
    except ValueError as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_stage6_collection_policy_unverified"
        ) from error
    results: Dict[str, bytes] = {}

    codes = sector.get("industryCodes")
    records = sector.get("records")
    state = _as_mapping(
        artifact.get("marketResearchState"),
        "formal_shadow_stage6_artifact_unverified",
    )
    if (
        isinstance(codes, list)
        and isinstance(records, list)
        and len(codes) > 0
        and len(records) == len(codes)
        and state.get("status") == "ready"
    ):
        trend_receipt = _receipt_bytes(
            module="trendRotation",
            run_id=run_id,
            as_of=as_of,
            source_time=source_time,
            fetched_at=fetched_at,
            observed_at=observed_at,
            expected_count=len(codes),
            lock_acquired=lock_acquired,
            duration_ms=duration_ms,
            source_artifact_json_bytes=source_artifact_json_bytes,
        )
        try:
            adapt_trend_rotation_observation(
                trend_receipt,
                source_artifact_json_bytes,
                sector_snapshot_json_bytes,
                evaluated_at=observed_at,
                collection_policy_json_bytes=policy_raw,
            )
        except ValueError:
            pass
        else:
            results["trendRotation"] = trend_receipt

    selection = artifact.get("evidenceCandidateSelection")
    collection = artifact.get("collection")
    if isinstance(selection, dict) and isinstance(collection, dict):
        plan = selection.get("evidencePlan")
        review = collection.get("stateDecisionReview")
        if isinstance(plan, dict) and isinstance(review, dict):
            items = plan.get("items")
            parent_count = review.get("parentCandidateCount")
            if (
                selection.get("status") == "ready"
                and plan.get("status") == "ready"
                and collection.get("status") == "ready_for_review"
                and isinstance(items, list)
                and len(items) > 0
                and isinstance(parent_count, int)
                and not isinstance(parent_count, bool)
                and parent_count == len(items)
            ):
                leader_receipt = _receipt_bytes(
                    module="leaderObservation",
                    run_id=run_id,
                    as_of=as_of,
                    source_time=source_time,
                    fetched_at=fetched_at,
                    observed_at=observed_at,
                    expected_count=parent_count,
                    lock_acquired=lock_acquired,
                    duration_ms=duration_ms,
                    source_artifact_json_bytes=source_artifact_json_bytes,
                )
                try:
                    adapt_leader_observation(
                        leader_receipt,
                        source_artifact_json_bytes,
                        evaluated_at=observed_at,
                        collection_policy_json_bytes=policy_raw,
                    )
                except ValueError:
                    pass
                else:
                    results["leaderObservation"] = leader_receipt
    return results


def _private_tmp_root(value: Path) -> Path:
    if not isinstance(value, Path):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_root_unverified"
        )
    resolved = _explicit_root(value)
    try:
        resolved.relative_to(_PRIVATE_TMP)
    except ValueError as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_root_unverified"
        ) from error
    return resolved


def _write_bytes_at(directory_fd: int, name: str, payload: bytes) -> None:
    if type(payload) is not bytes:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_bytes_required"
        )
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_secure_flags_unavailable"
        )
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise Stage6FormalShadowInputBundleError(
                "formal_shadow_input_bundle_file_unverified"
            )
    except Stage6FormalShadowInputBundleError:
        raise
    except (OSError, TypeError) as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_file_unverified"
        ) from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _create_directory_at(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        descriptor = _open_directory(name, dir_fd=parent_fd)
        os.fchmod(descriptor, 0o700)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
            os.close(descriptor)
            raise Stage6FormalShadowInputBundleError(
                "formal_shadow_input_bundle_directory_unverified"
            )
        return descriptor
    except Stage6FormalShadowInputBundleError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_directory_unverified"
        ) from error


def _remove_staging_tree(root_fd: int, staging_name: str) -> None:
    """只删除本函数刚创建的已知空/文件树；清理失败不覆盖原异常。"""

    try:
        staging_fd = _open_directory(staging_name, dir_fd=root_fd)
    except (OSError, TypeError, ValueError):
        return
    try:
        for module in _MODULE_ORDER:
            if _entry_kind(staging_fd, module) != "directory":
                continue
            try:
                module_fd = _open_directory(module, dir_fd=staging_fd)
            except (OSError, TypeError, ValueError):
                continue
            try:
                for filename in (
                    RECEIPT_FILENAME,
                    SOURCE_ARTIFACT_FILENAME,
                    COLLECTION_POLICY_FILENAME,
                    CALENDAR_INPUT_FILENAME,
                    CALENDAR_DOCUMENT_FILENAME,
                    TREND_SUPPORTING_FILENAME,
                ):
                    try:
                        os.unlink(filename, dir_fd=module_fd)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
            finally:
                os.close(module_fd)
            try:
                os.rmdir(module, dir_fd=staging_fd)
            except OSError:
                pass
    finally:
        os.close(staging_fd)
    try:
        os.rmdir(staging_name, dir_fd=root_fd)
        os.fsync(root_fd)
    except OSError:
        pass


def _remove_flat_staging_tree(
    root_fd: int,
    staging_name: str,
    filenames: tuple[str, ...],
) -> None:
    """清理本模块创建但尚未发布的 ETF 平铺暂存目录。"""

    try:
        staging_fd = _open_directory(staging_name, dir_fd=root_fd)
    except (OSError, TypeError, ValueError):
        return
    try:
        for filename in filenames:
            try:
                os.unlink(filename, dir_fd=staging_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    finally:
        os.close(staging_fd)
    try:
        os.rmdir(staging_name, dir_fd=root_fd)
        os.fsync(root_fd)
    except OSError:
        pass


def _publish_flat_input_directory(
    *,
    root: Path,
    payloads: Mapping[str, bytes],
    name_prefix: str,
) -> Path:
    """用同一安全原语一次性公开一个平铺输入目录。"""

    root_fd: Optional[int] = None
    staging_name: Optional[str] = None
    filenames = tuple(payloads)
    try:
        root_fd = _open_root_directory(root, create_missing=True)
        _verify_root_path(root, root_fd)
        staging_name = f".{name_prefix}{secrets.token_hex(16)}.staging"
        staging_fd = _create_directory_at(root_fd, staging_name)
        try:
            for filename, payload in payloads.items():
                _write_bytes_at(staging_fd, filename, payload)
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
        _verify_root_path(root, root_fd)
        final_name = name_prefix + secrets.token_hex(16)
        if _entry_kind(root_fd, final_name) != "missing":
            raise Stage6FormalShadowInputBundleError(
                "formal_shadow_input_bundle_target_exists"
            )
        os.rename(
            staging_name,
            final_name,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        staging_name = None
        os.fsync(root_fd)
        return root / final_name
    except Stage6FormalShadowInputBundleError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_publish_failed"
        ) from error
    finally:
        if root_fd is not None and staging_name is not None:
            _remove_flat_staging_tree(root_fd, staging_name, filenames)
        if root_fd is not None:
            os.close(root_fd)


def publish_stage6_formal_shadow_input_bundles(
    *,
    output_root: Path,
    source_artifact_json_bytes: bytes,
    sector_snapshot_json_bytes: bytes,
    collection_policy_json_bytes: bytes,
    calendar_envelope_json_bytes: bytes,
    calendar_document_bytes: bytes,
    observed_at: datetime,
    duration_ms: int,
    lock_acquired: bool,
) -> Stage6FormalShadowInputBundlePublication:
    """原子发布每个已完整的阶段6模块输入包。

    日历、策略和来源工件在创建任何目录前先被强类型复核；任一缺失或异常
    均不留下可供离线入口误读的最终目录。
    """

    root = _private_tmp_root(output_root)
    if type(calendar_document_bytes) is not bytes:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_document_bytes_required"
        )
    if len(calendar_document_bytes) > _MAX_CALENDAR_DOCUMENT_BYTES:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_document_too_large"
        )
    try:
        load_formal_shadow_collection_policy(collection_policy_json_bytes)
        calendar_evidence = load_official_sse_calendar_evidence(
            calendar_envelope_json_bytes,
            calendar_document_bytes,
        )
    except ValueError as error:
        raise Stage6FormalShadowInputBundleError(str(error)) from error
    artifact = _strict_json_object(
        source_artifact_json_bytes,
        "formal_shadow_stage6_artifact_unverified",
    )
    prepared = _as_mapping(
        artifact.get("prepared"),
        "formal_shadow_stage6_artifact_unverified",
    )
    _, as_of_raw, _, _ = _extract_common_stage6_times(artifact)
    try:
        as_of = _parse_aware_datetime(
            as_of_raw, "formal_shadow_calendar_prepared_binding_mismatch"
        )
        prepared_at = _parse_aware_datetime(
            prepared.get("preparedAt"),
            "formal_shadow_calendar_prepared_binding_mismatch",
        )
    except Stage6FormalShadowInputBundleError as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_prepared_binding_mismatch"
        ) from error
    actual_prefixed_sha = "sha256:" + hashlib.sha256(
        calendar_document_bytes
    ).hexdigest()
    local_day = as_of.astimezone(_SHANGHAI).date()
    if any((
        prepared.get("calendarDocumentRawContentAvailable") is not True,
        prepared.get("calendarDocumentRawContentSha256") != actual_prefixed_sha,
        calendar_evidence.source_url != SSE_CALENDAR_URL,
        calendar_evidence.source_document_sha256
        != actual_prefixed_sha.removeprefix("sha256:"),
        calendar_evidence.observed_through != local_day,
        calendar_evidence.fetched_at.astimezone(_SHANGHAI).date() != local_day,
        prepared_at.tzinfo is None,
        prepared_at.tzinfo is not None and prepared_at.utcoffset() is None,
        prepared_at.tzinfo is not None
        and not calendar_evidence.fetched_at <= prepared_at <= as_of,
    )):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_prepared_binding_mismatch"
        )
    receipts = derive_stage6_run_receipts(
        source_artifact_json_bytes=source_artifact_json_bytes,
        sector_snapshot_json_bytes=sector_snapshot_json_bytes,
        observed_at=observed_at,
        duration_ms=duration_ms,
        lock_acquired=lock_acquired,
        collection_policy_json_bytes=collection_policy_json_bytes,
    )
    # 锁竞争、合法空池或失败来源只能留下零发布结果，不能写“未就绪”半包。
    if not lock_acquired or not receipts:
        return Stage6FormalShadowInputBundlePublication(input_dirs={})

    root_fd: Optional[int] = None
    staging_name: Optional[str] = None
    final_name: Optional[str] = None
    try:
        root_fd = _open_root_directory(root, create_missing=True)
        _verify_root_path(root, root_fd)
        staging_name = ".stage6-formal-shadow-input-" + secrets.token_hex(16) + ".staging"
        staging_fd = _create_directory_at(root_fd, staging_name)
        try:
            input_names = []
            for module in _MODULE_ORDER:
                receipt = receipts.get(module)
                if receipt is None:
                    continue
                module_fd = _create_directory_at(staging_fd, module)
                try:
                    payloads = {
                        RECEIPT_FILENAME: receipt,
                        SOURCE_ARTIFACT_FILENAME: source_artifact_json_bytes,
                        COLLECTION_POLICY_FILENAME: collection_policy_json_bytes,
                        CALENDAR_INPUT_FILENAME: calendar_envelope_json_bytes,
                        CALENDAR_DOCUMENT_FILENAME: calendar_document_bytes,
                    }
                    if module == "trendRotation":
                        payloads[TREND_SUPPORTING_FILENAME] = sector_snapshot_json_bytes
                    for filename, payload in payloads.items():
                        _write_bytes_at(module_fd, filename, payload)
                    os.fsync(module_fd)
                    input_names.append(module)
                finally:
                    os.close(module_fd)
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
        _verify_root_path(root, root_fd)
        final_name = "stage6-formal-shadow-input-" + secrets.token_hex(16)
        if _entry_kind(root_fd, final_name) != "missing":
            raise Stage6FormalShadowInputBundleError(
                "formal_shadow_input_bundle_target_exists"
            )
        os.rename(
            staging_name,
            final_name,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        staging_name = None
        os.fsync(root_fd)
        final_path = root / final_name
        return Stage6FormalShadowInputBundlePublication(
            input_dirs={module: final_path / module for module in input_names}
        )
    except Stage6FormalShadowInputBundleError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_input_bundle_publish_failed"
        ) from error
    finally:
        if root_fd is not None and staging_name is not None:
            _remove_staging_tree(root_fd, staging_name)
        if root_fd is not None:
            os.close(root_fd)


def publish_etf_formal_shadow_input_bundle(
    *,
    output_root: Path,
    capture: EtfLiveShadowCapture,
    formal_admission_json_bytes: bytes,
    collection_policy_json_bytes: bytes,
    calendar_envelope_json_bytes: bytes,
    calendar_document_bytes: bytes,
    evaluated_at: datetime,
) -> EtfFormalShadowInputBundlePublication:
    """复核并原子发布一次 ETF 现场捕获，供正式影子入口直接消费。"""

    root = _private_tmp_root(output_root)
    if type(capture) is not EtfLiveShadowCapture:
        raise Stage6FormalShadowInputBundleError(
            "etf_live_shadow_capture_unverified"
        )
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_evaluated_at_timezone_required"
        )
    if type(calendar_document_bytes) is not bytes:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_document_bytes_required"
        )
    if len(calendar_document_bytes) > _MAX_CALENDAR_DOCUMENT_BYTES:
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_document_too_large"
        )

    try:
        source_payload = _strict_json_object(
            capture.source_json_bytes,
            "etf_live_shadow_capture_unverified",
        )
        artifact = EtfLiveShadowObservationArtifact.model_validate(
            source_payload
        )
        receipt = load_formal_shadow_run_receipt(capture.receipt_json_bytes)
        if any((
            artifact != capture.artifact,
            receipt != capture.receipt,
            capture.source_sha256
            != hashlib.sha256(capture.source_json_bytes).hexdigest(),
        )):
            raise Stage6FormalShadowInputBundleError(
                "etf_live_shadow_capture_unverified"
            )
        load_formal_shadow_collection_policy(collection_policy_json_bytes)
        calendar_evidence = load_official_sse_calendar_evidence(
            calendar_envelope_json_bytes,
            calendar_document_bytes,
        )
        adapt_etf_observation(
            capture.receipt_json_bytes,
            capture.source_json_bytes,
            formal_admission_json_bytes,
            evaluated_at=evaluated_at,
            collection_policy_json_bytes=collection_policy_json_bytes,
        )
    except Stage6FormalShadowInputBundleError:
        raise
    except (TypeError, ValueError) as error:
        message = str(error)
        if message in {
            "formal_shadow_calendar_observation_date_mismatch",
            "formal_shadow_calendar_document_hash_mismatch",
        }:
            raise Stage6FormalShadowInputBundleError(message) from error
        raise Stage6FormalShadowInputBundleError(
            "etf_live_shadow_capture_unverified"
        ) from error

    local_day = receipt.as_of.astimezone(_SHANGHAI).date()
    if (
        calendar_evidence.source_url != SSE_CALENDAR_URL
        or calendar_evidence.observed_through != local_day
        or calendar_evidence.fetched_at.astimezone(_SHANGHAI).date()
        != local_day
        or calendar_evidence.fetched_at > receipt.observed_at
        or receipt.observed_at > evaluated_at
    ):
        raise Stage6FormalShadowInputBundleError(
            "formal_shadow_calendar_observation_date_mismatch"
        )

    input_dir = _publish_flat_input_directory(
        root=root,
        name_prefix="etf-formal-shadow-input-",
        payloads={
            RECEIPT_FILENAME: capture.receipt_json_bytes,
            SOURCE_ARTIFACT_FILENAME: capture.source_json_bytes,
            COLLECTION_POLICY_FILENAME: collection_policy_json_bytes,
            CALENDAR_INPUT_FILENAME: calendar_envelope_json_bytes,
            CALENDAR_DOCUMENT_FILENAME: calendar_document_bytes,
            ETF_ADMISSION_FILENAME: formal_admission_json_bytes,
        },
    )
    return EtfFormalShadowInputBundlePublication(
        module="etfObservation",
        input_dir=input_dir,
    )
