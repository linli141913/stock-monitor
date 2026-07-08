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

    def get_finance_summary(self, symbol: str) -> str:
        """Fetch real quarterly finance core indices from EastMoney/EMWeb."""
        try:
            import requests
            secucode = symbol
            if not ("." in symbol):
                secucode = f"{symbol}.SH" if symbol.startswith('6') else f"{symbol}.SZ"
            
            is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)
            if is_hk:
                import akshare as ak
                symbol_pure = symbol.lower().replace("hk", "")
                df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_pure)
                if df.empty:
                    return "暂无最新财报财务核心数据。"
                df = df.fillna(0)
                reports = df.to_dict('records')
                summary_lines = []
                for r in reports[:3]:
                    date_str = str(r.get("REPORT_DATE", ""))[:10]
                    rev = r.get("OPERATE_INCOME", 0) or 0
                    rev_yoy = r.get("OPERATE_INCOME_YOY", 0) or 0
                    net = r.get("HOLDER_PROFIT", 0) or 0
                    net_yoy = r.get("HOLDER_PROFIT_YOY", 0) or 0
                    gross = r.get("GROSS_PROFIT_RATIO", 0) or 0
                    roe = r.get("ROE_YEARLY", 0) or 0
                    summary_lines.append(
                        f"报告期: {date_str}, 营收: {rev:,.0f}元 (同比 {rev_yoy:.2f}%), "
                        f"归母净利润: {net:,.0f}元 (同比 {net_yoy:.2f}%), "
                        f"销售毛利率: {gross:.2f}%, ROE: {roe:.2f}%"
                    )
                return "\n".join(summary_lines)
            else:
                url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={secucode}"
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
                data = resp.json().get("data", [])
                if not data:
                    return "暂无最新财报财务核心数据。"
                
                summary_lines = []
                for r in data[:3]:
                    date_name = r.get("REPORT_DATE_NAME", "")
                    rev = r.get("TOTALOPERATEREVE") or 0
                    rev_yoy = r.get("TOTALOPERATEREVETZ") or 0
                    net = r.get("PARENTNETPROFIT") or 0
                    net_yoy = r.get("PARENTNETPROFITTZ") or 0
                    gross = r.get("XSMLL") or 0
                    roe = r.get("ROEJQ") or 0
                    summary_lines.append(
                        f"报告期: {date_name}, 营收: {rev/100000000.0:.2f}亿元 (同比 {rev_yoy:.2f}%), "
                        f"归母净利润: {net/100000000.0:.2f}亿元 (同比 {net_yoy:.2f}%), "
                        f"销售毛利率: {gross:.2f}%, ROE: {roe:.2f}%"
                    )
                return "\n".join(summary_lines)
        except Exception as e:
            print(f"Error fetching finance summary: {e}")
            return "财报核心数据获取失败。"
