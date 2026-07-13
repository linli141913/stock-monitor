import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import requests
import json
import math
from datetime import datetime
import asyncio
from news_api import router as news_router
from ai_analysis import router as ai_analysis_router
from pydantic import BaseModel
import database
import market_calendar
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Backend-Token", "ngrok-skip-browser-warning"],
)


def request_requires_backend_token(request: Request) -> bool:
    path = request.url.path
    if request.method in {"GET", "POST"} and path == "/api/watchlist":
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

# ── 后台定时追踪任务 ──────────────────────────────────────────────
def run_collection_sync():
    try:
        import news_collector
        news_collector.run_collection()
    except Exception as e:
        print(f"[{datetime.now()}] 定时资讯采集失败: {e}")

async def auto_collect_news():
    print(f"[{datetime.now()}] 触发定时多源资讯采集任务...")
    await asyncio.to_thread(run_collection_sync)

async def auto_analyze_watchlist():
    print(f"[{datetime.now()}] 触发后台自动分析追踪任务...")
    items = database.get_watchlist()
    for item in items:
        symbol = item.get("stockCode")
        if not symbol: continue
        try:
            print(f"[{datetime.now()}] 开始自动分析 {symbol}...")
            # We call the ai_attribution endpoint internally via requests to trigger full pipeline including history injection
            # Wait, calling localhost directly in a background task is the easiest way to reuse all logic
            url = f"http://127.0.0.1:8001/api/stock/ai_attribution/{symbol}?trigger=auto"
            token = os.getenv("BACKEND_API_TOKEN", "").strip()
            headers = {"X-Backend-Token": token} if token else {}
            requests.get(url, headers=headers, timeout=60)
            print(f"[{datetime.now()}] {symbol} 自动分析完成。")
        except Exception as e:
            print(f"[{datetime.now()}] {symbol} 自动分析失败: {e}")

def run_watchlist_industry_update_sync():
    items = database.get_watchlist()
    for item in items:
        symbol = item.get("stockCode")
        if not symbol: continue
        try:
            # 获取所属行业名称
            company_data = get_company_info(symbol)
            industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
            industry_name = industry_tags[0] if industry_tags and industry_tags[0] != "未知行业" else "半导体"
            
            print(f"[{datetime.now()}] 开始自动更新 {symbol} ({industry_name}) 行业动态...")
            # 强制刷新缓存
            fetch_real_industry_dynamics(symbol, industry_name, force_refresh=True)
            print(f"[{datetime.now()}] {symbol} 行业动态更新完成。")
        except Exception as e:
            print(f"[{datetime.now()}] {symbol} 自动更新行业动态失败: {e}")


async def auto_update_watchlist_industry():
    print(f"[{datetime.now()}] 触发后台自动更新行业与政策监控任务...")
    await asyncio.to_thread(run_watchlist_industry_update_sync)

@app.on_event("startup")
def start_scheduler():
    scheduler = AsyncIOScheduler()
    # 启动时立即异步拉取一次新闻与行业监控大模型数据
    scheduler.add_job(auto_collect_news, 'date', run_date=datetime.now())
    scheduler.add_job(auto_update_watchlist_industry, 'date', run_date=datetime.now())
    # 随后每 30 分钟拉取一次新闻
    scheduler.add_job(auto_collect_news, 'interval', minutes=30)
    # 随后每 1 小时自动拉取更新一次行业动态
    scheduler.add_job(auto_update_watchlist_industry, 'interval', hours=1)
    # 每天 10:30, 11:30, 15:00, 22:00
    scheduler.add_job(auto_analyze_watchlist, 'cron', hour=10, minute=30)
    scheduler.add_job(auto_analyze_watchlist, 'cron', hour=11, minute=30)
    scheduler.add_job(auto_analyze_watchlist, 'cron', hour=15, minute=0)
    scheduler.add_job(auto_analyze_watchlist, 'cron', hour=22, minute=0)
    scheduler.start()
    print("后台自动化追踪与资讯采集调度器已启动。")
# ── 公共工具函数 ──────────────────────────────────────────────

def get_prefix(symbol: str) -> str:
    """
    根据股票代码返回对应的市场前缀 (sh, sz, hk)
    """
    symbol = symbol.lower()
    if symbol.startswith("hk"):
        return "" # 后面拼接时直接用 symbol，因为已经带了 hk

    # 如果纯数字且长度为5，或者是港股常见代码
    if symbol.isdigit() and len(symbol) == 5:
        return "hk"
        
    # 简单处理A股：6开头算沪市，0或3开头算深市，8或4算北交所(用bj，但腾讯接口通常是sz/sh)
    if symbol.startswith("6") or symbol.startswith("9"):
        return "sh"
    return "sz"

def get_em_prefix(symbol: str) -> str:
    """根据代码返回东方财富的 secid 前缀"""
    symbol = symbol.lower()
    if symbol.startswith("hk"):
        return "116."
    if symbol.isdigit() and len(symbol) == 5:
        return "116."
    if symbol.startswith('6') or symbol.startswith('9'):
        return "1."
    if symbol.startswith('8') or symbol.startswith('4'):
        return "0." # 北交所在东财也是 0.
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
        if symbol.lower().startswith("hk"):
            query_list.append(symbol.lower())
        elif symbol.isdigit() and len(symbol) == 5:
            query_list.append(f"hk{symbol}")
        elif symbol.startswith('6'):
            query_list.append(f"sh{symbol}")
        elif symbol.startswith('0') or symbol.startswith('3'):
            query_list.append(f"sz{symbol}")
        elif symbol.startswith('8') or symbol.startswith('4'):
            query_list.append(f"bj{symbol}")
            
    url = f"http://qt.gtimg.cn/q={','.join(query_list)}"
    
    results = []
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
                    "amount": raw_amount if is_hk or raw_amount is None else raw_amount * 10000,
                    "volume": raw_volume if is_hk or raw_volume is None else raw_volume * 100,
                })
                
        # 新浪公开接口逐只获取真实主力资金净额；港股暂不填充无可靠口径的数据。
        if results:
            for item in results:
                code = item["symbol"]
                if len(code) != 6:
                    continue
                raw_flow = get_sina_stock_fund_flow(code)
                if raw_flow is not None:
                    flow_yi = raw_flow / 100000000.0
                    if flow_yi > 0:
                        item["fundFlow"] = f"净流入 {round(flow_yi, 2)} 亿元"
                    elif flow_yi < 0:
                        item["fundFlow"] = f"净流出 {abs(round(flow_yi, 2))} 亿元"
                    else:
                        item["fundFlow"] = "主力资金净流 0.0 亿元"

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
        "details": {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "previousClose": prev_close,
            "volume": (f"{volume:.2f}万股" if is_hk else f"{volume:.2f}万手") if volume is not None else None,
            "turnoverAmount": (f"{turnover:.2f}亿港元" if is_hk else f"{turnover:.2f}亿元") if turnover is not None else None,
            "turnoverRate": turnover_rate,
            "peRatio": pe_ratio,
            "marketCap": (f"{market_cap}亿港元" if is_hk else f"{market_cap}亿元") if market_cap is not None else None,
        },
        "industry": "-",
        "concepts": []
    }
    
    is_hk = symbol.lower().startswith("hk") or (symbol.isdigit() and len(symbol) == 5)
    if is_hk:
        res["fundFlow"] = "暂无港股资金流数据"
    else:
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

    prefix = get_prefix(symbol)
    query_period = "month" if period == "year" else period
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={prefix}{symbol},{query_period},,,300,qfq"
    )

    try:
        resp = requests.get(url, timeout=8)
        data_json = resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"K线数据请求失败: {e}")

    if data_json.get("code") != 0:
        raise HTTPException(status_code=503, detail="腾讯财经 K 线接口返回错误")

    stock_data = data_json["data"].get(f"{prefix}{symbol}")
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
    is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)
    
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
                    "updateTime": "最新"
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
                    "updateTime": "最新"
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
                    "updateTime": "实时"
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
                    "updateTime": "实时"
                }
            }
        
    prefix = 'SZ' if symbol.startswith('0') or symbol.startswith('3') else 'SH'
    
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
            
            def format_money(val):
                if not val: return "-"
                val = float(val)
                if val > 1e8: return f"{val/1e8:.2f}亿"
                if val > 1e4: return f"{val/1e4:.2f}万"
                return f"{val:.2f}"

            financial_data["reportPeriod"] = f_data.get("REPORTDATE", "-").split(" ")[0]
            financial_data["revenue"] = format_money(f_data.get("TOTAL_OPERATE_INCOME"))
            financial_data["revenueYoy"] = f"{f_data.get('YSTZ', 0):.2f}%" if f_data.get('YSTZ') else "-"
            financial_data["netProfit"] = format_money(f_data.get("PARENT_NETPROFIT"))
            financial_data["netProfitYoy"] = f"{f_data.get('SJLTZ', 0):.2f}%" if f_data.get('SJLTZ') else "-"
            financial_data["roe"] = f"{f_data.get('WEIGHTAVG_ROE', 0):.2f}%" if f_data.get('WEIGHTAVG_ROE') else "-"
            financial_data["grossMargin"] = f"{f_data.get('XSMLL', 0):.2f}%" if f_data.get('XSMLL') else "-"
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
    if status_code != "trading":
        if available and flow_value is not None:
            return f"今日收盘 {'+' if flow_value >= 0 else ''}{flow_value} 亿元"
        return "暂无资金流数据（非交易时段）"
    if not available or flow_value is None:
        return "暂无行业资金流数据"
    if flow_value > 0:
        return f"净流入 {flow_value} 亿元"
    if flow_value < 0:
        return f"净流出 {abs(flow_value)} 亿元"
    return "主力资金净流 0.0 亿元"

@app.get("/api/stock/industry/{symbol}")
def get_industry_monitor(symbol: str):
    """
    按市场获取行业指标，并融入 AI 上下游与政策动态（自选限制）。
    """
    # 1. 先获取这只股票的所属行业
    company_data = get_company_info(symbol)
    industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
    industry_name = industry_tags[0] if industry_tags and industry_tags[0] != "未知行业" else "半导体"
    is_hk = symbol.lower().startswith("hk") or (symbol.isdigit() and len(symbol) == 5)
    
    market_status = get_market_status_for_symbol(symbol)
    fetched_at = datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds")

    # 同花顺公开行业资金流，按真实行业名称匹配。
    fallback_heat = None
    fallback_flow = None
    sector_change = None
    fund_flow_available = False
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
    except LookupError:
        pass
    except Exception as e:
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
    import time
    news_pool = database.get_latest_crawled_news(symbol, limit=80)
    all_news_list = []
    for x in news_pool:
        ctime_val = x.get("ctime", time.time())
        try:
            time_str = datetime.fromtimestamp(ctime_val).strftime("%m-%d %H:%M")
        except:
            time_str = "今日"
        all_news_list.append({
            "title": x.get("title"),
            "source": x.get("source"),
            "url": x.get("url"),
            "time": time_str
        })

    if not database.is_in_watchlist(symbol):
        return {
            "industryName": industry_name,
            "heatScore": fallback_heat,
            "sectorChangePercent": sector_change,
            "fundFlow": flow_str,
            "policySummary": "💡 本股票未加入监测列表，政策监控已休眠",
            "upstreamStatus": "💡 本股票未加入监测列表",
            "downstreamStatus": "上下游监控已休眠",
            "policies": [
                {
                    "title": "💡 本股票未加入监测列表，政策大模型监控已休眠",
                    "source": "系统提示",
                    "url": "",
                    "time": "实时"
                }
            ],
            "upstreamDownstream": [
                {
                    "title": "💡 本股票未加入监测列表，上下游大模型监控已休眠",
                    "source": "系统提示",
                    "url": "",
                    "time": "实时"
                }
            ],
            "allNews": all_news_list,
            "fetchedAt": fetched_at,
            "updateTime": "已休眠",
            "refreshInterval": "静态"
        }
    
    # 3. 如果在监测列表中，则获取共享缓存的AI大模型提炼条目
    dynamics = fetch_real_industry_dynamics(symbol, industry_name)
    
    # 提炼一句话总结，保证老接口的兼容性
    policies_list = dynamics.get("policies", [])
    p_summary = policies_list[0].get("title", "系统实时监测该板块相关政策") if policies_list else "系统实时监测该板块相关政策"
    
    upstream_downstream_list = dynamics.get("upstreamDownstream", [])
    up_status = upstream_downstream_list[0].get("title", "上游动态监控中") if len(upstream_downstream_list) > 0 else "上游动态监控中"
    down_status = upstream_downstream_list[1].get("title", "下游动态监控中") if len(upstream_downstream_list) > 1 else "暂无可验证下游动态"

    return {
        "industryName": industry_name,
        "heatScore": fallback_heat,
        "sectorChangePercent": sector_change,
        "fundFlow": flow_str,
        "policySummary": p_summary,
        "upstreamStatus": up_status,
        "downstreamStatus": down_status,
        "policies": dynamics.get("policies", []),
        "upstreamDownstream": dynamics.get("upstreamDownstream", []),
        "allNews": all_news_list,
        "fetchedAt": fetched_at,
        "updateTime": "实时监控",
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
                formatted_all.append({
                    "reportDate": date_str,
                    "reportName": f"{date_str[:4]}年报", # 港股API通常返回年报或半年报
                    "reportType": "年报",
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
