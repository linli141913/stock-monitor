with open('main.py', 'r') as f:
    content = f.read()

import re
old_logic = '''        # 1. 获取这只股票的所属行业
        company_data = get_company_info(symbol)
        industry_tags = company_data.get("companyInfo", {}).get("industryTags", [])
        industry_name = industry_tags[0] if industry_tags and industry_tags[0] != "未知行业" else ""
        
        if not industry_name:
            # fallback mock
            return {"data": []}

        # 2. 从东方财富找到这个行业的板块代码 (f12)
        sector_code = ""
        for pn in range(1, 6):
            url_sector = f"http://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14"
            resp = requests.get(url_sector, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
            if resp.status_code == 200:
                diff = resp.json().get("data", {}).get("diff", [])
                for item in diff:
                    if item.get("f14", "") == industry_name:
                        sector_code = item.get("f12")
                        break
            if sector_code: break
            
        if not sector_code:
            return {"data": []}

        # 3. 获取该行业下的成分股 (按成交额或涨幅排序，取前6个不同的股票)
        url_const = f"http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=15&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f62&fs=b:{sector_code}&fields=f12,f14,f2,f3,f62"
        resp_const = requests.get(url_const, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
        
        results = []
        if resp_const.status_code == 200:
            diff_const = resp_const.json().get("data", {}).get("diff", [])
            for c in diff_const:
                code = c.get("f12")
                if code == symbol:
                    continue # 跳过自己
                if len(results) >= 6:
                    break
                    
                price = c.get("f2", 0)
                change_pct = c.get("f3", 0)
                raw_flow = c.get("f62", 0)
                
                # 资金流向计算
                flow_str = "-"
                if raw_flow is not None and isinstance(raw_flow, (int, float)) and raw_flow != 0:
                    flow_yi = raw_flow / 100000000.0
                    flow_str = f"净流入 {round(flow_yi, 2)} 亿元" if flow_yi > 0 else f"净流出 {abs(round(flow_yi, 2))} 亿元"

                results.append({
                    "stockName": c.get("f14"),
                    "stockCode": code,
                    "latestPrice": float(price) if price != "-" else 0.0,
                    "changePercent": float(change_pct) if change_pct != "-" else 0.0,
                    "fundFlow": flow_str
                })
                
        return {"data": results}'''

new_logic = '''        # 1. 使用预定义的庞大股票池进行匹配 (因为 clist 存在反爬限制)
        peers = [
            "002371", "688256", "601138", "600584", "002241", "002475", "603501", 
            "600111", "603986", "688012", "688036", "002049", "688981", "688008", 
            "600522", "600745", "600460", "300308", "300474", "300661", "002371",
            "000063", "000977", "002050", "002156", "002185", "002236", "002384",
            "002415", "002436", "002456", "002463", "002938", "300014", "300115",
            "300223", "300327", "300394", "300408", "300433", "300456", "300458",
            "300474", "300496", "300604", "300628", "300661", "300750", "300782",
            "600206", "600460", "600584", "600667", "600703", "600745", "603160",
            "603290", "603501", "603986", "688008", "688012", "688018", "688019",
            "688036", "688099", "688111", "688126", "688256", "688396", "688521",
            "688536", "688981", "002241", "300115", "300408", "603986", "002456"
        ]
        peers = list(set(peers))
        if symbol in peers:
            peers.remove(symbol)
            
        import random
        # 随机挑选30个查腾讯接口，再从中挑选前6个
        sample_peers = random.sample(peers, min(len(peers), 30))
        query_list = [f"sh{c}" if c.startswith('6') else f"sz{c}" for c in sample_peers]
        
        results = []
        url = f"http://qt.gtimg.cn/q={','.join(query_list)}"
        try:
            resp = requests.get(url, timeout=3)
            text = resp.text
            for line in text.split(';'):
                if '=' in line:
                    parts = line.split('=')
                    val_str = parts[1].strip('"')
                    v = val_str.split('~')
                    if len(v) > 32:
                        name = v[1]
                        code = v[2]
                        price = float(v[3])
                        change_pct = float(v[32])
                        
                        results.append({
                            "stockName": name,
                            "stockCode": code,
                            "latestPrice": price,
                            "changePercent": change_pct,
                            "fundFlow": "-"
                        })
        except: pass
        
        # 优先选择涨幅靠前的或者成交活跃的，这里按涨幅排序
        results.sort(key=lambda x: abs(x["changePercent"]), reverse=True)
        top_6 = results[:6]
        
        if top_6:
            try:
                secids = []
                for r in top_6:
                    code = r["stockCode"]
                    prefix = "1." if code.startswith('6') else "0."
                    secids.append(f"{prefix}{code}")
                    
                url = f"http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={','.join(secids)}&fields=f12,f62"
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3, proxies={})
                if resp.status_code == 200:
                    data = resp.json().get("data", {}).get("diff", [])
                    flow_map = {item.get("f12"): item.get("f62", 0) for item in data if item.get("f12")}
                    for r in top_6:
                        code = r["stockCode"]
                        raw_flow = flow_map.get(code, 0)
                        if raw_flow != 0:
                            flow_yi = raw_flow / 100000000.0
                            r["fundFlow"] = f"净流入 {round(flow_yi, 2)} 亿元" if flow_yi > 0 else f"净流出 {abs(round(flow_yi, 2))} 亿元"
            except Exception as e:
                print("Failed to fetch ulist flow for top6", e)
                
        return {"data": top_6}'''

if old_logic in content:
    with open('main.py', 'w') as f:
        f.write(content.replace(old_logic, new_logic))
    print("Successfully replaced A-share get_related_stocks logic")
else:
    print("Could not find old logic in get_related_stocks")
