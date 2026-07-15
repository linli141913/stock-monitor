import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import event_classifier
import market_calendar
import news_api
import risk_engine


SaveEvent = Callable[[Dict[str, Any]], Tuple[Dict[str, Any], bool]]

_EXPECTED_FIELD_NAMES = {
    "event_type": ("event_type", "eventType"),
    "source_event_id": ("source_event_id", "sourceEventId"),
    "stock_name": ("stock_name", "stockName"),
}


def load_cases(path: Path) -> List[Dict[str, Any]]:
    cases = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"第 {line_number} 行不是对象")
            cases.append(value)
    return cases


def _parse_datetime(value: Any) -> Optional[datetime]:
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


def _field(event: Dict[str, Any], expected_name: str) -> Any:
    names = _EXPECTED_FIELD_NAMES.get(expected_name, (expected_name,))
    for name in names:
        if name in event:
            return event[name]
    return None


def _matches(event: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    return all(_field(event, name) == value for name, value in expected.items())


def _build_event(
    kind: str,
    observation: Dict[str, Any],
    case: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    item = {
        key: value
        for key, value in observation.items()
        if key != "available_at"
    }
    if kind == "official_event":
        return event_classifier.classify_official_event(item)
    if kind == "market_risk":
        result = risk_engine.evaluate_market_risk(
            item,
            list(case.get("turnover_history") or []),
            verified_history=case.get("verified_history"),
        )
        return risk_engine.build_risk_alert_event(item, result)
    if kind == "linkage_risk":
        result = risk_engine.evaluate_linkage_risk(item)
        return risk_engine.build_linkage_alert_event(item, result)
    raise ValueError(f"不支持的回放类型：{kind}")


def _blocked_reason(
    kind: str,
    observation: Dict[str, Any],
    as_of: datetime,
) -> Optional[str]:
    available_at = _parse_datetime(observation.get("available_at"))
    if available_at is None or available_at > as_of:
        return "future_evidence"

    if kind == "official_event":
        published_at = _parse_datetime(
            observation.get("published_at") or observation.get("publishedAt")
        )
        if published_at is None or published_at > as_of:
            return "future_evidence"
        if not news_api.is_source_published_today(observation, now=as_of):
            return "stale_source_item"
        return None

    source_time = _parse_datetime(observation.get("source_time"))
    if source_time is None or source_time > as_of:
        return "future_evidence"
    return None


def _memory_save_event():
    stored: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def save(event: Dict[str, Any]):
        key = (
            str(event.get("symbol") or ""),
            str(event.get("source_event_id") or ""),
            str(event.get("source") or ""),
        )
        existing = stored.get(key)
        if existing is not None:
            return existing, False
        saved = dict(event)
        saved["id"] = "fixture:" + ":".join(key)
        stored[key] = saved
        return saved, True

    return save


def _replay_case(
    case: Dict[str, Any],
    save_event: SaveEvent,
) -> Dict[str, Any]:
    case_id = str(case.get("id") or "").strip()
    kind = str(case.get("kind") or "").strip()
    as_of = _parse_datetime(case.get("as_of"))
    if not case_id or as_of is None:
        raise ValueError("每条回放样本必须包含 id 和带时区的 as_of")

    actual_by_id: Dict[str, Dict[str, Any]] = {}
    suppressed_duplicates = 0
    future_evidence_blocked = 0
    stale_source_items_rejected = 0
    for observation in list(case.get("observations") or []):
        reason = _blocked_reason(kind, observation, as_of)
        if reason == "future_evidence":
            future_evidence_blocked += 1
            continue
        if reason == "stale_source_item":
            stale_source_items_rejected += 1
            continue

        event = _build_event(kind, observation, case)
        if event is None:
            continue
        saved, changed = save_event(event)
        alert_id = str(saved.get("id") or "")
        if not changed:
            suppressed_duplicates += 1
            continue
        actual_by_id[alert_id] = saved

    expected_alerts = list(case.get("expected_alerts") or [])
    actual_alerts = list(actual_by_id.values())
    remaining_actual = list(actual_alerts)
    missed = 0
    for expected in expected_alerts:
        match_index = next(
            (
                index
                for index, event in enumerate(remaining_actual)
                if _matches(event, expected)
            ),
            None,
        )
        if match_index is None:
            missed += 1
        else:
            remaining_actual.pop(match_index)

    false_positive = len(remaining_actual)
    duplicate_alerts = max(
        0,
        len(actual_alerts)
        - len({
            (
                _field(item, "symbol"),
                _field(item, "source_event_id"),
                _field(item, "source"),
            )
            for item in actual_alerts
        }),
    )
    passed = missed == 0 and false_positive == 0 and duplicate_alerts == 0
    return {
        "id": case_id,
        "scope": str(case.get("scope") or "fixed_fixture"),
        "status": "passed" if passed else "failed",
        "expectedAlerts": len(expected_alerts),
        "generatedAlerts": len(actual_alerts),
        "missedAlerts": missed,
        "falsePositiveAlerts": false_positive,
        "duplicateAlerts": duplicate_alerts,
        "suppressedDuplicates": suppressed_duplicates,
        "futureEvidenceBlocked": future_evidence_blocked,
        "staleSourceItemsRejected": stale_source_items_rejected,
    }


def replay_cases(
    cases: Iterable[Dict[str, Any]],
    *,
    save_event: Optional[SaveEvent] = None,
) -> Dict[str, Any]:
    persist = save_event or _memory_save_event()
    results = [_replay_case(case, persist) for case in cases]
    historical_count = sum(
        item["scope"] == "historical_verified" for item in results
    )
    failed_cases = sum(item["status"] == "failed" for item in results)
    return {
        "status": "passed" if failed_cases == 0 else "failed",
        "scope": "deterministic_fixture",
        "totalCases": len(results),
        "passedCases": len(results) - failed_cases,
        "failedCases": failed_cases,
        "expectedAlerts": sum(item["expectedAlerts"] for item in results),
        "generatedAlerts": sum(item["generatedAlerts"] for item in results),
        "missedAlerts": sum(item["missedAlerts"] for item in results),
        "falsePositiveAlerts": sum(
            item["falsePositiveAlerts"] for item in results
        ),
        "duplicateAlerts": sum(item["duplicateAlerts"] for item in results),
        "suppressedDuplicates": sum(
            item["suppressedDuplicates"] for item in results
        ),
        "futureEvidenceBlocked": sum(
            item["futureEvidenceBlocked"] for item in results
        ),
        "staleSourceItemsRejected": sum(
            item["staleSourceItemsRejected"] for item in results
        ),
        "historicalVerifiedCases": historical_count,
        "historicalMetrics": {
            "status": "not_measured",
            "reason": (
                "没有独立核验过的真实历史事件集，不能计算真实覆盖率、"
                "误报率或 P95 延迟"
            ),
        },
        "cases": results,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    fixed_status = "通过" if report.get("status") == "passed" else "未通过"
    historical = report.get("historicalMetrics") or {}
    historical_status = (
        "暂无判断"
        if historical.get("status") == "not_measured"
        else str(historical.get("status") or "未知")
    )
    lines = [
        "# 提醒系统验收报告",
        "",
        f"- 固定 Fixture：{fixed_status}",
        f"- 真实历史样本：{historical_status}",
        f"- 样本数：{report.get('totalCases', 0)}",
        f"- 漏报：{report.get('missedAlerts', 0)}",
        f"- 误报：{report.get('falsePositiveAlerts', 0)}",
        f"- 重复提醒：{report.get('duplicateAlerts', 0)}",
        f"- 已拦截未来信息：{report.get('futureEvidenceBlocked', 0)}",
        f"- 已拒绝非当天来源：{report.get('staleSourceItemsRejected', 0)}",
        "",
        "## 真实历史指标限制",
        "",
        str(historical.get("reason") or "暂无说明"),
        "",
        "固定 Fixture 只验证规则、时间截断和去重行为，不能代替真实历史回放。",
        "",
        "## 样本明细",
        "",
        "| 样本 | 结果 | 预期提醒 | 实际提醒 | 漏报 | 误报 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report.get("cases") or []:
        status = "通过" if item.get("status") == "passed" else "未通过"
        lines.append(
            "| {id} | {status} | {expected} | {generated} | {missed} | {false_positive} |".format(
                id=item.get("id"),
                status=status,
                expected=item.get("expectedAlerts", 0),
                generated=item.get("generatedAlerts", 0),
                missed=item.get("missedAlerts", 0),
                false_positive=item.get("falsePositiveAlerts", 0),
            )
        )
    return "\n".join(lines) + "\n"
