import requests
import json

def test_eastmoney_announcements(symbol):
    prefix = 'SZ' if symbol.startswith('0') or symbol.startswith('3') else 'SH'
    url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=5&page_index=1&ann_type=A&client_source=web&stock_list={symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers)
        print(f"Announcements: {resp.text[:500]}")
    except Exception as e:
        print(e)

test_eastmoney_announcements("000021")
