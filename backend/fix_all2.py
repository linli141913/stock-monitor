with open('main.py', 'r') as f:
    content = f.read()

# 1. Fix HK cash flow in get_finance_data
old_finance_hk = '''            df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_pure)
            if df.empty:
                raise HTTPException(status_code=404, detail="暂无财报数据")
            df = df.fillna(0)
            reports = df.to_dict('records')
            
            formatted_all = []
            for r in reports:
                formatted_all.append({
                    "reportDate": str(r.get("REPORT_DATE", "-"))[:10],
                    "reportName": r.get("DATE_TYPE_CODE", "-") + "年报", 
                    "reportType": "年报", 
                    "revenue": r.get("OPERATE_INCOME", 0),
                    "revenueYoy": r.get("OPERATE_INCOME_YOY", 0),
                    "netProfit": r.get("HOLDER_PROFIT", 0),
                    "netProfitYoy": r.get("HOLDER_PROFIT_YOY", 0),
                    "deductNetProfit": r.get("HOLDER_PROFIT", 0), 
                    "deductNetProfitYoy": r.get("HOLDER_PROFIT_YOY", 0),
                    "grossMargin": r.get("GROSS_PROFIT_RATIO", 0),
                    "netMargin": r.get("NET_PROFIT_RATIO", 0),
                    "roe": r.get("ROE_YEARLY", 0),
                    "assetLiabilityRatio": r.get("DEBT_ASSET_RATIO", 0),
                    "operateCashFlow": None,
                    "eps": r.get("BASIC_EPS", 0)
                })'''

new_finance_hk = '''            df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_pure)
            if df.empty:
                raise HTTPException(status_code=404, detail="暂无财报数据")
            df = df.fillna(0)
            reports = df.to_dict('records')
            
            total_shares = 1
            try:
                df_ind = ak.stock_hk_financial_indicator_em(symbol=symbol_pure)
                if not df_ind.empty:
                    val = df_ind.iloc[0].get("已发行股本(股)", 0)
                    if val and str(val) != 'nan':
                        total_shares = float(val)
            except Exception as e:
                print("Failed to fetch total shares for HK", e)
            
            formatted_all = []
            for r in reports:
                per_cash = r.get("PER_NETCASH_OPERATE", 0)
                ocf = float(per_cash) * total_shares if per_cash and total_shares > 1 else None
                formatted_all.append({
                    "reportDate": str(r.get("REPORT_DATE", "-"))[:10],
                    "reportName": r.get("DATE_TYPE_CODE", "-") + "年报", 
                    "reportType": "年报", 
                    "revenue": r.get("OPERATE_INCOME", 0),
                    "revenueYoy": r.get("OPERATE_INCOME_YOY", 0),
                    "netProfit": r.get("HOLDER_PROFIT", 0),
                    "netProfitYoy": r.get("HOLDER_PROFIT_YOY", 0),
                    "deductNetProfit": r.get("HOLDER_PROFIT", 0), 
                    "deductNetProfitYoy": r.get("HOLDER_PROFIT_YOY", 0),
                    "grossMargin": r.get("GROSS_PROFIT_RATIO", 0),
                    "netMargin": r.get("NET_PROFIT_RATIO", 0),
                    "roe": r.get("ROE_YEARLY", 0),
                    "assetLiabilityRatio": r.get("DEBT_ASSET_RATIO", 0),
                    "operateCashFlow": ocf,
                    "eps": r.get("BASIC_EPS", 0)
                })'''
if old_finance_hk in content:
    content = content.replace(old_finance_hk, new_finance_hk)
    print('Fixed HK cash flow')

# 2. Fix HK announcements in get_company_info
old_ann = '''                "announcements": [],'''
new_ann = '''                "announcements": fetch_hk_announcements(symbol_pure),'''
if old_ann in content:
    content = content.replace(old_ann, new_ann, 1) # Only replace the first occurrence (inside get_company_info HK block)

# Add fetch_hk_announcements helper
helper_code = '''
def fetch_hk_announcements(symbol_pure):
    announcements = []
    try:
        import requests
        ann_url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=H&client_source=web&stock_list={symbol_pure}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(ann_url, headers=headers, timeout=5)
        data = resp.json().get("data", {}).get("list", [])
        for item in data:
            announcements.append({
                "id": item.get("art_code", ""),
                "title": item.get("title", ""),
                "publishTime": item.get("display_time", "")[:10],
                "source": "东方财富",
                "summary": item.get("title", ""),
                "url": f"https://data.eastmoney.com/notices/detail/{symbol_pure}/{item.get('art_code')}.html",
                "importance": "中"
            })
    except Exception as e:
        print("HK announcement fetch error:", e)
    return announcements

@app.get("/api/stock/company/{symbol}")'''

content = content.replace('@app.get("/api/stock/company/{symbol}")', helper_code)

with open('main.py', 'w') as f:
    f.write(content)
print('Done!')
