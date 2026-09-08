"""把六份内容校验的日终事实转换为四域客观结果提供方。"""

from __future__ import annotations

from datetime import date
import math
from pathlib import Path
from statistics import fmean
from typing import Dict, Iterable, Mapping, Sequence

from radar.replay_objective_outcomes import (
    ObjectiveOutcomeObservation,
    ObjectiveOutcomeRequest,
    _load_manifest_bound_model,
)
from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot, SHANGHAI_TZ


def _missing(observed_through, reason: str) -> ObjectiveOutcomeObservation:
    return ObjectiveOutcomeObservation(
        status="missing",
        observedThrough=observed_through,
        sourceIds=[],
        outcomeMetrics={},
        reasons=[reason],
    )


def _unverifiable(
    observed_through,
    reason: str,
) -> ObjectiveOutcomeObservation:
    return ObjectiveOutcomeObservation(
        status="unverifiable",
        observedThrough=observed_through,
        sourceIds=[],
        outcomeMetrics={},
        reasons=[reason],
    )


def _max_drawdown(values: Sequence[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def _normalized_mean_series(
    symbols: Sequence[str],
    quote_maps: Sequence[Mapping[str, object]],
):
    if not symbols:
        return None
    if any(symbol not in quote_maps[0] for symbol in symbols):
        return None
    cumulative = {symbol: 1.0 for symbol in symbols}
    result = [1.0]
    for quotes in quote_maps[1:]:
        if any(symbol not in quotes for symbol in symbols):
            return None
        if any(
            quotes[symbol].previous_close is None
            or float(quotes[symbol].previous_close) <= 0
            for symbol in symbols
        ):
            return None
        for symbol in symbols:
            cumulative[symbol] *= (
                float(quotes[symbol].price)
                / float(quotes[symbol].previous_close)
            )
        result.append(fmean(
            cumulative[symbol]
            for symbol in symbols
        ))
    return result


class DailySnapshotObjectiveOutcomeProvider:
    def __init__(self, snapshots: Iterable[DailyOutcomeSnapshot]):
        values = tuple(sorted(snapshots, key=lambda item: item.trade_date))
        if not values:
            raise ValueError("objective_daily_snapshots_required")
        if any(not isinstance(item, DailyOutcomeSnapshot) for item in values):
            raise ValueError("objective_daily_snapshot_unverified")
        if len({item.trade_date for item in values}) != len(values):
            raise ValueError("duplicate_objective_daily_trade_date")
        if len({item.task_bundle_id for item in values}) != 1:
            raise ValueError("objective_daily_task_identity_mismatch")
        if len({item.output_bundle_id for item in values}) != 1:
            raise ValueError("objective_daily_output_identity_mismatch")
        if any(
            item.sector_memberships_by_sample
            != values[0].sector_memberships_by_sample
            for item in values[1:]
        ):
            raise ValueError("objective_daily_membership_drift")
        self._task_bundle_id = values[0].task_bundle_id
        self._output_bundle_id = values[0].output_bundle_id
        self._snapshots = {item.trade_date: item for item in values}

    @property
    def snapshot_ids(self):
        return tuple(
            self._snapshots[day].snapshot_id for day in sorted(self._snapshots)
        )

    def __call__(
        self,
        request: ObjectiveOutcomeRequest,
    ) -> ObjectiveOutcomeObservation:
        if not isinstance(request, ObjectiveOutcomeRequest):
            raise ValueError("objective_outcome_request_unverified")
        if (
            request.task_bundle_id != self._task_bundle_id
            or request.output_bundle_id != self._output_bundle_id
        ):
            return _unverifiable(
                request.as_of,
                "objective_daily_bundle_identity_mismatch",
            )
        baseline_date = request.as_of.astimezone(SHANGHAI_TZ).date()
        required_dates = (baseline_date, *request.evaluation_trade_dates)
        available = [
            self._snapshots[day]
            for day in required_dates
            if day in self._snapshots
        ]
        observed_through = max(
            (item.captured_at for item in available),
            default=request.as_of,
        )
        if len(available) != len(required_dates):
            return _missing(
                observed_through,
                "objective_daily_snapshot_missing",
            )
        if any(item.status != "ready" for item in available):
            return _unverifiable(
                observed_through,
                "objective_daily_snapshot_not_ready",
            )
        if available[-1].trade_date != request.maturity_date:
            return _unverifiable(
                observed_through,
                "objective_daily_maturity_date_mismatch",
            )

        quote_maps = [
            {item.symbol: item for item in snapshot.security_quotes}
            for snapshot in available
        ]
        index_maps = [
            {item.index_key: item for item in snapshot.market_indices}
            for snapshot in available
        ]
        source_ids = sorted({
            source_id
            for snapshot in available
            for source_id in snapshot.source_ids
        })
        if request.domain == "market":
            metrics = self._market_metrics(
                request.sample_id,
                available,
                quote_maps,
                index_maps,
            )
        elif request.domain == "sector":
            metrics = self._sector_metrics(
                request.target_id,
                request.sample_id,
                available,
                quote_maps,
                index_maps,
            )
        elif request.domain == "etf":
            metrics = self._etf_metrics(request.target_id, quote_maps)
        elif request.domain == "leader":
            metrics = self._leader_metrics(
                request.target_id,
                request.sample_id,
                available,
                quote_maps,
            )
        else:
            return _unverifiable(
                observed_through,
                "objective_outcome_domain_unverified",
            )
        if metrics is None or any(
            isinstance(value, bool) or not math.isfinite(float(value))
            for value in metrics.values()
        ):
            return _missing(
                observed_through,
                f"objective_{request.domain}_daily_facts_incomplete",
            )
        return ObjectiveOutcomeObservation(
            status="ready",
            observedThrough=observed_through,
            sourceIds=source_ids,
            outcomeMetrics=metrics,
            reasons=[],
        )

    @staticmethod
    def _market_index_series(index_maps):
        keys = tuple(index_maps[0])
        if len(keys) != 4 or any(set(items) != set(keys) for items in index_maps):
            return None
        baseline = index_maps[0]
        return [fmean(
            float(items[key].price) / float(baseline[key].price)
            for key in keys
        ) for items in index_maps]

    def _market_metrics(self, sample_id, snapshots, quote_maps, index_maps):
        index_series = self._market_index_series(index_maps)
        members = sorted({
            symbol
            for symbols in snapshots[0].sector_memberships_by_sample.get(
                sample_id, {}
            ).values()
            for symbol in symbols
        })
        breadth_series = _normalized_mean_series(members, quote_maps)
        if index_series is None or breadth_series is None:
            return None
        return {
            "meanIndexReturn5d": index_series[-1] - 1.0,
            "marketBreadthReturn5d": breadth_series[-1] - 1.0,
            "maxDrawdown5d": _max_drawdown(index_series),
        }

    def _sector_metrics(
        self,
        target_id,
        sample_id,
        snapshots,
        quote_maps,
        index_maps,
    ):
        memberships = [
            snapshot.sector_memberships_by_sample.get(sample_id)
            for snapshot in snapshots
        ]
        if any(items is None for items in memberships):
            return None
        if any(items != memberships[0] for items in memberships[1:]):
            return None
        members = memberships[0].get(target_id)
        sector_series = _normalized_mean_series(members or (), quote_maps)
        market_series = self._market_index_series(index_maps)
        if sector_series is None or market_series is None:
            return None
        positive_days = sum(
            sector_series[index] > sector_series[index - 1]
            for index in range(1, len(sector_series))
        )
        return {
            "relativeMarketReturn3d": (
                sector_series[3] - market_series[3]
            ),
            "relativeMarketReturn5d": (
                sector_series[-1] - market_series[-1]
            ),
            "positiveReturnDays5d": float(positive_days),
            "constituentCoverage": 1.0,
        }

    @staticmethod
    def _etf_metrics(target_id, quote_maps):
        series = _normalized_mean_series((target_id,), quote_maps)
        if series is None:
            return None
        turnover = [
            items[target_id].turnover_amount_cny
            for items in quote_maps[1:]
        ]
        metrics = {
            "return5d": series[-1] - 1.0,
            "maxDrawdown": _max_drawdown(series),
        }
        if all(value is not None for value in turnover):
            metrics["turnover"] = fmean(float(value) for value in turnover)
        return metrics

    @staticmethod
    def _leader_metrics(target_id, sample_id, snapshots, quote_maps):
        if target_id == "__empty__":
            return None
        matching_sectors = [
            code
            for code, members in snapshots[0].sector_memberships_by_sample.get(
                sample_id, {}
            ).items()
            if target_id in members
        ]
        if len(matching_sectors) != 1:
            return None
        leader_series = _normalized_mean_series((target_id,), quote_maps)
        sector_series = _normalized_mean_series(
            snapshots[0].sector_memberships_by_sample[
                sample_id
            ][matching_sectors[0]],
            quote_maps,
        )
        if leader_series is None or sector_series is None:
            return None
        return {
            "relativeSectorReturn3d": leader_series[3] - sector_series[3],
            "relativeSectorReturn5d": leader_series[-1] - sector_series[-1],
            "maxAdverseExcursion": _max_drawdown(leader_series),
        }


def load_daily_outcome_snapshots(
    paths: Iterable[Path],
) -> Sequence[DailyOutcomeSnapshot]:
    values = []
    for path in paths:
        values.append(_load_manifest_bound_model(
            Path(path),
            manifest_key="dailyOutcomeSnapshot",
            model_type=DailyOutcomeSnapshot,
        ))
    return values


def build_daily_snapshot_outcome_providers(
    paths: Iterable[Path],
) -> Dict[str, DailySnapshotObjectiveOutcomeProvider]:
    provider = DailySnapshotObjectiveOutcomeProvider(
        load_daily_outcome_snapshots(paths)
    )
    return {
        domain: provider for domain in ("market", "sector", "etf", "leader")
    }
