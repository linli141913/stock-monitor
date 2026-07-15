import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlsplit


PROMPT_VERSION = "evidence-v2"

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
