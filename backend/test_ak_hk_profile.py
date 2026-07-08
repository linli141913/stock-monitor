import akshare as ak

try:
    print("--- stock_hk_company_profile_em ---")
    df = ak.stock_hk_company_profile_em(symbol="09863")
    print(df.to_dict('records'))
except Exception as e:
    print("Error:", e)

try:
    print("--- stock_financial_hk_analysis_indicator_em ---")
    df2 = ak.stock_financial_hk_analysis_indicator_em(symbol="09863")
    print(df2.head().to_dict('records'))
except Exception as e:
    print("Error:", e)

