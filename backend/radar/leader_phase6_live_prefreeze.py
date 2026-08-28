"""阶段6历史与行业证据的最终候选计划预冻结绑定。

调用方必须在最终候选计划生成前完成真实证据采集。最终时点的
行业特征由候选采集编排从冻结原始输入重算；本模块不改写上游时间，
并在暴露预冻结输入前重放现有历史 collector 和行业 collector。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_history_production_collector import (
    LeaderHistoryProductionFrozenBatch,
    collect_leader_history_production_source,
    fetch_leader_history_series_batch,
)
from radar.sources.leader_history_public_poc import (
    PublicHistorySeries,
    build_point_in_time_industry_membership,
)
from radar.sources.leader_tradability_public_poc import (
    PublicTradingCalendarEvidence,
)
from radar.leader_live_candidate_collection_batch import (
    LeaderLiveCandidateRuntimeInputs,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)
from radar.sector_rule_production_collector import (
    SectorRuleProductionFrozenBatch,
    collect_sector_rule_production_source,
)
from radar.sector_history_automatic_backfill import (
    SectorHistoryAutomaticBackfillRequest,
    run_sector_history_automatic_backfill,
)
from radar.sector_history_backfill import (
    SectorHistoricalAnalysis,
    SectorHistoryBackfillQuery,
    SectorHistoryBackfillResult,
    build_sector_history_backfill,
)
from radar.sector_rule_readiness import (
    ABNORMAL_TRADING_DAY_FILTER_CONTRACT_ID,
    SECTOR_RULE_VERSION,
    SectorHistoryCoverageEvidence,
    SectorMarketBaselineEvidence,
    SectorThresholdApprovalEvidence,
)
from radar.sector_rule_runtime_bridge import SectorRuleRuntimeSourceBatch
from radar.sector_threshold_review import (
    SectorThresholdApprovalRecord,
    SectorThresholdApprovalLoadResult,
    bind_latest_sector_threshold_approval,
    load_sector_threshold_approval,
    is_sector_threshold_approval_record_valid,
)
from market_calendar import parse_sse_calendar
from radar.contracts import (
    IndustryClassificationSnapshot,
    IndustryHistoryStatus,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    SourceBatch,
    SourceStatus,
    UnitVerificationStatus,
)
from radar.sources.leader_tradability_public_live_poc import (
    PublicCalendarDocument,
)


LEADER_PHASE6_PLAN_PREFREEZE_EVIDENCE_CONTRACT_ID = (
    "radar-leader-phase6-plan-prefreeze-evidence-v1"
)
LEADER_PHASE6_PREFROZEN_INPUTS_CONTRACT_ID = (
    "radar-leader-phase6-prefrozen-inputs-v1"
)
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class LeaderPhase6HistoryPreparationError(ValueError):
    """历史预冻结失败；对外保持稳定原因，本地诊断保留数据批次。"""

    def __init__(self, history_batch: Any) -> None:
        super().__init__("leader_phase6_public_prepare_history_unverified")
        self.history_batch = history_batch


class LeaderPhase6ShareBasisPreparationError(ValueError):
    """股本基础失败；保留精确缺项供本地诊断。"""

    def __init__(
        self,
        *,
        missing_symbols: Tuple[str, ...] = (),
        invalid_symbols: Tuple[str, ...] = (),
    ) -> None:
        super().__init__("leader_phase6_public_prepare_share_basis_unverified")
        self.missing_symbols = missing_symbols
        self.invalid_symbols = invalid_symbols


@dataclass(frozen=True, repr=False)
class LeaderPhase6PlanPrefreezeEvidence:
    history: LeaderHistoryProductionFrozenBatch = field(repr=False)
    sector_history_evidence: SectorHistoryCoverageEvidence = field(
        repr=False
    )
    market_baseline_evidence: SectorMarketBaselineEvidence = field(
        repr=False
    )
    sector_historical_analyses: Tuple[SectorHistoricalAnalysis, ...] = field(
        repr=False
    )
    sector_comparable_time: time
    threshold_approval_record: SectorThresholdApprovalRecord = field(
        repr=False
    )
    completed_at: datetime
    threshold_approval_evidence: Optional[
        SectorThresholdApprovalEvidence
    ] = field(default=None, repr=False)
    contract_id: str = LEADER_PHASE6_PLAN_PREFREEZE_EVIDENCE_CONTRACT_ID


@dataclass(frozen=True, repr=False)
class LeaderPhase6PrefrozenInputs:
    history: LeaderHistoryProductionFrozenBatch = field(repr=False)
    sector_rule: SectorRuleProductionFrozenBatch = field(repr=False)
    sector_historical_analyses: Tuple[SectorHistoricalAnalysis, ...] = field(
        repr=False
    )
    sector_comparable_time: time
    threshold_approval_record: SectorThresholdApprovalRecord = field(
        repr=False
    )
    contract_id: str = LEADER_PHASE6_PREFROZEN_INPUTS_CONTRACT_ID

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "history": self.history.to_evidence(),
            "sectorRule": self.sector_rule.to_evidence(),
            "sectorHistoricalAnalysisCount": len(
                self.sector_historical_analyses
            ),
            "sectorComparableTime": self.sector_comparable_time.isoformat(),
            "sectorThresholdApprovalId": (
                self.threshold_approval_record.approval_id
            ),
        }


@dataclass(frozen=True, repr=False)
class LeaderPhase6PreparedHistoricalInputs:
    """长耗时历史抓取在最终行情批次前完成后的只读内存结果。"""

    radar_run_id: str
    classification_document_sha256: str
    membership_symbols_by_industry: Mapping[str, Tuple[str, ...]] = field(
        repr=False
    )
    expected_trade_dates: Tuple[date, ...]
    series_by_symbol: Mapping[str, PublicHistorySeries] = field(repr=False)
    calendar_evidence: PublicTradingCalendarEvidence = field(repr=False)
    sector_history_rows: Tuple[Any, ...] = field(repr=False)
    sector_historical_analyses: Tuple[SectorHistoricalAnalysis, ...] = field(
        repr=False
    )
    sector_comparable_time: time
    sector_history_replay_query: SectorHistoryBackfillQuery = field(
        repr=False
    )
    sector_history_source_as_of: datetime
    threshold_approval_evidence: SectorThresholdApprovalEvidence = field(
        repr=False
    )
    threshold_approval_record: SectorThresholdApprovalRecord = field(
        repr=False
    )
    prepared_at: datetime
    contract_id: str = (
        "radar-leader-phase6-prepared-historical-inputs-v1"
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.radar_run_id, str)
            or not self.radar_run_id.strip()
            or re.fullmatch(
                r"[0-9a-f]{64}",
                self.classification_document_sha256,
            ) is None
            or not isinstance(self.membership_symbols_by_industry, Mapping)
            or not self.membership_symbols_by_industry
            or any(
                not isinstance(code, str)
                or not code
                or not isinstance(symbols, tuple)
                or not symbols
                or len(symbols) != len(set(symbols))
                or any(
                    re.fullmatch(r"[0-9]{6}", symbol) is None
                    for symbol in symbols
                )
                for code, symbols
                in self.membership_symbols_by_industry.items()
            )
            or not isinstance(self.expected_trade_dates, tuple)
            or len(self.expected_trade_dates) != 21
            or self.expected_trade_dates
            != tuple(sorted(self.expected_trade_dates))
            or len(self.expected_trade_dates)
            != len(set(self.expected_trade_dates))
            or not isinstance(self.series_by_symbol, Mapping)
            or any(
                not isinstance(symbol, str)
                or type(series) is not PublicHistorySeries
                or series.symbol != symbol
                for symbol, series in self.series_by_symbol.items()
            )
            or type(self.calendar_evidence)
            is not PublicTradingCalendarEvidence
            or self.calendar_evidence.trading_dates
            != self.expected_trade_dates
            or not isinstance(self.sector_history_rows, tuple)
            or not self.sector_history_rows
            or not isinstance(self.sector_historical_analyses, tuple)
            or not self.sector_historical_analyses
            or any(
                type(item) is not SectorHistoricalAnalysis
                or item.comparable_time != self.sector_comparable_time
                for item in self.sector_historical_analyses
            )
            or tuple(sorted(
                item.division_code
                for item in self.sector_historical_analyses
            )) != tuple(sorted(
                getattr(item, "division_code", None)
                for item in self.sector_history_rows
            ))
            or type(self.sector_comparable_time) is not time
            or type(self.sector_history_replay_query)
            is not SectorHistoryBackfillQuery
            or self.sector_history_replay_query.radar_run_id
            != self.radar_run_id
            or self.sector_history_replay_query.expected_trade_dates
            != self.expected_trade_dates
            or self.sector_history_replay_query.comparable_time
            != self.sector_comparable_time
            or self.sector_history_replay_query.as_of
            != self.sector_history_source_as_of
            or type(self.threshold_approval_evidence)
            is not SectorThresholdApprovalEvidence
            or type(self.threshold_approval_record)
            is not SectorThresholdApprovalRecord
            or not is_sector_threshold_approval_record_valid(
                self.threshold_approval_record
            )
            or self.threshold_approval_record.rule_version
            != self.threshold_approval_evidence.rule_version
            or self.threshold_approval_record.threshold_set_id
            != self.threshold_approval_evidence.threshold_set_id
            or self.threshold_approval_record.approval_id
            != self.threshold_approval_evidence.approval_id
            or self.threshold_approval_record.approved_at
            != self.threshold_approval_evidence.approved_at
            or not _aware(self.sector_history_source_as_of)
            or not _aware(self.prepared_at)
            or self.sector_history_source_as_of > self.prepared_at
        ):
            raise ValueError(
                "leader_phase6_prepared_historical_inputs_unverified"
            )

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "radarRunId": self.radar_run_id,
            "classificationDocumentSha256": (
                self.classification_document_sha256
            ),
            "industryCount": len(self.membership_symbols_by_industry),
            "expectedTradeDateCount": len(self.expected_trade_dates),
            "seriesCount": len(self.series_by_symbol),
            "sectorHistorySourceAsOf": (
                self.sector_history_source_as_of.isoformat()
            ),
            "sectorHistoricalAnalysisCount": len(
                self.sector_historical_analyses
            ),
            "sectorComparableTime": self.sector_comparable_time.isoformat(),
            "preparedAt": self.prepared_at.isoformat(),
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def resolve_sector_comparable_time(as_of: datetime) -> time:
    """返回当轮已经完成的上海市场5分钟比较时点。"""

    if not _aware(as_of):
        raise ValueError("sector_comparable_time_unavailable")
    local_time = as_of.astimezone(_SHANGHAI_TZ).time().replace(tzinfo=None)
    if local_time < time(9, 35):
        raise ValueError("sector_comparable_time_unavailable")
    if local_time >= time(15, 0):
        return time(15, 0)
    if time(11, 30) <= local_time < time(13, 0):
        return time(11, 30)
    total_minutes = local_time.hour * 60 + local_time.minute
    completed_minutes = total_minutes - total_minutes % 5
    return time(completed_minutes // 60, completed_minutes % 60)


def _history_fetched_times(
    history: LeaderHistoryProductionFrozenBatch,
) -> Tuple[datetime, ...]:
    values = []
    calendar = history.calendar_evidence
    if calendar is not None:
        values.append(getattr(calendar, "fetched_at", None))
    values.extend(
        getattr(value, "fetched_at", None)
        for value in history.memberships_by_industry.values()
    )
    values.extend(
        getattr(value, "fetched_at", None)
        for value in history.series_by_symbol.values()
    )
    if not values or any(not _aware(value) for value in values):
        return ()
    return tuple(values)


def _board_index(symbol: str) -> str:
    return "sh000001" if symbol.startswith("6") else "sz399001"


def _sector_membership_scope(
    memberships: Mapping[str, Tuple[str, ...]],
) -> Mapping[str, Tuple[str, ...]]:
    return {
        code: tuple(
            symbol for symbol in symbols
            if re.fullmatch(r"[036][0-9]{5}", symbol)
        )
        for code, symbols in memberships.items()
        if sum(
            re.fullmatch(r"[036][0-9]{5}", symbol) is not None
            for symbol in symbols
        ) > 1
    }


def _current_market_baseline(
    runtime: LeaderLiveCandidateRuntimeInputs,
    *,
    completed_at: datetime,
) -> SectorMarketBaselineEvidence:
    plan = runtime.candidate_plan
    expected_symbols = {
        symbol
        for values in runtime.source_context
        .industry_constituent_symbols_by_code.values()
        for symbol in values
        if re.fullmatch(r"[036][0-9]{5}", symbol)
    }
    rows_by_symbol = {}
    for row in runtime.quote_batch.items:
        if row.symbol in expected_symbols:
            rows_by_symbol.setdefault(row.symbol, []).append(row)
    active = []
    for symbol in sorted(expected_symbols):
        rows = rows_by_symbol.get(symbol, [])
        if len(rows) != 1:
            raise ValueError("leader_phase6_market_baseline_scope_unverified")
        row = rows[0]
        if row.is_explicitly_non_trading:
            continue
        if (
            isinstance(row.change_percent, bool)
            or not isinstance(row.change_percent, (int, float))
            or not math.isfinite(float(row.change_percent))
            or isinstance(row.market_cap_source, bool)
            or not isinstance(row.market_cap_source, (int, float))
            or not math.isfinite(float(row.market_cap_source))
            or float(row.market_cap_source) <= 0
        ):
            raise ValueError("leader_phase6_market_baseline_scope_unverified")
        active.append(row)
    if not active:
        raise ValueError("leader_phase6_market_baseline_scope_unverified")
    total_market_cap = sum(float(row.market_cap_source) for row in active)
    return SectorMarketBaselineEvidence(
        radar_run_id=plan.radar_run_id,
        as_of=completed_at,
        equal_weighted_return=sum(
            float(row.change_percent) for row in active
        ) / len(active),
        market_cap_weighted_return=sum(
            float(row.change_percent) * float(row.market_cap_source)
            for row in active
        ) / total_market_cap,
        source_batch_ids=(plan.quote_batch_id,),
    )


def build_leader_phase6_prepared_prefreeze_loader(
    prepared: LeaderPhase6PreparedHistoricalInputs,
    *,
    clock: Any,
    sector_history_rebuilder: Any = build_sector_history_backfill,
):
    """把冻结前长耗时结果绑定到随后产生的同轮最终行情身份。"""

    if (
        type(prepared) is not LeaderPhase6PreparedHistoricalInputs
        or not callable(clock)
        or not callable(sector_history_rebuilder)
    ):
        raise ValueError("leader_phase6_prepared_loader_unverified")

    def load(
        runtime: LeaderLiveCandidateRuntimeInputs,
        provisional_as_of: datetime,
    ) -> LeaderPhase6PlanPrefreezeEvidence:
        if (
            type(runtime) is not LeaderLiveCandidateRuntimeInputs
            or not _aware(provisional_as_of)
        ):
            raise ValueError("leader_phase6_prepared_binding_unverified")
        completed_at = clock()
        current_memberships = {
            key: tuple(value)
            for key, value in runtime.source_context
            .industry_constituent_symbols_by_code.items()
        }
        if any((
            not _aware(completed_at),
            completed_at < provisional_as_of,
            completed_at < prepared.prepared_at,
            prepared.radar_run_id != runtime.candidate_plan.radar_run_id,
            prepared.classification_document_sha256
            != runtime.industry_release.document_sha256,
            any(
                prepared.membership_symbols_by_industry.get(code) != symbols
                for code, symbols in current_memberships.items()
            ),
            prepared.calendar_evidence.fetched_at > completed_at,
            prepared.threshold_approval_evidence.approved_at > completed_at,
        )):
            raise ValueError("leader_phase6_prepared_binding_unverified")
        plan = runtime.candidate_plan
        industries = tuple(dict.fromkeys(
            item.industry_code for item in plan.items
        ))
        memberships = {}
        expected_series = set()
        for code in industries:
            all_symbols = current_memberships.get(code)
            symbols = tuple(
                symbol for symbol in (all_symbols or ())
                if re.fullmatch(r"[036][0-9]{5}", symbol)
            )
            candidate = next(
                (
                    item.symbol
                    for item in plan.items
                    if item.industry_code == code
                ),
                None,
            )
            if not symbols or candidate not in symbols:
                raise ValueError("leader_phase6_prepared_binding_unverified")
            memberships[code] = build_point_in_time_industry_membership(
                release=runtime.industry_release,
                industry_code=code,
                candidate_symbol=candidate,
                member_symbols=symbols,
                excluded_out_of_scope_count=(
                    len(all_symbols) - len(symbols)
                ),
            )
            expected_series.update(symbols)
        expected_series.update(
            _board_index(item.symbol) for item in plan.items
        )
        if not expected_series.issubset(set(prepared.series_by_symbol)):
            raise ValueError("leader_phase6_prepared_binding_unverified")
        sector_memberships = _sector_membership_scope(current_memberships)
        replay = prepared.sector_history_replay_query
        expected_sector_symbols = {
            symbol
            for symbols in sector_memberships.values()
            for symbol in symbols
        }
        if (
            dict(replay.memberships_by_division) != sector_memberships
            or set(replay.series_by_symbol) != expected_sector_symbols
            or set(replay.total_shares_by_symbol)
            != expected_sector_symbols
            or getattr(
                replay.classification_release,
                "document_sha256",
                None,
            ) != prepared.classification_document_sha256
        ):
            raise ValueError("leader_phase6_prepared_binding_unverified")
        history = LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=prepared.expected_trade_dates,
            memberships_by_industry=memberships,
            series_by_symbol=prepared.series_by_symbol,
            calendar_evidence=prepared.calendar_evidence,
        )
        if any(
            fetched_at > completed_at
            for fetched_at in _history_fetched_times(history)
        ):
            raise ValueError("leader_phase6_prepared_binding_unverified")
        comparable_time = resolve_sector_comparable_time(
            runtime.sector_feature_batch.source_time
        )
        replay_query = replace(
            replay,
            radar_run_id=plan.radar_run_id,
            as_of=completed_at,
            comparable_time=comparable_time,
            classification_release=runtime.industry_release,
            memberships_by_division=sector_memberships,
        )
        rebuilt = sector_history_rebuilder(replay_query)
        sector_history = getattr(rebuilt, "history_evidence", None)
        analyses = getattr(rebuilt, "sector_analyses", None)
        rows_by_code = {
            getattr(row, "division_code", None): row
            for row in getattr(sector_history, "rows", ())
        }
        if (
            type(rebuilt) is not SectorHistoryBackfillResult
            or rebuilt.status != "ready"
            or rebuilt.reasons
            or type(sector_history) is not SectorHistoryCoverageEvidence
            or sector_history.radar_run_id != plan.radar_run_id
            or sector_history.as_of != completed_at
            or sector_history.classification_document_sha256
            != runtime.industry_release.document_sha256
            or not isinstance(analyses, tuple)
            or not analyses
            or any(
                type(item) is not SectorHistoricalAnalysis
                or item.comparable_time != comparable_time
                for item in analyses
            )
            or any(code not in rows_by_code for code in industries)
        ):
            raise ValueError("leader_phase6_prepared_binding_unverified")
        return LeaderPhase6PlanPrefreezeEvidence(
            history=history,
            sector_history_evidence=sector_history,
            market_baseline_evidence=_current_market_baseline(
                runtime,
                completed_at=completed_at,
            ),
            threshold_approval_evidence=(
                prepared.threshold_approval_evidence
            ),
            sector_historical_analyses=(
                analyses
            ),
            sector_comparable_time=comparable_time,
            threshold_approval_record=(
                prepared.threshold_approval_record
            ),
            completed_at=completed_at,
        )

    return load


def _completed_official_trade_dates(
    document: PublicCalendarDocument,
    *,
    as_of: datetime,
) -> Tuple[date, ...]:
    values = []
    snapshots = {}
    current = as_of.date() - timedelta(days=1)
    while len(values) < 21:
        if current.year not in snapshots:
            snapshots[current.year] = parse_sse_calendar(
                document.text,
                current.year,
            )
        if (
            current.weekday() < 5
            and current.isoformat()
            not in snapshots[current.year].closed_days
        ):
            values.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(values))


def prepare_leader_phase6_public_historical_inputs(
    *,
    radar_run_id: str,
    security_master_batch: SourceBatch,
    classification_snapshot: IndustryClassificationSnapshot,
    share_quote_batch: SourceBatch,
    calendar_document: PublicCalendarDocument,
    artifact_dir: Path,
    reuse_store_dir: Optional[Path],
    clock: Any,
    history_checkpoint_dir: Optional[Path] = None,
    sector_checkpoint_dir: Optional[Path] = None,
    history_fetcher: Any = fetch_leader_history_series_batch,
    sector_backfill_runner: Any = run_sector_history_automatic_backfill,
    threshold_approval_loader: Any = load_sector_threshold_approval,
    sector_comparable_time: Optional[time] = None,
) -> LeaderPhase6PreparedHistoricalInputs:
    """先抓长耗时历史，再允许调用方开始最终候选行情批次。"""

    root = Path(artifact_dir).expanduser().resolve()
    try:
        root.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError("leader_phase6_prefreeze_artifact_path_unverified") \
            from exc
    checkpoint_root = Path(
        history_checkpoint_dir
        if history_checkpoint_dir is not None
        else root / "leader-history-checkpoints"
    ).expanduser().resolve()
    try:
        checkpoint_root.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_prefreeze_checkpoint_path_unverified"
        ) from exc
    sector_checkpoint_root = Path(
        sector_checkpoint_dir
        if sector_checkpoint_dir is not None else root
    ).expanduser().resolve()
    try:
        sector_checkpoint_root.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_prefreeze_sector_checkpoint_path_unverified"
        ) from exc
    if (
        isinstance(security_master_batch, SourceBatch)
        and (
            security_master_batch.meta.expected_count is None
            or security_master_batch.meta.row_coverage != 1.0
            or security_master_batch.meta.returned_count
            != len(security_master_batch.items)
            or bool(security_master_batch.meta.issues)
        )
    ):
        raise ValueError(
            "leader_phase6_public_prepare_security_master_unverified"
        )
    if (
        not isinstance(radar_run_id, str)
        or not radar_run_id.strip()
        or not callable(clock)
        or not callable(history_fetcher)
        or not callable(sector_backfill_runner)
        or not callable(threshold_approval_loader)
        or (
            sector_comparable_time is not None
            and type(sector_comparable_time) is not time
        )
        or not isinstance(security_master_batch, SourceBatch)
        or security_master_batch.meta.radar_run_id != radar_run_id
        or not security_master_batch.items
        or type(classification_snapshot) is not IndustryClassificationSnapshot
        or classification_snapshot.meta.radar_run_id != radar_run_id
        or classification_snapshot.release is None
        or classification_snapshot.release.history_status not in {
            IndustryHistoryStatus.FORWARD_OBSERVED,
            IndustryHistoryStatus.OFFICIAL_ARCHIVE_VERIFIED,
        }
        or not isinstance(share_quote_batch, SourceBatch)
        or share_quote_batch.meta.radar_run_id != radar_run_id
        or not share_quote_batch.items
        or type(calendar_document) is not PublicCalendarDocument
        or not _aware(calendar_document.source_time)
        or not _aware(calendar_document.fetched_at)
    ):
        raise ValueError("leader_phase6_public_prepare_contract_unverified")
    prepared_started_at = clock()
    if not _aware(prepared_started_at):
        raise ValueError("leader_phase6_public_prepare_clock_unverified")
    effective_sector_comparable_time = (
        sector_comparable_time
        if sector_comparable_time is not None
        else resolve_sector_comparable_time(prepared_started_at)
    )
    expected_dates = _completed_official_trade_dates(
        calendar_document,
        as_of=prepared_started_at,
    )
    master_symbols = {
        item.symbol for item in security_master_batch.items
        if getattr(item, "exchange", None) in {"sse", "szse", "bse"}
    }
    memberships = {}
    for record in classification_snapshot.records:
        if (
            record.record_status != IndustryRecordStatus.ACCEPTED
            or record.identity_status != IndustryIdentityStatus.EXACT
            or record.security_identity not in master_symbols
        ):
            continue
        memberships.setdefault(record.division_code, []).append(
            record.security_identity
        )
    membership_symbols = {
        code: tuple(sorted(set(symbols)))
        for code, symbols in memberships.items()
        if symbols
    }
    if not membership_symbols:
        raise ValueError("leader_phase6_public_prepare_memberships_unverified")
    sector_memberships = _sector_membership_scope(membership_symbols)
    history_symbols = tuple(sorted({
        symbol
        for symbols in membership_symbols.values()
        for symbol in symbols
        if re.fullmatch(r"[036][0-9]{5}", symbol)
    }))
    if not history_symbols:
        raise ValueError("leader_phase6_public_prepare_history_scope_unverified")
    query_symbols = tuple((
        *history_symbols,
        *(index for index in ("sh000001", "sz399001") if any(
            _board_index(symbol) == index for symbol in history_symbols
        )),
    ))
    history_batch = history_fetcher(
        query_symbols,
        expected_dates,
        clock=clock,
        checkpoint_dir=checkpoint_root,
        classification_document_sha256=(
            classification_snapshot.release.document_sha256
        ),
        terminal_non_trading_symbols=tuple(sorted(
            row.symbol for row in share_quote_batch.items
            if row.symbol in history_symbols
            and row.is_explicitly_non_trading
        )),
    )
    if (
        type(history_batch) is not LeaderHistoryProductionFrozenBatch
        or history_batch.source_status != "ready"
        or history_batch.failure_count != 0
        or history_batch.expected_trade_dates != expected_dates
        or not set(query_symbols).issubset(set(history_batch.series_by_symbol))
    ):
        raise LeaderPhase6HistoryPreparationError(history_batch)
    quote_by_symbol = {
        row.symbol: row for row in share_quote_batch.items
        if row.symbol in history_symbols
    }
    if set(quote_by_symbol) != set(history_symbols):
        raise LeaderPhase6ShareBasisPreparationError(
            missing_symbols=tuple(sorted(
                set(history_symbols) - set(quote_by_symbol)
            )),
        )
    total_shares = {}
    invalid_share_symbols = []
    for symbol in history_symbols:
        row = quote_by_symbol[symbol]
        if (
            row.market_cap_unit_status != UnitVerificationStatus.VERIFIED
            or isinstance(row.total_shares_source, bool)
            or not isinstance(row.total_shares_source, (int, float))
            or not math.isfinite(float(row.total_shares_source))
            or float(row.total_shares_source) <= 0
        ):
            invalid_share_symbols.append(symbol)
            continue
        total_shares[symbol] = float(row.total_shares_source)
    if invalid_share_symbols:
        raise LeaderPhase6ShareBasisPreparationError(
            invalid_symbols=tuple(invalid_share_symbols),
        )
    verified_history_non_trading = {
        symbol: series.verified_non_trading_dates
        for symbol, series in history_batch.series_by_symbol.items()
        if symbol in history_symbols
        and series.verified_non_trading_dates
    }
    sector_symbols = {
        symbol
        for symbols in sector_memberships.values()
        for symbol in symbols
    }
    sector_request = SectorHistoryAutomaticBackfillRequest(
        radar_run_id=radar_run_id,
        as_of=prepared_started_at,
        comparable_time=effective_sector_comparable_time,
        expected_trade_dates=expected_dates,
        classification_release=classification_snapshot.release,
        memberships_by_division=sector_memberships,
        total_shares_by_symbol={
            symbol: value
            for symbol, value in total_shares.items()
            if symbol in sector_symbols
        },
        source_batch_ids=(share_quote_batch.meta.batch_id,),
        terminal_non_trading_symbols=tuple(sorted(
            {
                symbol for symbol, row in quote_by_symbol.items()
                if row.is_explicitly_non_trading
            }
            | {
                symbol for symbol, values
                in verified_history_non_trading.items()
                if expected_dates[-1] in values
            }
        )),
        verified_non_trading_dates_by_symbol=(
            verified_history_non_trading
        ),
    )
    sector_result = sector_backfill_runner(
        sector_request,
        artifact_dir=sector_checkpoint_root,
        reuse_store_dir=(
            Path(reuse_store_dir).expanduser().resolve()
            if reuse_store_dir is not None else None
        ),
        clock=clock,
    )
    backfill = getattr(sector_result, "backfill_result", None)
    sector_history = getattr(backfill, "history_evidence", None)
    sector_analyses = getattr(backfill, "sector_analyses", None)
    sector_replay_query = getattr(sector_result, "replay_query", None)
    if (
        getattr(sector_result, "status", None) != "ready"
        or type(sector_history) is not SectorHistoryCoverageEvidence
        or not isinstance(sector_analyses, tuple)
        or not sector_analyses
        or any(
            type(item) is not SectorHistoricalAnalysis
            or item.comparable_time != effective_sector_comparable_time
            for item in sector_analyses
        )
        or type(sector_replay_query) is not SectorHistoryBackfillQuery
        or sector_history.radar_run_id != radar_run_id
        or sector_history.classification_document_sha256
        != classification_snapshot.release.document_sha256
    ):
        raise ValueError("leader_phase6_public_prepare_sector_unverified")
    approval = threshold_approval_loader()
    if (
        type(approval) is not SectorThresholdApprovalLoadResult
        or approval.status != "approved"
        or type(approval.evidence) is not SectorThresholdApprovalEvidence
        or type(approval.record) is not SectorThresholdApprovalRecord
    ):
        raise ValueError("leader_phase6_public_prepare_approval_unverified")
    completed_at = max(
        prepared_started_at,
        clock(),
        calendar_document.fetched_at,
        sector_history.as_of,
        *(
            series.fetched_at
            for series in history_batch.series_by_symbol.values()
        ),
    )
    calendar_evidence = PublicTradingCalendarEvidence(
        exchange="sse",
        trading_dates=expected_dates,
        source_contract_id="sse-a-share-trading-calendar-v1",
        source_name="上海证券交易所",
        source_url=calendar_document.source_url,
        document_id=calendar_document.document_id,
        source_time=calendar_document.source_time,
        fetched_at=calendar_document.fetched_at,
        content_sha256=(
            "sha256:" + hashlib.sha256(
                calendar_document.text.encode("utf-8")
            ).hexdigest()
        ),
    )
    return LeaderPhase6PreparedHistoricalInputs(
        radar_run_id=radar_run_id,
        classification_document_sha256=(
            classification_snapshot.release.document_sha256
        ),
        membership_symbols_by_industry=membership_symbols,
        expected_trade_dates=expected_dates,
        series_by_symbol=history_batch.series_by_symbol,
        calendar_evidence=calendar_evidence,
        sector_history_rows=sector_history.rows,
        sector_historical_analyses=sector_analyses,
        sector_comparable_time=effective_sector_comparable_time,
        sector_history_replay_query=sector_replay_query,
        sector_history_source_as_of=sector_history.as_of,
        threshold_approval_evidence=approval.evidence,
        threshold_approval_record=approval.record,
        prepared_at=completed_at,
    )


def validate_leader_phase6_plan_prefreeze_evidence(
    runtime: Any,
    evidence: Any,
    *,
    provisional_as_of: datetime,
) -> Tuple[str, ...]:
    """验证预冻结证据在最终候选计划之前已真实完成。"""

    if (
        type(runtime) is not LeaderLiveCandidateRuntimeInputs
        or type(evidence) is not LeaderPhase6PlanPrefreezeEvidence
        or evidence.contract_id
        != LEADER_PHASE6_PLAN_PREFREEZE_EVIDENCE_CONTRACT_ID
        or not _aware(provisional_as_of)
        or not _aware(evidence.completed_at)
    ):
        return ("leader_phase6_plan_prefreeze_contract_unverified",)
    plan = runtime.candidate_plan
    history = evidence.history
    sector_history = evidence.sector_history_evidence
    baseline = evidence.market_baseline_evidence
    history_fetched_times = (
        _history_fetched_times(history)
        if type(history) is LeaderHistoryProductionFrozenBatch
        else ()
    )
    if (
        type(history) is not LeaderHistoryProductionFrozenBatch
        or history.source_status != "ready"
        or history.failure_count != 0
        or not history_fetched_times
        or type(sector_history) is not SectorHistoryCoverageEvidence
        or type(baseline) is not SectorMarketBaselineEvidence
        or type(evidence.threshold_approval_evidence)
        is not SectorThresholdApprovalEvidence
        or not isinstance(evidence.sector_historical_analyses, tuple)
        or not evidence.sector_historical_analyses
        or type(evidence.sector_comparable_time) is not time
        or any(
            type(item) is not SectorHistoricalAnalysis
            or item.comparable_time != evidence.sector_comparable_time
            for item in evidence.sector_historical_analyses
        )
        or type(evidence.threshold_approval_record)
        is not SectorThresholdApprovalRecord
        or not is_sector_threshold_approval_record_valid(
            evidence.threshold_approval_record
        )
    ):
        return ("leader_phase6_plan_prefreeze_sources_unverified",)
    if any((
        sector_history.radar_run_id != plan.radar_run_id,
        baseline.radar_run_id != plan.radar_run_id,
        sector_history.as_of != evidence.completed_at,
        baseline.as_of != evidence.completed_at,
        plan.quote_batch_id not in baseline.source_batch_ids,
        sector_history.classification_document_sha256
        != runtime.industry_release.document_sha256,
        evidence.completed_at < provisional_as_of,
        max(history_fetched_times) > evidence.completed_at,
        runtime.industry_release.fetched_at > evidence.completed_at,
    )):
        return ("leader_phase6_plan_prefreeze_identity_unverified",)
    approval = evidence.threshold_approval_evidence
    record = evidence.threshold_approval_record
    if (
        approval.approved_at > evidence.completed_at
        or record.rule_version != approval.rule_version
        or record.threshold_set_id != approval.threshold_set_id
        or record.approval_id != approval.approval_id
        or record.approved_at != approval.approved_at
    ):
        return ("leader_phase6_plan_prefreeze_approval_unverified",)
    return ()


def bind_leader_phase6_prefrozen_inputs(
    runtime: Any,
    evidence: Any,
    *,
    threshold_approval_binder: Any = bind_latest_sector_threshold_approval,
) -> LeaderPhase6PrefrozenInputs:
    """绑到最终计划并重放现有两个生产合同。"""

    context = getattr(runtime, "source_context", None)
    if (
        not is_leader_research_runtime_source_context_valid(context)
        or type(runtime) is not LeaderLiveCandidateRuntimeInputs
        or type(evidence) is not LeaderPhase6PlanPrefreezeEvidence
        or not callable(threshold_approval_binder)
    ):
        raise ValueError("leader_phase6_plan_prefreeze_binding_unverified")
    reasons = validate_leader_phase6_plan_prefreeze_evidence(
        runtime,
        evidence,
        provisional_as_of=evidence.completed_at,
    )
    if (
        reasons
        or context.as_of != evidence.completed_at
        or runtime.as_of != evidence.completed_at
    ):
        raise ValueError("leader_phase6_plan_prefreeze_binding_unverified")
    plan = context.candidate_plan
    release_ids = {item.industry_release_id for item in plan.items}
    if len(release_ids) != 1:
        raise ValueError("leader_phase6_plan_prefreeze_binding_unverified")
    feature = runtime.sector_feature_batch
    if any((
        feature.radar_run_id != plan.radar_run_id,
        feature.quote_batch_id != plan.quote_batch_id,
        feature.as_of != plan.as_of,
        feature.classification_document_sha256
        != runtime.industry_release.document_sha256,
    )):
        raise ValueError("leader_phase6_plan_prefreeze_binding_unverified")
    source_batch = SectorRuleRuntimeSourceBatch(
        candidate_plan_id=plan.candidate_set_id,
        radar_run_id=plan.radar_run_id,
        quote_batch_id=plan.quote_batch_id,
        industry_release_id=next(iter(release_ids)),
        as_of=plan.as_of,
        feature_batch=feature,
        classification_release=runtime.industry_release.model_copy(deep=True),
        history_evidence=evidence.sector_history_evidence,
        market_baseline_evidence=evidence.market_baseline_evidence,
        threshold_approval_evidence=evidence.threshold_approval_evidence,
    )
    sector_frozen = SectorRuleProductionFrozenBatch(
        source_batch=source_batch,
        fetched_at=evidence.completed_at,
        source_status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
    )
    history_collected = collect_leader_history_production_source(
        context,
        evidence.history,
    )

    def bind_exact_approval(batch: SectorRuleRuntimeSourceBatch):
        loaded = threshold_approval_binder(batch)
        bound_batch = getattr(loaded, "source_batch", None)
        if (
            type(loaded) is SectorThresholdApprovalLoadResult
            and loaded.status == "approved"
            and type(bound_batch) is SectorRuleRuntimeSourceBatch
            and bound_batch.threshold_approval_evidence
            == evidence.threshold_approval_evidence
        ):
            return loaded
        return SectorThresholdApprovalLoadResult(
            status="not_ready",
            reasons=("sector_threshold_approval_prefreeze_mismatch",),
        )

    sector_collected = collect_sector_rule_production_source(
        context,
        sector_frozen,
        threshold_approval_binder=bind_exact_approval,
    )
    if (
        type(history_collected)
        is not LeaderFormalResearchProductionCollectedSource
        or type(sector_collected)
        is not LeaderFormalResearchProductionCollectedSource
    ):
        raise ValueError("leader_phase6_plan_prefreeze_binding_unverified")
    if any((
        history_collected.status
        != LeaderFormalResearchProductionSourceStatus.COMPLETED,
        sector_collected.status
        != LeaderFormalResearchProductionSourceStatus.COMPLETED,
        type(sector_collected.payload) is not SectorRuleRuntimeSourceBatch,
    )):
        raise ValueError("leader_phase6_plan_prefreeze_binding_unverified")
    return LeaderPhase6PrefrozenInputs(
        history=evidence.history,
        sector_rule=SectorRuleProductionFrozenBatch(
            source_batch=sector_collected.payload,
            fetched_at=evidence.completed_at,
            source_status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
        ),
        sector_historical_analyses=evidence.sector_historical_analyses,
        sector_comparable_time=evidence.sector_comparable_time,
        threshold_approval_record=evidence.threshold_approval_record,
    )
