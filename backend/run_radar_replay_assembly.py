"""阶段9不可变回放工件聚合命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from radar.replay_assembly import assemble_replay_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="聚合已冻结的阶段9前向样本、独立标签和确定性输出",
    )
    parser.add_argument(
        "--input-dir",
        action="append",
        required=True,
        type=Path,
        help="可重复；必须是/private/tmp下的前向采集工件目录",
    )
    parser.add_argument(
        "--label-bundle",
        action="append",
        default=[],
        type=Path,
        help="可重复；独立黄金标签radar-replay-label-bundle-v1",
    )
    parser.add_argument(
        "--output-bundle",
        action="append",
        default=[],
        type=Path,
        help="可重复；确定性规则输出radar-replay-output-bundle-v1",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = assemble_replay_artifacts(
            input_dirs=args.input_dir,
            label_bundle_paths=args.label_bundle,
            output_bundle_paths=args.output_bundle,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "errorType": type(exc).__name__,
            "reason": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": result.report.status,
        "pipelineStatus": result.report.pipeline_status,
        "effectivenessStatus": result.report.effectiveness_status,
        "replayRunId": result.replay.replay_run_id,
        "sampleCounts": result.report.sample_counts,
        "comparableLabelCount": result.report.comparable_label_count,
        "missingLabelDomains": result.report.missing_label_domains,
        "reasonCodes": result.report.reason_codes,
        "outputDir": str(result.output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
