"""离线生成阶段10运营检查包。

只接受显式的输入/输出目录；不访问SQLite、环境、网络或本地服务。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Optional, Sequence

from radar.formal_operational_checks import (
    OPERATIONAL_CHECKS_INPUT_CONTRACT_VERSION,
    RadarFormalOperationalChecks,
    build_operational_checks,
    canonical_operational_checks_sha256,
)


INPUT_FILENAME = "radar-formal-operational-checks-input-v1.json"
OUTPUT_FILENAME = "radar-formal-operational-checks-v1.json"
PRODUCTION_DATA_ROOT = Path(__file__).resolve().parent / "data"
_MAX_INPUT_BYTES = 8 * 1024 * 1024


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _contains_symlink(path: Path) -> bool:
    for candidate in (path,) + tuple(path.parents):
        try:
            if stat.S_ISLNK(candidate.lstat().st_mode):
                return True
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("operational_checks_path_unverified") from error
    return False


def _validated_directory(raw: Path | str) -> Path:
    path = Path(os.path.abspath(os.fspath(Path(raw).expanduser())))
    if _contains_symlink(path):
        raise ValueError("operational_checks_symlink_forbidden")
    if not path.is_dir():
        raise ValueError("operational_checks_directory_invalid")
    return path


def _directory_flags() -> int:
    if not isinstance(getattr(os, "O_DIRECTORY", None), int) or not isinstance(
        getattr(os, "O_NOFOLLOW", None), int
    ):
        raise ValueError("operational_checks_secure_flags_unavailable")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory(path: Path) -> int:
    try:
        descriptor = os.open(os.fspath(path), _directory_flags())
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("operational_checks_path_unverified")
        return descriptor
    except ValueError:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise
    except (OSError, TypeError) as error:
        raise ValueError("operational_checks_path_unverified") from error


def _verify_directory(path: Path, descriptor: int) -> None:
    probe = _open_directory(path)
    try:
        expected, current = os.fstat(descriptor), os.fstat(probe)
        if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("operational_checks_path_unverified")
    finally:
        os.close(probe)


def _strict_json_object(payload: bytes) -> dict:
    def reject_constant(_value: str) -> None:
        raise ValueError("operational_checks_input_invalid")

    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("operational_checks_input_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("operational_checks_input_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("operational_checks_input_invalid")
    return value


def _read_input(root: Path) -> dict:
    descriptor = _open_directory(root)
    file_descriptor: Optional[int] = None
    try:
        _verify_directory(root, descriptor)
        try:
            file_descriptor = os.open(
                INPUT_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
        except FileNotFoundError as error:
            raise ValueError("operational_checks_input_missing") from error
        except OSError as error:
            raise ValueError("operational_checks_input_invalid") from error
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("operational_checks_input_invalid")
        if before.st_size > _MAX_INPUT_BYTES:
            raise ValueError("operational_checks_input_too_large")
        chunks = []
        remaining = _MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(file_descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(file_descriptor)
        current = os.stat(INPUT_FILENAME, dir_fd=descriptor, follow_symlinks=False)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("operational_checks_input_too_large")
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError("operational_checks_input_invalid")
        payload = _strict_json_object(raw)
        _verify_directory(root, descriptor)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("operational_checks_input_invalid") from error
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        os.close(descriptor)
    if not isinstance(payload, dict):
        raise ValueError("operational_checks_input_invalid")
    return payload


def _write_atomic(root: Path, payload: dict) -> None:
    descriptor = _open_directory(root)
    temporary: Optional[str] = None
    file_descriptor: Optional[int] = None
    try:
        _verify_directory(root, descriptor)
        try:
            existing = os.stat(OUTPUT_FILENAME, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError("operational_checks_output_unverified")
        except FileNotFoundError:
            pass
        for _ in range(32):
            candidate = f".{OUTPUT_FILENAME}.{secrets.token_hex(12)}.tmp"
            try:
                file_descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=descriptor,
                )
                temporary = candidate
                break
            except FileExistsError:
                continue
        if file_descriptor is None or temporary is None:
            raise ValueError("operational_checks_output_unverified")
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True, indent=2)
            file.flush()
            os.fsync(file.fileno())
        file_descriptor = None
        _verify_directory(root, descriptor)
        os.replace(temporary, OUTPUT_FILENAME, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        temporary = None
        os.fsync(descriptor)
        _verify_directory(root, descriptor)
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except OSError:
                pass
        os.close(descriptor)


def run(input_dir: Path | str, output_dir: Path | str) -> RadarFormalOperationalChecks:
    input_root, output_root = _validated_directory(input_dir), _validated_directory(output_dir)
    production_root = Path(os.path.abspath(os.fspath(PRODUCTION_DATA_ROOT)))
    if _within(input_root, production_root) or _within(output_root, production_root):
        raise ValueError("operational_checks_production_path_forbidden")
    if _within(input_root, output_root) or _within(output_root, input_root):
        raise ValueError("operational_checks_paths_overlap")
    payload = _read_input(input_root)
    try:
        report = build_operational_checks(payload, input_root)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("operational_checks_collection_failed") from error
    _write_atomic(output_root, report.model_dump(mode="json", by_alias=True))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线构建阶段10运维检查包，不读SQLite、环境、网络或服务。")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run(args.input_dir, args.output_dir)
    except ValueError as error:
        _parser().error(str(error))
    print(json.dumps({
        "state": report.state,
        "contentSha256": canonical_operational_checks_sha256(report),
        "outputFilename": OUTPUT_FILENAME,
        "checkedAt": report.checked_at.isoformat(),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
