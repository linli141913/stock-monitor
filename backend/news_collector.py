import os
import time
import hashlib
import requests
import io
from datetime import datetime
import pypdf
import asset_context
import database
import market_calendar
import notification_service
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


def post_cninfo(url: str, **kwargs):
    """巨潮接口必须直连，避免继承本机失效代理。"""
    session = requests.Session()
    session.trust_env = False
    return session.post(url, **kwargs)

def fetch_sina_roll_news() -> list:
    """从新浪财经滚动接口抓取最新行业和政策资讯"""
    url = "https://feed.mix.sina.com.cn/api/roll/get"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    results = []
    seen_links = set()
    try:
        for page in (1, 2):
            time.sleep(1.5)  # 强制限速
            resp = requests.get(
                url,
                params={"pageid": 153, "lid": 2509, "num": 50, "page": page},
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"Error fetching Sina roll news page {page}: HTTP {resp.status_code}")
                continue

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

                if not title or not link or link in seen_links:
                    continue
                seen_links.add(link)

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
        resp = post_cninfo(url, data=payload, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"Error fetching Cninfo announcements: HTTP {resp.status_code}")
            return []
            
        announcements = resp.json().get('announcements', [])
        if not announcements:
            return []
            
        target_code = symbol_rel.lower()
        if target_code.startswith(("sh", "sz", "bj")):
            target_code = target_code[2:]

        for a in announcements:
            title = a.get('announcementTitle', '')
            adjunct_url = a.get('adjunctUrl', '')
            ann_time_ms = a.get('announcementTime')
            sec_code = str(a.get('secCode', '') or '').strip()

            if target_code and sec_code != target_code:
                continue
            
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
                "symbol": sec_code,
                "title": title,
                "url": pdf_link,
                "ctime": ctime,
                "source": ann_source,
                "content": content_summary,
                "category": "company"
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
    """从新浪财经滚动接口抓取最新港股媒体快讯。"""
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
                
            source = media_name
            if source in {"新浪财经", "港股快讯", "新浪港股"}:
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


def _parse_source_time(value) -> int:
    if value is None or value == "":
        return 0
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().split(".")[0]
        text = text.rsplit(":", 1)[0] if text.count(":") == 3 else text
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=market_calendar.SHANGHAI_TZ)
    return int(parsed.timestamp())


def _scoped_asset_url(url: str, symbol: str) -> str:
    return f"{str(url).split('#', 1)[0]}#asset={asset_context.normalize_symbol(symbol)}"


def fetch_market_disclosures() -> list:
    """抓取全市场最新 A 股和港股公告汇总，不受自选列表限制。"""
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    results = []
    for market, ann_type in (("cn", "A"), ("hk", "H")):
        params = {
            "sr": "-1",
            "page_size": 100,
            "page_index": 1,
            "ann_type": ann_type,
            "client_source": "web",
        }
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            rows = response.json().get("data", {}).get("list", [])
        except Exception as exc:
            print(f"Error fetching {market} market disclosures: {exc}")
            continue

        for row in rows:
            art_code = str(row.get("art_code") or "").strip()
            title = str(row.get("title") or "").strip()
            code_items = row.get("codes") or []
            raw_code = str(code_items[0].get("stock_code") or "").strip() if code_items else ""
            code = raw_code.zfill(5 if market == "hk" else 6)
            if not art_code or not title or not raw_code:
                continue
            detail_url = f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
            results.append({
                "id": f"{market}-{art_code}",
                "symbol": code,
                "stock_name": str(code_items[0].get("short_name") or "").strip(),
                "title": title,
                "url": detail_url,
                "ctime": _parse_source_time(row.get("display_time")),
                "source": "东方财富公告汇总",
                "content": title,
                "category": "company",
                "market": market,
                "asset_type": "hk_stock" if market == "hk" else "a_stock",
            })
    return results


def fetch_watchlist_asset_news(item: dict) -> list:
    """按证券代码精确查询资讯，适用于 A 股、港股和国内 ETF。"""
    code = asset_context.normalize_symbol(item.get("stockCode", ""))
    name = str(item.get("stockName") or "").strip()
    if not code:
        return []
    context = asset_context.resolve_asset_context(code, name)
    try:
        data = ak.stock_news_em(symbol=code)
    except Exception as exc:
        print(f"Error fetching asset news for {code}: {exc}")
        return []

    results = []
    for _, row in data.head(20).iterrows():
        title = str(row.get("新闻标题") or "").strip()
        url = str(row.get("新闻链接") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        scoped_url = _scoped_asset_url(url, code)
        results.append({
            "id": md5_hash(scoped_url),
            "symbol": code,
            "stock_name": name,
            "title": title,
            "url": scoped_url,
            "ctime": _parse_source_time(row.get("发布时间")),
            "source": str(row.get("文章来源") or "东方财富").strip(),
            "content": clean_html(str(row.get("新闻内容") or ""))[:500],
            "category": "industry",
            "asset_type": context["asset_type"],
        })
    return results


def fetch_hk_disclosures(symbol: str, stock_name: str = "") -> list:
    """读取按港股代码精确匹配的公告汇总；不冒充港交所原始链接。"""
    code = asset_context.normalize_symbol(symbol)
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": "-1",
        "page_size": 10,
        "page_index": 1,
        "ann_type": "H",
        "client_source": "web",
        "stock_list": code,
    }
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json().get("data", {}).get("list", [])
    except Exception as exc:
        print(f"Error fetching HK disclosures for {code}: {exc}")
        return []

    results = []
    for row in rows:
        codes = {
            str(code_item.get("stock_code") or "").zfill(5)
            for code_item in row.get("codes") or []
        }
        art_code = str(row.get("art_code") or "").strip()
        title = str(row.get("title") or "").strip()
        if code not in codes or not art_code or not title:
            continue
        detail_url = f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
        results.append({
            "id": art_code,
            "symbol": code,
            "stock_name": stock_name,
            "title": title,
            "url": detail_url,
            "ctime": _parse_source_time(row.get("display_time")),
            "source": "东方财富公告汇总",
            "content": title,
            "category": "company",
            "asset_type": "hk_stock",
        })
    return results


def fetch_etf_disclosures(symbol: str, stock_name: str = "") -> list:
    """读取国内 ETF 的基金公告汇总，保留日期精度和可追溯详情页。"""
    code = asset_context.normalize_symbol(symbol)
    url = "https://api.fund.eastmoney.com/f10/JJGG"
    try:
        response = requests.get(
            url,
            params={
                "fundcode": code,
                "pageIndex": 1,
                "pageSize": 10,
                "type": "0",
                "_": int(time.time() * 1000),
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://fundf10.eastmoney.com/jjgg_{code}.html",
            },
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json().get("Data") or []
    except Exception as exc:
        print(f"Error fetching ETF disclosures for {code}: {exc}")
        return []

    results = []
    for row in rows:
        fund_code = str(row.get("FUNDCODE") or "").strip()
        art_code = str(row.get("ID") or "").strip()
        title = str(row.get("TITLE") or "").strip()
        if fund_code != code or not art_code or not title:
            continue
        detail_url = f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
        results.append({
            "id": art_code,
            "symbol": code,
            "stock_name": stock_name or str(row.get("ShortTitle") or "").strip(),
            "title": title,
            "url": detail_url,
            "ctime": _parse_source_time(row.get("PUBLISHDATE")),
            "source": "天天基金公告",
            "content": title,
            "category": "company",
            "asset_type": "domestic_etf",
        })
    return results


def collect_watchlist_official_news() -> list:
    results = []
    for item in database.get_watchlist():
        code = asset_context.normalize_symbol(item.get("stockCode", ""))
        name = str(item.get("stockName") or "").strip()
        context = asset_context.build_asset_context(code, name)
        if context["asset_type"] == "hk_stock":
            results.extend(fetch_hk_disclosures(code, name))
        elif context["asset_type"] == "domestic_etf":
            results.extend(fetch_etf_disclosures(code, name))
        elif len(code) == 6 and code.isdigit():
            results.extend(fetch_cninfo_announcements(code, code))
    return results


def save_and_process_news(news_items: list) -> int:
    if not news_items:
        return 0
    database.save_crawled_news(news_items)
    return notification_service.process_official_news(news_items)


def run_official_collection() -> int:
    print(f"[{datetime.now().isoformat()}] Starting watchlist official alert collection...")
    official_items = collect_watchlist_official_news()
    created_count = save_and_process_news(official_items)
    print(
        f"Official alert collection completed: {len(official_items)} items, "
        f"{created_count} new alerts."
    )
    return created_count

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
    aggregated.extend(fetch_market_disclosures())
    
    # 3. 按监测列表资产代码精确抓取公司/ETF定向资讯和公告
    watchlist = database.get_watchlist()
    for item in watchlist:
        aggregated.extend(fetch_watchlist_asset_news(item))
    aggregated.extend(collect_watchlist_official_news())
            
    # 4. 抓取港股及港交所资讯
    print("Fetching Sina HK Stock News...")
    hk_news = fetch_sina_hk_news()
    
    aggregated.extend(hk_news)
            
    # 5. 入库保存
    if aggregated:
        print(f"Saving {len(aggregated)} aggregated news items to SQLite database...")
        created_alerts = save_and_process_news(aggregated)
        print(f"Created {created_alerts} new official alerts.")
        print("News collection pipeline completed successfully.")
    else:
        print("No new news items discovered.")
    return len(aggregated)

if __name__ == "__main__":
    run_collection()
