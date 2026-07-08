import requests
import json

urls = [
    "http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list=09863",
    "http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list=116.09863",
    "http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=HK&client_source=web&stock_list=09863"
]

for u in urls:
    try:
        r = requests.get(u)
        print(u)
        print(len(r.json().get('data', {}).get('list', [])))
    except Exception as e:
        print(e)
