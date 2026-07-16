import json
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
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

_HISTORICAL_HORIZONS = ("5m", "30m", "close", "next_day", "5_day")
_MIN_HISTORICAL_CASES = 20
_MIN_HISTORICAL_DATES = 10
_HISTORICAL_REQUIRED_FIELDS = {
    "official_event": (
        "available_at",
        "id",
        "symbol",
        "title",
        "source",
        "published_at",
    ),
    "market_risk": (
        "available_at",
        "symbol",
        "source_time",
        "change_percent",
        "high",
        "low",
        "previous_close",
        "volume_ratio",
        "turnover_rate",
        "turnover_amount",
    ),
    "linkage_risk": (
        "available_at",
        "symbol",
        "source_time",
        "sector",
        "overseas",
    ),
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


def _is_date_precision(observation: Dict[str, Any]) -> bool:
    if str(observation.get("published_at_precision") or "") == "date":
        return True
    text = str(
        observation.get("published_at")
        or observation.get("publishedAt")
        or ""
    ).strip()
    return len(text) == 10 and text[4:5] == "-" and text[7:8] == "-"


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_change(start: Any, end: Any) -> Optional[float]:
    start_value = _number(start)
    end_value = _number(end)
    if start_value is None or start_value <= 0 or end_value is None:
        return None
    return round((end_value / start_value - 1) * 100, 4)


def _percentile_95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * 0.95
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(
        ordered[lower] * (1 - weight) + ordered[upper] * weight,
        3,
    )


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
        if published_at is None:
            return "future_evidence"
        if _is_date_precision(observation):
            day_gap = (published_at.date() - as_of.date()).days
            if day_gap not in {0, 1}:
                return "stale_source_item" if day_gap < 0 else "future_evidence"
            if day_gap == 1 and as_of.hour < 18:
                return "future_evidence"
            return None
        if published_at > as_of:
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


def _historical_missing_fields(case: Dict[str, Any]) -> List[str]:
    kind = str(case.get("kind") or "")
    missing = []
    source_url = str(case.get("source_url") or "").strip()
    if not source_url.startswith(("http://", "https://")):
        missing.append("source_url")
    if not str(case.get("expected_rule") or "").strip():
        missing.append("expected_rule")
    if _parse_datetime(case.get("latest_alert_at")) is None:
        missing.append("latest_alert_at")

    required_fields = _HISTORICAL_REQUIRED_FIELDS.get(kind, ())
    observations = list(case.get("observations") or [])
    if not observations:
        missing.append("observations")
    for index, observation in enumerate(observations):
        for field in required_fields:
            if field not in observation or observation.get(field) is None:
                missing.append(f"observations[{index}].{field}")
    return sorted(set(missing))


def _observation_latency_seconds(
    kind: str,
    observation: Dict[str, Any],
) -> Optional[float]:
    available_at = _parse_datetime(observation.get("available_at"))
    if available_at is None:
        return None
    if kind == "official_event":
        if _is_date_precision(observation):
            return None
        event_time = _parse_datetime(
            observation.get("published_at") or observation.get("publishedAt")
        )
    else:
        event_time = _parse_datetime(observation.get("source_time"))
    if event_time is None or available_at < event_time:
        return None
    return round((available_at - event_time).total_seconds(), 3)


def _evidence_times(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    kind = str(case.get("kind") or "")
    result = []
    for observation in list(case.get("observations") or []):
        if kind == "official_event":
            event_time = (
                observation.get("published_at")
                or observation.get("publishedAt")
            )
            precision = "date" if _is_date_precision(observation) else "datetime"
        else:
            event_time = observation.get("source_time")
            precision = "datetime"
        result.append({
            "eventTime": event_time,
            "eventTimePrecision": precision,
            "availableAt": observation.get("available_at"),
        })
    return result


def _build_event_study(case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    reaction = case.get("market_reaction")
    if not isinstance(reaction, dict):
        return None

    source_url = str(reaction.get("source_url") or "").strip()
    entry = reaction.get("entry") or {}
    entry_stock = _number(entry.get("stock_price"))
    entry_index = _number(entry.get("index_price"))
    entry_sector = _number(entry.get("sector_price"))
    horizons = {}
    measured_horizons = 0
    for name in _HISTORICAL_HORIZONS:
        target = (reaction.get("horizons") or {}).get(name) or {}
        if target.get("status") == "not_measured":
            horizons[name] = {
                "status": "not_measured",
                "reason": str(target.get("reason") or "缺少真实行情数据"),
                "stockReturnPct": None,
                "indexReturnPct": None,
                "excessIndexPct": None,
                "sectorReturnPct": None,
                "excessSectorPct": None,
            }
            continue

        stock_return = _percent_change(entry_stock, target.get("stock_price"))
        if stock_return is None:
            horizons[name] = {
                "status": "not_measured",
                "reason": str(target.get("reason") or "个股起点或终点价格缺失"),
                "stockReturnPct": None,
                "indexReturnPct": None,
                "excessIndexPct": None,
                "sectorReturnPct": None,
                "excessSectorPct": None,
            }
            continue

        measured_horizons += 1
        index_return = _percent_change(entry_index, target.get("index_price"))
        sector_return = _percent_change(entry_sector, target.get("sector_price"))
        horizons[name] = {
            "status": "measured",
            "at": target.get("at"),
            "stockReturnPct": stock_return,
            "indexStatus": "measured" if index_return is not None else "not_measured",
            "indexReturnPct": index_return,
            "excessIndexPct": (
                round(stock_return - index_return, 4)
                if index_return is not None
                else None
            ),
            "indexReason": (
                None if index_return is not None
                else str(target.get("index_reason") or "主要指数行情缺失")
            ),
            "sectorStatus": "measured" if sector_return is not None else "not_measured",
            "sectorReturnPct": sector_return,
            "excessSectorPct": (
                round(stock_return - sector_return, 4)
                if sector_return is not None
                else None
            ),
            "sectorReason": (
                None if sector_return is not None
                else str(target.get("sector_reason") or "当时板块口径或行情缺失")
            ),
        }

    expected_alert = (list(case.get("expected_alerts") or []) or [{}])[0]
    direction = str(expected_alert.get("direction") or "neutral")
    high_return = _percent_change(entry_stock, reaction.get("window_high"))
    low_return = _percent_change(entry_stock, reaction.get("window_low"))
    favorable = None
    adverse = None
    if direction == "positive" and high_return is not None and low_return is not None:
        favorable = round(max(0.0, high_return), 4)
        adverse = round(max(0.0, -low_return), 4)
    elif direction == "negative" and high_return is not None and low_return is not None:
        favorable = round(max(0.0, -low_return), 4)
        adverse = round(max(0.0, high_return), 4)

    tradability = reaction.get("tradability") or {}
    if tradability.get("status") not in {"tradable", "restricted", "not_measured"}:
        tradability = {
            "status": "not_measured",
            "suspended": None,
            "limit_locked": None,
            "reason": "停牌和涨跌停可交易性证据缺失",
        }
    else:
        tradability = dict(tradability)

    return {
        "status": "measured" if measured_horizons else "not_measured",
        "sourceUrl": source_url or None,
        "indexSourceUrl": str(reaction.get("index_source_url") or "").strip() or None,
        "measuredHorizons": measured_horizons,
        "horizons": horizons,
        "maxFavorableMovePct": favorable,
        "maxAdverseMovePct": adverse,
        "tradability": tradability,
    }


def _aggregate_horizon(
    studies: List[Dict[str, Any]],
    name: str,
) -> Dict[str, Any]:
    rows = [
        study.get("horizons", {}).get(name) or {}
        for study in studies
    ]
    measured = [row for row in rows if row.get("status") == "measured"]
    stock_values = [
        float(row["stockReturnPct"])
        for row in measured
        if row.get("stockReturnPct") is not None
    ]
    index_values = [
        float(row["excessIndexPct"])
        for row in measured
        if row.get("excessIndexPct") is not None
    ]
    sector_values = [
        float(row["excessSectorPct"])
        for row in measured
        if row.get("excessSectorPct") is not None
    ]
    if not stock_values:
        reasons = [str(row.get("reason") or "") for row in rows if row.get("reason")]
        return {
            "status": "not_measured",
            "sampleSize": 0,
            "reason": reasons[0] if reasons else "没有可测量的真实行情样本",
            "medianStockReturnPct": None,
            "indexStatus": "not_measured",
            "medianExcessIndexPct": None,
            "sectorStatus": "not_measured",
            "medianExcessSectorPct": None,
        }
    return {
        "status": "measured",
        "sampleSize": len(stock_values),
        "medianStockReturnPct": round(median(stock_values), 4),
        "indexStatus": "measured" if index_values else "not_measured",
        "indexSampleSize": len(index_values),
        "medianExcessIndexPct": (
            round(median(index_values), 4) if index_values else None
        ),
        "sectorStatus": "measured" if sector_values else "not_measured",
        "sectorSampleSize": len(sector_values),
        "medianExcessSectorPct": (
            round(median(sector_values), 4) if sector_values else None
        ),
        "sectorReason": (
            None if sector_values
            else "缺少可追溯的当时板块成分口径或板块行情"
        ),
    }


def _build_historical_metrics(
    cases: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    paired = [
        (case, result)
        for case, result in zip(cases, results)
        if str(case.get("scope") or "") == "historical_verified"
    ]
    if not paired:
        return {
            "status": "not_measured",
            "reason": (
                "没有独立核验过的真实历史事件集，不能计算真实覆盖率、"
                "误报率或 P95 延迟"
            ),
        }

    sample_size = len(paired)
    dates = {
        parsed.date().isoformat()
        for case, _result in paired
        for parsed in [_parse_datetime(case.get("as_of"))]
        if parsed is not None
    }
    sufficient = (
        sample_size >= _MIN_HISTORICAL_CASES
        and len(dates) >= _MIN_HISTORICAL_DATES
    )
    expected = sum(result["expectedAlerts"] for _case, result in paired)
    missed = sum(result["missedAlerts"] for _case, result in paired)
    data_missing_cases = sum(
        1 for _case, result in paired if result.get("missingFields")
    )
    latencies = [
        float(value)
        for _case, result in paired
        for value in result.get("discoveryLatenciesSeconds") or []
    ]
    studies = [
        study
        for _case, result in paired
        for study in [result.get("eventStudy")]
        if isinstance(study, dict)
    ]

    groups = {}
    for case, result in paired:
        expected_alert = (list(case.get("expected_alerts") or []) or [{}])[0]
        key = (
            str(case.get("expected_rule") or "未标注规则"),
            str(expected_alert.get("priority") or "未标注优先级"),
        )
        group = groups.setdefault(key, {
            "rule": key[0],
            "priority": key[1],
            "sampleSize": 0,
            "passedCases": 0,
            "eventStudySamples": 0,
            "_studies": [],
            "_dates": set(),
        })
        group["sampleSize"] += 1
        as_of = _parse_datetime(case.get("as_of"))
        if as_of is not None:
            group["_dates"].add(as_of.date().isoformat())
        if result.get("status") == "passed":
            group["passedCases"] += 1
        study = result.get("eventStudy") or {}
        if study.get("status") == "measured":
            group["eventStudySamples"] += 1
            group["_studies"].append(study)

    grouped_results = []
    for group in groups.values():
        group_studies = group.pop("_studies")
        group_dates = group.pop("_dates")
        favorable = [
            float(study["maxFavorableMovePct"])
            for study in group_studies
            if study.get("maxFavorableMovePct") is not None
        ]
        adverse = [
            float(study["maxAdverseMovePct"])
            for study in group_studies
            if study.get("maxAdverseMovePct") is not None
        ]
        tradability = {
            "tradable": 0,
            "restricted": 0,
            "not_measured": 0,
            "suspended": 0,
            "limitLocked": 0,
        }
        for study in group_studies:
            trading = study.get("tradability") or {}
            status = str(trading.get("status") or "not_measured")
            if status not in {"tradable", "restricted", "not_measured"}:
                status = "not_measured"
            tradability[status] += 1
            if trading.get("suspended") is True:
                tradability["suspended"] += 1
            if trading.get("limit_locked") is True:
                tradability["limitLocked"] += 1
        group.update({
            "distinctDates": len(group_dates),
            "horizons": {
                name: _aggregate_horizon(group_studies, name)
                for name in _HISTORICAL_HORIZONS
            },
            "medianMaxFavorableMovePct": (
                round(median(favorable), 4) if favorable else None
            ),
            "medianMaxAdverseMovePct": (
                round(median(adverse), 4) if adverse else None
            ),
            "tradability": tradability,
            "informationValueStatus": "not_measured",
            "informationValueReason": (
                "该规则和优先级分组的真实样本及日期覆盖不足，"
                "当前只展示样本内市场反应"
            ),
        })
        grouped_results.append(group)

    measured_study_count = sum(
        1 for study in studies if study.get("status") == "measured"
    )
    event_study = {
        "status": (
            "measured"
            if sufficient and measured_study_count > 0
            else "not_measured"
        ),
        "reason": (
            "真实样本数量和日期覆盖不足，当前只展示样本内市场反应，"
            "不判断提醒是否具有稳定信息价值"
            if not sufficient
            else (
                "已按真实样本计算市场反应"
                if measured_study_count > 0
                else "没有可测量的真实市场反应数据"
            )
        ),
        "measuredSamples": measured_study_count,
        "horizons": {
            name: _aggregate_horizon(studies, name)
            for name in _HISTORICAL_HORIZONS
        },
        "groups": grouped_results,
    }

    reason = (
        "真实历史样本和日期覆盖已达到最低描述门槛"
        if sufficient
        else (
            f"样本不足：当前只有 {sample_size} 条真实样本、覆盖 {len(dates)} 个日期；"
            f"至少需要 {_MIN_HISTORICAL_CASES} 条且覆盖 {_MIN_HISTORICAL_DATES} 个日期，"
            "才能判断覆盖率和提醒信息价值"
        )
    )
    return {
        "status": "measured" if sufficient else "not_measured",
        "reason": reason,
        "sampleSize": sample_size,
        "distinctDates": len(dates),
        "minimumSampleSize": _MIN_HISTORICAL_CASES,
        "minimumDistinctDates": _MIN_HISTORICAL_DATES,
        "sampleRecall": round((expected - missed) / expected, 4) if expected else None,
        "sampleFalsePositiveAlerts": sum(
            result["falsePositiveAlerts"] for _case, result in paired
        ),
        "sampleDuplicateAlerts": sum(
            result["duplicateAlerts"] for _case, result in paired
        ),
        "dataMissingCases": data_missing_cases,
        "dataMissingRate": round(data_missing_cases / sample_size, 4),
        "measuredLatencySamples": len(latencies),
        "p95DiscoveryDelaySeconds": _percentile_95(latencies),
        "deadlineMisses": sum(
            int(result.get("deadlineMisses") or 0) for _case, result in paired
        ),
        "eventStudy": event_study,
    }


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
    discovery_latencies = []
    generated_at = []
    for observation in list(case.get("observations") or []):
        reason = _blocked_reason(kind, observation, as_of)
        if reason == "future_evidence":
            future_evidence_blocked += 1
            continue
        if reason == "stale_source_item":
            stale_source_items_rejected += 1
            continue

        latency = _observation_latency_seconds(kind, observation)
        if latency is not None:
            discovery_latencies.append(latency)

        event = _build_event(kind, observation, case)
        if event is None:
            continue
        saved, changed = save_event(event)
        alert_id = str(saved.get("id") or "")
        if not changed:
            suppressed_duplicates += 1
            continue
        actual_by_id[alert_id] = saved
        available_at = _parse_datetime(observation.get("available_at"))
        if available_at is not None:
            generated_at.append(available_at)

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
    latest_alert_at = _parse_datetime(case.get("latest_alert_at"))
    deadline_misses = 0
    if expected_alerts and latest_alert_at is not None:
        if not generated_at or min(generated_at) > latest_alert_at:
            deadline_misses = len(expected_alerts)
    is_historical = str(case.get("scope") or "") == "historical_verified"
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
        "missingFields": _historical_missing_fields(case) if is_historical else [],
        "discoveryLatenciesSeconds": discovery_latencies if is_historical else [],
        "deadlineMisses": deadline_misses if is_historical else 0,
        "eventStudy": _build_event_study(case) if is_historical else None,
        "sourceUrl": case.get("source_url") if is_historical else None,
        "expectedRule": case.get("expected_rule") if is_historical else None,
        "asOf": case.get("as_of") if is_historical else None,
        "latestAlertAt": case.get("latest_alert_at") if is_historical else None,
        "evidenceTimes": _evidence_times(case) if is_historical else [],
    }


def replay_cases(
    cases: Iterable[Dict[str, Any]],
    *,
    save_event: Optional[SaveEvent] = None,
) -> Dict[str, Any]:
    case_list = list(cases)
    persist = save_event or _memory_save_event()
    results = [_replay_case(case, persist) for case in case_list]
    historical_count = sum(
        item["scope"] == "historical_verified" for item in results
    )
    fixed_count = sum(item["scope"] != "historical_verified" for item in results)
    failed_cases = sum(item["status"] == "failed" for item in results)
    if historical_count and fixed_count:
        report_scope = "fixed_and_historical_replay"
    elif historical_count:
        report_scope = "historical_verified_replay"
    else:
        report_scope = "deterministic_fixture"
    return {
        "status": "passed" if failed_cases == 0 else "failed",
        "scope": report_scope,
        "totalCases": len(results),
        "fixedFixtureCases": fixed_count,
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
        "historicalMetrics": _build_historical_metrics(case_list, results),
        "cases": results,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    fixed_cases = [
        item for item in report.get("cases") or []
        if item.get("scope") != "historical_verified"
    ]
    fixed_status = (
        "未运行"
        if not fixed_cases
        else (
            "通过"
            if all(item.get("status") == "passed" for item in fixed_cases)
            else "未通过"
        )
    )
    historical = report.get("historicalMetrics") or {}
    historical_status = (
        "暂无判断"
        if historical.get("status") == "not_measured"
        else str(historical.get("status") or "未知")
    )
    historical_count = int(report.get("historicalVerifiedCases") or 0)
    if historical_count:
        historical_label = (
            f"{historical_status}（{historical_count} 条，"
            f"覆盖 {historical.get('distinctDates', 0)} 个日期）"
        )
    else:
        historical_label = historical_status
    lines = [
        "# 提醒系统验收报告",
        "",
        f"- 固定 Fixture：{fixed_status}",
        f"- 真实历史样本：{historical_label}",
        f"- 样本数：{report.get('totalCases', 0)}",
        f"- 漏报：{report.get('missedAlerts', 0)}",
        f"- 误报：{report.get('falsePositiveAlerts', 0)}",
        f"- 重复提醒：{report.get('duplicateAlerts', 0)}",
        f"- 已拦截未来信息：{report.get('futureEvidenceBlocked', 0)}",
        f"- 已拒绝非当天来源：{report.get('staleSourceItemsRejected', 0)}",
    ]
    if historical_count:
        sample_recall = historical.get("sampleRecall")
        missing_rate = historical.get("dataMissingRate")
        delay = historical.get("p95DiscoveryDelaySeconds")
        lines.extend([
            f"- 样本内召回率：{sample_recall * 100:.1f}%（只描述当前真实样本）"
            if sample_recall is not None
            else "- 样本内召回率：暂无判断",
            f"- 样本内数据缺失率：{missing_rate * 100:.1f}%"
            if missing_rate is not None
            else "- 样本内数据缺失率：暂无判断",
            f"- P95 发现延迟：{delay:.3f} 秒（仅统计时间精度足够的样本）"
            if delay is not None
            else "- P95 发现延迟：暂无判断",
            f"- 超过最迟提醒时间：{historical.get('deadlineMisses', 0)}",
        ])
    lines.extend([
        "",
        "## 真实历史指标限制",
        "",
        str(historical.get("reason") or "暂无说明"),
        "",
        "固定 Fixture 只验证规则、时间截断和去重行为，不能代替真实历史回放。",
    ])

    event_study = historical.get("eventStudy") or {}
    if historical_count:
        event_status = (
            "暂无判断"
            if event_study.get("status") == "not_measured"
            else "已测量"
        )
        lines.extend([
            "",
            "## 提醒后的市场反应",
            "",
            f"- 结论：{event_status}",
            f"- 可测样本：{event_study.get('measuredSamples', 0)}",
            f"- 说明：{event_study.get('reason') or '暂无说明'}",
            "",
            "| 时点 | 状态 | 样本 | 个股中位表现 | 相对指数中位超额 | 相对板块中位超额 |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ])
        for name in _HISTORICAL_HORIZONS:
            item = (event_study.get("horizons") or {}).get(name) or {}
            status = "已测量" if item.get("status") == "measured" else "未测量"
            def value_text(value):
                return "暂无" if value is None else f"{value:.2f}%"
            lines.append(
                f"| {name} | {status} | {item.get('sampleSize', 0)} | "
                f"{value_text(item.get('medianStockReturnPct'))} | "
                f"{value_text(item.get('medianExcessIndexPct'))} | "
                f"{value_text(item.get('medianExcessSectorPct'))} |"
            )
        lines.extend([
            "",
            "### 按规则与优先级分组",
            "",
            "| 规则 | 优先级 | 样本 | 5分钟中位表现 | 收盘中位表现 | 方向一致最大波动 | 方向相反最大波动 | 可交易/受限 | 结论 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ])
        for group in event_study.get("groups") or []:
            group_horizons = group.get("horizons") or {}
            five_minute = group_horizons.get("5m") or {}
            close = group_horizons.get("close") or {}
            trading = group.get("tradability") or {}
            conclusion = (
                "暂无判断（样本不足）"
                if group.get("informationValueStatus") == "not_measured"
                else "已测量"
            )
            lines.append(
                f"| {group.get('rule')} | {group.get('priority')} | "
                f"{group.get('sampleSize', 0)} | "
                f"{value_text(five_minute.get('medianStockReturnPct'))} | "
                f"{value_text(close.get('medianStockReturnPct'))} | "
                f"{value_text(group.get('medianMaxFavorableMovePct'))} | "
                f"{value_text(group.get('medianMaxAdverseMovePct'))} | "
                f"{trading.get('tradable', 0)}/{trading.get('restricted', 0)} | "
                f"{conclusion} |"
            )

    lines.extend([
        "",
        "## 样本明细",
        "",
        "| 样本 | 结果 | 预期提醒 | 实际提醒 | 漏报 | 误报 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ])
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
