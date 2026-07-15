from datetime import datetime
import re
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import alert_repository
import database
import market_calendar
import monitoring_health
import news_api
import notification_service


router = APIRouter(prefix="/api", tags=["Alerts"])


_MARKET_RISK_RULE_LABELS = {
    "limit_move": "极端涨跌区间",
    "extreme_price_move": "涨跌幅≥5%",
    "high_amplitude": "振幅≥8%",
    "high_volume_ratio": "量比≥2",
    "turnover_warning": "换手率警惕",
}


def _legacy_market_risk_codes(item):
    source_event_id = str(item.get("sourceEventId") or "").strip()
    parts = source_event_id.split(":")
    if len(parts) >= 4 and parts[0] == "risk" and parts[2] in {
        "positive",
        "negative",
        "neutral",
    }:
        return ",".join(parts[3:]).split(",")
    return []


def _enrich_legacy_market_risk(item):
    codes = _legacy_market_risk_codes(item)
    if not codes:
        return item
    rule_text = "、".join(
        _MARKET_RISK_RULE_LABELS.get(code, code)
        for code in codes
    )
    priority = str(item.get("priority") or "P3")
    level_text = f"{priority}强提醒" if priority in {"P1", "P2"} else f"{priority}观察提醒"
    item["title"] = f"{item['stockName']}触发{level_text}：{rule_text}"
    item["summary"] = (
        f"固定规则触发：{rule_text}。"
        "旧提醒未保存触发瞬间的具体数值，因此不补写数值。"
        "该提醒只表示风险或活跃度升高，不代表确定性涨跌。"
    )
    return item


def _market_risk_dedupe_key(item):
    if item.get("eventType") != "market_risk":
        return None
    source_event_id = str(item.get("sourceEventId") or "").strip()
    parts = source_event_id.split(":")
    if len(parts) >= 4 and parts[0] == "risk" and parts[2] in {
        "positive",
        "negative",
        "neutral",
    }:
        source_event_id = ":".join((parts[0], parts[1], *parts[3:]))
    return str(item.get("symbol") or "").strip().lower(), source_event_id


def _watchlist_alerts(
    *,
    unread_only: bool = False,
    limit: int = 200,
    today_only: bool = True,
):
    monitored = {
        str(item.get("stockCode") or "").strip().lower(): str(
            item.get("stockName") or ""
        ).strip()
        for item in database.get_watchlist()
    }
    source_rows_by_symbol = {}
    seen_market_risks = set()
    results = []
    for item in alert_repository.list_alerts(
            limit=limit,
            unread_only=False,
        ):
        symbol = item["symbol"].strip().lower()
        is_system_health = item.get("eventType") == "system_health"
        if today_only and symbol not in monitored and not is_system_health:
            continue
        if today_only and not news_api.is_source_published_today({
                "publishedAt": item.get("publishedAt"),
                "source": item.get("source"),
                "discoveredAt": item.get("triggeredAt"),
            }):
            continue

        market_risk_key = _market_risk_dedupe_key(item)
        if market_risk_key is not None:
            if market_risk_key in seen_market_risks:
                continue
            seen_market_risks.add(market_risk_key)
        if unread_only and item.get("isRead"):
            continue

        enriched = dict(item)
        stock_name = monitored.get(symbol) or enriched.get("stockName") or symbol
        enriched["stockName"] = stock_name
        enriched = _enrich_legacy_market_risk(enriched)
        title = str(enriched.get("title") or "").strip()
        if title and stock_name not in title:
            enriched["title"] = f"{stock_name}：{title}"

        if symbol not in source_rows_by_symbol:
            source_rows_by_symbol[symbol] = {
                str(row.get("url") or "").strip(): row
                for row in database.get_latest_crawled_news(symbol, limit=100)
                if str(row.get("url") or "").strip()
            }
        source_row = source_rows_by_symbol[symbol].get(
            str(enriched.get("sourceUrl") or "").strip()
        )
        source_summary = str((source_row or {}).get("content") or "").strip()
        current_summary = str(enriched.get("summary") or "").strip()
        if source_summary and source_summary not in current_summary:
            enriched["summary"] = (
                f"{source_summary} 影响判断：{current_summary}"
                if current_summary
                else source_summary
            )
        results.append(enriched)
    return results


class AlertPreferencesRequest(BaseModel):
    symbol: str
    enabled: bool = True
    emailEnabled: bool = True
    p2Email: bool = True


class GlobalEmailSettingsRequest(BaseModel):
    recipientEmail: str


class WatchlistSyncHealthRequest(BaseModel):
    items: list[dict]


def _valid_email(value: str) -> bool:
    candidate = value.strip()
    return bool(
        candidate
        and len(candidate) <= 254
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate)
    )


def _email_settings_response():
    settings = alert_repository.get_global_email_settings()
    recipient_configured = bool(settings.get("recipientEmail"))
    sender_configured = notification_service.get_smtp_config() is not None
    return {
        **settings,
        "recipientConfigured": recipient_configured,
        "senderConfigured": sender_configured,
        "configured": recipient_configured and sender_configured,
    }


@router.get("/alerts")
def get_alerts(
    symbol: Optional[str] = None,
    unread_only: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
    scope: Literal["today", "history"] = "today",
):
    items = _watchlist_alerts(
        unread_only=unread_only,
        today_only=scope == "today",
    )
    if symbol:
        items = [item for item in items if item["symbol"] == symbol]
    items = items[:limit]
    for item in items:
        item["deliveries"] = alert_repository.list_deliveries(item["id"])
    return {
        "data": items,
        "fetchedAt": datetime.now(
            market_calendar.SHANGHAI_TZ
        ).isoformat(timespec="seconds"),
    }


@router.get("/alerts/unread-count")
def get_alert_unread_count():
    return {"count": len(_watchlist_alerts(unread_only=True))}


@router.get("/alerts/preferences")
def get_alert_preferences(symbol: str):
    return {"data": alert_repository.get_preferences(symbol)}


@router.get("/alerts/email-settings")
def get_global_email_settings():
    return {"data": _email_settings_response()}


@router.get("/stock/risk/{symbol}")
def get_stock_risk(symbol: str):
    risk = alert_repository.get_latest_signal_state(symbol.strip())
    return {
        "data": risk,
        "status": "available" if risk is not None else "unavailable",
        "fetchedAt": datetime.now(
            market_calendar.SHANGHAI_TZ
        ).isoformat(timespec="seconds"),
    }


@router.get("/monitoring/health")
def get_monitoring_health():
    task_names = (
        "generalNews",
        "industryDynamics",
        "officialAnnouncements",
        "marketRisk",
        "aiAnalysis",
        "emailRetry",
    )
    tasks = {name: {"status": "not_run"} for name in task_names}
    tasks.update(monitoring_health.get_task_states())
    task_statuses = {task.get("status") for task in tasks.values()}
    if "failed" in task_statuses:
        status = "degraded"
    elif task_statuses & {"healthy", "running"}:
        status = "healthy"
    else:
        status = "unknown"
    email_settings = _email_settings_response()
    return {
        "status": status,
        "watchlistCount": len(database.get_watchlist()),
        "unreadCount": len(_watchlist_alerts(unread_only=True)),
        "email": {
            "status": "configured" if email_settings["configured"] else "not_configured",
            "configured": email_settings["configured"],
            "recipientConfigured": email_settings["recipientConfigured"],
            "senderConfigured": email_settings["senderConfigured"],
        },
        "watchlistSync": monitoring_health.get_watchlist_sync_state(),
        "tasks": tasks,
        "fetchedAt": datetime.now(
            market_calendar.SHANGHAI_TZ
        ).isoformat(timespec="seconds"),
    }


@router.post("/monitoring/health/watchlist-sync")
def report_watchlist_sync(request: WatchlistSyncHealthRequest):
    backend_items = database.get_watchlist()
    synced = monitoring_health.audit_watchlist_sync(
        request.items,
        backend_items,
    )
    return {
        "status": "synced" if synced else "mismatched",
        "frontendCount": len(request.items),
        "backendCount": len(backend_items),
    }


@router.put("/alerts/preferences")
def update_alert_preferences(request: AlertPreferencesRequest):
    if not request.symbol.strip():
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    return {
        "data": alert_repository.save_preferences(
            request.symbol.strip(),
            request.enabled,
            request.emailEnabled,
            request.p2Email,
        )
    }


@router.put("/alerts/email-settings")
def update_global_email_settings(request: GlobalEmailSettingsRequest):
    recipient = request.recipientEmail.strip()
    if not _valid_email(recipient):
        raise HTTPException(status_code=400, detail="请输入有效的收件邮箱")
    alert_repository.save_global_email_settings(recipient)
    return {"data": _email_settings_response()}


@router.post("/alerts/email-settings/test")
def test_global_email_settings():
    recipient = alert_repository.get_global_email_settings().get("recipientEmail")
    if not recipient:
        raise HTTPException(status_code=400, detail="请先保存收件邮箱")
    result = notification_service.send_test_email(str(recipient))
    status = str(result.get("status") or "failed")
    message = {
        "sent": "测试邮件已发送，请检查收件箱",
        "not_configured": "收件邮箱已保存，后端发件服务尚未配置",
        "failed": "测试邮件发送失败",
    }.get(status, "测试邮件状态未知")
    return {
        "status": status,
        "message": message,
        "error": result.get("error"),
        "recipientEmail": recipient,
    }


@router.patch("/alerts/{alert_id}/read")
def mark_alert_read(alert_id: str):
    if not alert_repository.mark_alert_read(alert_id):
        raise HTTPException(status_code=404, detail="提醒不存在")
    return {"message": "success", "alertId": alert_id}
