import os
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Dict, Optional

import alert_repository
import database
import market_calendar


RETRY_DELAYS_MINUTES = (1, 5, 15)
EVENT_AI_MAX_PENDING = 8
_EVENT_AI_PENDING = set()
_EVENT_AI_LOCK = threading.Lock()
_EVENT_AI_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="event-ai",
)


def _now() -> datetime:
    return datetime.now(market_calendar.SHANGHAI_TZ)


def get_smtp_config() -> Optional[Dict[str, Any]]:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    if not host or not sender:
        return None
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        return None
    return {
        "host": host,
        "port": port,
        "username": os.getenv("SMTP_USERNAME", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "sender": sender,
        "use_tls": os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false",
    }


def get_email_recipient() -> Optional[str]:
    settings = alert_repository.get_global_email_settings()
    saved_recipient = str(settings.get("recipientEmail") or "").strip()
    if saved_recipient:
        return saved_recipient
    fallback = os.getenv("ALERT_EMAIL_TO", "").strip()
    return fallback or None


def get_email_config() -> Optional[Dict[str, Any]]:
    smtp_config = get_smtp_config()
    recipient = get_email_recipient()
    if smtp_config is None or recipient is None:
        return None
    return {**smtp_config, "recipient": recipient}


def _send_message(message: EmailMessage, config: Dict[str, Any]) -> Dict[str, Optional[str]]:
    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=10) as smtp:
            if config["use_tls"]:
                smtp.starttls()
            if config["username"]:
                smtp.login(config["username"], config["password"])
            smtp.send_message(message)
        return {"status": "sent", "error": None}
    except Exception as exc:
        return {"status": "failed", "error": f"邮件发送失败: {type(exc).__name__}"}


def send_alert_email(alert: Dict[str, Any]) -> Dict[str, Optional[str]]:
    config = get_email_config()
    if config is None:
        return {"status": "not_configured", "error": "收件邮箱或发件服务尚未配置"}

    message = EmailMessage()
    message["Subject"] = f"[{alert['priority']}][{alert['stockName']}] {alert['title']}"
    message["From"] = config["sender"]
    message["To"] = config["recipient"]
    message.set_content(
        "\n".join((
            f"股票：{alert['stockName']}（{alert['symbol']}）",
            f"方向：{alert['direction']}",
            f"优先级：{alert['priority']}",
            f"证据等级：{alert['evidenceLevel']}",
            f"事件：{alert['title']}",
            f"说明：{alert['summary']}",
            f"原文：{alert.get('sourceUrl') or '暂无原文链接'}",
        ))
    )

    return _send_message(message, config)


def send_test_email(recipient: str) -> Dict[str, Optional[str]]:
    config = get_smtp_config()
    if config is None:
        return {"status": "not_configured", "error": "后端 SMTP 发件服务尚未配置"}

    message = EmailMessage()
    message["Subject"] = "[股票监测助手] 强提醒邮箱测试"
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content(
        "这是一封测试邮件。\n\n"
        "收到本邮件说明全局收件邮箱和 SMTP 发件服务已连通。\n"
        "此测试不代表任何股票风险或交易信号。"
    )
    return _send_message(message, config)


def _next_retry_at(attempt_count: int) -> Optional[str]:
    if attempt_count < 1 or attempt_count > len(RETRY_DELAYS_MINUTES):
        return None
    return (
        _now() + timedelta(minutes=RETRY_DELAYS_MINUTES[attempt_count - 1])
    ).isoformat(timespec="seconds")


def deliver_alert(alert: Dict[str, Any]) -> None:
    alert_repository.record_delivery(alert["id"], "site", "sent")
    if alert["priority"] not in {"P1", "P2"}:
        return

    preferences = alert_repository.get_preferences(alert["symbol"])
    if not preferences["enabled"] or not preferences["emailEnabled"]:
        return
    if alert["priority"] == "P2" and not preferences["p2Email"]:
        return

    result = send_alert_email(alert)
    status = str(result["status"])
    if status == "not_configured":
        alert_repository.record_delivery(
            alert["id"],
            "email",
            "not_configured",
            error=result.get("error"),
        )
        return
    if status == "sent":
        alert_repository.record_delivery(alert["id"], "email", "sent")
        return
    alert_repository.record_delivery(
        alert["id"],
        "email",
        "failed",
        error=result.get("error"),
        next_retry_at=_next_retry_at(1),
    )


def _run_event_ai_analysis(symbol: str, trigger: str) -> None:
    try:
        from ai_analysis import get_ai_attribution
        get_ai_attribution(symbol, trigger=trigger)
    except Exception as exc:
        failure = {
            "stockName": symbol,
            "stockCode": symbol,
            "changePercent": None,
            "score": None,
            "evidenceChain": {},
            "futureTrendPrediction": "暂无推演内容",
            "plainEnglishSummary": "AI 分析调用失败，本次未生成评分或结论。",
            "aiJudgment": f"大模型接口调用失败: {type(exc).__name__}",
            "credibility": "错误",
            "riskNotice": "请检查后台日志。",
            "analysisStatus": "failed",
        }
        existing = database.get_analysis_history_by_trigger(symbol, trigger)
        if existing is not None and existing["full_json"].get("analysisStatus") == "running":
            database.complete_analysis_trigger(
                symbol,
                trigger,
                failure["plainEnglishSummary"],
                failure,
            )
        elif existing is None:
            database.save_analysis_history(
                symbol,
                trigger,
                failure["plainEnglishSummary"],
                failure,
            )
        print(f"[{_now().isoformat()}] 事件 AI 分析失败，本事件不重试: {exc}")
    finally:
        with _EVENT_AI_LOCK:
            _EVENT_AI_PENDING.discard(trigger)


def trigger_event_ai_analysis(alert: Dict[str, Any]) -> str:
    if alert.get("priority") not in {"P1", "P2"}:
        return "skipped_priority"
    symbol = str(alert.get("symbol") or "").strip()
    alert_id = str(alert.get("id") or "").strip()
    if not symbol or not alert_id:
        return "skipped_invalid"
    trigger = f"event:{alert_id}"
    with _EVENT_AI_LOCK:
        if trigger in _EVENT_AI_PENDING:
            return "skipped_duplicate"
        if database.get_analysis_history_by_trigger(symbol, trigger) is not None:
            return "skipped_duplicate"
        if len(_EVENT_AI_PENDING) >= EVENT_AI_MAX_PENDING:
            return "skipped_capacity"
        _EVENT_AI_PENDING.add(trigger)
    try:
        _EVENT_AI_EXECUTOR.submit(_run_event_ai_analysis, symbol, trigger)
    except Exception:
        with _EVENT_AI_LOCK:
            _EVENT_AI_PENDING.discard(trigger)
        raise
    return "started"


def process_new_alert(alert: Dict[str, Any]) -> None:
    deliver_alert(alert)
    trigger_event_ai_analysis(alert)


def retry_due_email_deliveries() -> int:
    retried = 0
    now_iso = _now().isoformat(timespec="seconds")
    for delivery in alert_repository.list_due_email_deliveries(now_iso):
        alert = alert_repository.get_alert(delivery["alertId"])
        if alert is None:
            continue
        result = send_alert_email(alert)
        next_attempt = int(delivery["attemptCount"]) + 1
        if result["status"] == "sent":
            alert_repository.record_delivery(alert["id"], "email", "sent")
        else:
            next_retry_at = _next_retry_at(next_attempt)
            alert_repository.record_delivery(
                alert["id"],
                "email",
                "failed",
                error=result.get("error"),
                next_retry_at=next_retry_at,
            )
            if next_retry_at is None:
                import monitoring_health
                monitoring_health.record_email_final_failure(
                    alert,
                    result.get("error"),
                )
        retried += 1
    return retried


def process_news_items(news_items) -> int:
    from event_classifier import classify_official_event
    import news_api

    watchlist = {
        str(item.get("stockCode") or "").strip().lower(): str(
            item.get("stockName") or ""
        ).strip()
        for item in database.get_watchlist()
    }
    source_counts = news_api.build_independent_source_counts(news_items)
    created_count = 0
    for item in news_items:
        symbol = str(item.get("symbol") or "").strip().lower()
        if symbol not in watchlist:
            continue
        if not news_api.is_source_published_today(item):
            continue
        classification = news_api.classify_news_item(item, source_counts)
        event = None
        if (
            classification.get("credibility_level") == "S"
            and classification.get("content_type") == "official_announcement"
        ):
            event = classify_official_event({
                **item,
                "stock_name": watchlist[symbol] or item.get("stock_name") or symbol,
            })
        if event is None:
            if classification.get("credibility_level") not in {"A", "B"}:
                continue
            if classification.get("priority") not in {"P1", "P2"}:
                continue
            source_event_id = str(item.get("id") or "").strip()
            source_url = str(item.get("url") or "").strip()
            if not source_event_id or not source_url.startswith(("http://", "https://")):
                continue
            time_metadata = news_api.get_source_time_metadata(item)
            published_at = None
            ctime = item.get("ctime")
            if ctime is not None:
                try:
                    published_at = datetime.fromtimestamp(
                        float(ctime),
                        tz=market_calendar.SHANGHAI_TZ,
                    ).isoformat(timespec="seconds")
                except (TypeError, ValueError, OSError):
                    published_at = None
            event = {
                "symbol": symbol,
                "stock_name": watchlist[symbol] or item.get("stock_name") or symbol,
                "event_type": "traceable_news_event",
                "direction": classification["direction"],
                "priority": classification["priority"],
                "evidence_level": classification["credibility_level"],
                "title": str(item.get("title") or "").strip(),
                "summary": (
                    f"{classification['verification_status']}的定向资讯触发规则；"
                    "具体影响仍需结合原文和后续披露判断。"
                ),
                "source": str(item.get("source") or "").strip(),
                "source_url": source_url,
                "source_event_id": source_event_id,
                "published_at": published_at or time_metadata["publish_time"],
            }
        if event is None:
            continue
        alert, created = alert_repository.save_alert_event(event)
        if not created:
            continue
        process_new_alert(alert)
        created_count += 1
    return created_count


def process_official_news(news_items) -> int:
    """兼容旧调用名；官方公告和可追溯P2定向资讯统一处理。"""
    return process_news_items(news_items)
