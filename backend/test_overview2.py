import requests

symbol = "000021" # 深科技
em_prefix = "1." if symbol.startswith('6') else "0."
em_url = f"http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={em_prefix}{symbol}&fields=f12,f62"
try:
    resp_em = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
    print("Status:", resp_em.status_code)
    data = resp_em.json()
    print("JSON:", data)
except Exception as e:
    print(e)
