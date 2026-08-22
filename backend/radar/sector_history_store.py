"""行业真实历史工件的项目内持久索引。

逐证券分钟检查点和汇总证据保存在 Git 忽略的 ``backend/data``，本模块
只发布、校验和读取最新汇总，不接触生产 SQLite，也不暴露逐证券原始数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple


SECTOR_HISTORY_STORE_MANIFEST_CONTRACT_ID = (
    "radar-sector-history-store-manifest-v1"
)
SECTOR_HISTORY_EVIDENCE_CONTRACT_ID = (
    "radar-sector-history-automatic-backfill-v1"
)
DEFAULT_SECTOR_HISTORY_STORE_DIR = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "radar-sector-history"
)


@dataclass(frozen=True, repr=False)
class SectorHistoryStoreReadResult:
    status: str
    reasons: Tuple[str, ...]
    payload: Optional[Mapping[str, Any]] = field(default=None, repr=False)
    published_at: Optional[datetime] = None
    evidence_sha256: Optional[str] = None


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _false_gate(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("formalScoreReady") is False
        and value.get("formalGateReady") is False
        and value.get("formalUsable") is False
        and value.get("stateTransitionAllowed") is False
    )


def _evidence_valid(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    analysis = payload.get("analysis")
    calibration = (
        analysis.get("calibrationProposal")
        if isinstance(analysis, Mapping) else None
    )
    presence = payload.get("tradingPresence")
    try:
        datetime.fromisoformat(payload.get("asOf"))
    except (TypeError, ValueError):
        return False
    return bool(
        payload.get("contractId") == SECTOR_HISTORY_EVIDENCE_CONTRACT_ID
        and payload.get("status") == "ready"
        and payload.get("failureCount") == 0
        and isinstance(payload.get("requestedCount"), int)
        and payload.get("requestedCount") > 0
        and isinstance(payload.get("requestIdentity"), str)
        and len(payload.get("requestIdentity")) == 64
        and isinstance(analysis, Mapping)
        and analysis.get("status") == "ready"
        and analysis.get("historyCoverageReady") is True
        and isinstance(analysis.get("sectorCount"), int)
        and analysis.get("sectorCount") >= 20
        and isinstance(analysis.get("marketSampleCount"), int)
        and analysis.get("marketSampleCount") >= 21
        and isinstance(calibration, Mapping)
        and calibration.get("status") == "proposal_ready"
        and calibration.get("formalApproval") is False
        and isinstance(
            calibration.get("trainObservationDateCount"), int
        )
        and calibration.get("trainObservationDateCount") > 0
        and isinstance(
            calibration.get("holdoutObservationDateCount"), int
        )
        and calibration.get("holdoutObservationDateCount") > 0
        and isinstance(
            calibration.get("holdoutMetricSampleCounts"), Mapping
        )
        and isinstance(presence, Mapping)
        and presence.get("failureCount") == 0
        and _false_gate(payload.get("gate"))
        and _false_gate(analysis.get("gate"))
    )


def _failed(reason: str) -> SectorHistoryStoreReadResult:
    return SectorHistoryStoreReadResult(
        status="failed",
        reasons=(reason,),
    )


def publish_sector_history_evidence(
    evidence_path: Path,
    *,
    store_dir: Path = DEFAULT_SECTOR_HISTORY_STORE_DIR,
    published_at: datetime,
) -> SectorHistoryStoreReadResult:
    """原子发布最新真实汇总；证据必须已经位于项目历史仓内。"""

    if not _aware(published_at):
        raise ValueError("sector_history_store_published_at_unverified")
    root = Path(store_dir).expanduser().resolve()
    evidence = Path(evidence_path).expanduser().resolve(strict=True)
    try:
        relative = evidence.relative_to(root)
    except ValueError as exc:
        raise ValueError("sector_history_store_evidence_outside_store") from exc
    raw = evidence.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sector_history_store_evidence_unverified") from exc
    if not _evidence_valid(payload):
        raise ValueError("sector_history_store_evidence_unverified")
    _write_atomic(root / "latest.json", {
        "contractId": SECTOR_HISTORY_STORE_MANIFEST_CONTRACT_ID,
        "evidenceRelativePath": relative.as_posix(),
        "evidenceSha256": _sha256(raw),
        "publishedAt": published_at.isoformat(),
        "radarRunId": payload.get("radarRunId"),
        "asOf": payload.get("asOf"),
        "requestIdentity": payload.get("requestIdentity"),
    })
    return SectorHistoryStoreReadResult(
        status="available",
        reasons=(),
        payload=payload,
        published_at=published_at,
        evidence_sha256=_sha256(raw),
    )


def load_latest_sector_history_evidence(
    *,
    store_dir: Path = DEFAULT_SECTOR_HISTORY_STORE_DIR,
) -> SectorHistoryStoreReadResult:
    """跨进程读取并复核最新汇总，任何篡改都失败关闭。"""

    root = Path(store_dir).expanduser().resolve()
    manifest_path = root / "latest.json"
    if not manifest_path.is_file():
        return SectorHistoryStoreReadResult(
            status="not_ready",
            reasons=("sector_history_store_snapshot_missing",),
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative_text = manifest.get("evidenceRelativePath")
        published_at = datetime.fromisoformat(manifest.get("publishedAt"))
        expected_hash = manifest.get("evidenceSha256")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _failed("sector_history_store_manifest_unverified")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("contractId")
        != SECTOR_HISTORY_STORE_MANIFEST_CONTRACT_ID
        or not isinstance(relative_text, str)
        or not relative_text
        or Path(relative_text).is_absolute()
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or not _aware(published_at)
    ):
        return _failed("sector_history_store_manifest_unverified")
    evidence = (root / relative_text).resolve()
    try:
        evidence.relative_to(root)
    except ValueError:
        return _failed("sector_history_store_manifest_unverified")
    try:
        raw = evidence.read_bytes()
    except OSError:
        return _failed("sector_history_store_evidence_missing")
    if _sha256(raw) != expected_hash:
        return _failed("sector_history_store_evidence_hash_mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _failed("sector_history_store_evidence_unverified")
    if not _evidence_valid(payload):
        return _failed("sector_history_store_evidence_unverified")
    if any((
        manifest.get("radarRunId") != payload.get("radarRunId"),
        manifest.get("asOf") != payload.get("asOf"),
        manifest.get("requestIdentity") != payload.get("requestIdentity"),
    )):
        return _failed("sector_history_store_identity_mismatch")
    return SectorHistoryStoreReadResult(
        status="available",
        reasons=(),
        payload=payload,
        published_at=published_at,
        evidence_sha256=expected_hash,
    )
