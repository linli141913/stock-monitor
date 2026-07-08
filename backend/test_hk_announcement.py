import akshare as ak
try:
    df = ak.stock_hk_gg_dfcf()
    print("stock_hk_gg_dfcf success:", df.head())
except Exception as e:
    print(e)
