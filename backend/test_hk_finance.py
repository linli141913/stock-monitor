import akshare as ak
df = ak.stock_financial_hk_analysis_indicator_em(symbol="09863")
print(df.head().to_dict('records')[:2])
