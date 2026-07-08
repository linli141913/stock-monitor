import requests
import akshare as ak

# 1. Company Info: Is there an akshare function for HK company info?
print("--- HK Company Info ---")
try:
    df_profile = ak.stock_hk_spot_em()
    # just see if it exists
    print("spot_em columns:", df_profile.columns.tolist()[:5])
except Exception as e:
    print("spot_em error:", e)

# 2. Can we get HK stock fund flow?
print("--- HK Fund Flow ---")
url_flow = "http://push2.eastmoney.com/api/qt/stock/get?secid=116.00700&fields=f14,f62,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149"
print("Eastmoney Flow details:", requests.get(url_flow).text)

