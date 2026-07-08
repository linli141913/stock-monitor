import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
import akshare as ak
try:
    df = ak.stock_zh_a_spot_em()
    row = df[df['代码'] == '000021']
    print(row)
except Exception as e:
    print(e)
