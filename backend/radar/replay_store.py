"""阶段9内容寻址回放报告仓；不接触SQLite。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from radar.replay_service import RadarReplayQualityReport


DEFAULT_RADAR_REPLAY_STORE_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "radar-replays"
)
MANIFEST_CONTRACT_ID = "radar-replay-quality-manifest-v1"


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


@dataclass(frozen=True)
class RadarReplayStoreResult:
    status: str
    reasons: Tuple[str, ...] = ()
    report: Optional[RadarReplayQualityReport] = None
    evidence_sha256: Optional[str] = None


def publish_replay_report(
    report: RadarReplayQualityReport,
    store_dir: Path = DEFAULT_RADAR_REPLAY_STORE_DIR,
) -> RadarReplayStoreResult:
    if not isinstance(report, RadarReplayQualityReport):
        raise TypeError("report必须是RadarReplayQualityReport")
    root = Path(store_dir).expanduser().resolve()
    payload = report.model_dump(mode="json", by_alias=True)
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    relative = Path("snapshots") / f"{digest}.json"
    _write(root / relative, payload)
    _write(root / "latest.json", {
        "contractId": MANIFEST_CONTRACT_ID,
        "evidenceRelativePath": relative.as_posix(),
        "evidenceSha256": digest,
        "replayRunId": report.replay_run_id,
        "createdAt": report.created_at.isoformat(),
    })
    return RadarReplayStoreResult(
        status="available",
        report=report,
        evidence_sha256=digest,
    )


def _failed(reason: str) -> RadarReplayStoreResult:
    return RadarReplayStoreResult(status="failed", reasons=(reason,))


def load_latest_replay_report(
    store_dir: Path = DEFAULT_RADAR_REPLAY_STORE_DIR,
    *,
    checked_at: Optional[datetime] = None,
) -> RadarReplayStoreResult:
    root = Path(store_dir).expanduser().resolve()
    manifest_path = root / "latest.json"
    if not manifest_path.is_file():
        return RadarReplayStoreResult(
            status="not_ready",
            reasons=("radar_replay_report_missing",),
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("contractId") != MANIFEST_CONTRACT_ID:
            return _failed("radar_replay_manifest_unverified")
        relative = Path(manifest["evidenceRelativePath"])
        expected = manifest["evidenceSha256"]
        if relative.is_absolute() or len(expected) != 64:
            return _failed("radar_replay_manifest_unverified")
        evidence_path = (root / relative).resolve()
        evidence_path.relative_to(root)
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _failed("radar_replay_report_missing")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return _failed("radar_replay_report_unverified")
    actual = hashlib.sha256(_canonical(payload)).hexdigest()
    if actual != expected:
        return _failed("radar_replay_report_hash_mismatch")
    try:
        report = RadarReplayQualityReport.model_validate(payload)
    except Exception:
        return _failed("radar_replay_report_unverified")
    if (
        manifest.get("replayRunId") != report.replay_run_id
        or manifest.get("createdAt") != report.created_at.isoformat()
    ):
        return _failed("radar_replay_report_identity_mismatch")
    now = checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("checked_at_timezone_required")
    if (report.created_at - now).total_seconds() > 5:
        return _failed("radar_replay_report_from_future")
    return RadarReplayStoreResult(
        status="available",
        report=report,
        evidence_sha256=expected,
    )
