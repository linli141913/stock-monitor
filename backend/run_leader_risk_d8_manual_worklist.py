"""从真实D2公开源发现生成离线人工D8待办清单。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from radar.leader_risk_d8_manual_worklist import (
    LeaderRiskD8ManualWorklistStatus,
    build_leader_risk_d8_manual_worklist,
)
from radar.leader_risk_official_live_delivery import (
    build_leader_risk_official_live_delivery,
)


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    if path.exists() or path.is_symlink():
        raise FileExistsError("risk_d8_output_exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    temporary.replace(path)


def _inside_private_tmp(path: Path) -> bool:
    root = Path("/private/tmp")
    return path == root or root in path.parents


def _build_live_delivery(
    candidate_source_path: Path,
    *,
    collected_at: datetime,
):
    packet = json.loads(candidate_source_path.read_text(encoding="utf-8"))
    live = build_leader_risk_official_live_delivery(
        packet,
        collected_at=collected_at,
    )
    return live.delivery, live.candidate_source_packet_sha256


def _summary(
    *,
    status: str,
    reasons: Any = (),
    source_path: Optional[Path] = None,
    review_path: Optional[Path] = None,
    source_packet_sha256: Optional[str] = None,
    candidate_count: int = 0,
    document_count: int = 0,
) -> Mapping[str, object]:
    return {
        "status": status,
        "reasons": list(reasons),
        "candidateCount": candidate_count,
        "documentCount": document_count,
        "sourcePacketPath": str(source_path) if source_path else None,
        "reviewPacketPath": str(review_path) if review_path else None,
        "sourcePacketSha256": source_packet_sha256,
        "d8VersionCount": 0,
        "d8SubmissionReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "重放真实D2公开源并生成不含结论的人工D8待办清单"
        )
    )
    parser.add_argument("--candidate-source", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--confirm-live-poc",
        action="store_true",
        help="明确允许本轮只读请求公开官方来源",
    )
    args = parser.parse_args()
    if not args.confirm_live_poc:
        parser.error("必须显式传入 --confirm-live-poc")
    try:
        candidate_source = Path(args.candidate_source).expanduser().resolve(
            strict=True
        )
        output_dir = Path(args.output_dir).expanduser().resolve()
        if (
            not _inside_private_tmp(candidate_source)
            or not _inside_private_tmp(output_dir)
            or not candidate_source.is_file()
        ):
            raise ValueError("risk_d8_candidate_source_unverified")
        output_dir.mkdir(parents=True, exist_ok=True)
        collected_at = datetime.now().astimezone()
        delivery, candidate_source_packet_sha256 = _build_live_delivery(
            candidate_source,
            collected_at=collected_at,
        )
        if delivery.as_of is None:
            raise ValueError("risk_d8_delivery_identity_unverified")
        created_at = max(
            datetime.now(delivery.as_of.tzinfo),
            delivery.as_of,
        )
        worklist = build_leader_risk_d8_manual_worklist(
            delivery,
            created_at=created_at,
            candidate_source_packet_sha256=(
                candidate_source_packet_sha256
            ),
        )
        if (
            worklist.status
            is not LeaderRiskD8ManualWorklistStatus.PENDING_HUMAN_REVIEW
        ):
            print(json.dumps(_summary(
                status=worklist.status.value,
                reasons=worklist.reasons,
            ), ensure_ascii=False, indent=2))
            return 2
        stamp = created_at.strftime("%Y%m%dT%H%M%S")
        prefix = output_dir / f"stage6-d8-manual-worklist-{stamp}"
        source_path = prefix.with_name(prefix.name + "-source.json")
        review_path = prefix.with_name(prefix.name + "-review.json")
        source_packet = worklist.to_source_packet()
        review_packet = worklist.to_review_packet()
        _write_atomic(source_path, source_packet)
        _write_atomic(review_path, review_packet)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        print(json.dumps(_summary(
            status="source_unverified",
            reasons=("risk_d8_manual_worklist_generation_failed",),
        ), ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(_summary(
        status=worklist.status.value,
        source_path=source_path,
        review_path=review_path,
        source_packet_sha256=str(source_packet["packetSha256"]),
        candidate_count=worklist.candidate_count,
        document_count=sum(len(item.documents) for item in worklist.items),
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
