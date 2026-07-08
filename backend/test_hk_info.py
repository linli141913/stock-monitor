import akshare as ak
try:
    df_info = ak.stock_hk_profile(symbol="00700")
    print("HK Profile:")
    print(df_info.head())
except Exception as e:
    print(e)
