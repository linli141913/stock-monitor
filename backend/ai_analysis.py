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
    changePercent: float
    score: int
    evidenceChain: Dict[str, str]
    futureTrendPrediction: str
    plainEnglishSummary: Optional[str] = "暂无总结"
    aiJudgment: str
    credibility: str
    riskNotice: str

fetcher = RealDataFetcher()

@router.get("/ai_attribution/{symbol}", response_model=AiAttributionResponse)
def get_ai_attribution(symbol: str, trigger: str = "manual"):
    # 1. Fetch real data
    quote_data = fetcher.get_stock_quote(symbol)
    stock_news = fetcher.get_stock_news(symbol)
    macro_env = fetcher.get_macro_environment()
    industry_news = fetcher.get_industry_news_dehydrated(symbol)
    
    # 2. Fetch today's history for memory injection
    history_records = database.get_today_analysis_history(symbol)
    history_context = ""
    if history_records:
        history_context = "【今日历史追踪节点】\n"
        for idx, rec in enumerate(history_records):
            history_context += f"- {rec['time']} ({rec['trigger_type']}): {rec['plain_english_summary']}\n"
    
    # 3. Call AI pipeline
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct", 0.0),
            "score": 50,
            "evidenceChain": {},
            "futureTrendPrediction": "暂无推演内容",
            "plainEnglishSummary": "暂无总结",
            "aiJudgment": "⚠️ 请在后台配置 LLM_API_KEY 后，方可启动 AI 深度推理功能。",
            "credibility": "无",
            "riskNotice": "配置 API 密钥后将显示完整风险提示。"
        }
        
    try:
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
        今日涨跌幅：{quote_data.get('change_pct', 0.0)}%
        量比：{quote_data.get('volume_ratio', 1.0)}
        个股资金流向：{fund_info if 'fund_info' in locals() else '暂无'}
        所属板块表现：{sector_info if 'sector_info' in locals() else '暂无'}
        个股绝对相关新闻（定向狙击）：{stock_news}
        行业核心事件精选（百条脱水提纯）：{industry_news}
        宏观与海外环境：{macro_env}
        
        {history_context}
        
        【深度分析双引擎指令】
        必须严格按以下两个层次进行深度思考并输出内容：
        
        第一层：【今日复盘总结】（陈述事实与逻辑映射）
        结合盘口的量价表现、资金流向，以及上述“个股定向新闻”和“行业脱水事件”，精准复盘“今天这只股票为什么会走出这样的形态”。不要流水账式复述新闻，要点出核心驱动力（是情绪错杀，还是基本面共振？）。
        必须在分析中带出信息出处，使用严谨的 Markdown 超链接，如：`<br/>[来源: 新浪财经](url)`。
        
        第二层：【未来走势深度分析】（以基本面为主，技术面为辅）
        请利用机构投研思维推演，严禁说套话！
        基于今天发生的大事件和盘口情绪，推导明天或下周可能的资金进攻方向。如果有利好，预期能发酵到什么程度？如果有大跌，下方的逻辑支撑在哪里？有何致命风险？
        
        【极简要求】
        第三层：【小学生大白话】
        在深度分析的末尾，必须给出一段极简大白话总结。像给小白讲故事一样，通俗易懂地解释清楚这只股票今天到底发生了什么，主力在搞什么鬼，以及接下来的具体建议。
        【极其严格的死命令】：字数必须在 50~100 字之间，必须讲透细节，绝对不可使用一句短话敷衍！如果不满 50 字将视为严重错误并作废！这段话存放在 JSON 的 plainEnglishSummary 字段中。
        
        【输出格式要求】
        请务必返回合法的 JSON 格式，不要包含任何 markdown 标记(如```json)，只需纯净的 JSON 字符串。
        格式如下：
        {{
            "score": 75, // 0-100的机构健康度综合评分，70以上为健康，40以下为高危
            "evidenceChain": {{
                "technicalAndSentiment": "【量价与情绪面】用精炼语言剖析当天的量价异动...",
                "fundFactor": "【资金面博弈】洞察主力资金真实意图...",
                "fundamentalAndNews": "【基本面与资讯】把今日复盘总结写在这里，深度解读脱水资讯对股价的催化作用(务必附带链接)...",
                "sectorAndMacro": "【板块与宏观共振】一针见血指出板块协同与全球宏观映射..."
            }},
            "futureTrendPrediction": "【未来走势深度分析】写在这里，给出具有投研深度的短中期推演，不要模棱两可。",
            "plainEnglishSummary": "【小学生大白话】用50~100字讲清楚今天到底发生了什么，主力在干嘛，以及对接下来的操作建议。",
            "aiJudgment": "【一针见血】的最终综合诊断结论。",
            "credibility": "高 / 中 / 低",
            "riskNotice": "一句话致命风险提示"
        }}
        """
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个严格返回JSON格式的顶级机构股票分析API。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        print("RAW_LLM_OUTPUT:", content, flush=True)
        ai_res = json.loads(content)

        # 4. Save to database
        plain_english = ai_res.get("plainEnglishSummary", "暂无总结")

        final_dict = {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct", 0.0),
            "score": ai_res.get("score", 50),
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
            "changePercent": quote_data.get("change_pct", 0.0),
            "evidenceChain": {
                "stockPerformance": "-",
                "sectorFactor": "-",
                "fundFactor": "-",
                "newsFactor": "-",
                "overseasFactor": "-"
            },
            "score": 50,
            "aiJudgment": f"大模型接口调用失败: {str(e)}",
            "credibility": "错误",
            "riskNotice": "请检查网络或 API Key 额度。"
        }
