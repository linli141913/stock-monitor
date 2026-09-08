"""阶段9确定性规则输出桥接。

只读取调用方显式指定的 ``/private/tmp`` 行业状态快照、前向样本与阶段6工件，
重新校验内容摘要、规则身份、运行身份和时点边界后，导出可供回放聚合的
``RadarReplayOutputBundle``。不读取 SQLite、不联网，也不生成ETF排名或
正式龙头状态；ETF只导出官方产品分类研究状态，阶段6只导出已有的市场
研究状态和证据资格结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from radar.replay_contracts import (
    RadarReplayEvidence,
    RadarReplayInput,
    RadarReplayOutputBundle,
    RadarReplaySampleOutputSet,
)
from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfProductMasterRecord,
    ListedFundProductType,
)
from radar.etf_formal_admission import (
    EtfFormalAdmissionBundle,
    EtfFormalAdmissionEvidence,
    EtfFormalAdmissionStatus,
    load_etf_formal_admission_bundle,
)
from radar.replay_forward_baseline import _atomic_write_json, _validate_output_dir
from radar.sector_state_producer import load_sector_state_snapshot
from radar.sources.etf_product_master import (
    ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION,
)


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_timezone_required")
    return value


def _private_tmp_file(value: Path) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(Path("/private/tmp").resolve())
    except ValueError as exc:
        raise ValueError("sector_output_path_unverified") from exc
    if not path.is_file():
        raise ValueError("sector_output_path_unverified")
    return path


@dataclass(frozen=True)
class RadarReplayOutputBridgeResult:
    output_dir: Path
    output_bundle_path: Path
    manifest_path: Path
    snapshot_sha256: str
    bundle: RadarReplayOutputBundle
    market_snapshot_sha256: str | None = None
    leader_snapshot_sha256: str | None = None
    etf_snapshot_sha256: str | None = None


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _closed_gate(value: object) -> bool:
    return isinstance(value, dict) and all(
        value.get(key) is False
        for key in (
            "formalScoreReady",
            "formalGateReady",
            "formalUsable",
            "stateTransitionAllowed",
        )
    )


def _load_market_research_output(
    path: Path,
    *,
    radar_run_id: str,
    sample_as_of: datetime,
) -> tuple[RadarReplayEvidence, str]:
    source_path = _private_tmp_file(path)
    try:
        artifact = json.loads(source_path.read_text(encoding="utf-8"))
        state = artifact["marketResearchState"]
        observed_at = datetime.fromisoformat(state["asOf"])
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("market_output_artifact_unverified") from exc
    if (
        not isinstance(state, dict)
        or state.get("contractId") != "radar-market-research-state-v1"
        or state.get("status") != "ready"
        or state.get("researchUsable") is not True
        or state.get("formalUsable") is not False
        or state.get("state") not in {
            "strong", "oscillation", "retreat", "risk",
        }
        or not isinstance(state.get("metrics"), dict)
        or not isinstance(state.get("ruleVersion"), str)
        or not state["ruleVersion"].strip()
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(state.get("snapshotSha256") or ""),
        ) is None
    ):
        raise ValueError("market_output_artifact_unverified")
    _aware(observed_at, "marketObservedAt")
    if state.get("radarRunId") != radar_run_id:
        raise ValueError("market_output_radar_run_mismatch")
    if observed_at > sample_as_of:
        raise ValueError("market_output_from_future")
    file_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return RadarReplayEvidence(
        evidenceId=f"market-output:{state['snapshotSha256']}",
        domain="market",
        sourceId=(
            f"{state['contractId']}:{state['snapshotSha256']}"
        ),
        source="确定性市场研究状态生产器",
        sourceTime=observed_at,
        fetchedAt=observed_at,
        effectiveFrom=None,
        status="ready",
        payload={
            "snapshotSha256": state["snapshotSha256"],
            "ruleVersion": state["ruleVersion"],
            "states": [{
                "targetId": "a-share",
                "state": state["state"],
            }],
            "metrics": state["metrics"],
            "researchOnly": True,
            "formalUsable": False,
        },
    ), file_sha256


def _load_leader_research_output(
    path: Path,
    *,
    radar_run_id: str,
    sample_as_of: datetime,
) -> tuple[RadarReplayEvidence, str]:
    source_path = _private_tmp_file(path)
    try:
        artifact = json.loads(source_path.read_text(encoding="utf-8"))
        collection = artifact["collection"]
        qualification = collection["qualification"]
        review = collection["stateDecisionReview"]
        observed_at = datetime.fromisoformat(review["asOf"])
        items = review["items"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("leader_output_artifact_unverified") from exc
    _aware(observed_at, "leaderObservedAt")
    qualified_count = review.get("qualifiedCandidateCount")
    excluded_count = review.get("excludedCandidateCount")
    evaluated_count = review.get("parentCandidateCount")
    qualification_id = review.get("qualificationId")
    integer_counts = all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (evaluated_count, qualified_count, excluded_count)
    )
    if any((
        not isinstance(collection, dict),
        collection.get("contractId")
        != "radar-leader-phase6-live-source-collection-v1",
        collection.get("status") != "ready_for_review",
        collection.get("validEmptyResult") is not False,
        not isinstance(qualification, dict),
        qualification.get("contractId")
        != "radar-leader-evidence-qualification-v1",
        qualification.get("status") != "ready",
        not _closed_gate(qualification.get("gate")),
        not isinstance(review, dict),
        review.get("contractId")
        != "radar-leader-phase6-state-decision-review-v1",
        review.get("status") != "ready_for_review",
        review.get("radarRunId") != radar_run_id,
        observed_at > sample_as_of,
        not _closed_gate(review.get("gate")),
        not integer_counts,
        (
            integer_counts
            and min(evaluated_count, qualified_count, excluded_count) < 0
        ),
        (
            integer_counts
            and qualified_count + excluded_count != evaluated_count
        ),
        not isinstance(qualification_id, str),
        re.fullmatch(r"[0-9a-f]{64}", qualification_id or "") is None,
        not isinstance(items, list),
        integer_counts and len(items) != evaluated_count,
        qualification.get("qualificationId") != qualification_id,
        qualification.get("parentCandidatePlanId")
        != review.get("parentCandidatePlanId"),
        qualification.get("parentCandidateCount") != evaluated_count,
        qualification.get("qualifiedCandidatePlanId")
        != review.get("qualifiedCandidatePlanId"),
        qualification.get("qualifiedCandidateCount") != qualified_count,
        qualification.get("excludedCandidateCount") != excluded_count,
    )):
        raise ValueError("leader_output_artifact_unverified")

    qualified_items = []
    qualified_indexes = set()
    symbols = set()
    for expected_index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("leader_output_artifact_unverified")
        symbol = item.get("symbol")
        industry_code = item.get("industryCode")
        status = item.get("qualificationStatus")
        candidate_index = item.get("qualifiedCandidateIndex")
        reasons = item.get("reasons")
        first_rejection = item.get("firstRejectionReason")
        if any((
            item.get("index") != expected_index,
            not isinstance(symbol, str),
            re.fullmatch(r"\d{6}", symbol or "") is None,
            symbol in symbols,
            not isinstance(industry_code, str),
            not industry_code.strip(),
            status not in {"qualified", "excluded"},
            not isinstance(reasons, list),
            not reasons,
            (
                isinstance(reasons, list)
                and any(
                    not isinstance(reason, str) or not reason
                    for reason in reasons
                )
            ),
            not isinstance(first_rejection, str),
            not first_rejection,
            isinstance(reasons, list)
            and bool(reasons)
            and first_rejection != reasons[0],
        )):
            raise ValueError("leader_output_artifact_unverified")
        symbols.add(symbol)
        if status == "qualified":
            candidate_index_valid = (
                isinstance(candidate_index, int)
                and not isinstance(candidate_index, bool)
            )
            if any((
                item.get("reviewEligible") is not True,
                not candidate_index_valid,
                candidate_index_valid and candidate_index < 0,
                candidate_index_valid and candidate_index >= qualified_count,
                candidate_index_valid and candidate_index in qualified_indexes,
            )):
                raise ValueError("leader_output_artifact_unverified")
            qualified_indexes.add(candidate_index)
            qualified_items.append((candidate_index, {
                "targetId": symbol,
                "state": "research_qualified",
                "industryCode": industry_code,
                "firstRejectionReason": first_rejection,
            }))
        elif any((
            item.get("reviewEligible") is not False,
            candidate_index is not None,
        )):
            raise ValueError("leader_output_artifact_unverified")
    if (
        len(qualified_items) != qualified_count
        or qualified_indexes != set(range(qualified_count))
    ):
        raise ValueError("leader_output_artifact_unverified")
    states = [
        state for _, state in sorted(qualified_items, key=lambda value: value[0])
    ]
    semantic = {
        "contractId": review["contractId"],
        "radarRunId": radar_run_id,
        "asOf": observed_at.isoformat(),
        "qualificationId": qualification_id,
        "parentCandidatePlanId": review["parentCandidatePlanId"],
        "qualifiedCandidatePlanId": review["qualifiedCandidatePlanId"],
        "evaluatedCount": evaluated_count,
        "qualifiedCount": qualified_count,
        "excludedCount": excluded_count,
        "states": states,
    }
    snapshot_sha256 = _canonical_sha256(semantic)
    file_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return RadarReplayEvidence(
        evidenceId=f"leader-output:{snapshot_sha256}",
        domain="leader",
        sourceId=f"{review['contractId']}:{snapshot_sha256}",
        source="阶段6确定性证据资格规则",
        sourceTime=observed_at,
        fetchedAt=observed_at,
        effectiveFrom=None,
        status="ready",
        payload={
            "snapshotSha256": snapshot_sha256,
            "ruleVersion": "radar-leader-phase6-research-qualification-v1",
            "qualificationId": qualification_id,
            "evaluatedCount": evaluated_count,
            "qualifiedCount": qualified_count,
            "excludedCount": excluded_count,
            "states": states,
            "researchOnly": True,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    ), file_sha256


def _load_etf_product_research_output(
    path: Path,
    *,
    sample_id: str,
    radar_run_id: str,
    sample_as_of: datetime,
    formal_admission_bundle: EtfFormalAdmissionBundle | None = None,
) -> tuple[RadarReplayEvidence, str]:
    """Export product-layer ETF states without implying a ranking result."""
    source_path = _private_tmp_file(path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        replay = RadarReplayInput.model_validate(raw)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("etf_output_artifact_unverified") from exc
    samples = [
        item for item in replay.samples
        if (
            item.sample_id == sample_id
            and item.radar_run_id == radar_run_id
            and item.as_of == sample_as_of
        )
    ]
    if len(samples) != 1:
        raise ValueError("etf_output_sample_identity_mismatch")
    sample = samples[0]
    evidence_items = [
        item for item in sample.evidence
        if (
            item.domain == "etf"
            and item.payload.get("observationKind") == "forward_observed"
        )
    ]
    if len(evidence_items) != 1:
        raise ValueError("etf_output_artifact_unverified")
    source_evidence = evidence_items[0]
    payload = source_evidence.payload
    expected_hash = payload.get("snapshotSha256")
    hash_payload = dict(payload)
    hash_payload.pop("snapshotSha256", None)
    records_raw = payload.get("records")
    if any((
        source_evidence.status != "ready",
        source_evidence.source
        != "official_exchange_listed_fund_product_master",
        source_evidence.fetched_at > sample_as_of,
        not isinstance(records_raw, list),
        payload.get("recordCount") != len(records_raw or ()),
        payload.get("expectedCount") != len(records_raw or ()),
        payload.get("rowCoverage") != 1.0,
        payload.get("issues") != [],
        payload.get("reasons") != [],
        not isinstance(expected_hash, str),
        expected_hash != _canonical_sha256(hash_payload),
    )):
        raise ValueError("etf_output_artifact_unverified")
    try:
        products = tuple(
            EtfProductMasterRecord.model_validate(item)
            for item in records_raw
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("etf_output_artifact_unverified") from exc
    if (
        len({item.symbol for item in products}) != len(products)
        or any(
            item.fetched_at > sample_as_of
            or item.classification_mapping_version
            != ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION
            for item in products
        )
    ):
        raise ValueError("etf_output_artifact_unverified")

    admissions_by_symbol: dict[str, EtfFormalAdmissionEvidence] = {}
    formal_admission_snapshot_sha256 = None
    if formal_admission_bundle is not None:
        if any((
            formal_admission_bundle.sample_id != sample_id,
            formal_admission_bundle.radar_run_id != radar_run_id,
            formal_admission_bundle.as_of != sample_as_of,
        )):
            raise ValueError("etf_formal_admission_identity_mismatch")
        admissions_by_symbol = {
            item.symbol: item
            for item in formal_admission_bundle.admissions
        }
        if not set(admissions_by_symbol).issubset({
            item.symbol for item in products
        }):
            raise ValueError("etf_formal_admission_product_mismatch")
        formal_admission_snapshot_sha256 = (
            formal_admission_bundle.snapshot_sha256
        )

    states = []
    counts = {
        "indexResearchReadyCount": 0,
        "activeSeparateTrackCount": 0,
        "outOfScopeAssetCount": 0,
        "evidenceIncompleteCount": 0,
    }
    formal_counts = {
        "formalAdmissionCount": len(admissions_by_symbol),
        "monitoringReadyCount": 0,
        "monitoringMissingCount": 0,
        "rankingPolicyReadyCount": 0,
        "rankingPolicyMissingCount": 0,
    }
    for product in sorted(products, key=lambda item: item.symbol):
        if product.product_type != ListedFundProductType.ETF:
            continue
        state = {"targetId": product.symbol}
        if product.management_style == EtfManagementStyle.ACTIVE:
            state["state"] = "active_product_separate_track"
            counts["activeSeparateTrackCount"] += 1
        elif (
            product.management_style == EtfManagementStyle.PASSIVE_INDEX
            and product.asset_class == EtfAssetClass.DOMESTIC_EQUITY
            and product.target_index_name is not None
            and not product.classification_reasons
        ):
            state.update({
                "state": "product_ready_for_index_research",
                "targetIndexName": product.target_index_name,
            })
            counts["indexResearchReadyCount"] += 1
        elif product.asset_class not in {
            EtfAssetClass.DOMESTIC_EQUITY,
            EtfAssetClass.UNKNOWN,
        }:
            state["state"] = "out_of_scope_asset"
            counts["outOfScopeAssetCount"] += 1
        else:
            state["state"] = "product_evidence_incomplete"
            counts["evidenceIncompleteCount"] += 1
        admission = admissions_by_symbol.get(product.symbol)
        if admission is not None:
            if state["state"] != "product_ready_for_index_research":
                raise ValueError("etf_formal_admission_product_mismatch")
            monitoring_reasons = list(dict.fromkeys(
                reason
                for item in admission.items[:-1]
                for reason in item.reasons
            ))
            ranking_reasons = list(admission.items[-1].reasons)
            state.update({
                "monitoringStatus": admission.monitoring_status.value,
                "rankingStatus": admission.ranking_status.value,
                "monitoringReasons": monitoring_reasons,
                "rankingReasons": ranking_reasons,
            })
            if admission.monitoring_status == EtfFormalAdmissionStatus.READY:
                formal_counts["monitoringReadyCount"] += 1
            else:
                formal_counts["monitoringMissingCount"] += 1
            if admission.ranking_status == EtfFormalAdmissionStatus.READY:
                formal_counts["rankingPolicyReadyCount"] += 1
            else:
                formal_counts["rankingPolicyMissingCount"] += 1
        states.append(state)

    output_contract_id = (
        "radar-etf-product-research-output-v2"
        if formal_admission_bundle is not None
        else "radar-etf-product-research-output-v1"
    )
    semantic = {
        "contractId": output_contract_id,
        "sampleId": sample_id,
        "radarRunId": radar_run_id,
        "asOf": sample_as_of.isoformat(),
        "sourceSnapshotSha256": expected_hash,
        "classificationMappingVersion": (
            ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION
        ),
        "states": states,
        **counts,
    }
    if formal_admission_bundle is not None:
        semantic.update({
            "formalAdmissionSnapshotSha256": (
                formal_admission_snapshot_sha256
            ),
            **formal_counts,
        })
    snapshot_sha256 = _canonical_sha256(semantic)
    file_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return RadarReplayEvidence(
        evidenceId=f"etf-output:{snapshot_sha256}",
        domain="etf",
        sourceId=(
            f"{output_contract_id}:{snapshot_sha256}"
        ),
        source=(
            "沪深交易所官方ETF产品分类与正式监测准入输出"
            if formal_admission_bundle is not None
            else "沪深交易所官方ETF产品分类研究输出"
        ),
        sourceTime=(
            sample_as_of
            if formal_admission_bundle is not None
            else source_evidence.source_time
        ),
        fetchedAt=(
            sample_as_of
            if formal_admission_bundle is not None
            else source_evidence.fetched_at
        ),
        effectiveFrom=None,
        status="ready",
        payload={
            "snapshotSha256": snapshot_sha256,
            "sourceSnapshotSha256": expected_hash,
            "ruleVersion": "radar-etf-product-research-rule-v1",
            "classificationMappingVersion": (
                ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION
            ),
            "productCount": len(states),
            **counts,
            "rankingReadyCount": 0,
            "states": states,
            "researchOnly": True,
            "rankingReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
            **(
                {
                    "formalAdmissionSnapshotSha256": (
                        formal_admission_snapshot_sha256
                    ),
                    **formal_counts,
                }
                if formal_admission_bundle is not None
                else {}
            ),
        },
    ), file_sha256


def _load_etf_formal_admission_artifact(
    path: Path,
    *,
    sample_id: str,
    radar_run_id: str,
    sample_as_of: datetime,
) -> tuple[EtfFormalAdmissionBundle, str]:
    source_path = _private_tmp_file(path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        bundle = load_etf_formal_admission_bundle(raw)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "etf_formal_admission_artifact_unverified"
        ) from exc
    if any((
        bundle.sample_id != sample_id,
        bundle.radar_run_id != radar_run_id,
        bundle.as_of != sample_as_of,
    )):
        raise ValueError("etf_formal_admission_identity_mismatch")
    return bundle, hashlib.sha256(source_path.read_bytes()).hexdigest()


def export_sector_replay_output(
    *,
    sample_id: str,
    radar_run_id: str,
    sample_as_of: datetime,
    sector_snapshot_path: Path,
    stage6_artifact_path: Path | None = None,
    etf_replay_input_path: Path | None = None,
    etf_formal_admission_path: Path | None = None,
    output_dir: Path,
    clock: Callable[[], datetime],
) -> RadarReplayOutputBridgeResult:
    sample_id = str(sample_id or "").strip()
    radar_run_id = str(radar_run_id or "").strip()
    if not sample_id or not radar_run_id or not callable(clock):
        raise ValueError("sector_output_identity_unverified")
    sample_as_of = _aware(sample_as_of, "sampleAsOf")
    source_path = _private_tmp_file(sector_snapshot_path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(raw["observedAt"])
        industry_codes = tuple(raw["industryCodes"])
        classification_sha256 = raw["classificationDocumentSha256"]
        rule_version = raw["ruleVersion"]
        transition_policy_version = raw["transitionPolicyVersion"]
        threshold_set_id = raw["thresholdSetId"]
        approval_id = raw["approvalId"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("sector_output_snapshot_unverified") from exc
    _aware(observed_at, "sectorObservedAt")
    if observed_at > sample_as_of:
        raise ValueError("sector_output_from_future")
    try:
        snapshot = load_sector_state_snapshot(
            source_path,
            expected_industry_codes=industry_codes,
            classification_document_sha256=classification_sha256,
            rule_version=rule_version,
            transition_policy_version=transition_policy_version,
            threshold_set_id=threshold_set_id,
            approval_id=approval_id,
            before_as_of=sample_as_of + timedelta(microseconds=1),
        )
    except ValueError as exc:
        raise ValueError("sector_output_snapshot_unverified") from exc
    if snapshot.last_quote_batch_id != f"{radar_run_id}-quotes":
        raise ValueError("sector_output_radar_run_mismatch")

    created_at = _aware(clock(), "createdAt")
    if created_at < sample_as_of:
        raise ValueError("sector_output_created_before_sample")
    evidence = RadarReplayEvidence(
        evidenceId=f"sector-output:{snapshot.snapshot_sha256}",
        domain="sector",
        sourceId=(
            f"{snapshot.contract_id}:{snapshot.snapshot_sha256}"
        ),
        source="确定性行业状态生产器",
        sourceTime=snapshot.observed_at,
        fetchedAt=snapshot.observed_at,
        effectiveFrom=None,
        status="ready",
        payload={
            "snapshotSha256": snapshot.snapshot_sha256,
            "ruleVersion": snapshot.rule_version,
            "transitionPolicyVersion": snapshot.transition_policy_version,
            "thresholdSetId": snapshot.threshold_set_id,
            "states": [
                {
                    "targetId": item.industry_code,
                    "state": item.state.value,
                }
                for item in snapshot.records
            ],
        },
    )
    evidence_items = [evidence]
    market_file_sha256 = None
    market_snapshot_sha256 = None
    leader_snapshot_sha256 = None
    etf_snapshot_sha256 = None
    if stage6_artifact_path is not None:
        stage6_path = _private_tmp_file(stage6_artifact_path)
        try:
            stage6_payload = json.loads(stage6_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("stage6_output_artifact_unverified") from exc
        if not isinstance(stage6_payload, dict) or not any(
            key in stage6_payload
            for key in ("marketResearchState", "collection")
        ):
            raise ValueError("stage6_output_artifact_unverified")
        market_file_sha256 = hashlib.sha256(stage6_path.read_bytes()).hexdigest()
        if "marketResearchState" in stage6_payload:
            market_evidence, _ = _load_market_research_output(
                stage6_path,
                radar_run_id=radar_run_id,
                sample_as_of=sample_as_of,
            )
            market_snapshot_sha256 = market_evidence.payload["snapshotSha256"]
            evidence_items.insert(0, market_evidence)
        if "collection" in stage6_payload:
            leader_evidence, _ = _load_leader_research_output(
                stage6_path,
                radar_run_id=radar_run_id,
                sample_as_of=sample_as_of,
            )
            leader_snapshot_sha256 = leader_evidence.payload["snapshotSha256"]
            evidence_items.append(leader_evidence)
    etf_file_sha256 = None
    etf_formal_admission_file_sha256 = None
    etf_formal_admission_bundle = None
    if etf_formal_admission_path is not None:
        if etf_replay_input_path is None:
            raise ValueError("etf_formal_admission_product_input_missing")
        (
            etf_formal_admission_bundle,
            etf_formal_admission_file_sha256,
        ) = _load_etf_formal_admission_artifact(
            etf_formal_admission_path,
            sample_id=sample_id,
            radar_run_id=radar_run_id,
            sample_as_of=sample_as_of,
        )
    if etf_replay_input_path is not None:
        etf_evidence, etf_file_sha256 = _load_etf_product_research_output(
            etf_replay_input_path,
            sample_id=sample_id,
            radar_run_id=radar_run_id,
            sample_as_of=sample_as_of,
            formal_admission_bundle=etf_formal_admission_bundle,
        )
        etf_snapshot_sha256 = etf_evidence.payload["snapshotSha256"]
        evidence_items.append(etf_evidence)
    output_semantic_sha256 = _canonical_sha256([
        item.model_dump(mode="json", by_alias=True)
        for item in evidence_items
    ])
    bundle_identity = hashlib.sha256(
        (
            f"{sample_id}|{radar_run_id}|{sample_as_of.isoformat()}|"
            f"{snapshot.snapshot_sha256}|{output_semantic_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    bundle = RadarReplayOutputBundle(
        bundleId=f"stage9-sector-output-{bundle_identity[:24]}",
        createdAt=created_at,
        samples=[RadarReplaySampleOutputSet(
            sampleId=sample_id,
            radarRunId=radar_run_id,
            asOf=sample_as_of,
            evidence=evidence_items,
        )],
    )
    resolved_output = _validate_output_dir(Path(output_dir))
    resolved_output.mkdir(parents=True, exist_ok=False)
    bundle_path = resolved_output / "output-bundle.json"
    manifest_path = resolved_output / "manifest.json"
    bundle_sha256 = _atomic_write_json(bundle_path, bundle)
    manifest_payload = {
        "contractId": "radar-replay-output-bridge-manifest-v1",
        "bundleId": bundle.bundle_id,
        "createdAt": created_at,
        "sourceSnapshot": {
            "path": str(source_path),
            "fileSha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "snapshotSha256": snapshot.snapshot_sha256,
        },
        "files": {
            "outputBundle": {
                "path": bundle_path.name,
                "sha256": bundle_sha256,
            },
        },
        "outputSemanticSha256": output_semantic_sha256,
    }
    if stage6_artifact_path is not None:
        manifest_payload["sourceStage6Artifact"] = {
            "path": str(_private_tmp_file(stage6_artifact_path)),
            "fileSha256": market_file_sha256,
        }
    if etf_replay_input_path is not None:
        manifest_payload["sourceEtfReplayInput"] = {
            "path": str(_private_tmp_file(etf_replay_input_path)),
            "fileSha256": etf_file_sha256,
        }
    if etf_formal_admission_path is not None:
        manifest_payload["sourceEtfFormalAdmission"] = {
            "path": str(_private_tmp_file(etf_formal_admission_path)),
            "fileSha256": etf_formal_admission_file_sha256,
            "snapshotSha256": (
                etf_formal_admission_bundle.snapshot_sha256
                if etf_formal_admission_bundle is not None
                else None
            ),
        }
    _atomic_write_json(manifest_path, manifest_payload)
    return RadarReplayOutputBridgeResult(
        output_dir=resolved_output,
        output_bundle_path=bundle_path,
        manifest_path=manifest_path,
        snapshot_sha256=snapshot.snapshot_sha256,
        bundle=bundle,
        market_snapshot_sha256=market_snapshot_sha256,
        leader_snapshot_sha256=leader_snapshot_sha256,
        etf_snapshot_sha256=etf_snapshot_sha256,
    )
