"""把逐证券同响应成交额校验聚合为批次单位证据。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, Tuple

from radar.contracts import QuoteSnapshot, UnitVerificationStatus


TURNOVER_UNIT_EVIDENCE_CONTRACT_ID = "radar-turnover-unit-evidence-v1"
AMOUNT_TOLERANCE_CNY = 10_000.0
SYMBOL_PATTERN = re.compile(r"^[036][0-9]{5}$")


@dataclass(frozen=True)
class TurnoverUnitEvidence:
    status: UnitVerificationStatus
    stock_count: int
    active_stock_count: int
    verified_stock_count: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    contract_id: str = TURNOVER_UNIT_EVIDENCE_CONTRACT_ID


def _dedupe(values: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _amounts_match(quote: QuoteSnapshot) -> bool:
    raw = quote.turnover_amount_source
    cny = quote.turnover_amount_cny
    return bool(
        quote.turnover_amount_unit_status == UnitVerificationStatus.VERIFIED
        and isinstance(raw, (int, float))
        and not isinstance(raw, bool)
        and math.isfinite(float(raw))
        and float(raw) >= 0
        and isinstance(cny, (int, float))
        and not isinstance(cny, bool)
        and math.isfinite(float(cny))
        and float(cny) >= 0
        and abs(float(raw) * 10_000.0 - float(cny))
        <= AMOUNT_TOLERANCE_CNY
    )


def build_turnover_unit_evidence(
    quotes: Any,
    *,
    stock_symbols: Iterable[str],
) -> TurnoverUnitEvidence:
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
        return TurnoverUnitEvidence(
            status=UnitVerificationStatus.UNVERIFIED,
            stock_count=len(symbols),
            active_stock_count=0,
            verified_stock_count=0,
            reasons=("turnover_unit_evidence_contract_unverified",),
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
            reasons.append("turnover_unit_quote_missing")
            continue
        if len(rows) != 1:
            reasons.append("turnover_unit_quote_duplicate")
            continue
        quote = rows[0]
        if quote.is_explicitly_non_trading:
            continue
        active_stock_count += 1
        if quote.source != "tencent_finance":
            reasons.append("turnover_unit_source_unverified")
            continue
        if not _amounts_match(quote):
            reasons.append("turnover_unit_value_unverified")
            continue
        verified_stock_count += 1

    if active_stock_count == 0:
        reasons.append("turnover_unit_active_stock_universe_empty")
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
    return TurnoverUnitEvidence(
        status=status,
        stock_count=len(symbols),
        active_stock_count=active_stock_count,
        verified_stock_count=verified_stock_count,
        reasons=normalized_reasons,
    )
