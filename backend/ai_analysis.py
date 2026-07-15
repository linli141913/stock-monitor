import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

import json
import logging
import copy
import threading
import time
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

import database
import news_api
import alert_repository
import asset_context
import market_calendar
from ai_contract import (
    PROMPT_VERSION,
    build_evidence_fingerprint,
    build_evidence_snapshot,
    evidence_completeness,
    validate_ai_payload,
)
from real_data_fetcher import RealDataFetcher

load_dotenv()

router = APIRouter(prefix="/api/stock", tags=["AI Attribution"])

AI_SUCCESS_TTL_SECONDS = 20 * 60
_AI_SUCCESS_CACHE = {}
_AI_SUCCESS_CACHE_LOCK = threading.Lock()


def _is_single_attempt_trigger(trigger: str) -> bool:
    return trigger.startswith("auto:") or trigger.startswith("event:")


def _save_failed_single_attempt(symbol: str, trigger: str, result: dict) -> None:
    if not _is_single_attempt_trigger(trigger):
        return
    summary = result.get("plainEnglishSummary", "AI 分析调用失败")
    if not database.complete_analysis_trigger(symbol, trigger, summary, result):
        database.save_analysis_history(symbol, trigger, summary, result)


def build_trigger_event_context(trigger: str) -> str:
    alert = get_trigger_event(trigger)
    if alert is None:
        return ""
    return "\n".join((
        "【本轮触发的 P1/P2 事件】",
        f"标题：{alert.get('title') or '暂无标题'}",
        f"方向：{alert.get('direction') or 'uncertain'}",
        f"优先级：{alert.get('priority') or '暂无'}",
        f"证据等级：{alert.get('evidenceLevel') or '暂无'}",
        f"规则摘要：{alert.get('summary') or '暂无'}",
        f"来源：{alert.get('source') or '暂无'}",
        f"原文：{alert.get('sourceUrl') or '暂无原文链接'}",
        "必须优先解释该事件的可能影响、风险和待验证证据，不得把它改写为确定性涨跌结论。",
    ))


def get_trigger_event(trigger: str) -> Optional[dict]:
    if not trigger.startswith("event:"):
        return None
    alert_id = trigger.removeprefix("event:").strip()
    if not alert_id:
        return None
    return alert_repository.get_alert(alert_id)


def store_success_result(symbol: str, result: dict, now: Optional[float] = None) -> None:
    cached_at = time.monotonic() if now is None else now
    with _AI_SUCCESS_CACHE_LOCK:
        _AI_SUCCESS_CACHE[symbol] = (cached_at, copy.deepcopy(result))


def get_cached_success_result(symbol: str, now: Optional[float] = None) -> Optional[dict]:
    current = time.monotonic() if now is None else now
    with _AI_SUCCESS_CACHE_LOCK:
        cached = _AI_SUCCESS_CACHE.get(symbol)
        if cached is None:
            return None
        cached_at, result = cached
        if current - cached_at > AI_SUCCESS_TTL_SECONDS:
            _AI_SUCCESS_CACHE.pop(symbol, None)
            return None
        return copy.deepcopy(result)


def cached_result_matches_source_session(result: dict, quote_data: dict) -> bool:
    cached_date = str(result.get("sourceDate") or "").strip()
    current_date = str(quote_data.get("source_date") or "").strip()
    return bool(cached_date and current_date and cached_date == current_date)


def _mark_result_reused(result: dict) -> dict:
    reused = copy.deepcopy(result)
    reused["resultReused"] = True
    reused["reuseReason"] = "manual_cache_20m"
    return reused


def _analysis_metadata(quote_data: dict, reused: bool = False) -> dict:
    return {
        "sourceTime": quote_data.get("source_time"),
        "sourceDate": quote_data.get("source_date"),
        "analysisAt": datetime.now(market_calendar.SHANGHAI_TZ).isoformat(timespec="seconds"),
        "resultReused": reused,
    }

class AiAttributionResponse(BaseModel):
    stockName: str
    stockCode: str
    changePercent: Optional[float]
    score: Optional[int]
    evidenceChain: Dict[str, str]
    futureTrendPrediction: str
    scenarioAnalysis: Optional[str] = None
    plainEnglishSummary: Optional[str] = "暂无总结"
    aiJudgment: str
    credibility: str
    riskNotice: str
    sourceTime: Optional[str] = None
    sourceDate: Optional[str] = None
    analysisAt: Optional[str] = None
    resultReused: bool = False
    analysisStatus: Optional[str] = None
    sources: list = Field(default_factory=list)
    sourceIds: list = Field(default_factory=list)
    confirmedFacts: list = Field(default_factory=list)
    inferences: list = Field(default_factory=list)
    unknowns: list = Field(default_factory=list)
    evidenceCompleteness: Optional[Dict[str, Any]] = None
    evidenceFingerprint: Optional[str] = None
    promptVersion: Optional[str] = None
    model: Optional[str] = None
    durationMs: Optional[int] = None
    usage: Optional[Dict[str, Any]] = None
    reuseReason: Optional[str] = None

def _format_dynamics_item(item: dict, source_counts: dict) -> Optional[dict]:
    if not news_api.is_source_published_today(item):
        return None
    classification = news_api.classify_news_item(item, source_counts)
    evidence_level = classification.get("credibility_level")
    if evidence_level not in {"S", "A", "B", "C"}:
        return None

    time_metadata = news_api.get_source_time_metadata(item)
    return {
        "title": str(item.get("title") or "").strip(),
        "source": str(item.get("source") or "").strip(),
        "url": str(item.get("url") or item.get("original_link") or "").strip(),
        "time": time_metadata["publish_time"],
        "timePrecision": time_metadata["publish_time_precision"],
        "discoveredAt": time_metadata["discovered_at"],
        "categoryKey": classification["category_key"],
        "evidenceLevel": evidence_level,
        "verificationStatus": classification["verification_status"],
        "direction": classification["direction"],
        "priority": classification["priority"],
    }


def _eligible_dynamics_items(news_items: list) -> list:
    source_counts = news_api.build_independent_source_counts(news_items)
    eligible = []
    for raw_item in news_items:
        formatted = _format_dynamics_item(raw_item, source_counts)
        if formatted is not None:
            eligible.append((raw_item, formatted))
    return eligible


def _normalize_cached_dynamics(data: dict) -> dict:
    policies = list(data.get("policies") or [])
    upstream = list(data.get("upstreamDownstream") or [])
    combined = policies + upstream
    source_counts = news_api.build_independent_source_counts(combined)

    def normalize(items: list, expected_category: str) -> list:
        normalized = []
        for item in items:
            formatted = _format_dynamics_item(item, source_counts)
            if formatted is not None and formatted["categoryKey"] == expected_category:
                normalized.append(formatted)
        return normalized[:10]

    return {
        "policies": normalize(policies, "industry-policy"),
        "upstreamDownstream": normalize(upstream, "industry-dynamics"),
    }


def _fallback_dynamics(eligible_items: list) -> dict:
    policies = []
    upstream = []
    for raw_item, formatted in eligible_items:
        if formatted["categoryKey"] == "industry-policy" and len(policies) < 10:
            policies.append(formatted)
        elif formatted["categoryKey"] == "industry-dynamics" and len(upstream) < 10:
            upstream.append(formatted)
    return {"policies": policies, "upstreamDownstream": upstream}


def _match_llm_selection(entries: list, eligible_items: list, expected_category: str) -> list:
    allowed = [
        formatted
        for _, formatted in eligible_items
        if formatted["categoryKey"] == expected_category
    ]
    by_url = {formatted["url"]: formatted for formatted in allowed if formatted["url"]}
    by_title = {formatted["title"]: formatted for formatted in allowed if formatted["title"]}
    selected = []
    used_urls = set()
    for entry in entries or []:
        url = str(entry.get("url") or "").strip()
        title = str(entry.get("title") or "").strip()
        matched = by_url.get(url) or by_title.get(title)
        if matched is None or matched["url"] in used_urls:
            continue
        selected.append(matched)
        used_urls.add(matched["url"])
    return selected[:10]


def fetch_real_industry_dynamics(
    symbol: str,
    industry_name: str,
    force_refresh: bool = False,
    search_terms=None,
) -> dict:
    stock_items = database.get_latest_crawled_news(symbol, limit=100)
    relevance_terms = [
        str(term).strip().lower()
        for term in [industry_name, *(search_terms or [])]
        if len(str(term).strip()) >= 2 and "待确认" not in str(term)
    ]
    global_items = []
    for item in database.get_latest_crawled_news("", limit=200):
        if str(item.get("symbol") or "").strip():
            continue
        text = f"{item.get('title') or ''}\n{item.get('content') or ''}".lower()
        if relevance_terms and any(term in text for term in relevance_terms):
            global_items.append(item)
    news_items = []
    seen_urls = set()
    for item in stock_items + global_items:
        url = str(item.get("url") or "").strip()
        dedupe_key = url or str(item.get("id") or item.get("title") or "")
        if not dedupe_key or dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        news_items.append(item)

    eligible_items = _eligible_dynamics_items(news_items)
    if not eligible_items:
        return {"policies": [], "upstreamDownstream": []}
    return _fallback_dynamics(eligible_items)

fetcher = RealDataFetcher()


def reuse_unchanged_single_attempt(
    symbol: str,
    trigger: str,
    quote_data: dict,
    fingerprint: str,
) -> Optional[dict]:
    if not _is_single_attempt_trigger(trigger):
        return None
    source_date = str(quote_data.get("source_date") or "").strip()
    if not source_date:
        return None
    previous = database.get_latest_successful_analysis(symbol)
    if previous is None:
        return None
    if str(previous.get("sourceDate") or "").strip() != source_date:
        return None
    if previous.get("evidenceFingerprint") != fingerprint:
        return None
    reused = copy.deepcopy(previous)
    reused["resultReused"] = True
    reused["reuseReason"] = "evidence_unchanged"
    reused["reusedAt"] = datetime.now(market_calendar.SHANGHAI_TZ).isoformat(
        timespec="seconds"
    )
    database.complete_analysis_trigger(
        symbol,
        trigger,
        reused.get("plainEnglishSummary") or "已复用相同证据解释",
        reused,
    )
    store_success_result(symbol, reused)
    return reused


def _failed_result(
    symbol: str,
    quote_data: dict,
    *,
    message: str,
    risk_notice: str,
    evidence_snapshot: Optional[dict] = None,
    fingerprint: Optional[str] = None,
    completeness: Optional[dict] = None,
) -> dict:
    return {
        "stockName": quote_data.get("name", symbol),
        "stockCode": symbol,
        "changePercent": quote_data.get("change_pct"),
        "score": None,
        "evidenceChain": {},
        "scenarioAnalysis": "本次未生成条件情景分析。",
        "futureTrendPrediction": "本次未生成条件情景分析。",
        "plainEnglishSummary": "AI 解释暂不可用，规则提醒与原始证据仍然有效。",
        "aiJudgment": message,
        "credibility": (completeness or {}).get("label", "无"),
        "riskNotice": risk_notice,
        "sources": (evidence_snapshot or {}).get("sources", []),
        "sourceIds": [],
        "confirmedFacts": [],
        "inferences": [],
        "unknowns": [],
        "evidenceCompleteness": completeness,
        "evidenceFingerprint": fingerprint,
        "promptVersion": PROMPT_VERSION,
        "analysisStatus": "failed",
        **_analysis_metadata(quote_data),
    }


def _usage_dict(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            result[key] = value
    return result


@router.get("/ai_attribution/{symbol}", response_model=AiAttributionResponse)
def get_ai_attribution(symbol: str, trigger: str = "manual"):
    is_monitored = database.is_in_watchlist(symbol)
    if is_monitored:
        if _is_single_attempt_trigger(trigger):
            existing = database.get_analysis_history_by_trigger(symbol, trigger)
            if existing is not None:
                return existing["full_json"]
            if not database.claim_analysis_trigger(symbol, trigger):
                existing = database.get_analysis_history_by_trigger(symbol, trigger)
                if existing is not None:
                    return existing["full_json"]
    quote_data = fetcher.get_stock_quote(symbol)

    if is_monitored and not _is_single_attempt_trigger(trigger):
        cached_success = database.get_recent_successful_analysis(
            symbol,
            AI_SUCCESS_TTL_SECONDS,
        )
        if (
            cached_success is not None
            and cached_result_matches_source_session(cached_success, quote_data)
            and cached_success.get("promptVersion") == PROMPT_VERSION
        ):
            store_success_result(symbol, cached_success)
            return _mark_result_reused(cached_success)

        cached_success = get_cached_success_result(symbol)
        if (
            cached_success is not None
            and cached_result_matches_source_session(cached_success, quote_data)
            and cached_success.get("promptVersion") == PROMPT_VERSION
        ):
            return _mark_result_reused(cached_success)

    if not is_monitored:
        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "score": None,
            "evidenceChain": {
                "technicalAndSentiment": "💡 本股票目前未加入监测列表。",
                "fundFactor": "大模型智能量化监控处于休眠状态。",
                "fundamentalAndNews": "请先在上方把该股票【加入监测】，即可激活大模型产业链分析与当日政策归因。",
                "sectorAndMacro": "本保护机制能帮您有效节省 API Key 额度消耗。"
            },
            "scenarioAnalysis": "本股票未加入监测列表，未运行条件情景分析。",
            "futureTrendPrediction": "本股票未加入监测列表，未运行条件情景分析。",
            "plainEnglishSummary": "本股票未加入监测列表，AI 总结监控已休眠。请在上方将其加入监测以激活本板块。",
            "aiJudgment": "⚠️ 请先加入监测列表！",
            "credibility": "无",
            "riskNotice": "添加监测后将展示基于证据的风险解释。",
            "analysisStatus": "not_monitored",
            "promptVersion": PROMPT_VERSION,
            **_analysis_metadata(quote_data),
        }

    context = asset_context.resolve_asset_context(
        symbol,
        quote_data.get("name", symbol),
    )
    industry_name = context["industry_name"]
    finance_summary = fetcher.get_finance_summary(
        context["symbol"],
        quote_data.get("name", symbol),
    )
    dynamics = fetch_real_industry_dynamics(
        context["symbol"],
        industry_name,
        search_terms=context["search_terms"],
    )
    latest_signal_state = alert_repository.get_latest_signal_state(context["symbol"])
    linkage_risk = (
        latest_signal_state.get("linkageRisk")
        if latest_signal_state is not None
        else None
    )
    linkage_snapshot = (
        latest_signal_state.get("linkageSnapshot")
        if latest_signal_state is not None
        else None
    )
    source_date = str(quote_data.get("source_date") or "").strip()
    linkage_date = str((linkage_snapshot or {}).get("source_time") or "")[:10]
    if source_date and linkage_date and source_date != linkage_date:
        linkage_risk = None

    evidence_snapshot = build_evidence_snapshot(
        symbol=context["symbol"],
        stock_name=quote_data.get("name", symbol),
        asset_type=context["asset_type"],
        industry_name=industry_name,
        quote_data=quote_data,
        market_risk=latest_signal_state,
        linkage_risk=linkage_risk,
        finance_summary=finance_summary,
        dynamics=dynamics,
        trigger_event=get_trigger_event(trigger),
    )
    evidence_fingerprint = build_evidence_fingerprint(evidence_snapshot)
    completeness = evidence_completeness(evidence_snapshot)

    reused = reuse_unchanged_single_attempt(
        symbol,
        trigger,
        quote_data,
        evidence_fingerprint,
    )
    if reused is not None:
        return reused

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        failed_result = _failed_result(
            symbol,
            quote_data,
            message="后台尚未配置 AI 服务，未运行模型解释。",
            risk_notice="规则提醒和原始证据仍然有效。",
            evidence_snapshot=evidence_snapshot,
            fingerprint=evidence_fingerprint,
            completeness=completeness,
        )
        _save_failed_single_attempt(symbol, trigger, failed_result)
        return failed_result

    try:
        started_at = time.monotonic()
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout=60,
            max_retries=0,
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        prompt = f"""
        你是“事件与风险解释”模块，只能解释后端提供的证据快照。
        规则提醒是否触发已经由确定性规则决定，你不得改变优先级、证据等级或补充事实。

        【证据快照】
        {json.dumps(evidence_snapshot, ensure_ascii=False, default=str)}

        【必须遵守】
        1. 把内容分成已确认事实、基于事实的推断、暂无法确认三类。
        2. 数据状态为 unavailable 或 insufficient 时必须写“暂无判断”或“暂无法确认”。
        3. 海外标的只有在 linkageRisk 中存在精确业务映射时才能讨论，不能因同属一个宽泛行业建立联系。
        4. 不得沿用任何历史 AI 结论，不得使用证据快照之外的知识补全公司、客户、供应商、数字或因果关系。
        5. 不提供交易指令、数值化价格承诺或确定性价格方向；情景分析必须写清触发条件、可能影响、风险和待验证证据。
        6. 正文不得输出 URL、HTML 或 Markdown 链接。引用来源只写证据目录编号，例如 [S1]；sourceIds 只能填写实际引用过的编号。
        7. plainEnglishSummary 用 50 至 140 字说明已知事实、证据缺口和需要继续观察的条件，不得用夸张措辞。

        只返回合法 JSON 对象，不要代码块，结构如下：
        {{
            "evidenceChain": {{
                "technicalAndSentiment": "量价事实、规则状态和缺口",
                "fundFactor": "已验证资金证据和缺口",
                "fundamentalAndNews": "基本面、事件事实与来源编号",
                "sectorAndMacro": "板块和海外精确映射证据或缺口"
            }},
            "scenarioAnalysis": "条件情景分析，不是预测",
            "plainEnglishSummary": "通俗总结",
            "aiJudgment": "明确标注为模型解释的综合说明",
            "riskNotice": "主要风险与证据失效条件",
            "confirmedFacts": ["已确认事实，可带 [S1]"],
            "inferences": ["推断及其依据"],
            "unknowns": ["暂无法确认的事项"],
            "sourceIds": ["S1"]
        }}
        """
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": "你是严格受证据目录约束的事件解释 API，只返回 JSON。",
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=1800,
        )
        content_res = response.choices[0].message.content
        ai_res = validate_ai_payload(
            json.loads(content_res),
            {item["sourceId"] for item in evidence_snapshot["sources"]},
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)
        plain_english = ai_res["plainEnglishSummary"]
        used_sources = [
            item
            for item in evidence_snapshot["sources"]
            if item["sourceId"] in ai_res["sourceIds"]
        ]
        final_dict = {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "score": None,
            "evidenceChain": ai_res["evidenceChain"],
            "scenarioAnalysis": ai_res["scenarioAnalysis"],
            "futureTrendPrediction": ai_res["scenarioAnalysis"],
            "plainEnglishSummary": plain_english,
            "aiJudgment": ai_res["aiJudgment"],
            "credibility": completeness["label"],
            "riskNotice": ai_res["riskNotice"],
            "confirmedFacts": ai_res["confirmedFacts"],
            "inferences": ai_res["inferences"],
            "unknowns": ai_res["unknowns"],
            "sourceIds": ai_res["sourceIds"],
            "sources": used_sources,
            "evidenceCompleteness": completeness,
            "evidenceFingerprint": evidence_fingerprint,
            "promptVersion": PROMPT_VERSION,
            "model": model_name,
            "durationMs": duration_ms,
            "usage": _usage_dict(response),
            "analysisStatus": "success",
            **_analysis_metadata(quote_data),
        }
        if _is_single_attempt_trigger(trigger):
            database.complete_analysis_trigger(
                symbol,
                trigger,
                plain_english,
                final_dict,
            )
        else:
            database.save_analysis_history(symbol, trigger, plain_english, final_dict)
        store_success_result(symbol, final_dict)
        return final_dict

    except Exception as exc:
        print(f"AI explanation failed: {type(exc).__name__}")
        failed_result = _failed_result(
            symbol,
            quote_data,
            message="AI 服务调用或输出校验失败，本次未生成模型解释。",
            risk_notice="规则提醒和原始证据不受影响，请稍后查看下一次复盘。",
            evidence_snapshot=evidence_snapshot,
            fingerprint=evidence_fingerprint,
            completeness=completeness,
        )
        _save_failed_single_attempt(symbol, trigger, failed_result)
        return failed_result
