"""离线验证一轮真实影子运行并幂等发布阶段10 v2 台账。

本入口只读取显式输入目录中的固定文件，只写显式隔离台账目录；不读取环境、
SQLite，不联网，也不接入 ``main.py`` 或调度器。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import stat
from typing import Optional, Sequence

from radar.formal_shadow_calendar import (
    OfficialSseCalendarProvider,
    load_official_sse_calendar_evidence,
    merge_calendar_evidence,
)
from radar.formal_shadow_ledger import build_shadow_ledger
from radar.formal_shadow_ledger_store import (
    _MAX_CALENDAR_DOCUMENT_BYTES,
    StoredFormalShadowLedgerRef,
    load_latest_formal_shadow_ledger,
    save_formal_shadow_ledger,
)
from radar.formal_shadow_observation_collector import (
    adapt_formal_shadow_observation,
)


RECEIPT_FILENAME = "radar-formal-shadow-run-receipt-v1.json"
SOURCE_ARTIFACT_FILENAME = "radar-formal-shadow-source-artifact-v1.json"
COLLECTION_POLICY_FILENAME = "radar-formal-shadow-collection-policy-v1.json"
CALENDAR_INPUT_FILENAME = "radar-formal-shadow-calendar-input-v1.json"
CALENDAR_DOCUMENT_FILENAME = "sse-official-calendar.html"
TREND_SUPPORTING_FILENAME = "radar-formal-shadow-trend-supporting-v1.json"
ETF_ADMISSION_FILENAME = "radar-etf-formal-admission-v2.json"
PRODUCTION_DATA_ROOT = Path(__file__).resolve().parent / "data"
_MODULES = {"trendRotation", "etfObservation", "leaderObservation"}
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_CALENDAR_BYTES = _MAX_CALENDAR_DOCUMENT_BYTES
_FIXED_SYSTEM_ROOT_ALIASES = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


def _absolute(raw: Path | str) -> Path:
    try:
        value = Path(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("formal_shadow_path_invalid") from error
    if not value.is_absolute() or "~" in value.parts:
        raise ValueError("formal_shadow_path_invalid")
    path = Path(os.path.abspath(os.fspath(value)))
    if len(path.parts) >= 2:
        alias = Path(path.anchor) / path.parts[1]
        expected = _FIXED_SYSTEM_ROOT_ALIASES.get(alias)
        if expected is not None:
            try:
                raw_target = Path(os.readlink(alias))
                target = raw_target if raw_target.is_absolute() else alias.parent / raw_target
                target = Path(os.path.abspath(os.fspath(target)))
                alias_stat = alias.stat()
                expected_lstat = expected.lstat()
            except (OSError, TypeError, ValueError):
                pass
            else:
                if (
                    target == expected
                    and stat.S_ISDIR(expected_lstat.st_mode)
                    and not stat.S_ISLNK(expected_lstat.st_mode)
                    and (alias_stat.st_dev, alias_stat.st_ino)
                    == (expected_lstat.st_dev, expected_lstat.st_ino)
                ):
                    path = expected.joinpath(*path.parts[2:])
    return path


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _directory_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(directory, int) or not isinstance(nofollow, int):
        raise ValueError("formal_shadow_secure_flags_unavailable")
    return os.O_RDONLY | directory | nofollow


def _open_directory_at(path: Path | str, *, dir_fd: Optional[int] = None) -> int:
    descriptor = None
    try:
        descriptor = (
            os.open(os.fspath(path), _directory_flags(), dir_fd=dir_fd)
            if dir_fd is not None
            else os.open(os.fspath(path), _directory_flags())
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError
        return descriptor
    except (OSError, TypeError, ValueError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise ValueError("formal_shadow_input_directory_unverified") from error


def _open_directory(path: Path) -> int:
    if not path.is_absolute() or not path.anchor:
        raise ValueError("formal_shadow_input_directory_unverified")
    current = _open_directory_at(Path(path.anchor))
    try:
        for component in path.parts[1:]:
            child = None
            try:
                child = _open_directory_at(component, dir_fd=current)
                # 同一父目录描述符内立即重开，拒绝目录项在打开过程被替换。
                probe = _open_directory_at(component, dir_fd=current)
                try:
                    opened = os.fstat(child)
                    reopened = os.fstat(probe)
                    if (opened.st_dev, opened.st_ino) != (reopened.st_dev, reopened.st_ino):
                        raise ValueError("formal_shadow_input_directory_unverified")
                finally:
                    os.close(probe)
            except BaseException:
                if child is not None:
                    os.close(child)
                raise
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _verify_directory(path: Path, descriptor: int) -> None:
    probe = _open_directory(path)
    try:
        expected = os.fstat(descriptor)
        actual = os.fstat(probe)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("formal_shadow_input_directory_unverified")
    finally:
        os.close(probe)


def _validate_paths(input_dir: Path | str, ledger_dir: Path | str) -> tuple[Path, Path]:
    input_root = _absolute(input_dir)
    ledger_root = _absolute(ledger_dir)
    production = PRODUCTION_DATA_ROOT.resolve(strict=False)
    if _is_within(input_root, production) or _is_within(ledger_root, production):
        raise ValueError("formal_shadow_production_path_forbidden")
    private_tmp = Path("/private/tmp")
    if not (
        _is_within(input_root, private_tmp)
        and _is_within(ledger_root, private_tmp)
    ):
        raise ValueError("formal_shadow_private_tmp_required")
    if _is_within(input_root, ledger_root) or _is_within(ledger_root, input_root):
        raise ValueError("formal_shadow_paths_overlap")
    # 输入必须已存在且逐次可由 O_NOFOLLOW 打开；输出的逐段验证由内容仓负责。
    descriptor = _open_directory(input_root)
    try:
        _verify_directory(input_root, descriptor)
    finally:
        os.close(descriptor)
    return input_root, ledger_root


def _read_regular_bytes(
    directory: Path,
    names: tuple[str, ...],
) -> dict[str, bytes]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ValueError("formal_shadow_secure_flags_unavailable")
    root_fd = _open_directory(directory)
    results = {}
    try:
        _verify_directory(directory, root_fd)
        for name in names:
            descriptor = None
            try:
                descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=root_fd)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("formal_shadow_input_unverified")
                limit = (
                    _MAX_CALENDAR_BYTES
                    if name == CALENDAR_DOCUMENT_FILENAME else _MAX_JSON_BYTES
                )
                if metadata.st_size > limit:
                    raise ValueError("formal_shadow_input_too_large")
                chunks = []
                remaining = limit + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                if len(payload) > limit:
                    raise ValueError("formal_shadow_input_too_large")
                completed = os.fstat(descriptor)
                current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(current.st_mode)
                    or (metadata.st_dev, metadata.st_ino)
                    != (current.st_dev, current.st_ino)
                    or (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
                    != (completed.st_size, completed.st_mtime_ns, completed.st_ctime_ns)
                    or (completed.st_size, completed.st_mtime_ns, completed.st_ctime_ns)
                    != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                ):
                    raise ValueError("formal_shadow_input_unverified")
                results[name] = payload
            except FileNotFoundError as error:
                raise ValueError("formal_shadow_input_missing") from error
            except ValueError:
                raise
            except (OSError, TypeError) as error:
                raise ValueError("formal_shadow_input_unverified") from error
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        _verify_directory(directory, root_fd)
    finally:
        os.close(root_fd)
    return results


def _validated_evaluated_at(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("formal_shadow_evaluated_at_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("formal_shadow_evaluated_at_invalid")
    return value


def run(
    module: str,
    input_dir: Path | str,
    ledger_dir: Path | str,
    *,
    evaluated_at: datetime,
) -> StoredFormalShadowLedgerRef:
    """验证当前模块真实工件，合并旧 v2 台账并内容寻址发布。"""

    if module not in _MODULES:
        raise ValueError("formal_shadow_module_invalid")
    evaluated_at = _validated_evaluated_at(evaluated_at)
    input_root, ledger_root = _validate_paths(input_dir, ledger_dir)
    names = [
        RECEIPT_FILENAME,
        SOURCE_ARTIFACT_FILENAME,
        COLLECTION_POLICY_FILENAME,
        CALENDAR_INPUT_FILENAME,
        CALENDAR_DOCUMENT_FILENAME,
    ]
    if module == "trendRotation":
        names.append(TREND_SUPPORTING_FILENAME)
    elif module == "etfObservation":
        names.append(ETF_ADMISSION_FILENAME)
    raw = _read_regular_bytes(input_root, tuple(names))
    calendar_evidence = load_official_sse_calendar_evidence(
        raw[CALENDAR_INPUT_FILENAME],
        raw[CALENDAR_DOCUMENT_FILENAME],
    )
    if calendar_evidence.fetched_at > evaluated_at:
        raise ValueError("formal_shadow_calendar_after_evaluated_at")
    observation = adapt_formal_shadow_observation(
        raw[RECEIPT_FILENAME],
        raw[SOURCE_ARTIFACT_FILENAME],
        evaluated_at=evaluated_at,
        collection_policy_json_bytes=raw[COLLECTION_POLICY_FILENAME],
        supporting_artifact_json_bytes=raw.get(TREND_SUPPORTING_FILENAME),
        formal_admission_json_bytes=raw.get(ETF_ADMISSION_FILENAME),
    )
    if observation.module != module:
        raise ValueError("formal_shadow_observation_module_mismatch")
    if observation.observed_at > evaluated_at:
        raise ValueError("formal_shadow_observation_after_evaluated_at")
    if observation.observed_date != calendar_evidence.observed_through:
        raise ValueError("formal_shadow_calendar_observation_date_mismatch")

    loaded = load_latest_formal_shadow_ledger(ledger_root, now=evaluated_at)
    if loaded.status == "failed":
        raise ValueError(loaded.reason_codes[0])
    if loaded.status == "missing":
        old_observations = ()
        old_calendar_evidence = ()
    else:
        if loaded.ledger is None:
            raise ValueError("formal_shadow_ledger_unverified")
        if loaded.ledger.observations and not loaded.ledger.calendar_evidence:
            raise ValueError("formal_shadow_ledger_calendar_evidence_missing")
        old_observations = loaded.ledger.observations
        old_calendar_evidence = loaded.ledger.calendar_evidence
        if any(item.fetched_at > evaluated_at for item in old_calendar_evidence):
            raise ValueError("formal_shadow_calendar_after_evaluated_at")
    merged_calendar = merge_calendar_evidence(
        old_calendar_evidence,
        calendar_evidence,
    )
    provider = OfficialSseCalendarProvider(merged_calendar)
    ledger = build_shadow_ledger(
        old_observations + (observation,),
        calendar_provider=provider,
        calendar_evidence=merged_calendar,
    )
    saved = save_formal_shadow_ledger(
        ledger,
        ledger_root,
        calendar_documents_by_sha256={
            calendar_evidence.source_document_sha256: raw[CALENDAR_DOCUMENT_FILENAME],
        },
    )
    if saved.status == "contended":
        return saved
    replayed = load_latest_formal_shadow_ledger(ledger_root, now=evaluated_at)
    if (
        replayed.status != "available"
        or replayed.ledger != ledger
        or replayed.stored_ref is None
        or replayed.stored_ref.content_sha256 != saved.content_sha256
        or replayed.stored_ref.relative_path != saved.relative_path
    ):
        raise ValueError("formal_shadow_ledger_publish_unverified")
    return StoredFormalShadowLedgerRef(
        status=saved.status,
        content_sha256=replayed.stored_ref.content_sha256,
        relative_path=replayed.stored_ref.relative_path,
    )


def _parse_time(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("formal_shadow_evaluated_at_invalid") from error
    return _validated_evaluated_at(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "离线读取固定版本化影子回执、来源、外部策略与上交所官方日历原文，"
            "幂等发布 v2 台账；不读取环境、SQLite或网络。"
        ),
    )
    parser.add_argument("--module", required=True)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--ledger-dir", required=True, type=Path)
    parser.add_argument("--evaluated-at", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.module not in _MODULES:
            raise ValueError("formal_shadow_module_invalid")
        result = run(
            arguments.module,
            arguments.input_dir,
            arguments.ledger_dir,
            evaluated_at=_parse_time(arguments.evaluated_at),
        )
    except OSError:
        print(json.dumps({
            "reason": "formal_shadow_io_failed",
            "status": "failed",
        }, sort_keys=True))
        return 2
    except (TypeError, ValueError) as error:
        reason = str(error)
        if not reason or "/" in reason or "\\" in reason:
            reason = "formal_shadow_failed"
        print(json.dumps({"reason": reason, "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps({
        "status": result.status,
        "contentSha256": result.content_sha256,
        "ledgerRelativePath": result.relative_path,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
