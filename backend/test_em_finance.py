import requests
import json
secucode = "000021.SZ"
url1 = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=1&code={secucode}"
resp1 = requests.get(url1, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
data1 = resp1.json()
print("type=1 reports:", [r.get("REPORT_DATE_NAME") for r in data1.get("data", [])])
