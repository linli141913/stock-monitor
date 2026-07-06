import re

with open("backend/main.py", "r") as f:
    content = f.read()

new_func = """@app.get("/api/stock/abnormal_peers/{symbol}")
def get_abnormal_peers(symbol: str):
    peers = []
    base_codes = ["600584", "002049", "688012", "688981", "603501", "688008", "600460", "688126", "688037", "002371", "300661", "300373", "002156", "300671", "300077", "688120", "603986", "688099", "300223", "688018", "600584", "002049", "688012", "688981", "603501", "688008", "600460", "688126", "688037", "002371", "300661", "300373", "002156", "300671"]
    base_names = ["长电科技", "紫光国微", "中微公司", "中芯国际", "韦尔股份", "澜起科技", "士兰微", "沪硅产业", "芯源微", "北方华创", "圣邦股份", "扬杰科技", "通富微电", "富满微", "国民技术", "华海清科", "兆易创新", "太极实业", "北京君正", "睿创微纳", "长电科技2", "紫光国微2", "中微公司2", "中芯国际2", "韦尔股份2", "澜起科技2", "士兰微2", "沪硅产业2", "芯源微2", "北方华创2", "圣邦股份2", "扬杰科技2", "通富微电2", "富满微2"]
    
    for i in range(30):
        code = base_codes[i] if i < len(base_codes) else f"300{random.randint(100,999)}"
        name = base_names[i] if i < len(base_names) else f"芯片{i}股"
        peers.append({
            "stockCode": code,
            "stockName": name,
            "oneDayChange": round(random.uniform(-2, 10), 1),
            "fiveDayChange": round(random.uniform(5, 25), 1),
            "twentyDayChange": round(random.uniform(10, 50), 1),
            "volumeRatio": round(random.uniform(1.5, 4.0), 1),
            "turnoverRate": round(random.uniform(3.0, 15.0), 1),
            "fundFlow": f"净流入 {round(random.uniform(0.5, 5.0), 1)}亿元",
            "reason": random.choice(["半导体概念活跃，成交量放大", "受政策利好刺激，资金持续流入", "行业需求回暖，量价齐升"]),
            "riskNote": "短期涨幅较大，注意回撤风险" if i % 3 == 0 else ""
        })
    return {"data": peers}"""

# Find the old function and replace it
content = re.sub(r'@app\.get\("/api/stock/abnormal_peers/\{symbol\}"\).*?return \{"data": peers\}', new_func, content, flags=re.DOTALL)

with open("backend/main.py", "w") as f:
    f.write(content)
