import re

with open('main.py', 'r') as f:
    content = f.read()

# 1. Fix get_stock_overview update_time logic
old_time_logic = '''    # 格式化时间
    if len(update_time) >= 14:
        update_time = (
            f"{update_time[:4]}-{update_time[4:6]}-{update_time[6:8]} "
            f"{update_time[8:10]}:{update_time[10:12]}:{update_time[12:14]}"
        )
    else:
        update_time = "当前"'''

new_time_logic = '''    # 格式化时间
    if "/" in update_time and ":" in update_time:
        # 已经是 2026/07/06 15:11:12 这种格式，转成横杠
        update_time = update_time.replace("/", "-")
    elif len(update_time) >= 14:
        update_time = (
            f"{update_time[:4]}-{update_time[4:6]}-{update_time[6:8]} "
            f"{update_time[8:10]}:{update_time[10:12]}:{update_time[12:14]}"
        )
    else:
        update_time = "当前"'''
if old_time_logic in content:
    content = content.replace(old_time_logic, new_time_logic)
    print('Fixed update_time')

# 2. Fix get_finance_data operateCashFlow for HK
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
    print('Fixed HK finance cash flow')
else:
    print('Failed to find old HK finance cash flow block')

# 3. Fix HK announcements in get_company_info
# Currently there is:
old_ann_block = '''    # 3. 抓取最新公告
    ann_url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web&stock_list={symbol}"
    announcements = []'''

new_ann_block = '''    # 3. 抓取最新公告
    ann_type_param = "H" if is_hk else "A"
    ann_stock = symbol_pure if is_hk else symbol
    ann_url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type={ann_type_param}&client_source=web&stock_list={ann_stock}"
    announcements = []'''

# Wait, in get_company_info, we currently early-returned for is_hk BEFORE the announcement fetch logic!
# Let's check `if is_hk:` in get_company_info.
