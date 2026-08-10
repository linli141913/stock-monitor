"""阶段6L-C4C-2免费公开源真实POC隔离组装器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from market_calendar import parse_sse_calendar
from radar.contracts import QuoteSnapshot, SecurityMasterRecord, SourceBatch
from radar.leader_tradability_features import (
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositeTradabilityQuery,
    PublicCompositeTradabilityReport,
    PublicQuoteBatchEvidence,
    PublicSecurityContext,
    PublicSourceKind,
    STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN,
    PublicTradabilityObservation,
    PublicTradingCalendarEvidence,
    quote_batch_content_sha256,
    run_public_composite_tradability_poc,
)
from radar.sources.security_master import (
    SecurityMasterProviders,
    fetch_security_master,
)
from radar.sources.tencent_quotes import fetch_tencent_quotes


PUBLIC_LIVE_POC_CONTRACT_ID = (
    "radar-leader-tradability-public-live-poc-v1"
)
SSE_A_SHARE_CALENDAR_CONTRACT_ID = (
    "sse-a-share-trading-calendar-v1"
)
SSE_CALENDAR_URL = (
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
)
AKSHARE_STATUS_URL = (
    "https://akshare.akfamily.xyz/data/stock/stock.html"
)
EASTMONEY_ST_URL = (
    "https://quote.eastmoney.com/center/gridlist.html"
)
EASTMONEY_ST_API_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
)
EASTMONEY_SUSPENSION_URL = "https://data.eastmoney.com/tfpxx/"
TENCENT_SOURCE_URL = "https://qt.gtimg.cn/"
MAXIMUM_SAMPLE_COUNT = 8
MAXIMUM_COLLECTION_SECONDS = 40
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class PublicLivePocSourceError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        private_detail: Optional[str] = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.private_detail = private_detail


@dataclass(frozen=True, repr=False)
class PublicCalendarDocument:
    source_url: str
    document_id: str
    text: str
    source_time: Optional[datetime]
    fetched_at: datetime

    def __repr__(self) -> str:
        return (
            "PublicCalendarDocument("
            f"document_id={self.document_id!r})"
        )


@dataclass(frozen=True)
class PublicLivePocSourceBundle:
    query: PublicCompositeTradabilityQuery
    quotes: Tuple[QuoteSnapshot, ...] = ()
    official_observations: Tuple[
        PublicTradabilityObservation, ...
    ] = ()
    aggregator_observations: Tuple[
        PublicTradabilityObservation, ...
    ] = ()
    requested_symbols: Tuple[str, ...] = ()
    source_statuses: Mapping[str, str] = field(default_factory=dict)
    source_failures: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PublicLivePocExecution:
    execution_status: str
    real_poc_status: str
    reason: Optional[str]
    report: Optional[PublicCompositeTradabilityReport] = None
    source_statuses: Mapping[str, str] = field(default_factory=dict)
    source_failures: Tuple[str, ...] = ()
    elapsed_ms: Optional[int] = None
    as_of: Optional[datetime] = None
    trading_date: Optional[date] = None

    def to_evidence(self) -> dict:
        return {
            "contractId": PUBLIC_LIVE_POC_CONTRACT_ID,
            "executionStatus": self.execution_status,
            "realPocStatus": self.real_poc_status,
            "reason": self.reason,
            "resolutionStatus": (
                self.report.fixture_resolution_status.value
                if self.report is not None
                else None
            ),
            "expectedCount": (
                self.report.expected_count
                if self.report is not None
                else None
            ),
            "returnedCount": (
                self.report.returned_count
                if self.report is not None
                else None
            ),
            "fieldCoverage": (
                dict(self.report.field_coverage)
                if self.report is not None
                else {}
            ),
            "reasons": (
                list(self.report.reasons)
                if self.report is not None
                else []
            ),
            "sourceStatuses": dict(self.source_statuses),
            "sourceFailures": list(self.source_failures),
            "elapsedMs": self.elapsed_ms,
            "asOf": self.as_of.isoformat() if self.as_of is not None else None,
            "tradingDate": (
                self.trading_date.isoformat()
                if self.trading_date is not None
                else None
            ),
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        }


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def validate_public_live_symbols(
    values: Iterable[str],
) -> Optional[Tuple[str, ...]]:
    symbols = tuple(str(value or "").strip() for value in values)
    if not 1 <= len(symbols) <= MAXIMUM_SAMPLE_COUNT:
        return None
    if any(
        STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(symbol)
        is None
        for symbol in symbols
    ):
        return None
    if len(symbols) != len(set(symbols)):
        return None
    return symbols


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(payload)


def build_public_calendar_evidence(
    *,
    document: PublicCalendarDocument,
    trading_date: date,
    exchanges: Iterable[str],
) -> Tuple[PublicTradingCalendarEvidence, ...]:
    normalized_exchanges = tuple(dict.fromkeys(
        str(value or "").strip().lower()
        for value in exchanges
    ))
    if (
        not normalized_exchanges
        or any(value not in {"sse", "szse"} for value in normalized_exchanges)
        or not isinstance(trading_date, date)
        or isinstance(trading_date, datetime)
        or not _aware(document.source_time)
        or not _aware(document.fetched_at)
    ):
        raise PublicLivePocSourceError(
            "public_live_calendar_contract_invalid"
        )
    try:
        snapshot = parse_sse_calendar(document.text, trading_date.year)
    except Exception as exc:
        raise PublicLivePocSourceError(
            "public_live_calendar_parse_failed",
            private_detail=str(exc),
        ) from exc

    current = trading_date
    trading_dates = []
    for _ in range(40):
        current_text = current.isoformat()
        if (
            current.weekday() < 5
            and current_text not in snapshot.closed_days
        ):
            trading_dates.append(current)
            if len(trading_dates) == 6:
                break
        current -= timedelta(days=1)
    if len(trading_dates) != 6 or trading_dates[0] != trading_date:
        raise PublicLivePocSourceError(
            "public_live_calendar_window_unavailable"
        )
    ordered_dates = tuple(reversed(trading_dates))
    content_sha256 = _sha256_bytes(document.text.encode("utf-8"))
    return tuple(
        PublicTradingCalendarEvidence(
            exchange=exchange,
            trading_dates=ordered_dates,
            source_contract_id=SSE_A_SHARE_CALENDAR_CONTRACT_ID,
            source_name="上海证券交易所",
            source_url=document.source_url,
            document_id=document.document_id,
            source_time=document.source_time,
            fetched_at=document.fetched_at,
            content_sha256=content_sha256,
        )
        for exchange in normalized_exchanges
    )


def build_public_security_contexts(
    *,
    batch: SourceBatch,
    symbols: Tuple[str, ...],
) -> Tuple[PublicSecurityContext, ...]:
    if (
        not isinstance(batch, SourceBatch)
        or batch.meta.source != "official_exchange_security_master"
    ):
        raise PublicLivePocSourceError(
            "public_live_security_master_contract_unverified"
        )
    selected_items = tuple(
        item for item in batch.items if item.symbol in symbols
    )
    by_symbol = {
        item.symbol: item
        for item in selected_items
    }
    if (
        len(selected_items) != len(symbols)
        or set(by_symbol) != set(symbols)
    ):
        raise PublicLivePocSourceError(
            "public_live_security_master_incomplete"
        )
    result = []
    for symbol in symbols:
        item = by_symbol[symbol]
        exchange = str(item.exchange or "").strip().lower()
        if (
            not isinstance(item, SecurityMasterRecord)
            or item.source != exchange
        ):
            raise PublicLivePocSourceError(
                "public_live_security_master_contract_unverified"
            )
        if exchange == "sse":
            contract_id = "sse-public-security-list-v1"
            source_name = "上海证券交易所"
            source_url = (
                "https://query.sse.com.cn/sseQuery/commonQuery.do"
            )
        elif exchange == "szse":
            contract_id = "szse-public-security-list-v1"
            source_name = "深圳证券交易所"
            source_url = "https://www.szse.cn/api/report/ShowReport"
        else:
            raise PublicLivePocSourceError(
                "public_live_security_exchange_unsupported"
            )
        symbol_field = "证券代码" if exchange == "sse" else "A股代码"
        source_symbol = str(
            item.source_fields.get(symbol_field) or ""
        ).strip().zfill(6)
        if source_symbol != symbol:
            raise PublicLivePocSourceError(
                "public_live_security_master_contract_unverified"
            )
        if item.listing_date is None or not _aware(item.fetched_at):
            raise PublicLivePocSourceError(
                "public_live_security_identity_incomplete"
            )
        result.append(PublicSecurityContext(
            symbol=item.symbol,
            exchange=exchange,
            board=item.board,
            listing_date=item.listing_date,
            identity_source_contract_id=contract_id,
            identity_source_name=source_name,
            identity_source_url=source_url,
            identity_document_id=(
                f"{exchange}-security-list-capture-"
                f"{item.fetched_at.astimezone(SHANGHAI_TZ):%Y%m%d}"
            ),
            identity_source_time=batch.meta.source_time,
            identity_fetched_at=item.fetched_at,
            identity_content_sha256=_canonical_sha256({
                "symbol": item.symbol,
                "exchange": exchange,
                "board": item.board,
                "listingDate": item.listing_date.isoformat(),
                "sourceFields": dict(item.source_fields),
            }),
        ))
    return tuple(result)


def build_public_quote_evidence(
    batch: SourceBatch,
) -> Tuple[Tuple[QuoteSnapshot, ...], PublicQuoteBatchEvidence]:
    quotes = tuple(batch.items)
    batch_id = str(batch.meta.batch_id or "").strip()
    if not batch_id:
        raise PublicLivePocSourceError(
            "public_live_quote_batch_identity_missing"
        )
    return quotes, PublicQuoteBatchEvidence(
        source_contract_id="tencent-quote-snapshot-v1",
        source_name="腾讯财经",
        source_url=TENCENT_SOURCE_URL,
        batch_id=batch_id,
        content_sha256=quote_batch_content_sha256(
            batch_id=batch_id,
            quotes=quotes,
        ),
    )


def _optional_date(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _row_values(row: Any) -> dict:
    result = {}
    for key, value in row.items():
        if value is None or pd.isna(value):
            result[str(key)] = None
        elif isinstance(value, (date, datetime, pd.Timestamp)):
            result[str(key)] = value.isoformat()
        elif hasattr(value, "item"):
            result[str(key)] = value.item()
        else:
            result[str(key)] = value
    return result


def _fetch_eastmoney_st_frame(
    *,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    active_session = session or requests.Session()
    base_params = {
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0 f:4,m:1 f:4",
        "fields": "f12,f14,f124",
    }
    rows = []
    expected_total = None
    page_number = 1
    try:
        while True:
            response = active_session.get(
                EASTMONEY_ST_API_URL,
                params={**base_params, "pn": str(page_number)},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            page_rows = data.get("diff") if isinstance(data, dict) else None
            total = data.get("total") if isinstance(data, dict) else None
            if (
                payload.get("rc") != 0
                or not isinstance(total, int)
                or not isinstance(page_rows, list)
            ):
                raise ValueError("eastmoney_st_contract_invalid")
            if expected_total is None:
                expected_total = total
                if expected_total > 1000:
                    raise ValueError("eastmoney_st_total_exceeds_limit")
            elif total != expected_total:
                raise ValueError("eastmoney_st_total_changed")
            rows.extend(page_rows)
            if len(rows) >= expected_total:
                break
            page_number += 1
            if page_number > 10:
                raise ValueError("eastmoney_st_page_limit_exceeded")
    except Exception as exc:
        raise PublicLivePocSourceError(
            "public_live_st_request_failed",
            private_detail=type(exc).__name__,
        ) from exc

    normalized = []
    seen = set()
    for row in rows:
        symbol = str(row.get("f12") or "").strip().zfill(6)
        name = str(row.get("f14") or "").strip()
        try:
            upstream_time = datetime.fromtimestamp(
                int(row.get("f124")),
                tz=SHANGHAI_TZ,
            )
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if (
            symbol in seen
            or STAGE6_SHENZHEN_SHANGHAI_SYMBOL_PATTERN.fullmatch(
                symbol
            ) is None
            or not name
        ):
            continue
        seen.add(symbol)
        normalized.append({
            "代码": symbol,
            "名称": name,
            "上游时间": upstream_time,
        })
    if expected_total and not normalized:
        raise PublicLivePocSourceError(
            "public_live_st_contract_invalid"
        )
    return pd.DataFrame(
        normalized,
        columns=("代码", "名称", "上游时间"),
    )


def build_public_aggregator_observations(
    *,
    contexts: Tuple[PublicSecurityContext, ...],
    trading_date: date,
    fetched_at: datetime,
    st_frame: Optional[pd.DataFrame],
    suspension_frame: Optional[pd.DataFrame],
    source_time: Optional[datetime] = None,
    upstream_source_time: Optional[datetime] = None,
) -> Tuple[PublicTradabilityObservation, ...]:
    if not _aware(fetched_at):
        return ()
    if source_time is not None and (
        not _aware(source_time) or source_time > fetched_at
    ):
        return ()
    if upstream_source_time is not None and (
        not _aware(upstream_source_time)
        or source_time is None
        or upstream_source_time > source_time
    ):
        return ()
    st_rows = {}
    if st_frame is not None:
        for _, row in st_frame.iterrows():
            symbol = str(row.get("代码") or "").strip().zfill(6)
            if symbol in {item.symbol for item in contexts}:
                st_rows[symbol] = row
    suspension_rows = {}
    if suspension_frame is not None:
        for _, row in suspension_frame.iterrows():
            symbol = str(row.get("代码") or "").strip().zfill(6)
            if symbol in {item.symbol for item in contexts}:
                suspension_rows[symbol] = row

    observations = []
    for context in contexts:
        suspension = suspension_rows.get(context.symbol)
        st = st_rows.get(context.symbol)
        if suspension is not None:
            if (
                not _aware(source_time)
                or not _aware(upstream_source_time)
                or source_time > fetched_at
                or upstream_source_time > source_time
            ):
                suspension = None
        if suspension is not None:
            start = _optional_date(suspension.get("停牌时间"))
            end = _optional_date(suspension.get("停牌截止时间"))
            resume = _optional_date(suspension.get("预计复牌时间"))
            suspension_covers_day = bool(
                start is not None
                and end is not None
                and start <= trading_date <= end
                and (
                    (resume is not None and resume > trading_date)
                    or (resume is None and end > trading_date)
                )
            )
            if suspension_covers_day:
                row_values = _row_values(suspension)
                observations.append(PublicTradabilityObservation(
                    symbol=context.symbol,
                    exchange=context.exchange,
                    board=context.board,
                    trading_date=trading_date,
                    source_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
                    source_contract_id=(
                        "akshare-public-static-status-v1"
                    ),
                    source_name="AKShare公开数据聚合",
                    source_url=AKSHARE_STATUS_URL,
                    document_id=(
                        f"akshare-suspension-{trading_date:%Y%m%d}"
                    ),
                    source_time=source_time,
                    fetched_at=fetched_at,
                    content_sha256=_canonical_sha256(row_values),
                    effective_from=start,
                    effective_until=end,
                    upstream_source_name="东方财富停复牌信息",
                    upstream_source_url=EASTMONEY_SUSPENSION_URL,
                    upstream_document_id=(
                        f"eastmoney-suspension-{trading_date:%Y%m%d}"
                    ),
                    upstream_source_time=upstream_source_time,
                    trading_status=TradingSessionStatus.SUSPENDED,
                ))
                continue
        if st is not None:
            row_time = st.get("上游时间")
            st_source_time = (
                row_time if _aware(row_time) else source_time
            )
            st_upstream_source_time = (
                row_time if _aware(row_time) else upstream_source_time
            )
            if (
                not _aware(st_source_time)
                or not _aware(st_upstream_source_time)
                or st_source_time > fetched_at
                or st_upstream_source_time > st_source_time
            ):
                continue
            name = str(st.get("名称") or "").strip().upper()
            lifecycle = (
                SecurityLifecycleStatus.STAR_ST
                if "*ST" in name
                else SecurityLifecycleStatus.ST
            )
            observations.append(PublicTradabilityObservation(
                symbol=context.symbol,
                exchange=context.exchange,
                board=context.board,
                trading_date=trading_date,
                source_kind=PublicSourceKind.PUBLIC_AGGREGATOR,
                source_contract_id=(
                    "eastmoney-public-static-status-v1"
                ),
                source_name="东方财富公开数据聚合",
                source_url=EASTMONEY_ST_URL,
                document_id=(
                    f"eastmoney-st-board-{trading_date:%Y%m%d}"
                ),
                source_time=st_source_time,
                fetched_at=fetched_at,
                content_sha256=_canonical_sha256(_row_values(st)),
                effective_from=trading_date,
                effective_until=trading_date,
                upstream_source_name="东方财富风险警示板",
                upstream_source_url=EASTMONEY_ST_API_URL,
                upstream_document_id=(
                    f"eastmoney-st-board-{trading_date:%Y%m%d}"
                ),
                upstream_source_time=st_upstream_source_time,
                lifecycle_status=lifecycle,
            ))
    return tuple(observations)


def _fetch_calendar_document(
    *,
    as_of: datetime,
    session: Optional[requests.Session] = None,
) -> PublicCalendarDocument:
    active_session = session or requests.Session()
    active_session.trust_env = False
    try:
        response = active_session.get(
            SSE_CALENDAR_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        response.raise_for_status()
    except Exception as exc:
        raise PublicLivePocSourceError(
            "public_live_calendar_request_failed",
            private_detail=type(exc).__name__,
        ) from exc
    fetched_at = datetime.now(SHANGHAI_TZ)
    encoding = response.apparent_encoding or response.encoding or "utf-8"
    text = response.content.decode(encoding, errors="replace")
    source_time = None
    last_modified = response.headers.get("Last-Modified")
    if last_modified:
        try:
            parsed = parsedate_to_datetime(last_modified)
            if parsed.tzinfo is not None:
                parsed_source_time = parsed.astimezone(SHANGHAI_TZ)
                if parsed_source_time <= fetched_at:
                    source_time = parsed_source_time
        except (TypeError, ValueError, OverflowError):
            pass
    if source_time is None:
        annual_notice = re.search(
            rf'title="[^"]*{as_of.year}年部分节假日休市安排[^"]*"'
            r"[^>]*>.*?</a>\s*<span>\s*"
            r"(\d{4}-\d{2}-\d{2})\s*</span>",
            text,
            flags=re.DOTALL,
        )
        if annual_notice is not None:
            try:
                source_time = datetime.fromisoformat(
                    annual_notice.group(1)
                ).replace(tzinfo=SHANGHAI_TZ)
            except ValueError:
                source_time = None
    return PublicCalendarDocument(
        source_url=SSE_CALENDAR_URL,
        document_id=f"sse-a-share-calendar-{as_of.year}",
        text=text,
        source_time=source_time,
        fetched_at=fetched_at,
    )


def collect_default_public_live_sources(
    symbols: Tuple[str, ...],
    as_of: datetime,
) -> PublicLivePocSourceBundle:
    import akshare as ak

    run_id = f"public-live-{as_of:%Y%m%dT%H%M%S}"
    master_batch = fetch_security_master(
        radar_run_id=run_id,
        batch_id=f"{run_id}-security-master",
        as_of=as_of,
        providers=SecurityMasterProviders(
            sse=ak.stock_info_sh_name_code,
            szse=ak.stock_info_sz_name_code,
            bse=lambda: pd.DataFrame(),
        ),
    )
    contexts = build_public_security_contexts(
        batch=master_batch,
        symbols=symbols,
    )
    document = _fetch_calendar_document(as_of=as_of)
    calendars = build_public_calendar_evidence(
        document=document,
        trading_date=as_of.astimezone(SHANGHAI_TZ).date(),
        exchanges=tuple(item.exchange for item in contexts),
    )

    quote_batch = fetch_tencent_quotes(
        symbols=symbols,
        radar_run_id=run_id,
        batch_id=f"{run_id}-tencent-quote",
        as_of=as_of,
        batch_size=MAXIMUM_SAMPLE_COUNT,
        timeout_seconds=5,
    )
    quotes, quote_evidence = build_public_quote_evidence(quote_batch)

    failures = [
        f"security_master:{issue.code}"
        for issue in master_batch.meta.issues
    ]
    failures.extend(
        f"tencent_quote:{issue.code}"
        for issue in quote_batch.meta.issues
    )
    st_frame = None
    suspension_frame = None
    try:
        st_frame = _fetch_eastmoney_st_frame()
    except Exception:
        failures.append("public_aggregator:st_request_failed")
    try:
        suspension_frame = ak.stock_tfp_em(
            date=f"{as_of.astimezone(SHANGHAI_TZ):%Y%m%d}"
        )
    except Exception:
        failures.append("public_aggregator:suspension_request_failed")
    aggregator_observations = build_public_aggregator_observations(
        contexts=contexts,
        trading_date=as_of.astimezone(SHANGHAI_TZ).date(),
        fetched_at=datetime.now(SHANGHAI_TZ),
        st_frame=st_frame,
        suspension_frame=suspension_frame,
    )
    if suspension_frame is not None:
        failures.append(
            "public_aggregator:suspension_source_time_unavailable"
        )
    aggregator_status = (
        "degraded"
        if st_frame is not None or suspension_frame is not None
        else "failed"
    )
    query_as_of = datetime.now(SHANGHAI_TZ)
    return PublicLivePocSourceBundle(
        query=PublicCompositeTradabilityQuery(
            trading_date=query_as_of.date(),
            as_of=query_as_of,
            securities=contexts,
            trading_calendars=calendars,
            quote_batch_evidence=quote_evidence,
        ),
        quotes=quotes,
        official_observations=(),
        aggregator_observations=aggregator_observations,
        requested_symbols=symbols,
        source_statuses={
            "securityMaster": (
                "degraded"
                if master_batch.meta.issues
                else "completed"
            ),
            "tradingCalendar": "completed",
            "tencentQuote": (
                "degraded" if quote_batch.meta.issues else "completed"
            ),
            "officialTradability": "not_available",
            "publicAggregator": aggregator_status,
        },
        source_failures=tuple(dict.fromkeys(failures)),
    )


def run_public_live_poc(
    *,
    symbols: Iterable[str],
    as_of: datetime,
    collector: Callable[
        [Tuple[str, ...], datetime], PublicLivePocSourceBundle
    ] = collect_default_public_live_sources,
) -> PublicLivePocExecution:
    is_default_collector = collector is collect_default_public_live_sources
    normalized = validate_public_live_symbols(symbols)
    if normalized is None or not _aware(as_of):
        return PublicLivePocExecution(
            execution_status="blocked",
            real_poc_status="failed",
            reason="public_live_query_invalid",
        )
    started = time.monotonic()
    try:
        bundle = collector(normalized, as_of)
    except PublicLivePocSourceError as exc:
        return PublicLivePocExecution(
            execution_status="blocked",
            real_poc_status="failed",
            reason=exc.reason_code,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
    except Exception:
        return PublicLivePocExecution(
            execution_status="blocked",
            real_poc_status="failed",
            reason="public_live_source_unexpected_error",
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
    if not isinstance(bundle, PublicLivePocSourceBundle):
        return PublicLivePocExecution(
            execution_status="blocked",
            real_poc_status="failed",
            reason="public_live_collector_contract_mismatch",
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
    query_symbols = tuple(item.symbol for item in bundle.query.securities)
    declared_symbols = bundle.requested_symbols or query_symbols
    query_as_of = bundle.query.as_of
    query_time_valid = bool(
        _aware(query_as_of)
        and query_as_of.astimezone(SHANGHAI_TZ).date()
        == as_of.astimezone(SHANGHAI_TZ).date()
        and -5
        <= (query_as_of - as_of).total_seconds()
        <= MAXIMUM_COLLECTION_SECONDS
    )
    if (
        declared_symbols != normalized
        or query_symbols != normalized
        or not query_time_valid
        or bundle.query.trading_date
        != query_as_of.astimezone(SHANGHAI_TZ).date()
    ):
        return PublicLivePocExecution(
            execution_status="blocked",
            real_poc_status="failed",
            reason="public_live_collector_contract_mismatch",
            source_statuses=bundle.source_statuses,
            source_failures=bundle.source_failures,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
    report = run_public_composite_tradability_poc(
        query=bundle.query,
        quotes=bundle.quotes,
        official_observations=bundle.official_observations,
        aggregator_observations=bundle.aggregator_observations,
        executed=True,
    )
    return PublicLivePocExecution(
        execution_status="completed",
        real_poc_status=(
            "completed" if is_default_collector else "not_run"
        ),
        reason=None,
        report=report,
        source_statuses=bundle.source_statuses,
        source_failures=bundle.source_failures,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        as_of=query_as_of,
        trading_date=bundle.query.trading_date,
    )
