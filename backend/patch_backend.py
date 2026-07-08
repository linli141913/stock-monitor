import sys

new_endpoint = """
@app.get("/api/stock/company/{symbol}")
def get_company_info(symbol: str):
    prefix = 'SZ' if symbol.startswith('0') or symbol.startswith('3') else 'SH'
    
    # 1. 抓取公司信息
    info_url = f"http://f10.eastmoney.com/CompanySurvey/CompanySurveyAjax?code={prefix}{symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    company_info = {
        "mainBusiness": "-",
        "coreProducts": [],
        "industryTags": [],
        "companyDescription": "-",
        "businessRelation": "与当前股票所属产业链相关",
        "updateTime": "-"
    }
    
    try:
        resp = requests.get(info_url, headers=headers, timeout=5)
        data = resp.json().get("jbzl", {})
        company_info["mainBusiness"] = data.get("zyyw", "-")
        company_info["companyDescription"] = data.get("gsjj", "-")
        industry = data.get("sshy", "")
        if industry:
            company_info["industryTags"] = industry.split("-")
        else:
            company_info["industryTags"] = [data.get("sszjhhy", "")]
    except Exception:
        pass

    # 2. 抓取公告
    ann_url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list={symbol}"
    announcements = []
    try:
        resp = requests.get(ann_url, headers=headers, timeout=5)
        data = resp.json().get("data", {}).get("list", [])
        for item in data:
            announcements.append({
                "id": item.get("art_code", ""),
                "title": item.get("title", ""),
                "publishTime": item.get("display_time", "")[:10],
                "source": "东方财富",
                "summary": item.get("title", ""),
                "url": f"https://data.eastmoney.com/notices/detail/{symbol}/{item.get('art_code')}.html",
                "importance": "中"
            })
    except Exception:
        pass

    return {
        "companyInfo": company_info,
        "announcements": announcements,
        "financialData": {
            "reportPeriod": "最新",
            "revenue": "-", "revenueYoy": "-", "netProfit": "-", "netProfitYoy": "-",
            "grossMargin": "-", "netMargin": "-", "roe": "-", "debtRatio": "-", "updateTime": "-"
        },
        "news": []
    }
"""

with open("main.py", "r") as f:
    content = f.read()

if "get_company_info" not in content:
    content = content.replace('if __name__ == "__main__":', new_endpoint + '\nif __name__ == "__main__":')
    with open("main.py", "w") as f:
        f.write(content)
    print("Backend patched successfully")
else:
    print("Endpoint already exists")

