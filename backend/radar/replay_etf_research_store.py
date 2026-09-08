"""阶段9 ETF逐产品研究状态的内容寻址只读发布仓；不访问SQLite。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from radar.api_contracts import (
    RadarReplayEtfResearchItem,
    RadarReplayEtfResearchSnapshot,
)
from radar.replay_contracts import RadarReplayOutputBundle


MANIFEST_CONTRACT_ID = "radar-replay-etf-research-manifest-v1"
ALLOWED_SOURCE_CONTRACTS = {
    "radar-etf-product-research-output-v1",
    "radar-etf-product-research-output-v2",
}


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


def _sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _output_snapshot_sha256(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class RadarReplayEtfResearchStoreResult:
    status: str
    reasons: Tuple[str, ...] = ()
    snapshot: Optional[RadarReplayEtfResearchSnapshot] = None
    evidence_sha256: Optional[str] = None


def _failed(reason: str) -> RadarReplayEtfResearchStoreResult:
    return RadarReplayEtfResearchStoreResult(status="failed", reasons=(reason,))


def _snapshot_from_bundle(
    bundle: RadarReplayOutputBundle,
) -> RadarReplayEtfResearchSnapshot:
    if not isinstance(bundle, RadarReplayOutputBundle) or len(bundle.samples) != 1:
        raise ValueError("radar_replay_etf_research_unverified")
    sample = bundle.samples[0]
    evidence_items = [item for item in sample.evidence if item.domain == "etf"]
    if len(evidence_items) != 1:
        raise ValueError("radar_replay_etf_research_unverified")
    evidence = evidence_items[0]
    contract_id, separator, identity_sha = evidence.source_id.partition(":")
    payload = evidence.payload
    if any((
        evidence.status != "ready",
        separator != ":",
        contract_id not in ALLOWED_SOURCE_CONTRACTS,
        not isinstance(payload, dict),
        not _sha256(identity_sha),
        payload.get("snapshotSha256") != identity_sha,
        evidence.evidence_id != f"etf-output:{identity_sha}",
        not _sha256(payload.get("sourceSnapshotSha256")),
        payload.get("researchOnly") is not True,
        payload.get("rankingReady") is not False,
        payload.get("formalUsable") is not False,
        payload.get("stateTransitionAllowed") is not False,
    )):
        raise ValueError("radar_replay_etf_research_unverified")
    raw_states = payload.get("states")
    if not isinstance(raw_states, list):
        raise ValueError("radar_replay_etf_research_unverified")
    items = [RadarReplayEtfResearchItem(
        symbol=item.get("targetId"),
        researchState=item.get("state"),
        targetIndexName=item.get("targetIndexName"),
        monitoringStatus=item.get("monitoringStatus"),
        rankingStatus=item.get("rankingStatus"),
        monitoringReasons=item.get("monitoringReasons") or [],
        rankingReasons=item.get("rankingReasons") or [],
    ) for item in raw_states if isinstance(item, dict)]
    if len(items) != len(raw_states):
        raise ValueError("radar_replay_etf_research_unverified")
    symbols = [item.symbol for item in items]
    if len(symbols) != len(set(symbols)):
        raise ValueError("radar_replay_etf_research_unverified")
    state_counts = {
        "product_ready_for_index_research": 0,
        "active_product_separate_track": 0,
        "out_of_scope_asset": 0,
        "product_evidence_incomplete": 0,
    }
    for item in items:
        state_counts[item.research_state] += 1
    expected_counts = {
        "productCount": len(items),
        "indexResearchReadyCount": state_counts[
            "product_ready_for_index_research"
        ],
        "activeSeparateTrackCount": state_counts[
            "active_product_separate_track"
        ],
        "outOfScopeAssetCount": state_counts["out_of_scope_asset"],
        "evidenceIncompleteCount": state_counts[
            "product_evidence_incomplete"
        ],
    }
    if any(payload.get(key) != count for key, count in expected_counts.items()):
        raise ValueError("radar_replay_etf_research_unverified")
    formal_available = contract_id.endswith("-v2")
    formal_count = sum(
        item.monitoring_status is not None and item.ranking_status is not None
        for item in items
    )
    monitoring_ready = sum(item.monitoring_status == "ready" for item in items)
    monitoring_missing = sum(item.monitoring_status == "missing" for item in items)
    ranking_ready = sum(item.ranking_status == "ready" for item in items)
    ranking_missing = sum(item.ranking_status == "missing" for item in items)
    if formal_available:
        formal_counts = {
            "formalAdmissionCount": formal_count,
            "monitoringReadyCount": monitoring_ready,
            "monitoringMissingCount": monitoring_missing,
            "rankingPolicyReadyCount": ranking_ready,
            "rankingPolicyMissingCount": ranking_missing,
        }
        if any(payload.get(key) != count for key, count in formal_counts.items()):
            raise ValueError("radar_replay_etf_research_unverified")
    elif any(
        item.monitoring_status is not None or item.ranking_status is not None
        for item in items
    ):
        raise ValueError("radar_replay_etf_research_unverified")

    semantic = {
        "contractId": contract_id,
        "sampleId": sample.sample_id,
        "radarRunId": sample.radar_run_id,
        "asOf": sample.as_of.isoformat(),
        "sourceSnapshotSha256": payload["sourceSnapshotSha256"],
        "classificationMappingVersion": payload.get(
            "classificationMappingVersion"
        ),
        "states": raw_states,
        "indexResearchReadyCount": expected_counts[
            "indexResearchReadyCount"
        ],
        "activeSeparateTrackCount": expected_counts[
            "activeSeparateTrackCount"
        ],
        "outOfScopeAssetCount": expected_counts["outOfScopeAssetCount"],
        "evidenceIncompleteCount": expected_counts[
            "evidenceIncompleteCount"
        ],
    }
    if formal_available:
        semantic.update({
            "formalAdmissionSnapshotSha256": payload.get(
                "formalAdmissionSnapshotSha256"
            ),
            **formal_counts,
        })
    if _output_snapshot_sha256(semantic) != identity_sha:
        raise ValueError("radar_replay_etf_research_unverified")

    return RadarReplayEtfResearchSnapshot(
        replayOutputBundleId=bundle.bundle_id,
        sampleId=sample.sample_id,
        radarRunId=sample.radar_run_id,
        asOf=sample.as_of,
        createdAt=bundle.created_at,
        source=evidence.source,
        sourceTime=evidence.source_time,
        fetchedAt=evidence.fetched_at,
        outputSnapshotSha256=identity_sha,
        sourceSnapshotSha256=payload["sourceSnapshotSha256"],
        classificationMappingVersion=payload.get(
            "classificationMappingVersion"
        ),
        productCount=len(items),
        indexResearchReadyCount=expected_counts["indexResearchReadyCount"],
        activeSeparateTrackCount=expected_counts["activeSeparateTrackCount"],
        outOfScopeAssetCount=expected_counts["outOfScopeAssetCount"],
        evidenceIncompleteCount=expected_counts["evidenceIncompleteCount"],
        formalAdmissionAvailable=formal_available,
        formalAdmissionCount=(formal_count if formal_available else 0),
        monitoringReadyCount=(monitoring_ready if formal_available else 0),
        monitoringMissingCount=(monitoring_missing if formal_available else 0),
        rankingPolicyReadyCount=(ranking_ready if formal_available else 0),
        rankingPolicyMissingCount=(ranking_missing if formal_available else 0),
        researchOnly=True,
        rankingReady=False,
        formalUsable=False,
        stateTransitionAllowed=False,
        items=items,
        reasonCodes=[],
    )


def publish_replay_etf_research(
    bundle: RadarReplayOutputBundle,
    store_dir: Path,
) -> RadarReplayEtfResearchStoreResult:
    try:
        snapshot = _snapshot_from_bundle(bundle)
    except Exception as exc:
        raise ValueError("radar_replay_etf_research_unverified") from exc
    root = Path(store_dir).expanduser().resolve()
    payload = snapshot.model_dump(mode="json", by_alias=True)
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    relative = Path("etf-snapshots") / f"{digest}.json"
    _write(root / relative, payload)
    _write(root / "etf-latest.json", {
        "contractId": MANIFEST_CONTRACT_ID,
        "evidenceRelativePath": relative.as_posix(),
        "evidenceSha256": digest,
        "replayOutputBundleId": snapshot.replay_output_bundle_id,
        "sampleId": snapshot.sample_id,
        "radarRunId": snapshot.radar_run_id,
        "asOf": snapshot.as_of.isoformat(),
        "createdAt": snapshot.created_at.isoformat(),
    })
    return RadarReplayEtfResearchStoreResult(
        status="available",
        snapshot=snapshot,
        evidence_sha256=digest,
    )


def load_latest_replay_etf_research(
    store_dir: Path,
    *,
    checked_at: Optional[datetime] = None,
) -> RadarReplayEtfResearchStoreResult:
    root = Path(store_dir).expanduser().resolve()
    manifest_path = root / "etf-latest.json"
    if not manifest_path.is_file():
        return RadarReplayEtfResearchStoreResult(
            status="not_ready",
            reasons=("radar_replay_etf_research_missing",),
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("contractId") != MANIFEST_CONTRACT_ID:
            return _failed("radar_replay_etf_research_manifest_unverified")
        relative = Path(manifest["evidenceRelativePath"])
        expected = manifest["evidenceSha256"]
        if relative.is_absolute() or not _sha256(expected):
            return _failed("radar_replay_etf_research_manifest_unverified")
        evidence_path = (root / relative).resolve()
        evidence_path.relative_to(root)
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _failed("radar_replay_etf_research_missing")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return _failed("radar_replay_etf_research_unverified")
    actual = hashlib.sha256(_canonical(payload)).hexdigest()
    if actual != expected:
        return _failed("radar_replay_etf_research_hash_mismatch")
    try:
        snapshot = RadarReplayEtfResearchSnapshot.model_validate(payload)
    except Exception:
        return _failed("radar_replay_etf_research_unverified")
    identities = {
        "replayOutputBundleId": snapshot.replay_output_bundle_id,
        "sampleId": snapshot.sample_id,
        "radarRunId": snapshot.radar_run_id,
        "asOf": snapshot.as_of.isoformat(),
        "createdAt": snapshot.created_at.isoformat(),
    }
    if any(manifest.get(key) != value for key, value in identities.items()):
        return _failed("radar_replay_etf_research_identity_mismatch")
    now = checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("checked_at_timezone_required")
    if (snapshot.created_at - now).total_seconds() > 5:
        return _failed("radar_replay_etf_research_from_future")
    return RadarReplayEtfResearchStoreResult(
        status="available",
        snapshot=snapshot,
        evidence_sha256=expected,
    )
