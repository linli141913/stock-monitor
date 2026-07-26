from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    EtfShareObservation,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SSE_SHARE_SOURCE_CONTRACT = "exchange-etf-shares-sse-v1"
SZSE_SHARE_SOURCE_CONTRACT = "exchange-etf-shares-szse-v1"


@dataclass(frozen=True)
class EtfDailyShareProviders:
    sse: Callable[[str], pd.DataFrame]
    szse: Callable[[str, str], pd.DataFrame]


def _default_providers() -> EtfDailyShareProviders:
    import akshare as ak

    return EtfDailyShareProviders(
        sse=ak.fund_etf_scale_sse,
        szse=ak.fund_scale_daily_szse,
    )


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _normalize_symbol(value) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit():
        return None
    text = text.zfill(6)
    return text if len(text) == 6 else None


def _optional_float(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _optional_date(value) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _frame_value(row: pd.Series, *names):
    for name in names:
        if name in row:
            return row.get(name)
    return None


def fetch_etf_daily_share_observations(
    *,
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    report_date: date,
    sse_symbols: Iterable[str] = (),
    szse_symbols: Iterable[str] = (),
    providers: Optional[EtfDailyShareProviders] = None,
    clock: Callable[[], datetime] = _now,
) -> SourceBatch[EtfShareObservation]:
    """Read dated official exchange share facts without persistence."""
    providers = providers or _default_providers()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("asOf必须包含时区")
    requested_by_source = {
        "sse": tuple(dict.fromkeys(
            symbol for symbol in (
                _normalize_symbol(value) for value in sse_symbols
            )
            if symbol is not None
        )),
        "szse": tuple(dict.fromkeys(
            symbol for symbol in (
                _normalize_symbol(value) for value in szse_symbols
            )
            if symbol is not None
        )),
    }
    requested = set().union(*requested_by_source.values())
    fetched_at = clock()
    records = {}
    issues = []
    failed = False

    def add_issue(code, source, message, symbols=()):
        issues.append(SourceIssue(
            code=code,
            source=source,
            message=message,
            symbols=list(symbols),
        ))

    source_calls = (
        (
            "sse",
            requested_by_source["sse"],
            lambda: providers.sse(report_date.strftime("%Y%m%d")),
            SSE_SHARE_SOURCE_CONTRACT,
            ("统计日期", "日期"),
        ),
        (
            "szse",
            requested_by_source["szse"],
            lambda: providers.szse(
                report_date.strftime("%Y%m%d"),
                report_date.strftime("%Y%m%d"),
            ),
            SZSE_SHARE_SOURCE_CONTRACT,
            ("日期", "统计日期"),
        ),
    )

    for source, source_symbols, fetcher, contract_id, date_names in source_calls:
        if not source_symbols:
            continue
        try:
            frame = fetcher()
        except Exception as exc:
            failed = True
            add_issue(
                "source_request_failed",
                source,
                f"{source} ETF份额日频请求失败：{type(exc).__name__}",
                source_symbols,
            )
            continue

        if frame is None or frame.empty:
            failed = True
            add_issue(
                "empty_source_result",
                source,
                f"{source} ETF份额日频返回空结果",
                source_symbols,
            )
            continue

        seen_source = set()
        for _, row in frame.iterrows():
            symbol = _normalize_symbol(
                _frame_value(row, "基金代码", "证券代码", "代码")
            )
            if symbol is None:
                failed = True
                add_issue(
                    "invalid_symbol",
                    source,
                    f"{source} ETF份额日频返回无效代码",
                )
                continue
            if symbol not in source_symbols:
                # Official files are full-market snapshots; select only the
                # requested products and do not turn the rest into warnings.
                continue
            if symbol in seen_source or symbol in records:
                failed = True
                add_issue(
                    "duplicate_symbol",
                    source,
                    f"ETF代码{symbol}在份额日频批次中重复",
                    [symbol],
                )
                continue
            seen_source.add(symbol)

            observed_date = _optional_date(
                _frame_value(row, *date_names)
            )
            if observed_date is None:
                failed = True
                add_issue(
                    "missing_source_report_date",
                    source,
                    f"ETF代码{symbol}缺少份额统计日期",
                    [symbol],
                )
                continue
            if observed_date != report_date:
                failed = True
                add_issue(
                    "mismatched_source_report_date",
                    source,
                    f"ETF代码{symbol}份额统计日期与请求批次不一致",
                    [symbol],
                )
                continue

            fund_shares = _optional_float(
                _frame_value(row, "基金份额", "基金规模(份)")
            )
            if fund_shares is None or fund_shares < 0:
                failed = True
                add_issue(
                    "missing_fund_shares",
                    source,
                    f"ETF代码{symbol}缺少有效基金份额",
                    [symbol],
                )
                continue
            records[symbol] = EtfShareObservation(
                symbol=symbol,
                sourceReportDate=observed_date,
                fundShares=fund_shares,
                fundSharesUnit="share",
                sourceContractId=contract_id,
                source=f"{source}_official",
                fetchedAt=fetched_at,
            )

        missing = [
            symbol for symbol in source_symbols
            if symbol not in seen_source and symbol not in records
        ]
        if missing:
            add_issue(
                "missing_symbols",
                source,
                f"{source} ETF份额日频未返回{len(missing)}只证券",
                missing,
            )

    if requested and len(records) != len(requested) and not failed:
        failed = True
        add_issue(
            "incomplete_source_batch",
            "official_exchange_etf_daily_shares",
            "ETF份额日频批次未覆盖全部请求代码",
            sorted(requested - set(records)),
        )

    items = [
        records[symbol]
        for symbol in sorted(records)
    ]
    expected_count = None if failed else len(requested)
    row_coverage = (
        None
        if expected_count is None
        else (len(items) / expected_count if expected_count else 0.0)
    )
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source="official_exchange_etf_daily_shares",
        asOf=as_of,
        sourceTime=None,
        fetchedAt=fetched_at,
        expectedCount=expected_count,
        returnedCount=len(items),
        rowCoverage=row_coverage,
        requiredFieldCoverage={
            "source_report_date": (
                1.0 if items else 0.0
            ),
            "fund_shares": (
                1.0 if items else 0.0
            ),
        },
        issues=issues,
    )
    return SourceBatch[EtfShareObservation](meta=meta, items=items)
