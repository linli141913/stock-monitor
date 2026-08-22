"""阶段6行业阈值审阅稿导出与显式批准入口。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Optional, Sequence, TextIO

from radar.sector_history_store import (
    DEFAULT_SECTOR_HISTORY_STORE_DIR,
    load_latest_sector_history_evidence,
)
from radar.sector_threshold_review import (
    approve_sector_threshold_review,
    build_sector_threshold_review_draft,
    load_sector_threshold_approval,
    publish_sector_threshold_approval,
)


DRAFT_FILENAME = "threshold-review-draft.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出真实校准审阅稿，或显式批准完整八状态阈值策略",
    )
    parser.add_argument(
        "--store-dir",
        default=str(DEFAULT_SECTOR_HISTORY_STORE_DIR),
    )
    parser.add_argument("--approve-file")
    parser.add_argument("--approved-by")
    parser.add_argument("--approved-at")
    parser.add_argument("--confirm-calibration-identity")
    return parser


def _print(value: Mapping[str, object], stdout: TextIO) -> None:
    stdout.write(json.dumps(value, ensure_ascii=False, indent=2))
    stdout.write("\n")


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _template(draft: Any) -> Mapping[str, object]:
    return {
        **draft.to_evidence(),
        "statePolicies": {
            state_id: {
                field: None for field in draft.required_policy_fields
            }
            for state_id in draft.required_state_ids
        },
        "instructions": (
            "逐状态填写entry/hold/exit条件、连续次数、最短保持秒数、"
            "冷却秒数和数据失败行为；未经显式批准不会进入运行证据。"
        ),
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    now_provider: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> int:
    arguments = _parser().parse_args(argv)
    store_dir = Path(arguments.store_dir).expanduser().resolve()
    try:
        history = load_latest_sector_history_evidence(
            store_dir=store_dir
        )
        draft = build_sector_threshold_review_draft(history)
        if arguments.approve_file is None:
            _write_atomic(store_dir / DRAFT_FILENAME, _template(draft))
            _print({
                "status": "review_ready",
                "reasons": [],
                "calibrationIdentity": draft.calibration_identity,
                "trainObservationDateCount": (
                    draft.train_observation_date_count
                ),
                "holdoutObservationDateCount": (
                    draft.holdout_observation_date_count
                ),
                "formalApproval": False,
                "gate": {
                    "formalScoreReady": False,
                    "formalGateReady": False,
                    "formalUsable": False,
                    "stateTransitionAllowed": False,
                },
            }, stdout)
            return 0
        if any(not value for value in (
            arguments.approved_by,
            arguments.approved_at,
            arguments.confirm_calibration_identity,
        )):
            raise ValueError("sector_threshold_approval_confirmation_missing")
        review_path = Path(arguments.approve_file).expanduser().resolve(
            strict=True
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if (
            not isinstance(review, Mapping)
            or review.get("calibrationIdentity")
            != draft.calibration_identity
            or arguments.confirm_calibration_identity
            != draft.calibration_identity
        ):
            raise ValueError("sector_threshold_approval_identity_mismatch")
        approved_at = datetime.fromisoformat(arguments.approved_at)
        approval = approve_sector_threshold_review(
            draft,
            state_policies=review.get("statePolicies"),
            approved_by=arguments.approved_by,
            approved_at=approved_at,
            observed_at=now_provider(),
        )
        publish_sector_threshold_approval(
            approval,
            store_dir=store_dir,
        )
        loaded = load_sector_threshold_approval(
            store_dir=store_dir,
            history_evidence=history,
        )
        if loaded.status != "approved":
            raise ValueError("sector_threshold_approval_reload_failed")
    except Exception:
        _print({
            "status": "not_approved",
            "reasons": ["sector_threshold_approval_not_verified"],
            "formalApproval": False,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }, stdout)
        return 2
    _print({
        **loaded.to_evidence(),
        "formalApproval": True,
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }, stdout)
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
