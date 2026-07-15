from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Literal, Optional
import hashlib
import re
import asset_context
import database
import event_classifier
import market_calendar
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/semiconductor-news", tags=["News Radar"])

class RadarNews(BaseModel):
    id: str
    title: str
    source: str
    publish_time: str
    publish_time_precision: str
    discovered_at: Optional[str] = None
    original_link: str
    credibility_level: str  # S, A, B, C
    credibility_method: str
    content_type: str
    region: str            # 国内, 国外
    related_chains: List[str]
    related_stocks: List[str]
    source_summary: str
    heuristic_impact: str
    impact_method: str
    verification_status: str
    direction: str
    priority: str


class RadarNewsFeed(BaseModel):
    status: Literal["available", "available_empty", "unavailable"]
    data: List[RadarNews]
    error: Optional[str] = None
    checkedAt: str

def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode('utf-8', errors='ignore')).hexdigest()

UNKNOWN_SOURCES = {"", "未知", "未知来源", "来源不明"}
TRUSTED_MEDIA = {"新浪财经", "财联社电报", "市场资讯", "港股快讯"}
DISCLOSURE_AGGREGATORS = {"东方财富公告汇总", "天天基金公告"}
DATE_ONLY_DISCLOSURE_SOURCES = {"天天基金公告"}
GLOBAL_KEYWORDS = (
    "美国", "美方", "美股", "美联储", "华尔街", "荷兰", "欧盟", "欧洲", "英国", "法国", "德国",
    "日本", "韩国", "印度", "以色列", "阿联酋", "迪拜", "加拿大", "俄罗斯", "海外", "国外",
    "ASML", "阿斯麦", "英伟达", "Meta", "OpenAI", "SK海力士", "苹果", "微软", "谷歌",
    "亚马逊", "特斯拉", "AMD", "英特尔", "高通", "美光", "三星", "ARM", "马斯克", "奥特曼",
)
GLOBAL_SOURCES = {"环球市场播报"}
CONTROL_KEYWORDS = ("管制", "限制", "实体清单", "制裁", "禁令")
POLICY_KEYWORDS = (
    "政策", "国务院", "发改委", "工信部", "财政部", "商务部", "科技部", "海关总署",
    "税收", "央行", "证监会", "指导意见", "规划", "新规", "条例", "办法", "法案",
)
POLICY_SOURCE_KEYWORDS = (
    "国务院", "发改委", "工信部", "财政部", "商务部", "科技部", "海关总署", "央行", "证监会", "政府",
)
INDUSTRY_RELEVANCE_KEYWORDS = (
    "半导体", "芯片", "集成电路", "晶圆", "封测", "光刻", "硅片", "功率器件", "存储器",
    "显示面板", "OLED", "Mini LED", "Micro LED", "光电子", "电子元件", "消费电子", "汽车电子",
    "服务器", "数据中心", "算力", "人工智能", "AI", "英伟达", "ASML", "阿斯麦", "台积电", "中芯国际",
)
MARKET_RELEVANCE_KEYWORDS = INDUSTRY_RELEVANCE_KEYWORDS + (
    "A股", "港股", "美股", "股票", "股市", "证券", "上市公司", "IPO", "ETF", "基金",
    "期货", "债券", "央行", "美联储", "证监会", "交易所", "上交所", "深交所", "港交所",
    "沪指", "深成指", "创业板", "科创板", "恒生", "纳斯达克", "标普", "道指",
    "行业", "产业", "产业链", "汽车", "新能源", "光伏", "锂电", "储能", "医药", "生物",
    "化工", "有色", "钢铁", "煤炭", "原油", "铜价", "黄金", "稀土", "机器人", "军工",
    "银行", "保险", "房地产", "监管", "关税", "出口管制", "制裁", "财报", "业绩", "营收",
    "净利润", "回购", "增持", "减持", "并购", "重组", "定增", "中标", "订单", "产能",
    "涨价", "降价", "融资", "上市", "估值",
)


def _has_traceable_link(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def _is_hkex_original_link(value: str) -> bool:
    link = str(value or "").strip().lower()
    return any(domain in link for domain in (
        "hkexnews.hk/",
        "hkex.com.hk/",
    ))


def _is_official_original_link(source: str, value: str) -> bool:
    link = str(value or "").strip().lower()
    if not link.startswith(("http://", "https://")):
        return False
    if source == "港交所公告":
        return _is_hkex_original_link(link)
    if source == "巨潮公告":
        return "cninfo.com.cn/" in link
    if source in {"上交所", "上交所公告"}:
        return any(domain in link for domain in ("sse.com.cn/", "cninfo.com.cn/"))
    if source in {"深交所", "深交所公告"}:
        return any(domain in link for domain in ("szse.cn/", "cninfo.com.cn/"))
    if source in {"北交所", "北交所公告"}:
        return any(domain in link for domain in ("bse.cn/", "cninfo.com.cn/"))
    if source == "交易所公告":
        return any(domain in link for domain in (
            "sse.com.cn/",
            "szse.cn/",
            "bse.cn/",
            "cninfo.com.cn/",
            "hkexnews.hk/",
            "hkex.com.hk/",
        ))
    return False


def _story_key(title: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(title or "")).lower()


def build_independent_source_counts(news_items: List[dict]) -> dict:
    sources_by_story = {}
    for item in news_items:
        key = _story_key(item.get("title", ""))
        source = str(item.get("source") or "").strip()
        if key and source and source not in UNKNOWN_SOURCES:
            sources_by_story.setdefault(key, set()).add(source)
    return {key: len(sources) for key, sources in sources_by_story.items()}


def get_source_time_metadata(item: dict) -> dict:
    """保留来源时间精度；交易所午夜时间只表示公告日期。"""
    source = str(item.get("source") or "").strip()
    ctime = item.get("ctime")
    publish_time = "时间暂缺"
    precision = "unknown"
    if ctime is not None:
        try:
            published = datetime.fromtimestamp(
                float(ctime),
                tz=market_calendar.SHANGHAI_TZ,
            )
            if source in event_classifier.OFFICIAL_SOURCES | DATE_ONLY_DISCLOSURE_SOURCES and (
                published.hour,
                published.minute,
                published.second,
            ) == (0, 0, 0):
                publish_time = published.strftime("%Y-%m-%d")
                precision = "date"
            else:
                publish_time = published.strftime("%Y-%m-%d %H:%M:%S")
                precision = "datetime"
        except (TypeError, ValueError, OSError):
            pass
    else:
        legacy_time = str(item.get("time") or "").strip()
        if legacy_time:
            date_only_match = re.fullmatch(
                r"(\d{2}-\d{2}|\d{4}-\d{2}-\d{2}) 00:00(?::00)?",
                legacy_time,
            )
            if source in event_classifier.OFFICIAL_SOURCES and date_only_match:
                publish_time = date_only_match.group(1)
                precision = "date"
            else:
                publish_time = legacy_time
                precision = str(item.get("timePrecision") or "datetime")

    discovered_at = None
    created_at = item.get("created_at")
    if created_at is not None:
        try:
            discovered_at = datetime.fromtimestamp(
                float(created_at),
                tz=market_calendar.SHANGHAI_TZ,
            ).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            pass
    elif item.get("discoveredAt"):
        discovered_at = str(item["discoveredAt"])

    return {
        "publish_time": publish_time,
        "publish_time_precision": precision,
        "discovered_at": discovered_at,
    }


def _parse_source_datetime(item: dict, now: Optional[datetime] = None) -> Optional[datetime]:
    current = now or datetime.now(market_calendar.SHANGHAI_TZ)
    ctime = item.get("ctime")
    if ctime is not None:
        try:
            return datetime.fromtimestamp(
                float(ctime),
                tz=market_calendar.SHANGHAI_TZ,
            )
        except (TypeError, ValueError, OSError):
            return None

    for key in ("publishedAt", "published_at", "publish_time", "time"):
        text = str(item.get(key) or "").strip()
        if not text or text in {"实时", "今日", "时间暂缺"}:
            continue
        if re.fullmatch(r"\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?", text):
            text = f"{current.year}-{text}"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=market_calendar.SHANGHAI_TZ)
        return parsed.astimezone(market_calendar.SHANGHAI_TZ)
    return None


def _parse_discovered_datetime(item: dict) -> Optional[datetime]:
    created_at = item.get("created_at")
    if created_at is not None:
        try:
            return datetime.fromtimestamp(
                float(created_at),
                tz=market_calendar.SHANGHAI_TZ,
            )
        except (TypeError, ValueError, OSError):
            return None

    for key in ("discoveredAt", "discovered_at", "triggeredAt", "triggered_at"):
        text = str(item.get(key) or "").strip()
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=market_calendar.SHANGHAI_TZ)
        return parsed.astimezone(market_calendar.SHANGHAI_TZ)
    return None


def _source_time_precision(item: dict, published: datetime) -> str:
    source = str(item.get("source") or "").strip()
    for key in ("publishedAt", "published_at", "publish_time", "time"):
        text = str(item.get(key) or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return "date"
    if source in event_classifier.OFFICIAL_SOURCES | DATE_ONLY_DISCLOSURE_SOURCES and (
        published.hour,
        published.minute,
        published.second,
    ) == (0, 0, 0):
        return "date"
    return "datetime"


def is_source_published_today(item: dict, now: Optional[datetime] = None) -> bool:
    """只按来源发布日期判断当天内容；系统发现时间不冒充发布时间。"""
    current = (now or datetime.now(market_calendar.SHANGHAI_TZ)).astimezone(
        market_calendar.SHANGHAI_TZ
    )
    published = _parse_source_datetime(item, current)
    if published is None:
        return False
    if _source_time_precision(item, published) != "date":
        return published.date() == current.date()
    return published.date() == current.date()


def classify_news_source(
    source: str,
    original_link: str = "",
    independent_source_count: int = 1,
) -> dict:
    """按可追溯性和独立来源数量评级；低于 C 级返回未评级/拒绝。"""
    source = str(source or "").strip()
    if (
        source in event_classifier.OFFICIAL_SOURCES
        and _is_official_original_link(source, original_link)
    ):
        credibility_level = "S"
        content_type = "official_announcement"
        verification_status = "来源已核验"
    elif source in DISCLOSURE_AGGREGATORS and _has_traceable_link(original_link):
        credibility_level = "B"
        content_type = "security_announcement"
        verification_status = "单一来源"
    elif source in UNKNOWN_SOURCES or not _has_traceable_link(original_link):
        credibility_level = None
        content_type = "other"
        verification_status = "未评级/拒绝"
    elif independent_source_count >= 2:
        credibility_level = "A"
        content_type = "media_report"
        verification_status = "多源印证"
    elif "研报" in source or "证券" in source or "基金" in source:
        credibility_level = "B"
        content_type = "institution_research"
        verification_status = "单一来源"
    elif source in TRUSTED_MEDIA:
        credibility_level = "B"
        content_type = "media_report"
        verification_status = "单一来源"
    else:
        credibility_level = "C"
        content_type = "other"
        verification_status = "线索级，待核实"

    return {
        "credibility_level": credibility_level,
        "credibility_method": "source_rule",
        "content_type": content_type,
        "verification_status": verification_status,
    }


def classify_news_item(item: dict, source_counts: Optional[dict] = None) -> dict:
    title = str(item.get("title") or "")
    content = str(item.get("content") or "")
    source = str(item.get("source") or "").strip()
    source_counts = source_counts or {}
    source_classification = classify_news_source(
        source,
        original_link=str(item.get("url") or item.get("original_link") or ""),
        independent_source_count=source_counts.get(_story_key(title), 1),
    )
    evidence_level = source_classification["credibility_level"]
    dimensions = (
        event_classifier.classify_event_dimensions(item, evidence_level)
        if evidence_level
        else {"direction": "uncertain", "priority": "P3"}
    )

    combined_text = f"{title}\n{content}"
    region_text = title or content
    region = "国外" if (
        source in GLOBAL_SOURCES
        or any(keyword.lower() in region_text.lower() for keyword in GLOBAL_KEYWORDS)
    ) else "国内"
    if source_classification["content_type"] in {
        "official_announcement",
        "security_announcement",
    }:
        category_key = "company-announcements"
    elif region == "国外" or any(keyword in combined_text for keyword in CONTROL_KEYWORDS):
        category_key = "overseas-controls"
    elif any(keyword in title for keyword in POLICY_KEYWORDS) or (
        any(keyword in source for keyword in POLICY_SOURCE_KEYWORDS)
        and any(keyword in combined_text for keyword in POLICY_KEYWORDS)
    ):
        category_key = "industry-policy"
    else:
        category_key = "industry-dynamics"

    return {
        **source_classification,
        **dimensions,
        "region": region,
        "category_key": category_key,
    }


def is_industry_relevant(
    item: dict,
    classification: Optional[dict] = None,
    watchlist_symbols: Optional[set] = None,
    relevance_keywords: Optional[List[str]] = None,
) -> bool:
    symbol = asset_context.normalize_symbol(item.get("symbol", ""))
    if symbol:
        if watchlist_symbols is not None and symbol in watchlist_symbols:
            return True
        if watchlist_symbols is None:
            try:
                if database.is_in_watchlist(symbol):
                    return True
            except Exception:
                pass
    combined_text = f"{item.get('title') or ''}\n{item.get('content') or ''}".lower()
    keywords = (
        relevance_keywords
        if relevance_keywords is not None
        else list(INDUSTRY_RELEVANCE_KEYWORDS)
    )
    return any(keyword.lower() in combined_text for keyword in keywords)


def is_market_relevant(item: dict, classification: dict) -> bool:
    """行业洞察使用全市场相关性，不受监测列表限制。"""
    if classification.get("content_type") in {
        "official_announcement",
        "security_announcement",
    }:
        return True
    if asset_context.normalize_symbol(item.get("symbol", "")):
        return True
    combined_text = f"{item.get('title') or ''}\n{item.get('content') or ''}".lower()
    return any(keyword.lower() in combined_text for keyword in MARKET_RELEVANCE_KEYWORDS)

def get_real_news_from_db(
    category: str,
    raise_on_error: bool = False,
) -> List[dict]:
    """从数据库读取真实抓取的去重新闻和公告，并转换为前端需要的 RadarNews 格式"""
    try:
        watchlist = database.get_watchlist()
        stock_names = {
            asset_context.normalize_symbol(item.get("stockCode", "")): str(
                item.get("stockName") or ""
            ).strip()
            for item in watchlist
        }
        watchlist_symbols = set(stock_names)
        news_items = []
        seen_items = set()

        def add_items(items):
            for item in items:
                key = str(item.get("url") or item.get("id") or item.get("title") or "").strip()
                if not key or key in seen_items:
                    continue
                seen_items.add(key)
                news_items.append(item)

        for symbol in watchlist_symbols:
            add_items(database.get_latest_crawled_news(symbol, limit=100))
        add_items(database.get_latest_crawled_news("", limit=2000))
        news_items.sort(
            key=lambda item: (
                float(item.get("ctime") or 0),
                float(item.get("created_at") or 0),
            ),
            reverse=True,
        )
        results = []
        source_counts = build_independent_source_counts(news_items)
        for x in news_items:
            if not is_source_published_today(x):
                continue
            title = x.get("title", "")
            content = x.get("content", "") or ""
            source = x.get("source", "未知")
            url = x.get("url", "")
            symbol = x.get("symbol", "")
            time_metadata = get_source_time_metadata(x)
                
            source_classification = classify_news_item(x, source_counts)
            cred = source_classification["credibility_level"]
            if cred is None:
                continue
            if not is_market_relevant(x, source_classification):
                continue

            region = source_classification["region"]
            category_aliases = {
                "policies": "industry-policy",
                "company-events": "company-announcements",
                "export-control": "overseas-controls",
            }
            requested_category = category_aliases.get(category, category)
            if requested_category not in {"all", "domestic", "global"} and source_classification["category_key"] != requested_category:
                continue
            if requested_category == "domestic" and region != "国内":
                continue
            if requested_category == "global" and region != "国外":
                continue
                
            # 提取相关产业链标签
            chains = []
            specific_chain_rules = [
                ("半导体设备", "半导体设备"),
                ("半导体材料", "半导体材料"),
                ("存储芯片", "存储芯片"),
                ("存储器", "存储芯片"),
                ("封测", "先进封测"),
                ("芯片设计", "IC设计"),
                ("硅片", "半导体材料"),
                ("晶圆", "晶圆代工"),
                ("显示面板", "显示面板"),
                ("OLED", "显示面板"),
                ("光电子", "光电子"),
                ("研报", "行业研究"),
            ]
            for kw, ch in specific_chain_rules:
                if (kw in title or kw in content) and ch not in chains:
                    chains.append(ch)
            if not chains:
                for kw, ch in (("芯片", "芯片"), ("半导体", "半导体")):
                    if kw in title or kw in content:
                        chains.append(ch)
                
            # 关联个股
            stocks = []
            if symbol:
                normalized_symbol = asset_context.normalize_symbol(symbol)
                stock_name = stock_names.get(normalized_symbol) or normalized_symbol
                stocks.append(stock_name)
                if not chains:
                    context = asset_context.get_cached_asset_context(normalized_symbol)
                    if context is None:
                        context = asset_context.build_asset_context(
                            normalized_symbol,
                            stock_name,
                        )
                    industry_name = context["industry_name"]
                    if industry_name not in {
                        "行业待确认",
                        "港股行业待确认",
                        "国内ETF",
                    }:
                        chains.append(industry_name)
                
            # 影响分析
            if cred == "S":
                impact = "来源规则识别为官方公告，可能影响公司预期，仍需结合公告原文判断具体影响。"
            elif cred == "A":
                impact = "来源规则识别为机构研报，其内容属于机构观点，不等同于已确认事实。"
            else:
                impact = "该资讯可能影响相关板块情绪，具体影响需结合原文及其他独立来源判断。"
                
            results.append({
                "id": x.get("id") or md5_hash(url),
                "title": title,
                "source": source,
                "publish_time": time_metadata["publish_time"],
                "publish_time_precision": time_metadata["publish_time_precision"],
                "discovered_at": time_metadata["discovered_at"],
                "original_link": url,
                **source_classification,
                "related_chains": chains[:3],
                "related_stocks": stocks,
                "source_summary": content if content else "暂无来源摘要，请查看原文链接",
                "heuristic_impact": impact,
                "impact_method": "heuristic",
            })
        return results
    except Exception as e:
        print(f"Error getting real news from DB: {type(e).__name__}")
        if raise_on_error:
            raise
        return []

def get_integrated_news(
    category: str,
    raise_on_error: bool = False,
) -> List[dict]:
    # 生产接口只返回真实抓取且可追溯的资讯；没有数据时返回空列表。
    return get_real_news_from_db(category, raise_on_error=raise_on_error)


def get_news_feed(category: str) -> dict:
    checked_at = datetime.now(
        market_calendar.SHANGHAI_TZ
    ).isoformat(timespec="seconds")
    try:
        items = get_integrated_news(category, raise_on_error=True)
    except Exception as exc:
        print(f"News feed unavailable: {type(exc).__name__}")
        return {
            "status": "unavailable",
            "data": [],
            "error": "资讯数据读取失败",
            "checkedAt": checked_at,
        }
    return {
        "status": "available" if items else "available_empty",
        "data": items,
        "error": None,
        "checkedAt": checked_at,
    }

@router.get("/latest", response_model=RadarNewsFeed)
def get_latest_news(category: str = "all"):
    return get_news_feed(category)

@router.get("/domestic", response_model=RadarNewsFeed)
def get_domestic_news():
    return get_news_feed("domestic")

@router.get("/global", response_model=RadarNewsFeed)
def get_global_news():
    return get_news_feed("global")

@router.get("/policies", response_model=RadarNewsFeed)
def get_policies_news():
    return get_news_feed("policies")

@router.get("/company-events", response_model=RadarNewsFeed)
def get_company_events_news():
    return get_news_feed("company-events")

@router.get("/export-control", response_model=RadarNewsFeed)
def get_export_control_news():
    return get_news_feed("export-control")
