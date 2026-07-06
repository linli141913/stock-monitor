import os
import json
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any
from dotenv import load_dotenv
from openai import OpenAI
from real_data_fetcher import RealDataFetcher

load_dotenv()

router = APIRouter(prefix="/api/stock", tags=["AI Attribution"])

class AiAttributionResponse(BaseModel):
    stockName: str
    stockCode: str
    changePercent: float
    evidenceChain: Dict[str, str]
    aiJudgment: str
    credibility: str
    riskNotice: str

fetcher = RealDataFetcher()

@router.get("/ai_attribution/{symbol}", response_model=AiAttributionResponse)
def get_ai_attribution(symbol: str):
    # 1. Fetch real data
    quote_data = fetcher.get_stock_quote(symbol)
    stock_news = fetcher.get_stock_news(symbol)
    macro_env = fetcher.get_macro_environment()
    
    # Check if we have an API Key
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        # Graceful degradation if no API key
        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct", 0.0),
            "evidenceChain": {
                "stockPerformance": f"今日涨跌幅 {quote_data.get('change_pct', 0.0)}%，量比 {quote_data.get('volume_ratio', 1.0)}。",
                "sectorFactor": "暂未获取板块实时数据",
                "fundFactor": "暂未获取资金流数据",
                "newsFactor": stock_news,
                "overseasFactor": macro_env
            },
            "aiJudgment": "⚠️ 请在后台配置 LLM_API_KEY 后，方可启动 AI 深度推理功能。",
            "credibility": "无",
            "riskNotice": "配置 API 密钥后将显示完整风险提示。"
        }
    
    # 2. Call actual LLM
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")

        prompt = f"""
        你是一位顶级的量化与基本面结合的半导体行业分析师。
        请根据以下抓取到的真实数据，输出一段严谨的涨跌归因分析。
        
        【客观数据输入】
        股票名称：{quote_data.get('name', symbol)} ({symbol})
        今日涨跌幅：{quote_data.get('change_pct', 0.0)}%
        量比：{quote_data.get('volume_ratio', 1.0)}
        最新公司资讯：{stock_news}
        宏观与海外环境：{macro_env}
        
        【输出格式要求】
        请务必返回合法的 JSON 格式，不要包含任何 markdown 标记(如```json)，只需纯净的 JSON 字符串。
        格式如下：
        {{
            "evidenceChain": {{
                "stockPerformance": "根据量价数据分析个股表现...",
                "sectorFactor": "分析板块共振情况...",
                "fundFactor": "推测资金态度...",
                "newsFactor": "结合公司最新资讯...",
                "overseasFactor": "结合海外环境分析..."
            }},
            "aiJudgment": "一两句话的最终综合结论",
            "credibility": "高 / 中 / 低",
            "riskNotice": "一句话风险提示"
        }}
        严禁胡编乱造，如果缺乏某个维度的强烈信息，可以说“受大盘环境主导”。
        """

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个严格返回JSON格式的股票分析API。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        ai_res = json.loads(content)

        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct", 0.0),
            "evidenceChain": ai_res.get("evidenceChain", {}),
            "aiJudgment": ai_res.get("aiJudgment", "推理失败"),
            "credibility": ai_res.get("credibility", "未知"),
            "riskNotice": ai_res.get("riskNotice", "")
        }

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
            "aiJudgment": f"大模型接口调用失败: {str(e)}",
            "credibility": "错误",
            "riskNotice": "请检查网络或 API Key 额度。"
        }
