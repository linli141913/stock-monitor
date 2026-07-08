import akshare as ak
try:
    df = ak.stock_news_em(symbol="000021")
    print(df.columns)
    print(df.head(2))
except Exception as e:
    print(e)
