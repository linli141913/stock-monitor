from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from radar.contracts import (
    RadarBatchMeta,
    SourceHealthResult,
    SourceStatus,
)


@dataclass(frozen=True)
class SourceHealthPolicy:
    minimum_row_coverage: float = 0.995
    minimum_required_field_coverage: float = 0.99
    maximum_age_seconds: Optional[int] = 90
    maximum_future_skew_seconds: int = 5
    required_fields: Tuple[str, ...] = ("price", "source_time")

    def __post_init__(self):
        if not 0 <= self.minimum_row_coverage <= 1:
            raise ValueError("minimum_row_coverage必须在0到1之间")
        if not 0 <= self.minimum_required_field_coverage <= 1:
            raise ValueError("minimum_required_field_coverage必须在0到1之间")
        if self.maximum_age_seconds is not None and self.maximum_age_seconds <= 0:
            raise ValueError("maximum_age_seconds必须大于0或为空")
        if self.maximum_future_skew_seconds < 0:
            raise ValueError("maximum_future_skew_seconds不得小于0")


def evaluate_source_health(
    meta: RadarBatchMeta,
    policy: SourceHealthPolicy,
    now: datetime,
) -> SourceHealthResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now必须包含时区")

    reasons = []
    if meta.returned_count == 0:
        reasons.append("source_returned_no_rows")

    if (
        meta.row_coverage is not None
        and meta.row_coverage < policy.minimum_row_coverage
    ):
        reasons.append("row_coverage_below_threshold")

    for field_name in policy.required_fields:
        coverage = meta.required_field_coverage.get(field_name, 0.0)
        if coverage < policy.minimum_required_field_coverage:
            reasons.append(
                f"required_field_coverage_below_threshold:{field_name}"
            )

    if meta.issues:
        reasons.append("source_issues_present")

    age_seconds = None
    stale = False
    if policy.maximum_age_seconds is not None:
        if meta.source_time is None:
            reasons.append("source_time_missing")
        else:
            signed_age_seconds = (now - meta.source_time).total_seconds()
            age_seconds = max(0.0, signed_age_seconds)
            if signed_age_seconds < -policy.maximum_future_skew_seconds:
                reasons.append("source_time_in_future")
            if age_seconds > policy.maximum_age_seconds:
                stale = True
                reasons.append("source_time_stale")

    if meta.returned_count == 0:
        status = SourceStatus.FAILED
    elif stale:
        status = SourceStatus.STALE
    elif reasons:
        status = SourceStatus.DEGRADED
    else:
        status = SourceStatus.HEALTHY

    return SourceHealthResult(
        status=status,
        allowsNewState=status == SourceStatus.HEALTHY,
        reasons=tuple(dict.fromkeys(reasons)),
        ageSeconds=age_seconds,
    )
