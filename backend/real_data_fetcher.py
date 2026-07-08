import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

import akshare as ak
import pandas as pd
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from typing import Dict, Any

class RealDataFetcher:
    def __init__(self):
        pass

    def get_stock_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch basic stock info and latest daily quote (using Tencent API directly)."""
        try:
            import requests
            prefix = "sh" if symbol.startswith('6') else "sz"
            url = f"http://qt.gtimg.cn/q={prefix}{symbol}"
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                data = resp.text
                if len(data) > 30:
                    v = data.split('~')
                    return {
                        "name": v[1],
                        "price": float(v[3]),
                        "change_pct": float(v[32]),
                        "volume_ratio": float(v[49]) if len(v) > 49 and v[49] else 1.0,
                        "turnover_rate": float(v[38]) if len(v) > 38 and v[38] else 1.0
                    }
        except Exception as e:
            print(f"Error fetching quote for {symbol}: {e}")
        
        # Fallback to avoid breaking the prompt
        return {
            "name": "未知股票",
            "price": 0.0,
            "change_pct": 0.0,
            "volume_ratio": 1.0,
            "turnover_rate": 1.0
        }

    def get_stock_news(self, symbol: str) -> str:
        """Fetch latest news for a specific stock."""
        try:
            news_df = ak.stock_news_em(symbol=symbol)
            if not news_df.empty:
                # Get top 3 news items
                news_items = []
                for _, row in news_df.head(3).iterrows():
                    title = row.get('新闻标题', '')
                    time = row.get('发布时间', '')
                    source = row.get('文章来源', '东方财富网')
                    url = row.get('新闻链接', '')
                    news_items.append(f"【{time}】{title} (来源: {source}, 链接: {url})")
                return "\n".join(news_items)
        except Exception as e:
            print(f"Error fetching news for {symbol}: {e}")
            
        return "暂无最新重大公司新闻。"

    def get_macro_environment(self) -> str:
        """Fetch macro environment (e.g., sector, SOX, etc. - mocked temporarily for stability, can be expanded to scrape)"""
        # In a full production system, we would scrape or use specific AKShare APIs for SOX index, NVDA, etc.
        # For now, return a stable representative string so the LLM has context.
        return "海外半导体：昨夜费城半导体指数异动，英伟达等核心标的波动较大。国内：半导体板块整体受国产替代政策预期提振。"


    def get_industry_news_dehydrated(self, symbol: str) -> str:
        """Fetch global news, filter by industry keywords, deduplicate to top 20."""
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
                
            return "\n".join(filtered_items)
        except Exception as e:
            print(f"Error fetching industry news: {e}")
            return "行业资讯获取失败。"
