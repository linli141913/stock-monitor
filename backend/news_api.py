from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import List, Optional
import time
import random

router = APIRouter(prefix="/api/semiconductor-news", tags=["News Radar"])

class RadarNews(BaseModel):
    id: str
    title: str
    source: str
    publish_time: str
    original_link: str
    credibility_level: str  # S, A, B, C
    region: str            # 国内, 国外
    related_chains: List[str]
    related_stocks: List[str]
    ai_summary: str
    ai_impact: str
    ai_verification_status: str

# Mock data generator that mimics AKShare + AI Verification process
def generate_mock_news(category: str) -> List[dict]:
    # In a real scenario, this would call akshare.stock_news_em(symbol="半导体") 
    # and then pass the results through an LLM for classification and extraction.
    
    base_news = [
        {
            "id": "mock_1",
            "title": "工信部：加快推进半导体关键材料与设备国产替代化进程",
            "source": "工信部官方网站",
            "publish_time": "10分钟前",
            "original_link": "https://www.miit.gov.cn/",
            "credibility_level": "S",
            "region": "国内",
            "related_chains": ["半导体设备", "半导体材料"],
            "related_stocks": ["北方华创", "中微公司", "沪硅产业"],
            "ai_summary": "官方明确指出要加速半导体上游材料和设备的国产化进程，提供更多政策支持。",
            "ai_impact": "强劲利好国产设备和材料龙头，可能引发短期资金涌入相关板块。",
            "ai_verification_status": "✅ 已验证，来源为政府官方公告",
            "category": ["国内", "政策"]
        },
        {
            "id": "mock_2",
            "title": "美国商务部 BIS 更新半导体出口管制规则",
            "source": "美国商务部 BIS",
            "publish_time": "1小时前",
            "original_link": "https://www.bis.doc.gov/",
            "credibility_level": "S",
            "region": "国外",
            "related_chains": ["先进制程", "AI芯片", "半导体设备"],
            "related_stocks": ["寒武纪", "海光信息", "北方华创"],
            "ai_summary": "美国进一步收紧部分先进芯片和设备出口限制。",
            "ai_impact": "短期可能导致相关公司供应链受阻，但中长期将刺激国产替代方向关注度上升。",
            "ai_verification_status": "✅ 已验证，来源为美国政府公告",
            "category": ["国外", "出口管制", "政策"]
        },
        {
            "id": "mock_3",
            "title": "全球半导体行业展望：AI驱动下的超级周期",
            "source": "BofA (美国银行)",
            "publish_time": "2026-07-04",
            "original_link": "#",
            "credibility_level": "A",
            "region": "国外",
            "related_chains": ["AI芯片", "算力硬件", "HBM"],
            "related_stocks": ["工业富联", "中际旭创", "香农芯创"],
            "ai_summary": "美银分析师指出 AI 算力需求持续爆发，HBM 和先进制程产能供不应求将贯穿2026全年。",
            "ai_impact": "利好算力产业链、光模块及存储代理商，机构资金可能会持续加仓。",
            "ai_verification_status": "✅ 已验证，提取自美银最新深度研报",
            "category": ["国外", "AI芯片", "存储"]
        },
        {
            "id": "mock_4",
            "title": "台积电宣布3纳米工艺良率突破80%，预计下半年满产",
            "source": "Bloomberg",
            "publish_time": "2小时前",
            "original_link": "https://www.bloomberg.com/",
            "credibility_level": "A",
            "region": "国外",
            "related_chains": ["晶圆制造", "消费电子"],
            "related_stocks": ["中芯国际", "长电科技"],
            "ai_summary": "台积电先进制程进展顺利，苹果、英伟达等大客户产能已被包揽。",
            "ai_impact": "提升全球半导体景气度预期，但对国内代工厂造成高端制程的竞争压力。",
            "ai_verification_status": "✅ 已验证，多家国际权威媒体交叉报道",
            "category": ["国外", "龙头公司"]
        },
        {
            "id": "mock_5",
            "title": "某国内存储大厂被列入出口管制实体清单传闻发酵",
            "source": "路透社",
            "publish_time": "4小时前",
            "original_link": "https://www.reuters.com/",
            "credibility_level": "B",
            "region": "国外",
            "related_chains": ["存储芯片"],
            "related_stocks": ["兆易创新", "长江存储(非上市)", "北京君正"],
            "ai_summary": "传闻美方正考虑将某国产存储巨头列入实体清单，限制其获取关键设备。",
            "ai_impact": "存在较高不确定性风险，短期可能导致存储板块情绪承压，需等待官方证实。",
            "ai_verification_status": "⚠️ 待验证，目前仅为外媒单方面信源，无官方确认",
            "category": ["国外", "出口管制"]
        },
        {
            "id": "mock_6",
            "title": "中芯国际发布Q2财报，营收超预期，产能利用率回升",
            "source": "上交所公告",
            "publish_time": "昨天",
            "original_link": "http://www.sse.com.cn/",
            "credibility_level": "S",
            "region": "国内",
            "related_chains": ["晶圆制造", "国产替代"],
            "related_stocks": ["中芯国际", "华虹公司"],
            "ai_summary": "中芯国际Q2营收大幅好于预期，主要受成熟制程需求回暖及国产替代订单驱动。",
            "ai_impact": "强劲利好晶圆代工板块，表明国内半导体周期底部已过，正步入复苏上行通道。",
            "ai_verification_status": "✅ 已验证，数据提取自上交所正式财报",
            "category": ["国内", "财报", "龙头公司"]
        },
        {
            "id": "mock_7",
            "title": "英伟达(NVDA)推出下一代AI芯片，算力再翻倍",
            "source": "NVIDIA 官网",
            "publish_time": "3小时前",
            "original_link": "https://www.nvidia.com/",
            "credibility_level": "S",
            "region": "国外",
            "related_chains": ["AI芯片", "算力硬件", "HBM"],
            "related_stocks": ["工业富联", "中际旭创", "通富微电"],
            "ai_summary": "英伟达发布了新一代 AI 芯片架构，性能与能效比大幅提升，进一步拉大与竞争对手的差距。",
            "ai_impact": "强力提振全球 AI 芯片及算力产业链情绪，直接利好国内代工、封测及光模块核心供应商。",
            "ai_verification_status": "✅ 已验证，来源为英伟达官方发布会",
            "category": ["国外", "AI芯片", "龙头公司"]
        },
        {
            "id": "mock_8",
            "title": "费城半导体指数(SOX)隔夜大涨 3.5%",
            "source": "纳斯达克",
            "publish_time": "昨夜",
            "original_link": "https://www.nasdaq.com/",
            "credibility_level": "A",
            "region": "国外",
            "related_chains": ["全球半导体大盘"],
            "related_stocks": ["深科技", "北方华创"],
            "ai_summary": "受科技巨头业绩超预期提振，费城半导体指数隔夜大幅收涨 3.5%，创近一月最大单日涨幅。",
            "ai_impact": "显著提升 A 股半导体板块今日开盘的风险偏好，尤其是与海外映射较强的核心标的。",
            "ai_verification_status": "✅ 已验证，客观行情数据",
            "category": ["国外", "龙头公司"]
        }
    ]
    
    if category == "all" or category == "latest":
        return base_news
    
    # Map API endpoints to categories
    category_map = {
        "domestic": "国内",
        "global": "国外",
        "policies": "政策",
        "company-events": "龙头公司",
        "export-control": "出口管制"
    }
    
    target_tag = category_map.get(category, category)
    
    return [news for news in base_news if target_tag in news.get("category", [])]

@router.get("/latest", response_model=List[RadarNews])
def get_latest_news(category: str = "all"):
    # Allow filtering via query param for the frontend tabs
    return generate_mock_news(category)

@router.get("/domestic", response_model=List[RadarNews])
def get_domestic_news():
    return generate_mock_news("domestic")

@router.get("/global", response_model=List[RadarNews])
def get_global_news():
    return generate_mock_news("global")

@router.get("/policies", response_model=List[RadarNews])
def get_policies_news():
    return generate_mock_news("policies")

@router.get("/company-events", response_model=List[RadarNews])
def get_company_events_news():
    return generate_mock_news("company-events")

@router.get("/export-control", response_model=List[RadarNews])
def get_export_control_news():
    return generate_mock_news("export-control")

