from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceIssue,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class SecurityMasterProviders:
    sse: Callable[[str], pd.DataFrame]
    szse: Callable[[str], pd.DataFrame]
    bse: Callable[[], pd.DataFrame]


def _default_providers() -> SecurityMasterProviders:
    import akshare as ak

    return SecurityMasterProviders(
        sse=ak.stock_info_sh_name_code,
        szse=ak.stock_info_sz_name_code,
        bse=ak.stock_info_bj_name_code,
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


def fetch_security_master(
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    providers: Optional[SecurityMasterProviders] = None,
    clock: Callable[[], datetime] = _now,
) -> SourceBatch[SecurityMasterRecord]:
    providers = providers or _default_providers()
    fetched_at = clock()
    records = {}
    issues = []
    failed = False

    source_calls = (
        ("sse", "主板A股", lambda: providers.sse("主板A股")),
        ("sse", "科创板", lambda: providers.sse("科创板")),
        ("szse", "深交所A股", lambda: providers.szse("A股列表")),
        ("bse", "北交所A股", providers.bse),
    )

    for source, board, fetcher in source_calls:
        try:
            frame = fetcher()
        except Exception as exc:
            failed = True
            issues.append(SourceIssue(
                code="source_request_failed",
                source=source,
                message=f"{board}请求失败：{type(exc).__name__}",
            ))
            continue

        for _, row in frame.iterrows():
            if source == "sse":
                symbol = str(row.get("证券代码") or "").strip().zfill(6)
                name = _optional_text(row.get("证券简称")) or ""
                listing_date = _optional_date(row.get("上市日期"))
                total_shares = None
                circulating_shares = None
                source_industry = None
                report_date = None
            elif source == "szse":
                symbol = str(row.get("A股代码") or "").strip().zfill(6)
                name = _optional_text(row.get("A股简称")) or ""
                listing_date = _optional_date(row.get("A股上市日期"))
                total_shares = _optional_float(row.get("A股总股本"))
                circulating_shares = _optional_float(row.get("A股流通股本"))
                source_industry = _optional_text(row.get("所属行业"))
                report_date = None
                board = _optional_text(row.get("板块")) or board
            else:
                symbol = str(row.get("证券代码") or "").strip().zfill(6)
                name = _optional_text(row.get("证券简称")) or ""
                listing_date = _optional_date(row.get("上市日期"))
                total_shares = _optional_float(row.get("总股本"))
                circulating_shares = _optional_float(row.get("流通股本"))
                source_industry = _optional_text(row.get("所属行业"))
                report_date = _optional_date(row.get("报告日期"))

            if not symbol.isdigit() or len(symbol) != 6:
                issues.append(SourceIssue(
                    code="invalid_symbol",
                    source=source,
                    message=f"{board}返回无效证券代码",
                ))
                continue
            if symbol in records:
                issues.append(SourceIssue(
                    code="duplicate_symbol",
                    source=source,
                    message=f"证券代码{symbol}在主档中重复",
                    symbols=[symbol],
                ))
                continue
            records[symbol] = SecurityMasterRecord(
                symbol=symbol,
                name=name,
                exchange=source,
                board=board,
                listingDate=listing_date,
                totalShares=total_shares,
                circulatingShares=circulating_shares,
                sourceIndustry=source_industry,
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
        source="official_exchange_security_master",
        asOf=as_of,
        sourceTime=None,
        fetchedAt=fetched_at,
        expectedCount=expected_count,
        returnedCount=len(items),
        rowCoverage=row_coverage,
        requiredFieldCoverage=_field_coverage(
            items,
            ("symbol", "name", "listing_date"),
        ),
        issues=issues,
    )
    return SourceBatch[SecurityMasterRecord](meta=meta, items=items)
