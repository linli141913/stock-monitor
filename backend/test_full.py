from fastapi.testclient import TestClient
from main import app
import json

client = TestClient(app)

print("--- HK (09863) ---")
res = client.get("/api/stock/overview/09863").json()
print("Overview flow:", res.get('fundFlow'))

res2 = client.get("/api/stock/company/09863").json()
print("Company:", res2['companyInfo']['mainBusiness'], res2['financialSummary']['netProfit'])

res3 = client.get("/api/stock/industry/09863").json()
print("Industry:", res3['industryName'], res3['fundFlow'])

res4 = client.get("/api/stock/related/09863").json()
print("Related:", [(x['stockName'], x['fundFlow']) for x in res4['data'][:3]])

res5 = client.get("/api/stock/abnormal_peers/09863").json()
print("Peers:", [(x['stockName'], x['fundFlow']) for x in res5['data'][:3]])

print("\n--- A Share (000001) ---")
resa = client.get("/api/stock/overview/000001").json()
print("Overview flow:", resa.get('fundFlow'))

resa2 = client.get("/api/stock/company/000001").json()
print("Company:", resa2['companyInfo']['mainBusiness'])

