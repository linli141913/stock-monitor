"""阶段10正式就绪报告的显式目录内容寻址仓。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from radar.formal_readiness_contracts import RadarFormalReadiness


FORMAL_READINESS_MANIFEST_CONTRACT_ID = "radar-formal-readiness-manifest-v1"
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_REPORT_BYTES = 8 * 1024 * 1024
_FIXED_SYSTEM_ROOT_ALIASES = {
    Path("/var"): Path("/private/var"),
    Path("/tmp"): Path("/private/tmp"),
}


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def formal_readiness_content_sha256(report: RadarFormalReadiness) -> str:
    """按内容仓唯一规范 JSON 口径计算报告哈希。"""

    if not isinstance(report, RadarFormalReadiness):
        raise TypeError("formal_readiness_report_invalid")
    payload = report.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _verified_system_root_alias(alias: Path, expected: Path) -> bool:
    try:
        alias_metadata = alias.lstat()
        if not stat.S_ISLNK(alias_metadata.st_mode):
            return False
        raw_target = Path(os.readlink(alias))
        candidate = (
            raw_target
            if raw_target.is_absolute()
            else alias.parent / raw_target
        )
        candidate = Path(os.path.abspath(os.fspath(candidate)))
        if candidate != expected:
            return False
        expected_metadata = expected.lstat()
        followed_metadata = alias.stat()
    except (OSError, TypeError, ValueError):
        return False
    return (
        stat.S_ISDIR(expected_metadata.st_mode)
        and not stat.S_ISLNK(expected_metadata.st_mode)
        and (expected_metadata.st_dev, expected_metadata.st_ino)
        == (followed_metadata.st_dev, followed_metadata.st_ino)
    )


def _normalize_fixed_system_root_alias(path: Path) -> Path:
    if len(path.parts) < 2:
        return path
    first_component = Path(path.anchor) / path.parts[1]
    expected = _FIXED_SYSTEM_ROOT_ALIASES.get(first_component)
    if expected is None or not _verified_system_root_alias(
        first_component,
        expected,
    ):
        return path
    return expected.joinpath(*path.parts[2:])


def _explicit_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("formal_readiness_root_must_be_path")
    absolute = Path(os.path.abspath(os.fspath(root.expanduser())))
    return _normalize_fixed_system_root_alias(absolute)


def _secure_directory_flags() -> int:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(directory_flag, int) or not isinstance(nofollow_flag, int):
        raise ValueError("formal_readiness_secure_flags_unavailable")
    return os.O_RDONLY | directory_flag | nofollow_flag


def _open_directory(path: Path | str, *, dir_fd: Optional[int] = None) -> int:
    flags = _secure_directory_flags()
    try:
        if dir_fd is None:
            descriptor = os.open(os.fspath(path), flags)
        else:
            descriptor = os.open(os.fspath(path), flags, dir_fd=dir_fd)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("formal_readiness_store_path_unverified")
        return descriptor
    except ValueError:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise
    except (OSError, TypeError):
        raise ValueError("formal_readiness_store_path_unverified") from None


def _same_directory(left_fd: int, right_fd: int) -> bool:
    left = os.fstat(left_fd)
    right = os.fstat(right_fd)
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _verify_directory_path(path: Path, directory_fd: int) -> None:
    try:
        probe = _open_root_directory(path, create_missing=False)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        raise ValueError("formal_readiness_store_path_unverified") from None
    try:
        if not _same_directory(directory_fd, probe):
            raise ValueError("formal_readiness_store_path_unverified")
    except (OSError, TypeError):
        raise ValueError("formal_readiness_store_path_unverified") from None
    finally:
        os.close(probe)


def _verify_directory_entry(
    parent_fd: int,
    name: str,
    directory_fd: int,
) -> None:
    try:
        probe = _open_directory(name, dir_fd=parent_fd)
    except (OSError, TypeError, ValueError):
        raise ValueError("formal_readiness_store_path_unverified") from None
    try:
        if not _same_directory(directory_fd, probe):
            raise ValueError("formal_readiness_store_path_unverified")
    except (OSError, TypeError):
        raise ValueError("formal_readiness_store_path_unverified") from None
    finally:
        os.close(probe)


def _entry_kind(directory_fd: int, name: str) -> str:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "invalid"
    if stat.S_ISREG(metadata.st_mode):
        return "regular"
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    return "invalid"


def _open_root_directory(path: Path, *, create_missing: bool) -> int:
    """从文件系统根逐段打开目标，拒绝任意 symlink 或非目录祖先。"""

    if not path.is_absolute() or not path.anchor:
        raise ValueError("formal_readiness_store_path_unverified")
    current_fd = _open_directory(Path(path.anchor))
    try:
        for component in path.parts[1:]:
            kind = _entry_kind(current_fd, component)
            if kind == "missing":
                if not create_missing:
                    raise FileNotFoundError(os.fspath(path))
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except (OSError, TypeError) as error:
                    raise ValueError(
                        "formal_readiness_store_path_unverified",
                    ) from error
                kind = _entry_kind(current_fd, component)
            if kind != "directory":
                raise ValueError("formal_readiness_store_path_unverified")
            child_fd = _open_directory(component, dir_fd=current_fd)
            try:
                _verify_directory_entry(current_fd, component, child_fd)
            except BaseException:
                os.close(child_fd)
                raise
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_regular_file_at(directory_fd: int, name: str) -> tuple[int, os.stat_result]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow_flag, int):
        raise ValueError("formal_readiness_secure_flags_unavailable")
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow_flag,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("formal_readiness_store_path_unverified")
        return descriptor, metadata
    except FileNotFoundError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except (OSError, TypeError, ValueError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise ValueError("formal_readiness_store_path_unverified") from None


def _verify_regular_file_entry(
    directory_fd: int,
    name: str,
    expected: os.stat_result,
) -> None:
    try:
        descriptor, actual = _open_regular_file_at(directory_fd, name)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        raise ValueError("formal_readiness_store_path_unverified") from None
    try:
        if _stable_file_identity(actual) != _stable_file_identity(expected):
            raise ValueError("formal_readiness_store_path_unverified")
    except (OSError, TypeError):
        raise ValueError("formal_readiness_store_path_unverified") from None
    finally:
        os.close(descriptor)


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_bytes_at(
    directory_fd: int,
    name: str,
    *,
    max_bytes: int,
) -> tuple[bytes, os.stat_result]:
    descriptor, metadata = _open_regular_file_at(directory_fd, name)
    try:
        if metadata.st_size > max_bytes:
            raise ValueError("formal_readiness_file_too_large")
        chunks = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(64 * 1024, max_bytes + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("formal_readiness_file_too_large")
        after = os.fstat(descriptor)
        if (
            _stable_file_identity(metadata) != _stable_file_identity(after)
            or total != after.st_size
        ):
            raise ValueError("formal_readiness_store_path_unverified")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _read_json_at(
    directory_fd: int,
    name: str,
    *,
    max_bytes: int,
) -> tuple[object, os.stat_result]:
    raw, metadata = _read_bytes_at(
        directory_fd,
        name,
        max_bytes=max_bytes,
    )
    return json.load(io.StringIO(raw.decode("utf-8"))), metadata


def _write_atomic_at(directory_fd: int, target_name: str, payload: dict) -> None:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow_flag, int):
        raise ValueError("formal_readiness_secure_flags_unavailable")
    descriptor: Optional[int] = None
    temporary_name: Optional[str] = None
    for _ in range(32):
        candidate = f".{target_name}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag,
                0o600,
                dir_fd=directory_fd,
            )
            temporary_name = candidate
            break
        except FileExistsError:
            continue
    if descriptor is None or temporary_name is None:
        raise OSError("formal_readiness_temporary_file_unavailable")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            file.flush()
            os.fsync(file.fileno())
        descriptor = None
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        try:
            os.fsync(directory_fd)
        finally:
            raise


@dataclass(frozen=True)
class StoredFormalReadinessRef:
    status: str
    content_sha256: str
    relative_path: str
    checked_at: datetime

    @property
    def report_relative_path(self) -> str:
        return self.relative_path


@dataclass(frozen=True)
class FormalReadinessLoadResult:
    status: str
    reason_codes: Tuple[str, ...] = ()
    report: Optional[RadarFormalReadiness] = None
    stored_ref: Optional[StoredFormalReadinessRef] = None


def _path_kind(path: Path) -> str:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "invalid"
    if stat.S_ISREG(metadata.st_mode):
        return "regular"
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    return "invalid"


def _open_save_directories(store_root: Path) -> tuple[int, int]:
    root_fd = _open_root_directory(store_root, create_missing=True)
    try:
        _verify_directory_path(store_root, root_fd)
        if _entry_kind(root_fd, "latest.json") not in {"missing", "regular"}:
            raise ValueError("formal_readiness_store_path_unverified")
        reports_kind = _entry_kind(root_fd, "reports")
        if reports_kind == "missing":
            os.mkdir("reports", mode=0o700, dir_fd=root_fd)
        elif reports_kind != "directory":
            raise ValueError("formal_readiness_store_path_unverified")
        reports_fd = _open_directory("reports", dir_fd=root_fd)
        try:
            _verify_directory_entry(root_fd, "reports", reports_fd)
        except BaseException:
            os.close(reports_fd)
            raise
        return root_fd, reports_fd
    except BaseException:
        os.close(root_fd)
        raise


def save_formal_readiness(
    report: RadarFormalReadiness,
    root: Path,
) -> StoredFormalReadinessRef:
    """原子发布报告及其相对路径清单；目录必须由调用方显式给出。"""

    if not isinstance(report, RadarFormalReadiness):
        raise TypeError("formal_readiness_report_invalid")
    store_root = _explicit_root(root)
    report = RadarFormalReadiness.model_validate(
        report.model_dump(mode="json", by_alias=True, warnings="none"),
    )
    payload = report.model_dump(mode="json", by_alias=True)
    content_sha256 = formal_readiness_content_sha256(report)
    relative = Path("reports") / f"{content_sha256}.json"
    root_fd, reports_fd = _open_save_directories(store_root)
    try:
        if _entry_kind(reports_fd, relative.name) not in {"missing", "regular"}:
            raise ValueError("formal_readiness_store_path_unverified")
        _write_atomic_at(reports_fd, relative.name, payload)
        _verify_directory_path(store_root, root_fd)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        if _entry_kind(root_fd, "latest.json") not in {"missing", "regular"}:
            raise ValueError("formal_readiness_store_path_unverified")
        _write_atomic_at(root_fd, "latest.json", {
            "contractId": FORMAL_READINESS_MANIFEST_CONTRACT_ID,
            "reportRelativePath": relative.as_posix(),
            "contentSha256": content_sha256,
            "checkedAt": report.checked_at.isoformat(),
            "state": report.state,
            "stage9ReplayRunId": report.stage9_replay_run_id,
        })
        _verify_directory_path(store_root, root_fd)
        _verify_directory_entry(root_fd, "reports", reports_fd)
    finally:
        os.close(reports_fd)
        os.close(root_fd)
    return StoredFormalReadinessRef(
        status="available",
        content_sha256=content_sha256,
        relative_path=relative.as_posix(),
        checked_at=report.checked_at,
    )


def _failed(reason: str) -> FormalReadinessLoadResult:
    return FormalReadinessLoadResult(status="failed", reason_codes=(reason,))


def _validated_relative_path(root: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("formal_readiness_manifest_unverified")
    relative = Path(raw_path)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError("formal_readiness_manifest_unverified")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("formal_readiness_manifest_unverified") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("formal_readiness_manifest_unverified")
    return candidate


def _regular_file_kind(path: Path) -> str:
    kind = _path_kind(path)
    return kind if kind in {"missing", "regular"} else "invalid"


def load_latest_formal_readiness(root: Path) -> FormalReadinessLoadResult:
    """重放清单、哈希及内层Pydantic合同；任何不一致均失败关闭。"""

    store_root = _explicit_root(root)
    root_fd: Optional[int] = None
    reports_fd: Optional[int] = None
    try:
        root_fd = _open_root_directory(store_root, create_missing=False)
    except FileNotFoundError:
        return FormalReadinessLoadResult(
            status="missing",
            reason_codes=("formal_readiness_report_missing",),
        )
    except (OSError, TypeError, ValueError):
        return _failed("formal_readiness_manifest_unverified")
    try:
        _verify_directory_path(store_root, root_fd)
        try:
            manifest, manifest_metadata = _read_json_at(
                root_fd,
                "latest.json",
                max_bytes=_MAX_MANIFEST_BYTES,
            )
        except FileNotFoundError:
            return FormalReadinessLoadResult(
                status="missing",
                reason_codes=("formal_readiness_report_missing",),
            )
        _verify_regular_file_entry(root_fd, "latest.json", manifest_metadata)
        _verify_directory_path(store_root, root_fd)
        if not isinstance(manifest, dict) or (
            manifest.get("contractId") != FORMAL_READINESS_MANIFEST_CONTRACT_ID
        ):
            return _failed("formal_readiness_manifest_unverified")
        expected = manifest["contentSha256"]
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            return _failed("formal_readiness_manifest_unverified")
        if manifest.get("reportRelativePath") != f"reports/{expected}.json":
            return _failed("formal_readiness_manifest_unverified")

        reports_kind = _entry_kind(root_fd, "reports")
        if reports_kind == "missing":
            return _failed("formal_readiness_report_missing")
        if reports_kind != "directory":
            return _failed("formal_readiness_report_unverified")
        reports_fd = _open_directory("reports", dir_fd=root_fd)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        report_name = f"{expected}.json"
        try:
            payload, report_metadata = _read_json_at(
                reports_fd,
                report_name,
                max_bytes=_MAX_REPORT_BYTES,
            )
        except FileNotFoundError:
            return _failed("formal_readiness_report_missing")
        except json.JSONDecodeError:
            return _failed("formal_readiness_report_unverified")
        _verify_regular_file_entry(reports_fd, report_name, report_metadata)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        _verify_regular_file_entry(root_fd, "latest.json", manifest_metadata)
        _verify_directory_path(store_root, root_fd)
    except json.JSONDecodeError:
        return _failed("formal_readiness_manifest_unverified")
    except (KeyError, OSError, TypeError, ValueError):
        return _failed("formal_readiness_manifest_unverified")
    finally:
        if reports_fd is not None:
            os.close(reports_fd)
        if root_fd is not None:
            os.close(root_fd)

    try:
        actual = hashlib.sha256(_canonical_json(payload)).hexdigest()
    except (TypeError, ValueError):
        return _failed("formal_readiness_report_unverified")
    if actual != expected:
        return _failed("formal_readiness_report_hash_mismatch")
    try:
        report = RadarFormalReadiness.model_validate(payload)
    except Exception:
        return _failed("formal_readiness_report_unverified")
    if (
        manifest.get("checkedAt") != report.checked_at.isoformat()
        or manifest.get("state") != report.state
        or manifest.get("stage9ReplayRunId") != report.stage9_replay_run_id
    ):
        return _failed("formal_readiness_manifest_identity_mismatch")
    relative = Path(manifest["reportRelativePath"])
    reference = StoredFormalReadinessRef(
        status="available",
        content_sha256=expected,
        relative_path=relative.as_posix(),
        checked_at=report.checked_at,
    )
    return FormalReadinessLoadResult(
        status="available",
        report=report,
        stored_ref=reference,
    )
