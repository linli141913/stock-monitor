from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
import time
import hashlib
import database
from datetime import datetime

router = APIRouter(prefix="/api/semiconductor-news", tags=["News Radar"])

class RadarNews(BaseModel):
    id: str
    title: str
    source: str
    publish_time: str
    original_link: str
    credibility_level: str  # S, A, B, C
    credibility_method: str
    content_type: str
    region: str            # 国内, 国外
    related_chains: List[str]
    related_stocks: List[str]
    source_summary: str
    heuristic_impact: str
    impact_method: str
    verification_status: str

def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode('utf-8', errors='ignore')).hexdigest()

def classify_news_source(source: str) -> dict:
    """按来源名称做启发式分类，不代表事实核查或独立交叉验证。"""
    if source in ["巨潮公告", "上交所", "深交所", "上交所公告", "深交所公告", "北交所公告", "港交所公告", "交易所公告"]:
        credibility_level = "S"
        content_type = "official_announcement"
    elif "研报" in source or "证券" in source or "基金" in source:
        credibility_level = "A"
        content_type = "institution_research"
    elif source in ["新浪财经", "财联社电报", "市场资讯", "港股快讯"]:
        credibility_level = "B"
        content_type = "media_report"
    else:
        credibility_level = "C"
        content_type = "other"

    return {
        "credibility_level": credibility_level,
        "credibility_method": "source_rule",
        "content_type": content_type,
        "verification_status": "未独立交叉验证",
    }

def get_real_news_from_db(category: str) -> List[dict]:
    """从数据库读取真实抓取的去重新闻和公告，并转换为前端需要的 RadarNews 格式"""
    try:
        # 读取最多 120 条，供分类筛选
        news_items = database.get_latest_crawled_news("", limit=120)
        results = []
        for x in news_items:
            title = x.get("title", "")
            content = x.get("content", "") or ""
            source = x.get("source", "未知")
            url = x.get("url", "")
            ctime = x.get("ctime", time.time())
            symbol = x.get("symbol", "")
            
            # 格式化发布时间
            try:
                publish_time = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S")
            except:
                publish_time = "刚刚"
                
            source_classification = classify_news_source(source)
            cred = source_classification["credibility_level"]
                
            # 自动映射地域
            region_keywords = ["美国", "拜登", "特朗普", "西班牙", "美股", "美方", "荷兰", "阿斯麦", "ASML", "英伟达", "国外"]
            is_global = any(kw in title or kw in content for kw in region_keywords)
            region = "国外" if is_global else "国内"
            
            # 区分政策与行业动态的简易规则
            item_cat = "industry"
            policy_keywords = ["政策", "会议", "国务院", "发改委", "财政部", "税收", "央行", "监管", "证监会", "指导意见", "规划", "新规", "公告", "决定"]
            if any(k in title for k in policy_keywords):
                item_cat = "policy"
                
            # 各种分类过滤条件
            if category == "policies" and item_cat != "policy":
                continue
            if category == "domestic" and region != "国内":
                continue
            if category == "global" and region != "国外":
                continue
            if category == "company-events" and not symbol:
                continue
            if category == "export-control" and not any(kw in title or kw in content for kw in ["管制", "限制", "实体清单", "制裁", "禁令"]):
                continue
                
            # 提取相关产业链标签
            chains = []
            for kw, ch in [("设备", "半导体设备"), ("材料", "半导体材料"), ("封测", "先进封测"), ("设计", "IC设计"), ("硅片", "半导体材料"), ("晶圆", "晶圆代工"), ("公告", "公司披露"), ("研报", "行业研究")]:
                if kw in title or kw in content:
                    chains.append(ch)
            if not chains:
                chains = ["半导体核心"]
                
            # 关联个股
            stocks = []
            if symbol:
                stock_name = "京东方A" if symbol == "000725" else "中兵红箭" if symbol == "000519" else symbol
                stocks.append(stock_name)
                
            # 影响分析
            if cred == "S":
                impact = "来源规则识别为官方公告，可能影响公司预期，仍需结合公告原文判断具体影响。"
            elif cred == "A":
                impact = "来源规则识别为机构研报，其内容属于机构观点，不等同于已确认事实。"
            else:
                impact = "该资讯可能影响相关板块情绪，具体影响需结合原文及其他独立来源判断。"
                
            results.append({
                "id": x.get("id") or md5_hash(url),
                "title": title,
                "source": source,
                "publish_time": publish_time,
                "original_link": url,
                **source_classification,
                "region": region,
                "related_chains": chains[:3],
                "related_stocks": stocks,
                "source_summary": content if content else "暂无来源摘要，请查看原文链接",
                "heuristic_impact": impact,
                "impact_method": "heuristic",
            })
        return results
    except Exception as e:
        print(f"Error getting real news from DB: {e}")
        return []

def get_integrated_news(category: str) -> List[dict]:
    # 生产接口只返回真实抓取且可追溯的资讯；没有数据时返回空列表。
    return get_real_news_from_db(category)

@router.get("/latest", response_model=List[RadarNews])
def get_latest_news(category: str = "all"):
    return get_integrated_news(category)

@router.get("/domestic", response_model=List[RadarNews])
def get_domestic_news():
    return get_integrated_news("domestic")

@router.get("/global", response_model=List[RadarNews])
def get_global_news():
    return get_integrated_news("global")

@router.get("/policies", response_model=List[RadarNews])
def get_policies_news():
    return get_integrated_news("policies")

@router.get("/company-events", response_model=List[RadarNews])
def get_company_events_news():
    return get_integrated_news("company-events")

@router.get("/export-control", response_model=List[RadarNews])
def get_export_control_news():
    return get_integrated_news("export-control")
