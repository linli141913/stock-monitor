from datetime import datetime
from typing import Any, Dict, Optional

import market_calendar


OFFICIAL_SOURCES = {
    "巨潮公告",
    "上交所",
    "深交所",
    "北交所",
    "上交所公告",
    "深交所公告",
    "北交所公告",
    "港交所公告",
    "交易所公告",
}

NEGATIVE_RULES = (
    (("立案调查", "重大违法", "退市风险", "债务逾期", "停产"), "critical_risk", "P1"),
    (("异常波动", "特别风险提示", "风险提示"), "risk_warning", "P2"),
    (("预亏", "业绩大幅下降", "重大减值"), "earnings_decline", "P2"),
    (("减持",), "shareholder_reduction", "P2"),
    (("监管处罚", "行政处罚", "问询函", "重大诉讼"), "regulatory_risk", "P2"),
    (("项目终止", "合同终止", "客户流失", "辞职", "离任"), "operation_risk", "P2"),
)

POSITIVE_RULES = (
    (("业绩预增", "扭亏为盈", "同向上升"), "earnings_growth", "P2"),
    (("股份回购", "回购股份"), "share_buyback", "P2"),
    (("增持",), "shareholder_increase", "P2"),
    (("重大合同", "中标", "订单", "长期供货"), "major_contract", "P2"),
    (("正式批准", "获得批准", "获批"), "official_approval", "P2"),
    (("投产", "产能落地", "合作协议"), "project_progress", "P2"),
)

NEUTRAL_RULES = (
    (("分红", "利润分配"), "distribution", "P2"),
    (("定增", "非公开发行", "股权激励"), "capital_plan", "P2"),
    (("重大资产",), "major_asset_transaction", "P2"),
    (("董事会换届", "换届选举", "组织变更"), "governance_change", "P2"),
)


def _published_at(item: Dict[str, Any]) -> Optional[str]:
    if item.get("published_at"):
        return str(item["published_at"])
    ctime = item.get("ctime")
    if ctime is None:
        return None
    try:
        return datetime.fromtimestamp(
            float(ctime),
            tz=market_calendar.SHANGHAI_TZ,
        ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _match_rule(title: str, content: str, rules):
    text = f"{title}\n{content}"
    for keywords, event_type, priority in rules:
        if any(keyword in text for keyword in keywords):
            return event_type, priority
    return None


def classify_event_dimensions(
    item: Dict[str, Any],
    evidence_level: str,
) -> Dict[str, str]:
    """复用提醒中心规则，为可追溯资讯计算方向和重要程度。"""
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()

    matched = _match_rule(title, content, NEGATIVE_RULES)
    direction = "negative"
    if matched is None:
        matched = _match_rule(title, content, POSITIVE_RULES)
        direction = "positive"
    if matched is None:
        matched = _match_rule(title, content, NEUTRAL_RULES)
        direction = "neutral"
    if matched is None:
        return {"direction": "uncertain", "priority": "P3"}

    _, priority = matched
    if evidence_level == "C":
        priority = "P3"
    elif evidence_level != "S" and priority == "P1":
        priority = "P2"
    return {"direction": direction, "priority": priority}


def classify_official_event(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = str(item.get("source") or "").strip()
    symbol = str(item.get("symbol") or "").strip()
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()
    source_event_id = str(item.get("id") or item.get("source_event_id") or "").strip()
    source_url = str(item.get("url") or item.get("source_url") or "").strip()

    if source not in OFFICIAL_SOURCES or not symbol or not title or not source_event_id:
        return None

    matched = _match_rule(title, content, NEGATIVE_RULES)
    direction = "negative"
    if matched is None:
        matched = _match_rule(title, content, POSITIVE_RULES)
        direction = "positive"
    if matched is None:
        matched = _match_rule(title, content, NEUTRAL_RULES)
        direction = "neutral"
    if matched is None:
        matched = ("official_announcement", "P3")
        direction = "neutral"

    event_type, priority = matched
    direction_text = {
        "positive": "正面事件",
        "negative": "风险事件",
        "neutral": "重要中性事件",
    }[direction]
    stock_name = str(item.get("stock_name") or item.get("name") or symbol).strip()
    display_title = title if stock_name in title else f"{stock_name}：{title}"
    source_summary = content.strip()
    if source_summary in {
        "公告格式为纯图片或空文件",
        "公告 PDF 解析异常",
    }:
        source_summary = ""
    impact_summary = (
        f"影响判断：系统依据官方原文识别为{direction_text}；"
        "具体影响仍需结合公告原文和后续披露判断。"
    )

    return {
        "symbol": symbol,
        "stock_name": stock_name,
        "event_type": event_type,
        "direction": direction,
        "priority": priority,
        "evidence_level": "S",
        "title": display_title,
        "summary": f"{source_summary} {impact_summary}".strip(),
        "source": source,
        "source_url": source_url,
        "source_event_id": source_event_id,
        "published_at": _published_at(item),
    }
