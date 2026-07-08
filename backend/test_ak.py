import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['all_proxy'] = ''
os.environ['ALL_PROXY'] = ''
os.environ['no_proxy'] = '*'
os.environ['NO_PROXY'] = '*'

import akshare as ak
import traceback

try:
    df = ak.stock_zh_a_spot_em()
    print("Success! shape:", df.shape)
    print("Columns:", df.columns.tolist())
    row = df[df["代码"] == "000021"]
    print("Row empty?", row.empty)
except Exception as e:
    print("Error:")
    traceback.print_exc()
