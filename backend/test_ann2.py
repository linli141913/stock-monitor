import requests
u = "http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&client_source=web&stock_list=09863,116.09863"
r = requests.get(u)
print(r.json())
