"""阶段9到期客观结果只读回收入口。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from radar.replay_objective_outcomes import (
    collect_replay_objective_outcomes_from_files,
)
from radar.replay_outcome_daily_provider import (
    build_daily_snapshot_outcome_providers,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-bundle", type=Path, required=True)
    parser.add_argument("--output-bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--daily-snapshot",
        type=Path,
        action="append",
        default=[],
        help="可重复；内容校验的日终客观事实工件",
    )
    parser.add_argument(
        "--confirm-objective-outcome-collection",
        action="store_true",
        help="确认只读检查官方交易日历并生成/private/tmp结果工件",
    )
    args = parser.parse_args(argv)
    if not args.confirm_objective_outcome_collection:
        parser.error("必须显式提供--confirm-objective-outcome-collection")
    providers = (
        build_daily_snapshot_outcome_providers(args.daily_snapshot)
        if args.daily_snapshot
        else {}
    )
    daily_snapshot_ids = (
        next(iter(providers.values())).snapshot_ids if providers else ()
    )
    result = collect_replay_objective_outcomes_from_files(
        task_bundle_path=args.task_bundle,
        output_bundle_path=args.output_bundle,
        output_dir=args.output_dir,
        evaluated_at=datetime.now(SHANGHAI_TZ),
        providers=providers,
        daily_snapshot_ids=daily_snapshot_ids,
    )
    print(f"status={result.bundle.status}")
    print(f"objectiveOutcomes={result.outcome_bundle_path}")
    print(f"labelBundle={result.label_bundle_path or 'none'}")
    print(f"manifest={result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
