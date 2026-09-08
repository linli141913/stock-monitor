"""阶段6只读观察候选快照仓。

该仓只保存当轮真实行情与已确认行业映射形成的观察候选，不计算龙头
分数、不生成三级状态、不要求人工批准，也不接触 SQLite。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple


LEADER_OBSERVATION_CONTRACT_ID = "radar-leader-observation-v1"
LEADER_OBSERVATION_MANIFEST_CONTRACT_ID = (
    "radar-leader-observation-manifest-v1"
)
LEADER_OBSERVATION_COVERAGE_SCOPE = (
    "当轮健康沪深A股行情与已确认行业映射；每行业涨幅前5只"
)
DEFAULT_LEADER_OBSERVATION_STORE_DIR = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "radar-leader-observations"
)
_SYMBOL_PATTERN = re.compile(r"^\d{6}$")


def _aware(value: Any, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name}必须包含时区")
    return value


def _text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name}不能为空")
    return normalized


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


@dataclass(frozen=True)
class LeaderObservationItem:
    symbol: str
    name: str
    industry_code: str
    industry_name: str
    within_industry_rank: int
    price: float
    change_percent: float
    source_time: datetime
    quote_source_contract_id: str
    sector_source_contract_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not _SYMBOL_PATTERN.fullmatch(
            self.symbol
        ):
            raise ValueError("symbol必须是6位数字")
        for field_name in (
            "name",
            "industry_code",
            "industry_name",
            "quote_source_contract_id",
            "sector_source_contract_id",
        ):
            _text(getattr(self, field_name), field_name)
        if (
            not isinstance(self.within_industry_rank, int)
            or not 1 <= self.within_industry_rank <= 5
        ):
            raise ValueError("within_industry_rank必须在1到5之间")
        for value, field_name in (
            (self.price, "price"),
            (self.change_percent, "change_percent"),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{field_name}必须是有限数字")
        if self.price <= 0:
            raise ValueError("price必须大于0")
        _aware(self.source_time, "source_time")

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "industryCode": self.industry_code,
            "industryName": self.industry_name,
            "withinIndustryRank": self.within_industry_rank,
            "price": float(self.price),
            "changePercent": float(self.change_percent),
            "sourceTime": self.source_time.isoformat(),
            "quoteSourceContractId": self.quote_source_contract_id,
            "sectorSourceContractId": self.sector_source_contract_id,
        }


@dataclass(frozen=True)
class LeaderObservationSnapshot:
    radar_run_id: str
    candidate_plan_id: str
    as_of: datetime
    published_at: datetime
    scanned_count: int
    mapped_count: int
    items: Tuple[LeaderObservationItem, ...] = field(default_factory=tuple)
    coverage_scope: str = LEADER_OBSERVATION_COVERAGE_SCOPE
    human_approval_required: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def __post_init__(self) -> None:
        _text(self.radar_run_id, "radar_run_id")
        _text(self.candidate_plan_id, "candidate_plan_id")
        _text(self.coverage_scope, "coverage_scope")
        as_of = _aware(self.as_of, "as_of")
        published_at = _aware(self.published_at, "published_at")
        if published_at < as_of:
            raise ValueError("published_at不能早于as_of")
        if (
            not isinstance(self.scanned_count, int)
            or not isinstance(self.mapped_count, int)
            or self.scanned_count < 0
            or self.mapped_count < 0
            or self.mapped_count > self.scanned_count
        ):
            raise ValueError("观察覆盖数量无效")
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, LeaderObservationItem)
            for item in self.items
        ):
            raise ValueError("items必须是观察候选元组")
        symbols = tuple(item.symbol for item in self.items)
        if len(symbols) != len(set(symbols)):
            raise ValueError("观察候选包含重复symbol")
        if any(item.source_time > published_at for item in self.items):
            raise ValueError("source_time不能晚于published_at")
        if any((
            self.human_approval_required is not False,
            self.formal_usable is not False,
            self.state_transition_allowed is not False,
        )):
            raise ValueError("观察快照不得要求人工批准或开启正式状态")

    @property
    def candidate_count(self) -> int:
        return len(self.items)

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "contractId": LEADER_OBSERVATION_CONTRACT_ID,
            "status": "available" if self.items else "empty",
            "quality": "partial",
            "radarRunId": self.radar_run_id,
            "candidatePlanId": self.candidate_plan_id,
            "asOf": self.as_of.isoformat(),
            "publishedAt": self.published_at.isoformat(),
            "scannedCount": self.scanned_count,
            "mappedCount": self.mapped_count,
            "candidateCount": self.candidate_count,
            "coverageScope": self.coverage_scope,
            "humanApprovalRequired": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
            "items": [item.to_payload() for item in self.items],
        }


@dataclass(frozen=True)
class LeaderObservationStoreReadResult:
    status: str
    reasons: Tuple[str, ...]
    snapshot: Optional[LeaderObservationSnapshot] = field(
        default=None,
        repr=False,
    )
    evidence_sha256: Optional[str] = None


def _snapshot_from_payload(payload: Any) -> LeaderObservationSnapshot:
    if not isinstance(payload, Mapping):
        raise ValueError("leader_observation_evidence_unverified")
    if any((
        payload.get("contractId") != LEADER_OBSERVATION_CONTRACT_ID,
        payload.get("status") not in {"available", "empty"},
        payload.get("quality") != "partial",
        payload.get("humanApprovalRequired") is not False,
        payload.get("formalUsable") is not False,
        payload.get("stateTransitionAllowed") is not False,
        payload.get("coverageScope") != LEADER_OBSERVATION_COVERAGE_SCOPE,
        not isinstance(payload.get("items"), list),
    )):
        raise ValueError("leader_observation_evidence_unverified")
    items = tuple(LeaderObservationItem(
        symbol=item["symbol"],
        name=item["name"],
        industry_code=item["industryCode"],
        industry_name=item["industryName"],
        within_industry_rank=item["withinIndustryRank"],
        price=item["price"],
        change_percent=item["changePercent"],
        source_time=datetime.fromisoformat(item["sourceTime"]),
        quote_source_contract_id=item["quoteSourceContractId"],
        sector_source_contract_id=item["sectorSourceContractId"],
    ) for item in payload["items"])
    snapshot = LeaderObservationSnapshot(
        radar_run_id=payload["radarRunId"],
        candidate_plan_id=payload["candidatePlanId"],
        as_of=datetime.fromisoformat(payload["asOf"]),
        published_at=datetime.fromisoformat(payload["publishedAt"]),
        scanned_count=payload["scannedCount"],
        mapped_count=payload["mappedCount"],
        items=items,
    )
    if payload.get("candidateCount") != snapshot.candidate_count:
        raise ValueError("leader_observation_evidence_unverified")
    if payload.get("status") != (
        "available" if snapshot.items else "empty"
    ):
        raise ValueError("leader_observation_evidence_unverified")
    return snapshot


def publish_leader_observation_snapshot(
    snapshot: LeaderObservationSnapshot,
    *,
    store_dir: Path = DEFAULT_LEADER_OBSERVATION_STORE_DIR,
) -> LeaderObservationStoreReadResult:
    if not isinstance(snapshot, LeaderObservationSnapshot):
        raise ValueError("leader_observation_snapshot_unverified")
    root = Path(store_dir).expanduser().resolve()
    payload = snapshot.to_payload()
    canonical = _canonical_bytes(payload)
    evidence_sha256 = _sha256(canonical)
    relative = Path("snapshots") / f"{evidence_sha256}.json"
    _write_atomic(root / relative, payload)
    _write_atomic(root / "latest.json", {
        "contractId": LEADER_OBSERVATION_MANIFEST_CONTRACT_ID,
        "evidenceRelativePath": relative.as_posix(),
        "evidenceSha256": evidence_sha256,
        "radarRunId": snapshot.radar_run_id,
        "candidatePlanId": snapshot.candidate_plan_id,
        "asOf": snapshot.as_of.isoformat(),
        "publishedAt": snapshot.published_at.isoformat(),
    })
    return LeaderObservationStoreReadResult(
        status="available",
        reasons=(),
        snapshot=snapshot,
        evidence_sha256=evidence_sha256,
    )


def _failed(reason: str) -> LeaderObservationStoreReadResult:
    return LeaderObservationStoreReadResult(
        status="failed",
        reasons=(reason,),
    )


def load_latest_leader_observation(
    *,
    store_dir: Path = DEFAULT_LEADER_OBSERVATION_STORE_DIR,
) -> LeaderObservationStoreReadResult:
    root = Path(store_dir).expanduser().resolve()
    manifest_path = root / "latest.json"
    if not manifest_path.is_file():
        return LeaderObservationStoreReadResult(
            status="not_ready",
            reasons=("leader_observation_snapshot_missing",),
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative_text = manifest["evidenceRelativePath"]
        expected_hash = manifest["evidenceSha256"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return _failed("leader_observation_manifest_unverified")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("contractId")
        != LEADER_OBSERVATION_MANIFEST_CONTRACT_ID
        or not isinstance(relative_text, str)
        or not relative_text
        or Path(relative_text).is_absolute()
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
    ):
        return _failed("leader_observation_manifest_unverified")
    evidence_path = (root / relative_text).resolve()
    try:
        evidence_path.relative_to(root)
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _failed("leader_observation_evidence_missing")
    except (OSError, ValueError, json.JSONDecodeError):
        return _failed("leader_observation_evidence_unverified")
    try:
        canonical = _canonical_bytes(payload)
    except (TypeError, ValueError):
        return _failed("leader_observation_evidence_unverified")
    if _sha256(canonical) != expected_hash:
        return _failed("leader_observation_evidence_hash_mismatch")
    try:
        snapshot = _snapshot_from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return _failed("leader_observation_evidence_unverified")
    if any((
        manifest.get("radarRunId") != snapshot.radar_run_id,
        manifest.get("candidatePlanId") != snapshot.candidate_plan_id,
        manifest.get("asOf") != snapshot.as_of.isoformat(),
        manifest.get("publishedAt") != snapshot.published_at.isoformat(),
    )):
        return _failed("leader_observation_identity_mismatch")
    return LeaderObservationStoreReadResult(
        status="available",
        reasons=(),
        snapshot=snapshot,
        evidence_sha256=expected_hash,
    )


def build_leader_observation_snapshot(
    candidate_plan: Any,
    *,
    quote_items: Sequence[Any],
    security_records: Sequence[Any],
    published_at: datetime,
) -> LeaderObservationSnapshot:
    """从已验证候选计划构建不含评分的真实观察快照。"""

    from radar.leader_runtime_candidate_plan import (
        is_leader_runtime_candidate_plan_valid,
    )

    if not is_leader_runtime_candidate_plan_valid(candidate_plan):
        raise ValueError("leader_observation_candidate_plan_unverified")
    quotes = {getattr(item, "symbol", None): item for item in quote_items}
    securities = {
        getattr(item, "symbol", None): item for item in security_records
    }
    items = []
    for plan_item in candidate_plan.items:
        quote = quotes.get(plan_item.symbol)
        security = securities.get(plan_item.symbol)
        if quote is None or security is None:
            raise ValueError("leader_observation_source_scope_incomplete")
        source_time = getattr(quote, "source_time", None)
        price = getattr(quote, "price", None)
        change_percent = getattr(quote, "change_percent", None)
        name = getattr(security, "name", None) or getattr(quote, "name", None)
        items.append(LeaderObservationItem(
            symbol=plan_item.symbol,
            name=name,
            industry_code=plan_item.industry_code,
            industry_name=plan_item.industry_name,
            within_industry_rank=plan_item.within_industry_rank,
            price=price,
            change_percent=change_percent,
            source_time=source_time,
            quote_source_contract_id=plan_item.quote_source_contract_id,
            sector_source_contract_id=plan_item.sector_source_contract_id,
        ))
    return LeaderObservationSnapshot(
        radar_run_id=candidate_plan.radar_run_id,
        candidate_plan_id=candidate_plan.candidate_set_id,
        as_of=candidate_plan.as_of,
        published_at=published_at,
        scanned_count=candidate_plan.scanned_count,
        mapped_count=candidate_plan.mapped_count,
        items=tuple(items),
    )


def publish_runtime_leader_observation(
    candidate_plan: Any,
    quote_items: Sequence[Any],
    security_records: Sequence[Any],
    published_at: datetime,
    *,
    store_dir: Path = DEFAULT_LEADER_OBSERVATION_STORE_DIR,
) -> LeaderObservationStoreReadResult:
    """运行时便捷入口：构建并原子发布同轮观察候选。"""

    snapshot = build_leader_observation_snapshot(
        candidate_plan,
        quote_items=quote_items,
        security_records=security_records,
        published_at=published_at,
    )
    return publish_leader_observation_snapshot(
        snapshot,
        store_dir=store_dir,
    )
