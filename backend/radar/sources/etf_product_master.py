import warnings
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfProductMasterRecord,
    ListedFundProductType,
    RadarBatchMeta,
    SourceBatch,
    SourceIssue,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION = (
    "radar-etf-product-classification-v1"
)
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_PRODUCT_URL = "https://fund.szse.cn/api/report/ShowReport"
REQUEST_TIMEOUT_SECONDS = 20.0

SSE_DOMESTIC_EQUITY_CODES = frozenset({"F111", "F112", "F114", "F115"})
SSE_CROSS_BORDER_EQUITY_CODES = frozenset({"F113"})
SSE_BOND_CODES = frozenset({"F121", "F122", "F123"})
SSE_COMMODITY_CODES = frozenset({"F141"})
SSE_MONEY_MARKET_CODES = frozenset({"F150"})


@dataclass(frozen=True)
class EtfProductMasterProviders:
    sse_products: Callable[[], pd.DataFrame]
    sse_categories: Callable[[], pd.DataFrame]
    szse_products: Callable[[], pd.DataFrame]


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _fetch_sse(sql_id: str, *, paginated: bool) -> pd.DataFrame:
    params = {
        "isPagination": "true" if paginated else "false",
        "sqlId": sql_id,
    }
    if paginated:
        params.update({
            "pageHelp.pageSize": "5000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
        })
    with _session() as session:
        response = session.get(
            SSE_QUERY_URL,
            params=params,
            headers={
                "Referer": "https://etf.sse.com.cn/fundlist/",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise RuntimeError("上交所官方基金列表响应缺少result")
    return pd.DataFrame(rows)


def _fetch_sse_products() -> pd.DataFrame:
    return _fetch_sse("COMMON_JJZWZ_JJLB_L", paginated=True)


def _fetch_sse_categories() -> pd.DataFrame:
    return _fetch_sse("COMMON_JJZWZ_JJLB_JJLX_C", paginated=False)


def _fetch_szse_products() -> pd.DataFrame:
    with _session() as session:
        response = session.get(
            SZSE_PRODUCT_URL,
            params={
                "SHOWTYPE": "xlsx",
                "CATALOGID": "1000_lf",
                "TABKEY": "tab1",
            },
            headers={
                "Referer": (
                    "https://fund.szse.cn/marketdata/fundslist/index.html"
                ),
                "User-Agent": "Mozilla/5.0",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_excel(
            BytesIO(response.content),
            engine="openpyxl",
            dtype={"基金代码": str},
        )


def _default_providers() -> EtfProductMasterProviders:
    return EtfProductMasterProviders(
        sse_products=_fetch_sse_products,
        sse_categories=_fetch_sse_categories,
        szse_products=_fetch_szse_products,
    )


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _optional_text(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None
    return text


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


def _sse_product_type(category_code: Optional[str]) -> ListedFundProductType:
    if category_code is None:
        return ListedFundProductType.UNKNOWN
    if category_code.startswith("F1"):
        return ListedFundProductType.ETF
    if category_code.startswith("F2"):
        return ListedFundProductType.LOF
    if category_code.startswith("F6"):
        return ListedFundProductType.REIT
    if category_code.startswith("F4"):
        return ListedFundProductType.OTHER_LISTED_FUND
    return ListedFundProductType.UNKNOWN


def _sse_asset_class(category_code: Optional[str]) -> EtfAssetClass:
    if category_code in SSE_DOMESTIC_EQUITY_CODES:
        return EtfAssetClass.DOMESTIC_EQUITY
    if category_code in SSE_CROSS_BORDER_EQUITY_CODES:
        return EtfAssetClass.CROSS_BORDER_EQUITY
    if category_code in SSE_BOND_CODES:
        return EtfAssetClass.BOND
    if category_code in SSE_COMMODITY_CODES:
        return EtfAssetClass.COMMODITY
    if category_code in SSE_MONEY_MARKET_CODES:
        return EtfAssetClass.MONEY_MARKET
    return EtfAssetClass.UNKNOWN


def _szse_product_type(category_name: Optional[str]) -> ListedFundProductType:
    if category_name == "ETF":
        return ListedFundProductType.ETF
    if category_name == "LOF":
        return ListedFundProductType.LOF
    if category_name == "不动产基金":
        return ListedFundProductType.REIT
    if category_name is None:
        return ListedFundProductType.UNKNOWN
    return ListedFundProductType.OTHER_LISTED_FUND


def _szse_asset_class(
    product_type: ListedFundProductType,
    investment_type: Optional[str],
) -> EtfAssetClass:
    if product_type != ListedFundProductType.ETF:
        return EtfAssetClass.UNKNOWN
    if investment_type == "债券基金":
        return EtfAssetClass.BOND
    if investment_type == "货币市场基金":
        return EtfAssetClass.MONEY_MARKET
    if investment_type == "混合基金":
        return EtfAssetClass.MIXED
    return EtfAssetClass.UNKNOWN


def _classification_reasons(
    product_type: ListedFundProductType,
    asset_class: EtfAssetClass,
    *,
    asset_scope_reason: Optional[str] = None,
) -> Tuple[str, ...]:
    reasons = []
    if product_type == ListedFundProductType.UNKNOWN:
        reasons.append("product_type_unknown")
    elif product_type != ListedFundProductType.ETF:
        reasons.append("non_etf_product")
    else:
        reasons.append("management_style_unverified")
        if asset_scope_reason is not None:
            reasons.append(asset_scope_reason)
        elif asset_class == EtfAssetClass.UNKNOWN:
            reasons.append("asset_class_unverified")
    return tuple(reasons)


def _sse_record(
    row: pd.Series,
    categories: Dict[str, str],
    fetched_at: datetime,
) -> EtfProductMasterRecord:
    category_code = _optional_text(row.get("CATEGORY"))
    category_name = categories.get(category_code or "")
    mapped_code = category_code if category_name is not None else None
    product_type = _sse_product_type(mapped_code)
    asset_class = (
        _sse_asset_class(mapped_code)
        if product_type == ListedFundProductType.ETF
        else EtfAssetClass.UNKNOWN
    )
    return EtfProductMasterRecord(
        symbol=str(row.get("FUND_CODE") or "").strip().zfill(6),
        officialName=_optional_text(row.get("FUND_ABBR")) or "",
        exchange="sse",
        productType=product_type,
        managementStyle=EtfManagementStyle.UNKNOWN,
        assetClass=asset_class,
        sourceCategoryCode=category_code,
        sourceCategoryName=category_name,
        targetIndexName=_optional_text(row.get("INDEX_NAME")),
        listingDate=_optional_date(row.get("LISTING_DATE")),
        manager=_optional_text(row.get("COMPANY_NAME")),
        classificationMappingVersion=(
            ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION
        ),
        classificationReasons=_classification_reasons(
            product_type,
            asset_class,
            asset_scope_reason=(
                "cross_border_asset_unverified"
                if mapped_code == "F131"
                else None
            ),
        ),
        source="sse_official_fund_list",
        fetchedAt=fetched_at,
        sourceFields=_source_fields(row),
    )


def _szse_record(
    row: pd.Series,
    fetched_at: datetime,
) -> EtfProductMasterRecord:
    category_name = _optional_text(row.get("基金类别"))
    investment_type = _optional_text(row.get("投资类别"))
    product_type = _szse_product_type(category_name)
    asset_class = _szse_asset_class(product_type, investment_type)
    return EtfProductMasterRecord(
        symbol=str(row.get("基金代码") or "").strip().zfill(6),
        officialName=_optional_text(row.get("基金简称")) or "",
        exchange="szse",
        productType=product_type,
        managementStyle=EtfManagementStyle.UNKNOWN,
        assetClass=asset_class,
        sourceCategoryName=category_name,
        sourceInvestmentType=investment_type,
        listingDate=_optional_date(row.get("上市日期")),
        manager=_optional_text(row.get("基金管理人")),
        classificationMappingVersion=(
            ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION
        ),
        classificationReasons=_classification_reasons(
            product_type,
            asset_class,
            asset_scope_reason=(
                "equity_region_unverified"
                if (
                    product_type == ListedFundProductType.ETF
                    and investment_type == "股票基金"
                )
                else None
            ),
        ),
        source="szse_official_fund_list",
        fetchedAt=fetched_at,
        sourceFields=_source_fields(row),
    )


def _field_coverage(items):
    if not items:
        return {
            "official_name": 0.0,
            "product_type_known": 0.0,
            "listing_date": 0.0,
            "etf_asset_class_confirmed": 0.0,
            "etf_management_style_confirmed": 0.0,
        }
    etfs = [
        item
        for item in items
        if item.product_type == ListedFundProductType.ETF
    ]
    etf_count = len(etfs)
    return {
        "official_name": sum(bool(item.official_name) for item in items)
        / len(items),
        "product_type_known": sum(
            item.product_type != ListedFundProductType.UNKNOWN
            for item in items
        ) / len(items),
        "listing_date": sum(
            item.listing_date is not None
            for item in items
        ) / len(items),
        "etf_asset_class_confirmed": (
            sum(
                item.asset_class != EtfAssetClass.UNKNOWN
                for item in etfs
            ) / etf_count
            if etf_count
            else 0.0
        ),
        "etf_management_style_confirmed": (
            sum(
                item.management_style != EtfManagementStyle.UNKNOWN
                for item in etfs
            ) / etf_count
            if etf_count
            else 0.0
        ),
    }


def fetch_etf_product_master(
    radar_run_id: str,
    batch_id: str,
    as_of: datetime,
    providers: Optional[EtfProductMasterProviders] = None,
    clock: Callable[[], datetime] = _now,
) -> SourceBatch[EtfProductMasterRecord]:
    providers = providers or _default_providers()
    fetched_at = clock()
    issues = []
    failed = False
    expected_count = 0
    records = {}

    categories = {}
    try:
        category_frame = providers.sse_categories()
        if category_frame.empty:
            failed = True
            issues.append(SourceIssue(
                code="empty_source_result",
                source="sse_category_map",
                message="上交所官方基金分类返回空结果",
            ))
        else:
            categories = {
                str(row.get("CATEGORY_CODE") or "").strip(): category_name
                for _, row in category_frame.iterrows()
                if str(row.get("CATEGORY_CODE") or "").strip()
                and (category_name := _optional_text(
                    row.get("CATEGORY_NAME")
                )) is not None
            }
    except Exception as exc:
        failed = True
        issues.append(SourceIssue(
            code="source_request_failed",
            source="sse_category_map",
            message=f"上交所官方基金分类请求失败：{type(exc).__name__}",
        ))

    source_frames = []
    for source, fetcher in (
        ("sse_product_list", providers.sse_products),
        ("szse_product_list", providers.szse_products),
    ):
        try:
            frame = fetcher()
        except Exception as exc:
            failed = True
            issues.append(SourceIssue(
                code="source_request_failed",
                source=source,
                message=f"{source}请求失败：{type(exc).__name__}",
            ))
            continue
        if frame.empty:
            failed = True
            issues.append(SourceIssue(
                code="empty_source_result",
                source=source,
                message=f"{source}返回空结果",
            ))
            continue
        expected_count += len(frame)
        source_frames.append((source, frame))

    for source, frame in source_frames:
        for _, row in frame.iterrows():
            symbol = str(
                row.get("FUND_CODE")
                if source == "sse_product_list"
                else row.get("基金代码")
                or ""
            ).strip().zfill(6)
            if not symbol.isdigit() or len(symbol) != 6:
                issues.append(SourceIssue(
                    code="invalid_symbol",
                    source=source,
                    message=f"{source}返回无效基金代码",
                ))
                continue
            if symbol in records:
                issues.append(SourceIssue(
                    code="duplicate_symbol",
                    source=source,
                    message=f"基金代码{symbol}在官方产品主档中重复",
                    symbols=[symbol],
                ))
                continue
            records[symbol] = (
                _sse_record(row, categories, fetched_at)
                if source == "sse_product_list"
                else _szse_record(row, fetched_at)
            )

    items = list(records.values())
    meta_expected_count = None if failed else expected_count
    row_coverage = (
        None
        if meta_expected_count is None
        else len(items) / meta_expected_count
    )
    meta = RadarBatchMeta(
        radarRunId=radar_run_id,
        batchId=batch_id,
        source="official_exchange_listed_fund_product_master",
        asOf=as_of,
        sourceTime=None,
        fetchedAt=fetched_at,
        expectedCount=meta_expected_count,
        returnedCount=len(items),
        rowCoverage=row_coverage,
        requiredFieldCoverage=_field_coverage(items),
        issues=issues,
    )
    return SourceBatch[EtfProductMasterRecord](meta=meta, items=items)
