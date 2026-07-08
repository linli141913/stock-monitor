import requests

url1 = "http://qt.gtimg.cn/q=sz009863"
url2 = "http://qt.gtimg.cn/q=hk09863"

print("sz009863:", requests.get(url1).text)
print("hk09863:", requests.get(url2).text)
