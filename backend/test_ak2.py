import akshare as ak
try:
    df = ak.stock_individual_fund_flow_rank(indicator="今日")
    # df has symbol, name, and today net inflow
    # "代码", "今日-主力净流入-净额"
    row = df[df["代码"] == "000021"]
    print(row)
except Exception as e:
    print(e)
