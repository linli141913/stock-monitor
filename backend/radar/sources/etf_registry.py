from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd

import market_calendar

from radar.contracts import (
    EtfRegistryRecord,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MAX_SSE_REPORT_LAG_DAYS = 14


@dataclass(frozen=True)
class EtfRegistryProviders:
    sse: Callable[[str], pd.DataFrame]
    szse: Callable[[], pd.DataFrame]


def _default_providers() -> EtfRegistryProviders:
    import akshare as ak

    return EtfRegistryProviders(
        sse=ak.fund_etf_scale_sse,
        szse=ak.fund_etf_scale_szse,
    )


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _optional_text(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_date(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _source_fields(row: pd.Series) -> Dict[str, Any]:
    values = {}
    for key, value in row.items():
        if value is None or pd.isna(value):
            values[str(key)] = None
        elif isinstance(value, (date, datetime, pd.Timestamp)):
            values[str(key)] = value.isoformat()
        elif hasattr(value, "item"):
            values[str(key)] = value.item()
        else:
            values[str(key)] = value
    return values


def _field_coverage(records, field_names):
    if not records:
        return {field_name: 0.0 for field_name in field_names}
    return {
        field_name: sum(
            getattr(record, field_name) is not None
            for record in records
        ) / len(records)
        for field_name in field_names
    }


def _latest_completed_sse_report_date(
    as_of: datetime,
    snapshot_date: date,
    calendar_day_provider: Callable[[str, date], market_calendar.CalendarDay],
) -> date:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("asOf必须包含时区")

    local_as_of_date = as_of.astimezone(SHANGHAI_TZ).date()
    candidate = min(snapshot_date, local_as_of_date)
    if candidate >= local_as_of_date:
        candidate = local_as_of_date - timedelta(days=1)

    while (local_as_of_date - candidate).days <= MAX_SSE_REPORT_LAG_DAYS:
        calendar_day = calendar_day_provider("cn", candidate)
        if calendar_day.kind in {"full", "half"}:
            return candidate
        if calendar_day.kind != "closed":
            raise RuntimeError("上交所官方交易日历暂时无法确认")
        candidate -= timedelta(days=1)

    raise RuntimeError("上交所ETF统计日期超过最大允许滞后")


def fetch_etf_registry(
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    snapshot_date: date,
    providers: Optional[EtfRegistryProviders] = None,
    clock: Callable[[], datetime] = _now,
    calendar_day_provider: Callable[
        [str, date],
        market_calendar.CalendarDay,
    ] = market_calendar.get_calendar_day_kind,
) -> SourceBatch[EtfRegistryRecord]:
    providers = providers or _default_providers()
    fetched_at = clock()
    records = {}
    issues = []
    failed = False

    requested_sse_report_date = None

    def fetch_sse():
        nonlocal requested_sse_report_date
        requested_sse_report_date = _latest_completed_sse_report_date(
            as_of,
            snapshot_date,
            calendar_day_provider,
        )
        return providers.sse(requested_sse_report_date.strftime("%Y%m%d"))

    for source, fetcher in (
        ("sse", fetch_sse),
        ("szse", providers.szse),
    ):
        try:
            frame = fetcher()
        except Exception as exc:
            failed = True
            issues.append(SourceIssue(
                code="source_request_failed",
                source=source,
                message=f"{source} ETF名册请求失败：{type(exc).__name__}",
            ))
            continue

        if source == "sse" and frame.empty:
            failed = True
            issues.append(SourceIssue(
                code="empty_source_result",
                source=source,
                message=f"{source} ETF名册返回空结果",
            ))
            continue

        for _, row in frame.iterrows():
            symbol = str(row.get("基金代码") or "").strip().zfill(6)
            if not symbol.isdigit() or len(symbol) != 6:
                issues.append(SourceIssue(
                    code="invalid_symbol",
                    source=source,
                    message=f"{source} ETF名册返回无效代码",
                ))
                continue
            if symbol in records:
                issues.append(SourceIssue(
                    code="duplicate_symbol",
                    source=source,
                    message=f"ETF代码{symbol}在官方名册中重复",
                    symbols=[symbol],
                ))
                continue

            if source == "sse":
                source_type = _optional_text(row.get("ETF类型"))
                investment_type = None
                listing_date = None
                manager = None
                sponsor = None
                custodian = None
                nav = None
                report_date = _optional_date(row.get("统计日期"))
                local_as_of_date = as_of.astimezone(SHANGHAI_TZ).date()
                if report_date is None:
                    failed = True
                    issues.append(SourceIssue(
                        code="missing_source_report_date",
                        source=source,
                        message=f"ETF代码{symbol}缺少官方统计日期",
                        symbols=[symbol],
                    ))
                    continue
                if report_date >= local_as_of_date:
                    failed = True
                    issues.append(SourceIssue(
                        code="future_source_report_date",
                        source=source,
                        message=f"ETF代码{symbol}统计日期不早于本轮时点",
                        symbols=[symbol],
                    ))
                    continue
                if (
                    local_as_of_date - report_date
                ).days > MAX_SSE_REPORT_LAG_DAYS:
                    failed = True
                    issues.append(SourceIssue(
                        code="stale_source_report_date",
                        source=source,
                        message=f"ETF代码{symbol}统计日期超过允许滞后",
                        symbols=[symbol],
                    ))
                    continue
                if report_date != requested_sse_report_date:
                    failed = True
                    issues.append(SourceIssue(
                        code="mismatched_source_report_date",
                        source=source,
                        message=f"ETF代码{symbol}统计日期与请求批次不一致",
                        symbols=[symbol],
                    ))
                    continue
            else:
                source_type = _optional_text(row.get("基金类别"))
                investment_type = _optional_text(row.get("投资类别"))
                listing_date = _optional_date(row.get("上市日期"))
                manager = _optional_text(row.get("基金管理人"))
                sponsor = _optional_text(row.get("基金发起人"))
                custodian = _optional_text(row.get("基金托管人"))
                nav = _optional_float(row.get("净值"))
                report_date = None

            records[symbol] = EtfRegistryRecord(
                symbol=symbol,
                name=_optional_text(row.get("基金简称")) or "",
                exchange=source,
                sourceType=source_type,
                investmentType=investment_type,
                listingDate=listing_date,
                fundShares=_optional_float(row.get("基金份额")),
                manager=manager,
                sponsor=sponsor,
                custodian=custodian,
                nav=nav,
                sourceReportDate=report_date,
                source=source,
                fetchedAt=fetched_at,
                sourceFields=_source_fields(row),
            )

    items = list(records.values())
    expected_count = None if failed else len(items)
    row_coverage = None if expected_count is None else (1.0 if items else 0.0)
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source="official_exchange_etf_registry",
        asOf=as_of,
        sourceTime=None,
        fetchedAt=fetched_at,
        expectedCount=expected_count,
        returnedCount=len(items),
        rowCoverage=row_coverage,
        requiredFieldCoverage=_field_coverage(
            items,
            ("symbol", "name", "source_type"),
        ),
        issues=issues,
    )
    return SourceBatch[EtfRegistryRecord](meta=meta, items=items)
