"""一次性采集 ETF 现场影子行情、原子组包并登记离线台账。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from radar.etf_live_shadow_capture import (
    run_live_etf_shadow_capture,
    validate_etf_live_shadow_symbols,
)
from radar.formal_shadow_ledger_store import (
    _explicit_root,
    _open_root_directory,
    _verify_root_path,
)
from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock


_PRIVATE_TMP = Path("/private/tmp")
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_CALENDAR_BYTES = 2 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _default_publish(**kwargs: Any) -> Any:
    from radar.formal_shadow_input_bundle import (
        publish_etf_formal_shadow_input_bundle,
    )

    return publish_etf_formal_shadow_input_bundle(**kwargs)


def _default_register(
    module: str,
    input_dir: Path,
    ledger_dir: Path,
    *,
    evaluated_at: datetime,
) -> Any:
    from run_radar_formal_shadow_observation import run

    return run(
        module,
        input_dir,
        ledger_dir,
        evaluated_at=evaluated_at,
    )


@dataclass(frozen=True)
class EtfCaptureCliHooks:
    capture: Callable[..., Any] = run_live_etf_shadow_capture
    publish: Callable[..., Any] = _default_publish
    register: Callable[..., Any] = _default_register
    clock: Callable[[], datetime] = _now


def _private_tmp_path(raw: Path | str) -> Path:
    try:
        value = Path(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("etf_live_shadow_private_tmp_required") from error
    if not value.is_absolute() or "~" in value.parts:
        raise ValueError("etf_live_shadow_private_tmp_required")
    normalized = _explicit_root(value)
    if normalized == _PRIVATE_TMP or _PRIVATE_TMP not in normalized.parents:
        raise ValueError("etf_live_shadow_private_tmp_required")
    return normalized


def _prepare_private_root(path: Path, *, reason: str) -> None:
    descriptor: Optional[int] = None
    try:
        descriptor = _open_root_directory(path, create_missing=True)
        _verify_root_path(path, descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError(reason)
        _verify_root_path(path, descriptor)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == reason:
            raise
        raise ValueError(reason) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ValueError("etf_live_shadow_secure_flags_unavailable")
    descriptor: Optional[int] = None
    parent_fd: Optional[int] = None
    try:
        parent_fd = _open_root_directory(path.parent, create_missing=False)
        _verify_root_path(path.parent, parent_fd)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | nofollow,
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if any((
            not stat.S_ISREG(before.st_mode),
            not stat.S_ISREG(current.st_mode),
            before.st_nlink != 1,
            (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino),
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (current.st_size, current.st_mtime_ns, current.st_ctime_ns),
            before.st_size > maximum_bytes,
        )):
            raise ValueError("etf_live_shadow_input_unverified")
        chunks = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if any((
            not payload,
            len(payload) > maximum_bytes,
            (before.st_dev, before.st_ino, before.st_size,
             before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns),
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns)
            != (final.st_dev, final.st_ino, final.st_size,
                final.st_mtime_ns, final.st_ctime_ns),
        )):
            raise ValueError("etf_live_shadow_input_unverified")
        _verify_root_path(path.parent, parent_fd)
        return payload
    except ValueError as error:
        if str(error) == "etf_live_shadow_input_unverified":
            raise
        raise ValueError("etf_live_shadow_input_unverified") from error
    except (OSError, TypeError) as error:
        raise ValueError("etf_live_shadow_input_unverified") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "显式采集一次腾讯 ETF 行情，并通过 /private/tmp 原子输入包登记"
            "阶段10离线影子台账；不访问 SQLite、环境或服务。"
        ),
    )
    parser.add_argument("--confirm-live-etf-shadow", action="store_true")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--admission-file", required=True, type=Path)
    parser.add_argument("--calendar-envelope-file", required=True, type=Path)
    parser.add_argument("--calendar-document-file", required=True, type=Path)
    parser.add_argument("--collection-policy-file", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--ledger-dir", required=True, type=Path)
    return parser


def run_cli(
    argv: Sequence[str],
    *,
    hooks: Optional[EtfCaptureCliHooks] = None,
) -> Mapping[str, Any]:
    arguments = _parser().parse_args(argv)
    if not arguments.confirm_live_etf_shadow:
        raise ValueError("etf_live_shadow_confirmation_required")
    symbols = validate_etf_live_shadow_symbols(arguments.symbols)
    run_id = str(arguments.run_id or "").strip()
    if not run_id:
        raise ValueError("etf_live_shadow_run_id_invalid")
    admission_path = _private_tmp_path(arguments.admission_file)
    calendar_envelope_path = _private_tmp_path(arguments.calendar_envelope_file)
    calendar_document_path = _private_tmp_path(arguments.calendar_document_file)
    policy_path = _private_tmp_path(arguments.collection_policy_file)
    output_root = _private_tmp_path(arguments.output_root)
    ledger_dir = _private_tmp_path(arguments.ledger_dir)
    if (
        output_root == ledger_dir
        or output_root in ledger_dir.parents
        or ledger_dir in output_root.parents
    ):
        raise ValueError("etf_live_shadow_paths_overlap")

    admission_raw = _read_regular_file(
        admission_path,
        maximum_bytes=_MAX_JSON_BYTES,
    )
    calendar_envelope_raw = _read_regular_file(
        calendar_envelope_path,
        maximum_bytes=_MAX_JSON_BYTES,
    )
    calendar_document_raw = _read_regular_file(
        calendar_document_path,
        maximum_bytes=_MAX_CALENDAR_BYTES,
    )
    policy_raw = _read_regular_file(
        policy_path,
        maximum_bytes=_MAX_JSON_BYTES,
    )
    _prepare_private_root(
        output_root,
        reason="etf_live_shadow_output_root_unverified",
    )
    active = hooks or EtfCaptureCliHooks()
    capture = active.capture(
        symbols=symbols,
        radar_run_id=run_id,
        lock_path=output_root / ".stage10-etf-live-shadow.lock",
        clock=active.clock,
        lock_factory=PrivateTmpNoFollowFileLock,
    )
    artifact_lock_state = getattr(
        getattr(capture, "artifact", None),
        "lock_state",
        None,
    )
    receipt_lock_state = getattr(
        getattr(capture, "receipt", None),
        "lock_state",
        None,
    )
    if (
        artifact_lock_state not in {"acquired", "contended", "failed"}
        or receipt_lock_state != artifact_lock_state
    ):
        raise ValueError("etf_live_shadow_capture_lock_unverified")
    if artifact_lock_state == "contended":
        return {
            "contentSha256": None,
            "ledgerRelativePath": None,
            "module": "etfObservation",
            "status": "contended",
        }
    if artifact_lock_state != "acquired":
        raise ValueError("etf_live_shadow_capture_lock_failed")
    _prepare_private_root(
        ledger_dir,
        reason="etf_live_shadow_ledger_root_unverified",
    )
    evaluated_at = capture.receipt.observed_at
    publication = active.publish(
        output_root=output_root,
        capture=capture,
        formal_admission_json_bytes=admission_raw,
        collection_policy_json_bytes=policy_raw,
        calendar_envelope_json_bytes=calendar_envelope_raw,
        calendar_document_bytes=calendar_document_raw,
        evaluated_at=evaluated_at,
    )
    if (
        getattr(publication, "module", None) != "etfObservation"
        or not isinstance(getattr(publication, "input_dir", None), Path)
    ):
        raise ValueError("etf_live_shadow_bundle_publish_unverified")
    registered = active.register(
        "etfObservation",
        publication.input_dir,
        ledger_dir,
        evaluated_at=evaluated_at,
    )
    status = getattr(registered, "status", None)
    content_sha256 = getattr(registered, "content_sha256", None)
    relative_path = getattr(registered, "relative_path", None)
    if status not in {"available", "unchanged", "contended"}:
        raise ValueError("etf_live_shadow_ledger_registration_unverified")
    if status == "contended":
        if content_sha256 not in {"", None} or relative_path not in {"", None}:
            raise ValueError("etf_live_shadow_ledger_registration_unverified")
        content_sha256 = None
        relative_path = None
    elif (
        not isinstance(content_sha256, str)
        or len(content_sha256) != 64
        or not isinstance(relative_path, str)
        or relative_path.startswith("/")
        or ".." in Path(relative_path).parts
    ):
        raise ValueError("etf_live_shadow_ledger_registration_unverified")
    return {
        "contentSha256": content_sha256,
        "ledgerRelativePath": relative_path,
        "module": "etfObservation",
        "status": status,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        result = run_cli(list(argv) if argv is not None else list(os.sys.argv[1:]))
    except (OSError, TypeError, ValueError) as error:
        reason = str(error)
        if not reason or "/" in reason or "\\" in reason:
            reason = "etf_live_shadow_capture_failed"
        print(json.dumps({"reason": reason, "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
