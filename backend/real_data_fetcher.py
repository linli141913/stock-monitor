import akshare as ak
import pandas as pd
from typing import Dict, Any

class RealDataFetcher:
    def __init__(self):
        pass

    def get_stock_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch basic stock info and latest daily quote (Mocked fallback if akshare fails)."""
        try:
            # We assume symbol is like '000021'
            stock_zh_a_spot_em_df = ak.stock_zh_a_spot_em()
            row = stock_zh_a_spot_em_df[stock_zh_a_spot_em_df['代码'] == symbol]
            if not row.empty:
                return {
                    "name": row.iloc[0]['名称'],
                    "price": row.iloc[0]['最新价'],
                    "change_pct": row.iloc[0]['涨跌幅'],
                    "volume_ratio": row.iloc[0]['量比'],
                    "turnover_rate": row.iloc[0]['换手率']
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
                # Get top 3 news titles
                titles = news_df.head(3)['新闻标题'].tolist()
                return "；".join(titles)
        except Exception as e:
            print(f"Error fetching news for {symbol}: {e}")
            
        return "暂无最新重大公司新闻。"

    def get_macro_environment(self) -> str:
        """Fetch macro environment (e.g., sector, SOX, etc. - mocked temporarily for stability, can be expanded to scrape)"""
        # In a full production system, we would scrape or use specific AKShare APIs for SOX index, NVDA, etc.
        # For now, return a stable representative string so the LLM has context.
        return "海外半导体：昨夜费城半导体指数异动，英伟达等核心标的波动较大。国内：半导体板块整体受国产替代政策预期提振。"

