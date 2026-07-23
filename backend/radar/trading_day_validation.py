"""阶段2B-6B2交易日续验的只读采证与分层判定。"""

from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Union
from zoneinfo import ZoneInfo

from radar.migrations import validate_applied_migrations
from radar.runtime import (
    RADAR_ETF_QUOTES_JOB_ID,
    RADAR_REGISTRY_JOB_ID,
    RADAR_RUNTIME_LOCK_PATH,
    RADAR_STOCK_QUOTES_JOB_ID,
)


UTC = timezone.utc
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PathLike = Union[str, Path]
CHECK_STATUSES = frozenset({"passed", "failed", "pending"})
FINAL_RUN_STATUSES = frozenset({"succeeded", "degraded", "failed"})
COUNT_QUERIES = {
    "radarRuns": "SELECT COUNT(*) FROM radar_runs",
    "radarSourceStatus": "SELECT COUNT(*) FROM radar_source_status",
    "securityMasterHistory": "SELECT COUNT(*) FROM security_master_history",
    "etfProductRegistry": "SELECT COUNT(*) FROM etf_product_registry",
}
SCOPE_PREFIXES = {
    "registry": f"{RADAR_REGISTRY_JOB_ID}-",
    "stock": f"{RADAR_STOCK_QUOTES_JOB_ID}-",
    "etf": f"{RADAR_ETF_QUOTES_JOB_ID}-",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value.astimezone(UTC)


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"数据库中的{field_name}不是有效时间") from exc
    return _aware_utc(parsed, field_name)


def _day_bounds(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trading_date, time.min, SHANGHAI_TZ)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def _json_object(value: str, field_name: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"数据库中的{field_name}不是有效JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"数据库中的{field_name}必须是对象")
    return parsed


@contextmanager
def _read_only_database(database_path: PathLike) -> Iterator[sqlite3.Connection]:
    resolved = Path(database_path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError("雷达验收数据库路径不是现有文件")
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        validate_applied_migrations(connection)
        yield connection
    finally:
        connection.close()


@dataclass(frozen=True)
class RadarValidationBaseline:
    schema_version: int
    captured_at: datetime
    counts: Mapping[str, int]

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("不支持的雷达验收基线版本")
        _aware_utc(self.captured_at, "captured_at")
        missing = set(COUNT_QUERIES) - set(self.counts)
        if missing:
            raise ValueError(f"雷达验收基线缺少计数：{sorted(missing)}")
        if any(int(self.counts[key]) < 0 for key in COUNT_QUERIES):
            raise ValueError("雷达验收基线计数不能小于0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "capturedAt": _aware_utc(
                self.captured_at,
                "captured_at",
            ).isoformat(),
            "counts": {key: int(self.counts[key]) for key in COUNT_QUERIES},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RadarValidationBaseline":
        return cls(
            schema_version=int(value.get("schemaVersion", 0)),
            captured_at=_parse_datetime(value.get("capturedAt", ""), "capturedAt"),
            counts=value.get("counts", {}),
        )


@dataclass(frozen=True)
class ValidationCheck:
    key: str
    status: str
    message: str
    evidence: Mapping[str, Any]

    def __post_init__(self):
        if self.status not in CHECK_STATUSES:
            raise ValueError("雷达验收检查状态无效")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class TradingDayValidationReport:
    trading_date: date
    generated_at: datetime
    overall_status: str
    checks: tuple[ValidationCheck, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": 1,
            "tradingDate": self.trading_date.isoformat(),
            "generatedAt": _aware_utc(
                self.generated_at,
                "generated_at",
            ).isoformat(),
            "overallStatus": self.overall_status,
            "readOnly": True,
            "checks": [item.to_dict() for item in self.checks],
        }


def _read_counts(connection: sqlite3.Connection) -> Dict[str, int]:
    return {
        key: int(connection.execute(query).fetchone()[0])
        for key, query in COUNT_QUERIES.items()
    }


def capture_validation_baseline(
    database_path: PathLike,
    *,
    captured_at: Optional[datetime] = None,
) -> RadarValidationBaseline:
    """以SQLite只读连接冻结交易日前计数，不读取业务明细。"""

    captured = _aware_utc(captured_at or _utc_now(), "captured_at")
    with _read_only_database(database_path) as connection:
        counts = _read_counts(connection)
    return RadarValidationBaseline(
        schema_version=1,
        captured_at=captured,
        counts=counts,
    )


def _scope_for_run_id(run_id: str) -> Optional[str]:
    for scope, prefix in SCOPE_PREFIXES.items():
        if run_id.startswith(prefix):
            return scope
    return None


def _load_runs(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> list[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT radar_run_id, as_of, status, started_at, completed_at, "
        "expected_stock_count, returned_stock_count, stock_coverage, "
        "expected_etf_count, returned_etf_count, etf_coverage, error_code "
        "FROM radar_runs WHERE as_of >= ? AND as_of < ? ORDER BY as_of",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    runs = []
    for row in rows:
        run = dict(row)
        scope = _scope_for_run_id(str(run["radar_run_id"]))
        if scope is None:
            continue
        run["scope"] = scope
        run["as_of"] = _parse_datetime(run["as_of"], "as_of")
        run["started_at"] = _parse_datetime(run["started_at"], "started_at")
        run["completed_at"] = (
            _parse_datetime(run["completed_at"], "completed_at")
            if run["completed_at"] is not None
            else None
        )
        runs.append(run)
    return runs


def _load_sources(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> list[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT radar_run_id, batch_id, source, as_of, source_time, fetched_at, "
        "status, expected_count, returned_count, row_coverage, "
        "required_field_coverage_json, issues_json "
        "FROM radar_source_status WHERE as_of >= ? AND as_of < ? ORDER BY as_of",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    sources = []
    for row in rows:
        item = dict(row)
        if _scope_for_run_id(str(item["radar_run_id"])) is None:
            continue
        item["as_of"] = _parse_datetime(item["as_of"], "来源as_of")
        item["source_time"] = (
            _parse_datetime(item["source_time"], "source_time")
            if item["source_time"] is not None
            else None
        )
        item["fetched_at"] = _parse_datetime(item["fetched_at"], "fetched_at")
        item["field_coverage"] = _json_object(
            item["required_field_coverage_json"],
            "required_field_coverage_json",
        )
        item["issues"] = _json_object(item["issues_json"], "issues_json")
        sources.append(item)
    return sources


def _check(
    key: str,
    status: str,
    message: str,
    **evidence: Any,
) -> ValidationCheck:
    return ValidationCheck(key, status, message, evidence)


def _registry_check(
    runs: Sequence[Mapping[str, Any]],
    *,
    finished: bool,
) -> ValidationCheck:
    registry = [item for item in runs if item["scope"] == "registry"]
    succeeded = [item for item in registry if item["status"] == "succeeded"]
    if len(succeeded) > 1:
        status, message = "failed", "名册同一交易日成功超过一次"
    elif len(succeeded) == 1:
        status, message = "passed", "名册同一交易日恰好成功一次"
    elif finished:
        status, message = "failed", "交易日结束后仍没有健康名册批次"
    else:
        status, message = "pending", "等待当天首个健康名册批次"
    return _check(
        "registry_daily_once",
        status,
        message,
        attempts=len(registry),
        succeeded=len(succeeded),
        degraded=sum(item["status"] == "degraded" for item in registry),
        failed=sum(item["status"] == "failed" for item in registry),
    )


def _session_key(value: datetime) -> Optional[str]:
    local = value.astimezone(SHANGHAI_TZ)
    local_time = local.time().replace(tzinfo=None)
    if time(9, 30) <= local_time <= time(11, 30):
        return "morning"
    if time(13, 0) <= local_time <= time(15, 0):
        return "afternoon"
    return None


def _cadence_check(
    runs: Sequence[Mapping[str, Any]],
    *,
    trading_date: date,
    now: datetime,
    scope: str,
    key: str,
    label: str,
    interval_seconds: int,
    tolerance_seconds: int,
    finished: bool,
) -> ValidationCheck:
    scoped = [item for item in runs if item["scope"] == scope]
    outside = [item for item in scoped if _session_key(item["as_of"]) is None]
    gaps = []
    previous = None
    for item in scoped:
        if (
            previous is not None
            and _session_key(previous["as_of"]) == _session_key(item["as_of"])
            and _session_key(item["as_of"]) is not None
        ):
            gaps.append((item["as_of"] - previous["as_of"]).total_seconds())
        previous = item

    lower = interval_seconds - tolerance_seconds
    upper = interval_seconds + tolerance_seconds
    invalid_gaps = [gap for gap in gaps if gap < lower or gap > upper]
    boundary_gaps = []
    for session_name, start_time, end_time in (
        ("morning", time(9, 30), time(11, 30)),
        ("afternoon", time(13, 0), time(15, 0)),
    ):
        session_start = datetime.combine(
            trading_date,
            start_time,
            SHANGHAI_TZ,
        ).astimezone(UTC)
        session_end = datetime.combine(
            trading_date,
            end_time,
            SHANGHAI_TZ,
        ).astimezone(UTC)
        observed_end = min(now, session_end)
        if observed_end <= session_start:
            continue
        if (observed_end - session_start).total_seconds() < interval_seconds * 2:
            continue
        session_runs = [
            item for item in scoped
            if session_start <= item["as_of"] <= observed_end
        ]
        if not session_runs:
            boundary_gaps.append({
                "session": session_name,
                "startGapSeconds": None,
                "endGapSeconds": None,
            })
            continue
        start_gap = (session_runs[0]["as_of"] - session_start).total_seconds()
        end_gap = (observed_end - session_runs[-1]["as_of"]).total_seconds()
        if start_gap > upper or end_gap > upper:
            boundary_gaps.append({
                "session": session_name,
                "startGapSeconds": start_gap,
                "endGapSeconds": end_gap,
            })
    if outside:
        status, message = "failed", f"{label}在非交易时段产生了运行记录"
    elif len(scoped) < 3 or len(gaps) < 2:
        status = "failed" if finished else "pending"
        message = (
            f"{label}交易日样本不足"
            if finished
            else f"等待至少三次{label}真实运行"
        )
    elif invalid_gaps or boundary_gaps:
        status, message = "failed", f"{label}真实运行间隔不符合合同"
    else:
        status, message = "passed", f"{label}真实运行间隔符合合同"
    return _check(
        key,
        status,
        message,
        runCount=len(scoped),
        expectedIntervalSeconds=interval_seconds,
        toleranceSeconds=tolerance_seconds,
        minimumObservedGapSeconds=min(gaps) if gaps else None,
        maximumObservedGapSeconds=max(gaps) if gaps else None,
        invalidGapCount=len(invalid_gaps),
        boundaryGapCount=len(boundary_gaps),
        boundaryGaps=boundary_gaps,
        nonTradingRunCount=len(outside),
    )


def _registry_source_check(
    runs: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    *,
    finished: bool,
) -> ValidationCheck:
    succeeded_ids = {
        item["radar_run_id"]
        for item in runs
        if item["scope"] == "registry" and item["status"] == "succeeded"
    }
    relevant = [item for item in sources if item["radar_run_id"] in succeeded_ids]
    violations = []
    expected_sources = {
        "official_exchange_security_master",
        "official_exchange_etf_registry",
    }
    if succeeded_ids and {item["source"] for item in relevant} != expected_sources:
        violations.append("registry_sources_incomplete")
    for item in relevant:
        if item["status"] != "healthy":
            violations.append("registry_source_not_healthy")
        if item["returned_count"] <= 0:
            violations.append("registry_returned_no_rows")
        if item["row_coverage"] is not None and item["row_coverage"] < 0.995:
            violations.append("registry_row_coverage_below_threshold")
        if not item["issues"].get("allowsNewState", False):
            violations.append("registry_not_allowed_for_new_state")
    if violations:
        status, message = "failed", "名册来源合同存在未通过项"
    elif succeeded_ids:
        status, message = "passed", "名册来源、覆盖率和准入状态符合合同"
    elif finished:
        status, message = "failed", "交易日结束后没有可验证的健康名册来源"
    else:
        status, message = "pending", "等待健康名册来源记录"
    return _check(
        "registry_source_contract",
        status,
        message,
        sourceRecordCount=len(relevant),
        violationCounts={
            name: violations.count(name) for name in sorted(set(violations))
        },
    )


def _quote_source_check(
    sources: Sequence[Mapping[str, Any]],
    *,
    finished: bool,
) -> ValidationCheck:
    quotes = [
        item for item in sources
        if str(item["batch_id"]).endswith((":stock-quotes", ":etf-quotes"))
    ]
    violations = []
    ages = []
    fetch_lags = []
    for item in quotes:
        if item["source"] != "tencent_finance":
            violations.append("unexpected_quote_source")
        if item["status"] != "healthy":
            violations.append("quote_source_not_healthy")
        if item["expected_count"] is None or item["expected_count"] <= 0:
            violations.append("quote_expected_count_missing")
        if item["returned_count"] <= 0:
            violations.append("quote_returned_no_rows")
        if item["row_coverage"] is None or item["row_coverage"] < 0.995:
            violations.append("quote_row_coverage_below_threshold")
        for field_name in ("price", "source_time"):
            if float(item["field_coverage"].get(field_name, 0.0)) < 0.99:
                violations.append(f"quote_field_coverage_below_threshold:{field_name}")
        source_time = item["source_time"]
        if source_time is None:
            violations.append("quote_source_time_missing")
        else:
            age = (item["as_of"] - source_time).total_seconds()
            ages.append(age)
            if age < -5:
                violations.append("quote_source_time_in_future")
            elif age > 90:
                violations.append("quote_source_time_stale")
        fetch_lag = (item["fetched_at"] - item["as_of"]).total_seconds()
        fetch_lags.append(fetch_lag)
        if fetch_lag < 0:
            violations.append("quote_fetched_before_as_of")
        if not item["issues"].get("allowsNewState", False):
            violations.append("quote_not_allowed_for_new_state")
        if item["issues"].get("healthReasons"):
            violations.append("quote_health_reasons_present")
        if item["issues"].get("sourceIssues"):
            violations.append("quote_source_issues_present")

    if violations:
        status, message = "failed", "行情来源时间、覆盖率或缺失语义不符合合同"
    elif quotes:
        status, message = "passed", "行情来源时间、覆盖率和缺失语义符合合同"
    elif finished:
        status, message = "failed", "交易日结束后没有行情来源记录"
    else:
        status, message = "pending", "等待真实行情来源记录"
    return _check(
        "quote_source_contract",
        status,
        message,
        sourceRecordCount=len(quotes),
        maximumSourceAgeSeconds=max(ages) if ages else None,
        maximumFetchLagSeconds=max(fetch_lags) if fetch_lags else None,
        violationCounts={
            name: violations.count(name) for name in sorted(set(violations))
        },
    )


def _single_instance_check(
    runs: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> ValidationCheck:
    ordered = sorted(runs, key=lambda item: item["started_at"])
    overlaps = 0
    previous_end = None
    for item in ordered:
        if previous_end is not None and item["started_at"] < previous_end:
            overlaps += 1
        if item["completed_at"] is not None:
            previous_end = max(previous_end or item["completed_at"], item["completed_at"])
        else:
            previous_end = max(previous_end or now, now)

    running = [item for item in ordered if item["completed_at"] is None]
    stale_running = [
        item for item in running
        if (now - item["started_at"]).total_seconds() > 900
    ]
    if overlaps or len(running) > 1 or stale_running:
        status, message = "failed", "发现任务重叠或长时间未完成运行"
    elif running:
        status, message = "pending", "当前有一项正常执行中的任务，需稍后复核释放"
    elif ordered:
        status, message = "passed", "数据库证据未发现并发重叠运行"
    else:
        status, message = "pending", "等待交易日运行记录后检查单实例"
    return _check(
        "single_instance",
        status,
        message,
        runCount=len(ordered),
        overlapCount=overlaps,
        runningCount=len(running),
        staleRunningCount=len(stale_running),
    )


def _database_increment_check(
    counts: Mapping[str, int],
    baseline: Optional[RadarValidationBaseline],
    *,
    start: datetime,
    has_runs: bool,
    finished: bool,
) -> ValidationCheck:
    if baseline is None:
        return _check(
            "database_increment",
            "pending",
            "缺少交易日前只读基线，不能正式验证数据库增量",
        )
    if _aware_utc(baseline.captured_at, "captured_at") >= start:
        return _check(
            "database_increment",
            "failed",
            "基线冻结时间不早于交易日开始时间",
        )
    deltas = {
        key: int(counts[key]) - int(baseline.counts[key])
        for key in COUNT_QUERIES
    }
    if any(value < 0 for value in deltas.values()):
        status, message = "failed", "生产表计数小于冻结基线"
    elif deltas["radarRuns"] > 0 and deltas["radarSourceStatus"] > 0:
        status, message = "passed", "雷达运行与来源健康表产生了可追溯增量"
    elif finished:
        status, message = "failed", "交易日结束后没有形成预期雷达数据库增量"
    elif has_runs:
        status, message = "pending", "已有运行但来源健康增量尚未完整形成"
    else:
        status, message = "pending", "等待交易日影子写入"
    return _check(
        "database_increment",
        status,
        message,
        deltas=deltas,
    )


def _lock_file_check(lock_path: PathLike, *, has_runs: bool) -> ValidationCheck:
    path = Path(lock_path).expanduser()
    if not path.exists():
        status = "failed" if has_runs else "pending"
        message = (
            "已有运行记录但稳定锁文件不存在"
            if has_runs
            else "等待首次真实运行创建稳定锁文件"
        )
        return _check("lock_file_contract", status, message, exists=False)
    file_mode = stat.S_IMODE(path.stat().st_mode)
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    if not path.is_file() or file_mode != 0o600 or parent_mode != 0o700:
        status, message = "failed", "稳定锁文件或父目录权限不符合合同"
    else:
        status, message = "passed", "稳定锁文件和父目录权限符合合同"
    return _check(
        "lock_file_contract",
        status,
        message,
        exists=True,
        fileMode=oct(file_mode),
        parentMode=oct(parent_mode),
    )


def validate_trading_day(
    database_path: PathLike,
    trading_date: date,
    *,
    baseline: Optional[RadarValidationBaseline] = None,
    lock_path: PathLike = RADAR_RUNTIME_LOCK_PATH,
    now: Optional[datetime] = None,
    stock_interval_seconds: int = 180,
    etf_interval_seconds: int = 300,
) -> TradingDayValidationReport:
    """只读验证一个A股交易日；证据不足时必须返回pending。"""

    if not isinstance(trading_date, date):
        raise TypeError("trading_date必须是日期")
    generated_at = _aware_utc(now or _utc_now(), "now")
    local_now = generated_at.astimezone(SHANGHAI_TZ)
    start, end = _day_bounds(trading_date)
    finished = (
        local_now.date() > trading_date
        or (
            local_now.date() == trading_date
            and local_now.time().replace(tzinfo=None) >= time(15, 10)
        )
    )
    future = local_now.date() < trading_date

    with _read_only_database(database_path) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        counts = _read_counts(connection)
        runs = _load_runs(connection, start, end)
        sources = _load_sources(connection, start, end)

    checks = [
        _check(
            "database_integrity",
            "passed" if quick_check == "ok" else "failed",
            "生产SQLite只读完整性检查通过"
            if quick_check == "ok"
            else "生产SQLite完整性检查失败",
            quickCheck=quick_check,
        )
    ]
    if trading_date.weekday() >= 5:
        checks.append(_check(
            "target_date",
            "failed",
            "目标日期是周末，不能作为交易日续验证据",
            weekday=trading_date.isoweekday(),
        ))
    elif future:
        checks.append(_check(
            "target_date",
            "pending",
            "目标交易日尚未到达",
            weekday=trading_date.isoweekday(),
        ))
    else:
        checks.append(_check(
            "target_date",
            "passed",
            "目标日期已进入可采证范围",
            weekday=trading_date.isoweekday(),
        ))

    if future:
        checks.append(_check(
            "trading_day_complete",
            "pending",
            "目标交易日尚未开始，不能形成最终结论",
        ))
    elif finished:
        checks.append(_check(
            "trading_day_complete",
            "passed",
            "目标交易日已收盘并进入最终采证时点",
        ))
    else:
        checks.append(_check(
            "trading_day_complete",
            "pending",
            "盘中证据仅作预检，收盘后才能形成最终结论",
        ))

    checks.extend((
        _registry_check(runs, finished=finished),
        _cadence_check(
            runs,
            trading_date=trading_date,
            now=generated_at,
            scope="stock",
            key="stock_quote_cadence",
            label="股票行情",
            interval_seconds=stock_interval_seconds,
            tolerance_seconds=30,
            finished=finished,
        ),
        _cadence_check(
            runs,
            trading_date=trading_date,
            now=generated_at,
            scope="etf",
            key="etf_quote_cadence",
            label="ETF行情",
            interval_seconds=etf_interval_seconds,
            tolerance_seconds=45,
            finished=finished,
        ),
        _registry_source_check(runs, sources, finished=finished),
        _quote_source_check(sources, finished=finished),
        _single_instance_check(runs, now=generated_at),
        _database_increment_check(
            counts,
            baseline,
            start=start,
            has_runs=bool(runs),
            finished=finished,
        ),
        _lock_file_check(lock_path, has_runs=bool(runs)),
    ))

    if any(item.status == "failed" for item in checks):
        overall = "failed"
    elif any(item.status == "pending" for item in checks):
        overall = "pending"
    else:
        overall = "passed"
    return TradingDayValidationReport(
        trading_date=trading_date,
        generated_at=generated_at,
        overall_status=overall,
        checks=tuple(checks),
    )
