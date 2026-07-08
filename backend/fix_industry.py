with open('main.py', 'r') as f:
    content = f.read()

import re
old_logic = '''    try:
        is_hk = (symbol.lower().startswith("hk")) or (symbol.isdigit() and len(symbol) == 5)
        if is_hk:
            # 港股暂时无法使用A股板块接口，从相关股票中提取均值代表板块热度
            related = get_related_stocks(symbol)
            related_data = related.get("data", [])
            total_flow = 0.0
            total_change = 0.0
            count = len(related_data)
            
            for r in related_data:
                flow_str = r.get("fundFlow", "")
                if "流入" in flow_str:
                    try:
                        val = float(flow_str.replace("净", "").replace("流入", "").replace("亿港元", "").replace("亿元", "").strip())
                        total_flow += val
                    except: pass
                elif "流出" in flow_str:
                    try:
                        val = float(flow_str.replace("净", "").replace("流出", "").replace("亿港元", "").replace("亿元", "").strip())
                        total_flow -= val
                    except: pass
                total_change += r.get("changePercent", 0.0)
            
            if count > 0:
                fallback_flow = round(total_flow, 2)
                sector_change = round(total_change / count, 2)
                fallback_heat = min(100, int(50 + (abs(fallback_flow) * 2) + sector_change * 3))
            found_real_data = True
        else:
            # 遍历板块所有分页获取真实数据 (大概5页)
            for pn in range(1, 6):
                url = f"http://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f8"
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
                if resp.status_code == 200:
                    data = resp.json().get("data")
                    if not data: continue
                    diff = data.get("diff", [])
                    if not diff: break
                    
                    for item in diff:
                        if item.get("f14", "") == industry_name:
                            # f62: 主力净流入 (元), f3: 涨幅, f8: 换手率(可作为热度参考)
                            flow = item.get("f62", 0) / 100000000.0
                            fallback_flow = round(flow, 2)
                            sector_change = float(item.get("f3", 0))
                            
                            # 根据换手率或者涨幅动态计算热度
                            turnover = float(item.get("f8", 5.0))
                            fallback_heat = min(100, int(50 + turnover * 3 + sector_change * 2))
                            
                            found_real_data = True
                            break
                if found_real_data:
                    break
    except Exception as e:
        print("Fetch sector flow error:", e)'''

new_logic = '''    try:
        # A股由于 push2 接口的 clist 限制，暂时无法直接抓取板块汇总。这里统一下游为根据同行计算
        related = get_related_stocks(symbol)
        related_data = related.get("data", [])
        total_flow = 0.0
        total_change = 0.0
        count = len(related_data)
        
        for r in related_data:
            flow_str = r.get("fundFlow", "")
            if "流入" in flow_str:
                try:
                    val = float(flow_str.replace("净", "").replace("流入", "").replace("亿港元", "").replace("亿元", "").strip())
                    total_flow += val
                except: pass
            elif "流出" in flow_str:
                try:
                    val = float(flow_str.replace("净", "").replace("流出", "").replace("亿港元", "").replace("亿元", "").strip())
                    total_flow -= val
                except: pass
            
            # 由于可能出现 changePercent 或者 oneDayChange
            cp = r.get("changePercent")
            if cp is None:
                cp = r.get("oneDayChange", 0.0)
            total_change += cp
        
        if count > 0:
            fallback_flow = round(total_flow, 2)
            sector_change = round(total_change / count, 2)
            # 热度估算
            fallback_heat = min(100, int(50 + (abs(fallback_flow) * 2) + sector_change * 3))
            
        found_real_data = True
    except Exception as e:
        print("Fetch sector flow error:", e)'''

if old_logic in content:
    with open('main.py', 'w') as f:
        f.write(content.replace(old_logic, new_logic))
    print("Replaced successfully")
else:
    print("old logic not found! Checking if spaces mismatch")
