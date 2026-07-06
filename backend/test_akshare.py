import akshare as ak
import json

try:
    # 东方财富-财务分析-主要指标
    df = ak.stock_financial_analysis_indicator(symbol="000021")
    print(df.head())
except Exception as e:
    print(e)
