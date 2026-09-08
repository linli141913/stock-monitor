"""阶段9跨交易日活动清单单入口。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from radar.replay_campaign import (
    SHANGHAI_TZ,
    create_campaign,
    register_cohort,
    resume_campaign,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "登记阶段9不可变样本，并在应采集的收盘日幂等追加"
            "真实日终事实和到期客观标签"
        ),
    )
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--campaign-id")
    parser.add_argument(
        "--mode",
        choices=("formal_sequence", "standalone_diagnostic"),
    )
    parser.add_argument("--task-bundle", type=Path)
    parser.add_argument("--output-bundle", type=Path)
    parser.add_argument(
        "--daily-snapshot",
        action="append",
        default=[],
        type=Path,
    )
    parser.add_argument(
        "--confirm-live-close-capture",
        action="store_true",
        help="确认仅在官方交易日安全收盘后读取公开行情",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if (args.task_bundle is None) != (args.output_bundle is None):
        parser.error("--task-bundle与--output-bundle必须同时提供")
    if args.daily_snapshot and args.task_bundle is None:
        parser.error("--daily-snapshot只能与任务包、输出包一起登记")
    if args.create and (not args.campaign_id or not args.mode):
        parser.error("--create需要--campaign-id和--mode")
    if not args.create and (args.campaign_id or args.mode):
        parser.error("--campaign-id和--mode只能在--create时提供")
    if (
        args.create
        and args.mode == "formal_sequence"
        and args.task_bundle is not None
    ):
        parser.error("formal_sequence只能由正式cohort入口登记样本")
    now = datetime.now(SHANGHAI_TZ)
    try:
        if args.create:
            create_campaign(
                campaign_dir=args.campaign_dir,
                campaign_id=args.campaign_id,
                mode=args.mode,
                updated_at=now,
            )
        if args.task_bundle is not None:
            register_cohort(
                campaign_dir=args.campaign_dir,
                task_bundle_path=args.task_bundle,
                output_bundle_path=args.output_bundle,
                daily_snapshot_paths=args.daily_snapshot,
                updated_at=now,
            )
        report = resume_campaign(
            campaign_dir=args.campaign_dir,
            now=now,
            confirm_live_close_capture=args.confirm_live_close_capture,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "errorType": type(exc).__name__,
            "reason": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": report.status,
        "campaignId": report.state.campaign_id,
        "revision": report.state.revision,
        "cohortCount": len(report.state.cohorts),
        "qualityStatus": getattr(
            getattr(report.state, "assembly", None),
            "quality_status",
            None,
        ),
        "actions": list(report.actions),
        "campaignDir": str(args.campaign_dir.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
