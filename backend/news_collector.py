import os
import time
import hashlib
import requests
import io
from datetime import datetime
import pypdf
import database
import akshare as ak

# 强制禁用代理参数，以防影响内网连接，如果透明代理会自动接管
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode('utf-8', errors='ignore')).hexdigest()

def clean_html(text: str) -> str:
    """去除简易的 HTML 标签"""
    import re
    return re.sub(r'<[^>]+>', '', text).strip()

def fetch_sina_roll_news() -> list:
    """从新浪财经滚动接口抓取最新行业和政策资讯"""
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=80&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    results = []
    try:
        time.sleep(1.5)  # 强制限速
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"Error fetching Sina roll news: HTTP {resp.status_code}")
            return []
            
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        for item in items:
            title = item.get("title", "")
            link = item.get("url", "")
            intro = item.get("intro", "") or item.get("summary", "") or ""
            ctime_raw = item.get("ctime")
            media_name = item.get("media_name", "新浪财经")
            
            if "财联社" in media_name or "财联社" in title:
                media_name = "财联社电报"
                
            if not title or not link:
                continue
                
            # 区分政策与行业动态的简易规则
            category = "industry"
            policy_keywords = ["政策", "会议", "国务院", "发改委", "财政部", "税收", "央行", "监管", "证监会", "指导意见", "规划", "新规"]
            if any(k in title for k in policy_keywords):
                category = "policy"
                
            ctime = int(ctime_raw) if ctime_raw else int(time.time())
            
            results.append({
                "id": md5_hash(link),
                "symbol": "",
                "title": title,
                "url": link,
                "ctime": ctime,
                "source": media_name,
                "content": clean_html(intro)[:200],
                "category": category
            })
            
    except Exception as e:
        print(f"Error parsing Sina roll news: {e}")
        
    return results

def fetch_eastmoney_reports() -> list:
    """利用 AkShare 抓取东方财富最新的研报并入库"""
    results = []
    try:
        time.sleep(1.5)  # 强制限速
        df = ak.stock_research_report_em()
        if df.empty:
            return []
            
        # 提取前 40 条最新研报即可
        records = df.head(40).to_dict("records")
        for r in records:
            title = f"{r.get('股票简称')}研报：{r.get('报告名称')}"
            link = r.get("报告PDF链接", "")
            agency = r.get("机构", "东财研报")
            date_raw = r.get("日期")  # datetime.date 对象
            symbol = r.get("股票代码", "")
            
            if not title or not link:
                continue
                
            ctime = int(time.time())
            if date_raw:
                try:
                    dt = datetime.combine(date_raw, datetime.min.time())
                    ctime = int(dt.timestamp())
                except:
                    pass
                    
            results.append({
                "id": md5_hash(link),
                "symbol": symbol,
                "title": title,
                "url": link,
                "ctime": ctime,
                "source": agency,
                "content": f"评级：{r.get('东财评级')} | 行业分类：{r.get('行业')} | 目标盈利：2026年盈利预测 {r.get('2026-盈利预测-收益')} 元",
                "category": "industry"
            })
    except Exception as e:
        print(f"Error fetching EastMoney reports: {e}")
        
    return results

def summarize_pdf_text_with_llm(text: str) -> str:
    """利用轻量级 LLM 将 PDF 提取的前 1000 字总结为 150 字内的核心大意"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return text[:150] + "..."
        
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
        model_name = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        prompt = f"""
        请将以下从公司官方 PDF 公告中提取的前段文字，精简并总结为一段 80 到 150 字之内的“核心公告要点摘要”，去除多余的公文客套话，一针见血说明公司做了什么事以及核心指标数字：
        
        【PDF 文本】
        {text}
        """
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个只返回纯净、精炼、无废话的一句话公告要点总结助手。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=200
        )
        summary = response.choices[0].message.content.strip()
        return summary
    except Exception as e:
        print(f"LLM PDF summary failed: {e}")
        return text[:150] + "..."

def parse_cninfo_pdf(pdf_url: str) -> str:
    """下载并解析巨潮公告 PDF 核心文字"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        time.sleep(1.5)  # 强制限速
        resp = requests.get(pdf_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return "PDF 下载失败"
            
        bytes_io = io.BytesIO(resp.content)
        reader = pypdf.PdfReader(bytes_io)
        
        extracted_text = ""
        # 仅读取前 3 页防内存爆炸
        for page in reader.pages[:3]:
            extracted_text += page.extract_text() or ""
            
        cleaned_text = " ".join(extracted_text.split())[:1200]
        if not cleaned_text.strip():
            return "公告格式为纯图片或空文件"
            
        return summarize_pdf_text_with_llm(cleaned_text)
    except Exception as e:
        print(f"Error parsing PDF {pdf_url}: {e}")
        return "公告 PDF 解析异常"

def fetch_cninfo_announcements(search_key: str, symbol_rel: str = "") -> list:
    """从巨潮资讯接口抓取公告并解析 PDF"""
    url = 'http://www.cninfo.com.cn/new/hisAnnouncement/query'
    payload = {
        'pageNum': 1,
        'pageSize': 15,
        'tabName': 'fulltext',
        'column': 'szse',
        'stock': '',
        'searchkey': search_key,
        'secid': '',
        'category': '',
        'trade': '',
        'sortName': '',
        'sortType': '',
        'isInit': 'true'
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    results = []
    try:
        time.sleep(1.5)  # 强制限速
        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"Error fetching Cninfo announcements: HTTP {resp.status_code}")
            return []
            
        announcements = resp.json().get('announcements', [])
        if not announcements:
            return []
            
        for a in announcements:
            title = a.get('announcementTitle', '')
            adjunct_url = a.get('adjunctUrl', '')
            ann_time_ms = a.get('announcementTime')
            sec_code = a.get('secCode', '')
            
            if not title or not adjunct_url:
                continue
                
            pdf_link = f"http://static.cninfo.com.cn/{adjunct_url}"
            unique_id = md5_hash(pdf_link)
            
            # 转换毫秒时间戳为秒
            ctime = int(ann_time_ms / 1000) if ann_time_ms else int(time.time())
            
            # 判断是否已经爬取过，防止重复下载 PDF 和调用 LLM
            if exists_in_database(unique_id):
                continue
                
            print(f"Found new Cninfo Announcement: {title}, downloading PDF...")
            content_summary = parse_cninfo_pdf(pdf_link)
            
            # Determine source based on exchange
            ann_source = "巨潮公告"
            if sec_code:
                if sec_code.startswith(('60', '68', '90', '7')):
                    ann_source = "上交所公告"
                elif sec_code.startswith(('00', '30', '20')):
                    ann_source = "深交所公告"
                elif sec_code.startswith(('43', '83', '87', '88')):
                    ann_source = "北交所公告"
            
            results.append({
                "id": unique_id,
                "symbol": symbol_rel or sec_code,
                "title": title,
                "url": pdf_link,
                "ctime": ctime,
                "source": ann_source,
                "content": content_summary,
                "category": "policy" if "公告" in title or "决定" in title else "industry"
            })
            
    except Exception as e:
        print(f"Error parsing Cninfo announcements: {e}")
        
    return results

def exists_in_database(news_id: str) -> bool:
    """辅助判断是否已经存在该新闻，免去重复解析 PDF"""
    import sqlite3
    try:
        conn = sqlite3.connect(database.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM crawled_news WHERE id=?", (news_id,))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except:
        return False

def fetch_sina_hk_news() -> list:
    """从新浪财经滚动接口抓取最新港股快讯和交易所公告"""
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=50&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn/"
    }
    results = []
    try:
        time.sleep(1.5)  # 强制限速
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"Error fetching Sina HK news: HTTP {resp.status_code}")
            return []
            
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        for item in items:
            title = item.get("title", "")
            link = item.get("url", "")
            intro = item.get("intro", "") or item.get("summary", "") or ""
            ctime_raw = item.get("ctime")
            media_name = item.get("media_name", "港股快讯")
            
            if not title or not link:
                continue
                
            # 如果标题或媒体名包含特定公告或增减持、派息关键字，打标为“港交所公告”
            source = media_name
            if any(k in title for k in ["公告", "业绩预告", "分红", "派息", "除权", "增持", "减持", "招股", "董事会", "股份购买"]):
                source = "港交所公告"
            elif source == "新浪财经" or source == "港股快讯" or source == "新浪港股":
                source = "港股快讯"
                
            ctime = int(ctime_raw) if ctime_raw else int(time.time())
            
            results.append({
                "id": md5_hash(link),
                "symbol": "",
                "title": title,
                "url": link,
                "ctime": ctime,
                "source": source,
                "content": clean_html(intro)[:200],
                "category": "industry"
            })
            
    except Exception as e:
        print(f"Error parsing Sina HK news: {e}")
        
    return results

def run_collection():
    """多源资讯采集总调度口"""
    print(f"[{datetime.now().isoformat()}] Starting news collection pipeline...")
    
    aggregated = []
    
    # 1. 抓取新浪滚动快讯
    print("Fetching Sina Roll News...")
    aggregated.extend(fetch_sina_roll_news())
    
    # 2. 抓取东方财富研报
    print("Fetching EastMoney Research Reports...")
    aggregated.extend(fetch_eastmoney_reports())
    
    # 3. 抓取巨潮个股及行业公告
    print("Fetching Cninfo: 行业公告关键词 (半导体)...")
    aggregated.extend(fetch_cninfo_announcements("半导体"))
    
    watchlist = database.get_watchlist()
    for item in watchlist:
        code = item.get("stockCode")
        name = item.get("stockName")
        if code:
            # A股公告抓取 (代码以 0, 3, 6, 8 等开头且长度为6)
            if len(code) == 6:
                print(f"Fetching Cninfo: 个股公告 ({name} / {code})...")
                aggregated.extend(fetch_cninfo_announcements(code, code))
            
    # 4. 抓取港股及港交所资讯
    print("Fetching Sina HK Stock News...")
    hk_news = fetch_sina_hk_news()
    
    # 关联港股自选股 (自选股代码长度为 5，如 00700)
    for item in hk_news:
        for w_item in watchlist:
            code = w_item.get("stockCode", "")
            name = w_item.get("stockName", "")
            if code and len(code) == 5 and name:
                # 模糊名字匹配 (例如 "腾讯" 匹配 "腾讯控股股价...")
                short_name = name.replace("A", "").replace("B", "").replace("H", "").replace(" ", "")
                if len(short_name) >= 2 and short_name[:2] in item["title"]:
                    item["symbol"] = code
                    print(f"Mapped HK news to watchlist item: {name} ({code}) -> {item['title']}")
                    break
    aggregated.extend(hk_news)
            
    # 5. 入库保存
    if aggregated:
        print(f"Saving {len(aggregated)} aggregated news items to SQLite database...")
        database.save_crawled_news(aggregated)
        print("News collection pipeline completed successfully.")
    else:
        print("No new news items discovered.")

if __name__ == "__main__":
    run_collection()
