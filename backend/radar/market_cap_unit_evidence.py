"""把腾讯同响应市值校验聚合为批次级单位证据。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, Tuple

from radar.contracts import QuoteSnapshot, UnitVerificationStatus
from radar.sources.tencent_quotes import (
    MARKET_CAP_SCALE_TO_CNY,
    market_cap_crosscheck_matches,
)


MARKET_CAP_UNIT_EVIDENCE_CONTRACT_ID = "radar-market-cap-unit-evidence-v1"
SYMBOL_PATTERN = re.compile(r"^[034689][0-9]{5}$")


@dataclass(frozen=True)
class MarketCapUnitEvidence:
    status: UnitVerificationStatus
    stock_count: int
    active_stock_count: int
    verified_stock_count: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = MARKET_CAP_UNIT_EVIDENCE_CONTRACT_ID


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _positive_finite_number(value: Any) -> bool:
    return bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _values_match(quote: QuoteSnapshot) -> bool:
    values = (
        quote.market_cap_source,
        quote.market_cap_cny,
        quote.price,
        quote.total_shares_source,
    )
    if (
        quote.market_cap_unit_status != UnitVerificationStatus.VERIFIED
        or quote.currency != "CNY"
        or not all(_positive_finite_number(value) for value in values)
    ):
        return False
    scaled_market_cap = (
        float(quote.market_cap_source) * MARKET_CAP_SCALE_TO_CNY
    )
    derived_market_cap = (
        float(quote.price) * float(quote.total_shares_source)
    )
    return bool(
        abs(scaled_market_cap - float(quote.market_cap_cny)) <= 1e-6
        and market_cap_crosscheck_matches(
            scaled_market_cap,
            derived_market_cap,
        )
    )


def build_market_cap_unit_evidence(
    quotes: Any,
    *,
    stock_symbols: Iterable[str],
) -> MarketCapUnitEvidence:
    """仅在当前A股全集的活跃行情逐条通过同响应校验时确认单位。"""

    symbols = tuple(stock_symbols) if stock_symbols is not None else ()
    if (
        not isinstance(quotes, tuple)
        or not symbols
        or len(symbols) != len(set(symbols))
        or any(
            not isinstance(symbol, str)
            or SYMBOL_PATTERN.fullmatch(symbol) is None
            for symbol in symbols
        )
        or any(type(item) is not QuoteSnapshot for item in quotes)
    ):
        return MarketCapUnitEvidence(
            status=UnitVerificationStatus.UNVERIFIED,
            stock_count=len(symbols),
            active_stock_count=0,
            verified_stock_count=0,
            reasons=("market_cap_unit_evidence_contract_unverified",),
        )

    rows_by_symbol = {symbol: [] for symbol in symbols}
    for item in quotes:
        if item.symbol in rows_by_symbol:
            rows_by_symbol[item.symbol].append(item)

    reasons = []
    active_stock_count = 0
    verified_stock_count = 0
    for symbol in symbols:
        rows = rows_by_symbol[symbol]
        if not rows:
            reasons.append("market_cap_unit_quote_missing")
            continue
        if len(rows) != 1:
            reasons.append("market_cap_unit_quote_duplicate")
            continue
        quote = rows[0]
        if quote.is_explicitly_non_trading:
            continue
        active_stock_count += 1
        if quote.source != "tencent_finance":
            reasons.append("market_cap_unit_source_unverified")
            continue
        if not _values_match(quote):
            reasons.append("market_cap_unit_value_unverified")
            continue
        verified_stock_count += 1

    if active_stock_count == 0:
        reasons.append("market_cap_unit_active_stock_universe_empty")
    normalized_reasons = _dedupe(reasons)
    status = (
        UnitVerificationStatus.VERIFIED
        if (
            not normalized_reasons
            and active_stock_count > 0
            and verified_stock_count == active_stock_count
        )
        else UnitVerificationStatus.UNVERIFIED
    )
    return MarketCapUnitEvidence(
        status=status,
        stock_count=len(symbols),
        active_stock_count=active_stock_count,
        verified_stock_count=verified_stock_count,
        reasons=normalized_reasons,
    )
