import akshare as ak
try:
    df = ak.stock_info_global_cls()
    print(df.head(5).to_dict('records'))
except Exception as e:
    print("Error:", e)
