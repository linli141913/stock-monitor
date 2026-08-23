"""阶段6官方主营证据全集自动化只读命令。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Callable, Optional, Sequence, TextIO

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
)
from radar.leader_business_automatic_evidence import (
    LeaderBusinessAutomaticEvidenceSources,
    run_leader_business_automatic_evidence,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从候选全集 source.json 生成确定性官方主营证据",
    )
    parser.add_argument("source_path")
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument(
        "--gap-diagnostic",
        action="store_true",
        help="写出仅供规则设计的对象缺失新鲜语料，不参与正式门",
    )
    return parser


def _print(payload: Any, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    stdout.write("\n")


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    sources: Optional[LeaderBusinessAutomaticEvidenceSources] = None,
    clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
) -> int:
    try:
        arguments = _parser().parse_args(argv)
        source_path = Path(arguments.source_path)
        if not source_path.is_file():
            raise ValueError
        packet = json.loads(source_path.read_text(encoding="utf-8"))
        result = run_leader_business_automatic_evidence(
            packet,
            artifact_dir=Path(arguments.artifact_dir),
            sources=sources,
            clock=clock,
            write_gap_diagnostic=arguments.gap_diagnostic,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        _print({
            "status": "error",
            "reasons": ["business_automatic_cli_input_or_write_error"],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }, stdout)
        return 3
    payload = dict(result.to_evidence())
    if arguments.gap_diagnostic:
        payload["gapDiagnosticPath"] = (
            str(result.gap_diagnostic_path)
            if result.gap_diagnostic_path else None
        )
    _print(payload, stdout)
    return (
        0
        if result.status is AutomaticBusinessEvidenceStatus.READY
        else 2
    )


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
