import akshare as ak
df = ak.stock_info_global_sina()
print(df.columns.tolist())
print(df.head(1).to_dict('records'))
