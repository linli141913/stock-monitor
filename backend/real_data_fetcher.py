import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

import akshare as ak
import pandas as pd
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from datetime import datetime
from typing import Dict, Any
import asset_context
import market_calendar


def optional_number(value):
    if value is None or value == "" or value == "-" or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_optional_number(value, *, scale=1.0, suffix="", digits=2, comma=False):
    number = optional_number(value)
    if number is None:
        return "暂无数据"
    scaled = number / scale
    formatted = f"{scaled:,.{digits}f}" if comma else f"{scaled:.{digits}f}"
    return f"{formatted}{suffix}"


def normalize_market_timestamp(value: str):
    text = str(value or "").strip()
    if "/" in text and ":" in text:
        normalized = text.replace("/", "-")
    elif len(text) >= 14 and text[:14].isdigit():
        normalized = (
            f"{text[:4]}-{text[4:6]}-{text[6:8]} "
            f"{text[8:10]}:{text[10:12]}:{text[12:14]}"
        )
    else:
        return None
    try:
        datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return normalized

class RealDataFetcher:
    def __init__(self):
        pass

    def get_stock_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch basic stock info and latest daily quote (using Tencent API directly)."""
        try:
            import requests
            code = asset_context.normalize_symbol(symbol)
            prefix = asset_context.quote_prefix(symbol)
            url = f"http://qt.gtimg.cn/q={prefix}{code}"
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                resp.encoding = "gbk"
                data = resp.text
                if len(data) > 30:
                    v = data.split('~')
                    source_time = normalize_market_timestamp(v[30] if len(v) > 30 else "")
                    return {
                        "name": v[1],
                        "price": optional_number(v[3] if len(v) > 3 else None),
                        "change_pct": optional_number(v[32] if len(v) > 32 else None),
                        "volume_ratio": optional_number(v[49] if len(v) > 49 else None),
                        "turnover_rate": optional_number(v[38] if len(v) > 38 else None),
                        "source_time": source_time,
                        "source_date": source_time[:10] if source_time else None,
                        "fetched_at": datetime.now(
                            market_calendar.SHANGHAI_TZ
                        ).isoformat(timespec="seconds"),
                    }
        except Exception as e:
            print(f"Error fetching quote for {symbol}: {e}")
        
        return {
            "name": symbol,
            "price": None,
            "change_pct": None,
            "volume_ratio": None,
            "turnover_rate": None,
            "source_time": None,
            "source_date": None,
            "fetched_at": datetime.now(
                market_calendar.SHANGHAI_TZ
            ).isoformat(timespec="seconds"),
        }

    def get_stock_news(self, symbol: str) -> str:
        """Fetch latest news for a specific stock."""
        try:
            news_df = ak.stock_news_em(symbol=symbol)
            if not news_df.empty:
                today = datetime.now(market_calendar.SHANGHAI_TZ).date()
                current_items = []
                for _, row in news_df.iterrows():
                    published = pd.to_datetime(row.get('发布时间'), errors='coerce')
                    if pd.isna(published) or published.date() != today:
                        continue
                    current_items.append((published, row))
                current_items.sort(key=lambda item: item[0], reverse=True)
                news_items = []
                for _, row in current_items[:3]:
                    title = row.get('新闻标题', '')
                    published_text = row.get('发布时间', '')
                    source = row.get('文章来源', '东方财富网')
                    url = row.get('新闻链接', '')
                    news_items.append(
                        f"【{published_text}】{title} (来源: {source}, 链接: {url})"
                    )
                if news_items:
                    return "\n".join(news_items)
                return "今日暂无可追溯的公司新闻。"
        except Exception as e:
            print(f"Error fetching news for {symbol}: {e}")
            return "公司资讯获取失败。"

        return "今日暂无可追溯的公司新闻。"

    def get_macro_environment(self, context: Dict[str, Any] = None) -> str:
        """只在存在明确半导体映射时读取半导体海外基准，其他行业不强行关联。"""
        context = context or {}
        terms = context.get("search_terms") or []
        if not any(term in {"半导体", "芯片", "集成电路"} for term in terms):
            industry_name = context.get("industry_name") or "当前行业"
            return f"{industry_name}暂未配置可追溯的专属海外基准，暂无法确认跨市场映射。"
        try:
            import requests
            url = "https://hq.sinajs.cn/list=gb_soxx,gb_nvda,gb_amd,gb_intc,gb_tsm,int_ndaq"
            headers = {
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0"
            }
            resp = requests.get(url, headers=headers, timeout=5)
            resp.encoding = "gbk"
            raw = resp.text

            def parse_sina_us(line: str):
                """解析新浪美股行情行"""
                try:
                    fields = line.split('"')[1].split(",")
                    name = fields[0]
                    price = float(fields[1]) if fields[1] else 0.0
                    change_pct = float(fields[2]) if fields[2] else 0.0
                    source_update = fields[3] if len(fields) > 3 else ""
                    market_time = fields[26] if len(fields) > 26 else ""
                    return name, price, change_pct, source_update, market_time
                except Exception:
                    return None, None, None, "", ""

            lines = [l.strip() for l in raw.strip().split("\n") if "hq_str_" in l]
            results = []
            for line in lines:
                name, price, change_pct, source_update, market_time = parse_sina_us(line)
                if name and price is not None:
                    direction = "▲" if change_pct >= 0 else "▼"
                    sign = "+" if change_pct >= 0 else ""
                    results.append(
                        f"{name} {direction}{sign}{change_pct:.2f}%（现价 {price}；"
                        f"数据源更新：{source_update or '暂无法确认'}；"
                        f"市场时点：{market_time or '暂无法确认'}）"
                    )

            if results:
                summary = "【海外市场最近交易快照】\n" + "\n".join(results)
                return summary
        except Exception as e:
            print(f"Error fetching macro environment: {e}")

        # 降级兜底
        return "海外半导体：费城半导体指数与英伟达等核心标的数据获取失败，请检查网络。"


    def get_industry_news_dehydrated(
        self,
        symbol: str,
        industry_name: str = "",
        search_terms=None,
    ) -> str:
        """Fetch global news, filter by industry keywords, deduplicate to top 20."""
        try:
            import requests
            url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=150&page=1"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=5)
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            
            industry_keywords = [
                str(term).strip()
                for term in [industry_name, *(search_terms or [])]
                if len(str(term).strip()) >= 2
                and "待确认" not in str(term)
            ]
                
            filtered_items = []
            seen_titles = set()
            today = datetime.now(market_calendar.SHANGHAI_TZ).date()
            
            for item in items:
                title = item.get("title", "")
                url = item.get("url", "")
                ctime = item.get("ctime", "")
                try:
                    published = datetime.fromtimestamp(
                        int(ctime),
                        market_calendar.SHANGHAI_TZ,
                    )
                except (TypeError, ValueError, OSError):
                    continue
                if published.date() != today:
                    continue
                
                match = bool(industry_keywords) and any(
                    kw.lower() in title.lower()
                    for kw in industry_keywords
                )
                if match:
                    if title not in seen_titles:
                        seen_titles.add(title)
                        time_str = published.strftime('%m-%d %H:%M')
                        filtered_items.append(f"【{time_str}】{title} <br/>[来源: 新浪财经]({url})")
                        
                if len(filtered_items) >= 20:
                    break
                    
            if not filtered_items:
                return "今日暂无高价值行业脱水事件。"
                
            return "\n".join(filtered_items)
        except Exception as e:
            print(f"Error fetching industry news: {e}")
            return "行业资讯获取失败。"

    def get_finance_summary(self, symbol: str, stock_name: str = "") -> str:
        """Fetch real quarterly finance core indices from EastMoney/EMWeb."""
        try:
            import requests
            context = asset_context.build_asset_context(symbol, stock_name)
            if context["asset_type"] == "domestic_etf":
                return "国内ETF不适用上市公司营收、净利润和ROE口径；应关注跟踪指数、基金公告、份额与成交变化。"
            code = context["symbol"]
            secucode = symbol
            if not ("." in symbol):
                market_suffix = {
                    "sh": "SH",
                    "sz": "SZ",
                    "bj": "BJ",
                }.get(context["quote_prefix"], "SZ")
                secucode = f"{code}.{market_suffix}"

            is_hk = context["asset_type"] == "hk_stock"
            if is_hk:
                import akshare as ak
                symbol_pure = code
                df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_pure)
                if df.empty:
                    return "暂无最新财报财务核心数据。"
                reports = df.to_dict('records')
                summary_lines = []
                for r in reports[:3]:
                    date_str = str(r.get("REPORT_DATE", ""))[:10]
                    summary_lines.append(
                        f"报告期: {date_str}, "
                        f"营收: {format_optional_number(r.get('OPERATE_INCOME'), suffix='元', digits=0, comma=True)} "
                        f"(同比 {format_optional_number(r.get('OPERATE_INCOME_YOY'), suffix='%')}), "
                        f"归母净利润: {format_optional_number(r.get('HOLDER_PROFIT'), suffix='元', digits=0, comma=True)} "
                        f"(同比 {format_optional_number(r.get('HOLDER_PROFIT_YOY'), suffix='%')}), "
                        f"销售毛利率: {format_optional_number(r.get('GROSS_PROFIT_RATIO'), suffix='%')}, "
                        f"ROE: {format_optional_number(r.get('ROE_YEARLY'), suffix='%')}"
                    )
                return "\n".join(summary_lines)
            else:
                url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={secucode}"
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
                data = resp.json().get("data", [])
                if not data:
                    return "暂无最新财报财务核心数据。"
                
                summary_lines = []
                for r in data[:3]:
                    date_name = r.get("REPORT_DATE_NAME", "")
                    summary_lines.append(
                        f"报告期: {date_name}, "
                        f"营收: {format_optional_number(r.get('TOTALOPERATEREVE'), scale=100000000.0, suffix='亿元')} "
                        f"(同比 {format_optional_number(r.get('TOTALOPERATEREVETZ'), suffix='%')}), "
                        f"归母净利润: {format_optional_number(r.get('PARENTNETPROFIT'), scale=100000000.0, suffix='亿元')} "
                        f"(同比 {format_optional_number(r.get('PARENTNETPROFITTZ'), suffix='%')}), "
                        f"销售毛利率: {format_optional_number(r.get('XSMLL'), suffix='%')}, "
                        f"ROE: {format_optional_number(r.get('ROEJQ'), suffix='%')}"
                    )
                return "\n".join(summary_lines)
        except Exception as e:
            print(f"Error fetching finance summary: {e}")
            return "财报核心数据获取失败。"
