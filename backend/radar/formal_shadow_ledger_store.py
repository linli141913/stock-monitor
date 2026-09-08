"""阶段10影子观察 v2 台账的显式内容寻址仓。

此模块只接收已经由合同层构建完成的 ``FormalShadowLedgerV2``；它既不读取
数据库也不发网络请求。所有路径均通过目录描述符逐段验证，避免用户路径中的
符号链接、文件替换或目录替换把工件写到台账根目录之外。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Tuple

from radar.formal_shadow_calendar import load_official_sse_calendar_evidence
from radar.formal_shadow_ledger import FormalShadowLedgerV2
from radar.strict_json import strict_json_loads


FORMAL_SHADOW_LEDGER_MANIFEST_CONTRACT_ID = "radar-formal-shadow-ledger-manifest-v1"
_LOCK_NAME = ".formal-shadow-ledger.lock"
_FIXED_SYSTEM_ROOT_ALIASES = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_LEDGER_BYTES = 8 * 1024 * 1024
_MAX_CALENDAR_DOCUMENT_BYTES = 2 * 1024 * 1024


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def formal_shadow_ledger_content_sha256(ledger: FormalShadowLedgerV2) -> str:
    """按唯一规范 JSON 计算 v2 台账内容哈希。"""
    if not isinstance(ledger, FormalShadowLedgerV2):
        raise TypeError("formal_shadow_ledger_v2_required")
    return hashlib.sha256(_canonical_json(ledger.model_dump(mode="json", by_alias=True))).hexdigest()


def _verified_system_root_alias(alias: Path, expected: Path) -> bool:
    try:
        metadata = alias.lstat()
        if not stat.S_ISLNK(metadata.st_mode):
            return False
        raw = Path(os.readlink(alias))
        target = raw if raw.is_absolute() else alias.parent / raw
        target = Path(os.path.abspath(os.fspath(target)))
        expected_metadata = expected.lstat()
        followed = alias.stat()
    except (OSError, TypeError, ValueError):
        return False
    return target == expected and stat.S_ISDIR(expected_metadata.st_mode) and not stat.S_ISLNK(expected_metadata.st_mode) and (expected_metadata.st_dev, expected_metadata.st_ino) == (followed.st_dev, followed.st_ino)


def _explicit_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("formal_shadow_ledger_root_must_be_path")
    path = Path(os.path.abspath(os.fspath(root.expanduser())))
    if len(path.parts) >= 2:
        alias = Path(path.anchor) / path.parts[1]
        expected = _FIXED_SYSTEM_ROOT_ALIASES.get(alias)
        if expected is not None and _verified_system_root_alias(alias, expected):
            path = expected.joinpath(*path.parts[2:])
    return path


def _directory_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(directory, int) or not isinstance(nofollow, int):
        raise ValueError("formal_shadow_ledger_secure_flags_unavailable")
    return os.O_RDONLY | directory | nofollow


def _open_directory(path: Path | str, *, dir_fd: Optional[int] = None) -> int:
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(os.fspath(path), _directory_flags(), dir_fd=dir_fd) if dir_fd is not None else os.open(os.fspath(path), _directory_flags())
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("formal_shadow_ledger_store_path_unverified")
        return descriptor
    except ValueError:
        if descriptor is not None:
            try: os.close(descriptor)
            except OSError: pass
        raise
    except (OSError, TypeError) as error:
        if descriptor is not None:
            try: os.close(descriptor)
            except OSError: pass
        raise ValueError("formal_shadow_ledger_store_path_unverified") from error


def _same_inode(left: int, right: int) -> bool:
    a, b = os.fstat(left), os.fstat(right)
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _entry_kind(parent_fd: int, name: str) -> str:
    try: metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError: return "missing"
    except OSError: return "invalid"
    if stat.S_ISDIR(metadata.st_mode): return "directory"
    if stat.S_ISREG(metadata.st_mode): return "regular"
    return "invalid"


def _open_root_directory(path: Path, *, create_missing: bool) -> int:
    if not path.is_absolute() or not path.anchor:
        raise ValueError("formal_shadow_ledger_store_path_unverified")
    current = _open_directory(Path(path.anchor))
    try:
        for component in path.parts[1:]:
            kind = _entry_kind(current, component)
            if kind == "missing":
                if not create_missing:
                    raise FileNotFoundError(os.fspath(path))
                try: os.mkdir(component, mode=0o700, dir_fd=current)
                except FileExistsError: pass
                except (OSError, TypeError) as error:
                    raise ValueError("formal_shadow_ledger_store_path_unverified") from error
                kind = _entry_kind(current, component)
            if kind != "directory":
                raise ValueError("formal_shadow_ledger_store_path_unverified")
            child = _open_directory(component, dir_fd=current)
            try:
                probe = _open_directory(component, dir_fd=current)
                try:
                    if not _same_inode(child, probe):
                        raise ValueError("formal_shadow_ledger_store_path_unverified")
                finally: os.close(probe)
            except BaseException:
                os.close(child)
                raise
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _verify_root_path(path: Path, root_fd: int) -> None:
    try:
        probe = _open_root_directory(path, create_missing=False)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        raise ValueError("formal_shadow_ledger_store_path_unverified") from None
    try:
        if not _same_inode(root_fd, probe):
            raise ValueError("formal_shadow_ledger_store_path_unverified")
    except (OSError, TypeError):
        raise ValueError("formal_shadow_ledger_store_path_unverified") from None
    finally:
        os.close(probe)


def _verify_directory_entry(parent_fd: int, name: str, expected_fd: int) -> None:
    try:
        probe = _open_directory(name, dir_fd=parent_fd)
    except (OSError, TypeError, ValueError):
        raise ValueError("formal_shadow_ledger_store_path_unverified") from None
    try:
        if not _same_inode(expected_fd, probe):
            raise ValueError("formal_shadow_ledger_store_path_unverified")
    except (OSError, TypeError):
        raise ValueError("formal_shadow_ledger_store_path_unverified") from None
    finally:
        os.close(probe)


def _open_regular_at(parent_fd: int, name: str) -> tuple[int, os.stat_result]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ValueError("formal_shadow_ledger_secure_flags_unavailable")
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("formal_shadow_ledger_store_path_unverified")
        return descriptor, metadata
    except FileNotFoundError:
        if descriptor is not None: os.close(descriptor)
        raise
    except (OSError, TypeError, ValueError) as error:
        if descriptor is not None:
            try: os.close(descriptor)
            except OSError: pass
        raise ValueError("formal_shadow_ledger_store_path_unverified") from error


def _verify_regular_entry(parent_fd: int, name: str, expected: os.stat_result) -> None:
    try:
        descriptor, actual = _open_regular_at(parent_fd, name)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        raise ValueError("formal_shadow_ledger_store_path_unverified") from None
    try:
        if _stable_file_identity(actual) != _stable_file_identity(expected):
            raise ValueError("formal_shadow_ledger_store_path_unverified")
    except (OSError, TypeError):
        raise ValueError("formal_shadow_ledger_store_path_unverified") from None
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
    parent_fd: int,
    name: str,
    *,
    max_bytes: int,
) -> tuple[bytes, os.stat_result]:
    descriptor, metadata = _open_regular_at(parent_fd, name)
    try:
        if metadata.st_size > max_bytes:
            raise ValueError("formal_shadow_ledger_file_too_large")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("formal_shadow_ledger_file_too_large")
        after = os.fstat(descriptor)
        if (
            _stable_file_identity(metadata) != _stable_file_identity(after)
            or total != after.st_size
        ):
            raise ValueError("formal_shadow_ledger_store_path_unverified")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _read_json_at(
    parent_fd: int,
    name: str,
    *,
    max_bytes: int,
) -> tuple[object, os.stat_result]:
    raw, metadata = _read_bytes_at(parent_fd, name, max_bytes=max_bytes)
    return strict_json_loads(
        raw,
        error_code="formal_shadow_ledger_json_unverified",
    ), metadata


def _write_atomic_at(parent_fd: int, target: str, payload: dict) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ValueError("formal_shadow_ledger_secure_flags_unavailable")
    descriptor: Optional[int] = None
    temporary: Optional[str] = None
    for _ in range(32):
        candidate = f".{target}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=parent_fd)
            temporary = candidate
            break
        except FileExistsError: continue
    if descriptor is None or temporary is None:
        raise OSError("formal_shadow_ledger_temporary_file_unavailable")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            descriptor = None
            json.dump(payload, file, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            file.flush(); os.fsync(file.fileno())
        os.replace(temporary, target, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary = None
        os.fsync(parent_fd)
    except BaseException:
        if descriptor is not None:
            try: os.close(descriptor)
            except OSError: pass
        if temporary is not None:
            try: os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError: pass
        try: os.fsync(parent_fd)
        finally: raise


def _write_atomic_bytes_at(parent_fd: int, target: str, payload: bytes) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ValueError("formal_shadow_ledger_secure_flags_unavailable")
    descriptor: Optional[int] = None
    temporary: Optional[str] = None
    for _ in range(32):
        candidate = f".{target}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=parent_fd,
            )
            temporary = candidate
            break
        except FileExistsError:
            continue
    if descriptor is None or temporary is None:
        raise OSError("formal_shadow_ledger_temporary_file_unavailable")
    try:
        with os.fdopen(descriptor, "wb") as file:
            descriptor = None
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(
            temporary,
            target,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary = None
        os.fsync(parent_fd)
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        try:
            os.fsync(parent_fd)
        finally:
            raise


def _acquire_lock(root_fd: int) -> bool:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ValueError("formal_shadow_ledger_secure_flags_unavailable")
    try:
        descriptor = os.open(_LOCK_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=root_fd)
    except FileExistsError:
        return False
    try:
        os.write(descriptor, b"locked\n")
        os.fsync(descriptor)
    finally: os.close(descriptor)
    os.fsync(root_fd)
    return True


def _release_lock(root_fd: int) -> None:
    try: os.unlink(_LOCK_NAME, dir_fd=root_fd)
    except FileNotFoundError: return
    os.fsync(root_fd)


@dataclass(frozen=True)
class StoredFormalShadowLedgerRef:
    status: str
    content_sha256: str = ""
    relative_path: str = ""

    @property
    def ledger_relative_path(self) -> str:
        return self.relative_path


@dataclass(frozen=True)
class FormalShadowLedgerLoadResult:
    status: str
    reason_codes: Tuple[str, ...] = ()
    ledger: Optional[FormalShadowLedgerV2] = None
    stored_ref: Optional[StoredFormalShadowLedgerRef] = None


def _calendar_manifest_entries(ledger: FormalShadowLedgerV2) -> list[dict]:
    return [
        {
            "year": evidence.year,
            "sourceDocumentSha256": evidence.source_document_sha256,
            "documentRelativePath": (
                f"calendar/{evidence.source_document_sha256}.html"
            ),
        }
        for evidence in ledger.calendar_evidence
    ]


def _calendar_envelope_bytes(evidence) -> bytes:
    return _canonical_json({
        "contractVersion": "radar-formal-shadow-calendar-input-v1",
        "market": evidence.market,
        "sourceName": evidence.source_name,
        "sourceUrl": evidence.source_url,
        "year": evidence.year,
        "fetchedAt": evidence.fetched_at.isoformat(),
        "observedThrough": evidence.observed_through.isoformat(),
        "sourceDocumentSha256": evidence.source_document_sha256,
    })


def _validate_calendar_document(evidence, raw: bytes) -> None:
    if type(raw) is not bytes or len(raw) > _MAX_CALENDAR_DOCUMENT_BYTES:
        raise ValueError("formal_shadow_calendar_document_unverified")
    try:
        replayed = load_official_sse_calendar_evidence(
            _calendar_envelope_bytes(evidence),
            raw,
        )
    except Exception as error:
        raise ValueError("formal_shadow_calendar_document_unverified") from error
    if replayed != evidence:
        raise ValueError("formal_shadow_calendar_evidence_mismatch")


def _open_or_create_child_directory(root_fd: int, name: str) -> int:
    kind = _entry_kind(root_fd, name)
    if kind == "missing":
        try:
            os.mkdir(name, mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        except (OSError, TypeError) as error:
            raise ValueError("formal_shadow_ledger_store_path_unverified") from error
        kind = _entry_kind(root_fd, name)
    if kind != "directory":
        raise ValueError("formal_shadow_ledger_store_path_unverified")
    child_fd = _open_directory(name, dir_fd=root_fd)
    try:
        _verify_directory_entry(root_fd, name, child_fd)
    except BaseException:
        os.close(child_fd)
        raise
    return child_fd


def _store_calendar_documents(
    ledger: FormalShadowLedgerV2,
    *,
    root_fd: int,
    supplied: Optional[Mapping[str, bytes]],
) -> Optional[int]:
    if supplied is not None and not isinstance(supplied, Mapping):
        raise TypeError("formal_shadow_calendar_documents_mapping_required")
    if not ledger.calendar_evidence:
        if supplied:
            raise ValueError("formal_shadow_calendar_documents_unexpected")
        return None
    calendar_fd = _open_or_create_child_directory(root_fd, "calendar")
    try:
        for evidence in ledger.calendar_evidence:
            sha256 = evidence.source_document_sha256
            name = f"{sha256}.html"
            provided = supplied.get(sha256) if supplied is not None else None
            kind = _entry_kind(calendar_fd, name)
            if provided is not None:
                _validate_calendar_document(evidence, provided)
                if kind == "missing":
                    _write_atomic_bytes_at(calendar_fd, name, provided)
                elif kind == "regular":
                    existing, metadata = _read_bytes_at(
                        calendar_fd,
                        name,
                        max_bytes=_MAX_CALENDAR_DOCUMENT_BYTES,
                    )
                    _verify_regular_entry(calendar_fd, name, metadata)
                    if existing != provided:
                        raise ValueError("formal_shadow_calendar_document_unverified")
                else:
                    raise ValueError("formal_shadow_ledger_store_path_unverified")
            elif kind == "regular":
                existing, metadata = _read_bytes_at(
                    calendar_fd,
                    name,
                    max_bytes=_MAX_CALENDAR_DOCUMENT_BYTES,
                )
                _verify_regular_entry(calendar_fd, name, metadata)
                _validate_calendar_document(evidence, existing)
            elif kind == "missing":
                raise ValueError("formal_shadow_calendar_document_missing")
            else:
                raise ValueError("formal_shadow_ledger_store_path_unverified")
        _verify_directory_entry(root_fd, "calendar", calendar_fd)
        return calendar_fd
    except BaseException:
        os.close(calendar_fd)
        raise


def _verify_calendar_documents_at(
    ledger: FormalShadowLedgerV2,
    *,
    root_fd: int,
    manifest_entries: object,
) -> None:
    expected_entries = _calendar_manifest_entries(ledger)
    if manifest_entries != expected_entries:
        raise ValueError("formal_shadow_calendar_manifest_mismatch")
    if not expected_entries:
        return
    if _entry_kind(root_fd, "calendar") != "directory":
        raise ValueError("formal_shadow_calendar_document_missing")
    calendar_fd = _open_directory("calendar", dir_fd=root_fd)
    try:
        _verify_directory_entry(root_fd, "calendar", calendar_fd)
        raw_by_sha256 = {}
        for evidence in ledger.calendar_evidence:
            sha256 = evidence.source_document_sha256
            raw = raw_by_sha256.get(sha256)
            if raw is None:
                name = f"{sha256}.html"
                try:
                    raw, metadata = _read_bytes_at(
                        calendar_fd,
                        name,
                        max_bytes=_MAX_CALENDAR_DOCUMENT_BYTES,
                    )
                except FileNotFoundError as error:
                    raise ValueError(
                        "formal_shadow_calendar_document_missing",
                    ) from error
                _verify_regular_entry(calendar_fd, name, metadata)
                raw_by_sha256[sha256] = raw
            _validate_calendar_document(evidence, raw)
        _verify_directory_entry(root_fd, "calendar", calendar_fd)
    finally:
        os.close(calendar_fd)


def save_formal_shadow_ledger(
    ledger: FormalShadowLedgerV2,
    root: Path,
    *,
    calendar_documents_by_sha256: Optional[Mapping[str, bytes]] = None,
) -> StoredFormalShadowLedgerRef:
    """以不可变内容名发布 v2 台账；锁竞争不写入并返回 ``contended``。"""
    if not isinstance(ledger, FormalShadowLedgerV2):
        raise TypeError("formal_shadow_ledger_v2_required")
    ledger = FormalShadowLedgerV2.model_validate(ledger.model_dump(mode="json", by_alias=True, warnings="none"))
    payload = ledger.model_dump(mode="json", by_alias=True)
    content_sha256 = formal_shadow_ledger_content_sha256(ledger)
    name = f"{content_sha256}.json"
    manifest_payload = {
        "contractId": FORMAL_SHADOW_LEDGER_MANIFEST_CONTRACT_ID,
        "ledgerRelativePath": f"reports/{name}",
        "contentSha256": content_sha256,
        "contractVersion": ledger.contract_version,
        "calendarDocuments": _calendar_manifest_entries(ledger),
    }
    store_root = _explicit_root(root)
    root_fd = _open_root_directory(store_root, create_missing=True)
    reports_fd: Optional[int] = None
    calendar_fd: Optional[int] = None
    locked = False
    try:
        _verify_root_path(store_root, root_fd)
        if _entry_kind(root_fd, "latest.json") not in {"missing", "regular"}:
            raise ValueError("formal_shadow_ledger_store_path_unverified")
        locked = _acquire_lock(root_fd)
        if not locked:
            return StoredFormalShadowLedgerRef(status="contended")
        calendar_fd = _store_calendar_documents(
            ledger,
            root_fd=root_fd,
            supplied=calendar_documents_by_sha256,
        )
        reports_fd = _open_or_create_child_directory(root_fd, "reports")
        kind = _entry_kind(reports_fd, name)
        report_existed = kind == "regular"
        if kind == "regular":
            try:
                existing, metadata = _read_json_at(
                    reports_fd,
                    name,
                    max_bytes=_MAX_LEDGER_BYTES,
                )
            except json.JSONDecodeError as error:
                raise ValueError("formal_shadow_ledger_content_unverified") from error
            _verify_regular_entry(reports_fd, name, metadata)
            if _canonical_json(existing) != _canonical_json(payload):
                raise ValueError("formal_shadow_ledger_content_unverified")
        elif kind != "missing":
            raise ValueError("formal_shadow_ledger_store_path_unverified")
        else:
            _write_atomic_at(reports_fd, name, payload)
        _verify_root_path(store_root, root_fd)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        if calendar_fd is not None:
            _verify_directory_entry(root_fd, "calendar", calendar_fd)
        latest_kind = _entry_kind(root_fd, "latest.json")
        if latest_kind not in {"missing", "regular"}:
            raise ValueError("formal_shadow_ledger_store_path_unverified")
        if report_existed and latest_kind == "regular":
            try:
                current_manifest, manifest_metadata = _read_json_at(
                    root_fd,
                    "latest.json",
                    max_bytes=_MAX_MANIFEST_BYTES,
                )
            except json.JSONDecodeError:
                current_manifest = None
            else:
                _verify_regular_entry(root_fd, "latest.json", manifest_metadata)
                _verify_root_path(store_root, root_fd)
            if current_manifest == manifest_payload:
                return StoredFormalShadowLedgerRef(
                    "unchanged",
                    content_sha256,
                    f"reports/{name}",
                )
        _write_atomic_at(root_fd, "latest.json", manifest_payload)
        _verify_root_path(store_root, root_fd)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        if calendar_fd is not None:
            _verify_directory_entry(root_fd, "calendar", calendar_fd)
        return StoredFormalShadowLedgerRef("available", content_sha256, f"reports/{name}")
    finally:
        if locked: _release_lock(root_fd)
        if calendar_fd is not None: os.close(calendar_fd)
        if reports_fd is not None: os.close(reports_fd)
        os.close(root_fd)


def _failed(reason: str) -> FormalShadowLedgerLoadResult:
    return FormalShadowLedgerLoadResult("failed", (reason,))


def load_latest_formal_shadow_ledger(root: Path, *, now: Optional[datetime] = None) -> FormalShadowLedgerLoadResult:
    """验证 latest 清单、哈希与 v2 合同；缺失、篡改、未来数据都失败关闭。"""
    store_root = _explicit_root(root)
    root_fd: Optional[int] = None; reports_fd: Optional[int] = None
    try:
        root_fd = _open_root_directory(store_root, create_missing=False)
    except FileNotFoundError:
        return FormalShadowLedgerLoadResult("missing", ("formal_shadow_ledger_missing",))
    except (OSError, TypeError, ValueError):
        return _failed("formal_shadow_ledger_manifest_unverified")
    try:
        _verify_root_path(store_root, root_fd)
        try:
            manifest, manifest_metadata = _read_json_at(
                root_fd,
                "latest.json",
                max_bytes=_MAX_MANIFEST_BYTES,
            )
        except FileNotFoundError: return FormalShadowLedgerLoadResult("missing", ("formal_shadow_ledger_missing",))
        _verify_regular_entry(root_fd, "latest.json", manifest_metadata)
        if not isinstance(manifest, dict) or manifest.get("contractId") != FORMAL_SHADOW_LEDGER_MANIFEST_CONTRACT_ID:
            return _failed("formal_shadow_ledger_manifest_unverified")
        expected = manifest.get("contentSha256")
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None or manifest.get("ledgerRelativePath") != f"reports/{expected}.json" or manifest.get("contractVersion") != "radar-formal-shadow-ledger-v2":
            return _failed("formal_shadow_ledger_manifest_unverified")
        if _entry_kind(root_fd, "reports") != "directory":
            return _failed("formal_shadow_ledger_missing" if _entry_kind(root_fd, "reports") == "missing" else "formal_shadow_ledger_report_unverified")
        reports_fd = _open_directory("reports", dir_fd=root_fd)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        try:
            payload, metadata = _read_json_at(
                reports_fd,
                f"{expected}.json",
                max_bytes=_MAX_LEDGER_BYTES,
            )
        except FileNotFoundError: return _failed("formal_shadow_ledger_missing")
        _verify_regular_entry(reports_fd, f"{expected}.json", metadata)
        _verify_directory_entry(root_fd, "reports", reports_fd)
        _verify_regular_entry(root_fd, "latest.json", manifest_metadata)
        _verify_root_path(store_root, root_fd)
        try:
            actual = hashlib.sha256(_canonical_json(payload)).hexdigest()
        except (TypeError, ValueError):
            return _failed("formal_shadow_ledger_report_unverified")
        if actual != expected:
            return _failed("formal_shadow_ledger_hash_mismatch")
        try:
            ledger = FormalShadowLedgerV2.model_validate(payload)
        except Exception:
            return _failed("formal_shadow_ledger_report_unverified")
        try:
            _verify_calendar_documents_at(
                ledger,
                root_fd=root_fd,
                manifest_entries=manifest.get("calendarDocuments", []),
            )
        except (OSError, TypeError, ValueError):
            return _failed("formal_shadow_calendar_document_unverified")
        _verify_regular_entry(root_fd, "latest.json", manifest_metadata)
        _verify_root_path(store_root, root_fd)
        reference_time = now or datetime.now(timezone.utc)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            return _failed("formal_shadow_ledger_reference_time_invalid")
        if any(item.observed_at > reference_time for item in ledger.observations):
            return _failed("formal_shadow_ledger_future_observation")
        return FormalShadowLedgerLoadResult(
            "available",
            ledger=ledger,
            stored_ref=StoredFormalShadowLedgerRef(
                "available",
                expected,
                f"reports/{expected}.json",
            ),
        )
    except json.JSONDecodeError:
        return _failed("formal_shadow_ledger_manifest_unverified")
    except (OSError, TypeError, ValueError, KeyError):
        return _failed("formal_shadow_ledger_manifest_unverified")
    finally:
        if reports_fd is not None: os.close(reports_fd)
        if root_fd is not None: os.close(root_fd)
