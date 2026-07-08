import akshare as ak
try:
    print(ak.stock_hk_company_profile_em(symbol="09863")[["所属行业"]].to_dict('records'))
except: pass
