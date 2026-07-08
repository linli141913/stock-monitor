import akshare as ak
try:
    df_info = ak.stock_individual_info_em(symbol="00700")
    print(df_info.head())
except Exception as e:
    print(e)
