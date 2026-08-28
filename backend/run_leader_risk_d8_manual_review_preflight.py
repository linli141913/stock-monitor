"""只读临时库执行现有D8人工版本预检，不保存版本。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Optional, Sequence

from pydantic import ValidationError

from radar.api_contracts import (
    RadarLeaderReviewVersionPreflightResponse,
    RadarLeaderReviewVersionRequest,
)
from radar.leader_risk_d8_manual_review_session import (
    LEADER_RISK_D8_MANUAL_REVIEW_SESSION_CONTRACT_ID,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_risk_review_service import preflight_manual_review_version


UTC = timezone.utc
OFFLINE_PREFLIGHT_CONTRACT_ID = (
    "radar-leader-risk-d8-manual-review-offline-preflight-v1"
)


def _inside_private_tmp(path: Path) -> bool:
    root = Path("/private/tmp")
    return path == root or root in path.parents


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("risk_d8_manual_review_preflight_output_exists")
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


def _false_gate(value: Any) -> bool:
    return value == {
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def _material_items(value: Any) -> Optional[tuple[Mapping[str, Any], ...]]:
    if not isinstance(value, list) or not value:
        return None
    items = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("documentId"), str)
            or not isinstance(item.get("candidateCategory"), str)
            or not isinstance(item.get("contentSha256"), str)
            or len(item["contentSha256"]) != 64
            or not isinstance(item.get("candidateId"), str)
            or item.get("review") is not None
            or item.get("d8SubmissionReady") is not False
            or not isinstance(item.get("pages"), list)
            or not item["pages"]
        ):
            return None
        items.append(item)
    identities = {
        (item["documentId"], item["candidateCategory"])
        for item in items
    }
    if len(identities) != len(items):
        return None
    return tuple(items)


def _load_material(value: Any) -> Optional[Mapping[str, Any]]:
    if not isinstance(value, Mapping) or "packetSha256" not in value:
        return None
    payload = {key: item for key, item in value.items() if key != "packetSha256"}
    packet_sha = value.get("packetSha256")
    items = _material_items(value.get("items"))
    try:
        prepared_at = datetime.fromisoformat(value.get("preparedAt", ""))
    except (TypeError, ValueError):
        return None
    if (
        value.get("contractId")
        != LEADER_RISK_D8_MANUAL_REVIEW_SESSION_CONTRACT_ID
        or value.get("status") != "pending_human_review"
        or not isinstance(value.get("reviewBatchId"), str)
        or not value["reviewBatchId"]
        or prepared_at.tzinfo is None
        or prepared_at.utcoffset() is None
        or not isinstance(packet_sha, str)
        or _digest(payload) != packet_sha
        or items is None
        or value.get("selectedCount") != len(items)
        or value.get("d8VersionCount") != 0
        or value.get("d8SubmissionReady") is not False
        or value.get("reasons") != []
        or not _false_gate(value.get("gate"))
    ):
        return None
    return {
        "packetSha256": packet_sha,
        "reviewBatchId": value["reviewBatchId"],
        "preparedAt": prepared_at,
        "items": items,
    }


def _bound_submission(
    material: Mapping[str, Any],
    request: RadarLeaderReviewVersionRequest,
) -> bool:
    matches = tuple(
        item
        for item in material["items"]
        if (
            item["documentId"] == request.document_id
            and item["candidateCategory"] == request.candidate_category
        )
    )
    return bool(
        request.review_batch_id == material["reviewBatchId"]
        and len(matches) == 1
        and matches[0]["contentSha256"] == request.content_sha256
        and matches[0]["candidateId"] == request.candidate_id
    )


def _summary(
    *,
    status: str,
    reasons: Sequence[str] = (),
    output_path: Optional[Path] = None,
) -> Mapping[str, object]:
    return {
        "status": status,
        "reasons": list(reasons),
        "outputPath": str(output_path) if output_path else None,
        "d8VersionPersisted": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    parser = argparse.ArgumentParser(
        description="只读执行现有D8预检，不保存人工版本",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--material", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--confirm-human-review", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_human_review:
        parser.error("必须显式传入 --confirm-human-review")

    try:
        database_path = Path(args.database).expanduser().resolve(strict=True)
        material_path = Path(args.material).expanduser().resolve(strict=True)
        submission_path = Path(args.submission).expanduser().resolve(strict=True)
        output_dir = Path(args.output_dir).expanduser().resolve()
        paths = (database_path, material_path, submission_path, output_dir)
        if (
            any(not _inside_private_tmp(path) for path in paths)
            or not database_path.is_file()
            or not material_path.is_file()
            or not submission_path.is_file()
        ):
            raise ValueError("risk_d8_manual_review_preflight_path_unverified")
        material = _load_material(json.loads(
            material_path.read_text(encoding="utf-8")
        ))
        if material is None:
            raise ValueError("risk_d8_manual_review_preflight_material_unverified")
        request = RadarLeaderReviewVersionRequest.model_validate(json.loads(
            submission_path.read_text(encoding="utf-8")
        ))
        if not _bound_submission(material, request):
            raise ValueError("risk_d8_manual_review_preflight_identity_mismatch")
        checked_at = clock()
        if (
            not isinstance(checked_at, datetime)
            or checked_at.tzinfo is None
            or checked_at.utcoffset() is None
            or checked_at < material["preparedAt"]
        ):
            raise ValueError("risk_d8_manual_review_preflight_clock_unverified")
        checked_at = checked_at.astimezone(UTC)

        connection = sqlite3.connect(
            database_path.as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            repository = LeaderRiskReviewRepository(
                connection,
                clock=lambda: checked_at,
            )
            before_count = connection.execute(
                "SELECT COUNT(*) FROM "
                "radar_leader_risk_manual_review_versions"
            ).fetchone()[0]
            result = preflight_manual_review_version(
                repository,
                request,
                as_of=checked_at,
            )
            after_count = connection.execute(
                "SELECT COUNT(*) FROM "
                "radar_leader_risk_manual_review_versions"
            ).fetchone()[0]
        finally:
            connection.close()
        if before_count != 0 or after_count != 0:
            raise ValueError("risk_d8_manual_review_preflight_version_detected")

        preflight = RadarLeaderReviewVersionPreflightResponse(
            checkedAt=checked_at,
            reviewBatchId=request.review_batch_id,
            documentId=request.document_id,
            candidateCategory=request.candidate_category,
            writeEnabled=False,
            submissionAllowed=False,
            **result,
        ).model_dump(mode="json", by_alias=True)
        payload = {
            "contractId": OFFLINE_PREFLIGHT_CONTRACT_ID,
            "status": "validated_not_persisted",
            "checkedAt": checked_at.isoformat(),
            "materialPacketSha256": material["packetSha256"],
            "databaseSha256": _file_digest(database_path),
            "preflight": preflight,
            "d8VersionPersisted": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }
        packet = {**payload, "packetSha256": _digest(payload)}
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = checked_at.strftime("%Y%m%dT%H%M%S%fZ")
        output_path = output_dir / (
            f"stage6-d8-manual-review-preflight-{stamp}.json"
        )
        _write_atomic(output_path, packet)
    except (
        OSError,
        TypeError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
        sqlite3.Error,
    ):
        print(json.dumps(_summary(
            status="source_unverified",
            reasons=("risk_d8_manual_review_preflight_failed",),
        ), ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(_summary(
        status="validated_not_persisted",
        output_path=output_path,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
