import requests

url = "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101&secid=1.600000&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&ut=b2884a393a59ad64002292a3e90d46a5"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
print(resp.status_code)
data = resp.json().get("data", {}).get("klines", [])
if data:
    last_day = data[-1].split(',')
    # f52 = 主力净流入 (net large order inflow)
    print("Net inflow (RMB):", last_day[1])
