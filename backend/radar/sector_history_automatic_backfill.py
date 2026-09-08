"""行业历史主动回填的断点续跑编排器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

from radar.sector_history_backfill import (
    HistoricalMinuteBar,
    HistoricalMinuteSeries,
    SectorHistoryBackfillQuery,
    SectorHistoryBackfillResult,
    build_sector_history_backfill,
)
from radar.sector_history_backfill_collector import (
    SectorHistoryMinuteFrozenBatch,
    fetch_sector_history_minute_series_batch,
)
from radar.sector_history_trading_presence import (
    HistoricalTradingPresenceBatch,
    fetch_historical_trading_presence_batch,
)


SECTOR_HISTORY_AUTOMATIC_BACKFILL_CONTRACT_ID = (
    "radar-sector-history-automatic-backfill-v1"
)
SECTOR_HISTORY_SERIES_CHECKPOINT_CONTRACT_ID = (
    "radar-sector-history-series-checkpoint-v1"
)


@dataclass(frozen=True, repr=False)
class SectorHistoryAutomaticBackfillRequest:
    radar_run_id: str
    as_of: datetime
    comparable_time: time
    expected_trade_dates: Tuple[date, ...]
    classification_release: Any = field(repr=False)
    memberships_by_division: Mapping[str, Tuple[str, ...]] = field(repr=False)
    total_shares_by_symbol: Mapping[str, float] = field(repr=False)
    source_batch_ids: Tuple[str, ...]
    terminal_non_trading_symbols: Tuple[str, ...] = ()
    verified_non_trading_dates_by_symbol: Mapping[
        str, Tuple[date, ...]
    ] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SectorHistoryAutomaticBackfillResult:
    status: str
    reasons: Tuple[str, ...]
    requested_count: int
    fetched_count: int
    reused_count: int
    failure_count: int
    evidence_path: Optional[Path]
    backfill_result: Optional[SectorHistoryBackfillResult] = field(
        default=None,
        repr=False,
    )
    replay_query: Optional[SectorHistoryBackfillQuery] = field(
        default=None,
        repr=False,
    )
    contract_id: str = SECTOR_HISTORY_AUTOMATIC_BACKFILL_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "requestedCount": self.requested_count,
            "fetchedCount": self.fetched_count,
            "reusedCount": self.reused_count,
            "failureCount": self.failure_count,
            "evidencePath": (
                str(self.evidence_path) if self.evidence_path else None
            ),
            "backfill": (
                self.backfill_result.to_evidence()
                if self.backfill_result is not None else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        }


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()


def _write_atomic(
    path: Path,
    payload: Mapping[str, object],
    *,
    compact: bool = False,
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _request_identity(value: SectorHistoryAutomaticBackfillRequest) -> str:
    return _digest({
        "classificationDocumentSha256": getattr(
            value.classification_release,
            "document_sha256",
            None,
        ),
        "memberships": {
            key: list(value.memberships_by_division[key])
            for key in sorted(value.memberships_by_division)
        },
    })


def _checkpoint_requires_refresh(
    series: HistoricalMinuteSeries,
    *,
    latest_completed_trade_date: date,
    latest_verified_non_trading: bool,
) -> bool:
    if latest_verified_non_trading:
        return False
    return not any(
        bar.occurred_at.date() == latest_completed_trade_date
        for bar in series.bars
    )


def _series_payload(
    series: HistoricalMinuteSeries,
    *,
    request_identity: str,
) -> Mapping[str, object]:
    return {
        "contractId": SECTOR_HISTORY_SERIES_CHECKPOINT_CONTRACT_ID,
        "requestIdentity": request_identity,
        "symbol": series.symbol,
        "sourceContractId": series.source_contract_id,
        "sourceUrl": series.source_url,
        "fetchedAt": series.fetched_at.isoformat(),
        "contentSha256": series.content_sha256,
        "bars": [
            [
                item.occurred_at.isoformat(),
                item.close,
                item.turnover_amount_cny,
                item.volume_shares,
            ]
            for item in series.bars
        ],
    }


def _series_from_payload(
    payload: Any,
    *,
    symbol: str,
) -> HistoricalMinuteSeries:
    embedded_identity = (
        payload.get("requestIdentity")
        if isinstance(payload, Mapping) else None
    )
    if (
        not isinstance(payload, Mapping)
        or payload.get("contractId")
        != SECTOR_HISTORY_SERIES_CHECKPOINT_CONTRACT_ID
        or not isinstance(embedded_identity, str)
        or len(embedded_identity) != 64
        or any(value not in "0123456789abcdef" for value in embedded_identity)
        or payload.get("symbol") != symbol
        or not isinstance(payload.get("bars"), list)
    ):
        raise ValueError("sector_history_checkpoint_unverified")
    bars = []
    for row in payload["bars"]:
        if not isinstance(row, list) or len(row) not in {3, 4}:
            raise ValueError("sector_history_checkpoint_unverified")
        bars.append(HistoricalMinuteBar(
            occurred_at=datetime.fromisoformat(row[0]),
            close=row[1],
            turnover_amount_cny=row[2],
            volume_shares=row[3] if len(row) == 4 else None,
        ))
    return HistoricalMinuteSeries(
        symbol=symbol,
        source_contract_id=payload.get("sourceContractId"),
        source_url=payload.get("sourceUrl"),
        fetched_at=datetime.fromisoformat(payload.get("fetchedAt")),
        content_sha256=payload.get("contentSha256"),
        bars=tuple(bars),
    )


def _published_checkpoint_root(artifact_dir: Path) -> Optional[Path]:
    """在合同升级后继续复用最近一次已发布的真实逐证券断点。"""

    root = artifact_dir.expanduser().resolve()
    try:
        manifest = json.loads(
            (root / "latest.json").read_text(encoding="utf-8")
        )
        relative = manifest.get("evidenceRelativePath")
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("contractId")
        != "radar-sector-history-store-manifest-v1"
        or not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
    ):
        return None
    candidate = (root / relative).resolve().parent
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if (candidate / "series").is_dir() else None


def _analysis_payload(
    value: SectorHistoryBackfillResult,
) -> Mapping[str, object]:
    latest_market = value.market_samples[-1] if value.market_samples else None
    return {
        **value.to_evidence(),
        "marketLatest": (
            {
                "tradeDate": latest_market.trade_date.isoformat(),
                "equalWeightedReturn": latest_market.equal_weighted_return,
                "marketCapWeightedReturn": (
                    latest_market.market_cap_weighted_return
                ),
            }
            if latest_market is not None else None
        ),
        "sectors": [
            {
                "divisionCode": item.division_code,
                "memberCount": item.member_count,
                "sameMinuteSampleCount": len(
                    item.same_minute_turnover_samples
                ),
                "persistenceSampleCount": len(item.persistence_samples),
                "latestTurnoverRatio20d": item.latest_turnover_ratio_20d,
                "latestPersistencePositiveRatio5d": (
                    item.latest_persistence_positive_ratio_5d
                ),
                "latestRelativeReturn": (
                    item.relative_return_samples[-1].value
                    if item.relative_return_samples else None
                ),
                "comparableTime": (
                    item.comparable_time.isoformat()
                    if item.comparable_time is not None else None
                ),
            }
            for item in value.sector_analyses
        ],
    }


def run_sector_history_automatic_backfill(
    request: Any,
    *,
    artifact_dir: Path,
    reuse_store_dir: Optional[Path] = None,
    minute_loader: Callable[
        [Tuple[str, ...], Tuple[date, ...]],
        SectorHistoryMinuteFrozenBatch,
    ] = fetch_sector_history_minute_series_batch,
    presence_loader: Callable[..., HistoricalTradingPresenceBatch] = (
        fetch_historical_trading_presence_batch
    ),
    clock: Optional[Callable[[], datetime]] = None,
) -> SectorHistoryAutomaticBackfillResult:
    """按内容身份复用逐证券检查点，不写任何生产存储。"""

    if type(request) is not SectorHistoryAutomaticBackfillRequest:
        return SectorHistoryAutomaticBackfillResult(
            status="error",
            reasons=("sector_history_automatic_request_unverified",),
            requested_count=0,
            fetched_count=0,
            reused_count=0,
            failure_count=0,
            evidence_path=None,
        )
    symbols = tuple(sorted({
        symbol
        for members in request.memberships_by_division.values()
        for symbol in members
    }))
    verified_history_non_trading = (
        request.verified_non_trading_dates_by_symbol
    )
    if (
        not isinstance(verified_history_non_trading, Mapping)
        or any(
            symbol not in symbols
            or not isinstance(values, tuple)
            or len(values) != len(set(values))
            or any(value not in request.expected_trade_dates for value in values)
            for symbol, values in verified_history_non_trading.items()
        )
    ):
        return SectorHistoryAutomaticBackfillResult(
            status="error",
            reasons=("sector_history_non_trading_evidence_unverified",),
            requested_count=len(symbols),
            fetched_count=0,
            reused_count=0,
            failure_count=len(symbols),
            evidence_path=None,
        )
    identity = _request_identity(request)
    root = (
        _published_checkpoint_root(artifact_dir)
        or artifact_dir / f"sector-history-{identity[:16]}"
    )
    read_roots = [root]
    if reuse_store_dir is not None:
        external_root = _published_checkpoint_root(reuse_store_dir)
        if external_root is not None and external_root != root:
            read_roots.append(external_root)
    series_dir = root / "series"
    try:
        series_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return SectorHistoryAutomaticBackfillResult(
            status="error",
            reasons=("sector_history_artifact_directory_unavailable",),
            requested_count=len(symbols),
            fetched_count=0,
            reused_count=0,
            failure_count=len(symbols),
            evidence_path=None,
        )

    series_by_symbol = {}
    reused_symbols = set()
    reused = 0
    missing = []
    for symbol in symbols:
        series = None
        for read_root in read_roots:
            path = read_root / "series" / f"series-{symbol}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                candidate = _series_from_payload(
                    payload,
                    symbol=symbol,
                )
                if _checkpoint_requires_refresh(
                    candidate,
                    latest_completed_trade_date=(
                        request.expected_trade_dates[-1]
                    ),
                    latest_verified_non_trading=(
                        request.expected_trade_dates[-1]
                        in verified_history_non_trading.get(symbol, ())
                    ),
                ):
                    continue
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            series = candidate
            break
        if series is None:
            missing.append(symbol)
            continue
        series_by_symbol[symbol] = series
        reused_symbols.add(symbol)
        reused += 1

    fetched = 0
    loader_failure_count = 0
    if missing:
        try:
            batch = minute_loader(tuple(missing), request.expected_trade_dates)
        except Exception:
            batch = None
        if type(batch) is SectorHistoryMinuteFrozenBatch:
            loader_failure_count = batch.failure_count
            for symbol, series in batch.series_by_symbol.items():
                if symbol not in missing:
                    continue
                path = series_dir / f"series-{symbol}.json"
                try:
                    _write_atomic(path, _series_payload(
                        series,
                        request_identity=identity,
                    ), compact=True)
                except OSError:
                    continue
                series_by_symbol[symbol] = series
                fetched += 1
        else:
            loader_failure_count = len(missing)

    dates_to_verify = {}
    for symbol, series in series_by_symbol.items():
        grouped = {value: [] for value in request.expected_trade_dates}
        for bar in series.bars:
            if bar.occurred_at.date() in grouped:
                grouped[bar.occurred_at.date()].append(bar)
        gaps = []
        for trade_day in request.expected_trade_dates:
            bars = sorted(
                grouped[trade_day], key=lambda item: item.occurred_at
            )
            bar_times = tuple(
                item.occurred_at.timetz().replace(tzinfo=None)
                for item in bars
            )
            if not (
                bars
                and bar_times[0] <= time(9, 35)
                and request.comparable_time in bar_times
                and bar_times[-1] >= time(15, 0)
            ):
                gaps.append(trade_day)
        if gaps:
            dates_to_verify[symbol] = tuple(gaps)
    presence_evidence = {
        "contractId": "radar-sector-trading-presence-v1",
        "sourceContractIds": [],
        "sourceStatus": "not_required",
        "requestedCount": 0,
        "returnedCount": 0,
        "failureCount": 0,
    }
    verified_trading = {}
    verified_non_trading = {}
    daily_trading_proofs = {}
    presence_batches = []
    if dates_to_verify:
        try:
            presence = presence_loader(
                dates_to_verify,
                expected_trade_dates=request.expected_trade_dates,
                terminal_non_trading_symbols=tuple(
                    symbol
                    for symbol in request.terminal_non_trading_symbols
                    if symbol in dates_to_verify
                ),
            )
        except Exception:
            presence = None
        if type(presence) is HistoricalTradingPresenceBatch:
            presence_batches.append(presence)
            verified_trading.update(
                presence.verified_trading_dates_by_symbol
            )
            verified_non_trading.update(
                presence.verified_non_trading_dates_by_symbol
            )
            daily_trading_proofs.update(
                presence.daily_trading_proofs_by_symbol
            )
    if presence_batches:
        reason_counts = {}
        for batch in presence_batches:
            for reason, count in batch.failure_reason_counts.items():
                reason_counts[reason] = reason_counts.get(reason, 0) + count
        presence_evidence = {
            "contractId": "radar-sector-trading-presence-v1",
            "sourceContractIds": sorted({
                contract
                for batch in presence_batches
                for contract in batch.source_contract_ids_by_symbol.values()
            }),
            "sourceStatus": (
                "ready" if all(
                    batch.source_status == "ready"
                    and batch.failure_count == 0
                    for batch in presence_batches
                ) else "source_failed"
            ),
            "requestedCount": sum(
                batch.requested_count for batch in presence_batches
            ),
            "returnedCount": sum(
                len(batch.source_hashes_by_symbol)
                for batch in presence_batches
            ),
            "failureCount": sum(
                batch.failure_count for batch in presence_batches
            ),
            "failureReasonCounts": reason_counts,
        }

    reconciliation_refresh = []
    for symbol, proofs in daily_trading_proofs.items():
        series = series_by_symbol.get(symbol)
        if (
            symbol not in reused_symbols
            or type(series) is not HistoricalMinuteSeries
        ):
            continue
        proof_dates = {proof.trade_date for proof in proofs}
        bars_by_date = {
            trade_day: tuple(
                bar for bar in series.bars
                if bar.occurred_at.date() == trade_day
            )
            for trade_day in proof_dates
        }
        if any(
            not bars
            or (
                (
                    bars[0].occurred_at.timetz().replace(tzinfo=None)
                    > time(9, 35)
                    or bars[-1].occurred_at.timetz().replace(tzinfo=None)
                    < time(15, 0)
                )
                and any(bar.volume_shares is None for bar in bars)
            )
            for bars in bars_by_date.values()
        ):
            reconciliation_refresh.append(symbol)
    if reconciliation_refresh:
        refresh_symbols = tuple(sorted(reconciliation_refresh))
        try:
            refresh_batch = minute_loader(
                refresh_symbols,
                request.expected_trade_dates,
            )
        except Exception:
            refresh_batch = None
        refreshed = set()
        if type(refresh_batch) is SectorHistoryMinuteFrozenBatch:
            loader_failure_count += refresh_batch.failure_count
            for symbol, series in refresh_batch.series_by_symbol.items():
                if symbol not in refresh_symbols:
                    continue
                try:
                    _write_atomic(
                        series_dir / f"series-{symbol}.json",
                        _series_payload(series, request_identity=identity),
                        compact=True,
                    )
                except OSError:
                    continue
                series_by_symbol[symbol] = series
                refreshed.add(symbol)
                fetched += 1
                reused -= 1
        loader_failure_count += len(set(refresh_symbols) - refreshed)

    completed_at = (
        clock()
        if clock is not None
        else datetime.now(request.as_of.tzinfo)
    )
    if (
        not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or completed_at.utcoffset() is None
    ):
        completed_at = request.as_of
    effective_as_of = max(
        request.as_of,
        completed_at,
        *(series.fetched_at for series in series_by_symbol.values()),
    )
    replay_query = SectorHistoryBackfillQuery(
        radar_run_id=request.radar_run_id,
        as_of=effective_as_of,
        comparable_time=request.comparable_time,
        expected_trade_dates=request.expected_trade_dates,
        classification_release=request.classification_release,
        memberships_by_division=request.memberships_by_division,
        series_by_symbol=series_by_symbol,
        total_shares_by_symbol=request.total_shares_by_symbol,
        source_batch_ids=request.source_batch_ids,
        verified_trading_dates_by_symbol=verified_trading,
        verified_non_trading_dates_by_symbol=verified_non_trading,
        daily_trading_proofs_by_symbol=daily_trading_proofs,
    )
    backfill = build_sector_history_backfill(replay_query)
    failure_count = len(symbols) - len(series_by_symbol)
    if failure_count and loader_failure_count == 0:
        loader_failure_count = failure_count
    evidence_payload = {
        "contractId": SECTOR_HISTORY_AUTOMATIC_BACKFILL_CONTRACT_ID,
        "status": backfill.status,
        "reasons": list(backfill.reasons),
        "radarRunId": request.radar_run_id,
        "asOf": effective_as_of.isoformat(),
        "requestIdentity": identity,
        "requestedCount": len(symbols),
        "fetchedCount": fetched,
        "reusedCount": reused,
        "failureCount": failure_count,
        "tradingPresence": presence_evidence,
        "analysis": _analysis_payload(backfill),
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }
    evidence_path = root / f"evidence-{_digest(evidence_payload)}.json"
    try:
        _write_atomic(evidence_path, evidence_payload)
    except OSError:
        evidence_path = None
    reasons = backfill.reasons
    if loader_failure_count and not reasons:
        reasons = ("sector_history_minute_source_failed",)
    return SectorHistoryAutomaticBackfillResult(
        status=backfill.status,
        reasons=reasons,
        requested_count=len(symbols),
        fetched_count=fetched,
        reused_count=reused,
        failure_count=failure_count,
        evidence_path=evidence_path,
        backfill_result=backfill,
        replay_query=replay_query,
    )
