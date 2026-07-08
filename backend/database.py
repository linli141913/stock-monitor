import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "stock_monitor.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 监测列表表 (最多5只股票)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS watchlist (
        symbol TEXT PRIMARY KEY,
        added_at TEXT
    )
    ''')
    
    try:
        cursor.execute("ALTER TABLE watchlist ADD COLUMN name TEXT")
    except sqlite3.OperationalError:
        pass # Column already exists
    
    # AI 分析历史表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ai_analysis_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        date TEXT,
        time TEXT,
        timestamp TEXT,
        trigger_type TEXT,
        plain_english_summary TEXT,
        full_json TEXT
    )
    ''')
    
    # 多源新闻公告聚合表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS crawled_news (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        title TEXT,
        url TEXT UNIQUE,
        ctime INTEGER,
        source TEXT,
        content TEXT,
        category TEXT,
        created_at REAL
    )
    ''')
    
    conn.commit()
    conn.close()

def get_watchlist():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, name, added_at FROM watchlist")
    results = []
    for row in cursor.fetchall():
        results.append({
            "stockCode": row[0],
            "stockName": row[1] or "",
            "addedAt": row[2] or ""
        })
    conn.close()
    return results

def is_in_watchlist(symbol: str) -> bool:
    watchlist = get_watchlist()
    watchlist_codes = {item["stockCode"].strip().lower() for item in watchlist}
    return symbol.strip().lower() in watchlist_codes

def add_to_watchlist(symbol: str) -> bool:
    """添加股票到监测列表，如果已满 10 个则返回 False"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM watchlist")
    count = cursor.fetchone()[0]
    
    if count >= 10:
        # 检查是否已经在里面
        cursor.execute("SELECT symbol FROM watchlist WHERE symbol=?", (symbol,))
        if not cursor.fetchone():
            conn.close()
            return False
            
    cursor.execute("INSERT OR REPLACE INTO watchlist (symbol, added_at) VALUES (?, ?)", 
                   (symbol, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return True

def remove_from_watchlist(symbol: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()
    
def replace_watchlist(items: list) -> bool:
    """全量替换监测列表，超过 10 只直接截断"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist")
    
    limited_items = items[:10]
    for item in limited_items:
        sym = item.get("stockCode", "")
        name = item.get("stockName", "")
        added = item.get("addedAt", datetime.now().isoformat())
        if sym:
            cursor.execute("INSERT INTO watchlist (symbol, name, added_at) VALUES (?, ?, ?)", 
                           (sym, name, added))
    conn.commit()
    conn.close()
    return True

def save_analysis_history(symbol: str, trigger_type: str, plain_english_summary: str, full_json_dict: dict):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    timestamp = now.isoformat()
    
    cursor.execute('''
    INSERT INTO ai_analysis_history (symbol, date, time, timestamp, trigger_type, plain_english_summary, full_json)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (symbol, date_str, time_str, timestamp, trigger_type, plain_english_summary, json.dumps(full_json_dict, ensure_ascii=False)))
    
    conn.commit()
    conn.close()

def get_market_by_symbol(symbol: str) -> str:
    sym = symbol.lower()
    if sym.startswith("hk") or (sym.isdigit() and len(sym) == 5):
        return "hk"
    return "cn"

def is_trading_day(dt, market="cn"):
    # dt is a datetime or date object
    if dt.weekday() >= 5:  # Saturday or Sunday
        return False
    
    date_str = dt.strftime("%Y-%m-%d")
    
    # 2026 CN Holidays (A-Share)
    cn_holidays = {
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
        "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"
    }
    
    # 2026 HK Holidays
    hk_holidays = {
        "2026-01-01",
        "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-04-03", "2026-04-04", "2026-04-06",
        "2026-05-01", "2026-05-25",
        "2026-06-19",
        "2026-07-01",
        "2026-09-26",
        "2026-10-01", "2026-10-19",
        "2026-12-25", "2026-12-26"
    }
    
    if market == "hk":
        return date_str not in hk_holidays
    else:
        return date_str not in cn_holidays

def get_previous_trading_day(dt, market="cn"):
    from datetime import timedelta
    prev = dt - timedelta(days=1)
    while not is_trading_day(prev, market):
        prev -= timedelta(days=1)
    return prev

def get_next_trading_day(dt, market="cn"):
    from datetime import timedelta
    nxt = dt + timedelta(days=1)
    while not is_trading_day(nxt, market):
        nxt += timedelta(days=1)
    return nxt

def get_trading_session_bounds_for_symbol(symbol: str):
    from datetime import timedelta
    market = get_market_by_symbol(symbol)
    now = datetime.now()
    cutoff_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    today_is_trade = is_trading_day(now, market)
    
    if today_is_trade:
        if now < cutoff_time:
            prev_trade = get_previous_trading_day(now, market)
            start_time = prev_trade.replace(hour=15, minute=30, second=0, microsecond=0)
            end_time = cutoff_time
        else:
            next_trade = get_next_trading_day(now, market)
            start_time = cutoff_time
            end_time = next_trade.replace(hour=15, minute=30, second=0, microsecond=0)
    else:
        prev_trade = get_previous_trading_day(now, market)
        next_trade = get_next_trading_day(now, market)
        start_time = prev_trade.replace(hour=15, minute=30, second=0, microsecond=0)
        end_time = next_trade.replace(hour=15, minute=30, second=0, microsecond=0)
        
    return start_time.isoformat(), end_time.isoformat()

def get_target_trading_date_for_timestamp(ts_str: str, market: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str)
    except Exception:
        return ts_str.split("T")[0]
        
    cutoff_time = dt.replace(hour=15, minute=30, second=0, microsecond=0)
    today_is_trade = is_trading_day(dt, market)
    
    if today_is_trade:
        if dt < cutoff_time:
            return dt.strftime("%Y-%m-%d")
        else:
            next_trade = get_next_trading_day(dt, market)
            return next_trade.strftime("%Y-%m-%d")
    else:
        next_trade = get_next_trading_day(dt, market)
        return next_trade.strftime("%Y-%m-%d")

def get_today_analysis_history(symbol: str) -> list:
    """获取某只股票今天的分析历史，用于记忆注入和前端时间轴展示 (按交易日 15:30 到 15:30 划分)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    market = get_market_by_symbol(symbol)
    start_time, end_time = get_trading_session_bounds_for_symbol(symbol)
    
    cursor.execute('''
    SELECT date, time, timestamp, trigger_type, plain_english_summary, full_json 
    FROM ai_analysis_history 
    WHERE symbol=? AND timestamp >= ? AND timestamp < ?
    ORDER BY timestamp ASC
    ''', (symbol, start_time, end_time))
    
    results = []
    for row in cursor.fetchall():
        ts = row[2]
        target_date = get_target_trading_date_for_timestamp(ts, market)
        results.append({
            "date": row[0],
            "time": row[1],
            "timestamp": ts,
            "trigger_type": row[3],
            "plain_english_summary": row[4],
            "full_json": json.loads(row[5]) if row[5] else {},
            "target_date": target_date
        })
        
    conn.close()
    return results

def get_all_analysis_history(symbol: str) -> list:
    """获取某只股票所有的历史记录，按时间倒序排列"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    market = get_market_by_symbol(symbol)
    
    cursor.execute('''
    SELECT date, time, timestamp, trigger_type, plain_english_summary, full_json 
    FROM ai_analysis_history 
    WHERE symbol=?
    ORDER BY timestamp DESC
    ''', (symbol,))
    
    results = []
    for row in cursor.fetchall():
        ts = row[2]
        target_date = get_target_trading_date_for_timestamp(ts, market)
        results.append({
            "date": row[0],
            "time": row[1],
            "timestamp": ts,
            "trigger_type": row[3],
            "plain_english_summary": row[4],
            "full_json": json.loads(row[5]) if row[5] else {},
            "target_date": target_date
        })
        
    conn.close()
    return results


def get_cached_dynamics(symbol: str) -> dict:
    """获取缓存的行业动态新闻及时间，如果不存在或超过 1 小时则返回 None"""
    import time
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS dynamics_cache (symbol TEXT PRIMARY KEY, last_updated REAL, news_json TEXT)")
    cursor.execute("SELECT last_updated, news_json FROM dynamics_cache WHERE symbol=?", (symbol,))
    row = cursor.fetchone()
    conn.close()
    if row:
        last_updated, news_json = row
        if time.time() - last_updated < 3600:
            try:
                return json.loads(news_json)
            except:
                pass
    return None

def save_cached_dynamics(symbol: str, data: dict):
    """将大模型筛选出的新闻数据缓存入库"""
    import time
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS dynamics_cache (symbol TEXT PRIMARY KEY, last_updated REAL, news_json TEXT)")
    cursor.execute("INSERT OR REPLACE INTO dynamics_cache (symbol, last_updated, news_json) VALUES (?, ?, ?)",
                   (symbol, time.time(), json.dumps(data, ensure_ascii=False)))
    conn.commit()
    conn.close()

def save_crawled_news(news_list: list):
    """批量插入抓取到的新闻公告，自动忽略已存在的 URL 链接"""
    import time
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS crawled_news (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        title TEXT,
        url TEXT UNIQUE,
        ctime INTEGER,
        source TEXT,
        content TEXT,
        category TEXT,
        created_at REAL
    )
    ''')
    for item in news_list:
        try:
            cursor.execute('''
            INSERT OR IGNORE INTO crawled_news (id, symbol, title, url, ctime, source, content, category, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item.get("id"),
                item.get("symbol", ""),
                item.get("title", ""),
                item.get("url", ""),
                item.get("ctime", 0),
                item.get("source", ""),
                item.get("content", ""),
                item.get("category", ""),
                time.time()
            ))
        except Exception as e:
            print(f"DB Error inserting news: {e}")
    conn.commit()
    conn.close()

def get_latest_crawled_news(symbol: str, limit: int = 100) -> list:
    """从聚合新闻池中读取与个股或对应行业关联的最新的 50~100 条去重资讯"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS crawled_news (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        title TEXT,
        url TEXT UNIQUE,
        ctime INTEGER,
        source TEXT,
        content TEXT,
        category TEXT,
        created_at REAL
    )
    ''')
    # 读取最新 24 小时或最新的 100 条
    # 支持模糊关联该股票代码或者通用政策/行业
    cursor.execute('''
    SELECT symbol, title, url, ctime, source, content, category 
    FROM crawled_news 
    WHERE symbol=? OR symbol='' OR symbol IS NULL
    ORDER BY ctime DESC, created_at DESC 
    LIMIT ?
    ''', (symbol, limit))
    
    results = []
    for row in cursor.fetchall():
        results.append({
            "symbol": row[0],
            "title": row[1],
            "url": row[2],
            "ctime": row[3],
            "source": row[4],
            "content": row[5],
            "category": row[6]
        })
    conn.close()
    return results

# 初始化
init_db()
