import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import requests
import json
import math
import threading
from datetime import datetime
import asyncio
import asset_context
import news_api
from news_api import router as news_router
from ai_analysis import router as ai_analysis_router, get_ai_attribution
from alerts_api import router as alerts_router
from pydantic import BaseModel
import database
import alert_repository
import market_calendar
import risk_engine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import requests

app = FastAPI(title="量化监测-股票", version="1.0.0")


def get_allowed_origins() -> list[str]:
    configured = os.getenv("FRONTEND_ORIGINS", "")
    origins = ["http://localhost:4000", "http://127.0.0.1:4000"]
    origins.extend(origin.strip() for origin in configured.split(",") if origin.strip())
    return list(dict.fromkeys(origin for origin in origins if origin != "*"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Backend-Token", "ngrok-skip-browser-warning"],
)


def request_requires_backend_token(request: Request) -> bool:
    path = request.url.path
    if request.method in {"GET", "POST"} and path == "/api/watchlist":
        return True
    if path.startswith("/api/alerts") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if path == "/api/monitoring/health/watchlist-sync" and request.method == "POST":
        return True
    protected_ai_prefixes = ("/api/stock/ai_attribution/",)
    return request.method == "GET" and path.startswith(protected_ai_prefixes)


@app.middleware("http")
async def protect_sensitive_endpoints(request: Request, call_next):
    if not request_requires_backend_token(request):
        return await call_next(request)

    expected_token = os.getenv("BACKEND_API_TOKEN", "").strip()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if not expected_token:
        if app_env == "production":
            return JSONResponse(
                status_code=503,
                content={"detail": "服务端访问保护尚未配置"},
            )
        return await call_next(request)

    provided_token = request.headers.get("X-Backend-Token", "")
    import hmac

    if not hmac.compare_digest(provided_token, expected_token):
        return JSONResponse(status_code=401, content={"detail": "未授权访问"})
    return await call_next(request)

app.include_router(ai_analysis_router)
app.include_router(news_router)
app.include_router(alerts_router)

# ── 后台定时追踪任务 ──────────────────────────────────────────────
AI_ANALYSIS_SLOTS = ("10:30", "11:30", "15:00", "22:00")
_AI_ANALYSIS_LOCK = threading.Lock()
_AI_ANALYSIS_STATE_LOCK = threading.Lock()
_AI_ANALYSIS_COMPLETED_ROUNDS = set()
_AI_ANALYSIS_ROUND_ORDER = []
_EXTENDED_RISK_CACHE: Dict[str, Dict[str, Any]] = {}
_EXTENDED_RISK_CACHE_LOCK = threading.Lock()
_VERIFIED_MARKET_HISTORY_CACHE: Dict[str, Dict[str, Any]] = {}
_VERIFIED_MARKET_HISTORY_CACHE_LOCK = threading.Lock()


def build_ai_analysis_round_id(slot: str, now: Optional[datetime] = None) -> str:
    current = now or datetime.now(market_calendar.SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=market_calendar.SHANGHAI_TZ)
    else:
        current = current.astimezone(market_calendar.SHANGHAI_TZ)
    return f"{current:%Y-%m-%d}:{slot}"


def build_ai_analysis_task_id(symbol: str, round_id: str) -> str:
    date_part, separator, slot_part = round_id.partition(":")
    if not separator or not slot_part:
        return f"{symbol}-{round_id.replace(':', '').replace('-', '')}"
    return f"{symbol}-{date_part.replace('-', '')}-{slot_part.replace(':', '')}"


def _record_ai_round_failure(symbol: str, trigger: str, error: Exception) -> None:
    existing = database.get_analysis_history_by_trigger(symbol, trigger)
    if existing is not None and existing["full_json"].get("analysisStatus") != "running":
        return
    failure = {
        "stockName": symbol,
        "stockCode": symbol,
        "changePercent": None,
        "score": None,
        "evidenceChain": {},
        "futureTrendPrediction": "暂无推演内容",
        "plainEnglishSummary": "AI 分析调用失败，本轮未生成评分或结论。",
        "aiJudgment": f"大模型接口调用失败: {type(error).__name__}",
        "credibility": "错误",
        "riskNotice": "请检查后台日志。",
        "analysisStatus": "failed",
    }
    if not database.complete_analysis_trigger(
        symbol,
        trigger,
        failure["plainEnglishSummary"],
        failure,
    ):
        database.save_analysis_history(
            symbol,
            trigger,
            failure["plainEnglishSummary"],
            failure,
        )


def _ai_round_is_completed(round_id: str) -> bool:
    with _AI_ANALYSIS_STATE_LOCK:
        return round_id in _AI_ANALYSIS_COMPLETED_ROUNDS


def _mark_ai_round_completed(round_id: str) -> None:
    with _AI_ANALYSIS_STATE_LOCK:
        if round_id in _AI_ANALYSIS_COMPLETED_ROUNDS:
            return
        _AI_ANALYSIS_COMPLETED_ROUNDS.add(round_id)
        _AI_ANALYSIS_ROUND_ORDER.append(round_id)
        while len(_AI_ANALYSIS_ROUND_ORDER) > 32:
            expired = _AI_ANALYSIS_ROUND_ORDER.pop(0)
            _AI_ANALYSIS_COMPLETED_ROUNDS.discard(expired)


def run_collection_sync():
    import monitoring_health
    monitoring_health.record_task_started("generalNews")
    try:
        import news_collector
        item_count = news_collector.run_collection()
        monitoring_health.record_task_success("generalNews", item_count=item_count)
    except Exception as e:
        monitoring_health.record_task_failure("generalNews", e)
        print(f"[{datetime.now()}] 定时资讯采集失败: {e}")

async def auto_collect_news():
    print(f"[{datetime.now()}] 触发定时多源资讯采集任务...")
    await asyncio.to_thread(run_collection_sync)


def run_official_alert_collection_sync():
    import monitoring_health
    monitoring_health.record_task_started("officialAnnouncements")
    try:
        import news_collector
        created_count = news_collector.run_official_collection()
        monitoring_health.record_task_success(
            "officialAnnouncements",
            item_count=created_count,
        )
    except Exception as e:
        monitoring_health.record_task_failure("officialAnnouncements", e)
        print(f"[{datetime.now()}] 官方公告提醒采集失败: {e}")


async def auto_collect_official_alerts():
    print(f"[{datetime.now()}] 触发监测列表官方公告提醒任务...")
    await asyncio.to_thread(run_official_alert_collection_sync)


def run_email_retry_sync():
    import monitoring_health
    monitoring_health.record_task_started("emailRetry")
    try:
        import notification_service
        retry_count = notification_service.retry_due_email_deliveries()
        monitoring_health.record_task_success("emailRetry", item_count=retry_count)
    except Exception as e:
        monitoring_health.record_task_failure("emailRetry", e)
        print(f"[{datetime.now()}] 提醒邮件重试失败: {e}")


async def auto_retry_alert_emails():
    await asyncio.to_thread(run_email_retry_sync)


def run_market_risk_collection_sync():
    import monitoring_health
    monitoring_health.record_task_started("marketRisk")
    processed_count = 0
    first_error = None
    watchlist = database.get_watchlist()
    trading_symbols = set()
    for item in watchlist:
        symbol = asset_context.normalize_symbol(item.get("stockCode", ""))
        stock_name = str(item.get("stockName") or "").strip()
        context = asset_context.build_asset_context(
            symbol,
            stock_name,
        )
        if not symbol.isdigit() or context["asset_type"] not in {
            "a_stock",
            "hk_stock",
            "domestic_etf",
        }:
            monitoring_health.record_mapping_failure(
                symbol,
                stock_name,
                "股票代码无法识别为A股、港股或国内ETF",
            )
            continue
        if not stock_name:
            monitoring_health.record_mapping_failure(
                symbol,
                stock_name,
                "后端监测列表缺少公司或基金名称",
            )
            continue
        try:
            overview = get_stock_overview(symbol)
            if overview.get("marketStatusCode") == "trading":
                trading_symbols.add(symbol)
            if not str(overview.get("name") or "").strip():
                monitoring_health.record_mapping_failure(
                    symbol,
                    stock_name,
                    "行情源未返回可核验的证券名称",
                )
            source_time = str(overview.get("sourceTime") or "")
            if overview.get("marketStatusCode") == "trading" and source_time:
                extended = get_extended_risk_inputs(
                    symbol,
                    stock_name,
                    source_time,
                )
                details = overview.get("details") or {}
                risk_engine.process_market_snapshot({
                    "symbol": symbol,
                    "stock_name": stock_name,
                    "market": context["market"],
                    "source_time": source_time,
                    "fetched_at": overview.get("fetchedAt"),
                    "change_percent": overview.get("changePercent"),
                    "close": overview.get("latestPrice"),
                    "high": details.get("high"),
                    "low": details.get("low"),
                    "previous_close": details.get("previousClose"),
                    "volume_ratio": details.get("volumeRatio"),
                    "turnover_rate": details.get("turnoverRate"),
                    "turnover_amount": details.get("turnoverAmountValue"),
                    "verified_history": extended.get("verified_history") or [],
                }, persist_snapshot=True, create_alert=True)
                linkage_snapshot = {
                    "symbol": symbol,
                    "stock_name": stock_name,
                    "source_time": source_time,
                    "fetched_at": overview.get("fetchedAt"),
                    "sector": extended.get("sector") or {"status": "unavailable"},
                    "overseas": extended.get("overseas") or [],
                }
                extended["linkage_snapshot"] = linkage_snapshot
                with _EXTENDED_RISK_CACHE_LOCK:
                    cached = _EXTENDED_RISK_CACHE.get(symbol)
                    if cached:
                        cached["data"] = extended
                risk_engine.process_linkage_snapshot(
                    linkage_snapshot,
                    create_alert=True,
                )
            processed_count += 1
        except Exception as e:
            if first_error is None:
                first_error = e
            print(f"[{datetime.now()}] {symbol} 量价风险采样失败: {e}")
    monitoring_health.audit_stale_watchlist(
        watchlist,
        trading_symbols=trading_symbols,
        expected_seconds=180,
    )
    if first_error is not None:
        monitoring_health.record_task_failure("marketRisk", first_error)
    else:
        monitoring_health.record_task_success("marketRisk", item_count=processed_count)


async def auto_collect_market_risk():
    print(f"[{datetime.now()}] 触发监测列表量价风险采样任务...")
    await asyncio.to_thread(run_market_risk_collection_sync)

def run_ai_analysis_round_sync(round_id: str) -> str:
    if _ai_round_is_completed(round_id):
        return "skipped_duplicate"
    if not _AI_ANALYSIS_LOCK.acquire(blocking=False):
        _mark_ai_round_completed(round_id)
        print(f"[{datetime.now()}] AI 轮次 {round_id} 跳过：上一轮仍在执行。")
        return "skipped_busy"

    import monitoring_health
    monitoring_health.record_task_started("aiAnalysis")
    had_errors = False
    processed_count = 0
    try:
        if _ai_round_is_completed(round_id):
            return "skipped_duplicate"
        for item in database.get_watchlist():
            symbol = str(item.get("stockCode") or "").strip()
            if not symbol:
                continue
            task_id = build_ai_analysis_task_id(symbol, round_id)
            trigger = f"auto:{task_id}"
            try:
                print(f"[{datetime.now()}] 开始自动分析 {symbol}，轮次 {round_id}...")
                result = get_ai_attribution(symbol, trigger=trigger)
                if isinstance(result, dict) and result.get("analysisStatus") == "failed":
                    had_errors = True
                    print(f"[{datetime.now()}] {symbol} 自动分析失败，本轮不重试。")
                else:
                    print(f"[{datetime.now()}] {symbol} 自动分析完成。")
                processed_count += 1
            except Exception as e:
                had_errors = True
                try:
                    _record_ai_round_failure(symbol, trigger, e)
                except Exception as record_error:
                    print(f"[{datetime.now()}] {symbol} AI 失败记录写入失败: {record_error}")
                print(f"[{datetime.now()}] {symbol} 自动分析失败，本轮不重试: {e}")
        if had_errors:
            monitoring_health.record_task_failure(
                "aiAnalysis",
                RuntimeError("one_or_more_analysis_tasks_failed"),
            )
            return "completed_with_errors"
        monitoring_health.record_task_success(
            "aiAnalysis",
            item_count=processed_count,
        )
        return "completed"
    finally:
        _mark_ai_round_completed(round_id)
        _AI_ANALYSIS_LOCK.release()


async def auto_analyze_watchlist(slot: str):
    round_id = build_ai_analysis_round_id(slot)
    print(f"[{datetime.now()}] 触发后台自动分析追踪任务，轮次 {round_id}...")
    await asyncio.to_thread(run_ai_analysis_round_sync, round_id)

def run_watchlist_industry_update_sync():
    import monitoring_health
    monitoring_health.record_task_started("industryDynamics")
    items = database.get_watchlist()
    processed_count = 0
    first_error = None
    for item in items:
        symbol = item.get("stockCode")
        if not symbol: continue
        try:
            # 获取所属行业名称
            company_data = get_company_info(symbol)
            industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
            context = asset_context.register_asset_context(
                asset_context.build_asset_context(
                    symbol,
                    item.get("stockName", ""),
                    industry_tags,
                )
            )
            industry_name = context["industry_name"]
            
            print(f"[{datetime.now()}] 开始自动更新 {symbol} ({industry_name}) 行业动态...")
            # 强制刷新缓存
            fetch_real_industry_dynamics(
                symbol,
                industry_name,
                force_refresh=True,
                search_terms=context["search_terms"],
            )
            print(f"[{datetime.now()}] {symbol} 行业动态更新完成。")
            processed_count += 1
        except Exception as e:
            if first_error is None:
                first_error = e
            print(f"[{datetime.now()}] {symbol} 自动更新行业动态失败: {e}")
    if first_error is not None:
        monitoring_health.record_task_failure("industryDynamics", first_error)
    else:
        monitoring_health.record_task_success(
            "industryDynamics",
            item_count=processed_count,
        )


async def auto_update_watchlist_industry():
    print(f"[{datetime.now()}] 触发后台自动更新行业与政策监控任务...")
    await asyncio.to_thread(run_watchlist_industry_update_sync)

@app.on_event("startup")
def start_scheduler():
    scheduler = AsyncIOScheduler()
    # 启动时立即异步拉取一次新闻与行业监控大模型数据
    scheduler.add_job(auto_collect_news, 'date', run_date=datetime.now())
    scheduler.add_job(auto_update_watchlist_industry, 'date', run_date=datetime.now())
    scheduler.add_job(auto_collect_official_alerts, 'date', run_date=datetime.now())
    # 随后每 30 分钟拉取一次新闻
    scheduler.add_job(auto_collect_news, 'interval', minutes=30)
    # 官方公告独立高频扫描；夜间降低频率，避免普通媒体跟随高频抓取。
    scheduler.add_job(
        auto_collect_official_alerts,
        'cron',
        hour='7-22',
        minute='*/5',
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        auto_collect_official_alerts,
        'cron',
        hour='0-6,23',
        minute='0,30',
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        auto_retry_alert_emails,
        'interval',
        minutes=1,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        auto_collect_market_risk,
        'cron',
        day_of_week='mon-fri',
        hour='9-15',
        minute='*',
        max_instances=1,
        coalesce=True,
    )
    # 随后每 1 小时自动拉取更新一次行业动态
    scheduler.add_job(auto_update_watchlist_industry, 'interval', hours=1)
    # 每天 10:30、11:30、15:00、22:00；轮次在后台线程内执行且不排队。
    for slot in AI_ANALYSIS_SLOTS:
        hour, minute = (int(part) for part in slot.split(":"))
        scheduler.add_job(
            auto_analyze_watchlist,
            'cron',
            hour=hour,
            minute=minute,
            args=[slot],
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    print("后台自动化追踪与资讯采集调度器已启动。")
# ── 公共工具函数 ──────────────────────────────────────────────

def get_prefix(symbol: str) -> str:
    """
    根据股票代码返回对应的市场前缀 (sh, sz, hk)
    """
    return asset_context.quote_prefix(symbol)

def get_em_prefix(symbol: str) -> str:
    """根据代码返回东方财富的 secid 前缀"""
    prefix = asset_context.quote_prefix(symbol)
    if prefix == "hk":
        return "116."
    if prefix == "sh":
        return "1."
    return "0."

def get_em_data(url: str, timeout: int = 3) -> requests.Response:
    """用安全的 HTTPS 协议和 Referer 头请求东方财富接口，绕过防爬拦截"""
    url = url.replace("http://push2.eastmoney.com", "https://push2.eastmoney.com")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
        "Host": "push2.eastmoney.com"
    }
    return requests.get(url, headers=headers, timeout=timeout)


def calc_ma(closes: list[float], window: int) -> list:
    """计算移动平均线，数据不足时返回 None"""
    result = []
    for i in range(len(closes)):
        if i < window - 1:
            result.append(None)
        else:
            avg = sum(closes[i - window + 1: i + 1]) / window
            result.append(round(avg, 3))
    return result


def format_market_timestamp(value: str) -> Optional[str]:
    """将行情源时间统一为可读格式；缺失或格式异常时返回 None。"""
    if not value:
        return None
    if "/" in value and ":" in value:
        return value.replace("/", "-")
    if len(value) >= 14 and value[:14].isdigit():
        return (
            f"{value[:4]}-{value[4:6]}-{value[6:8]} "
            f"{value[8:10]}:{value[10:12]}:{value[12:14]}"
        )
    return None


def get_market_status_for_symbol(symbol: str) -> dict:
    market = database.get_market_by_symbol(symbol)
    status, calendar_day = market_calendar.get_market_status(market)
    return {
        "marketStatus": status.label,
        "marketStatusCode": status.code,
        "market": market,
        "calendarSource": calendar_day.source_url,
        "calendarCheckedAt": calendar_day.checked_at,
        "calendarError": calendar_day.error,
    }


def parse_optional_float(value: Any) -> Optional[float]:
    """保留真实 0；缺失、空字符串或无效数字返回 None。"""
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def format_financial_money(value: Any) -> str:
    number = parse_optional_float(value)
    if number is None:
        return "-"
    magnitude = abs(number)
    if magnitude >= 1e8:
        return f"{number / 1e8:.2f}亿"
    if magnitude >= 1e4:
        return f"{number / 1e4:.2f}万"
    return f"{number:.2f}"


def format_financial_percent(value: Any) -> str:
    number = parse_optional_float(value)
    return f"{number:.2f}%" if number is not None else "-"


def classify_report_type_by_date(date_text: str) -> tuple[str, str]:
    value = str(date_text or "")[:10]
    year = value[:4]
    if value.endswith("12-31"):
        return f"{year}年报", "年报"
    if value.endswith("06-30"):
        return f"{year}中报", "中报"
    if value.endswith("03-31"):
        return f"{year}一季报", "一季报"
    if value.endswith("09-30"):
        return f"{year}三季报", "三季报"
    return f"{year}财报", "财报"


def parse_sina_money_flow(payload: dict) -> Optional[float]:
    """解析新浪资金流接口报告的主力净额；缺失时保持 None。"""
    return parse_optional_float(payload.get("netamount"))


def find_sina_industry_node(nodes: Any, industry_name: str) -> Optional[str]:
    """从新浪真实板块目录中查找与公司行业一致的板块代码。"""
    if not industry_name:
        return None
    candidates = []

    def walk(value: Any) -> None:
        if not isinstance(value, list):
            return
        if (
            len(value) >= 3
            and isinstance(value[0], str)
            and isinstance(value[2], str)
            and value[2].startswith(("sw1_", "sw2_", "sw3_", "new_"))
        ):
            candidates.append((value[0].strip(), value[2]))
        for child in value:
            walk(child)

    walk(nodes)
    for name, node in candidates:
        if name == industry_name:
            return node
    for name, node in candidates:
        if industry_name in name or name in industry_name:
            return node
    return None


def parse_sina_peer_codes(rows: list[dict], symbol: str) -> list[str]:
    current = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
    peers = []
    for row in rows:
        code = str(row.get("code", "")).strip().zfill(6)
        if code.isdigit() and len(code) == 6 and code != current and code not in peers:
            peers.append(code)
    return peers


def get_sina_data(url: str, timeout: int = 5) -> requests.Response:
    """新浪国内公开接口直连，不继承本机代理地址。"""
    session = requests.Session()
    session.trust_env = False
    return session.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        },
        timeout=timeout,
    )


def get_sina_stock_fund_flow(symbol: str) -> Optional[float]:
    code = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    prefix = "sh" if code.startswith("6") else "sz"
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"MoneyFlow.ssi_ssfx_flzjtj?daima={prefix}{code}"
    )
    try:
        response = get_sina_data(url)
        response.raise_for_status()
        return parse_sina_money_flow(response.json())
    except Exception as exc:
        print(f"Failed to fetch Sina fund flow for {code}: {exc}")
        return None


def get_a_share_industry_peer_codes(symbol: str, industry_name: str) -> list[str]:
    """从新浪申万行业目录获取真实同行代码，失败时返回空列表。"""
    if not industry_name:
        return []
    try:
        nodes_url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodes"
        )
        nodes_response = get_sina_data(nodes_url)
        nodes_response.raise_for_status()
        node = find_sina_industry_node(nodes_response.json(), industry_name)
        if not node:
            return []
        detail_url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page=1&num=300&sort=symbol&asc=1&node={node}"
            "&symbol=&_s_r_a=page"
        )
        detail_response = get_sina_data(detail_url)
        detail_response.raise_for_status()
        return parse_sina_peer_codes(detail_response.json(), symbol)
    except Exception as exc:
        print(f"Failed to fetch Sina industry constituents for {industry_name}: {exc}")
        return []


def fetch_eastmoney_fund_history(symbol: str) -> list[dict]:
    market = asset_context.quote_prefix(symbol)
    market_id = {"sh": 1, "sz": 0, "bj": 0}.get(market)
    if market_id is None:
        return []
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        params={
            "lmt": "0",
            "klt": "101",
            "secid": f"{market_id}.{symbol}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=8,
    )
    response.raise_for_status()
    content = ((response.json().get("data") or {}).get("klines") or [])
    rows = []
    for item in content:
        fields = str(item).split(",")
        if len(fields) < 12:
            continue
        rows.append({
            "trade_date": fields[0][:10],
            "fund_close": parse_optional_float(fields[11]),
            "fund_flow": parse_optional_float(fields[1]),
        })
    return rows


def get_verified_market_history(symbol: str, expected_trade_date: str) -> list[dict]:
    context = asset_context.build_asset_context(symbol)
    if context["asset_type"] != "a_stock":
        return []
    try:
        fund_rows = fetch_eastmoney_fund_history(symbol)
    except Exception as exc:
        print(f"Failed to fetch verified fund history for {symbol}: {exc}")
        fund_rows = []
    try:
        kline_rows = [
            {
                "trade_date": str(item.get("time") or "")[:10],
                "close": item.get("close"),
                "ma5": item.get("ma5"),
                "ma10": item.get("ma10"),
                "ma20": item.get("ma20"),
            }
            for item in get_stock_kline(symbol, period="day").get("data", [])
        ]
    except Exception as exc:
        print(f"Failed to fetch verified K-line history for {symbol}: {exc}")
        kline_rows = []
    return risk_engine.merge_verified_market_history(
        fund_rows,
        kline_rows,
        expected_trade_date=expected_trade_date,
    )


def get_cached_verified_market_history(
    symbol: str,
    expected_trade_date: str,
) -> list[dict]:
    import time

    now_monotonic = time.monotonic()
    with _VERIFIED_MARKET_HISTORY_CACHE_LOCK:
        cached = _VERIFIED_MARKET_HISTORY_CACHE.get(symbol)
        if (
            cached
            and cached.get("expected_trade_date") == expected_trade_date
            and now_monotonic - float(cached.get("cached_at") or 0) < 180
        ):
            return [dict(item) for item in cached.get("data") or []]

    rows = get_verified_market_history(symbol, expected_trade_date)
    with _VERIFIED_MARKET_HISTORY_CACHE_LOCK:
        _VERIFIED_MARKET_HISTORY_CACHE[symbol] = {
            "expected_trade_date": expected_trade_date,
            "cached_at": now_monotonic,
            "data": [dict(item) for item in rows],
        }
    return rows


def get_constituent_quote_snapshot(symbols: list[str]) -> list[dict]:
    codes = list(dict.fromkeys(
        asset_context.normalize_symbol(symbol)
        for symbol in symbols
        if asset_context.normalize_symbol(symbol)
    ))
    rows = []
    for start in range(0, len(codes), 50):
        batch = codes[start:start + 50]
        query = ",".join(
            f"{asset_context.quote_prefix(code)}{code}"
            for code in batch
        )
        try:
            response = requests.get(f"http://qt.gtimg.cn/q={query}", timeout=5)
            response.encoding = "gbk"
            for line in response.text.split(";"):
                if "=" not in line:
                    continue
                fields = line.split("=", 1)[1].strip().strip('"').split("~")
                if len(fields) <= 45:
                    continue
                code = asset_context.normalize_symbol(fields[2])
                change_percent = parse_optional_float(fields[32])
                market_cap = parse_optional_float(fields[45])
                if not code or change_percent is None:
                    continue
                rows.append({
                    "symbol": code,
                    "name": fields[1],
                    "change_percent": change_percent,
                    "market_cap": market_cap,
                    "source_time": format_market_timestamp(fields[30]),
                })
        except Exception as exc:
            print(f"Failed to fetch constituent quotes: {exc}")
    return rows


def get_verified_sector_risk_snapshot(
    symbol: str,
    industry_name: str,
) -> dict:
    if asset_context.build_asset_context(symbol)["asset_type"] != "a_stock":
        return {
            "status": "unavailable",
            "name": industry_name,
            "reason": "当前资产尚无经过验证的板块成分口径",
        }
    try:
        import akshare as ak

        sector_rows = ak.stock_fund_flow_industry(symbol="即时").to_dict("records")
        constituents = list(dict.fromkeys([
            symbol,
            *get_a_share_industry_peer_codes(symbol, industry_name),
        ]))
        if not constituents:
            raise LookupError("未匹配到真实板块成分")
        quotes = get_constituent_quote_snapshot(constituents)
        return risk_engine.build_verified_sector_snapshot(
            industry_name,
            sector_rows,
            quotes,
            expected_constituents=len(constituents),
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "name": industry_name,
            "change_percent": None,
            "advancers": None,
            "total": None,
            "leader": None,
            "fund_flow": {"verified": False},
            "reason": f"板块真实数据获取失败：{type(exc).__name__}",
        }


def get_exact_overseas_quote_snapshot(mappings: list[dict]) -> list[dict]:
    if not mappings:
        return []
    query = ",".join(item["query_symbol"] for item in mappings)
    try:
        response = get_sina_data(f"https://hq.sinajs.cn/list={query}")
        response.encoding = "gbk"
        by_query = {item["query_symbol"].lower(): item for item in mappings}
        results = []
        for line in response.text.splitlines():
            if "hq_str_" not in line or '"' not in line:
                continue
            query_symbol = line.split("hq_str_", 1)[1].split("=", 1)[0].lower()
            mapping = by_query.get(query_symbol)
            fields = line.split('"', 2)[1].split(",")
            change_percent = parse_optional_float(fields[2] if len(fields) > 2 else None)
            if mapping is None or change_percent is None:
                continue
            results.append({
                **mapping,
                "change_percent": change_percent,
                "source_time": fields[3] if len(fields) > 3 else None,
                "market_time": fields[26] if len(fields) > 26 else None,
                "source": "新浪财经海外行情",
            })
        return results
    except Exception as exc:
        print(f"Failed to fetch exact overseas linkage quotes: {exc}")
        return []


def get_extended_risk_inputs(
    symbol: str,
    stock_name: str,
    source_time: str,
) -> dict:
    import time

    source_date = str(source_time or "")[:10]
    now_monotonic = time.monotonic()
    with _EXTENDED_RISK_CACHE_LOCK:
        cached = _EXTENDED_RISK_CACHE.get(symbol)
        if (
            cached
            and cached.get("source_date") == source_date
            and now_monotonic - float(cached.get("cached_at") or 0) < 180
        ):
            return dict(cached["data"])

    company = get_company_info(symbol)
    company_info = company.get("companyInfo") or {}
    context = asset_context.register_asset_context(
        asset_context.build_asset_context(
            symbol,
            stock_name,
            company_info.get("industryTags") or [],
        )
    )
    business_text = "；".join([
        str(company_info.get("mainBusiness") or ""),
        *[str(item) for item in company_info.get("coreProducts") or []],
    ])
    mappings = risk_engine.build_exact_overseas_mappings(context, business_text)
    data = {
        "verified_history": get_cached_verified_market_history(symbol, source_date),
        "sector": get_verified_sector_risk_snapshot(
            symbol,
            context["industry_name"],
        ),
        "overseas": get_exact_overseas_quote_snapshot(mappings),
        "context": context,
    }
    with _EXTENDED_RISK_CACHE_LOCK:
        _EXTENDED_RISK_CACHE[symbol] = {
            "source_date": source_date,
            "cached_at": now_monotonic,
            "data": data,
        }
    return dict(data)


def get_cached_linkage_risk(symbol: str) -> Optional[dict]:
    normalized_symbol = asset_context.normalize_symbol(symbol)
    with _EXTENDED_RISK_CACHE_LOCK:
        cached = _EXTENDED_RISK_CACHE.get(normalized_symbol)
        if cached:
            data = cached.get("data") or {}
            linkage_snapshot = data.get("linkage_snapshot")
            if linkage_snapshot:
                return risk_engine.evaluate_linkage_risk(linkage_snapshot)
    trade_date = datetime.now(market_calendar.SHANGHAI_TZ).strftime("%Y-%m-%d")
    return alert_repository.get_latest_linkage_state(
        normalized_symbol,
        trade_date,
    )


# ── 接口实现 ──────────────────────────────────────────────────

class WatchlistRequest(BaseModel):
    items: list[dict]

@app.get("/api/watchlist")
def get_watchlist():
    return {"data": database.get_watchlist()}

@app.post("/api/watchlist")
def update_watchlist(req: WatchlistRequest):
    success = database.replace_watchlist(req.items)
    if not success:
        raise HTTPException(status_code=400, detail="保存监测列表失败")
    return {"message": "success", "data": database.get_watchlist()}

@app.get("/api/stock/ai_history/{symbol}")
def get_ai_history(symbol: str):
    start_time, end_time = database.get_trading_session_bounds_for_symbol(symbol)
    bounds = None
    calendar_status = "unknown"
    if start_time is not None and end_time is not None:
        bounds = {"start": start_time, "end": end_time}
        calendar_status = "available"
    return {
        "data": database.get_today_analysis_history(symbol),
        "bounds": bounds,
        "calendarStatus": calendar_status,
    }

@app.get("/api/stock/ai_history_all/{symbol}")
def get_all_ai_history(symbol: str):
    return {"data": database.get_all_analysis_history(symbol)}

@app.get("/api/stock/batch_overview")
def get_batch_overview(symbols: str):
    """
    获取多只股票的实时基本信息，以逗号分隔
    """
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"data": []}
        
    query_list = []
    for symbol in symbol_list:
        code = asset_context.normalize_symbol(symbol)
        if code.isdigit():
            query_list.append(f"{asset_context.quote_prefix(symbol)}{code}")
            
    url = f"http://qt.gtimg.cn/q={','.join(query_list)}"
    
    results = []
    fetched_at = datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds")
    try:
        resp = requests.get(url, timeout=5)
        # response encoding is gbk
        resp.encoding = 'gbk'
        content = resp.text
        lines = content.split(';')
        for line in lines:
            if not line.strip():
                continue
            parts = line.split('=')
            if len(parts) != 2:
                continue
            v = parts[1].strip().strip('"').split('~')
            if len(v) > 32:
                name = v[1]
                code = v[2]
                price = parse_optional_float(v[3] if len(v) > 3 else None)
                change_pct = parse_optional_float(v[32] if len(v) > 32 else None)
                is_hk = len(code) == 5 and code.isdigit()
                raw_amount = parse_optional_float(v[37] if len(v) > 37 else None)
                raw_volume = parse_optional_float(v[36] if len(v) > 36 else None)
                
                results.append({
                    "symbol": code,
                    "name": name,
                    "price": price,
                    "changePct": f"{change_pct}%" if change_pct is not None else None,
                    "changePercent": change_pct,
                    "sourceTime": format_market_timestamp(v[30] if len(v) > 30 else ""),
                    "fetchedAt": fetched_at,
                    "amount": raw_amount if is_hk or raw_amount is None else raw_amount * 10000,
                    "volume": raw_volume if is_hk or raw_volume is None else raw_volume * 100,
                })
                
        # 新浪公开接口逐只获取真实主力资金净额；港股暂不填充无可靠口径的数据。
        if results:
            for item in results:
                code = item["symbol"]
                if len(code) != 6:
                    item["fundFlowTimeScope"] = "不适用（暂无港股资金流口径）"
                    continue
                flow_market_status = get_market_status_for_symbol(code)
                item["fundFlowTimeScope"] = describe_undated_fund_flow_scope(
                    flow_market_status["marketStatusCode"]
                )
                raw_flow = get_sina_stock_fund_flow(code)
                if raw_flow is not None:
                    flow_yi = raw_flow / 100000000.0
                    if flow_yi > 0:
                        item["fundFlow"] = f"净流入 {round(flow_yi, 2)} 亿元"
                    elif flow_yi < 0:
                        item["fundFlow"] = f"净流出 {abs(round(flow_yi, 2))} 亿元"
                    else:
                        item["fundFlow"] = "主力资金净流 0.0 亿元"

        for item in results:
            try:
                stored_risk = alert_repository.get_latest_signal_state(item["symbol"])
                live_risk = risk_engine.evaluate_market_risk({
                    "symbol": item["symbol"],
                    "market": "hk" if len(item["symbol"]) == 5 else "cn",
                    "change_percent": item.get("changePercent"),
                    "high": None,
                    "low": None,
                    "previous_close": None,
                    "volume_ratio": None,
                    "turnover_rate": None,
                    "turnover_amount": item.get("amount"),
                }, [])
                if live_risk.get("priority") == "P1":
                    if stored_risk and stored_risk.get("turnoverRisk"):
                        live_risk["turnoverRisk"] = stored_risk["turnoverRisk"]
                    item["risk"] = live_risk
                elif stored_risk is not None:
                    item["risk"] = stored_risk
                elif live_risk.get("priority") is not None:
                    item["risk"] = live_risk
                else:
                    item["risk"] = None
            except Exception:
                item["risk"] = None

        return {"data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stock/overview/{symbol}")
def get_stock_overview(symbol: str):
    """
    获取股票实时行情（腾讯财经）
    """
    # 尝试中文/拼音解析为股票代码
    if symbol and not (symbol.isalnum() and not any('\u4e00' <= c <= '\u9fff' for c in symbol)):
        try:
            suggest_url = f"http://suggest3.sinajs.cn/suggest/type=&key={symbol}"
            s_resp = requests.get(suggest_url, timeout=2)
            s_resp.encoding = 'gbk'
            if '=";' not in s_resp.text:
                data = s_resp.text.split('="')[1].split('";')[0]
                if data:
                    first_match = data.split(';')[0].split(',')
                    if len(first_match) >= 4:
                        symbol_resolved = first_match[3]
                        if symbol_resolved.startswith(('sh', 'sz', 'hk', 'bj')):
                            symbol = symbol_resolved[2:]
                        else:
                            symbol = symbol_resolved
        except:
            pass

    prefix = get_prefix(symbol)
    
    # 1. 获取基本行情
    try:
        quote_symbol = symbol.lower() if symbol.lower().startswith("hk") else f"{prefix}{symbol}"
        url = f"http://qt.gtimg.cn/q={quote_symbol}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        resp.encoding = 'gbk'
        text = resp.text
        
        # 容错：如果用户输入6位数（如009863）被当成A股，但其实是多打了一个0的港股(09863)
        if 'v_pv_none_match="1"' in text and symbol.startswith("0") and len(symbol) == 6:
            real_hk_code = symbol[1:] # 截掉第一个0
            url = f"http://qt.gtimg.cn/q=hk{real_hk_code}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
            resp.encoding = 'gbk'
            text = resp.text
            if 'v_pv_none_match="1"' not in text:
                symbol = f"hk{real_hk_code}" # 修正 symbol 供后续使用
                prefix = ""
        
        if not text or 'v_pv_none_match="1"' in text:
            raise HTTPException(status_code=400, detail="腾讯财经返回数据格式异常，可能是无效代码")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"数据源请求失败: {e}")

    if "=" not in text or "~" not in text:
        raise HTTPException(status_code=503, detail="腾讯财经返回数据格式异常，可能是无效代码")

    data_str = text.split("=")[1].strip().strip('";')
    fields = data_str.split("~")

    is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)

    try:
        name = fields[1] if len(fields) > 1 and fields[1] else symbol
        latest_price = parse_optional_float(fields[3] if len(fields) > 3 else None)
        prev_close = parse_optional_float(fields[4] if len(fields) > 4 else None)
        open_price = parse_optional_float(fields[5] if len(fields) > 5 else None)
        change_amount = parse_optional_float(fields[31] if len(fields) > 31 else None)
        change_percent = parse_optional_float(fields[32] if len(fields) > 32 else None)
        high_price = parse_optional_float(fields[33] if len(fields) > 33 else None)
        low_price = parse_optional_float(fields[34] if len(fields) > 34 else None)
        raw_volume = parse_optional_float(fields[36] if len(fields) > 36 else None)
        raw_turnover = parse_optional_float(fields[37] if len(fields) > 37 else None)
        volume = raw_volume / 10000.0 if raw_volume is not None else None
        if raw_turnover is None:
            turnover = None
        else:
            turnover = raw_turnover / 100000000.0 if is_hk else raw_turnover / 10000.0
        turnover_rate_index = 59 if is_hk else 38
        turnover_rate = parse_optional_float(
            fields[turnover_rate_index] if len(fields) > turnover_rate_index else None
        )
        volume_ratio = parse_optional_float(fields[49] if len(fields) > 49 else None)
        pe_ratio = parse_optional_float(fields[39] if len(fields) > 39 else None)
        market_cap = parse_optional_float(fields[45] if len(fields) > 45 else None)
        update_time = fields[30] if len(fields) > 30 else ""
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"数据解析失败: {e}")

    source_time = format_market_timestamp(update_time)
    fetched_at = datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds")

    if change_amount is None:
        status = "unknown"
    else:
        status = "up" if change_amount > 0 else ("down" if change_amount < 0 else "flat")

    market_status = get_market_status_for_symbol(symbol)

    try:
        is_monitored = database.is_in_watchlist(symbol)
        monitoring_status = "active" if is_monitored else "inactive"
        monitoring_error = None
    except Exception:
        is_monitored = None
        monitoring_status = "unknown"
        monitoring_error = "后台监测状态读取失败"

    context = asset_context.build_asset_context(symbol, name)
    market = context["market"]
    market_snapshot = {
        "symbol": symbol,
        "stock_name": name,
        "market": market,
        "source_time": source_time,
        "fetched_at": fetched_at,
        "change_percent": change_percent,
        "high": high_price,
        "low": low_price,
        "previous_close": prev_close,
        "volume_ratio": volume_ratio,
        "turnover_rate": turnover_rate,
        "turnover_amount": raw_turnover,
    }
    try:
        if is_monitored is not True:
            market_risk = risk_engine.evaluate_market_risk(market_snapshot, [])
        else:
            verified_history = get_cached_verified_market_history(
                symbol,
                source_time[:10],
            ) if source_time else []
            market_risk = risk_engine.process_market_snapshot(
                {**market_snapshot, "verified_history": verified_history},
                persist_snapshot=True,
                create_alert=market_status.get("marketStatusCode") == "trading",
            )
    except Exception:
        market_risk = {
            "riskStatus": "unavailable",
            "priority": None,
            "direction": "neutral",
            "signals": [],
            "turnoverRisk": {
                "status": "unavailable",
                "label": "暂无判断",
                "baseline": None,
                "multiple": None,
                "reason": "风险计算暂不可用",
            },
            "reason": "风险计算暂不可用",
            "dataComplete": False,
        }

    res = {
        "name": name,
        "code": symbol,
        "status": status,
        **market_status,
        "latestPrice": latest_price,
        "changeAmount": change_amount,
        "changePercent": change_percent,
        "sourceTime": source_time,
        "fetchedAt": fetched_at,
        "isMonitored": is_monitored,
        "monitoringStatus": monitoring_status,
        "monitoringError": monitoring_error,
        "risk": market_risk,
        "details": {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "previousClose": prev_close,
            "volume": (f"{volume:.2f}万股" if is_hk else f"{volume:.2f}万手") if volume is not None else None,
            "turnoverAmount": (f"{turnover:.2f}亿港元" if is_hk else f"{turnover:.2f}亿元") if turnover is not None else None,
            "turnoverAmountValue": raw_turnover,
            "turnoverRate": turnover_rate,
            "volumeRatio": volume_ratio,
            "turnoverRisk": market_risk["turnoverRisk"],
            "peRatio": pe_ratio,
            "marketCap": (f"{market_cap}亿港元" if is_hk else f"{market_cap}亿元") if market_cap is not None else None,
        },
        "industry": "-",
        "concepts": []
    }
    
    is_hk = symbol.lower().startswith("hk") or (symbol.isdigit() and len(symbol) == 5)
    if is_hk:
        res["fundFlow"] = "暂无港股资金流数据"
        res["fundFlowTimeScope"] = "不适用（暂无港股资金流口径）"
    else:
        res["fundFlowTimeScope"] = describe_undated_fund_flow_scope(
            market_status["marketStatusCode"]
        )
        raw_flow = get_sina_stock_fund_flow(symbol)
        if raw_flow is not None:
            flow_yi = raw_flow / 100000000.0
            if flow_yi > 0:
                res["fundFlow"] = f"净流入 {round(flow_yi, 2)} 亿元"
            elif flow_yi < 0:
                res["fundFlow"] = f"净流出 {abs(round(flow_yi, 2))} 亿元"
            else:
                res["fundFlow"] = "主力资金净流 0.0 亿元"

    return res

@app.get("/api/stock/kline/{symbol}")
def get_stock_kline(symbol: str, period: str = "day"):
    """
    获取历史 K 线（腾讯财经），自动计算 MA5/MA10/MA20。
    period: day | week | month | year
    """
    allowed = {"day", "week", "month", "year"}
    if period not in allowed:
        raise HTTPException(status_code=400, detail=f"period 只能是: {allowed}")

    code = asset_context.normalize_symbol(symbol)
    query_symbol = f"{asset_context.quote_prefix(symbol)}{code}"
    query_period = "month" if period == "year" else period
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={query_symbol},{query_period},,,300,qfq"
    )

    try:
        resp = requests.get(url, timeout=8)
        data_json = resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"K线数据请求失败: {e}")

    if data_json.get("code") != 0:
        raise HTTPException(status_code=503, detail="腾讯财经 K 线接口返回错误")

    stock_data = data_json["data"].get(query_symbol)
    if not stock_data:
        raise HTTPException(status_code=404, detail=f"找不到股票 {symbol} 的 K 线数据")

    # 优先取复权数据
    raw_data = stock_data.get(f"qfq{query_period}", stock_data.get(query_period, []))

    kline_data = []
    
    if period == "year":
        # 将月K线聚合为年K线
        year_dict = {}
        for item in raw_data:
            if len(item) < 6: continue
            year = item[0][:4] # 取出年份 YYYY
            if year not in year_dict:
                year_dict[year] = {
                    "time": year,
                    "open": float(item[1]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                    "close": float(item[2]),
                    "volume": float(item[5])
                }
            else:
                year_dict[year]["high"] = max(year_dict[year]["high"], float(item[3]))
                year_dict[year]["low"] = min(year_dict[year]["low"], float(item[4]))
                year_dict[year]["close"] = float(item[2])
                year_dict[year]["volume"] += float(item[5])
        kline_data = list(year_dict.values())
    else:
        for item in raw_data:
            # 腾讯格式: [日期, open, close, high, low, volume]
            if len(item) >= 6:
                kline_data.append({
                    "time":   item[0],
                    "open":   float(item[1]),
                    "high":   float(item[3]),
                    "low":    float(item[4]),
                    "close":  float(item[2]),
                    "volume": float(item[5])
                })

    # 计算均线
    closes = [item["close"] for item in kline_data]
    ma5  = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)

    for i, item in enumerate(kline_data):
        item["ma5"]  = ma5[i]
        item["ma10"] = ma10[i]
        item["ma20"] = ma20[i]

    return {"data": kline_data}

def fetch_hk_announcements(symbol_pure):
    announcements = []
    try:
        import requests
        ann_url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=H&client_source=web&stock_list={symbol_pure}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(ann_url, headers=headers, timeout=5)
        data = resp.json().get("data", {}).get("list", [])
        for item in data:
            announcements.append({
                "id": item.get("art_code", ""),
                "title": item.get("title", ""),
                "publishTime": item.get("display_time", "")[:10],
                "source": "东方财富",
                "summary": item.get("title", ""),
                "url": f"https://data.eastmoney.com/notices/detail/{symbol_pure}/{item.get('art_code')}.html",
                "importance": "中"
            })
    except Exception as e:
        print("HK announcement fetch error:", e)
    return announcements

_company_info_cache = {}

@app.get("/api/stock/company/{symbol}")
def get_company_info(symbol: str):
    import time
    now = time.time()
    if symbol in _company_info_cache:
        ts, cached_data = _company_info_cache[symbol]
        if now - ts < 43200:  # 12 hours
            return cached_data

    import akshare as ak
    symbol_pure = symbol.lower().replace("hk", "")
    watchlist_item = next((
        item
        for item in database.get_watchlist()
        if asset_context.normalize_symbol(item.get("stockCode", "")) == symbol_pure
    ), {})
    stock_name = str(watchlist_item.get("stockName") or "").strip()
    asset = asset_context.resolve_asset_context(symbol_pure, stock_name)
    is_hk = asset["asset_type"] == "hk_stock"

    if asset["asset_type"] == "domestic_etf":
        import news_collector

        announcements = []
        for item in news_collector.fetch_etf_disclosures(symbol_pure, stock_name):
            time_metadata = news_api.get_source_time_metadata(item)
            announcements.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "publishTime": time_metadata["publish_time"],
                "source": item.get("source", "天天基金公告"),
                "summary": item.get("content", ""),
                "url": item.get("url", ""),
                "importance": "按公告内容判断",
            })
        result = {
            "companyInfo": {
                "mainBusiness": f"跟踪{asset['industry_name']}的国内ETF",
                "coreProducts": [asset["industry_name"]],
                "industryTags": [asset["industry_name"]],
                "companyDescription": "国内交易所上市ETF，关注跟踪指数、基金公告、份额与成交变化。",
                "businessRelation": "ETF按跟踪指数或主题关联资讯，不套用上市公司上下游口径。",
                "updateTime": "配置口径",
            },
            "announcements": announcements,
            "news": [],
            "financialData": {
                "reportPeriod": "不适用",
                "revenue": "不适用",
                "revenueYoy": "不适用",
                "netProfit": "不适用",
                "netProfitYoy": "不适用",
                "grossMargin": "不适用",
                "netMargin": "不适用",
                "roe": "不适用",
                "eps": "不适用",
                "debtRatio": "不适用",
                "updateTime": "不适用",
            },
        }
        _company_info_cache[symbol] = (now, result)
        return result
    
    if is_hk:
        try:
            # 港股基本资料
            df_profile = ak.stock_hk_company_profile_em(symbol=symbol_pure)
            df_profile = df_profile.fillna("")
            profile_dict = df_profile.to_dict('records')[0] if not df_profile.empty else {}
            
            industry_name = profile_dict.get('所属行业', '港股')
            company_desc = profile_dict.get('公司介绍', '暂无简介')
            main_business = profile_dict.get('公司名称', '港股公司信息')
            
            # 港股财务指标
            df_finance = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_pure)
            finance_dict = df_finance.to_dict('records')[0] if not df_finance.empty else {}
            
            report_period = str(finance_dict.get('REPORT_DATE', '-'))[:10]
            revenue_value = parse_optional_float(finance_dict.get('OPERATE_INCOME'))
            revenue = f"{revenue_value/100000000:.2f}亿" if revenue_value is not None else "-"
            revenue_yoy_value = parse_optional_float(finance_dict.get('OPERATE_INCOME_YOY'))
            revenue_yoy = f"{revenue_yoy_value:.2f}%" if revenue_yoy_value is not None else "-"
            net_profit_value = parse_optional_float(finance_dict.get('HOLDER_PROFIT'))
            net_profit = f"{net_profit_value/100000000:.2f}亿" if net_profit_value is not None else "-"
            net_profit_yoy_value = parse_optional_float(finance_dict.get('HOLDER_PROFIT_YOY'))
            net_profit_yoy = f"{net_profit_yoy_value:.2f}%" if net_profit_yoy_value is not None else "-"
            gross_margin_value = parse_optional_float(finance_dict.get('GROSS_PROFIT_RATIO'))
            gross_margin = f"{gross_margin_value:.2f}%" if gross_margin_value is not None else "-"
            roe_value = parse_optional_float(finance_dict.get('ROE_YEARLY'))
            roe = f"{roe_value:.2f}%" if roe_value is not None else "-"
            eps_value = parse_optional_float(finance_dict.get('BASIC_EPS'))
            eps = eps_value if eps_value is not None else "-"
            
            return {
                "companyInfo": {
                    "mainBusiness": main_business,
                    "coreProducts": [industry_name],
                    "industryTags": [industry_name],
                    "companyDescription": company_desc,
                    "businessRelation": "-",
                    "updateTime": "数据源未提供更新时间"
                },
                "announcements": fetch_hk_announcements(symbol_pure),
                "news": [],
                "financialData": {
                    "reportPeriod": report_period,
                    "revenue": revenue,
                    "revenueYoy": revenue_yoy,
                    "netProfit": net_profit,
                    "netProfitYoy": net_profit_yoy,
                    "grossMargin": gross_margin,
                    "netMargin": "-",
                    "roe": roe,
                    "eps": str(eps),
                    "debtRatio": "-",
                    "updateTime": report_period if report_period != "-" else "数据源未提供更新时间"
                }
            }
        except Exception as e:
            print("Failed to fetch HK company info; returning missing-data state:", e)
            return {
                "companyInfo": {
                    "mainBusiness": "港股公司信息",
                    "coreProducts": ["暂无详细数据"],
                    "industryTags": ["港股"],
                    "companyDescription": "暂时无法获取该港股的全量数据。",
                    "businessRelation": "-",
                    "updateTime": "数据获取失败"
                },
                "financialData": {
                    "reportPeriod": "-",
                    "revenue": "-",
                    "revenueYoy": "-",
                    "netProfit": "-",
                    "netProfitYoy": "-",
                    "grossMargin": "-",
                    "netMargin": "-",
                    "roe": "-",
                    "eps": "-",
                    "debtRatio": "-",
                    "updateTime": "数据获取失败"
                }
            }
        
    prefix = asset_context.quote_prefix(symbol).upper()
    
    # 1. 抓取公司信息
    info_url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?code={prefix}{symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    company_info = {
        "mainBusiness": "-",
        "coreProducts": [],
        "industryTags": [],
        "companyDescription": "-",
        "businessRelation": "与当前股票所属产业链相关",
        "updateTime": "-"
    }
    
    try:
        resp = requests.get(info_url, headers=headers, timeout=5)
        data = resp.json().get("jbzl", {})
        company_info["mainBusiness"] = data.get("jyfw", "-")
        company_info["companyDescription"] = data.get("gsjj", "-")
        
        # 将经营范围切片作为核心产品演示
        jyfw = data.get("jyfw", "")
        if jyfw:
            products = [p.strip() for p in jyfw.replace("、", "，").split("，") if len(p) > 1][:4]
            company_info["coreProducts"] = products

        industry = data.get("sshy", "")
        if industry and industry.strip("-"):
            company_info["industryTags"] = [t for t in industry.split("-") if t]
        else:
            zjhhy = data.get("sszjhhy", "")
            company_info["industryTags"] = [zjhhy] if zjhhy and zjhhy.strip("-") else ["未知行业"]
    except Exception:
        pass

    # 2. 抓取财务摘要
    finance_url = f"https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_LICO_FN_CPD&columns=ALL&filter=(SECURITY_CODE%3D%22{symbol}%22)"
    financial_data = {
        "reportPeriod": "-",
        "revenue": "-", "revenueYoy": "-", "netProfit": "-", "netProfitYoy": "-",
        "grossMargin": "-", "netMargin": "-", "roe": "-", "debtRatio": "-", "updateTime": "-"
    }
    try:
        f_resp = requests.get(finance_url, headers=headers, timeout=5)
        f_json = f_resp.json()
        if f_json and f_json.get("result") and f_json["result"].get("data"):
            f_data = f_json["result"]["data"][0]
            
            financial_data["reportPeriod"] = f_data.get("REPORTDATE", "-").split(" ")[0]
            financial_data["revenue"] = format_financial_money(f_data.get("TOTAL_OPERATE_INCOME"))
            financial_data["revenueYoy"] = format_financial_percent(f_data.get("YSTZ"))
            financial_data["netProfit"] = format_financial_money(f_data.get("PARENT_NETPROFIT"))
            financial_data["netProfitYoy"] = format_financial_percent(f_data.get("SJLTZ"))
            financial_data["roe"] = format_financial_percent(f_data.get("WEIGHTAVG_ROE"))
            financial_data["grossMargin"] = format_financial_percent(f_data.get("XSMLL"))
            financial_data["updateTime"] = f_data.get("UPDATE_DATE", "-").split(" ")[0]
    except Exception as e:
        print("Finance fetch error:", e)

    # 3. 抓取最新公告
    ann_url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list={symbol}"
    announcements = []
    try:
        resp = requests.get(ann_url, headers=headers, timeout=5)
        data = resp.json().get("data", {}).get("list", [])
        for item in data:
            announcements.append({
                "id": item.get("art_code", ""),
                "title": item.get("title", ""),
                "publishTime": item.get("display_time", "")[:10],
                "source": "东方财富",
                "summary": item.get("title", ""),
                "url": f"https://data.eastmoney.com/notices/detail/{symbol}/{item.get('art_code')}.html",
                "importance": "中"
            })
    except Exception:
        pass

    res = {
        "companyInfo": company_info,
        "announcements": announcements,
        "financialData": financial_data,
        "news": []
    }
    _company_info_cache[symbol] = (now, res)
    return res

from ai_analysis import fetch_real_industry_dynamics


def format_industry_fund_flow(status_code, available, flow_value, is_hk):
    if is_hk:
        return "暂无港股行业资金流数据"
    if status_code == "unknown":
        return "暂无资金流数据（市场状态未知）"
    if status_code == "lunch_break":
        if available and flow_value is not None:
            return f"午间休市 {'+' if flow_value >= 0 else ''}{flow_value} 亿元"
        return "暂无资金流数据（午间休市）"
    if status_code == "pre_open":
        if available and flow_value is not None:
            return f"盘前参考（上游未提供数据日期） {'+' if flow_value >= 0 else ''}{flow_value} 亿元"
        return "暂无资金流数据（盘前）"
    if status_code != "trading":
        if available and flow_value is not None:
            return f"最近一次行业资金流（上游未提供数据日期） {'+' if flow_value >= 0 else ''}{flow_value} 亿元"
        return "暂无资金流数据（非交易时段）"
    if not available or flow_value is None:
        return "暂无行业资金流数据"
    if flow_value > 0:
        return f"净流入 {flow_value} 亿元"
    if flow_value < 0:
        return f"净流出 {abs(flow_value)} 亿元"
    return "主力资金净流 0.0 亿元"


def describe_undated_fund_flow_scope(status_code: str) -> str:
    if status_code == "trading":
        return "交易时段参考（上游未提供数据日期）"
    if status_code == "lunch_break":
        return "午间参考（上游未提供数据日期）"
    if status_code == "pre_open":
        return "上一交易时段参考（上游未提供数据日期）"
    if status_code in {"closed", "holiday"}:
        return "最近交易时段（上游未提供数据日期）"
    return "数据日期暂无法确认"

@app.get("/api/stock/industry/{symbol}")
def get_industry_monitor(symbol: str):
    """
    按市场获取行业指标，并融入 AI 上下游与政策动态（自选限制）。
    """
    # 1. 先获取这只股票的所属行业
    company_data = get_company_info(symbol)
    industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
    watchlist_item = next((
        item
        for item in database.get_watchlist()
        if asset_context.normalize_symbol(item.get("stockCode", ""))
        == asset_context.normalize_symbol(symbol)
    ), {})
    context = asset_context.register_asset_context(
        asset_context.build_asset_context(
            symbol,
            watchlist_item.get("stockName", ""),
            industry_tags,
        )
    )
    industry_name = context["industry_name"]
    is_hk = context["asset_type"] == "hk_stock"
    linkage_risk = get_cached_linkage_risk(symbol) or {
        "riskStatus": "unavailable",
        "priority": None,
        "direction": "neutral",
        "signals": [],
        "sectorRisk": {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "板块风险采样尚未完成或真实数据不完整",
        },
        "overseasRisk": {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "没有经过业务精确映射且带真实行情的海外标的",
        },
        "reason": "板块与海外联动暂无判断",
        "dataComplete": False,
    }
    
    market_status = get_market_status_for_symbol(symbol)
    fetched_at = datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds")

    # 同花顺公开行业资金流，按真实行业名称匹配。
    fallback_heat = None
    fallback_flow = None
    sector_change = None
    fund_flow_available = False
    industry_data_status = "not_applicable" if is_hk else "unavailable"
    industry_data_error = None
    try:
        if is_hk:
            raise LookupError("港股不使用 A 股行业资金流口径")
        import akshare as ak
        df_sector = ak.stock_fund_flow_industry(symbol="即时")
        matched = df_sector[df_sector["行业"].astype(str) == industry_name]
        if matched.empty:
            for kw in industry_name.split("·"):
                matched = df_sector[df_sector["行业"].astype(str).str.contains(kw.strip(), na=False)]
                if not matched.empty:
                    break
        if not matched.empty:
            row = matched.iloc[0]
            raw_flow = parse_optional_float(row.get("净额"))
            raw_pct = parse_optional_float(row.get("行业-涨跌幅"))
            if raw_flow is not None:
                fallback_flow = round(raw_flow, 2)
                fund_flow_available = True
            if raw_pct is not None:
                sector_change = round(raw_pct, 2)
            if fallback_flow is not None and sector_change is not None:
                fallback_heat = max(0, min(100, int(50 + abs(fallback_flow) * 0.3 + sector_change * 3)))
            if fallback_flow is not None or sector_change is not None:
                industry_data_status = "available"
            else:
                industry_data_error = "上游返回的行业指标为空"
        else:
            industry_data_error = "上游行业列表未匹配到当前行业"
    except LookupError:
        industry_data_status = "not_applicable"
    except Exception as e:
        industry_data_status = "unavailable"
        industry_data_error = "行业资金流上游抓取失败"
        print("THS industry fund flow failed:", e)

    flow_val = fallback_flow
    flow_str = format_industry_fund_flow(
        market_status["marketStatusCode"],
        fund_flow_available,
        flow_val,
        is_hk,
    )
    
    # 2. 检查是否在自选监测列表中，若不在则不调用大模型筛选，展示说明文字，防额度消耗
    # 实时去重多源资讯池（读取 80 条）
    news_pool = database.get_latest_crawled_news(symbol, limit=80)
    source_counts = news_api.build_independent_source_counts(news_pool)
    all_news_list = []
    for x in news_pool:
        if not news_api.is_source_published_today(x):
            continue
        classification = news_api.classify_news_item(x, source_counts)
        evidence_level = classification.get("credibility_level")
        if evidence_level not in {"S", "A", "B", "C"}:
            continue
        time_metadata = news_api.get_source_time_metadata(x)
        all_news_list.append({
            "title": x.get("title"),
            "source": x.get("source"),
            "url": x.get("url") or x.get("original_link"),
            "time": time_metadata["publish_time"],
            "timePrecision": time_metadata["publish_time_precision"],
            "discoveredAt": time_metadata["discovered_at"],
            "categoryKey": classification["category_key"],
            "evidenceLevel": evidence_level,
            "verificationStatus": classification["verification_status"],
            "direction": classification["direction"],
            "priority": classification["priority"],
        })

    if not database.is_in_watchlist(symbol):
        return {
            "industryName": industry_name,
            "heatScore": fallback_heat,
            "heatScoreMethod": "calculated" if fallback_heat is not None else "unavailable",
            "sectorChangePercent": sector_change,
            "fundFlow": flow_str,
            "fundFlowTimeScope": describe_undated_fund_flow_scope(
                market_status["marketStatusCode"]
            ),
            "industryDataStatus": industry_data_status,
            "industryDataError": industry_data_error,
            "linkageRisk": linkage_risk,
            "policySummary": "💡 本股票未加入监测列表，政策监控已休眠",
            "upstreamStatus": "💡 本股票未加入监测列表",
            "downstreamStatus": "上下游监控已休眠",
            "policies": [],
            "upstreamDownstream": [],
            "allNews": all_news_list,
            "fetchedAt": fetched_at,
            "updateTime": "已休眠",
            "refreshInterval": "静态"
        }
    
    # 3. 如果在监测列表中，则获取共享缓存的AI大模型提炼条目
    dynamics = fetch_real_industry_dynamics(
        symbol,
        industry_name,
        search_terms=context["search_terms"],
    )
    
    # 提炼一句话总结，保证老接口的兼容性
    policies_list = dynamics.get("policies", [])
    p_summary = policies_list[0].get("title", "当日暂无已验证政策") if policies_list else "当日暂无已验证政策"
    
    upstream_downstream_list = dynamics.get("upstreamDownstream", [])
    up_status = upstream_downstream_list[0].get("title", "当日暂无已验证上下游动态") if len(upstream_downstream_list) > 0 else "当日暂无已验证上下游动态"
    down_status = upstream_downstream_list[1].get("title", "下游动态监控中") if len(upstream_downstream_list) > 1 else "暂无可验证下游动态"

    return {
        "industryName": industry_name,
        "heatScore": fallback_heat,
        "heatScoreMethod": "calculated" if fallback_heat is not None else "unavailable",
        "sectorChangePercent": sector_change,
        "fundFlow": flow_str,
        "fundFlowTimeScope": describe_undated_fund_flow_scope(
            market_status["marketStatusCode"]
        ),
        "industryDataStatus": industry_data_status,
        "industryDataError": industry_data_error,
        "linkageRisk": linkage_risk,
        "policySummary": p_summary,
        "upstreamStatus": up_status,
        "downstreamStatus": down_status,
        "policies": dynamics.get("policies", []),
        "upstreamDownstream": dynamics.get("upstreamDownstream", []),
        "allNews": all_news_list,
        "fetchedAt": fetched_at,
        "updateTime": "上游未提供数据时间" if industry_data_status == "available" else "数据不可用",
        "refreshInterval": "动态"
    }

@app.get("/api/stock/telegraph")
def get_telegraph():
    """
    获取真实的 7x24 小时去重新闻与公告电报
    """
    try:
        import time
        news_items = database.get_latest_crawled_news("", limit=50)
        news_list = []
        for x in news_items:
            ctime_val = x.get("ctime", time.time())
            try:
                time_str = datetime.fromtimestamp(ctime_val).strftime("%H:%M:%S")
            except:
                time_str = "实时"
            news_list.append({
                "time": time_str,
                "title": f"[{x.get('source')}] {x.get('title')}",
                "content": x.get("content", "")
            })
        return {"data": news_list}
    except Exception as e:
        print(f"Error fetching telegraph: {e}")
        return {"data": []}

@app.get("/api/stock/abnormal_peers/{symbol}")
def get_abnormal_peers(symbol: str):
    """
    抓取同板块涨跌幅异常（>5% 或 <-5%）的推荐同行股票
    """
    is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)
    
    if is_hk:
        # 暂无可追溯的港股行业成分接口，不使用手工股票列表冒充真实同行。
        return {"data": []}

    company_data = get_company_info(symbol)
    industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
    industry_name = industry_tags[0] if industry_tags and industry_tags[0] != "未知行业" else ""
    peers = get_a_share_industry_peer_codes(symbol, industry_name)

    if not peers:
        return {"data": []}

    # 保持数据源顺序并去重，避免每次刷新结果随机变化。
    peers = list(dict.fromkeys(peers))
    
    query_list = []
    for c in peers:
        if is_hk:
            query_list.append(f"hk{c}")
        else:
            query_list.append(f"sh{c}" if c.startswith('6') else f"sz{c}")
        
    results = []
    # 腾讯接口最多一次请求 50-100 个，切片请求
    import time
    for i in range(0, len(query_list), 30):
        url = f"http://qt.gtimg.cn/q={','.join(query_list[i:i+30])}"
        try:
            resp = requests.get(url, timeout=3)
            text = resp.text
            for line in text.split(';'):
                if '=' in line:
                    parts = line.split('=')
                    val_str = parts[1].strip('"')
                    v = val_str.split('~')
                    if len(v) > 32:
                        name = v[1]
                        code = v[2]
                        price = parse_optional_float(v[3] if len(v) > 3 else None)
                        change_pct = parse_optional_float(v[32] if len(v) > 32 else None)
                        
                        # 排除自身
                        if code == symbol:
                            continue
                            
                        # 筛选逻辑：涨幅跌停至少达到5%以上的股票
                        if abs(change_pct) >= 5.0:
                            results.append({
                                "stockName": name,
                                "stockCode": code,
                                "oneDayChange": change_pct,
                                "twentyDayChange": None,
                                "volumeRatio": parse_optional_float(v[49] if len(v) > 49 else None),
                                "reason": None,
                                "riskNote": None,
                                "updateTime": format_market_timestamp(v[30] if len(v) > 30 else ""),
                                "fundFlow": None
                            })
        except Exception as e:
            pass
        
    # 去重，按涨跌幅绝对值从大到小排序
    seen = set()
    unique_results = []
    for r in results:
        if r["stockCode"] not in seen:
            seen.add(r["stockCode"])
            unique_results.append(r)
            
    unique_results.sort(key=lambda x: abs(x["oneDayChange"]), reverse=True)
    # 取前 20 个（前端后续可能过滤）
    top_results = unique_results[:20]
    
    # 新浪公开接口返回这批 A 股的真实主力净额。
    if top_results:
        for item in top_results:
            raw_flow = get_sina_stock_fund_flow(item["stockCode"])
            if raw_flow is None:
                continue
            flow_yi = raw_flow / 100000000.0
            if flow_yi > 0:
                item["fundFlow"] = f"净流入 {round(flow_yi, 2)} 亿元"
                item["reason"] = "主力资金净流入"
            elif flow_yi < 0:
                item["fundFlow"] = f"净流出 {abs(round(flow_yi, 2))} 亿元"
                item["reason"] = "主力资金净流出"
            else:
                item["fundFlow"] = "主力资金净流 0.0 亿元"
                item["reason"] = "主力资金净流为 0"
            
    return {"data": top_results}

@app.get("/api/stock/finance/{symbol}")
def get_finance_data(symbol: str):
    """
    获取真实的财报数据，包含最新一期核心数据、近3-4年年报、近8个报告期
    """
    import datetime
    import requests
    from fastapi import HTTPException
    
    secucode = symbol
    if not ("." in symbol):
        secucode = f"{symbol}.SH" if symbol.startswith('6') else f"{symbol}.SZ"

    is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)
    
    if is_hk:
        try:
            import akshare as ak
            symbol_pure = symbol.lower().replace("hk", "")
            df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_pure)
            if df.empty:
                raise HTTPException(status_code=404, detail="暂无财报数据")
            reports = df.to_dict('records')
            
            total_shares = None
            try:
                df_ind = ak.stock_hk_financial_indicator_em(symbol=symbol_pure)
                if not df_ind.empty:
                    total_shares = parse_optional_float(df_ind.iloc[0].get("已发行股本(股)"))
            except Exception as e:
                print("Failed to fetch total shares for HK", e)
            
            formatted_all = []
            for r in reports:
                per_cash = parse_optional_float(r.get("PER_NETCASH_OPERATE"))
                ocf = per_cash * total_shares if per_cash is not None and total_shares is not None else None
                date_str = str(r.get("REPORT_DATE", ""))[:10]
                report_name, report_type = classify_report_type_by_date(date_str)
                formatted_all.append({
                    "reportDate": date_str,
                    "reportName": report_name,
                    "reportType": report_type,
                    "revenue": parse_optional_float(r.get("OPERATE_INCOME")),
                    "revenueYoy": parse_optional_float(r.get("OPERATE_INCOME_YOY")),
                    "netProfit": parse_optional_float(r.get("HOLDER_PROFIT")),
                    "netProfitYoy": parse_optional_float(r.get("HOLDER_PROFIT_YOY")),
                    "deductNetProfit": None,
                    "deductNetProfitYoy": None,
                    "grossMargin": parse_optional_float(r.get("GROSS_PROFIT_RATIO")),
                    "netMargin": parse_optional_float(r.get("NET_PROFIT_RATIO")),
                    "roe": parse_optional_float(r.get("ROE_YEARLY")),
                    "assetLiabilityRatio": parse_optional_float(r.get("DEBT_ASSET_RATIO")),
                    "operateCashFlow": ocf,
                    "eps": parse_optional_float(r.get("BASIC_EPS"))
                })
            
            formatted_all.sort(key=lambda x: x["reportDate"], reverse=True)
            formatted_year = formatted_all # 港股暂不区分全量和年度
            
            latest_report = formatted_all[0] if formatted_all else None
            yearly_reports = formatted_year[:4]
            quarterly_reports = formatted_all[:8]
            
            return {
                "source": "东方财富",
                "fetchedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "stockCode": symbol,
                "stockName": reports[0].get("SECURITY_NAME_ABBR", "") if reports else "",
                "latest": latest_report,
                "yearly": yearly_reports,
                "quarterly": quarterly_reports
            }
        except Exception as e:
            print("HK Finance data error:", e)
            raise HTTPException(status_code=404, detail="暂无财报数据")
    else:
        # 使用东方财富新版财务摘要接口 ZYZBAjaxNew
        # type=0: 按报告期 (获取最近的季报/中报/年报)
        # type=1: 按年度 (专门获取最近的年报)
        url_all = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={secucode}"
        url_year = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=1&code={secucode}"
        
        try:
            resp_all = requests.get(url_all, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            reports_all = resp_all.json().get("data", [])
            
            resp_year = requests.get(url_year, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            reports_year = resp_year.json().get("data", [])
            
            if not reports_all:
                raise HTTPException(status_code=404, detail="暂无财报数据")
                
            def format_reports(reports_list):
                formatted = []
                for r in reports_list:
                    # 核心数据
                    revenue = r.get("TOTALOPERATEREVE")
                    revenue_yoy = r.get("TOTALOPERATEREVETZ")
                    
                    net_profit = r.get("PARENTNETPROFIT")
                    net_profit_yoy = r.get("PARENTNETPROFITTZ")
                    
                    deduct_net_profit = r.get("KCFJCXSYJLR")
                    deduct_net_profit_yoy = r.get("KCFJCXSYJLRTZ")
                    
                    gross_margin = r.get("XSMLL")
                    net_margin = r.get("XSJLL")
                    roe = r.get("ROEJQ")
                    asset_liability_ratio = r.get("ZCFZL")
                    
                    eps = r.get("EPSJB")
                    mgjyxjje = r.get("MGJYXJJE")
                    operate_cash_flow = None
                    if eps and mgjyxjje and net_profit:
                        shares = net_profit / eps
                        operate_cash_flow = shares * mgjyxjje
                    
                    report_date = r.get("REPORT_DATE", "").split(" ")[0]
                    report_type = r.get("REPORT_TYPE", "")
                    report_date_name = r.get("REPORT_DATE_NAME", "")
                    
                    formatted.append({
                        "reportDate": report_date,
                        "reportName": report_date_name, 
                        "reportType": report_type,
                        "revenue": revenue,
                        "revenueYoy": revenue_yoy,
                        "netProfit": net_profit,
                        "netProfitYoy": net_profit_yoy,
                        "deductNetProfit": deduct_net_profit,
                        "deductNetProfitYoy": deduct_net_profit_yoy,
                        "grossMargin": gross_margin,
                        "netMargin": net_margin,
                        "roe": roe,
                        "assetLiabilityRatio": asset_liability_ratio,
                        "operateCashFlow": operate_cash_flow,
                        "eps": eps
                    })
                # 确保日期倒序
                formatted.sort(key=lambda x: x["reportDate"], reverse=True)
                return formatted

            formatted_all = format_reports(reports_all)
            formatted_year = format_reports(reports_year)
            
            latest_report = formatted_all[0] if formatted_all else None
            
            # 获取近4年的年报 (直接取 type=1 的前4条)
            yearly_reports = formatted_year[:4]
            
            # 获取近8个季度的报告 (取 type=0 的前8条)
            quarterly_reports = formatted_all[:8]
            
            return {
                "source": "东方财富",
                "fetchedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "stockCode": symbol,
                "stockName": reports_all[0].get("SECURITY_NAME_ABBR", ""),
                "latest": latest_report,
                "yearly": yearly_reports,
                "quarterly": quarterly_reports
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"财报获取失败: {str(e)}")

@app.get("/api/stock/related/{symbol}")
def get_related_stocks(symbol: str):
    """
    根据输入的股票，动态获取同板块（真实所属行业）的其他成分股
    并获取它们的实时行情和真实资金流向
    """
    try:
        is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)
        company_data = get_company_info(symbol)
        industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
        industry_name = industry_tags[0] if industry_tags and industry_tags[0] != "未知行业" else ""

        if is_hk:
            # 暂无可追溯的港股行业成分接口，不使用手工股票列表冒充真实同行。
            return {"data": []}

        peers = get_a_share_industry_peer_codes(symbol, industry_name)
        if not peers:
            return {"data": []}

        # 腾讯接口批量请求数量受限，按行业成分原始顺序取前 30 只，结果保持稳定。
        query_list = [f"sh{c}" if c.startswith('6') else f"sz{c}" for c in peers[:30]]
        
        results = []
        url = f"http://qt.gtimg.cn/q={','.join(query_list)}"
        try:
            resp = requests.get(url, timeout=3)
            text = resp.text
            for line in text.split(';'):
                if '=' in line:
                    parts = line.split('=')
                    val_str = parts[1].strip('"')
                    v = val_str.split('~')
                    if len(v) > 32:
                        name = v[1]
                        code = v[2]
                        price = float(v[3])
                        change_pct = float(v[32])
                        
                        results.append({
                            "stockName": name,
                            "stockCode": code,
                            "latestPrice": price,
                            "changePercent": change_pct,
                            "fundFlow": None
                        })
        except: pass
        
        # 优先选择涨幅靠前的或者成交活跃的，这里按涨幅排序
        results.sort(
            key=lambda item: abs(item["changePercent"]) if item["changePercent"] is not None else -1,
            reverse=True,
        )
        top_6 = results[:6]
        
        for item in top_6:
            raw_flow = get_sina_stock_fund_flow(item["stockCode"])
            if raw_flow is None:
                continue
            flow_yi = raw_flow / 100000000.0
            if flow_yi > 0:
                item["fundFlow"] = f"净流入 {round(flow_yi, 2)} 亿元"
            elif flow_yi < 0:
                item["fundFlow"] = f"净流出 {abs(round(flow_yi, 2))} 亿元"
            else:
                item["fundFlow"] = "主力资金净流 0.0 亿元"
                
        return {"data": top_6}
    except Exception as e:
        print("Error fetching related stocks:", e)
        return {"data": []}



if __name__ == "__main__":

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
