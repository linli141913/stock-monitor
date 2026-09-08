"""阶段9客观结果的每日收盘事实冻结。

每日只抓一次规则目标所需的证券收盘事实和四个市场指数。该工件只保存
真实行情及样本时点已有的行业成员关系，不生成标签、不判断规则正确与否。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Dict, List, Literal, Optional, Sequence
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from market_calendar import get_calendar_day_kind
from radar.contracts import IndexQuoteSnapshot, QuoteSnapshot, SourceBatch
from radar.replay_assembly import _private_tmp_child
from radar.replay_contracts import (
    RadarReplayModel,
    RadarReplayOutputBundle,
)
from radar.replay_forward_baseline import _atomic_write_json, _validate_output_dir
from radar.replay_label_tasks import RadarReplayLabelTaskBundle
from radar.replay_objective_outcomes import load_replay_objective_inputs
from radar.sources.market_indices import (
    MARKET_INDEX_IDENTITIES,
    fetch_market_indices,
)
from radar.sources.tencent_quotes import fetch_tencent_quotes_concurrent


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SAFE_CLOSE_TIME = time(15, 5)
MARKET_CLOSE_TIME = time(15, 0)
MAXIMUM_SECURITY_COUNT = 10_000
DAILY_MARKET_INDEX_IDENTITIES = {
    item.index_key.value: (item.symbol, item.source_symbol)
    for item in MARKET_INDEX_IDENTITIES
}


def _outcome_points_digest(items: Sequence[object]) -> str:
    return hashlib.sha256(json.dumps(
        [item.model_dump(mode="json", by_alias=True) for item in items],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _membership_digest(value: Dict[str, Dict[str, List[str]]]) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _daily_snapshot_id(
    *,
    task_bundle_id: str,
    output_bundle_id: str,
    trade_date: date,
    security_digest: str,
    index_digest: str,
    membership_digest: str,
) -> str:
    identity = hashlib.sha256(
        f"{task_bundle_id}|{output_bundle_id}|{trade_date.isoformat()}|"
        f"{security_digest}|{index_digest}|{membership_digest}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"stage9-outcome-daily-{identity[:24]}"


class DailySecurityOutcomePoint(RadarReplayModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str
    source_time: Optional[datetime] = Field(default=None, alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    price: float = Field(gt=0, allow_inf_nan=False)
    previous_close: Optional[float] = Field(
        default=None,
        alias="previousClose",
        gt=0,
        allow_inf_nan=False,
    )
    high_price: Optional[float] = Field(
        default=None,
        alias="highPrice",
        ge=0,
        allow_inf_nan=False,
    )
    low_price: Optional[float] = Field(
        default=None,
        alias="lowPrice",
        ge=0,
        allow_inf_nan=False,
    )
    turnover_amount_cny: Optional[float] = Field(
        default=None,
        alias="turnoverAmountCny",
        ge=0,
        allow_inf_nan=False,
    )
    trading_status: Optional[
        Literal["suspended", "delisted", "unlisted"]
    ] = Field(default=None, alias="tradingStatus")

    @model_validator(mode="after")
    def validate_times(self):
        for field_name, value in (
            ("sourceTime", self.source_time),
            ("fetchedAt", self.fetched_at),
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name}_timezone_required")
        if self.source_time is not None and self.source_time > self.fetched_at:
            raise ValueError("sourceTime_after_fetchedAt")
        return self


class DailyMarketIndexOutcomePoint(RadarReplayModel):
    index_key: str = Field(alias="indexKey", min_length=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    source_symbol: str = Field(alias="sourceSymbol", pattern=r"^(sh|sz)\d{6}$")
    source_time: datetime = Field(alias="sourceTime")
    fetched_at: datetime = Field(alias="fetchedAt")
    price: float = Field(gt=0, allow_inf_nan=False)
    change_percent: float = Field(alias="changePercent", allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_times(self):
        for field_name, value in (
            ("sourceTime", self.source_time),
            ("fetchedAt", self.fetched_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name}_timezone_required")
        if self.source_time > self.fetched_at:
            raise ValueError("sourceTime_after_fetchedAt")
        if DAILY_MARKET_INDEX_IDENTITIES.get(self.index_key) != (
            self.symbol,
            self.source_symbol,
        ):
            raise ValueError("daily_outcome_market_index_identity_mismatch")
        return self


class DailyOutcomeSnapshot(RadarReplayModel):
    contract_id: Literal["radar-replay-outcome-daily-snapshot-v1"] = Field(
        default="radar-replay-outcome-daily-snapshot-v1",
        alias="contractId",
    )
    snapshot_id: str = Field(alias="snapshotId", min_length=1, max_length=240)
    task_bundle_id: str = Field(alias="taskBundleId", min_length=1, max_length=240)
    output_bundle_id: str = Field(alias="outputBundleId", min_length=1, max_length=240)
    trade_date: date = Field(alias="tradeDate")
    captured_at: datetime = Field(alias="capturedAt")
    status: Literal["ready", "partial", "failed"]
    expected_security_count: int = Field(alias="expectedSecurityCount", ge=0)
    ready_security_count: int = Field(alias="readySecurityCount", ge=0)
    sector_memberships_by_sample: Dict[str, Dict[str, List[str]]] = Field(
        alias="sectorMembershipsBySample"
    )
    security_quotes: List[DailySecurityOutcomePoint] = Field(alias="securityQuotes")
    market_indices: List[DailyMarketIndexOutcomePoint] = Field(alias="marketIndices")
    missing_symbols: List[str] = Field(alias="missingSymbols")
    source_ids: List[str] = Field(alias="sourceIds", min_length=1)
    reasons: List[str]

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("capturedAt_timezone_required")
        local_captured = self.captured_at.astimezone(SHANGHAI_TZ)
        if local_captured.date() != self.trade_date:
            raise ValueError("daily_outcome_snapshot_trade_date_mismatch")
        if (
            local_captured.time().replace(tzinfo=None)
            < SAFE_CLOSE_TIME
        ):
            raise ValueError("daily_outcome_snapshot_before_safe_close")
        if self.ready_security_count != len(self.security_quotes):
            raise ValueError("daily_outcome_ready_count_mismatch")
        if self.expected_security_count < self.ready_security_count:
            raise ValueError("daily_outcome_security_count_mismatch")
        if (
            self.expected_security_count - self.ready_security_count
            != len(self.missing_symbols)
        ):
            raise ValueError("daily_outcome_missing_count_mismatch")
        symbols = [item.symbol for item in self.security_quotes]
        if len(symbols) != len(set(symbols)):
            raise ValueError("daily_outcome_duplicate_security")
        index_keys = [item.index_key for item in self.market_indices]
        if len(index_keys) != len(set(index_keys)):
            raise ValueError("daily_outcome_duplicate_market_index")
        membership_symbols = []
        for sample_id, memberships in self.sector_memberships_by_sample.items():
            if not sample_id or not memberships:
                raise ValueError("daily_outcome_membership_unverified")
            for industry_code, members in memberships.items():
                if (
                    not industry_code
                    or not members
                    or members != sorted(set(members))
                ):
                    raise ValueError("daily_outcome_membership_unverified")
                membership_symbols.extend(members)
        if (
            self.status == "ready"
            and not set(membership_symbols).issubset(set(symbols))
        ):
            raise ValueError("daily_outcome_membership_quote_missing")
        if (
            len(self.source_ids) != 2
            or len(self.source_ids) != len(set(self.source_ids))
            or any(
                re.search(r":sha256:[0-9a-f]{64}$", value) is None
                for value in self.source_ids
            )
        ):
            raise ValueError("daily_outcome_source_identity_unverified")
        if self.status == "ready" and (
            self.missing_symbols
            or len(self.market_indices) != 4
            or self.reasons
        ):
            raise ValueError("ready_daily_outcome_snapshot_incomplete")
        if self.status != "ready" and not self.reasons:
            raise ValueError("unready_daily_outcome_reason_required")
        for item in self.security_quotes:
            local_fetched = item.fetched_at.astimezone(SHANGHAI_TZ)
            if (
                local_fetched.date() != self.trade_date
                or local_fetched.time().replace(tzinfo=None)
                < SAFE_CLOSE_TIME
            ):
                raise ValueError("daily_outcome_fetch_time_invalid")
            if item.trading_status is None:
                local_source = (
                    item.source_time.astimezone(SHANGHAI_TZ)
                    if item.source_time is not None else None
                )
                if (
                    local_source is None
                    or local_source.date() != self.trade_date
                    or local_source.time().replace(tzinfo=None)
                    < MARKET_CLOSE_TIME
                ):
                    raise ValueError(
                        "daily_outcome_security_close_time_invalid"
                    )
        for item in self.market_indices:
            local_source = item.source_time.astimezone(SHANGHAI_TZ)
            local_fetched = item.fetched_at.astimezone(SHANGHAI_TZ)
            if (
                local_fetched.date() != self.trade_date
                or local_fetched.time().replace(tzinfo=None)
                < SAFE_CLOSE_TIME
            ):
                raise ValueError("daily_outcome_fetch_time_invalid")
            if (
                local_source.date() != self.trade_date
                or local_source.time().replace(tzinfo=None)
                < MARKET_CLOSE_TIME
            ):
                raise ValueError("daily_outcome_index_close_time_invalid")
        if any(
            value > self.captured_at
            for item in (*self.security_quotes, *self.market_indices)
            for value in (item.source_time, item.fetched_at)
            if value is not None
        ):
            raise ValueError("daily_outcome_future_source_time")
        declared_content_hashes = sorted(
            item.rsplit(":sha256:", 1)[1] for item in self.source_ids
        )
        expected_content_hashes = sorted((
            _outcome_points_digest(self.security_quotes),
            _outcome_points_digest(self.market_indices),
        ))
        if declared_content_hashes != expected_content_hashes:
            raise ValueError("daily_outcome_source_content_hash_mismatch")
        expected_snapshot_id = _daily_snapshot_id(
            task_bundle_id=self.task_bundle_id,
            output_bundle_id=self.output_bundle_id,
            trade_date=self.trade_date,
            security_digest=_outcome_points_digest(self.security_quotes),
            index_digest=_outcome_points_digest(self.market_indices),
            membership_digest=_membership_digest(
                self.sector_memberships_by_sample
            ),
        )
        if self.snapshot_id != expected_snapshot_id:
            raise ValueError("daily_outcome_snapshot_identity_mismatch")
        return self


def _default_security_quotes(
    symbols: Sequence[str],
    *,
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
) -> SourceBatch[QuoteSnapshot]:
    return fetch_tencent_quotes_concurrent(
        symbols,
        radar_run_id=radar_run_id,
        batch_id=batch_id,
        as_of=as_of,
    )


def _default_market_indices(
    *,
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
) -> SourceBatch[IndexQuoteSnapshot]:
    return fetch_market_indices(
        radar_run_id=radar_run_id,
        batch_id=batch_id,
        as_of=as_of,
    )


@dataclass(frozen=True)
class DailyOutcomeCaptureSources:
    security_quotes: Callable[..., SourceBatch[QuoteSnapshot]] = (
        _default_security_quotes
    )
    market_indices: Callable[..., SourceBatch[IndexQuoteSnapshot]] = (
        _default_market_indices
    )


@dataclass(frozen=True)
class DailyOutcomeCaptureResult:
    output_dir: Path
    snapshot_path: Path
    manifest_path: Path
    snapshot: DailyOutcomeSnapshot


def _official_day_kind(day: date) -> str:
    return get_calendar_day_kind("cn", day).kind


def _identity_sets(task_bundle, output_bundle):
    tasks = {
        (item.sample_id, item.radar_run_id, item.as_of): item
        for item in task_bundle.samples
    }
    outputs = {
        (item.sample_id, item.radar_run_id, item.as_of): item
        for item in output_bundle.samples
    }
    if set(tasks) != set(outputs):
        raise ValueError("daily_outcome_sample_identity_mismatch")
    return tasks, outputs


def _targets(output_set, domain: str) -> List[str]:
    targets = []
    output_seen = False
    for evidence in output_set.evidence:
        if evidence.domain != domain or "states" not in evidence.payload:
            continue
        output_seen = True
        if evidence.status != "ready":
            raise ValueError("daily_outcome_rule_output_unready")
        states = evidence.payload.get("states")
        if not isinstance(states, list):
            raise ValueError("daily_outcome_rule_output_unverified")
        for item in states:
            if not isinstance(item, dict):
                raise ValueError("daily_outcome_rule_output_unverified")
            target = str(
                item.get("targetId") or item.get("symbol") or ""
            ).strip()
            if not target:
                raise ValueError("daily_outcome_target_missing")
            targets.append(target)
    if not output_seen:
        raise ValueError(f"daily_outcome_rule_output_missing:{domain}")
    if len(targets) != len(set(targets)):
        raise ValueError("daily_outcome_duplicate_target")
    return targets


def _load_source_snapshot(task) -> dict:
    path = _private_tmp_child(Path(task.source_snapshots.path))
    if (
        not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest()
        != task.source_snapshots.sha256
    ):
        raise ValueError("daily_outcome_source_snapshot_hash_mismatch")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("daily_outcome_source_snapshot_unverified") from exc
    if (
        payload.get("radarRunId") != task.radar_run_id
        or datetime.fromisoformat(str(payload.get("sampleAsOf"))) != task.as_of
    ):
        raise ValueError("daily_outcome_source_snapshot_identity_mismatch")
    return payload


def _capture_requirements(task_bundle, output_bundle):
    tasks, outputs = _identity_sets(task_bundle, output_bundle)
    symbols = set()
    memberships_by_sample: Dict[str, Dict[str, set]] = {}
    for identity, task in tasks.items():
        output_set = outputs[identity]
        sector_targets = set(_targets(output_set, "sector"))
        memberships = memberships_by_sample.setdefault(task.sample_id, {})
        symbols.update(
            target for target in _targets(output_set, "etf")
            if target.isdigit() and len(target) == 6
        )
        symbols.update(
            target for target in _targets(output_set, "leader")
            if target != "__empty__" and target.isdigit() and len(target) == 6
        )
        _targets(output_set, "market")
        source_snapshot = _load_source_snapshot(task)
        industry = (source_snapshot.get("sources") or {}).get("industry") or {}
        records = industry.get("records")
        if not isinstance(records, list):
            raise ValueError("daily_outcome_industry_records_unverified")
        for record in records:
            if not isinstance(record, dict):
                continue
            division = str(record.get("divisionCode") or "").strip()
            symbol = str(record.get("securityIdentity") or "").strip()
            if (
                division in sector_targets
                and record.get("recordStatus") == "accepted"
                and record.get("identityStatus") in {"exact", "verified_alias"}
                and symbol.isdigit()
                and len(symbol) == 6
            ):
                memberships.setdefault(division, set()).add(symbol)
                symbols.add(symbol)
        missing_memberships = sector_targets - set(memberships)
        if missing_memberships:
            raise ValueError("daily_outcome_sector_membership_missing")
    ordered = tuple(sorted(symbols))
    if not ordered or len(ordered) > MAXIMUM_SECURITY_COUNT:
        raise ValueError("daily_outcome_security_scope_unverified")
    return ordered, {
        sample_id: {
            code: sorted(items) for code, items in sorted(memberships.items())
        }
        for sample_id, memberships in sorted(memberships_by_sample.items())
    }


def capture_replay_outcome_day(
    *,
    task_bundle: RadarReplayLabelTaskBundle,
    output_bundle: RadarReplayOutputBundle,
    output_dir: Path,
    trade_date: date,
    captured_at: datetime,
    sources: DailyOutcomeCaptureSources = DailyOutcomeCaptureSources(),
    day_kind_provider: Callable[[date], str] = _official_day_kind,
) -> DailyOutcomeCaptureResult:
    if not isinstance(task_bundle, RadarReplayLabelTaskBundle):
        raise TypeError("task_bundle必须是RadarReplayLabelTaskBundle")
    if not isinstance(output_bundle, RadarReplayOutputBundle):
        raise TypeError("output_bundle必须是RadarReplayOutputBundle")
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("capturedAt_timezone_required")
    local_captured = captured_at.astimezone(SHANGHAI_TZ)
    if local_captured.date() != trade_date:
        raise ValueError("daily_outcome_capture_trade_date_mismatch")
    if local_captured.time().replace(tzinfo=None) < SAFE_CLOSE_TIME:
        raise ValueError("daily_outcome_capture_before_safe_close")
    if day_kind_provider(trade_date) not in {"full", "half"}:
        raise ValueError("daily_outcome_capture_not_trading_day")
    resolved_output = _validate_output_dir(Path(output_dir))
    symbols, memberships_by_sample = _capture_requirements(
        task_bundle,
        output_bundle,
    )
    run_id = f"stage9-outcome-daily-{trade_date.isoformat()}"
    quotes = sources.security_quotes(
        symbols,
        radar_run_id=run_id,
        batch_id=f"{run_id}-securities",
        as_of=captured_at,
    )
    indices = sources.market_indices(
        radar_run_id=run_id,
        batch_id=f"{run_id}-indices",
        as_of=captured_at,
    )
    if not isinstance(quotes, SourceBatch) or not isinstance(indices, SourceBatch):
        raise ValueError("daily_outcome_source_contract_unverified")
    completed_at = max(
        captured_at,
        quotes.meta.fetched_at,
        indices.meta.fetched_at,
    )
    if completed_at.astimezone(SHANGHAI_TZ).date() != trade_date:
        raise ValueError("daily_outcome_capture_completed_date_mismatch")

    security_points = []
    returned_symbols = set()
    for item in quotes.items:
        if not isinstance(item, QuoteSnapshot) or item.symbol not in symbols:
            continue
        source_time = item.source_time
        local_source_time = (
            source_time.astimezone(SHANGHAI_TZ)
            if source_time is not None else None
        )
        close_fact = local_source_time is not None and (
            local_source_time.date() == trade_date
            and local_source_time.time().replace(tzinfo=None)
            >= MARKET_CLOSE_TIME
        )
        explicit_non_trading = item.trading_status is not None
        if (
            item.price is None
            or item.price <= 0
            or (not close_fact and not explicit_non_trading)
            or item.fetched_at > completed_at
        ):
            continue
        returned_symbols.add(item.symbol)
        security_points.append(DailySecurityOutcomePoint(
            symbol=item.symbol,
            name=item.name,
            sourceTime=item.source_time,
            fetchedAt=item.fetched_at,
            price=item.price,
            previousClose=item.previous_close,
            highPrice=item.high_price,
            lowPrice=item.low_price,
            turnoverAmountCny=item.turnover_amount_cny,
            tradingStatus=(
                item.trading_status.value if item.trading_status else None
            ),
        ))

    index_points = []
    for item in indices.items:
        if not isinstance(item, IndexQuoteSnapshot):
            continue
        local_source_time = (
            item.source_time.astimezone(SHANGHAI_TZ)
            if item.source_time is not None else None
        )
        if (
            local_source_time is None
            or local_source_time.date() != trade_date
            or local_source_time.time().replace(tzinfo=None)
            < MARKET_CLOSE_TIME
            or item.fetched_at > completed_at
            or item.price is None
            or item.price <= 0
            or item.change_percent is None
        ):
            continue
        index_points.append(DailyMarketIndexOutcomePoint(
            indexKey=item.index_key.value,
            symbol=item.symbol,
            sourceSymbol=item.source_symbol,
            sourceTime=item.source_time,
            fetchedAt=item.fetched_at,
            price=item.price,
            changePercent=item.change_percent,
        ))

    missing_symbols = sorted(set(symbols) - returned_symbols)
    reasons = []
    if missing_symbols:
        reasons.append("daily_security_quote_incomplete")
    if len(index_points) != 4:
        reasons.append("daily_market_index_incomplete")
    if not security_points or not index_points:
        status = "failed"
    elif reasons:
        status = "partial"
    else:
        status = "ready"
    security_points.sort(key=lambda item: item.symbol)
    index_points.sort(key=lambda item: item.index_key)
    security_digest = _outcome_points_digest(security_points)
    index_digest = _outcome_points_digest(index_points)
    snapshot_id = _daily_snapshot_id(
        task_bundle_id=task_bundle.task_bundle_id,
        output_bundle_id=output_bundle.bundle_id,
        trade_date=trade_date,
        security_digest=security_digest,
        index_digest=index_digest,
        membership_digest=_membership_digest(memberships_by_sample),
    )
    source_ids = [
        f"{quotes.meta.source}:{quotes.meta.batch_id}:sha256:{security_digest}",
        f"{indices.meta.source}:{indices.meta.batch_id}:sha256:{index_digest}",
    ]
    snapshot = DailyOutcomeSnapshot(
        snapshotId=snapshot_id,
        taskBundleId=task_bundle.task_bundle_id,
        outputBundleId=output_bundle.bundle_id,
        tradeDate=trade_date,
        capturedAt=completed_at,
        status=status,
        expectedSecurityCount=len(symbols),
        readySecurityCount=len(security_points),
        sectorMembershipsBySample=memberships_by_sample,
        securityQuotes=security_points,
        marketIndices=index_points,
        missingSymbols=missing_symbols,
        sourceIds=source_ids,
        reasons=reasons,
    )
    resolved_output.mkdir(parents=True, exist_ok=False)
    snapshot_path = resolved_output / "daily-outcome-snapshot.json"
    snapshot_sha = _atomic_write_json(snapshot_path, snapshot)
    manifest_path = resolved_output / "manifest.json"
    _atomic_write_json(manifest_path, {
        "contractId": "radar-replay-outcome-daily-manifest-v1",
        "snapshotId": snapshot.snapshot_id,
        "taskBundleId": task_bundle.task_bundle_id,
        "outputBundleId": output_bundle.bundle_id,
        "tradeDate": trade_date,
        "capturedAt": completed_at,
        "status": snapshot.status,
        "files": {
            "dailyOutcomeSnapshot": {
                "path": snapshot_path.name,
                "sha256": snapshot_sha,
            },
        },
    })
    return DailyOutcomeCaptureResult(
        output_dir=resolved_output,
        snapshot_path=snapshot_path,
        manifest_path=manifest_path,
        snapshot=snapshot,
    )


def capture_replay_outcome_day_from_files(
    *,
    task_bundle_path: Path,
    output_bundle_path: Path,
    output_dir: Path,
    trade_date: date,
    captured_at: datetime,
    sources: DailyOutcomeCaptureSources = DailyOutcomeCaptureSources(),
    day_kind_provider: Callable[[date], str] = _official_day_kind,
) -> DailyOutcomeCaptureResult:
    task_bundle, output_bundle = load_replay_objective_inputs(
        task_bundle_path=task_bundle_path,
        output_bundle_path=output_bundle_path,
    )
    return capture_replay_outcome_day(
        task_bundle=task_bundle,
        output_bundle=output_bundle,
        output_dir=output_dir,
        trade_date=trade_date,
        captured_at=captured_at,
        sources=sources,
        day_kind_provider=day_kind_provider,
    )
