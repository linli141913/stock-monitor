import requests
import json

url1 = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62"
url2 = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:3&fields=f12,f14,f2,f3,f62"

for idx, url in enumerate([url1, url2]):
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, proxies={})
    data = resp.json().get("data", {})
    if data:
        data = data.get("diff", [])
    else:
        data = []
    names = [item.get("f14") for item in data]
    print(f"URL {idx} Total sectors: {len(names)}")
    if "消费电子" in names:
        item = next(i for i in data if i.get("f14") == "消费电子")
        print("FOUND", item)
