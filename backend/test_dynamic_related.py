import requests
import json

symbol = "002340"

# 1. Get company info to find sector
url_info = f"http://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?code={'SZ' if symbol.startswith('0') else 'SH'}{symbol}"
resp_info = requests.get(url_info, headers={"User-Agent": "Mozilla/5.0"}, proxies={})
info_data = resp_info.json()
industry = info_data.get("SecurityMarketInfo", [{}])[0].get("Industry", "")
print("Industry:", industry)

# 2. Find sector code
sector_code = ""
for pn in range(1, 6):
    url_sector = f"http://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14"
    r = requests.get(url_sector, headers={"User-Agent": "Mozilla/5.0"}, proxies={}).json()
    diff = r.get("data", {}).get("diff", [])
    for item in diff:
        if item.get("f14") == industry:
            sector_code = item.get("f12")
            break
    if sector_code: break

print("Sector Code:", sector_code)

# 3. Get constituents of the sector
if sector_code:
    url_const = f"http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=b:{sector_code}&fields=f12,f14,f2,f3,f62"
    r = requests.get(url_const, headers={"User-Agent": "Mozilla/5.0"}, proxies={}).json()
    consts = r.get("data", {}).get("diff", [])
    print("Constituents:")
    for c in consts:
        if c.get("f12") != symbol:
            print(f"{c.get('f14')} ({c.get('f12')}): {c.get('f3')}% Flow: {c.get('f62')}")
