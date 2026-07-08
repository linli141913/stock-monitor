import requests
url = "http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=116.00700&klt=101&fqt=1&end=20500101&lmt=100&iscca=1&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
r = requests.get(url)
print(r.text[:200])
