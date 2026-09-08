"""从阶段9前向工件导出独立标签任务，不访问网络或SQLite。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from radar.replay_label_tasks import export_replay_label_tasks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出阶段9独立评测标签任务")
    parser.add_argument(
        "--input-dir",
        action="append",
        required=True,
        type=Path,
        help="可重复；阶段9不可变前向工件目录",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = export_replay_label_tasks(
            input_dirs=args.input_dir,
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
        "status": "ready",
        "taskBundleId": result.bundle.task_bundle_id,
        "sampleCount": len(result.bundle.samples),
        "sampleRoles": [item.role for item in result.bundle.samples],
        "outputDir": str(result.output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
