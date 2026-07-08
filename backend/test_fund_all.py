import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
import akshare as ak
try:
    df = ak.stock_individual_fund_flow_rank(indicator="今日")
    print(df.head(2).to_dict('records'))
except Exception as e:
    print(e)
