import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import database
import market_calendar


_PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3}


def _connect():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds")


def init_alert_tables() -> None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS monitoring_preferences (
        symbol TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        email_enabled INTEGER NOT NULL DEFAULT 1,
        p2_email INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS alert_events (
        id TEXT PRIMARY KEY,
        dedupe_key TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        stock_name TEXT,
        event_type TEXT NOT NULL,
        direction TEXT NOT NULL,
        priority TEXT NOT NULL,
        evidence_level TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        source TEXT NOT NULL,
        source_url TEXT,
        source_event_id TEXT NOT NULL,
        published_at TEXT,
        triggered_at TEXT NOT NULL,
        is_read INTEGER NOT NULL DEFAULT 0
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS alert_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        next_retry_at TEXT,
        sent_at TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(alert_id, channel),
        FOREIGN KEY(alert_id) REFERENCES alert_events(id)
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS signal_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        time_bucket TEXT NOT NULL,
        source_time TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        change_percent REAL,
        amplitude REAL,
        volume_ratio REAL,
        turnover_rate REAL,
        turnover_amount REAL,
        risk_status TEXT,
        priority TEXT,
        risk_reason TEXT,
        turnover_status TEXT,
        turnover_baseline REAL,
        turnover_multiple REAL,
        signals_json TEXT NOT NULL DEFAULT '[]',
        data_complete INTEGER NOT NULL DEFAULT 0,
        UNIQUE(symbol, trade_date, time_bucket)
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS alert_email_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        recipient_email TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    ''')
    conn.commit()
    conn.close()


def _alert_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "stockName": row["stock_name"] or row["symbol"],
        "eventType": row["event_type"],
        "direction": row["direction"],
        "priority": row["priority"],
        "evidenceLevel": row["evidence_level"],
        "title": row["title"],
        "summary": row["summary"],
        "source": row["source"],
        "sourceUrl": row["source_url"],
        "sourceEventId": row["source_event_id"],
        "publishedAt": row["published_at"],
        "triggeredAt": row["triggered_at"],
        "isRead": bool(row["is_read"]),
    }


def save_alert_event(event: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    init_alert_tables()
    dedupe_key = "|".join((
        str(event["symbol"]),
        str(event["source_event_id"]),
        str(event["source"]),
    ))
    alert_id = hashlib.md5(dedupe_key.encode("utf-8")).hexdigest()
    triggered_at = _now()
    conn = _connect()
    cursor = conn.cursor()
    existing = cursor.execute('''
    SELECT * FROM alert_events
    WHERE symbol=? AND source_event_id=? AND source=?
    ORDER BY CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
             triggered_at DESC
    LIMIT 1
    ''', (
        event["symbol"],
        event["source_event_id"],
        event["source"],
    )).fetchone()
    if existing is not None:
        current_rank = _PRIORITY_RANK.get(existing["priority"], 99)
        incoming_rank = _PRIORITY_RANK.get(str(event["priority"]), 99)
        is_more_specific = (
            existing["event_type"] == "official_announcement"
            and event["event_type"] != "official_announcement"
        )
        upgraded = incoming_rank < current_rank or (
            incoming_rank == current_rank and is_more_specific
        )
        if upgraded:
            cursor.execute('''
            UPDATE alert_events SET
                event_type=?, direction=?, priority=?, evidence_level=?,
                title=?, summary=?, stock_name=?, source_url=?, published_at=?,
                triggered_at=?, is_read=0
            WHERE id=?
            ''', (
                event["event_type"],
                event["direction"],
                event["priority"],
                event["evidence_level"],
                event["title"],
                event["summary"],
                event.get("stock_name"),
                event.get("source_url"),
                event.get("published_at"),
                triggered_at,
                existing["id"],
            ))
        conn.commit()
        row = cursor.execute(
            "SELECT * FROM alert_events WHERE id=?",
            (existing["id"],),
        ).fetchone()
        conn.close()
        return _alert_from_row(row), upgraded

    cursor.execute('''
    INSERT OR IGNORE INTO alert_events (
        id, dedupe_key, symbol, stock_name, event_type, direction, priority,
        evidence_level, title, summary, source, source_url, source_event_id,
        published_at, triggered_at, is_read
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    ''', (
        alert_id,
        dedupe_key,
        event["symbol"],
        event.get("stock_name"),
        event["event_type"],
        event["direction"],
        event["priority"],
        event["evidence_level"],
        event["title"],
        event["summary"],
        event["source"],
        event.get("source_url"),
        event["source_event_id"],
        event.get("published_at"),
        triggered_at,
    ))
    created = cursor.rowcount == 1
    conn.commit()
    cursor.execute("SELECT * FROM alert_events WHERE id=?", (alert_id,))
    row = cursor.fetchone()
    conn.close()
    return _alert_from_row(row), created


def get_alert(alert_id: str) -> Optional[Dict[str, Any]]:
    init_alert_tables()
    conn = _connect()
    row = conn.execute("SELECT * FROM alert_events WHERE id=?", (alert_id,)).fetchone()
    conn.close()
    return _alert_from_row(row) if row else None


def list_alerts(
    limit: int = 100,
    symbol: Optional[str] = None,
    unread_only: bool = False,
) -> List[Dict[str, Any]]:
    init_alert_tables()
    conditions = []
    params = []
    if symbol:
        conditions.append("symbol=?")
        params.append(symbol)
    if unread_only:
        conditions.append("is_read=0")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    requested_limit = max(1, min(int(limit), 200))
    params.append(200)
    conn = _connect()
    rows = conn.execute(
        f"SELECT * FROM alert_events {where} ORDER BY triggered_at DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    deduped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        item = _alert_from_row(row)
        key = (item["symbol"], item["sourceEventId"], item["source"])
        current = deduped.get(key)
        if current is None or _PRIORITY_RANK.get(item["priority"], 99) < _PRIORITY_RANK.get(current["priority"], 99):
            deduped[key] = item
    return sorted(
        deduped.values(),
        key=lambda item: item["triggeredAt"],
        reverse=True,
    )[:requested_limit]


def get_unread_count() -> int:
    init_alert_tables()
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) AS count FROM alert_events WHERE is_read=0").fetchone()
    conn.close()
    return int(row["count"])


def mark_alert_read(alert_id: str) -> bool:
    init_alert_tables()
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE alert_events SET is_read=1 WHERE id=?", (alert_id,))
    changed = cursor.rowcount == 1
    conn.commit()
    conn.close()
    return changed


def record_delivery(
    alert_id: str,
    channel: str,
    status: str,
    error: Optional[str] = None,
    next_retry_at: Optional[str] = None,
) -> None:
    init_alert_tables()
    now = _now()
    attempted = 1 if channel == "email" and status not in {"not_configured", "pending"} else 0
    sent_at = now if status == "sent" else None
    conn = _connect()
    conn.execute('''
    INSERT INTO alert_deliveries (
        alert_id, channel, status, attempt_count, error, next_retry_at,
        sent_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(alert_id, channel) DO UPDATE SET
        status=excluded.status,
        attempt_count=alert_deliveries.attempt_count + excluded.attempt_count,
        error=excluded.error,
        next_retry_at=excluded.next_retry_at,
        sent_at=COALESCE(excluded.sent_at, alert_deliveries.sent_at),
        updated_at=excluded.updated_at
    ''', (
        alert_id,
        channel,
        status,
        attempted,
        error,
        next_retry_at,
        sent_at,
        now,
    ))
    conn.commit()
    conn.close()


def list_deliveries(alert_id: str) -> List[Dict[str, Any]]:
    init_alert_tables()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM alert_deliveries WHERE alert_id=? ORDER BY id",
        (alert_id,),
    ).fetchall()
    conn.close()
    return [{
        "id": row["id"],
        "alertId": row["alert_id"],
        "channel": row["channel"],
        "status": row["status"],
        "attemptCount": row["attempt_count"],
        "error": row["error"],
        "nextRetryAt": row["next_retry_at"],
        "sentAt": row["sent_at"],
        "updatedAt": row["updated_at"],
    } for row in rows]


def list_due_email_deliveries(now_iso: str) -> List[Dict[str, Any]]:
    init_alert_tables()
    conn = _connect()
    rows = conn.execute('''
    SELECT * FROM alert_deliveries
    WHERE channel='email' AND status='failed' AND next_retry_at IS NOT NULL
      AND next_retry_at<=? AND attempt_count<4
    ORDER BY next_retry_at
    ''', (now_iso,)).fetchall()
    conn.close()
    return [{
        "alertId": row["alert_id"],
        "attemptCount": row["attempt_count"],
    } for row in rows]


def get_preferences(symbol: str) -> Dict[str, Any]:
    init_alert_tables()
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM monitoring_preferences WHERE symbol=?",
        (symbol,),
    ).fetchone()
    conn.close()
    if not row:
        return {
            "symbol": symbol,
            "enabled": True,
            "emailEnabled": True,
            "p2Email": True,
        }
    return {
        "symbol": row["symbol"],
        "enabled": bool(row["enabled"]),
        "emailEnabled": bool(row["email_enabled"]),
        "p2Email": bool(row["p2_email"]),
    }


def save_preferences(
    symbol: str,
    enabled: bool,
    email_enabled: bool,
    p2_email: bool,
) -> Dict[str, Any]:
    init_alert_tables()
    conn = _connect()
    conn.execute('''
    INSERT INTO monitoring_preferences (
        symbol, enabled, email_enabled, p2_email, updated_at
    ) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(symbol) DO UPDATE SET
        enabled=excluded.enabled,
        email_enabled=excluded.email_enabled,
        p2_email=excluded.p2_email,
        updated_at=excluded.updated_at
    ''', (symbol, int(enabled), int(email_enabled), int(p2_email), _now()))
    conn.commit()
    conn.close()
    return get_preferences(symbol)


def get_global_email_settings() -> Dict[str, Any]:
    init_alert_tables()
    conn = _connect()
    row = conn.execute(
        "SELECT recipient_email, updated_at FROM alert_email_settings WHERE id=1"
    ).fetchone()
    conn.close()
    if row is None:
        return {"recipientEmail": None, "updatedAt": None}
    return {
        "recipientEmail": row["recipient_email"],
        "updatedAt": row["updated_at"],
    }


def save_global_email_settings(recipient_email: str) -> Dict[str, Any]:
    init_alert_tables()
    conn = _connect()
    conn.execute('''
    INSERT INTO alert_email_settings (id, recipient_email, updated_at)
    VALUES (1, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        recipient_email=excluded.recipient_email,
        updated_at=excluded.updated_at
    ''', (recipient_email.strip(), _now()))
    conn.commit()
    conn.close()
    return get_global_email_settings()


def _snapshot_time_parts(source_time: str):
    parsed = datetime.strptime(source_time, "%Y-%m-%d %H:%M:%S")
    rounded = parsed + timedelta(minutes=2, seconds=30)
    bucket_minute = (rounded.minute // 5) * 5
    bucket = rounded.replace(minute=bucket_minute, second=0, microsecond=0)
    return parsed.strftime("%Y-%m-%d"), bucket.strftime("%H:%M")


def _decode_snapshot_details(raw_value: Optional[str]) -> Dict[str, Any]:
    parsed = json.loads(raw_value or "[]")
    if isinstance(parsed, dict):
        return dict(parsed)
    return {
        "signals": parsed if isinstance(parsed, list) else [],
    }


def save_signal_snapshot(snapshot: Dict[str, Any]) -> None:
    init_alert_tables()
    trade_date, time_bucket = _snapshot_time_parts(snapshot["source_time"])
    risk = snapshot.get("risk") or {}
    turnover_risk = risk.get("turnoverRisk") or {}
    risk_details = {
        "signals": risk.get("signals") or [],
        "direction": risk.get("direction"),
        "fundFlowRisk": risk.get("fundFlowRisk"),
        "movingAverageRisk": risk.get("movingAverageRisk"),
    }
    high = snapshot.get("high")
    low = snapshot.get("low")
    previous_close = snapshot.get("previous_close")
    amplitude = None
    if high is not None and low is not None and previous_close:
        amplitude = round((float(high) - float(low)) / float(previous_close) * 100, 2)
    conn = _connect()
    existing = conn.execute('''
    SELECT signals_json FROM signal_snapshots
    WHERE symbol=? AND trade_date=? AND time_bucket=?
    ''', (snapshot["symbol"], trade_date, time_bucket)).fetchone()
    if existing:
        existing_details = _decode_snapshot_details(existing["signals_json"])
        for key in ("linkageRisk", "linkageSnapshot"):
            if key in existing_details:
                risk_details[key] = existing_details[key]
    conn.execute('''
    INSERT INTO signal_snapshots (
        symbol, trade_date, time_bucket, source_time, fetched_at,
        change_percent, amplitude, volume_ratio, turnover_rate,
        turnover_amount, risk_status, priority, risk_reason,
        turnover_status, turnover_baseline, turnover_multiple,
        signals_json, data_complete
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(symbol, trade_date, time_bucket) DO UPDATE SET
        source_time=excluded.source_time,
        fetched_at=excluded.fetched_at,
        change_percent=excluded.change_percent,
        amplitude=excluded.amplitude,
        volume_ratio=excluded.volume_ratio,
        turnover_rate=excluded.turnover_rate,
        turnover_amount=excluded.turnover_amount,
        risk_status=excluded.risk_status,
        priority=excluded.priority,
        risk_reason=excluded.risk_reason,
        turnover_status=excluded.turnover_status,
        turnover_baseline=excluded.turnover_baseline,
        turnover_multiple=excluded.turnover_multiple,
        signals_json=excluded.signals_json,
        data_complete=excluded.data_complete
    ''', (
        snapshot["symbol"],
        trade_date,
        time_bucket,
        snapshot["source_time"],
        snapshot["fetched_at"],
        snapshot.get("change_percent"),
        amplitude,
        snapshot.get("volume_ratio"),
        snapshot.get("turnover_rate"),
        snapshot.get("turnover_amount"),
        risk.get("riskStatus"),
        risk.get("priority"),
        risk.get("reason"),
        turnover_risk.get("status"),
        turnover_risk.get("baseline"),
        turnover_risk.get("multiple"),
        json.dumps(risk_details, ensure_ascii=False),
        int(bool(risk.get("dataComplete"))),
    ))
    conn.commit()
    conn.close()


def save_linkage_snapshot(
    snapshot: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    init_alert_tables()
    source_time = str(snapshot.get("source_time") or "")
    fetched_at = str(snapshot.get("fetched_at") or "")
    if not source_time or not fetched_at:
        return
    trade_date, time_bucket = _snapshot_time_parts(source_time)
    conn = _connect()
    existing = conn.execute('''
    SELECT signals_json FROM signal_snapshots
    WHERE symbol=? AND trade_date=? AND time_bucket=?
    ''', (snapshot["symbol"], trade_date, time_bucket)).fetchone()
    details = _decode_snapshot_details(
        existing["signals_json"] if existing else None
    )
    details["linkageRisk"] = result
    details["linkageSnapshot"] = {
        "sourceTime": source_time,
        "fetchedAt": fetched_at,
        "sector": snapshot.get("sector") or {"status": "unavailable"},
        "overseas": snapshot.get("overseas") or [],
    }
    conn.execute('''
    INSERT INTO signal_snapshots (
        symbol, trade_date, time_bucket, source_time, fetched_at,
        signals_json, data_complete
    ) VALUES (?, ?, ?, ?, ?, ?, 0)
    ON CONFLICT(symbol, trade_date, time_bucket) DO UPDATE SET
        source_time=excluded.source_time,
        fetched_at=excluded.fetched_at,
        signals_json=excluded.signals_json
    ''', (
        snapshot["symbol"],
        trade_date,
        time_bucket,
        source_time,
        fetched_at,
        json.dumps(details, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()


def get_signal_history(
    symbol: str,
    source_time: str,
    limit_days: int = 20,
) -> List[Dict[str, Any]]:
    init_alert_tables()
    current_date, time_bucket = _snapshot_time_parts(source_time)
    conn = _connect()
    rows = conn.execute('''
    SELECT turnover_rate, turnover_amount, trade_date, source_time
    FROM signal_snapshots
    WHERE symbol=? AND time_bucket=? AND trade_date<?
    ORDER BY trade_date DESC
    LIMIT ?
    ''', (symbol, time_bucket, current_date, limit_days)).fetchall()
    conn.close()
    return [{
        "turnover_rate": row["turnover_rate"],
        "turnover_amount": row["turnover_amount"],
        "trade_date": row["trade_date"],
        "source_time": row["source_time"],
    } for row in rows]


def get_latest_linkage_state(
    symbol: str,
    trade_date: str,
) -> Optional[Dict[str, Any]]:
    record = get_latest_linkage_record(symbol, trade_date)
    return record.get("risk") if record else None


def get_latest_linkage_record(
    symbol: str,
    trade_date: str,
) -> Optional[Dict[str, Any]]:
    init_alert_tables()
    conn = _connect()
    rows = conn.execute('''
    SELECT signals_json FROM signal_snapshots
    WHERE symbol=? AND trade_date=?
    ORDER BY source_time DESC
    ''', (symbol, trade_date)).fetchall()
    conn.close()
    for row in rows:
        details = _decode_snapshot_details(row["signals_json"])
        linkage_risk = details.get("linkageRisk")
        if isinstance(linkage_risk, dict):
            linkage_snapshot = details.get("linkageSnapshot")
            return {
                "risk": linkage_risk,
                "snapshot": (
                    linkage_snapshot
                    if isinstance(linkage_snapshot, dict)
                    else None
                ),
            }
    return None


def get_recent_risk_states(
    symbol: str,
    source_time: str,
    risk_kind: str,
    limit: int = 2,
) -> List[Dict[str, Any]]:
    init_alert_tables()
    trade_date, _time_bucket = _snapshot_time_parts(source_time)
    conn = _connect()
    rows = conn.execute('''
    SELECT * FROM signal_snapshots
    WHERE symbol=? AND trade_date=? AND source_time<?
    ORDER BY source_time DESC
    LIMIT ?
    ''', (symbol, trade_date, source_time, max(1, int(limit)))).fetchall()
    conn.close()
    states = []
    for row in rows:
        details = _decode_snapshot_details(row["signals_json"])
        if risk_kind == "linkage":
            state = details.get("linkageRisk")
            if not isinstance(state, dict):
                continue
            states.append({
                "sourceTime": row["source_time"],
                "riskStatus": state.get("riskStatus") or "normal",
                "priority": state.get("priority"),
                "direction": state.get("direction"),
            })
            continue
        if risk_kind != "market":
            raise ValueError("risk_kind 只能是 market 或 linkage")
        states.append({
            "sourceTime": row["source_time"],
            "riskStatus": row["risk_status"] or "normal",
            "priority": row["priority"],
            "direction": details.get("direction"),
        })
    return states


def get_latest_risk_episode_alert(
    symbol: str,
    event_type: str,
    direction: str,
    trade_date: str,
) -> Optional[Dict[str, Any]]:
    init_alert_tables()
    conn = _connect()
    row = conn.execute('''
    SELECT * FROM alert_events
    WHERE symbol=? AND event_type=? AND direction=?
      AND published_at LIKE ?
    ORDER BY triggered_at DESC
    LIMIT 1
    ''', (symbol, event_type, direction, f"{trade_date}%")).fetchone()
    conn.close()
    return _alert_from_row(row) if row else None


def get_latest_signal_state(symbol: str) -> Optional[Dict[str, Any]]:
    init_alert_tables()
    conn = _connect()
    row = conn.execute('''
    SELECT * FROM signal_snapshots
    WHERE symbol=?
    ORDER BY source_time DESC
    LIMIT 1
    ''', (symbol,)).fetchone()
    conn.close()
    if not row:
        return None
    stored_details = _decode_snapshot_details(row["signals_json"])
    if isinstance(stored_details, dict):
        signals = stored_details.get("signals") or []
        direction = stored_details.get("direction")
        fund_flow_risk = stored_details.get("fundFlowRisk")
        moving_average_risk = stored_details.get("movingAverageRisk")
        linkage_risk = stored_details.get("linkageRisk")
        linkage_snapshot = stored_details.get("linkageSnapshot")
    else:
        signals = stored_details if isinstance(stored_details, list) else []
        direction = None
        fund_flow_risk = None
        moving_average_risk = None
        linkage_risk = None
        linkage_snapshot = None
    if not isinstance(fund_flow_risk, dict):
        fund_flow_risk = {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "当前保存记录缺少完整、可验证的历史资金数据",
        }
    if not isinstance(moving_average_risk, dict):
        moving_average_risk = {
            "status": "unavailable",
            "label": "暂无判断",
            "periods": [],
            "reason": "当前保存记录缺少完整、可验证的历史均线数据",
        }
    turnover_status = row["turnover_status"] or "insufficient"
    turnover_multiple = row["turnover_multiple"]
    if turnover_status == "insufficient":
        turnover_reason = "同一时点有效历史不足 20 个交易日，仍在积累样本"
    elif turnover_status == "unavailable":
        turnover_reason = "当前换手率或可比历史数据不足"
    elif turnover_multiple is not None:
        suffix = "，并伴随其他量价异常" if turnover_status == "warning" else ""
        turnover_reason = f"当前为近20日同一时点中位数的 {turnover_multiple} 倍{suffix}"
    else:
        turnover_reason = "换手率相对自身历史基线的状态已计算"
    return {
        "symbol": row["symbol"],
        "sourceTime": row["source_time"],
        "fetchedAt": row["fetched_at"],
        "riskStatus": row["risk_status"] or "normal",
        "priority": row["priority"],
        "direction": direction,
        "reason": row["risk_reason"] or "当前未触发量价风险规则",
        "signals": signals,
        "fundFlowRisk": fund_flow_risk,
        "movingAverageRisk": moving_average_risk,
        "linkageRisk": linkage_risk if isinstance(linkage_risk, dict) else None,
        "linkageSnapshot": (
            linkage_snapshot if isinstance(linkage_snapshot, dict) else None
        ),
        "turnoverRisk": {
            "status": turnover_status,
            "label": {
                "normal": "正常",
                "active": "活跃",
                "warning": "警惕",
                "insufficient": "样本不足",
                "unavailable": "暂无判断",
            }.get(row["turnover_status"], "样本不足"),
            "baseline": row["turnover_baseline"],
            "multiple": turnover_multiple,
            "reason": turnover_reason,
        },
        "sourceTime": row["source_time"],
        "fetchedAt": row["fetched_at"],
        "dataComplete": bool(row["data_complete"]),
    }
