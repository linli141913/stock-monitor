from datetime import datetime
from typing import Dict, Mapping, Optional, Sequence, Tuple

from radar.contracts import (
    EtfDailyFact,
    EtfMetricState,
    EtfRankingInputAudit,
    EtfShareChangeFact,
    EtfShareObservation,
    QuoteSnapshot,
    UnitVerificationStatus,
)
from radar.sources.etf_turnover_history import VerifiedEtfTurnoverWindow
from radar.sources.etf_fund_size_history import VerifiedEtfFundSizeSnapshot
from radar.sources.etf_tracking_history import VerifiedEtfTrackingWindow


SHARE_CHANGE_FORMULA_VERSION = "radar-etf-share-change-v1"
RANKING_INPUT_AUDIT_VERSION = "radar-etf-ranking-input-v1"
FORMAL_RANKING_FIELDS: Tuple[str, ...] = (
    "fundSize",
    "averageTurnover20d",
    "trackingDifference",
    "trackingError",
    "indexCorrelation",
)


def calculate_share_change(
    current: EtfShareObservation,
    prior: EtfShareObservation,
    *,
    window_trading_days: int,
    sample_count: int = 2,
) -> EtfShareChangeFact:
    if current.symbol != prior.symbol:
        raise ValueError("份额变化两期代码必须一致")
    if window_trading_days < 1:
        raise ValueError("份额变化窗口必须为正整数")

    reasons = []
    share_change = None
    if current.fund_shares_unit != prior.fund_shares_unit:
        reasons.append("share_unit_mismatch")
    if prior.fund_shares <= 0:
        reasons.append("prior_shares_zero")
    if sample_count < 2:
        reasons.append("share_sample_insufficient")
    if not reasons:
        share_change = (
            current.fund_shares / prior.fund_shares
        ) - 1

    return EtfShareChangeFact(
        symbol=current.symbol,
        windowTradingDays=window_trading_days,
        currentReportDate=current.source_report_date,
        priorReportDate=prior.source_report_date,
        currentFundShares=current.fund_shares,
        priorFundShares=prior.fund_shares,
        shareChange=share_change,
        sampleCount=sample_count,
        formulaVersion=SHARE_CHANGE_FORMULA_VERSION,
        formalUsable=not reasons,
        reasons=tuple(reasons),
    )


def build_etf_daily_fact(
    observation: EtfShareObservation,
    *,
    computed_at: datetime,
    share_changes: Optional[Mapping[int, EtfShareChangeFact]] = None,
    unverified_values: Optional[Mapping[str, Optional[float]]] = None,
    turnover_window: Optional[VerifiedEtfTurnoverWindow] = None,
    fund_size_snapshot: Optional[VerifiedEtfFundSizeSnapshot] = None,
    tracking_window: Optional[VerifiedEtfTrackingWindow] = None,
) -> EtfDailyFact:
    """Build a versioned daily fact without promoting unknown metrics."""
    values = dict(unverified_values or {})
    share_changes = share_changes or {}
    states = {
        field_name: EtfMetricState.SOURCE_UNVERIFIED
        for field_name in (
            "fundSize",
            "fundShares",
            "nav",
            "shareChange5d",
            "shareChange20d",
            "averageTurnover20d",
            "trackingDifference",
            "trackingError",
            "indexCorrelation",
        )
    }
    values["fundShares"] = observation.fund_shares
    states["fundShares"] = EtfMetricState.VERIFIED
    source_contract_ids = {
        "fundShares": observation.source_contract_id,
    }
    fund_size_unit = None
    nav_currency = None
    if fund_size_snapshot is not None:
        if (
            not isinstance(fund_size_snapshot, VerifiedEtfFundSizeSnapshot)
            or fund_size_snapshot.symbol != observation.symbol
            or fund_size_snapshot.trade_date
            != observation.source_report_date
            or fund_size_snapshot.fetched_at > computed_at
        ):
            raise ValueError("etf_fund_size_snapshot_identity_mismatch")
        if (
            fund_size_snapshot.formal_usable
            and fund_size_snapshot.fund_size_cny is not None
            and not fund_size_snapshot.reasons
        ):
            values["fundSize"] = fund_size_snapshot.fund_size_cny
            states["fundSize"] = EtfMetricState.VERIFIED
            source_contract_ids["fundSize"] = (
                fund_size_snapshot.source_contract_id
            )
            fund_size_unit = "CNY"
    for window, field_name in ((5, "shareChange5d"), (20, "shareChange20d")):
        change = share_changes.get(window)
        if change is None:
            continue
        if change.share_change is not None and change.formal_usable:
            values[field_name] = change.share_change
            states[field_name] = EtfMetricState.VERIFIED
        else:
            states[field_name] = EtfMetricState.SOURCE_UNVERIFIED
    if turnover_window is not None:
        if (
            not isinstance(turnover_window, VerifiedEtfTurnoverWindow)
            or turnover_window.symbol != observation.symbol
            or turnover_window.fetched_at > computed_at
            or not turnover_window.expected_trade_dates
            or turnover_window.expected_trade_dates[-1]
            != observation.source_report_date
        ):
            raise ValueError("etf_turnover_window_identity_mismatch")
        if (
            turnover_window.formal_usable
            and turnover_window.average_turnover_20d is not None
            and turnover_window.sample_count == 20
            and not turnover_window.reasons
        ):
            values["averageTurnover20d"] = (
                turnover_window.average_turnover_20d
            )
            states["averageTurnover20d"] = EtfMetricState.VERIFIED
            source_contract_ids["averageTurnover20d"] = (
                turnover_window.source_contract_id
            )
    if tracking_window is not None:
        if (
            not isinstance(tracking_window, VerifiedEtfTrackingWindow)
            or tracking_window.symbol != observation.symbol
            or tracking_window.computed_at > computed_at
            or not tracking_window.expected_trade_dates
            or tracking_window.expected_trade_dates[-1]
            != observation.source_report_date
        ):
            raise ValueError("etf_tracking_window_identity_mismatch")
        if (
            tracking_window.formal_usable
            and tracking_window.tracking_difference is not None
            and tracking_window.tracking_error is not None
            and tracking_window.index_correlation is not None
            and tracking_window.latest_unit_nav is not None
            and tracking_window.sample_count == 60
            and not tracking_window.reasons
        ):
            values["nav"] = tracking_window.latest_unit_nav
            values["trackingDifference"] = (
                tracking_window.tracking_difference
            )
            values["trackingError"] = tracking_window.tracking_error
            values["indexCorrelation"] = (
                tracking_window.index_correlation
            )
            states["nav"] = EtfMetricState.VERIFIED
            states["trackingDifference"] = EtfMetricState.VERIFIED
            states["trackingError"] = EtfMetricState.VERIFIED
            states["indexCorrelation"] = EtfMetricState.VERIFIED
            source_contract_ids["nav"] = (
                tracking_window.nav_source_contract_id
            )
            for field_name in (
                "trackingDifference",
                "trackingError",
                "indexCorrelation",
            ):
                source_contract_ids[field_name] = (
                    tracking_window.formula_version
                )
            nav_currency = "CNY"

    field_values = {
        "fundSize": values.get("fundSize"),
        "fundShares": values.get("fundShares"),
        "nav": values.get("nav"),
        "shareChange5d": values.get("shareChange5d"),
        "shareChange20d": values.get("shareChange20d"),
        "averageTurnover20d": values.get("averageTurnover20d"),
        "trackingDifference": values.get("trackingDifference"),
        "trackingError": values.get("trackingError"),
        "indexCorrelation": values.get("indexCorrelation"),
    }
    reasons = [
        "daily_fact_field_source_unverified",
        "ranking_required_fact_unavailable",
    ]
    return EtfDailyFact(
        symbol=observation.symbol,
        tradeDate=observation.source_report_date,
        sourceReportDate=observation.source_report_date,
        fundSize=field_values["fundSize"],
        fundSizeUnit=fund_size_unit,
        fundShares=field_values["fundShares"],
        fundSharesUnit=observation.fund_shares_unit,
        nav=field_values["nav"],
        navCurrency=nav_currency,
        shareChange5d=field_values["shareChange5d"],
        shareChange20d=field_values["shareChange20d"],
        averageTurnover20d=field_values["averageTurnover20d"],
        trackingDifference=field_values["trackingDifference"],
        trackingError=field_values["trackingError"],
        indexCorrelation=field_values["indexCorrelation"],
        windowTradingDays=(
            tracking_window.window_trading_days
            if tracking_window is not None
            else 20
        ),
        sampleCount=(
            max(
                [
                    change.sample_count
                    for change in share_changes.values()
                ] + (
                    [turnover_window.sample_count]
                    if turnover_window is not None
                    else []
                ) + (
                    [tracking_window.sample_count]
                    if tracking_window is not None
                    else []
                ),
                default=None,
            )
        ),
        formulaVersion=(
            tracking_window.formula_version
            if tracking_window is not None
            else SHARE_CHANGE_FORMULA_VERSION
        ),
        fieldStates=states,
        sourceContractIds=source_contract_ids,
        fetchedAt=observation.fetched_at,
        computedAt=computed_at,
        formalUsable=False,
        reasons=tuple(reasons),
    )


def build_ranking_input_audit(
    quote: QuoteSnapshot,
    daily_fact: Optional[EtfDailyFact],
    *,
    as_of: datetime,
    formal_gate_ready: bool = False,
) -> EtfRankingInputAudit:
    """Audit ranking inputs; values stay visible but unverified values cannot rank."""
    turnover_cny_verified = (
        quote.turnover_amount_unit_status == UnitVerificationStatus.VERIFIED
        and quote.turnover_amount_cny is not None
    )
    metric_values: Dict[str, Optional[float]] = {
        "price": quote.price,
        "changePercent": quote.change_percent,
        "turnoverAmountSource": quote.turnover_amount_source,
        "turnoverAmountCny": quote.turnover_amount_cny,
    }
    field_states: Dict[str, EtfMetricState] = {
        "price": (
            EtfMetricState.VERIFIED
            if quote.price is not None
            else EtfMetricState.MISSING
        ),
        "changePercent": (
            EtfMetricState.VERIFIED
            if quote.change_percent is not None
            else EtfMetricState.MISSING
        ),
        "turnoverAmountSource": EtfMetricState.SOURCE_UNVERIFIED,
        "turnoverAmountCny": (
            EtfMetricState.VERIFIED
            if turnover_cny_verified
            else EtfMetricState.SOURCE_UNVERIFIED
        ),
    }
    if daily_fact is None:
        for field_name in FORMAL_RANKING_FIELDS:
            field_states[field_name] = EtfMetricState.SOURCE_UNVERIFIED
            metric_values[field_name] = None
        reasons = ["daily_fact_missing"]
    else:
        value_by_field = {
            "fundSize": daily_fact.fund_size,
            "averageTurnover20d": daily_fact.average_turnover_20d,
            "trackingDifference": daily_fact.tracking_difference,
            "trackingError": daily_fact.tracking_error,
            "indexCorrelation": daily_fact.index_correlation,
        }
        for field_name in FORMAL_RANKING_FIELDS:
            metric_values[field_name] = value_by_field[field_name]
            field_states[field_name] = daily_fact.field_states[field_name]
        reasons = []
    rankable_fields = tuple(
        field_name
        for field_name in FORMAL_RANKING_FIELDS
        if field_states[field_name] == EtfMetricState.VERIFIED
        and metric_values[field_name] is not None
    )
    excluded_fields = tuple(
        field_name
        for field_name, state in field_states.items()
        if state != EtfMetricState.VERIFIED
    )
    required_fields_complete = all(
        field_states[field_name] == EtfMetricState.VERIFIED
        and metric_values[field_name] is not None
        for field_name in FORMAL_RANKING_FIELDS
    )
    if not required_fields_complete:
        reasons.append("ranking_input_source_unverified")
        reasons.append("ranking_required_fields_incomplete")
    if not formal_gate_ready:
        reasons.append("product_index_exposure_gate_not_proven")

    return EtfRankingInputAudit(
        symbol=quote.symbol,
        asOf=as_of,
        fetchedAt=quote.fetched_at,
        metricValues=metric_values,
        fieldStates=field_states,
        rankableFields=rankable_fields,
        excludedFields=excluded_fields,
        formalReady=(
            formal_gate_ready
            and not reasons
            and required_fields_complete
        ),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def formal_rankable_fields(
    audits: Sequence[EtfRankingInputAudit],
) -> Tuple[str, ...]:
    """Return the intersection of formally verified ranking fields."""
    if not audits:
        return ()
    common = set(FORMAL_RANKING_FIELDS)
    for audit in audits:
        common &= set(audit.rankable_fields)
    return tuple(
        field_name
        for field_name in FORMAL_RANKING_FIELDS
        if field_name in common
    )
