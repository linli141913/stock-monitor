import requests
all_sectors = []
for i in range(1, 6):
    url = f"http://push2.eastmoney.com/api/qt/clist/get?pn={i}&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, proxies={})
    data = resp.json().get("data")
    if not data: continue
    diff = data.get("diff", [])
    if not diff: break
    names = [item.get("f14") for item in diff]
    all_sectors.extend(diff)

print(f"Total sectors t:2: {len(all_sectors)}")
print("消费电子" in [item.get("f14") for item in all_sectors])

all_concepts = []
for i in range(1, 6):
    url = f"http://push2.eastmoney.com/api/qt/clist/get?pn={i}&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:3&fields=f12,f14,f2,f3,f62"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, proxies={})
    data = resp.json().get("data")
    if not data: continue
    diff = data.get("diff", [])
    if not diff: break
    names = [item.get("f14") for item in diff]
    all_concepts.extend(diff)

print(f"Total concepts t:3: {len(all_concepts)}")
item = next((i for i in all_concepts if i.get("f14") == "消费电子"), None)
print("消费电子 concept:", item)
