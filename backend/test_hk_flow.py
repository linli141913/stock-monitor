import requests
url = "http://push2.eastmoney.com/api/qt/stock/get?secid=116.09863&fields=f62,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149"
print(requests.get(url).text)
