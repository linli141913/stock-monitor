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
    
    conn.commit()
    conn.close()

def get_watchlist():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM watchlist")
    results = [row[0] for row in cursor.fetchall()]
    conn.close()
    return results

def add_to_watchlist(symbol: str) -> bool:
    """添加股票到监测列表，如果已满 5 个则返回 False"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM watchlist")
    count = cursor.fetchone()[0]
    
    if count >= 5:
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
    
def replace_watchlist(symbols: list[str]) -> bool:
    """全量替换监测列表，超过 5 只直接截断"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist")
    
    limited_symbols = symbols[:5]
    for sym in limited_symbols:
        cursor.execute("INSERT INTO watchlist (symbol, added_at) VALUES (?, ?)", 
                       (sym, datetime.now().isoformat()))
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

def get_trading_session_bounds():
    from datetime import timedelta
    now = datetime.now()
    cutoff_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < cutoff_time:
        start_time = cutoff_time - timedelta(days=1)
        end_time = cutoff_time
    else:
        start_time = cutoff_time
        end_time = cutoff_time + timedelta(days=1)
    return start_time.isoformat(), end_time.isoformat()

def get_today_analysis_history(symbol: str) -> list:
    """获取某只股票今天的分析历史，用于记忆注入和前端时间轴展示 (按交易日 15:30 到 15:30 划分)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    start_time, end_time = get_trading_session_bounds()
    
    cursor.execute('''
    SELECT date, time, timestamp, trigger_type, plain_english_summary, full_json 
    FROM ai_analysis_history 
    WHERE symbol=? AND timestamp >= ? AND timestamp < ?
    ORDER BY timestamp ASC
    ''', (symbol, start_time, end_time))
    
    results = []
    for row in cursor.fetchall():
        results.append({
            "date": row[0],
            "time": row[1],
            "timestamp": row[2],
            "trigger_type": row[3],
            "plain_english_summary": row[4],
            "full_json": json.loads(row[5]) if row[5] else {}
        })
        
    conn.close()
    return results

def get_all_analysis_history(symbol: str) -> list:
    """获取某只股票所有的历史记录，按时间倒序排列"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT date, time, timestamp, trigger_type, plain_english_summary, full_json 
    FROM ai_analysis_history 
    WHERE symbol=?
    ORDER BY timestamp DESC
    ''', (symbol,))
    
    results = []
    for row in cursor.fetchall():
        results.append({
            "date": row[0],
            "time": row[1],
            "timestamp": row[2],
            "trigger_type": row[3],
            "plain_english_summary": row[4],
            "full_json": json.loads(row[5]) if row[5] else {}
        })
        
    conn.close()
    return results

# 初始化
init_db()
