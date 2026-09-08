"""导出阶段9同轮确定性市场、行业、ETF与龙头研究输出。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from radar.replay_output_bridge import export_sector_replay_output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出阶段9严格时点市场/行业/ETF/龙头规则输出（不访问SQLite）",
    )
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--radar-run-id", required=True)
    parser.add_argument("--sample-as-of", required=True)
    parser.add_argument("--sector-snapshot", required=True, type=Path)
    parser.add_argument("--stage6-artifact", type=Path)
    parser.add_argument("--etf-replay-input", type=Path)
    parser.add_argument("--etf-formal-admission", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        sample_as_of = datetime.fromisoformat(args.sample_as_of)
        result = export_sector_replay_output(
            sample_id=args.sample_id,
            radar_run_id=args.radar_run_id,
            sample_as_of=sample_as_of,
            sector_snapshot_path=args.sector_snapshot,
            stage6_artifact_path=args.stage6_artifact,
            etf_replay_input_path=args.etf_replay_input,
            etf_formal_admission_path=args.etf_formal_admission,
            output_dir=args.output_dir,
            clock=lambda: datetime.now(sample_as_of.tzinfo),
        )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "errorType": type(exc).__name__,
            "reason": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "completed",
        "bundleId": result.bundle.bundle_id,
        "snapshotSha256": result.snapshot_sha256,
        "marketSnapshotSha256": result.market_snapshot_sha256,
        "leaderSnapshotSha256": result.leader_snapshot_sha256,
        "etfSnapshotSha256": result.etf_snapshot_sha256,
        "outputDir": str(result.output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
