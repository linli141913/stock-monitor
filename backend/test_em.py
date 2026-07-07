import requests

secids = "1.600000,0.000001"
url = f"http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={secids}&fields=f12,f14,f62,f2,f3"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
print(resp.json())
