import requests

url = "http://127.0.0.1:8000/api/stock/batch_overview?symbols=000021,601138,002475,600584,002241,601231"
r = requests.get(url)
print(r.json())
