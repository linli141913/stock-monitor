from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

res = client.get("/api/stock/finance/09863")
if res.status_code == 200:
    data = res.json()
    print("Latest report:", data.get("latest"))
    print("Yearly count:", len(data.get("yearly", [])))
else:
    print("Error:", res.status_code, res.text)

res2 = client.get("/api/stock/company/09863")
if res2.status_code == 200:
    data = res2.json()
    print("Company Info Keys:", list(data.keys()))
else:
    print("Error:", res2.status_code, res2.text)
