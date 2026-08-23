"""候选全集官方主营证据的本地只读编排器。

只写显式工件目录，不保存原始 PDF，不读写数据库，不打开正式门。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from radar.leader_business_annual_report_selector import (
    select_latest_official_annual_report,
)
from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_catalyst_facts import (
    OfficialBusinessCatalystFactResult,
    extract_official_business_catalyst_facts,
)
from radar.leader_business_catalyst_features import BusinessCatalystRelation
from radar.leader_business_deterministic_verification import (
    DeterministicOfficialBusinessVerificationArtifact,
    build_deterministic_official_business_verification,
    replay_deterministic_official_business_verification,
)
from radar.leader_business_document_facts import (
    OfficialBusinessEvidenceFragment,
    OfficialBusinessFactResult,
    extract_official_business_facts,
)
from radar.leader_business_material_review_submission import (
    LeaderBusinessMaterialReviewSourcePacketStatus,
    load_leader_business_material_review_source_packet,
)
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystDocument,
    OfficialBusinessCatalystKind,
    OfficialBusinessCatalystQuery,
    fetch_official_business_catalysts,
)
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    fetch_official_business_document_content,
)


LEADER_BUSINESS_AUTOMATIC_EVIDENCE_CONTRACT_ID = (
    "radar-leader-business-automatic-evidence-v1"
)
LEADER_BUSINESS_AUTOMATIC_CHECKPOINT_CONTRACT_ID = (
    "radar-leader-business-automatic-checkpoint-v1"
)
LEADER_BUSINESS_AUTOMATIC_DELIVERY_CONTRACT_ID = (
    "radar-leader-business-automatic-delivery-v1"
)
LEADER_BUSINESS_AUTOMATIC_GAP_DIAGNOSTIC_CONTRACT_ID = (
    "radar-leader-business-automatic-gap-diagnostic-v1"
)
MAXIMUM_PDF_WORKERS = 2
CATALYST_LOOKBACK_DAYS = 365
MAXIMUM_DIAGNOSTIC_SNIPPETS_PER_DOCUMENT = 8
MAXIMUM_DIAGNOSTIC_SNIPPET_CHARACTERS = 600
DIAGNOSTIC_TEXT_MARKERS = (
    "收入", "销量", "销售", "价格", "均价", "毛利", "利润", "盈利",
    "产量", "出货", "交付", "投产", "中标", "合同", "认证", "业务",
)


@dataclass(frozen=True)
class LeaderBusinessAutomaticEvidenceSources:
    discover_catalysts: Callable[..., Any] = fetch_official_business_catalysts
    fetch_document_content: Callable[..., Any] = (
        fetch_official_business_document_content
    )


@dataclass(frozen=True, repr=False)
class LeaderBusinessAutomaticEvidenceBatchItem:
    index: int
    symbol: str
    status: AutomaticBusinessEvidenceStatus
    reasons: Tuple[str, ...] = ()
    checkpoint_path: Optional[Path] = None
    reused: bool = False
    artifact: Optional[
        DeterministicOfficialBusinessVerificationArtifact
    ] = field(default=None, repr=False)
    gap_diagnostic: Optional[Mapping[str, object]] = field(
        default=None,
        repr=False,
    )


@dataclass(frozen=True, repr=False)
class LeaderBusinessAutomaticEvidenceBatchResult:
    status: AutomaticBusinessEvidenceStatus
    candidate_plan_id: Optional[str]
    candidate_count: int
    items: Tuple[LeaderBusinessAutomaticEvidenceBatchItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    packet_path: Optional[Path] = None
    delivery_packet_path: Optional[Path] = None
    gap_diagnostic_path: Optional[Path] = None
    contract_id: str = LEADER_BUSINESS_AUTOMATIC_EVIDENCE_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    @property
    def ready_count(self) -> int:
        return sum(
            item.status is AutomaticBusinessEvidenceStatus.READY
            for item in self.items
        )

    @property
    def missing_count(self) -> int:
        return sum(
            item.status is AutomaticBusinessEvidenceStatus.MISSING
            for item in self.items
        )

    @property
    def source_failed_count(self) -> int:
        return sum(
            item.status is AutomaticBusinessEvidenceStatus.SOURCE_FAILED
            for item in self.items
        )

    @property
    def source_unverified_count(self) -> int:
        return sum(
            item.status is AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED
            for item in self.items
        )

    @property
    def reused_count(self) -> int:
        return sum(item.reused for item in self.items)

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status.value,
            "candidatePlanId": self.candidate_plan_id,
            "candidateCount": self.candidate_count,
            "readyCount": self.ready_count,
            "missingCount": self.missing_count,
            "sourceFailedCount": self.source_failed_count,
            "sourceUnverifiedCount": self.source_unverified_count,
            "reusedCount": self.reused_count,
            "reasons": list(self.reasons),
            "packetPath": str(self.packet_path) if self.packet_path else None,
            "deliveryPacketPath": (
                str(self.delivery_packet_path)
                if self.delivery_packet_path else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _diagnostic_snippets(
    content: OfficialBusinessDocumentContentResult,
) -> Tuple[Mapping[str, object], ...]:
    snippets = []
    seen = set()
    for page in content.pages:
        normalized = " ".join(page.text.split())
        for raw_text in re.split(r"[。；;!?！？\n]+", normalized):
            text = raw_text.strip(" ：:，,")
            if (
                len(text) < 8
                or text == "业绩变动原因说明"
                or not any(marker in text for marker in DIAGNOSTIC_TEXT_MARKERS)
            ):
                continue
            text = text[:MAXIMUM_DIAGNOSTIC_SNIPPET_CHARACTERS]
            identity = (page.page_number, text)
            if identity in seen:
                continue
            seen.add(identity)
            snippets.append({
                "pageNumber": page.page_number,
                "text": text,
            })
            if len(snippets) >= MAXIMUM_DIAGNOSTIC_SNIPPETS_PER_DOCUMENT:
                return tuple(snippets)
    return tuple(snippets)


def _fragment_payload(value: OfficialBusinessEvidenceFragment):
    return {
        "pageNumber": value.page_number,
        "fragmentSha256": value.fragment_sha256,
        "text": value.text,
    }


def _fragment_from_payload(value: Any):
    if not isinstance(value, Mapping) or set(value) != {
        "pageNumber", "fragmentSha256", "text",
    }:
        raise ValueError
    return OfficialBusinessEvidenceFragment(
        page_number=value["pageNumber"],
        fragment_sha256=value["fragmentSha256"],
        text=value["text"],
    )


def _artifact_payload(
    value: DeterministicOfficialBusinessVerificationArtifact,
) -> Mapping[str, object]:
    annual = value.annual_facts
    catalyst = value.catalyst_facts
    return {
        "verificationId": value.verification_id,
        "ruleVersion": value.rule_version,
        "symbol": value.symbol,
        "industryCode": value.industry_code,
        "industryName": value.industry_name,
        "industryReleaseId": value.industry_release_id,
        "relation": value.relation.value,
        "matchedTerms": list(value.matched_terms),
        "annualDocumentId": value.annual_document_id,
        "annualDocumentVersion": value.annual_document_version,
        "annualContentSha256": value.annual_content_sha256,
        "catalystDocumentId": value.catalyst_document_id,
        "catalystDocumentVersion": value.catalyst_document_version,
        "catalystContentSha256": value.catalyst_content_sha256,
        "issuerIdentity": value.issuer_identity,
        "annualFragmentSha256s": list(value.annual_fragment_sha256s),
        "catalystFragmentSha256s": list(value.catalyst_fragment_sha256s),
        "validatedAt": value.validated_at.isoformat(),
        "annualFacts": {
            "status": annual.status.value,
            "symbol": annual.symbol,
            "industryCode": annual.industry_code,
            "industryReleaseId": annual.industry_release_id,
            "documentId": annual.document_id,
            "documentVersion": annual.document_version,
            "contentSha256": annual.content_sha256,
            "businessTerms": list(annual.business_terms),
            "fragments": [
                _fragment_payload(item) for item in annual.fragments
            ],
            "sourceTime": annual.source_time.isoformat(),
            "validatedAt": annual.validated_at.isoformat(),
        },
        "catalystFacts": {
            "status": catalyst.status.value,
            "documentId": catalyst.document_id,
            "documentVersion": catalyst.document_version,
            "symbol": catalyst.symbol,
            "issuerIdentity": catalyst.issuer_identity,
            "eventKind": catalyst.event_kind.value,
            "contentSha256": catalyst.content_sha256,
            "businessTerms": list(catalyst.business_terms),
            "fragments": [
                _fragment_payload(item) for item in catalyst.fragments
            ],
            "negativeEvent": catalyst.negative_event,
            "sourceTime": catalyst.source_time.isoformat(),
            "validatedAt": catalyst.validated_at.isoformat(),
        },
    }


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if not _aware(parsed):
        raise ValueError
    return parsed


def _artifact_from_payload(value: Any):
    if not isinstance(value, Mapping):
        raise ValueError
    annual = value["annualFacts"]
    catalyst = value["catalystFacts"]
    annual_facts = OfficialBusinessFactResult(
        status=AutomaticBusinessEvidenceStatus(annual["status"]),
        symbol=annual["symbol"],
        industry_code=annual["industryCode"],
        industry_release_id=annual["industryReleaseId"],
        document_id=annual["documentId"],
        document_version=annual["documentVersion"],
        content_sha256=annual["contentSha256"],
        business_terms=tuple(annual["businessTerms"]),
        fragments=tuple(
            _fragment_from_payload(item) for item in annual["fragments"]
        ),
        source_time=_parse_datetime(annual["sourceTime"]),
        validated_at=_parse_datetime(annual["validatedAt"]),
    )
    catalyst_facts = OfficialBusinessCatalystFactResult(
        status=AutomaticBusinessEvidenceStatus(catalyst["status"]),
        document_id=catalyst["documentId"],
        document_version=catalyst["documentVersion"],
        symbol=catalyst["symbol"],
        issuer_identity=catalyst["issuerIdentity"],
        event_kind=OfficialBusinessCatalystKind(catalyst["eventKind"]),
        content_sha256=catalyst["contentSha256"],
        business_terms=tuple(catalyst["businessTerms"]),
        fragments=tuple(
            _fragment_from_payload(item) for item in catalyst["fragments"]
        ),
        negative_event=catalyst["negativeEvent"],
        source_time=_parse_datetime(catalyst["sourceTime"]),
        validated_at=_parse_datetime(catalyst["validatedAt"]),
    )
    return DeterministicOfficialBusinessVerificationArtifact(
        verification_id=value["verificationId"],
        rule_version=value["ruleVersion"],
        symbol=value["symbol"],
        industry_code=value["industryCode"],
        industry_name=value["industryName"],
        industry_release_id=value["industryReleaseId"],
        relation=BusinessCatalystRelation(value["relation"]),
        matched_terms=tuple(value["matchedTerms"]),
        annual_document_id=value["annualDocumentId"],
        annual_document_version=value["annualDocumentVersion"],
        annual_content_sha256=value["annualContentSha256"],
        catalyst_document_id=value["catalystDocumentId"],
        catalyst_document_version=value["catalystDocumentVersion"],
        catalyst_content_sha256=value["catalystContentSha256"],
        issuer_identity=value["issuerIdentity"],
        annual_fragment_sha256s=tuple(value["annualFragmentSha256s"]),
        catalyst_fragment_sha256s=tuple(value["catalystFragmentSha256s"]),
        validated_at=_parse_datetime(value["validatedAt"]),
        annual_facts=annual_facts,
        catalyst_facts=catalyst_facts,
    )


def _checkpoint_scope(plan_id, plan_item, annual, catalysts):
    return {
        "candidatePlanId": plan_id,
        "index": plan_item.index,
        "symbol": plan_item.symbol,
        "industryCode": plan_item.industry_code,
        "industryName": plan_item.industry_name,
        "industryReleaseId": plan_item.industry_release_id,
        "annualDocumentId": annual.document_id,
        "annualDocumentVersion": annual.document_version,
        "catalystDocuments": [
            {
                "documentId": item.document_id,
                "documentVersion": item.document_version,
            }
            for item in catalysts
        ],
        "ruleVersion": DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
    }


def _load_checkpoint(path, scope):
    if not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(envelope, Mapping)
            or set(envelope) != {"contractId", "payloadSha256", "payload"}
            or envelope["contractId"]
            != LEADER_BUSINESS_AUTOMATIC_CHECKPOINT_CONTRACT_ID
            or not isinstance(envelope["payload"], Mapping)
            or envelope["payloadSha256"] != _digest(envelope["payload"])
            or envelope["payload"].get("scope") != scope
        ):
            return None
        artifact = _artifact_from_payload(envelope["payload"]["artifact"])
        replayed = replay_deterministic_official_business_verification(
            artifact,
            as_of=artifact.validated_at,
        )
        if (
            replayed.status is not AutomaticBusinessEvidenceStatus.READY
            or replayed.artifact != artifact
        ):
            return None
        return artifact
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None


def _item(index, symbol, status, reasons, **changes):
    return LeaderBusinessAutomaticEvidenceBatchItem(
        index=index,
        symbol=symbol,
        status=status,
        reasons=_dedupe(reasons),
        **changes,
    )


def _process_candidate(
    plan_id,
    plan_item,
    queue_item,
    *,
    artifact_dir,
    sources,
    validated_at,
    write_gap_diagnostic,
):
    selection = select_latest_official_annual_report(plan_item, queue_item)
    if selection.status is not AutomaticBusinessEvidenceStatus.READY:
        return _item(
            plan_item.index,
            plan_item.symbol,
            selection.status,
            selection.reasons,
        )
    annual = selection.document
    query = OfficialBusinessCatalystQuery(
        candidate_plan_id=plan_id,
        symbol=plan_item.symbol,
        issuer_identity=annual.issuer_identity,
        window_from=plan_item.as_of.date() - timedelta(
            days=CATALYST_LOOKBACK_DAYS
        ),
        window_until=plan_item.as_of.date(),
    )
    try:
        discovery = sources.discover_catalysts(
            query,
            fetched_at=validated_at,
        )
    except Exception:
        return _item(
            plan_item.index,
            plan_item.symbol,
            AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
            ("business_automatic_catalyst_source_failed",),
        )
    if (
        not hasattr(discovery, "status")
        or discovery.status is not AutomaticBusinessEvidenceStatus.READY
        or not isinstance(getattr(discovery, "documents", None), tuple)
        or not discovery.documents
    ):
        status = getattr(
            discovery,
            "status",
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        )
        if not isinstance(status, AutomaticBusinessEvidenceStatus):
            status = AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED
        return _item(
            plan_item.index,
            plan_item.symbol,
            status,
            getattr(discovery, "reasons", ())
            or ("business_automatic_catalyst_unverified",),
        )
    catalysts = discovery.documents
    scope = _checkpoint_scope(plan_id, plan_item, annual, catalysts)
    checkpoint_path = (
        artifact_dir / "checkpoints" / f"{_digest(scope)}.json"
    )
    cached = _load_checkpoint(checkpoint_path, scope)
    if cached is not None:
        return _item(
            plan_item.index,
            plan_item.symbol,
            AutomaticBusinessEvidenceStatus.READY,
            (),
            checkpoint_path=checkpoint_path,
            reused=True,
            artifact=cached,
        )
    try:
        annual_content = sources.fetch_document_content(
            annual,
            kind=OfficialBusinessDocumentKind.ANNUAL_REPORT,
            fetched_at=validated_at,
        )
    except Exception:
        return _item(
            plan_item.index,
            plan_item.symbol,
            AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
            ("business_automatic_document_source_failed",),
        )
    annual_facts = extract_official_business_facts(
        plan_item,
        selection,
        annual_content,
    )
    if annual_facts.status is not AutomaticBusinessEvidenceStatus.READY:
        return _item(
            plan_item.index,
            plan_item.symbol,
            annual_facts.status,
            annual_facts.reasons,
        )
    catalyst_facts = []
    object_missing_reasons = []
    catalyst_diagnostics = []
    for catalyst in catalysts:
        try:
            content = sources.fetch_document_content(
                catalyst,
                kind=OfficialBusinessDocumentKind.CATALYST,
                fetched_at=validated_at,
            )
        except Exception:
            return _item(
                plan_item.index,
                plan_item.symbol,
                AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
                ("business_automatic_document_source_failed",),
            )
        if (
            type(content) is OfficialBusinessDocumentContentResult
            and isinstance(content.status, AutomaticBusinessEvidenceStatus)
            and content.status is not AutomaticBusinessEvidenceStatus.READY
        ):
            return _item(
                plan_item.index,
                plan_item.symbol,
                content.status,
                content.reasons
                or ("business_automatic_document_unverified",),
            )
        facts = extract_official_business_catalyst_facts(catalyst, content)
        if facts.status is not AutomaticBusinessEvidenceStatus.READY:
            if facts.reasons == ("business_catalyst_fact_object_missing",):
                object_missing_reasons.extend(facts.reasons)
                if write_gap_diagnostic:
                    catalyst_diagnostics.append({
                        "documentId": catalyst.document_id,
                        "documentVersion": catalyst.document_version,
                        "title": catalyst.title,
                        "eventKind": catalyst.event_kind.value,
                        "publishedAt": catalyst.published_at.isoformat(),
                        "sourceUrl": catalyst.source_url,
                        "contentSha256": content.content_sha256,
                        "snippets": list(_diagnostic_snippets(content)),
                    })
                continue
            return _item(
                plan_item.index,
                plan_item.symbol,
                facts.status,
                facts.reasons,
            )
        catalyst_facts.append(facts)
    if not catalyst_facts:
        gap_diagnostic = None
        if write_gap_diagnostic and catalyst_diagnostics:
            gap_diagnostic = {
                "index": plan_item.index,
                "symbol": plan_item.symbol,
                "issuerIdentity": annual.issuer_identity,
                "annualDocumentId": annual.document_id,
                "annualDocumentVersion": annual.document_version,
                "annualContentSha256": annual_content.content_sha256,
                "annualTerms": list(annual_facts.business_terms),
                "catalysts": catalyst_diagnostics,
            }
        return _item(
            plan_item.index,
            plan_item.symbol,
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            object_missing_reasons
            or ("business_catalyst_fact_object_missing",),
            gap_diagnostic=gap_diagnostic,
        )
    verified = build_deterministic_official_business_verification(
        plan_item,
        annual_facts,
        tuple(catalyst_facts),
        validated_at=validated_at,
    )
    if (
        verified.status is not AutomaticBusinessEvidenceStatus.READY
        or verified.artifact is None
    ):
        return _item(
            plan_item.index,
            plan_item.symbol,
            verified.status,
            verified.reasons,
        )
    checkpoint_payload = {
        "scope": scope,
        "artifact": _artifact_payload(verified.artifact),
    }
    _write_atomic_json(checkpoint_path, {
        "contractId": LEADER_BUSINESS_AUTOMATIC_CHECKPOINT_CONTRACT_ID,
        "payloadSha256": _digest(checkpoint_payload),
        "payload": checkpoint_payload,
    })
    return _item(
        plan_item.index,
        plan_item.symbol,
        AutomaticBusinessEvidenceStatus.READY,
        (),
        checkpoint_path=checkpoint_path,
        artifact=verified.artifact,
    )


def _overall_status(items):
    if all(
        item.status is AutomaticBusinessEvidenceStatus.READY
        for item in items
    ):
        return AutomaticBusinessEvidenceStatus.READY
    for status in (
        AutomaticBusinessEvidenceStatus.SOURCE_FAILED,
        AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
        AutomaticBusinessEvidenceStatus.MISSING,
    ):
        if any(item.status is status for item in items):
            return status
    return AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED


def run_leader_business_automatic_evidence(
    source_packet: Any,
    *,
    artifact_dir: Path,
    sources: Optional[LeaderBusinessAutomaticEvidenceSources] = None,
    clock: Callable[[], datetime],
    write_gap_diagnostic: bool = False,
) -> LeaderBusinessAutomaticEvidenceBatchResult:
    """为候选全集产生长期官方证据；结果不回填原候选时点。"""

    loaded = load_leader_business_material_review_source_packet(source_packet)
    if loaded.status is not LeaderBusinessMaterialReviewSourcePacketStatus.READY:
        raise ValueError("business_automatic_source_packet_unverified")
    if not isinstance(artifact_dir, Path):
        raise ValueError("business_automatic_artifact_dir_unverified")
    actual_sources = sources or LeaderBusinessAutomaticEvidenceSources()
    if type(actual_sources) is not LeaderBusinessAutomaticEvidenceSources:
        raise ValueError("business_automatic_sources_unverified")
    if type(write_gap_diagnostic) is not bool:
        raise ValueError("business_automatic_gap_diagnostic_unverified")
    validated_at = clock()
    if not _aware(validated_at):
        raise ValueError("business_automatic_clock_unverified")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if not artifact_dir.is_dir():
        raise ValueError("business_automatic_artifact_dir_unverified")
    (artifact_dir / "checkpoints").mkdir(exist_ok=True)
    plan = loaded.candidate_plan
    queue = loaded.review_queue
    assert plan is not None and queue is not None
    indexed = tuple(zip(plan.items, queue.items))

    def process(pair):
        plan_item, queue_item = pair
        try:
            return _process_candidate(
                plan.candidate_set_id,
                plan_item,
                queue_item,
                artifact_dir=artifact_dir,
                sources=actual_sources,
                validated_at=validated_at,
                write_gap_diagnostic=write_gap_diagnostic,
            )
        except OSError:
            raise
        except Exception:
            return _item(
                plan_item.index,
                plan_item.symbol,
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                ("business_automatic_candidate_unverified",),
            )

    with ThreadPoolExecutor(max_workers=MAXIMUM_PDF_WORKERS) as executor:
        items = tuple(executor.map(process, indexed))
    status = _overall_status(items)
    basename = hashlib.sha256(
        plan.candidate_set_id.encode("utf-8")
    ).hexdigest()
    packet_path = artifact_dir / f"evidence-{basename}.json"
    report = {
        "contractId": LEADER_BUSINESS_AUTOMATIC_EVIDENCE_CONTRACT_ID,
        "candidatePlanId": plan.candidate_set_id,
        "validatedAt": validated_at.isoformat(),
        "status": status.value,
        "candidateCount": len(items),
        "items": [{
            "index": item.index,
            "symbol": item.symbol,
            "status": item.status.value,
            "reasons": list(item.reasons),
            "checkpointPath": (
                str(item.checkpoint_path) if item.checkpoint_path else None
            ),
            "reused": item.reused,
        } for item in items],
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }
    _write_atomic_json(packet_path, report)
    gap_diagnostic_path = None
    gap_items = [
        item.gap_diagnostic
        for item in items
        if item.gap_diagnostic is not None
    ]
    if write_gap_diagnostic:
        gap_diagnostic_path = (
            artifact_dir / f"gap-diagnostic-{basename}.json"
        )
        _write_atomic_json(gap_diagnostic_path, {
            "contractId": LEADER_BUSINESS_AUTOMATIC_GAP_DIAGNOSTIC_CONTRACT_ID,
            "candidatePlanId": plan.candidate_set_id,
            "validatedAt": validated_at.isoformat(),
            "ruleVersion": DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
            "diagnosticOnly": True,
            "targetReason": "business_catalyst_fact_object_missing",
            "candidateCount": len(gap_items),
            "items": gap_items,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        })
    delivery_path = None
    if status is AutomaticBusinessEvidenceStatus.READY:
        delivery_path = artifact_dir / f"delivery-{basename}.json"
        delivery_payload = {
            "candidatePlanId": plan.candidate_set_id,
            "sourceCandidateAsOf": plan.as_of.isoformat(),
            "validatedAt": validated_at.isoformat(),
            "ruleVersion": DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION,
            "items": [
                {
                    "index": item.index,
                    "symbol": item.symbol,
                    "artifact": _artifact_payload(item.artifact),
                }
                for item in items
            ],
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }
        _write_atomic_json(delivery_path, {
            "contractId": LEADER_BUSINESS_AUTOMATIC_DELIVERY_CONTRACT_ID,
            "payloadSha256": _digest(delivery_payload),
            "payload": delivery_payload,
        })
    reasons = _dedupe(
        reason for item in items for reason in item.reasons
    )
    return LeaderBusinessAutomaticEvidenceBatchResult(
        status=status,
        candidate_plan_id=plan.candidate_set_id,
        candidate_count=len(items),
        items=items,
        reasons=reasons,
        packet_path=packet_path,
        delivery_packet_path=delivery_path,
        gap_diagnostic_path=gap_diagnostic_path,
    )
