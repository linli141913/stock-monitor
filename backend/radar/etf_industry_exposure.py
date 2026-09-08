from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from radar.contracts import (
    EtfIndexIdentityEvidence,
    IndexConstituentOverlap,
    IndexConstituentSetEvidence,
    IndexEvidenceStatus,
    IndexIndustryExposureItem,
    IndexIndustryExposureResult,
    IndexProductGroup,
    IndexProductGroupCollection,
    IndustryClassificationSnapshot,
    IndustryIdentityStatus,
    IndustryRecordStatus,
    SourceStatus,
)


INDUSTRY_EXPOSURE_CALCULATION_VERSION = (
    "radar-etf-industry-exposure-v1"
)


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label}必须包含时区")


def _dedupe(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _group_key(provider: str, index_code: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    normalized_code = str(index_code or "").strip().upper()
    if not normalized_provider or not normalized_code:
        raise ValueError("指数产品组必须具有指数公司和代码")
    return f"{normalized_provider}:{normalized_code}"


def _constituent_set_id(value: IndexConstituentSetEvidence) -> str:
    source_date = (
        value.source_date.isoformat()
        if value.source_date is not None
        else "undated"
    )
    return (
        f"{_group_key(value.index_provider, value.index_code)}:"
        f"{source_date}:{value.evidence_sha256[:16]}"
    )


def calculate_index_industry_exposure(
    constituents: IndexConstituentSetEvidence,
    classification: IndustryClassificationSnapshot,
    *,
    as_of: datetime,
    computed_at: datetime,
    calculation_version: str = INDUSTRY_EXPOSURE_CALCULATION_VERSION,
) -> IndexIndustryExposureResult:
    _require_aware(as_of, "asOf")
    _require_aware(computed_at, "computedAt")
    if classification.release is None:
        raise ValueError("行业分类快照缺少发布版本")
    if classification.status == SourceStatus.FAILED:
        raise ValueError("失败的行业分类不能计算ETF行业暴露")
    if (
        constituents.returned_count == 0
        or constituents.weight_count != constituents.returned_count
        or constituents.weight_total is None
    ):
        raise ValueError("指数行业暴露必须使用完整原始权重")
    constituent_codes = [item.stock_code for item in constituents.items]
    if len(constituent_codes) != len(set(constituent_codes)):
        raise ValueError("指数行业暴露不能消费重复成分")
    if constituents.source_date is not None and constituents.source_date > as_of.date():
        raise ValueError("指数成分来源日期晚于行业暴露时点")

    release = classification.release
    if release.published_date > as_of.date():
        raise ValueError("行业分类发布日期晚于行业暴露时点")
    if release.classification_start_date > as_of.date():
        raise ValueError("行业分类工作起始日晚于行业暴露时点")

    reasons: List[str] = []
    if not constituents.formal_ready:
        reasons.append("constituent_version_not_formal")
    # A release first discovered after its publication cannot be used to
    # backfill an earlier replay.  Once it has actually been observed, the
    # official document may support a current forward calculation.  The
    # target constituent mapping below is audited independently, so unrelated
    # full-market master gaps do not invalidate a 100%-mapped index basket.
    if release.knowledge_effective_from > as_of:
        reasons.append("industry_mapping_retrospective_unverified")
    if (
        release.knowledge_effective_to is not None
        and release.knowledge_effective_to <= as_of
    ):
        reasons.append("industry_mapping_historical_expired")
    records_by_identity = defaultdict(list)
    for record in classification.records:
        if (
            record.record_status != IndustryRecordStatus.ACCEPTED
            or record.identity_status == IndustryIdentityStatus.UNRESOLVED
            or record.security_identity is None
        ):
            continue
        if record.security_identity in constituent_codes:
            records_by_identity[record.security_identity].append(record)

    conflicts = {
        symbol
        for symbol, records in records_by_identity.items()
        if len(records) != 1
    }
    if conflicts:
        reasons.append("industry_mapping_conflict")

    weights_by_industry: Dict[Tuple[str, str], float] = defaultdict(float)
    unmapped_symbols = []
    total_weight = 0.0
    mapped_weight = 0.0
    for constituent in constituents.items:
        weight = constituent.weight
        if weight is None:
            raise ValueError("指数行业暴露不得把缺失权重当作0")
        total_weight += weight
        records = records_by_identity.get(constituent.stock_code, [])
        if constituent.stock_code in conflicts or len(records) != 1:
            unmapped_symbols.append(constituent.stock_code)
            continue
        record = records[0]
        key = (record.division_code, record.division_name)
        weights_by_industry[key] += weight
        mapped_weight += weight

    if total_weight <= 0:
        raise ValueError("指数原始权重合计必须大于0")
    unmapped_weight = total_weight - mapped_weight
    mapping_coverage = mapped_weight / total_weight
    if unmapped_symbols:
        reasons.append("industry_mapping_incomplete")

    exposures = [
        IndexIndustryExposureItem(
            industryCode=industry_code,
            industryName=industry_name,
            rawWeight=round(raw_weight, 8),
            exposureRatio=raw_weight / total_weight,
        )
        for (industry_code, industry_name), raw_weight
        in sorted(
            weights_by_industry.items(),
            key=lambda item: (-item[1], item[0][0]),
        )
    ]
    reasons_tuple = _dedupe(reasons)
    formal_ready = not reasons_tuple
    industry_release_id = (
        f"{release.release_period}:{release.document_sha256[:16]}"
    )
    return IndexIndustryExposureResult(
        constituentSetId=_constituent_set_id(constituents),
        indexProvider=constituents.index_provider,
        indexCode=constituents.index_code,
        indexName=constituents.index_name,
        constituentSourceDate=constituents.source_date,
        industryReleaseId=industry_release_id,
        industryReleasePeriod=release.release_period,
        industryDocumentSha256=release.document_sha256,
        totalWeight=round(total_weight, 8),
        mappedWeight=round(mapped_weight, 8),
        unmappedWeight=round(unmapped_weight, 8),
        mappingCoverage=mapping_coverage,
        unmappedSymbols=tuple(sorted(unmapped_symbols)),
        exposures=exposures,
        asOf=as_of,
        computedAt=computed_at,
        calculationVersion=calculation_version,
        formalReady=formal_ready,
        reasons=reasons_tuple,
    )


def build_index_product_groups(
    relations: Iterable[EtfIndexIdentityEvidence],
) -> IndexProductGroupCollection:
    relation_items = list(relations)
    keys_by_symbol = defaultdict(set)
    relation_by_key_symbol = {}
    excluded_symbols = set()
    collection_reasons = []

    for relation in relation_items:
        if (
            relation.status != IndexEvidenceStatus.VERIFIED
            or not relation.identity_matched
        ):
            excluded_symbols.add(relation.symbol)
            collection_reasons.append("index_relation_not_verified")
            continue
        key = _group_key(
            relation.index_provider,
            relation.provider_index_code,
        )
        keys_by_symbol[relation.symbol].add(key)
        relation_by_key_symbol[(key, relation.symbol)] = relation

    conflicting_symbols = {
        symbol
        for symbol, keys in keys_by_symbol.items()
        if len(keys) > 1
    }
    if conflicting_symbols:
        excluded_symbols.update(conflicting_symbols)
        collection_reasons.append("index_group_identity_conflict")

    symbols_by_key = defaultdict(list)
    for symbol, keys in keys_by_symbol.items():
        if symbol in conflicting_symbols:
            continue
        for key in keys:
            symbols_by_key[key].append(symbol)

    groups = []
    for key, symbols in sorted(symbols_by_key.items()):
        provider, index_code = key.split(":", 1)
        members = tuple(sorted(set(symbols)))
        ready_symbols = tuple(
            symbol
            for symbol in members
            if relation_by_key_symbol[(key, symbol)].formal_ready
        )
        group_reasons = ["representative_selection_deferred"]
        if len(ready_symbols) != len(members):
            group_reasons.append("group_relation_not_formal")
        groups.append(IndexProductGroup(
            groupKey=key,
            indexProvider=provider,
            indexCode=index_code,
            memberSymbols=members,
            formallyReadySymbols=ready_symbols,
            candidateSlotCount=1,
            representativeSymbol=None,
            groupReady=bool(members),
            candidateReady=False,
            reasons=tuple(group_reasons),
        ))

    return IndexProductGroupCollection(
        relationCount=len(relation_items),
        groupCount=len(groups),
        candidateSlotCount=len(groups),
        groups=groups,
        excludedSymbols=tuple(sorted(excluded_symbols)),
        reasons=_dedupe(collection_reasons),
    )


def calculate_constituent_overlap(
    left: IndexConstituentSetEvidence,
    right: IndexConstituentSetEvidence,
    *,
    as_of: datetime,
) -> IndexConstituentOverlap:
    _require_aware(as_of, "asOf")
    reasons = []
    left_codes = [item.stock_code for item in left.items]
    right_codes = [item.stock_code for item in right.items]
    if len(left_codes) != len(set(left_codes)) or len(right_codes) != len(
        set(right_codes)
    ):
        reasons.append("overlap_duplicate_constituent")

    left_set = set(left_codes)
    right_set = set(right_codes)
    common = left_set & right_set
    union = left_set | right_set
    common_count = len(common)
    union_count = len(union)
    symbol_jaccard = common_count / union_count if union_count else None

    if left.source_date != right.source_date:
        reasons.append("constituent_version_time_mismatch")
    if not left.formal_ready or not right.formal_ready:
        reasons.append("overlap_constituent_version_not_formal")

    left_weights = {
        item.stock_code: item.weight
        for item in left.items
    }
    right_weights = {
        item.stock_code: item.weight
        for item in right.items
    }
    all_weights_present = all(
        value is not None
        for value in list(left_weights.values()) + list(right_weights.values())
    )
    common_minimum_weight = None
    weighted_overlap = None
    if not all_weights_present:
        reasons.append("overlap_weight_missing")
    else:
        left_total = sum(value for value in left_weights.values() if value is not None)
        right_total = sum(
            value
            for value in right_weights.values()
            if value is not None
        )
        denominator = min(left_total, right_total)
        common_minimum_weight = sum(
            min(left_weights[symbol], right_weights[symbol])
            for symbol in common
        )
        if denominator > 0:
            weighted_overlap = common_minimum_weight / denominator

    reasons_tuple = _dedupe(reasons)
    return IndexConstituentOverlap(
        leftGroupKey=_group_key(left.index_provider, left.index_code),
        rightGroupKey=_group_key(right.index_provider, right.index_code),
        leftSourceDate=left.source_date,
        rightSourceDate=right.source_date,
        commonCount=common_count,
        unionCount=union_count,
        symbolJaccard=symbol_jaccard,
        commonMinimumWeight=common_minimum_weight,
        weightedOverlap=weighted_overlap,
        asOf=as_of,
        formalReady=not reasons_tuple,
        reasons=reasons_tuple,
    )
