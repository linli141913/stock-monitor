import requests

# Let's search Eastmoney for Leapmotor (09863) to see if we can find its related peers.
# A common way is to query the sector it belongs to and list stocks in that sector.
import akshare as ak
try:
    df = ak.stock_hk_hot_rank_em()
    print("hot rank columns:", df.columns.tolist())
    print(df.head())
except Exception as e:
    pass
