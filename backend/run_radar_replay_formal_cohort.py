"""阶段9正式前向样本的一次性安全命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from radar.replay_formal_cohort import prepare_formal_cohort
from radar.replay_etf_research_store import publish_replay_etf_research
from radar.replay_store import DEFAULT_RADAR_REPLAY_STORE_DIR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "用同轮阶段6真实工件采集下一个正式回放样本，导出四域输出与"
            "客观标签任务，并在全部成功后登记到跨交易日活动清单"
        ),
    )
    parser.add_argument("--confirm-live-cohort", action="store_true")
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--stage6-artifact", required=True, type=Path)
    parser.add_argument("--cninfo-pdf-cache-dir", type=Path)
    parser.add_argument(
        "--formal-etf",
        action="append",
        default=[],
        help="要采集正式监测证据的ETF代码；可重复指定，最多10只",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_live_cohort:
        print(json.dumps({
            "status": "rejected",
            "reason": "confirmation_required",
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        result = prepare_formal_cohort(
            confirm_live_cohort=True,
            campaign_dir=args.campaign_dir,
            output_dir=args.output_dir,
            stage6_artifact_path=args.stage6_artifact,
            formal_etf_symbols=tuple(args.formal_etf),
            cninfo_pdf_cache_dir=args.cninfo_pdf_cache_dir,
        )
        published = publish_replay_etf_research(
            result.output.bundle,
            DEFAULT_RADAR_REPLAY_STORE_DIR,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "errorType": type(exc).__name__,
            "reason": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    sample = result.baseline.replay.samples[0]
    print(json.dumps({
        "status": "registered",
        "role": result.plan.role,
        "sampleId": sample.sample_id,
        "radarRunId": sample.radar_run_id,
        "sampleAsOf": str(sample.as_of),
        "campaignId": result.campaign_state.campaign_id,
        "campaignRevision": result.campaign_state.revision,
        "cohortCount": len(result.campaign_state.cohorts),
        "manifestPath": str(result.manifest_path),
        "etfResearchEvidenceSha256": published.evidence_sha256,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
