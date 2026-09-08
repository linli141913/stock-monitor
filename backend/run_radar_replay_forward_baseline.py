"""阶段9真实前向基线安全命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from radar.replay_forward_baseline import collect_forward_replay_baseline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="采集阶段9真实前向开发样本（不访问SQLite）",
    )
    parser.add_argument(
        "--confirm-live-baseline",
        action="store_true",
        help="确认调用公开真实来源并写入显式/private/tmp目录",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--radar-run-id",
        help="可选；绑定同轮确定性输出的radarRunId",
    )
    parser.add_argument(
        "--cninfo-pdf-cache-dir",
        type=Path,
        help=(
            "可选；只允许/private/tmp子目录，仅复用校验通过的巨潮官方PDF，"
            "不会缓存公告查询分页"
        ),
    )
    parser.add_argument(
        "--sample-role",
        choices=("development", "calibration", "holdout"),
        default="development",
        help="采集开始前冻结样本分区；落盘后不得改写",
    )
    parser.add_argument(
        "--formal-etf",
        action="append",
        default=[],
        help=(
            "可选；在样本时间冻结前采集该ETF的公开正式准入材料，"
            "可重复指定，最多10只"
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_live_baseline:
        print(
            json.dumps({
                "status": "rejected",
                "reason": "confirmation_required",
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    try:
        result = collect_forward_replay_baseline(
            confirm_live_baseline=True,
            output_dir=args.output_dir,
            sample_role=args.sample_role,
            cninfo_pdf_cache_dir=args.cninfo_pdf_cache_dir,
            radar_run_id=args.radar_run_id,
            formal_etf_symbols=tuple(args.formal_etf),
        )
    except Exception as exc:
        print(
            json.dumps({
                "status": "failed",
                "errorType": type(exc).__name__,
                "reason": str(exc),
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({
        "status": result.report.status,
        "replayRunId": result.replay.replay_run_id,
        "sampleCounts": result.report.sample_counts,
        "missingPartitions": result.report.missing_partitions,
        "missingDomains": result.report.missing_domains,
        "unverifiableCount": result.report.unverifiable_count,
        "failedCount": result.report.failed_count,
        "scopedExclusionCount": getattr(
            result.report,
            "scoped_exclusion_count",
            0,
        ),
        "scopedExclusionCounts": getattr(
            result.report,
            "scoped_exclusion_counts",
            {},
        ),
        "outputDir": str(result.output_dir),
        "etfFormalAdmissionPath": (
            str(result.etf_formal_admission_path)
            if getattr(result, "etf_formal_admission_path", None)
            is not None
            else None
        ),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
