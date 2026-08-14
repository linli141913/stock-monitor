from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Set

from pydantic import ValidationError

from radar.ai.contracts import (
    FrozenRadarEvidencePackage,
    RadarAiStructuredOutput,
)


FORBIDDEN_OUTPUT_FIELDS = frozenset({
    "formalState",
    "formalScore",
    "formalScoreBreakdown",
    "score",
    "rank",
    "ranking",
    "priority",
    "industryState",
    "sectorState",
    "etfState",
    "leaderState",
    "evidenceLevel",
})
PROHIBITED_INVESTMENT_CLAIMS = (
    "目标价",
    "收益率",
    "保证上涨",
    "保证收益",
    "建议立即买入",
    "建议立即卖出",
    "买入建议",
    "卖出建议",
    "建议买入",
    "建议卖出",
    "推荐买入",
    "推荐卖出",
)


class RadarAiEvidenceError(ValueError):
    pass


class RadarAiOutputError(ValueError):
    pass


def _source_ids(package: FrozenRadarEvidencePackage) -> Set[str]:
    return {item.source_id for item in package.source_catalog}


def _referenced_sources(
    values: Iterable,
) -> Set[str]:
    referenced: Set[str] = set()
    for item in values:
        referenced.update(item.source_ids)
    return referenced


def validate_evidence_package(package: FrozenRadarEvidencePackage) -> str:
    if not package.formal_state_enabled:
        raise RadarAiEvidenceError("formal_state_not_enabled")
    if not package.data_completeness.is_complete:
        raise RadarAiEvidenceError("data_completeness_failed")
    if package.coverage < package.data_completeness.minimum_coverage:
        raise RadarAiEvidenceError("coverage_below_minimum")
    if not any(
        item.fact_kind in {"verified", "calculated", "reviewed"}
        for item in package.evidence
    ):
        raise RadarAiEvidenceError("verified_evidence_required")
    catalog_ids = _source_ids(package)
    if len(catalog_ids) != len(package.source_catalog):
        raise RadarAiEvidenceError("duplicate_source_id")
    referenced = _referenced_sources(
        (*package.evidence, *package.counter_evidence)
    )
    if not referenced.issubset(catalog_ids):
        raise RadarAiEvidenceError("unknown_source_id")
    normalized = package.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=False,
    )
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in FORBIDDEN_OUTPUT_FIELDS for key in value):
            return True
        return any(_contains_forbidden_field(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_field(item) for item in value)
    return False


def _contains_prohibited_claim(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace(" ", "")
        return any(claim in normalized for claim in PROHIBITED_INVESTMENT_CLAIMS)
    if isinstance(value, dict):
        return any(_contains_prohibited_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_prohibited_claim(item) for item in value)
    return False


def validate_model_output(
    payload: Dict[str, Any],
    package: FrozenRadarEvidencePackage,
) -> RadarAiStructuredOutput:
    if _contains_forbidden_field(payload):
        raise RadarAiOutputError("forbidden_output_field")
    if _contains_prohibited_claim(payload):
        raise RadarAiOutputError("prohibited_investment_claim")
    try:
        output = RadarAiStructuredOutput.model_validate(payload)
    except ValidationError as exc:
        raise RadarAiOutputError("invalid_output_contract") from exc
    allowed = _source_ids(package)
    referenced = set(output.source_ids)
    for fact in output.confirmed_facts:
        referenced.update(fact.source_ids)
    if not referenced.issubset(allowed):
        raise RadarAiOutputError("unknown_source_id")
    return output
