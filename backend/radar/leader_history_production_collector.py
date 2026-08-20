"""候选全集历史连续性的生产只读输入冻结与准入。

调用方必须先显式冻结官方交易日、时点行业成员和所有证券/指数历史序列，
本模块只做身份、全集、时间和现有历史 POC 重放。不联网、不读写数据库，
也不会生成正式分数或龙头状态。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
import re
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_history_features import HistoryAdjustmentBasis
from radar.leader_research_source_admission import (
    LeaderResearchHistoryAdmissionEntry,
)
from radar.leader_runtime_candidate_plan import (
    is_leader_runtime_candidate_plan_valid,
)
from radar.sources.leader_history_public_poc import (
    PointInTimeIndustryMembership,
    PublicHistoryPocQuery,
    PublicHistorySeries,
    REQUIRED_HISTORY_DATES,
    TENCENT_HISTORY_URL,
    parse_tencent_history_payload,
    run_public_history_input_poc,
)
from radar.sources.leader_tradability_public_poc import (
    PublicTradingCalendarEvidence,
)
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    is_leader_research_runtime_source_context_valid,
)


LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID = (
    "radar-leader-history-production-collector-v1"
)
LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID = (
    "radar-leader-history-production-source-v1"
)
MAXIMUM_PRODUCTION_INDUSTRY_MEMBER_COUNT = 6000
MAXIMUM_FUTURE_SKEW_SECONDS = 5
HISTORY_REQUEST_TIMEOUT_SECONDS = 8
HISTORY_FETCH_WORKERS = 8
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_SYMBOL_PATTERN = re.compile(r"^[036][0-9]{5}$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, repr=False)
class LeaderHistoryProductionFrozenBatch:
    """历史来源在候选计划前冻结后的纯内存批次。"""

    expected_trade_dates: Tuple[date, ...]
    memberships_by_industry: Mapping[str, PointInTimeIndustryMembership] = field(
        repr=False
    )
    series_by_symbol: Mapping[str, PublicHistorySeries] = field(repr=False)
    calendar_evidence: Optional[PublicTradingCalendarEvidence] = field(
        default=None,
        repr=False,
    )
    contract_id: str = LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID
    source_status: str = "ready"
    failure_count: int = 0

    def to_evidence(self) -> Mapping[str, object]:
        return {
            "contractId": self.contract_id,
            "expectedTradeDateCount": len(self.expected_trade_dates),
            "industryCount": len(self.memberships_by_industry),
            "fetchedSeriesCount": len(self.series_by_symbol),
            "returnedCount": len(self.series_by_symbol),
            "calendarEvidenceReady": self.calendar_evidence is not None,
            "sourceStatus": self.source_status,
            "failureCount": self.failure_count,
        }


def _unverified() -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name="history",
        source_contract_id=LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        status=LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        source_time=None,
        fetched_at=None,
        symbols=(),
        payload=None,
    )


def _source_failed() -> LeaderFormalResearchProductionCollectedSource:
    return LeaderFormalResearchProductionCollectedSource(
        component_name="history",
        source_contract_id=LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        status=LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
        source_time=None,
        fetched_at=None,
        symbols=(),
        payload=None,
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _board_index(symbol: str) -> str:
    return "sh000001" if symbol.startswith("6") else "sz399001"


def _query_symbol(symbol: str) -> str:
    if symbol in {"sh000001", "sz399001"}:
        return symbol
    if re.fullmatch(r"[036][0-9]{5}", symbol):
        return ("sh" if symbol.startswith("6") else "sz") + symbol
    raise ValueError("history_symbol_invalid")


def _default_history_requester(query_symbol: str) -> Any:
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            TENCENT_HISTORY_URL,
            params={"param": f"{query_symbol},day,,,40,qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HISTORY_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()


def fetch_leader_history_series_batch(
    symbols: Tuple[str, ...],
    expected_trade_dates: Tuple[date, ...],
    *,
    requester: Any = None,
    clock: Any = None,
    max_workers: int = HISTORY_FETCH_WORKERS,
) -> LeaderHistoryProductionFrozenBatch:
    """按唯一证券/指数代码批量抓取腾讯日线；任一失败都关闭整批。"""

    if (
        not isinstance(symbols, tuple)
        or not symbols
        or not isinstance(expected_trade_dates, tuple)
        or len(expected_trade_dates) != REQUIRED_HISTORY_DATES
        or any(not isinstance(day, date) for day in expected_trade_dates)
        or expected_trade_dates != tuple(sorted(expected_trade_dates))
        or len(expected_trade_dates) != len(set(expected_trade_dates))
        or not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or max_workers < 1
        or max_workers > HISTORY_FETCH_WORKERS
    ):
        return LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=expected_trade_dates
            if isinstance(expected_trade_dates, tuple)
            else (),
            memberships_by_industry={},
            series_by_symbol={},
            source_status="source_unverified",
        )
    unique_symbols = tuple(dict.fromkeys(symbols))
    try:
        query_symbols = {
            symbol: _query_symbol(symbol) for symbol in unique_symbols
        }
    except (TypeError, ValueError):
        return LeaderHistoryProductionFrozenBatch(
            expected_trade_dates=expected_trade_dates,
            memberships_by_industry={},
            series_by_symbol={},
            source_status="source_unverified",
        )
    request = requester or _default_history_requester
    now = clock or (lambda: datetime.now(SHANGHAI_TZ))
    results = {}
    failures = []

    def load(symbol: str):
        payload = request(query_symbols[symbol])
        board = symbol in {"sh000001", "sz399001"}
        fetched_at = now()
        if not _aware(fetched_at):
            raise ValueError("history_fetched_at_timezone_missing")
        return symbol, parse_tencent_history_payload(
            symbol=symbol,
            query_symbol=query_symbols[symbol],
            payload=payload,
            expected_trade_dates=expected_trade_dates,
            fetched_at=fetched_at,
            adjustment_basis=(
                HistoryAdjustmentBasis.CONTINUOUS_INDEX
                if board
                else HistoryAdjustmentBasis.FORWARD_ADJUSTED
            ),
        )

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(unique_symbols))
    ) as pool:
        futures = {pool.submit(load, symbol): symbol for symbol in unique_symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                key, series = future.result()
            except Exception:
                failures.append(symbol)
            else:
                results[key] = series
    return LeaderHistoryProductionFrozenBatch(
        expected_trade_dates=expected_trade_dates,
        memberships_by_industry={},
        series_by_symbol=results if not failures else {},
        source_status="source_failed" if failures else "ready",
        failure_count=len(failures),
    )


def _expected_members(
    context: LeaderResearchRuntimeSourceContext,
    industry_code: str,
) -> Optional[Tuple[Tuple[str, ...], int]]:
    values = context.industry_constituent_symbols_by_code.get(industry_code)
    if not isinstance(values, tuple) or not values:
        return None
    if len(values) != len(set(values)) or any(
        not isinstance(symbol, str)
        or re.fullmatch(r"\d{6}", symbol) is None
        for symbol in values
    ):
        return None
    members = tuple(
        symbol for symbol in values
        if _SYMBOL_PATTERN.fullmatch(symbol) is not None
    )
    if not members:
        return None
    return members, len(values) - len(members)


def _valid_batch(
    batch: Any,
    context: LeaderResearchRuntimeSourceContext,
) -> bool:
    if (
        type(batch) is not LeaderHistoryProductionFrozenBatch
        or batch.contract_id != LEADER_HISTORY_PRODUCTION_COLLECTOR_CONTRACT_ID
        or batch.source_status != "ready"
        or not isinstance(batch.failure_count, int)
        or isinstance(batch.failure_count, bool)
        or batch.failure_count != 0
        or not isinstance(batch.expected_trade_dates, tuple)
        or len(batch.expected_trade_dates) != REQUIRED_HISTORY_DATES
        or any(not isinstance(day, date) for day in batch.expected_trade_dates)
        or not isinstance(batch.memberships_by_industry, Mapping)
        or not isinstance(batch.series_by_symbol, Mapping)
    ):
        return False
    if (
        batch.expected_trade_dates != tuple(sorted(batch.expected_trade_dates))
        or len(batch.expected_trade_dates) != len(set(batch.expected_trade_dates))
        or batch.expected_trade_dates[-1] > context.as_of.date()
    ):
        return False
    calendar = batch.calendar_evidence
    if (
        type(calendar) is not PublicTradingCalendarEvidence
        or calendar.exchange != "sse"
        or calendar.trading_dates != batch.expected_trade_dates
        or not all(isinstance(value, str) for value in (
            calendar.source_contract_id,
            calendar.source_name,
            calendar.source_url,
            calendar.document_id,
            calendar.content_sha256,
        ))
        or calendar.source_contract_id not in {
            "sse-public-trading-calendar-v1",
            "sse-a-share-trading-calendar-v1",
        }
        or calendar.source_name != "上海证券交易所"
        or urlparse(calendar.source_url).hostname not in {
            "www.sse.com.cn",
            "sse.com.cn",
        }
        or not calendar.document_id.strip()
        or _SHA256_PATTERN.fullmatch(calendar.content_sha256) is None
        or not _aware(calendar.source_time)
        or not _aware(calendar.fetched_at)
        or calendar.fetched_at < calendar.source_time
        or calendar.fetched_at > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return False
    industries = tuple(dict.fromkeys(
        item.industry_code for item in context.candidate_plan.items
    ))
    if any(
        industry not in batch.memberships_by_industry
        for industry in industries
    ):
        return False
    expected_series = set()
    for item in context.candidate_plan.items:
        member_scope = _expected_members(context, item.industry_code)
        membership = batch.memberships_by_industry.get(item.industry_code)
        if (
            member_scope is None
            or type(membership) is not PointInTimeIndustryMembership
            or not isinstance(membership.member_symbols, tuple)
            or membership.release_id != item.industry_release_id
            or membership.industry_code != item.industry_code
            or not isinstance(membership.source_contract_id, str)
            or not isinstance(membership.release_id, str)
            or not membership.source_contract_id.strip()
            or not membership.release_id.strip()
            or not _aware(membership.first_observed_at)
            or not _aware(membership.fetched_at)
            or membership.fetched_at > context.as_of + timedelta(
                seconds=MAXIMUM_FUTURE_SKEW_SECONDS
            )
            or membership.first_observed_at > membership.fetched_at
            or not _SYMBOL_PATTERN.fullmatch(item.symbol)
        ):
            return False
        members, excluded_count = member_scope
        if (
            tuple(membership.member_symbols) != members
            or membership.excluded_out_of_scope_count != excluded_count
        ):
            return False
        expected_series.update(members)
        expected_series.add(_board_index(item.symbol))
    if any(
        not isinstance(symbol, str)
        or not isinstance(series, PublicHistorySeries)
        or series.symbol != symbol
        or series.source_url != TENCENT_HISTORY_URL
        for symbol, series in batch.series_by_symbol.items()
    ):
        return False
    return expected_series.issubset(set(batch.series_by_symbol))


def collect_leader_history_production_source(
    context: Any,
    batch: Any,
) -> LeaderFormalResearchProductionCollectedSource:
    """把候选全集历史冻结批次重放为正式研究 history 来源。"""

    if (
        not is_leader_research_runtime_source_context_valid(context)
        or not is_leader_runtime_candidate_plan_valid(context.candidate_plan)
    ):
        return _unverified()
    if (
        type(batch) is LeaderHistoryProductionFrozenBatch
        and batch.source_status == "source_failed"
    ):
        return _source_failed()
    if not _valid_batch(batch, context):
        return _unverified()

    plan = context.candidate_plan
    entries = []
    source_times = [batch.calendar_evidence.source_time]
    fetched_times = [batch.calendar_evidence.fetched_at]
    for item in plan.items:
        member_scope = _expected_members(context, item.industry_code)
        if member_scope is None:
            return _unverified()
        members, _ = member_scope
        membership = batch.memberships_by_industry[item.industry_code]
        fetched_times.append(membership.fetched_at)
        candidate_membership = replace(
            membership,
            candidate_symbol=item.symbol,
        )
        query = PublicHistoryPocQuery(
            as_of=context.as_of,
            expected_trade_dates=batch.expected_trade_dates,
            candidate_symbol=item.symbol,
            board_index_symbol=_board_index(item.symbol),
            membership=candidate_membership,
            series_by_symbol=batch.series_by_symbol,
            maximum_member_count=MAXIMUM_PRODUCTION_INDUSTRY_MEMBER_COUNT,
        )
        try:
            result = run_public_history_input_poc(query)
        except Exception:
            return _unverified()
        if result.resolution_status != "ready":
            return _unverified()
        entries.append(LeaderResearchHistoryAdmissionEntry(
            symbol=item.symbol,
            candidate_plan_id=plan.candidate_set_id,
            radar_run_id=plan.radar_run_id,
            quote_batch_id=plan.quote_batch_id,
            query=query,
            result=result,
        ))
        source_times.append(datetime.combine(
            batch.expected_trade_dates[-1],
            time(15, 0),
            tzinfo=SHANGHAI_TZ,
        ))
        fetched_times.extend(
            series.fetched_at
            for symbol, series in batch.series_by_symbol.items()
            if symbol in set(members or ()) | {_board_index(item.symbol)}
        )

    if len(entries) != plan.candidate_count or not fetched_times:
        return _unverified()
    source_time = max(source_times)
    fetched_at = max(fetched_times)
    if (
        not _aware(source_time)
        or not _aware(fetched_at)
        or fetched_at < source_time
        or source_time > context.as_of + timedelta(
            seconds=MAXIMUM_FUTURE_SKEW_SECONDS
        )
    ):
        return _unverified()
    return LeaderFormalResearchProductionCollectedSource(
        component_name="history",
        source_contract_id=LEADER_HISTORY_PRODUCTION_SOURCE_CONTRACT_ID,
        status=LeaderFormalResearchProductionSourceStatus.COMPLETED,
        source_time=source_time,
        fetched_at=fetched_at,
        symbols=tuple(item.symbol for item in plan.items),
        payload=tuple(entries),
    )


def build_leader_history_production_loader(
    batch: LeaderHistoryProductionFrozenBatch,
):
    """为现有四源生产 provider 构造一个无网络 history loader。"""

    if type(batch) is not LeaderHistoryProductionFrozenBatch:
        raise ValueError("leader_history_production_batch_unverified")

    def load(context: LeaderResearchRuntimeSourceContext):
        return collect_leader_history_production_source(context, batch)

    return load
