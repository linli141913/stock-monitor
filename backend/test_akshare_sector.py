import akshare as ak
try:
    df = ak.stock_board_industry_cons_em(symbol="能源金属")
    print(df.head())
except Exception as e:
    print(e)
