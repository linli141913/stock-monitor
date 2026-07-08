import os
import re

def patch_fetcher():
    path = "real_data_fetcher.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    if "def get_industry_news_dehydrated" not in content:
        # Append the new method to the class
        # Find the end of get_macro_environment method
        new_method = """
    def get_industry_news_dehydrated(self, symbol: str) -> str:
        \"\"\"Fetch global news, filter by industry keywords, deduplicate to top 20.\"\"\"
        try:
            import requests
            url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=150&page=1"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=5)
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            
            industry_keywords = ["半导体", "芯片", "科技", "电子", "AI", "算力", "存储", "封测", "设备", "材料", "晶圆"]
            try:
                local_url = f"http://127.0.0.1:8001/api/stock/industry/{symbol}"
                ind_resp = requests.get(local_url, timeout=2)
                if ind_resp.status_code == 200:
                    ind_name = ind_resp.json().get('industryName', '')
                    if ind_name:
                        industry_keywords.append(ind_name)
            except:
                pass
                
            filtered_items = []
            seen_titles = set()
            
            for item in items:
                title = item.get("title", "")
                url = item.get("url", "")
                ctime = item.get("ctime", "")
                
                match = any(kw in title for kw in industry_keywords)
                if match:
                    if title not in seen_titles:
                        seen_titles.add(title)
                        from datetime import datetime
                        time_str = datetime.fromtimestamp(int(ctime)).strftime('%m-%d %H:%M') if ctime else ""
                        filtered_items.append(f"【{time_str}】{title} <br/>[来源: 新浪财经]({url})")
                        
                if len(filtered_items) >= 20:
                    break
                    
            if not filtered_items:
                return "今日暂无高价值行业脱水事件。"
                
            return "\\n".join(filtered_items)
        except Exception as e:
            print(f"Error fetching industry news: {e}")
            return "行业资讯获取失败。"
"""
        content += new_method
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched real_data_fetcher.py")
    else:
        print("get_industry_news_dehydrated already exists in real_data_fetcher.py")

def patch_analysis():
    path = "ai_analysis.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Replace the fetch block
    old_fetch = "macro_env = fetcher.get_macro_environment()"
    new_fetch = """macro_env = fetcher.get_macro_environment()
    industry_news = fetcher.get_industry_news_dehydrated(symbol)"""
    if "industry_news = fetcher.get_industry_news_dehydrated(symbol)" not in content:
        content = content.replace(old_fetch, new_fetch)
        
    # Replace the prompt section
    # Use regex to replace everything from "prompt = f\"\"\"" to "response = client.chat.completions.create("
    
    prompt_start = content.find("prompt = f\"\"\"")
    prompt_end = content.find("response = client.chat.completions.create(")
    
    if prompt_start != -1 and prompt_end != -1:
        new_prompt = '''prompt = f"""
        你是一位顶尖的“量化与基本面结合”的半导体行业分析师。你的语言风格应该是【专业、犀利、有洞察力，且富有极强的市场嗅觉】，绝对不要死气沉沉或者像机器生成的公文！
        请根据以下抓取到的真实数据，输出一段鲜活、有深度的涨跌归因分析。
        
        【客观数据输入】
        股票名称：{quote_data.get('name', symbol)} ({symbol})
        今日涨跌幅：{quote_data.get('change_pct', 0.0)}%
        量比：{quote_data.get('volume_ratio', 1.0)}
        个股资金流向：{fund_info}
        所属板块表现：{sector_info}
        个股绝对相关新闻（定向狙击）：{stock_news}
        行业核心事件精选（百条脱水提纯）：{industry_news}
        宏观与海外环境：{macro_env}
        
        【深度分析双引擎指令】
        必须严格按以下两个层次进行深度思考并输出内容：
        
        第一层：【今日复盘总结】（陈述事实与逻辑映射）
        结合盘口的量价表现、资金流向，以及上述“个股定向新闻”和“行业脱水事件”，精准复盘“今天这只股票为什么会走出这样的形态”。不要流水账式复述新闻，要点出核心驱动力（是情绪错杀，还是基本面共振？）。
        必须在分析中带出信息出处，使用严谨的 Markdown 超链接，如：`<br/>[来源: 新浪财经](url)`。
        
        第二层：【未来走势深度分析】（以基本面为主，技术面为辅）
        请利用机构投研思维推演，严禁说套话！
        基于今天发生的大事件和盘口情绪，推导明天或下周可能的资金进攻方向。如果有利好，预期能发酵到什么程度？如果有大跌，下方的逻辑支撑在哪里？有何致命风险？
        
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
            "aiJudgment": "【一针见血】的最终综合诊断结论。",
            "credibility": "高 / 中 / 低",
            "riskNotice": "一句话致命风险提示"
        }}
        """
        
        '''
        content = content[:prompt_start] + new_prompt + content[prompt_end:]
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched ai_analysis.py")

if __name__ == "__main__":
    patch_fetcher()
    patch_analysis()
