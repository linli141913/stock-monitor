"""在/private/tmp临时库中处理最多三份真实D8人工待审公告。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Optional, Sequence

from radar.leader_risk_d8_manual_review_session import (
    LeaderRiskD8ManualReviewSelection,
    LeaderRiskD8ManualReviewSessionStatus,
    load_leader_risk_d8_manual_review_packet,
    prepare_leader_risk_d8_manual_review_session,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.migrations import (
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    fetch_official_risk_document_content,
)


UTC = timezone.utc


def _inside_private_tmp(path: Path) -> bool:
    root = Path("/private/tmp")
    return path == root or root in path.parents


def _selection(value: str) -> LeaderRiskD8ManualReviewSelection:
    document_id, separator, category = value.partition("=")
    if not separator or not document_id.strip() or not category.strip():
        raise argparse.ArgumentTypeError(
            "公告选择格式必须为documentId=candidateCategory"
        )
    return LeaderRiskD8ManualReviewSelection(
        document_id=document_id.strip(),
        candidate_category=category.strip(),
    )


def _create_private_file(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("risk_d8_manual_review_session_output_exists")
    temporary = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _summary(
    *,
    status: str,
    reasons: Sequence[str] = (),
    database_path: Optional[Path] = None,
    material_path: Optional[Path] = None,
    selected_count: int = 0,
) -> Mapping[str, object]:
    return {
        "status": status,
        "reasons": list(reasons),
        "selectedCount": selected_count,
        "databasePath": str(database_path) if database_path else None,
        "materialPath": str(material_path) if material_path else None,
        "d8VersionCount": 0,
        "d8SubmissionReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    fetcher: Callable[..., OfficialRiskDocumentContentResult] = (
        fetch_official_risk_document_content
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "从真实D2待办中下载官方正文，生成未填结论的D8人工材料"
        )
    )
    parser.add_argument("--review-worklist", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--document",
        action="append",
        required=True,
        type=_selection,
    )
    parser.add_argument("--confirm-live-poc", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_poc:
        parser.error("必须显式传入 --confirm-live-poc")

    database_path = None
    try:
        worklist_path = Path(args.review_worklist).expanduser().resolve(
            strict=True
        )
        output_dir = Path(args.output_dir).expanduser().resolve()
        if (
            not _inside_private_tmp(worklist_path)
            or not _inside_private_tmp(output_dir)
            or not worklist_path.is_file()
        ):
            raise ValueError("risk_d8_manual_review_session_path_unverified")
        raw_packet = json.loads(worklist_path.read_text(encoding="utf-8"))
        loaded = load_leader_risk_d8_manual_review_packet(raw_packet)
        if loaded is None:
            raise ValueError("risk_d8_manual_review_session_packet_unverified")
        prepared_at = clock()
        if (
            not isinstance(prepared_at, datetime)
            or prepared_at.tzinfo is None
            or prepared_at.utcoffset() is None
        ):
            raise ValueError("risk_d8_manual_review_session_clock_unverified")
        prepared_at = prepared_at.astimezone(UTC)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = prepared_at.strftime("%Y%m%dT%H%M%S%fZ")
        prefix = output_dir / f"stage6-d8-manual-review-session-{stamp}"
        database_path = prefix.with_suffix(".sqlite")
        material_path = prefix.with_suffix(".json")
        _create_private_file(database_path)

        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            apply_pending_migrations(
                connection,
                migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
                clock=lambda: prepared_at,
            )
            as_of = loaded.as_of.isoformat()
            connection.execute(
                "INSERT INTO radar_runs (radar_run_id, as_of, status, "
                "shadow_mode, started_at, created_at) "
                "VALUES (?, ?, 'succeeded', 1, ?, ?)",
                (loaded.radar_run_id, as_of, as_of, as_of),
            )
            connection.commit()
            result = prepare_leader_risk_d8_manual_review_session(
                raw_packet,
                tuple(args.document),
                repository=LeaderRiskReviewRepository(
                    connection,
                    clock=lambda: prepared_at,
                ),
                prepared_at=prepared_at,
                confirmed=True,
                fetcher=fetcher,
            )
        finally:
            connection.close()
        _write_atomic(material_path, result.to_packet())
    except (OSError, TypeError, ValueError, json.JSONDecodeError, sqlite3.Error):
        if database_path is not None:
            try:
                database_path.unlink(missing_ok=True)
            except OSError:
                pass
        print(json.dumps(_summary(
            status="source_unverified",
            reasons=("risk_d8_manual_review_session_failed",),
        ), ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(_summary(
        status=result.status.value,
        reasons=result.reasons,
        database_path=database_path,
        material_path=material_path,
        selected_count=len(result.items),
    ), ensure_ascii=False, indent=2))
    return (
        0
        if result.status
        is LeaderRiskD8ManualReviewSessionStatus.PENDING_HUMAN_REVIEW
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
