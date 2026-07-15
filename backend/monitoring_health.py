from copy import deepcopy
from datetime import datetime
from threading import Lock
from typing import Any, Dict, Iterable, Optional, Set

import alert_repository
import asset_context
import market_calendar


_LOCK = Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}
_WATCHLIST_SYNC: Dict[str, Any] = {}


def _now_iso() -> str:
    return datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds")


def _system_event(
    issue_type: str,
    subject: str,
    title: str,
    summary: str,
    priority: str = "P2",
) -> Dict[str, Any]:
    now = datetime.now(market_calendar.SHANGHAI_TZ)
    return {
        "symbol": "SYSTEM",
        "stock_name": "系统监测",
        "event_type": "system_health",
        "direction": "negative",
        "priority": priority,
        "evidence_level": "A",
        "title": title,
        "summary": summary,
        "source": "系统健康审计",
        "source_url": None,
        "source_event_id": f"health:{now.date().isoformat()}:{issue_type}:{subject}",
        "published_at": now.isoformat(timespec="seconds"),
    }


def _save_system_alert(event: Dict[str, Any]) -> bool:
    alert, created = alert_repository.save_alert_event(event)
    if created:
        alert_repository.record_delivery(alert["id"], "site", "sent")
    return created


def reset_runtime_health() -> None:
    with _LOCK:
        _TASKS.clear()
        _WATCHLIST_SYNC.clear()


def record_task_started(task_name: str) -> None:
    with _LOCK:
        current = _TASKS.get(task_name, {})
        _TASKS[task_name] = {
            **current,
            "status": "running",
            "lastStartedAt": _now_iso(),
            "lastError": None,
        }


def record_task_success(task_name: str, item_count: Optional[int] = None) -> None:
    with _LOCK:
        current = _TASKS.get(task_name, {})
        _TASKS[task_name] = {
            **current,
            "status": "healthy",
            "lastSuccessAt": _now_iso(),
            "lastError": None,
            "itemCount": item_count,
            "consecutiveFailures": 0,
        }


def record_task_failure(task_name: str, error: Exception) -> None:
    alert_event = None
    with _LOCK:
        current = _TASKS.get(task_name, {})
        previous_failures = int(current.get("consecutiveFailures") or 0)
        consecutive_failures = previous_failures + 1
        failure_episode = int(current.get("failureEpisode") or 0)
        if previous_failures == 0:
            failure_episode += 1
        _TASKS[task_name] = {
            **current,
            "status": "failed",
            "lastFailedAt": _now_iso(),
            "lastError": type(error).__name__,
            "consecutiveFailures": consecutive_failures,
            "failureEpisode": failure_episode,
        }
        if consecutive_failures == 2:
            alert_event = _system_event(
                "source_failure",
                f"{task_name}:{failure_episode}",
                f"数据源连续失败：{task_name}",
                (
                    f"{task_name} 已连续两个周期失败，最近错误类型为"
                    f" {type(error).__name__}。系统没有把旧数据冒充为本周期结果。"
                ),
            )
    if alert_event is not None:
        _save_system_alert(alert_event)


def get_task_states() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        return deepcopy(_TASKS)


def _watchlist_codes(items: Iterable[Dict[str, Any]]) -> Set[str]:
    return {
        asset_context.normalize_symbol(item.get("stockCode", ""))
        for item in items
        if asset_context.normalize_symbol(item.get("stockCode", ""))
    }


def audit_watchlist_sync(
    frontend_items: Iterable[Dict[str, Any]],
    backend_items: Iterable[Dict[str, Any]],
) -> bool:
    frontend_codes = _watchlist_codes(frontend_items)
    backend_codes = _watchlist_codes(backend_items)
    sync_state = {
        "status": "synced" if frontend_codes == backend_codes else "mismatched",
        "frontendCount": len(frontend_codes),
        "backendCount": len(backend_codes),
        "lastCheckedAt": _now_iso(),
    }
    with _LOCK:
        _WATCHLIST_SYNC.clear()
        _WATCHLIST_SYNC.update(sync_state)
    if frontend_codes == backend_codes:
        return True
    frontend_only = sorted(frontend_codes - backend_codes)
    backend_only = sorted(backend_codes - frontend_codes)
    subject = f"front-{','.join(sorted(frontend_codes))}|back-{','.join(sorted(backend_codes))}"
    _save_system_alert(_system_event(
        "watchlist_mismatch",
        subject,
        "前端与后端监测列表不同步",
        (
            f"仅前端存在：{', '.join(frontend_only) or '无'}；"
            f"仅后端存在：{', '.join(backend_only) or '无'}。"
            "系统未自动覆盖任一侧列表。"
        ),
    ))
    return False


def get_watchlist_sync_state() -> Dict[str, Any]:
    with _LOCK:
        if not _WATCHLIST_SYNC:
            return {
                "status": "not_checked",
                "frontendCount": None,
                "backendCount": None,
                "lastCheckedAt": None,
            }
        return deepcopy(_WATCHLIST_SYNC)


def _parse_source_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=market_calendar.SHANGHAI_TZ)
    return parsed.astimezone(market_calendar.SHANGHAI_TZ)


def audit_stale_watchlist(
    watchlist: Iterable[Dict[str, Any]],
    trading_symbols: Set[str],
    expected_seconds: int,
    now: Optional[datetime] = None,
) -> int:
    current = (now or datetime.now(market_calendar.SHANGHAI_TZ)).astimezone(
        market_calendar.SHANGHAI_TZ
    )
    stale_count = 0
    for item in watchlist:
        symbol = asset_context.normalize_symbol(item.get("stockCode", ""))
        if not symbol or symbol not in trading_symbols:
            continue
        state = alert_repository.get_latest_signal_state(symbol)
        source_time = _parse_source_time((state or {}).get("sourceTime"))
        age_seconds = None if source_time is None else (current - source_time).total_seconds()
        if age_seconds is not None and age_seconds <= expected_seconds:
            continue
        stock_name = str(item.get("stockName") or symbol).strip()
        age_text = "尚无有效更新时间" if age_seconds is None else f"已 {int(age_seconds)} 秒未更新"
        _save_system_alert(_system_event(
            "stale_watchlist",
            symbol,
            f"监测股票超过预期时间未更新：{stock_name}",
            (
                f"{stock_name}（{symbol}）超过预期时间未更新：{age_text}，"
                f"预期上限为 {expected_seconds} 秒。"
                "该检查仅在对应市场交易时段执行。"
            ),
        ))
        stale_count += 1
    return stale_count


def record_email_final_failure(alert: Dict[str, Any], error: Optional[str]) -> None:
    alert_id = str(alert.get("id") or "unknown")
    title = str(alert.get("title") or alert_id)
    _save_system_alert(_system_event(
        "email_final_failure",
        alert_id,
        "提醒邮件达到最终重试次数仍失败",
        f"提醒“{title}”的邮件已完成全部重试仍未送达：{error or '上游未提供错误原因'}。",
        priority="P1",
    ))


def record_mapping_failure(symbol: str, stock_name: str, reason: str) -> None:
    normalized = asset_context.normalize_symbol(symbol) or "unknown"
    display_name = str(stock_name or normalized).strip()
    _save_system_alert(_system_event(
        "mapping_failure",
        normalized,
        f"股票代码或公司名称无法精确映射：{display_name}",
        f"{display_name}（{normalized}）无法精确映射：{reason}。该证券不会生成高优先级业务联动判断。",
    ))
