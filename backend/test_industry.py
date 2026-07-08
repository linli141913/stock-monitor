import requests
import json

url = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, proxies={})
data = resp.json().get("data", {}).get("diff", [])
names = [item.get("f14") for item in data]
print(f"Total sectors: {len(names)}")
print("消费电子" in names)
print(names[:20])
