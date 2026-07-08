import requests
import json
url = "http://push2.eastmoney.com/api/qt/stock/get?secid=116.09863&fields=f14,f13,f12,f201,f202,f203,f204,f205,f206,f207"
print(requests.get(url).text)

