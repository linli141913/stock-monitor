import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

import json
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

import database
from real_data_fetcher import RealDataFetcher

load_dotenv()

router = APIRouter(prefix="/api/stock", tags=["AI Attribution"])

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

def fetch_real_industry_dynamics(symbol: str, industry_name: str, force_refresh: bool = False) -> dict:
    # 优先从 SQLite 数据库缓存中读取，保证 1 小时内完全定死且支持多 worker 共享
    if not force_refresh:
        cached = database.get_cached_dynamics(symbol)
        if cached:
            return cached

    import time
    from datetime import datetime
    news_items = database.get_latest_crawled_news(symbol, limit=100)
    
    if not news_items:
        return {"policies": [], "upstreamDownstream": []}

    # 序列化新闻文本
    news_text = ""
    for idx, item in enumerate(news_items):
        t_val = item.get("ctime", time.time())
        try:
            time_str = datetime.fromtimestamp(t_val).strftime("%m-%d %H:%M")
        except:
            time_str = "今日"
        news_text += f"[{idx+1}] 时间: {time_str} | 来源: {item.get('source')} | 标题: {item.get('title')} | 链接: {item.get('url')}\n"

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        # 简单策略拆分
        policies = []
        updown = []
        for x in news_items:
            t_val = x.get("ctime", time.time())
            try:
                time_str = datetime.fromtimestamp(t_val).strftime("%m-%d %H:%M")
            except:
                time_str = "今日"
            pkg = {
                "title": x.get("title"),
                "source": x.get("source"),
                "url": x.get("url"),
                "time": time_str
            }
            if x.get("category") == "policy" and len(policies) < 10:
                policies.append(pkg)
            elif len(updown) < 10:
                updown.append(pkg)
        return {"policies": policies, "upstreamDownstream": updown}

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        prompt = f"""
        你是一个半导体与电子行业的顶尖分析助理。请从以下抓取到的真实行业及宏观新闻列表中，进行严格的“相关性分类筛选”，不要重写标题，不要包含任何自定义描述或AI润色，保持新闻的真实性：
        1. 筛选出与该行业 ({industry_name}) 以及股票代码 {symbol} 最相关的“国家产业政策/国内政策/海外法规动态/交易所公告监管”新闻（最多10条，必须有真实的新闻标题、发布时间、来源和链接）。
        2. 筛选出与该股票或者行业产业链相关的“上游原材料/设备供应商重大动态”、“公司财务/研报业绩预测”和“下游核心客户/终端消费市场需求”新闻（最多10条，必须有真实的新闻标题、发布时间、来源和链接）。
        
        【极重要规则】：请确保 policies 和 upstreamDownstream 两个数组均最多各有 10 条有价值的新闻，绝对不可返回空列表！
        
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
        res = json.loads(content_res)
        
        # 写入数据库持久缓存，一小时内定死，不再消耗大模型 token
        database.save_cached_dynamics(symbol, res)
        return res
    except Exception as e:
        print(f"Error fetching industry dynamics via LLM: {e}")
        # 降级兜底
        policies = []
        updown = []
        for x in news_items[:20]:
            t_val = x.get("ctime", time.time())
            try:
                time_str = datetime.fromtimestamp(t_val).strftime("%m-%d %H:%M")
            except:
                time_str = "今日"
            pkg = {
                "title": x.get("title"),
                "source": x.get("source"),
                "url": x.get("url"),
                "time": time_str
            }
            if x.get("category") == "policy" and len(policies) < 10:
                policies.append(pkg)
            elif len(updown) < 10:
                updown.append(pkg)
        return {"policies": policies, "upstreamDownstream": updown}

fetcher = RealDataFetcher()

@router.get("/ai_attribution/{symbol}", response_model=AiAttributionResponse)
def get_ai_attribution(symbol: str, trigger: str = "manual"):
    # 1. Fetch real data
    quote_data = fetcher.get_stock_quote(symbol)
    
    # 2. 检查是否在自选监测列表中，不在则不调用大模型分析以防超额
    if not database.is_in_watchlist(symbol):
        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "score": None,
            "evidenceChain": {
                "technicalAndSentiment": "💡 本股票目前未加入监测列表。",
                "fundFactor": "大模型智能量化监控处于休眠状态。",
                "fundamentalAndNews": "请先在上方把该股票【加入监测】，即可激活全套大模型产业链分析与实时政策归因。",
                "sectorAndMacro": "本保护机制能帮您有效节省 API Key 额度消耗。"
            },
            "futureTrendPrediction": "⚠️ 本股票未加入监测列表，AI 情景与风险分析已休眠。请在上方将其加入监测列表以激活该功能。",
            "plainEnglishSummary": "本股票未加入监测列表，AI 总结监控已休眠。请在上方将其加入监测以激活本板块。",
            "aiJudgment": "⚠️ 请先加入监测列表！",
            "credibility": "无",
            "riskNotice": "添加监测后将展示完整风险提示与推演。"
        }

    stock_news = fetcher.get_stock_news(symbol)
    macro_env = fetcher.get_macro_environment()
    industry_news = fetcher.get_industry_news_dehydrated(symbol)
    finance_summary = fetcher.get_finance_summary(symbol)
    
    # 3. Fetch today's history for memory injection
    history_records = database.get_today_analysis_history(symbol)
    history_context = ""
    if history_records:
        history_context = "【今日历史追踪节点】\n"
        for idx, rec in enumerate(history_records):
            history_context += f"- {rec['time']} ({rec['trigger_type']}): {rec['plain_english_summary']}\n"
            
    # 4. 只有可追溯的数据才能作为产业链证据；当前没有可靠映射时明确告知模型不要推演。
    chain_context = """
    【产业链上下游映射关系】
    当前没有提供可追溯的产业链映射。不得凭知识库编造上游供应商或下游客户；证据不足时请明确写“暂无法确认”。
    """
        
    # 5. Fetch cached dynamics news and format for prompt
    industry_name = "未确认行业"
    dynamics = fetch_real_industry_dynamics(symbol, industry_name)
    
    policies_list = dynamics.get("policies", [])
    upstream_downstream_list = dynamics.get("upstreamDownstream", [])
    
    policies_context = "【右侧面板同步的最新行业相关政策快讯】\n"
    for p in policies_list:
        policies_context += f"- 【{p.get('time', '实时')}】{p.get('title')} (来源: {p.get('source')})\n"
        
    dynamics_context = "【右侧面板同步的最新上下游产业链动态快讯】\n"
    for d in upstream_downstream_list:
        dynamics_context += f"- 【{d.get('time', '实时')}】{d.get('title')} (来源: {d.get('source')})\n"
    
    # 6. Call AI pipeline
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct"),
            "score": None,
            "evidenceChain": {},
            "futureTrendPrediction": "暂无推演内容",
            "plainEnglishSummary": "暂无总结",
            "aiJudgment": "⚠️ 请在后台配置 LLM_API_KEY 后，方可启动 AI 深度推理功能。",
            "credibility": "无",
            "riskNotice": "配置 API 密钥后将显示完整风险提示。"
        }
        
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        prompt = f"""
        你是一位顶尖的“量化与基本面结合”的半导体行业分析师。你的语言风格应该是【专业、犀利、有洞察力，且富有极强的市场嗅觉】，绝对不要死气沉沉或者像机器生成的公文！
        请根据以下抓取到的真实数据，输出一段鲜活、有深度的涨跌归因分析。
        
        【客观数据输入】
        股票名称：{quote_data.get('name', symbol)} ({symbol})
        今日涨跌幅：{quote_data.get('change_pct') if quote_data.get('change_pct') is not None else '暂无数据'}
        量比：{quote_data.get('volume_ratio') if quote_data.get('volume_ratio') is not None else '暂无数据'}
        个股资金流向：{fund_info if 'fund_info' in locals() else '暂无'}
        所属板块表现：{sector_info if 'sector_info' in locals() else '暂无'}
        个股绝对相关新闻（定向狙击）：{stock_news}
        行业核心事件精选（百条脱水提纯）：{industry_news}
        宏观与海外环境：{macro_env}
        
        【公司基本面核心财务摘要（最近3个报告期）】
        {finance_summary}
        
        【右侧面板同步的最新行业相关政策（共用同一套缓存，保持逻辑吻合）】
        {policies_context}
        
        【右侧面板同步的最新上下游产业链动态（共用同一套缓存，保持逻辑吻合）】
        {dynamics_context}
        
        {chain_context}
        
        {history_context}
        
        【事实归因与风险分析指令】
        必须严格按以下两个层次进行深度思考并输出内容：
        只分析局势、影响、风险和证据，不提供任何交易指令，不对价格方向作确定性承诺。
        
        第一层：【今日复盘总结】（陈述事实与逻辑映射）
        结合盘口的量价表现、资金流向，以及上述“个股定向新闻”、“行业脱水事件”，特别是“最新行业政策”和“上下游动态”，精准复盘“今天这只股票为什么会走出这样的形态”。不要流水账式复述新闻，要点出核心驱动力（是情绪错杀，还是基本面/产业链/财务/政策层面的多重共振？）。
        必须在分析中带出信息出处，使用严谨的 Markdown 超链接，如：`<br/>[来源: 新浪财经](url)`。
        
        第二层：【情景影响与风险分析】（以基本面为主，技术面为辅）
        请基于公司最新季度财报、产业链上下游变化和当日市场情绪，列出可能的正面、中性和负面情景。每个情景都要说明触发条件、可能影响、主要风险与待验证证据；证据不足时明确写“暂无法确认”。
        
        【极简要求】
        第三层：【极简通俗总结】
        在深度分析的末尾，必须给出一段极简通俗总结。用容易理解的语言说清楚今天发生了什么、对公司和行业可能产生什么影响、接下来需要关注哪些证据和风险。
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
            "plainEnglishSummary": "用50~100字通俗易懂地讲清楚今天发生了什么、可能影响和需要关注的风险与证据，开头不要带任何括号或前缀标题。",
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
            "riskNotice": ai_res.get("riskNotice", "")
        }
        database.save_analysis_history(symbol, trigger, plain_english, final_dict)
        return final_dict

    except Exception as e:
        print(f"LLM Error: {e}")
        return {
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
            "riskNotice": "请检查网络或 API Key 额度。"
        }
