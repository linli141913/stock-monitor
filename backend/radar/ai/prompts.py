from __future__ import annotations

from typing import Dict

from radar.ai.contracts import RadarAiScopeType


PROMPT_VERSIONS: Dict[RadarAiScopeType, str] = {
    "market": "radar-market-v1",
    "sector": "radar-sector-v1",
    "etf": "radar-etf-v1",
    "leader": "radar-leader-v1",
}


def prompt_version_for_scope(scope_type: RadarAiScopeType) -> str:
    try:
        return PROMPT_VERSIONS[scope_type]
    except KeyError as exc:
        raise ValueError("unsupported_radar_ai_scope") from exc


def system_prompt_for_scope(scope_type: RadarAiScopeType) -> str:
    labels = {
        "market": "市场环境",
        "sector": "行业主线",
        "etf": "行业ETF",
        "leader": "龙头梯队",
    }
    prompt_version_for_scope(scope_type)
    return (
        f"你是主线雷达的{labels[scope_type]}证据解释器。"
        "只能解释输入中已经冻结的正式状态、事实、反证和未知项；"
        "不得返回或修改状态、分数、排名、优先级和证据等级；"
        "不提供目标价、收益承诺或确定买卖建议。"
        "只输出JSON对象，且必须严格使用以下字段："
        "analysisStatus(固定为success)、confirmedFacts(元素仅含text和sourceIds)、"
        "inferences、unknowns、counterEvidence、conditionalScenarios、"
        "plainEnglishSummary、sourceIds、invalidatingConditions。"
        "除plainEnglishSummary为字符串外，其余内容字段均为数组；"
        "sourceIds只能引用输入sourceCatalog已存在的编号。"
    )
