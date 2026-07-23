"""阶段2B-6B2交易日续验的只读命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from radar.runtime import RADAR_RUNTIME_LOCK_PATH
from radar.trading_day_validation import (
    RadarValidationBaseline,
    capture_validation_baseline,
    validate_trading_day,
)


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "stock_monitor.db"


def _write_json_exclusive(path: Path, value: dict) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
        json.dump(value, file_handle, ensure_ascii=False, indent=2)
        file_handle.write("\n")


def _load_baseline(path: Path) -> RadarValidationBaseline:
    with path.expanduser().resolve(strict=True).open(encoding="utf-8") as file_handle:
        value = json.load(file_handle)
    if not isinstance(value, dict):
        raise ValueError("雷达验收基线必须是JSON对象")
    return RadarValidationBaseline.from_dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读冻结或验证主线雷达交易日影子运行证据",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline_parser = subparsers.add_parser("baseline", help="冻结交易日前只读基线")
    baseline_parser.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    baseline_parser.add_argument("--output", required=True)

    validate_parser = subparsers.add_parser("validate", help="验证指定交易日")
    validate_parser.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    validate_parser.add_argument("--date", required=True)
    validate_parser.add_argument("--baseline", required=True)
    validate_parser.add_argument("--lock-path", default=str(RADAR_RUNTIME_LOCK_PATH))
    validate_parser.add_argument("--output")

    args = parser.parse_args()
    if args.command == "baseline":
        baseline = capture_validation_baseline(args.database)
        result = baseline.to_dict()
        _write_json_exclusive(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    baseline = _load_baseline(Path(args.baseline))
    report = validate_trading_day(
        args.database,
        date.fromisoformat(args.date),
        baseline=baseline,
        lock_path=args.lock_path,
    )
    result = report.to_dict()
    if args.output:
        _write_json_exclusive(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"passed": 0, "failed": 1, "pending": 2}[report.overall_status]


if __name__ == "__main__":
    raise SystemExit(main())
