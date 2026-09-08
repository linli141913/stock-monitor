"""离线构建阶段10正式就绪报告。

输入目录必须包含固定文件 ``radar-formal-readiness-input-v1.json``，其外层
``contractVersion`` 必须为 ``radar-formal-readiness-input-v1``。输入、输出与可选
影子台账目录均须显式、已存在、互相隔离且位于 ``/private/tmp`` 下。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Optional, Sequence

from radar.formal_readiness_service import (
    FormalReadinessInputs,
    build_formal_readiness,
    formal_shadow_ledger_evidence_ref,
)
from radar.formal_readiness_store import (
    StoredFormalReadinessRef,
    save_formal_readiness,
)
from radar.formal_shadow_ledger_store import (
    load_latest_formal_shadow_ledger,
)


FORMAL_READINESS_INPUT_FILENAME = "radar-formal-readiness-input-v1.json"
FORMAL_READINESS_INPUT_CONTRACT_VERSION = "radar-formal-readiness-input-v1"
PRODUCTION_DATA_ROOT = Path(__file__).resolve().parent / "data"
PRIVATE_TMP_ROOT = Path("/private/tmp")
TMP_ALIAS_ROOT = Path("/tmp")
_MAX_INPUT_BYTES = 8 * 1024 * 1024


def _contains_symlink(path: Path) -> bool:
    candidates = (path,) + tuple(path.parents)
    for candidate in candidates:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("formal_readiness_path_unverified") from error
        if stat.S_ISLNK(metadata.st_mode):
            return True
    return False


def _normalized_path(raw_path: Path | str) -> Path:
    path = Path(os.path.abspath(os.fspath(Path(raw_path))))
    if path == TMP_ALIAS_ROOT or TMP_ALIAS_ROOT in path.parents:
        try:
            if not os.path.samefile(TMP_ALIAS_ROOT, PRIVATE_TMP_ROOT):
                raise ValueError("formal_readiness_path_unverified")
        except OSError as error:
            raise ValueError("formal_readiness_path_unverified") from error
        path = PRIVATE_TMP_ROOT / path.relative_to(TMP_ALIAS_ROOT)
    return path


def _validated_directory(raw_path: Path | str) -> Path:
    path = _normalized_path(raw_path)
    if _contains_symlink(path):
        raise ValueError("formal_readiness_symlink_forbidden")
    if not path.exists():
        raise ValueError("formal_readiness_directory_missing")
    if not path.is_dir():
        raise ValueError("formal_readiness_directory_invalid")
    descriptor = _open_input_directory(path)
    try:
        _verify_input_directory(path, descriptor)
    finally:
        os.close(descriptor)
    return path


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _secure_directory_flags() -> int:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(directory_flag, int) or not isinstance(nofollow_flag, int):
        raise ValueError("formal_readiness_secure_flags_unavailable")
    return os.O_RDONLY | directory_flag | nofollow_flag


def _open_directory_at(
    value: Path | str,
    *,
    dir_fd: Optional[int] = None,
) -> int:
    descriptor: Optional[int] = None
    try:
        descriptor = (
            os.open(
                os.fspath(value),
                _secure_directory_flags(),
                dir_fd=dir_fd,
            )
            if dir_fd is not None
            else os.open(os.fspath(value), _secure_directory_flags())
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("formal_readiness_input_path_unverified")
        return descriptor
    except ValueError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except (OSError, TypeError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise ValueError("formal_readiness_input_path_unverified") from error


def _open_input_directory(input_root: Path) -> int:
    if not input_root.is_absolute() or not input_root.anchor:
        raise ValueError("formal_readiness_input_path_unverified")
    current = _open_directory_at(Path(input_root.anchor))
    try:
        for component in input_root.parts[1:]:
            child: Optional[int] = None
            try:
                child = _open_directory_at(component, dir_fd=current)
                probe = _open_directory_at(component, dir_fd=current)
                try:
                    opened = os.fstat(child)
                    reopened = os.fstat(probe)
                    if (opened.st_dev, opened.st_ino) != (
                        reopened.st_dev,
                        reopened.st_ino,
                    ):
                        raise ValueError("formal_readiness_input_path_unverified")
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


def _verify_input_directory(input_root: Path, input_fd: int) -> None:
    probe = _open_input_directory(input_root)
    try:
        current = os.fstat(input_fd)
        reopened = os.fstat(probe)
        if (current.st_dev, current.st_ino) != (reopened.st_dev, reopened.st_ino):
            raise ValueError("formal_readiness_input_path_unverified")
    finally:
        os.close(probe)


def _validated_roots(
    input_dir: Path | str,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    input_root = _validated_directory(input_dir)
    output_root = _validated_directory(output_dir)
    production_root = PRODUCTION_DATA_ROOT.resolve(strict=False)
    if (
        _is_within(input_root, production_root)
        or _is_within(output_root, production_root)
    ):
        raise ValueError("formal_readiness_production_path_forbidden")
    if not (
        _is_within(input_root, PRIVATE_TMP_ROOT)
        and _is_within(output_root, PRIVATE_TMP_ROOT)
    ):
        raise ValueError("formal_readiness_private_tmp_required")
    if _is_within(input_root, output_root) or _is_within(
        output_root,
        input_root,
    ):
        raise ValueError("formal_readiness_paths_overlap")
    return input_root, output_root


def _load_inputs(input_root: Path) -> FormalReadinessInputs:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow_flag, int):
        raise ValueError("formal_readiness_secure_flags_unavailable")
    input_fd = _open_input_directory(input_root)
    file_fd: Optional[int] = None
    try:
        _verify_input_directory(input_root, input_fd)
        try:
            file_fd = os.open(
                FORMAL_READINESS_INPUT_FILENAME,
                os.O_RDONLY | nofollow_flag,
                dir_fd=input_fd,
            )
        except FileNotFoundError as error:
            raise ValueError("formal_readiness_input_missing") from error
        except (OSError, TypeError) as error:
            raise ValueError("formal_readiness_input_invalid") from error
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("formal_readiness_input_invalid")
        if metadata.st_size > _MAX_INPUT_BYTES:
            raise ValueError("formal_readiness_input_too_large")
        try:
            chunks = []
            remaining = _MAX_INPUT_BYTES + 1
            while remaining:
                chunk = os.read(file_fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > _MAX_INPUT_BYTES:
                raise ValueError("formal_readiness_input_too_large")
            completed = os.fstat(file_fd)
            current = os.stat(
                FORMAL_READINESS_INPUT_FILENAME,
                dir_fd=input_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(current.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (current.st_dev, current.st_ino)
                or (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
                != (completed.st_size, completed.st_mtime_ns, completed.st_ctime_ns)
                or (completed.st_size, completed.st_mtime_ns, completed.st_ctime_ns)
                != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
            ):
                raise ValueError("formal_readiness_input_invalid")

            def strict_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate_json_key")
                    value[key] = item
                return value

            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("non_finite_json_number")
                ),
            )
        except ValueError as error:
            if str(error) == "formal_readiness_input_too_large":
                raise
            raise ValueError("formal_readiness_input_invalid") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("formal_readiness_input_invalid") from error
        _verify_input_directory(input_root, input_fd)
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        os.close(input_fd)
    if not isinstance(payload, dict):
        raise ValueError("formal_readiness_input_invalid")
    payload = dict(payload)
    if payload.pop("contractVersion", None) != (
        FORMAL_READINESS_INPUT_CONTRACT_VERSION
    ):
        raise ValueError("formal_readiness_input_contract_invalid")
    try:
        return FormalReadinessInputs.model_validate(payload)
    except Exception as error:
        raise ValueError("formal_readiness_input_invalid") from error


def run(
    input_dir: Path | str,
    output_dir: Path | str,
    *,
    shadow_ledger_dir: Optional[Path | str] = None,
) -> StoredFormalReadinessRef:
    """从显式版本化JSON构建，并只写显式隔离输出仓。"""

    input_root, output_root = _validated_roots(input_dir, output_dir)
    inputs = _load_inputs(input_root)
    if shadow_ledger_dir is not None:
        try:
            shadow_root = _validated_directory(shadow_ledger_dir)
        except (TypeError, ValueError) as error:
            if isinstance(error, ValueError):
                raise
            raise ValueError("formal_readiness_shadow_store_path_invalid") from error
        production_root = PRODUCTION_DATA_ROOT.resolve(strict=False)
        if _is_within(shadow_root, production_root):
            raise ValueError("formal_readiness_production_path_forbidden")
        if not _is_within(shadow_root, PRIVATE_TMP_ROOT):
            raise ValueError("formal_readiness_private_tmp_required")
        if any((
            _is_within(shadow_root, input_root),
            _is_within(input_root, shadow_root),
            _is_within(shadow_root, output_root),
            _is_within(output_root, shadow_root),
        )):
            raise ValueError("formal_readiness_paths_overlap")
        loaded = load_latest_formal_shadow_ledger(
            shadow_root,
            now=inputs.checked_at,
        )
        if loaded.status != "available" or loaded.ledger is None or loaded.stored_ref is None:
            reason = (
                loaded.reason_codes[0]
                if loaded.reason_codes else "formal_shadow_ledger_missing"
            )
            raise ValueError(reason)
        stored = loaded.stored_ref
        expected_ref = formal_shadow_ledger_evidence_ref(
            loaded.ledger,
            stored.content_sha256,
        )
        supplied_refs = tuple(
            item for item in inputs.evidence
            if item.evidence_type == "formal_shadow_ledger"
        )
        if (
            inputs.shadow_ledger is not None
            and inputs.shadow_ledger.model_dump(mode="json", by_alias=True)
            != loaded.ledger.model_dump(mode="json", by_alias=True)
        ) or (
            inputs.shadow_ledger_sha256 is not None
            and inputs.shadow_ledger_sha256 != stored.content_sha256
        ) or (
            supplied_refs and supplied_refs != (expected_ref,)
        ):
            raise ValueError("formal_readiness_shadow_store_mismatch")
        inputs = inputs.model_copy(update={
            "shadow_ledger": loaded.ledger,
            "shadow_ledger_sha256": stored.content_sha256,
            "evidence": tuple(
                item for item in inputs.evidence
                if item.evidence_type != "formal_shadow_ledger"
            ) + (expected_ref,),
        })
    elif (
        inputs.shadow_ledger is not None
        or inputs.shadow_ledger_sha256 is not None
        or any(
            item.evidence_type == "formal_shadow_ledger"
            for item in inputs.evidence
        )
    ):
        raise ValueError("formal_readiness_shadow_store_required")
    report = build_formal_readiness(inputs)
    return save_formal_readiness(report, output_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "读取 input-dir/"
            f"{FORMAL_READINESS_INPUT_FILENAME} "
            "（contractVersion="
            f"{FORMAL_READINESS_INPUT_CONTRACT_VERSION}），离线构建并发布到"
            "独立 output-dir；不读取环境、SQLite或网络。"
        ),
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--shadow-ledger-dir",
        type=Path,
        help="可选的显式 v2 影子台账内容寻址仓；提供时以仓内哈希身份为准。",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        stored = run(
            arguments.input_dir,
            arguments.output_dir,
            shadow_ledger_dir=arguments.shadow_ledger_dir,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({
        "status": stored.status,
        "contentSha256": stored.content_sha256,
        "reportRelativePath": stored.relative_path,
        "checkedAt": stored.checked_at.isoformat(),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
