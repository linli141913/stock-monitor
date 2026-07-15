import re
from typing import Dict, Iterable, List, Optional


UNKNOWN_INDUSTRIES = {"", "未知行业", "港股", "-", "暂无数据"}
ETF_THEME_ALIASES = (
    ("新能源车", ("新能源车", "新能源汽车", "汽车")),
    ("汽车", ("汽车", "整车", "汽车零部件")),
    ("沪深300", ("沪深300", "大盘蓝筹")),
    ("中证A500", ("中证A500", "A500")),
    ("科创50", ("科创50", "科创板")),
    ("创业板", ("创业板",)),
    ("恒生科技", ("恒生科技", "港股科技")),
    ("半导体", ("半导体", "芯片", "集成电路")),
    ("人工智能", ("人工智能", "AI", "算力")),
    ("红利", ("红利", "高股息")),
    ("证券", ("证券", "券商")),
    ("医药", ("医药", "医疗")),
    ("消费", ("消费",)),
)

_CONTEXT_CACHE: Dict[str, dict] = {}


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().lower()
    if value.startswith(("sh", "sz", "hk", "bj")):
        value = value[2:]
    return value


def detect_asset_type(symbol: str, stock_name: str = "") -> str:
    code = normalize_symbol(symbol)
    name = str(stock_name or "").upper()
    if code.isdigit() and len(code) == 5:
        return "hk_stock"
    if "ETF" in name or "交易型开放式指数" in name:
        return "domestic_etf"
    if code.startswith("5") or code.startswith("159"):
        return "domestic_etf"
    return "a_stock"


def quote_prefix(symbol: str, stock_name: str = "") -> str:
    code = normalize_symbol(symbol)
    asset_type = detect_asset_type(code, stock_name)
    if asset_type == "hk_stock":
        return "hk"
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _valid_industries(industry_tags: Optional[Iterable[str]]) -> List[str]:
    return [
        str(tag).strip()
        for tag in industry_tags or []
        if str(tag).strip() not in UNKNOWN_INDUSTRIES
    ]


def _etf_theme(stock_name: str) -> tuple[str, List[str]]:
    name = str(stock_name or "").strip()
    for theme, aliases in ETF_THEME_ALIASES:
        if theme.lower() in name.lower():
            return theme, list(aliases)
    cleaned = re.sub(r"交易型开放式指数证券投资基金|交易型开放式指数|ETF.*$", "", name, flags=re.I)
    cleaned = cleaned.strip(" -—_（）()")
    return (cleaned or "国内ETF"), ([cleaned] if cleaned else [])


def _dedupe_terms(values: Iterable[str]) -> List[str]:
    result = []
    for value in values:
        term = str(value or "").strip()
        if len(term) < 2 or term in result:
            continue
        result.append(term)
    return result


def build_asset_context(
    symbol: str,
    stock_name: str = "",
    industry_tags: Optional[Iterable[str]] = None,
) -> dict:
    code = normalize_symbol(symbol)
    asset_type = detect_asset_type(code, stock_name)
    industries = _valid_industries(industry_tags)

    if asset_type == "domestic_etf":
        industry_name, theme_terms = _etf_theme(stock_name)
        search_terms = _dedupe_terms([stock_name, industry_name, *theme_terms])
        market = "cn"
    else:
        fallback = "港股行业待确认" if asset_type == "hk_stock" else "行业待确认"
        industry_name = industries[0] if industries else fallback
        search_terms = _dedupe_terms([stock_name, *industries])
        market = "hk" if asset_type == "hk_stock" else "cn"

    return {
        "symbol": code,
        "stock_name": str(stock_name or "").strip(),
        "market": market,
        "asset_type": asset_type,
        "quote_prefix": quote_prefix(code, stock_name),
        "industry_name": industry_name,
        "search_terms": search_terms,
    }


def register_asset_context(context: dict) -> dict:
    code = normalize_symbol(context.get("symbol", ""))
    if code:
        _CONTEXT_CACHE[code] = dict(context)
    return context


def get_cached_asset_context(symbol: str) -> Optional[dict]:
    context = _CONTEXT_CACHE.get(normalize_symbol(symbol))
    return dict(context) if context else None


def get_watchlist_search_terms(watchlist: Iterable[dict]) -> List[str]:
    terms = []
    for item in watchlist:
        code = normalize_symbol(item.get("stockCode", ""))
        context = get_cached_asset_context(code) or build_asset_context(
            code,
            item.get("stockName", ""),
        )
        terms.extend(context["search_terms"])
    return _dedupe_terms(terms)


def _fetch_industry_tags(symbol: str, stock_name: str, asset_type: str) -> List[str]:
    code = normalize_symbol(symbol)
    if asset_type == "domestic_etf":
        return []
    if asset_type == "hk_stock":
        import akshare as ak

        data = ak.stock_hk_company_profile_em(symbol=code)
        if data.empty:
            return []
        industry = str(data.fillna("").iloc[0].get("所属行业") or "").strip()
        return [industry] if industry and industry not in UNKNOWN_INDUSTRIES else []

    import requests

    prefix = quote_prefix(code, stock_name).upper()
    response = requests.get(
        "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax",
        params={"code": f"{prefix}{code}"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=6,
    )
    response.raise_for_status()
    data = response.json().get("jbzl") or {}
    raw_industry = str(data.get("sshy") or data.get("sszjhhy") or "").strip()
    return _valid_industries(raw_industry.split("-") if raw_industry else [])


def resolve_asset_context(
    symbol: str,
    stock_name: str = "",
    refresh: bool = False,
) -> dict:
    code = normalize_symbol(symbol)
    initial = build_asset_context(code, stock_name)
    cached = get_cached_asset_context(code)
    if not refresh and cached and cached.get("industry_name") not in {
        "行业待确认",
        "港股行业待确认",
    }:
        return cached
    if initial["asset_type"] == "domestic_etf":
        return register_asset_context(initial)
    try:
        industry_tags = _fetch_industry_tags(
            code,
            stock_name,
            initial["asset_type"],
        )
    except Exception:
        industry_tags = []
    return register_asset_context(
        build_asset_context(code, stock_name, industry_tags)
    )
