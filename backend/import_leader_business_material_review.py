"""离线校验阶段6主营事实人工回填，并生成脱敏验收回执。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Callable, Mapping, Optional

from radar.leader_business_material_review_submission import (
    LEADER_BUSINESS_MATERIAL_REVIEW_SUBMISSION_CONTRACT_ID,
    LeaderBusinessMaterialReviewSourcePacketStatus,
    LeaderBusinessMaterialReviewSubmissionStatus,
    load_leader_business_material_review_source_packet,
    parse_leader_business_material_review_submission,
)
from radar.sources.leader_tradability_public_live_poc import SHANGHAI_TZ


def _read_json(path: Path) -> Optional[Mapping[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _input_failed(reason: str) -> Mapping[str, object]:
    return {
        "contractId": LEADER_BUSINESS_MATERIAL_REVIEW_SUBMISSION_CONTRACT_ID,
        "status": "blocked",
        "candidateCount": 0,
        "reasons": [reason],
        "receiptPath": None,
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }


def _print(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(
    *,
    source_path: Path,
    submission_path: Path,
    receipt_path: Optional[Path] = None,
    clock: Callable[[], datetime] = lambda: datetime.now(SHANGHAI_TZ),
) -> int:
    if not all(isinstance(path, Path) for path in (
        source_path,
        submission_path,
    )):
        _print(_input_failed("business_material_review_import_path_unverified"))
        return 3
    source_payload = _read_json(source_path)
    submission_payload = _read_json(submission_path)
    if source_payload is None or submission_payload is None:
        _print(_input_failed("business_material_review_import_file_unverified"))
        return 3
    loaded = load_leader_business_material_review_source_packet(
        source_payload
    )
    if (
        loaded.status
        is not LeaderBusinessMaterialReviewSourcePacketStatus.READY
        or loaded.candidate_plan is None
        or loaded.review_queue is None
    ):
        payload = dict(loaded.to_evidence())
        payload["receiptPath"] = None
        _print(payload)
        return 2
    result = parse_leader_business_material_review_submission(
        loaded.candidate_plan,
        loaded.review_queue,
        submission_payload,
        clock=clock,
    )
    actual_receipt_path = receipt_path or submission_path.with_name(
        f"{submission_path.stem}-verified.json"
    )
    payload = dict(result.to_evidence())
    payload["receiptPath"] = str(actual_receipt_path)
    try:
        actual_receipt_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        payload["receiptPath"] = None
        payload["receiptWriteStatus"] = "failed"
        _print(payload)
        return 3
    _print(payload)
    return (
        0
        if result.status
        is LeaderBusinessMaterialReviewSubmissionStatus.READY
        else 2
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="校验主营材料人工复核JSON并生成脱敏回执",
    )
    parser.add_argument("source_path", type=Path)
    parser.add_argument("submission_path", type=Path)
    parser.add_argument("--receipt-path", type=Path, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _arguments()
    raise SystemExit(main(
        source_path=arguments.source_path,
        submission_path=arguments.submission_path,
        receipt_path=arguments.receipt_path,
    ))
