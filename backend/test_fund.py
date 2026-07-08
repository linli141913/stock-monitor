import requests
import json

url = "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62"
try:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
    print(resp.status_code)
    print(resp.json())
except Exception as e:
    print(e)
