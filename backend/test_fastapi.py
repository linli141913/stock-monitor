from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
response = client.get("/api/stock/industry/000021")
print(response.json())
