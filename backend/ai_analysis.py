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
from pydantic import BaseModel
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

import database
import news_api
import alert_repository
import asset_context
import market_calendar
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
    if not trigger.startswith("event:"):
        return ""
    alert_id = trigger.removeprefix("event:").strip()
    if not alert_id:
        return ""
    alert = alert_repository.get_alert(alert_id)
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
    plainEnglishSummary: Optional[str] = "暂无总结"
    aiJudgment: str
    credibility: str
    riskNotice: str
    sourceTime: Optional[str] = None
    sourceDate: Optional[str] = None
    analysisAt: Optional[str] = None
    resultReused: bool = False
    analysisStatus: Optional[str] = None

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
    # 优先从 SQLite 数据库缓存中读取，保证 1 小时内完全定死且支持多 worker 共享
    if not force_refresh:
        cached = database.get_cached_dynamics(symbol)
        if cached:
            return _normalize_cached_dynamics(cached)

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

    # 序列化新闻文本
    news_text = ""
    for idx, (_, item) in enumerate(eligible_items):
        news_text += f"[{idx+1}] 时间: {item['time']} | 来源: {item['source']} | 标题: {item['title']} | 链接: {item['url']}\n"

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return _fallback_dynamics(eligible_items)

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        prompt = f"""
        你是一个上市证券产业资讯分析助理。请从以下抓取到的真实行业及宏观新闻列表中，进行严格的“相关性分类筛选”，不要重写标题，不要包含任何自定义描述或AI润色，保持新闻的真实性：
        1. 筛选出与该行业 ({industry_name}) 以及股票代码 {symbol} 最相关的“国家产业政策/国内政策/海外法规动态/交易所公告监管”新闻（最多10条，必须有真实的新闻标题、发布时间、来源和链接）。
        2. 筛选出与该股票或者行业产业链相关的“上游原材料/设备供应商重大动态”、“公司财务/研报业绩预测”和“下游核心客户/终端消费市场需求”新闻（最多10条，必须有真实的新闻标题、发布时间、来源和链接）。
        
        【极重要规则】：policies 和 upstreamDownstream 两个数组均最多各 10 条；没有合格来源时必须返回空列表，不得补写或虚构。
        
        【行业新闻输入】
        {news_text}

        请严格返回如下的 JSON 格式，不要包含任何 markdown 标记(如```json)，只需纯净的 JSON 字符串：
        {{
            "policies": [
                {{
                    "title": "100%保持原文的新闻标题",
                    "source": "新闻中对应的来源",
                    "url": "新闻中对应的链接，如果没有则留空",
                    "time": "新闻中对应的时间，例如：07-08 12:30"
                }}
            ],
            "upstreamDownstream": [
                {{
                    "title": "100%保持原文的新闻标题",
                    "source": "新闻中对应的来源",
                    "url": "新闻中对应的链接，如果没有则留空",
                    "time": "新闻中对应的时间，例如：07-08 12:30"
                }}
            ]
        }}
        """
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个严格进行新闻分类筛选、绝不胡说八道 and 重写标题的API助理。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        content_res = response.choices[0].message.content
        import json
        raw_result = json.loads(content_res)
        res = {
            "policies": _match_llm_selection(
                raw_result.get("policies", []),
                eligible_items,
                "industry-policy",
            ),
            "upstreamDownstream": _match_llm_selection(
                raw_result.get("upstreamDownstream", []),
                eligible_items,
                "industry-dynamics",
            ),
        }
        
        # 写入数据库持久缓存，一小时内定死，不再消耗大模型 token
        database.save_cached_dynamics(symbol, res)
        return res
    except Exception as e:
        print(f"Error fetching industry dynamics via LLM: {e}")
        return _fallback_dynamics(eligible_items)

fetcher = RealDataFetcher()

@router.get("/ai_attribution/{symbol}", response_model=AiAttributionResponse)
def get_ai_attribution(symbol: str, trigger: str = "manual"):
    # 1. 只有后台监测中的股票才允许复用或生成 AI 分析。
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
        ):
            store_success_result(symbol, cached_success)
            return _mark_result_reused(cached_success)

        cached_success = get_cached_success_result(symbol)
        if (
            cached_success is not None
            and cached_result_matches_source_session(cached_success, quote_data)
        ):
            return _mark_result_reused(cached_success)

    # 2. 未加入监测列表时不调用大模型，以防超额。
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
            "futureTrendPrediction": "⚠️ 本股票未加入监测列表，AI 情景与风险分析已休眠。请在上方将其加入监测列表以激活该功能。",
            "plainEnglishSummary": "本股票未加入监测列表，AI 总结监控已休眠。请在上方将其加入监测以激活本板块。",
            "aiJudgment": "⚠️ 请先加入监测列表！",
            "credibility": "无",
            "riskNotice": "添加监测后将展示完整风险提示与推演。",
            **_analysis_metadata(quote_data),
        }

    context = asset_context.resolve_asset_context(
        symbol,
        quote_data.get("name", symbol),
    )
    industry_name = context["industry_name"]
    stock_news = fetcher.get_stock_news(context["symbol"])
    macro_env = fetcher.get_macro_environment(context)
    industry_news = fetcher.get_industry_news_dehydrated(
        context["symbol"],
        industry_name,
        context["search_terms"],
    )
    finance_summary = fetcher.get_finance_summary(
        context["symbol"],
        quote_data.get("name", symbol),
    )
    
    # 3. Fetch today's history for memory injection
    history_records = database.get_today_analysis_history(symbol)
    history_context = ""
    if history_records:
        history_context = "【今日历史追踪节点】\n"
        for idx, rec in enumerate(history_records):
            history_context += f"- {rec['time']} ({rec['trigger_type']}): {rec['plain_english_summary']}\n"
    trigger_event_context = build_trigger_event_context(trigger)
            
    # 4. 只有可追溯的数据才能作为产业链证据；当前没有可靠映射时明确告知模型不要推演。
    chain_context = """
    【产业链上下游映射关系】
    当前没有提供可追溯的产业链映射。不得凭知识库编造上游供应商或下游客户；证据不足时请明确写“暂无法确认”。
    """
        
    # 5. Fetch cached dynamics news and format for prompt
    dynamics = fetch_real_industry_dynamics(
        context["symbol"],
        industry_name,
        search_terms=context["search_terms"],
    )
    
    policies_list = dynamics.get("policies", [])
    upstream_downstream_list = dynamics.get("upstreamDownstream", [])
    
    policies_context = "【右侧面板同步的当日行业相关政策快讯】\n"
    for p in policies_list:
        policies_context += f"- 【{p.get('time', '时间暂缺')}】{p.get('title')} (来源: {p.get('source')})\n"
        
    dynamics_context = "【右侧面板同步的当日上下游产业链动态快讯】\n"
    for d in upstream_downstream_list:
        dynamics_context += f"- 【{d.get('time', '时间暂缺')}】{d.get('title')} (来源: {d.get('source')})\n"
    
    # 6. Call AI pipeline
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        failed_result = {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "score": None,
            "evidenceChain": {},
            "futureTrendPrediction": "暂无推演内容",
            "plainEnglishSummary": "暂无总结",
            "aiJudgment": "⚠️ 请在后台配置 LLM_API_KEY 后，方可启动 AI 深度推理功能。",
            "credibility": "无",
            "riskNotice": "配置 API 密钥后将显示完整风险提示。",
            "analysisStatus": "failed",
            **_analysis_metadata(quote_data),
        }
        _save_failed_single_attempt(symbol, trigger, failed_result)
        return failed_result
        
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        source_date = str(quote_data.get("source_date") or "").strip()
        source_time = str(quote_data.get("source_time") or "").strip()
        today = datetime.now(market_calendar.SHANGHAI_TZ).date().isoformat()
        if source_date == today:
            source_period_label = "今日"
            freshness_instruction = "行情源日期为今天，可以使用“今日”，但不得把抓取时间写成行情发布时间。"
        elif source_date:
            source_period_label = f"{source_date}对应交易日"
            freshness_instruction = (
                f"行情源日期是{source_date}，不是今天。严禁把该行情描述为“今日”或“今天”，"
                "必须明确这是最近一次可追溯交易快照。"
            )
        else:
            source_period_label = "数据源日期未知的最近快照"
            freshness_instruction = "行情源没有提供日期，严禁使用“今日”或“今天”，并明确时间暂无法确认。"

        prompt = f"""
        你是一位严格依据证据进行局势、影响和风险归因的上市证券产业分析师。语言保持专业、直接，不提供交易建议。
        请根据以下抓取到的真实数据，输出一段鲜活、有深度的涨跌归因分析。
        
        【客观数据输入】
        证券名称：{quote_data.get('name', symbol)} ({symbol})
        资产类型：{context['asset_type']}
        所属行业或ETF主题：{industry_name}
        行情源时间：{source_time or '暂无法确认'}
        {source_period_label}涨跌幅：{quote_data.get('change_pct') if quote_data.get('change_pct') is not None else '暂无数据'}
        量比：{quote_data.get('volume_ratio') if quote_data.get('volume_ratio') is not None else '暂无数据'}
        个股资金流向：暂无已验证的同时间口径数据
        所属板块表现：暂无已验证的同时间口径数据
        个股绝对相关新闻（定向狙击）：{stock_news}
        当日可追溯行业事件精选：{industry_news}
        宏观与海外环境：{macro_env}
        
        【公司财务或ETF适用信息】
        {finance_summary}
        
        【右侧面板同步的最新行业相关政策（共用同一套缓存，保持逻辑吻合）】
        {policies_context}
        
        【右侧面板同步的最新上下游产业链动态（共用同一套缓存，保持逻辑吻合）】
        {dynamics_context}
        
        {chain_context}
        
        {history_context}

        {trigger_event_context}
        
        【事实归因与风险分析指令】
        时间真实性要求：{freshness_instruction}
        必须严格按以下两个层次进行深度思考并输出内容：
        只分析局势、影响、风险和证据，不提供任何交易指令，不对价格方向作确定性承诺。
        
        第一层：【{source_period_label}复盘总结】（陈述事实与逻辑映射）
        结合盘口量价表现及上述可追溯资讯，特别是当日行业政策和上下游动态，复盘该行情源时点的形态。不要流水账式复述新闻；缺少资金流或板块同口径数据时必须明确写“暂无法确认”，不得补写。
        必须在分析中带出信息出处，使用严谨的 Markdown 超链接，如：`<br/>[来源: 新浪财经](url)`。
        
        第二层：【情景影响与风险分析】（以可验证事实为主，技术面为辅）
        对公司股票结合财报与产业链，对ETF结合跟踪主题、基金公告与成交变化，列出可能的正面、中性和负面情景。每个情景都要说明触发条件、可能影响、主要风险与待验证证据；证据不足时明确写“暂无法确认”。
        
        【极简要求】
        第三层：【极简通俗总结】
        在深度分析的末尾，必须给出一段极简通俗总结。用容易理解的语言说清楚该行情源时点发生了什么、对公司和行业可能产生什么影响、接下来需要关注哪些证据和风险。
        【极其严格的死命令】：字数必须在 50~100 字之间，必须讲透细节，绝对不可使用一句短话敷衍！如果不满 50 字将视为严重错误并作废！这段话存存放于 JSON 的 plainEnglishSummary 字段中。
        
        【输出格式要求】
        请务必返回合法的 JSON 格式，不要包含任何 markdown 标记(如```json)，只需纯净的 JSON 字符串。
        格式如下：
        {{
            "score": 75, // 0-100的机构健康度综合评分，70以上为健康，40以下为高危
            "evidenceChain": {{
                "technicalAndSentiment": "【量价与情绪面】用精炼语言剖析当天的量价异动...",
                "fundFactor": "【资金面博弈】洞察主力资金真实意图...",
                "fundamentalAndNews": "【基本面与资讯】把今日复盘总结写在这里，结合最新的核心财报表现、政策及上下游新闻，深度解读对股价的催化作用(务必附带链接)...",
                "sectorAndMacro": "【板块与宏观共振】结合产业链上下游传导关系，一针见微指出板块协同与全球宏观映射..."
            }},
            "futureTrendPrediction": "【情景影响与风险分析】结合公司核心财报与产业链关系，列出情景、触发条件、可能影响、主要风险与待验证证据。",
            "plainEnglishSummary": "用50~100字通俗易懂地讲清楚该行情源时点发生了什么、可能影响和需要关注的风险与证据，开头不要带任何括号或前缀标题。",
            "aiJudgment": "【一针见血】的最终综合诊断结论。",
            "credibility": "高 / 中 / 低",
            "riskNotice": "一句话致命风险提示"
        }}
        """
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个严格返回 JSON 的产业局势、影响、风险与证据分析 API。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        content_res = response.choices[0].message.content
        import json
        ai_res = json.loads(content_res)

        # 4. Save to database and return result
        plain_english = ai_res.get("plainEnglishSummary", "暂无总结")
        final_dict = {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "score": ai_res.get("score"),
            "evidenceChain": ai_res.get("evidenceChain", {}),
            "futureTrendPrediction": ai_res.get("futureTrendPrediction") or "暂无推演内容",
            "plainEnglishSummary": plain_english,
            "aiJudgment": ai_res.get("aiJudgment", "推理失败"),
            "credibility": ai_res.get("credibility", "未知"),
            "riskNotice": ai_res.get("riskNotice", ""),
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

    except Exception as e:
        print(f"LLM Error: {e}")
        failed_result = {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "evidenceChain": {
                "stockPerformance": "-",
                "sectorFactor": "-",
                "fundFactor": "-",
                "newsFactor": "-",
                "overseasFactor": "-"
            },
            "score": None,
            "futureTrendPrediction": "暂无推演内容",
            "plainEnglishSummary": "AI 分析调用失败，本次未生成评分或结论。",
            "aiJudgment": f"大模型接口调用失败: {str(e)}",
            "credibility": "错误",
            "riskNotice": "请检查网络或 API Key 额度。",
            "analysisStatus": "failed",
            **_analysis_metadata(quote_data),
        }
        _save_failed_single_attempt(symbol, trigger, failed_result)
        return failed_result
