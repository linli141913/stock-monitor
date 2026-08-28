"""阶段6官方确定性风险证据真实只读入口。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Optional, Sequence, TextIO

from radar.leader_risk_official_deterministic import (
    LeaderOfficialDeterministicRiskBatchResult,
    LeaderOfficialDeterministicRiskStatus,
    build_leader_official_deterministic_risk_batch,
)
from radar.leader_risk_official_live_delivery import (
    build_leader_risk_official_live_delivery,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="重放七类官方查询，生成可追溯非人工风险证据",
    )
    parser.add_argument("--candidate-source", required=True)
    parser.add_argument("--output-dir", default="/private/tmp")
    parser.add_argument("--confirm-live-poc", action="store_true")
    return parser


def _print(payload: Any, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    stdout.write("\n")


def _private_tmp_path(value: str, *, must_exist: bool) -> Path:
    unresolved = Path(value).expanduser()
    if (
        not unresolved.is_absolute()
        or not str(unresolved).startswith("/private/tmp")
    ):
        raise ValueError("risk_official_deterministic_path_unverified")
    resolved = unresolved.resolve(strict=must_exist)
    try:
        resolved.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "risk_official_deterministic_path_unverified"
        ) from exc
    return resolved


def _write_new_json(path: Path, payload: Any) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _run_live(
    *,
    candidate_source: Path,
    collected_at: datetime,
) -> LeaderOfficialDeterministicRiskBatchResult:
    packet = json.loads(candidate_source.read_text(encoding="utf-8"))
    live = build_leader_risk_official_live_delivery(
        packet,
        collected_at=collected_at,
    )
    return build_leader_official_deterministic_risk_batch(
        candidate_plan=live.candidate_plan,
        delivery=live.delivery,
        document_contents=(),
    )


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    now_provider: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    live_runner: Callable[..., Any] = _run_live,
) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.confirm_live_poc:
        _print({
            "status": "not_run",
            "reason": "risk_official_deterministic_confirmation_missing",
            "riskSourceReady": False,
        }, stdout)
        return 2
    try:
        candidate_source = _private_tmp_path(
            arguments.candidate_source,
            must_exist=True,
        )
        output_dir = _private_tmp_path(
            arguments.output_dir,
            must_exist=False,
        )
        if not candidate_source.is_file():
            raise ValueError("risk_official_deterministic_path_unverified")
        output_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        _print({
            "status": "error",
            "reason": "risk_official_deterministic_path_unverified",
            "riskSourceReady": False,
        }, stdout)
        return 3
    try:
        collected_at = now_provider()
        if (
            not isinstance(collected_at, datetime)
            or collected_at.tzinfo is None
            or collected_at.utcoffset() is None
        ):
            raise ValueError("risk_official_deterministic_clock_unverified")
        result = live_runner(
            candidate_source=candidate_source,
            collected_at=collected_at,
        )
        if type(result) is not LeaderOfficialDeterministicRiskBatchResult:
            raise ValueError("risk_official_deterministic_result_unverified")
        stamp = collected_at.strftime("%Y%m%dT%H%M%S%f")
        artifact = output_dir / f"stage6-official-risk-{stamp}.json"
        _write_new_json(artifact, result.to_evidence())
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        _print({
            "status": "error",
            "reason": "risk_official_deterministic_execution_failed",
            "riskSourceReady": False,
        }, stdout)
        return 3
    ready = result.status == LeaderOfficialDeterministicRiskStatus.READY
    _print({
        "status": result.status.value,
        "artifactPath": str(artifact),
        "riskSourceReady": ready,
        "candidateCount": result.candidate_count,
        "readyCount": result.ready_count,
        "claimScope": "bounded_official_query_window",
        "gate": {
            "riskFilterPassed": False,
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }, stdout)
    return 0 if ready else 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
