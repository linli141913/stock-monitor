import requests
url = "http://push2.eastmoney.com/api/qt/stock/get?ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&secid=116.00700&fields=f14,f62"
print(requests.get(url).text)
