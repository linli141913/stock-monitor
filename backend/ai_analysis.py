import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

import os
import json
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from fastapi import APIRouter
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from pydantic import BaseModel
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from typing import Dict, Any
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from dotenv import load_dotenv
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from openai import OpenAI
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from real_data_fetcher import RealDataFetcher

load_dotenv()

router = APIRouter(prefix="/api/stock", tags=["AI Attribution"])

class AiAttributionResponse(BaseModel):
    stockName: str
    stockCode: str
    changePercent: float
    score: int
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
    
    # 获取真实的板块与个股资金流数据
    sector_info = "暂无"
    fund_info = "暂无"
    try:
        import requests
        # 获取个股资金流
        prefix = "1." if symbol.startswith('6') else "0."
        url = f"http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={prefix}{symbol}&fields=f12,f62"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("diff", [])
            if data and data[0].get("f62") is not None:
                flow_yi = data[0].get("f62") / 100000000.0
                fund_info = f"今日主力净{'流入' if flow_yi > 0 else '流出'} {abs(round(flow_yi, 2))} 亿元"
                
        # 获取板块资金流 (复用内部接口逻辑或直接调本地接口)
        local_url = f"http://127.0.0.1:8001/api/stock/industry/{symbol}"
        resp2 = requests.get(local_url, timeout=3)
        if resp2.status_code == 200:
            ind_data = resp2.json()
            sector_info = f"所属板块 {ind_data.get('industryName')}，今日涨跌幅 {ind_data.get('sectorChangePercent')}%，板块资金{ind_data.get('fundFlow')}。"
    except Exception as e:
        print("Error fetching fund flow for AI:", e)
    
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
                "sectorFactor": sector_info,
                "fundFactor": fund_info,
                "newsFactor": stock_news,
                "overseasFactor": macro_env
            },
            "score": 50,
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
        你是一位顶尖的“量化与基本面结合”的半导体行业分析师。你的语言风格应该是【专业、犀利、有洞察力，且富有极强的市场嗅觉】，绝对不要死气沉沉或者像机器生成的公文！
        请根据以下抓取到的真实数据，输出一段鲜活、有深度的涨跌归因分析。
        
        【重要规则】
        1. 在分析新闻资讯或宏观环境时，**必须在段落的最后，另起一行，使用严格的 Markdown 格式输出信息出处的超链接**。
           格式要求：`<br/>[来源: 文章来源平台](原文链接)`
           例如：`<br/>[来源: 新浪财经](http://finance.sina.com...)`
        2. 如果没有明确出处，请注明`<br/>[来源: 量化数据抓取]`。
        3. 请确保链接是可以直接点击的纯标准 Markdown 语法！
        
        【客观数据输入】
        股票名称：{quote_data.get('name', symbol)} ({symbol})
        今日涨跌幅：{quote_data.get('change_pct', 0.0)}%
        量比：{quote_data.get('volume_ratio', 1.0)}
        个股资金流向：{fund_info}
        所属板块表现：{sector_info}
        最新公司资讯：{stock_news}
        宏观与海外环境：{macro_env}
        
        【输出格式要求】
        请务必返回合法的 JSON 格式，不要包含任何 markdown 标记(如```json)，只需纯净的 JSON 字符串。
        格式如下：
        {{
            "score": 75, // 0-100的机构健康度综合评分，70以上为健康，40以下为高危
            "evidenceChain": {{
                "technicalAndSentiment": "【量价与情绪面】用精炼、犀利的语言剖析当天的量价异动与市场情绪...",
                "fundFactor": "【资金面博弈】洞察主力资金流向的真实意图...",
                "fundamentalAndNews": "【基本面与资讯】深度解读最新基本面数据与资讯影响(务必附上类似 <br/>[来源: xx](url) 的出处)...",
                "sectorAndMacro": "【板块与宏观共振】一针见血指出板块协同效应与全球宏观映射(务必附上类似 <br/>[来源: xx](url) 的出处)..."
            }},
            "futureTrendPrediction": "【未来走势预测】给出明确的短期和中期走势推演，不要模棱两可。",
            "aiJudgment": "【一针见血】的最终综合诊断结论，要像资深机构操盘手一样具有前瞻性。",
            "credibility": "高 / 中 / 低",
            "riskNotice": "一句话致命风险提示"
        }}
        严禁胡编乱造，如果缺乏某个维度的信息，可以说“暂无明显催化剂”。
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
        ai_res = json.loads(content)

        return {
            "stockName": quote_data.get("name", symbol),
            "stockCode": symbol,
            "changePercent": quote_data.get("change_pct", 0.0),
            "score": ai_res.get("score", 50),
            "evidenceChain": ai_res.get("evidenceChain", {}),
            "futureTrendPrediction": ai_res.get("futureTrendPrediction", "暂无预测"),
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
            "score": 50,
            "aiJudgment": f"大模型接口调用失败: {str(e)}",
            "credibility": "错误",
            "riskNotice": "请检查网络或 API Key 额度。"
        }
