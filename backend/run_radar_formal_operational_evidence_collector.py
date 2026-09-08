"""阶段10运营证据自动汇总 CLI：只读显式快照，输出既有运营检查报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Optional, Sequence

from radar.formal_operational_checks import (
    OPERATIONAL_COLLECTOR_INPUT_CONTRACT_VERSION,
    OperationalCollectorInputRef,
    RadarFormalOperationalChecks,
    canonical_operational_checks_sha256,
)
from radar.formal_operational_evidence_collector import (
    _read_relative_bytes,
    _strict_json_object,
    collect_and_build_operational_checks,
)
from run_radar_formal_operational_checks import _open_directory, _validated_directory, _verify_directory, _write_atomic


INPUT_FILENAME = "radar-formal-operational-evidence-input-v1.json"
OUTPUT_FILENAME = "radar-formal-operational-checks-v1.json"
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_PRIVATE_TMP = Path("/private/tmp")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _private_tmp_directory(raw: Path | str) -> Path:
    value = _validated_directory(raw)
    if value != _PRIVATE_TMP and _PRIVATE_TMP not in value.parents:
        raise ValueError("operational_evidence_private_tmp_required")
    return value


def _read_evidence_input(root: Path) -> dict:
    """通过目录描述符读取固定名称，不更改其他入口的模块状态。"""
    descriptor = _open_directory(root)
    file_descriptor: Optional[int] = None
    try:
        _verify_directory(root, descriptor)
        try:
            file_descriptor = os.open(INPUT_FILENAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        except FileNotFoundError as error:
            raise ValueError("operational_evidence_input_missing") from error
        except OSError as error:
            raise ValueError("operational_evidence_input_invalid") from error
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            raise ValueError("operational_evidence_input_invalid")
        before = os.fstat(file_descriptor)
        if before.st_size > _MAX_INPUT_BYTES:
            raise ValueError("operational_evidence_input_too_large")
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
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("operational_evidence_input_too_large")
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError("operational_evidence_input_invalid")
        try:
            value = _strict_json_object(raw)
        except ValueError as error:
            raise ValueError("operational_evidence_input_invalid") from error
        os.close(file_descriptor)
        file_descriptor = None
        _verify_directory(root, descriptor)
        if not isinstance(value, dict):
            raise ValueError("operational_evidence_input_invalid")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("operational_evidence_input_invalid") from error
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        os.close(descriptor)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def run(
    input_dir: Path | str,
    output_dir: Path | str,
    asset_root: Path | str,
) -> RadarFormalOperationalChecks:
    input_root = _private_tmp_directory(input_dir)
    output_root = _private_tmp_directory(output_dir)
    if _paths_overlap(input_root, output_root):
        raise ValueError("operational_evidence_paths_overlap")
    assets = _validated_directory(asset_root)
    if assets != _PROJECT_ROOT and assets != _PRIVATE_TMP and _PRIVATE_TMP not in assets.parents:
        raise ValueError("operational_evidence_asset_root_forbidden")
    try:
        relative_path = str(
            (input_root / INPUT_FILENAME).relative_to(_PRIVATE_TMP)
        )
        raw = _read_relative_bytes(
            _PRIVATE_TMP,
            relative_path,
            maximum=_MAX_INPUT_BYTES,
        )
        reference = OperationalCollectorInputRef(
            contractVersion=OPERATIONAL_COLLECTOR_INPUT_CONTRACT_VERSION,
            relativePath=relative_path,
            contentSha256=hashlib.sha256(raw).hexdigest(),
        )
        report = collect_and_build_operational_checks(
            reference,
            snapshot_root=_PRIVATE_TMP,
            asset_root=assets,
            project_asset_root=_PROJECT_ROOT,
        )
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("operational_evidence_collection_failed") from error
    _write_atomic(output_root, report.model_dump(mode="json", by_alias=True))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从显式影子快照离线生成阶段10运营证据。")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--asset-root", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run(args.input_dir, args.output_dir, args.asset_root)
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
