"""阶段9客观结果每日收盘事实只读冻结入口。"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from radar.replay_outcome_daily_capture import (
    capture_replay_outcome_day_from_files,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-bundle", type=Path, required=True)
    parser.add_argument("--output-bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trade-date", type=date.fromisoformat)
    parser.add_argument(
        "--confirm-live-close-capture",
        action="store_true",
        help="确认读取公开行情并向/private/tmp写入当日收盘事实",
    )
    args = parser.parse_args(argv)
    if not args.confirm_live_close_capture:
        parser.error("必须显式提供--confirm-live-close-capture")
    captured_at = datetime.now(SHANGHAI_TZ)
    trade_date = args.trade_date or captured_at.date()
    result = capture_replay_outcome_day_from_files(
        task_bundle_path=args.task_bundle,
        output_bundle_path=args.output_bundle,
        output_dir=args.output_dir,
        trade_date=trade_date,
        captured_at=captured_at,
    )
    print(f"status={result.snapshot.status}")
    print(f"tradeDate={result.snapshot.trade_date.isoformat()}")
    print(
        "securities="
        f"{result.snapshot.ready_security_count}/"
        f"{result.snapshot.expected_security_count}"
    )
    print(f"indices={len(result.snapshot.market_indices)}/4")
    print(f"snapshot={result.snapshot_path}")
    print(f"manifest={result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
