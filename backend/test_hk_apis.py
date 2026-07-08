from fastapi.testclient import TestClient
from main import app
import json

client = TestClient(app)
print("Overview:")
res1 = client.get("/api/stock/overview/00700")
print(json.dumps(res1.json(), ensure_ascii=False))

print("Kline:")
res2 = client.get("/api/stock/kline/00700?period=daily")
data2 = res2.json().get("data", [])
print(f"Kline count: {len(data2)}")

print("Related:")
res3 = client.get("/api/stock/related/00700")
print(json.dumps(res3.json(), ensure_ascii=False))
