import requests
# Tencent: hk00700
url1 = "http://qt.gtimg.cn/q=hk00700"
resp1 = requests.get(url1).text
print("Tencent GT:", resp1[:100])

# EastMoney secid for HK? 116.00700? Let's search
url2 = "http://push2.eastmoney.com/api/qt/slist/get?spt=1&fltt=2&invt=2&fields=f12,f13,f14&secid=116.00700"
resp2 = requests.get(url2).text
print("Tencent EM:", resp2[:100])
