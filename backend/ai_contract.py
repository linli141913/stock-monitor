import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlsplit


PROMPT_VERSION = "evidence-v3"

_CHAIN_KEYS = (
    "technicalAndSentiment",
    "fundFactor",
    "fundamentalAndNews",
    "sectorAndMacro",
)
_TEXT_FIELDS = (
    "scenarioAnalysis",
    "plainEnglishSummary",
    "aiJudgment",
    "riskNotice",
)
_LIST_FIELDS = ("confirmedFacts", "inferences", "unknowns")
_FORBIDDEN_DIRECTIVES = (
    "买入",
    "卖出",
    "加仓",
    "减仓",
    "建仓",
    "清仓",
    "目标价",
    "止盈",
    "止损",
)
_SOURCE_ID_PATTERN = re.compile(r"\[(S\d+)\]")
_STATUS_TERMS = {
    "warning": "警惕",
    "negative": "负向风险",
    "positive": "正向信号",
    "neutral": "中性",
    "uncertain": "暂无法确认",
    "available": "数据可用",
    "unavailable": "暂无可靠数据",
    "insufficient": "样本不足",
    "null": "本轮没有对应事件",
    "triggered": "已触发",
    "no_signal": "未触发",
    "not_applicable": "不适用",
}
_STATUS_TERM_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_STATUS_TERMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_PRICE_SIGNAL_CODES = {
    "limit_move",
    "extreme_price_move",
    "high_amplitude",
    "high_volume_ratio",
    "turnover_warning",
}
_FUND_SIGNAL_CODES = {
    "consecutive_fund_inflow",
    "consecutive_fund_outflow",
    "price_fund_divergence",
}


def is_safe_http_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def _clean_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    text = _STATUS_TERM_PATTERN.sub(
        lambda match: _STATUS_TERMS[match.group(0).lower()],
        text,
    )
    text = re.sub(r"^【[^】]+】\s*", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > limit:
        raise ValueError("AI 输出字段超过长度限制")
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered:
        raise ValueError("AI 输出不得自行生成链接")
    if "<" in text or ">" in text:
        raise ValueError("AI 输出不得包含 HTML")
    if any(directive in text for directive in _FORBIDDEN_DIRECTIVES):
        raise ValueError("AI 输出包含交易指令")
    return text


def validate_ai_payload(payload: dict, allowed_source_ids: Set[str]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("AI 输出不是 JSON 对象")

    raw_chain = payload.get("evidenceChain")
    if not isinstance(raw_chain, dict):
        raw_chain = {}
    chain = {
        key: _clean_text(
            raw_chain.get(key) or "模型未返回该部分，暂无判断。",
            limit=1600,
        )
        for key in _CHAIN_KEYS
    }

    scenario = payload.get("scenarioAnalysis")
    if not scenario:
        scenario = payload.get("futureTrendPrediction")
    normalized = {
        "evidenceChain": chain,
        "scenarioAnalysis": _clean_text(
            scenario or "证据不足，暂无法形成条件情景分析。",
            limit=2000,
        ),
        "plainEnglishSummary": _clean_text(
            payload.get("plainEnglishSummary") or "当前证据不足，暂无法形成完整解释。",
            limit=360,
        ),
        "aiJudgment": _clean_text(
            payload.get("aiJudgment") or "已确认事实有限，暂无法形成进一步判断。",
            limit=800,
        ),
        "riskNotice": _clean_text(
            payload.get("riskNotice") or "数据或证据不足时不得形成确定性结论。",
            limit=500,
        ),
    }

    for field in _LIST_FIELDS:
        raw_items = payload.get(field)
        if not isinstance(raw_items, list):
            raw_items = []
        normalized[field] = [
            _clean_text(item, limit=360)
            for item in raw_items[:8]
            if str(item or "").strip()
        ]

    raw_source_ids = payload.get("sourceIds")
    if not isinstance(raw_source_ids, list):
        raw_source_ids = []
    source_ids = []
    for value in raw_source_ids:
        source_id = str(value or "").strip()
        if not source_id or source_id in source_ids:
            continue
        if source_id not in allowed_source_ids:
            raise ValueError("AI 输出引用了证据目录以外的来源")
        source_ids.append(source_id)

    all_text = "\n".join(
        [*chain.values(), *(normalized[field] for field in _TEXT_FIELDS)]
        + [item for field in _LIST_FIELDS for item in normalized[field]]
    )
    cited_ids = set(_SOURCE_ID_PATTERN.findall(all_text))
    if not cited_ids.issubset(allowed_source_ids):
        raise ValueError("AI 正文引用了证据目录以外的来源")
    for source_id in sorted(cited_ids):
        if source_id not in source_ids:
            source_ids.append(source_id)
    normalized["sourceIds"] = source_ids
    return normalized


def _source_catalog(items: Iterable[dict]) -> list:
    sources = []
    seen_urls = set()
    for item in items:
        url = str(item.get("url") or item.get("sourceUrl") or "").strip()
        if not is_safe_http_url(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({
            "sourceId": f"S{len(sources) + 1}",
            "title": str(item.get("title") or "来源标题暂缺").strip(),
            "source": str(item.get("source") or "来源暂缺").strip(),
            "url": url,
            "time": item.get("time") or item.get("publishedAt"),
            "evidenceLevel": item.get("evidenceLevel"),
        })
    return sources


def _available_or_unknown(value: Optional[dict], reason: str) -> dict:
    if isinstance(value, dict) and value:
        return deepcopy(value)
    return {"status": "unavailable", "label": "暂无判断", "reason": reason}


def build_evidence_snapshot(
    *,
    symbol: str,
    stock_name: str,
    asset_type: str,
    industry_name: str,
    quote_data: dict,
    market_risk: Optional[dict],
    linkage_risk: Optional[dict],
    finance_summary: Optional[dict],
    dynamics: dict,
    trigger_event: Optional[dict],
) -> dict:
    quote_date = str(quote_data.get("source_date") or "").strip()
    market = _available_or_unknown(
        market_risk,
        "尚无与当前股票匹配的量价风险快照",
    )
    market_date = str(market.get("sourceTime") or "")[:10]
    if quote_date and market_date and quote_date != market_date:
        market = {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "最新量价风险快照与当前行情来源日期不一致",
        }

    linkage = _available_or_unknown(
        linkage_risk,
        "板块或海外标的尚未完成精确业务映射",
    )
    if isinstance(finance_summary, dict) and finance_summary:
        finance = deepcopy(finance_summary)
        finance.setdefault("status", "available")
    elif str(finance_summary or "").strip():
        summary = str(finance_summary).strip()
        if "不适用" in summary:
            status = "not_applicable"
        elif summary.startswith("暂无") or "获取失败" in summary:
            status = "unavailable"
        else:
            status = "available"
        finance = {"status": status, "summary": summary}
    else:
        finance = {
            "status": "unavailable",
            "reason": "财务摘要暂不可用",
        }

    raw_sources = []
    if trigger_event:
        raw_sources.append(trigger_event)
    for group in ("policies", "upstreamDownstream"):
        raw_sources.extend(dynamics.get(group) or [])
    sources = _source_catalog(raw_sources)

    event = None
    if trigger_event:
        event = {
            "title": trigger_event.get("title"),
            "direction": trigger_event.get("direction"),
            "priority": trigger_event.get("priority"),
            "evidenceLevel": trigger_event.get("evidenceLevel"),
            "summary": trigger_event.get("summary"),
            "source": trigger_event.get("source"),
            "publishedAt": trigger_event.get("publishedAt"),
        }
        source_match = next(
            (
                item["sourceId"]
                for item in sources
                if item["url"] == str(trigger_event.get("sourceUrl") or "").strip()
            ),
            None,
        )
        if source_match:
            event["sourceId"] = source_match

    return {
        "security": {
            "symbol": symbol,
            "name": stock_name,
            "assetType": asset_type,
            "industryOrTheme": industry_name,
        },
        "quote": {
            "source": "腾讯财经",
            "sourceDate": quote_date or None,
            "sourceTime": quote_data.get("source_time"),
            "changePercent": quote_data.get("change_pct"),
            "volumeRatio": quote_data.get("volume_ratio"),
        },
        "marketRisk": market,
        "linkageRisk": linkage,
        "finance": finance,
        "triggerEvent": event,
        "sources": sources,
    }


def _signal_codes(market_risk: dict) -> list:
    codes = []
    for signal in market_risk.get("signals") or []:
        code = signal.get("code") if isinstance(signal, dict) else signal
        if code:
            codes.append(str(code))
    return sorted(set(codes))


def build_evidence_fingerprint(snapshot: dict) -> str:
    quote = snapshot.get("quote") or {}
    market = snapshot.get("marketRisk") or {}
    event = snapshot.get("triggerEvent") or {}
    material = {
        "quote": {
            "sourceDate": quote.get("sourceDate"),
            "changePercent": quote.get("changePercent"),
            "volumeRatio": quote.get("volumeRatio"),
        },
        "marketRisk": {
            "status": market.get("status") or market.get("riskStatus"),
            "priority": market.get("priority"),
            "direction": market.get("direction"),
            "signals": _signal_codes(market),
            "fundFlowStatus": (market.get("fundFlowRisk") or {}).get("status"),
            "movingAverageStatus": (market.get("movingAverageRisk") or {}).get("status"),
            "turnoverStatus": (market.get("turnoverRisk") or {}).get("status"),
        },
        "linkageRisk": snapshot.get("linkageRisk") or {},
        "finance": snapshot.get("finance") or {},
        "event": {
            "title": event.get("title"),
            "direction": event.get("direction"),
            "priority": event.get("priority"),
            "evidenceLevel": event.get("evidenceLevel"),
            "summary": event.get("summary"),
            "sourceId": event.get("sourceId"),
        },
        "sources": [
            {
                "sourceId": item.get("sourceId"),
                "title": item.get("title"),
                "url": item.get("url"),
                "evidenceLevel": item.get("evidenceLevel"),
            }
            for item in snapshot.get("sources") or []
        ],
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evidence_completeness(snapshot: dict) -> dict:
    quote = snapshot.get("quote") or {}
    market = snapshot.get("marketRisk") or {}
    linkage = snapshot.get("linkageRisk") or {}
    checks = {
        "行情时间与涨跌幅": bool(
            quote.get("sourceDate") and quote.get("changePercent") is not None
        ),
        "量价风险": (market.get("status") or market.get("riskStatus"))
        not in {None, "unavailable", "insufficient"},
        "历史资金": (market.get("fundFlowRisk") or {}).get("status")
        not in {None, "unavailable", "insufficient"},
        "板块与海外精确映射": (linkage.get("status") or linkage.get("riskStatus"))
        not in {None, "unavailable", "insufficient"},
        "可追溯事件来源": bool(snapshot.get("sources")),
        "财务摘要": (snapshot.get("finance") or {}).get("status") == "available",
    }
    available = [name for name, ready in checks.items() if ready]
    missing = [name for name, ready in checks.items() if not ready]
    total = len(checks)
    return {
        "available": available,
        "missing": missing,
        "availableCount": len(available),
        "totalCount": total,
        "ratio": round(len(available) / total, 2),
        "label": f"{len(available)}/{total} 项证据可用",
    }


def _signal_items(market_risk: dict) -> list:
    return [
        item for item in market_risk.get("signals") or []
        if isinstance(item, dict)
    ]


def _has_usable_status(value: Optional[dict]) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    status = value.get("status") or value.get("riskStatus")
    return status not in {None, "unavailable", "insufficient"}


def _periods_in_chinese(periods: Iterable[Any]) -> list:
    labels = {
        "MA5": "5日均线",
        "MA10": "10日均线",
        "MA20": "20日均线",
    }
    return [labels.get(str(period), str(period)) for period in periods]


def _sector_dimension(
    dimensions: dict,
    key: str,
    label: str,
) -> dict:
    raw = dimensions.get(key) if isinstance(dimensions, dict) else None
    if not isinstance(raw, dict) or raw.get("status") == "unavailable":
        return {
            "key": key,
            "label": label,
            "state": "暂无判断",
            "summary": (
                raw.get("reason")
                if isinstance(raw, dict) and raw.get("reason")
                else f"{label}数据缺失或口径未验证"
            ),
            "details": {},
        }
    return {
        "key": key,
        "label": label,
        "state": "已触发" if raw.get("status") == "triggered" else "未触发",
        "summary": raw.get("reason") or "当前已完成规则核对",
        "details": deepcopy(raw.get("details") or {}),
    }


def build_market_view(snapshot: dict) -> dict:
    quote = snapshot.get("quote") or {}
    market = snapshot.get("marketRisk") or {}
    linkage = snapshot.get("linkageRisk") or {}
    signals = _signal_items(market)
    signal_codes = {
        str(item.get("code")) for item in signals if item.get("code")
    }
    signal_directions = {
        str(item.get("direction"))
        for item in signals
        if item.get("direction") in {"positive", "negative"}
    }
    market_direction = market.get("direction")
    linkage_direction = linkage.get("direction")
    directions = set(signal_directions)
    if market_direction in {"positive", "negative"}:
        directions.add(market_direction)
    if linkage_direction in {"positive", "negative"}:
        directions.add(linkage_direction)

    market_available = _has_usable_status(market)
    linkage_available = _has_usable_status(linkage)
    has_negative = "negative" in directions
    has_positive = "positive" in directions
    has_fund_confirmation = bool(signal_codes & _FUND_SIGNAL_CODES)
    has_ma_confirmation = "ma_breakdown" in signal_codes
    has_sector_confirmation = linkage_available and linkage_direction == "negative"

    if not market_available and not linkage_available and quote.get("changePercent") is None:
        overall_state = "暂无判断"
        structure_label = "关键行情与联动数据不足，暂时不能形成市场结构判断"
    elif has_negative and has_positive:
        overall_state = "中性"
        structure_label = "多空信号并存，价格、资金或板块方向尚未统一"
    elif has_negative:
        if has_fund_confirmation and has_ma_confirmation and has_sector_confirmation:
            overall_state = "风险升高"
            structure_label = "价格、资金、均线与板块形成负向共振"
        elif has_sector_confirmation and not has_fund_confirmation and not has_ma_confirmation:
            overall_state = "偏弱"
            structure_label = "单日走弱，板块同步承压，资金与均线尚未二次确认"
        else:
            overall_state = "偏弱"
            structure_label = "价格偏弱，资金、均线或板块尚未形成完整确认"
    elif has_positive:
        overall_state = "偏强"
        structure_label = "价格与现有已验证信号偏强，仍需观察资金和板块确认"
    else:
        overall_state = "中性"
        structure_label = "现有信号未形成一致方向，继续等待新的确认条件"

    price_signals = [
        item.get("label") or str(item.get("code"))
        for item in signals
        if item.get("code") in _PRICE_SIGNAL_CODES
    ]
    if market_available:
        if market.get("priority") == "P1":
            price_state = "紧急风险"
        elif market.get("priority") == "P2" or market.get("riskStatus") == "warning":
            price_state = "重要风险"
        elif market.get("priority") == "P3" or market.get("riskStatus") == "watch":
            price_state = "观察信号"
        else:
            price_state = "未触发"
        price_summary = market.get("reason") or "当前未触发量价风险规则"
    elif quote.get("changePercent") is not None:
        price_state = "仅有行情"
        price_summary = "已有涨跌幅，但完整量价规则快照暂不可用"
    else:
        price_state = "暂无判断"
        price_summary = market.get("reason") or "量价数据缺失或口径未验证"
    dimensions = [{
        "key": "priceVolume",
        "label": "量价风险",
        "state": price_state,
        "summary": price_summary,
        "details": {
            "changePercent": quote.get("changePercent"),
            "volumeRatio": quote.get("volumeRatio"),
            "triggeredRules": price_signals,
        },
    }]

    fund_risk = market.get("fundFlowRisk") or {}
    fund_signal_codes = signal_codes & _FUND_SIGNAL_CODES
    if not _has_usable_status(fund_risk):
        fund_state = "暂无判断"
    elif "consecutive_fund_outflow" in fund_signal_codes:
        fund_state = "连续流出"
    elif "consecutive_fund_inflow" in fund_signal_codes:
        fund_state = "连续流入"
    elif "price_fund_divergence" in fund_signal_codes:
        fund_state = "价格资金背离"
    else:
        fund_state = "未形成连续信号"
    dimensions.append({
        "key": "continuousFund",
        "label": "连续资金数据",
        "state": fund_state,
        "summary": fund_risk.get("reason") or "连续资金数据缺失或口径未验证",
        "details": {
            key: deepcopy(value)
            for key, value in fund_risk.items()
            if key not in {"status", "label"}
        },
    })

    moving_average = market.get("movingAverageRisk") or {}
    if not _has_usable_status(moving_average):
        ma_state = "暂无判断"
    elif has_ma_confirmation or moving_average.get("status") == "triggered":
        ma_state = "已确认破位"
    else:
        ma_state = "未发生破位"
    dimensions.append({
        "key": "movingAverage",
        "label": "均线破位",
        "state": ma_state,
        "summary": moving_average.get("reason") or "均线数据缺失或口径未验证",
        "details": {
            "periods": _periods_in_chinese(moving_average.get("periods") or []),
        },
    })

    sector_dimensions = ((linkage.get("sectorRisk") or {}).get("dimensions") or {})
    dimensions.extend((
        _sector_dimension(sector_dimensions, "breadth", "板块上涨股票占比"),
        _sector_dimension(sector_dimensions, "leader", "板块龙头与代表股"),
        _sector_dimension(sector_dimensions, "fundFlow", "板块资金排名"),
    ))
    overseas = linkage.get("overseasRisk") or {}
    dimensions.append({
        "key": "overseas",
        "label": "精确海外映射",
        "state": (
            "已触发"
            if overseas.get("status") == "triggered"
            else "未触发"
            if overseas.get("status") == "no_signal"
            else "暂无判断"
        ),
        "summary": overseas.get("reason") or "没有经过业务精确映射的海外标的",
        "details": deepcopy(overseas.get("details") or {}),
    })

    confirmed_count = sum(item["state"] != "暂无判断" for item in dimensions)
    unavailable_count = len(dimensions) - confirmed_count
    improving_conditions = []
    continuing_conditions = []
    worsening_conditions = []
    if overall_state in {"偏弱", "风险升高"}:
        improving_conditions.append("大幅涨跌和高振幅规则退出，价格波动开始收敛")
        continuing_conditions.append("量价风险继续触发，但没有新增连续资金流出或均线破位")
        worsening_conditions.append("新增连续资金流出、价格资金负向背离或均线破位")
    else:
        improving_conditions.append("正向量价、资金和板块信号继续得到验证")
        continuing_conditions.append("当前信号结构和规则状态保持不变")
        worsening_conditions.append("新增大幅下跌、高振幅、连续资金流出或均线破位")

    triggered_sector = {
        key for key, item in sector_dimensions.items()
        if isinstance(item, dict) and item.get("status") == "triggered"
    }
    if "breadth" in triggered_sector:
        improving_conditions.append("板块上涨股票占比恢复到20%以上")
    if "leader" in triggered_sector:
        improving_conditions.append("板块龙头跌幅收窄至8%以内并退出跌停状态")
    if "fundFlow" in triggered_sector:
        improving_conditions.append("板块资金流出排名退出前5")
    if triggered_sector:
        continuing_conditions.append("板块上涨股票占比、龙头或资金排名仍处于触发区间")
        worsening_conditions.append("个股、板块龙头和板块资金同时继续走弱")

    return {
        "overallState": overall_state,
        "structureLabel": structure_label,
        "dimensions": dimensions,
        "improvingConditions": improving_conditions,
        "continuingConditions": continuing_conditions,
        "worseningConditions": worsening_conditions,
        "confirmedCount": confirmed_count,
        "unavailableCount": unavailable_count,
    }
