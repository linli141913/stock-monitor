import requests
url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62"
try:
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        proxies={"http": None, "https": None},
        timeout=5
    )
    print("STATUS:", resp.status_code)
    print("DATA:", resp.text[:300])
except Exception as e:
    print("ERROR:", e)
