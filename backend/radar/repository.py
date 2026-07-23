"""主线雷达独立SQLite仓储。

仓储只使用调用方显式提供且已完成雷达迁移的连接，不查找生产数据库路径，
也不会在导入或初始化时自动建表。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, Type

from radar.contracts import (
    EtfRegistryRecord,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    MarketFeatureSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SectorFeatureBatch,
    SourceBatch,
    SourceHealthResult,
)
from radar.migrations import (
    INDUSTRY_STORAGE_MIGRATION,
    MARKET_ENVIRONMENT_STORAGE_MIGRATION,
)


class RadarRepositoryError(RuntimeError):
    """雷达仓储基础错误。"""


class RepositoryStateError(RadarRepositoryError):
    """连接或记录状态不允许当前操作。"""


class RepositoryConflictError(RadarRepositoryError):
    """相同业务标识已保存不同内容。"""


class HistoryAsOfError(RadarRepositoryError):
    """历史版本的生效时间没有向前推进。"""


class RepositoryWriteError(RadarRepositoryError):
    """SQLite写入失败且本次事务已经回滚。"""


@dataclass(frozen=True)
class HistoryWriteResult:
    inserted: int = 0
    unchanged: int = 0
    closed: int = 0

    def as_tuple(self) -> Tuple[int, int, int]:
        return self.inserted, self.unchanged, self.closed


FINAL_RUN_STATUSES = frozenset({"succeeded", "degraded", "failed"})
UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}必须包含时区")
    return value


def _datetime_text(value: datetime, field_name: str) -> str:
    aware = _aware_datetime(value, field_name)
    return aware.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RepositoryStateError(f"数据库中的{field_name}不是有效时间") from exc
    return _aware_datetime(parsed, field_name).astimezone(UTC)


def _json_default(value: Any):
    if isinstance(value, datetime):
        return _datetime_text(value, "JSON时间")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"不支持写入JSON的类型：{type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def _checksum(value: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _optional_date_text(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _load_json(value: str, label: str, expected_type: Type) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RepositoryStateError(f"数据库中的{label}不是有效JSON") from exc
    if not isinstance(parsed, expected_type):
        raise RepositoryStateError(f"数据库中的{label}类型不正确")
    return parsed


def _industry_release_id(release: IndustryClassificationRelease) -> str:
    return (
        f"{release.classification_system}:{release.release_period}:"
        f"{release.document_sha256[:16]}"
    )


def _symbols_checksum(symbols: Tuple[str, ...]) -> str:
    payload = _canonical_json(list(symbols))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coverage(
    expected_count: Optional[int],
    returned_count: Optional[int],
    label: str,
) -> Optional[float]:
    if expected_count is not None and expected_count < 0:
        raise ValueError(f"{label}预期数量不能小于0")
    if returned_count is not None and returned_count < 0:
        raise ValueError(f"{label}返回数量不能小于0")
    if expected_count is None:
        return None
    if returned_count is None:
        raise ValueError(f"{label}预期数量已知时必须提供返回数量")
    if returned_count > expected_count:
        raise ValueError(f"{label}返回数量不能大于预期数量")
    return returned_count / expected_count if expected_count else 0.0


class RadarRepository:
    """在调用方连接上执行雷达仓储写入，每个公开写方法单独提交。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._connection = connection
        self._clock = clock

    def _clock_text(self) -> str:
        return _datetime_text(self._clock(), "仓储写入时间")

    def _list_symbols_as_of(self, table: str, as_of: datetime) -> Tuple[str, ...]:
        as_of_text = _datetime_text(as_of, "as_of")
        rows = self._connection.execute(
            f"SELECT DISTINCT symbol FROM {table} "
            "WHERE effective_from <= ? "
            "AND (effective_to IS NULL OR effective_to > ?) "
            "ORDER BY symbol",
            (as_of_text, as_of_text),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def list_security_symbols(self, as_of: datetime) -> Tuple[str, ...]:
        """读取冻结时点实际生效的证券池，不使用未来版本。"""
        return self._list_symbols_as_of("security_master_history", as_of)

    def list_security_master_records(
        self,
        as_of: datetime,
    ) -> Tuple[SecurityMasterRecord, ...]:
        """读取冻结时点实际生效的完整证券主档，不使用未来版本。"""
        as_of_text = _datetime_text(as_of, "as_of")
        rows = self._connection.execute(
            "SELECT symbol, name, exchange, board, listing_date, "
            "total_shares, circulating_shares, source_industry, "
            "source_report_date, source, created_at, source_fields_json "
            "FROM security_master_history "
            "WHERE effective_from <= ? "
            "AND (effective_to IS NULL OR effective_to > ?) "
            "ORDER BY symbol, source",
            (as_of_text, as_of_text),
        ).fetchall()
        return tuple(SecurityMasterRecord(
            symbol=row[0],
            name=row[1],
            exchange=row[2],
            board=row[3],
            listingDate=row[4],
            totalShares=row[5],
            circulatingShares=row[6],
            sourceIndustry=row[7],
            sourceReportDate=row[8],
            source=row[9],
            fetchedAt=_parse_datetime(str(row[10]), "证券主档created_at"),
            sourceFields=_load_json(row[11], "证券主档来源字段", dict),
        ) for row in rows)

    def list_etf_symbols(self, as_of: datetime) -> Tuple[str, ...]:
        """读取冻结时点实际生效的ETF池，不使用未来版本。"""
        return self._list_symbols_as_of("etf_product_registry", as_of)

    def latest_healthy_source_as_of(self, source: str) -> Optional[datetime]:
        """返回某来源最后一次健康批次的冻结时点。"""
        source = source.strip()
        if not source:
            raise ValueError("source不能为空")
        row = self._connection.execute(
            "SELECT as_of FROM radar_source_status "
            "WHERE source=? AND status='healthy' "
            "ORDER BY as_of DESC LIMIT 1",
            (source,),
        ).fetchone()
        if row is None:
            return None
        return _parse_datetime(str(row[0]), "来源健康as_of")

    def get_latest_run_row(self, run_id_prefix: str) -> Optional[Dict[str, Any]]:
        """按稳定任务前缀读取最近一次运行，不改变任何运行状态。"""
        run_id_prefix = run_id_prefix.strip()
        if not run_id_prefix:
            raise ValueError("run_id_prefix不能为空")
        row = self._connection.execute(
            "SELECT radar_run_id, as_of, status, shadow_mode, rule_version_id, "
            "started_at, completed_at, error_code "
            "FROM radar_runs WHERE radar_run_id LIKE ? "
            "ORDER BY as_of DESC, started_at DESC LIMIT 1",
            (f"{run_id_prefix}%",),
        ).fetchone()
        if row is None:
            return None
        return {
            "radarRunId": row[0],
            "asOf": _parse_datetime(str(row[1]), "运行as_of"),
            "status": row[2],
            "shadowMode": bool(row[3]),
            "ruleVersionId": row[4],
            "startedAt": _parse_datetime(str(row[5]), "运行started_at"),
            "completedAt": (
                _parse_datetime(str(row[6]), "运行completed_at")
                if row[6] is not None
                else None
            ),
            "errorCode": row[7],
        }

    def list_source_status_rows(
        self,
        radar_run_id: str,
    ) -> Tuple[Dict[str, Any], ...]:
        """读取一轮运行的来源健康摘要，不返回证券明细。"""
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        rows = self._connection.execute(
            "SELECT batch_id, source, as_of, source_time, fetched_at, status, "
            "expected_count, returned_count, row_coverage, "
            "required_field_coverage_json, issues_json "
            "FROM radar_source_status WHERE radar_run_id=? "
            "ORDER BY batch_id, source",
            (radar_run_id,),
        ).fetchall()
        return tuple({
            "batchId": row[0],
            "source": row[1],
            "asOf": _parse_datetime(str(row[2]), "来源状态as_of"),
            "sourceTime": (
                _parse_datetime(str(row[3]), "来源状态source_time")
                if row[3] is not None
                else None
            ),
            "fetchedAt": _parse_datetime(
                str(row[4]),
                "来源状态fetched_at",
            ),
            "status": row[5],
            "expectedCount": row[6],
            "returnedCount": row[7],
            "rowCoverage": row[8],
            "requiredFieldCoverage": _load_json(
                row[9],
                "来源状态字段覆盖率",
                dict,
            ),
            "details": _load_json(row[10], "来源状态详情", dict),
        } for row in rows)

    def _require_industry_storage(self) -> None:
        try:
            row = self._connection.execute(
                "SELECT name, checksum FROM radar_schema_migrations "
                "WHERE version=?",
                (INDUSTRY_STORAGE_MIGRATION.version,),
            ).fetchone()
            existing_tables = {
                str(item[0])
                for item in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ("
                    "'industry_classification_releases', "
                    "'industry_classification_records', "
                    "'sector_feature_snapshots'"
                    ")"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise RepositoryStateError(
                "数据库未完成行业仓储迁移版本2"
            ) from exc
        expected = (
            INDUSTRY_STORAGE_MIGRATION.name,
            INDUSTRY_STORAGE_MIGRATION.checksum,
        )
        if row is None or tuple(row) != expected:
            raise RepositoryStateError(
                "数据库未完成行业仓储迁移版本2或迁移记录已漂移"
            )
        required_tables = {
            "industry_classification_releases",
            "industry_classification_records",
            "sector_feature_snapshots",
        }
        if existing_tables != required_tables:
            raise RepositoryStateError("数据库缺少行业仓储表")

    def _require_market_storage(self) -> None:
        try:
            row = self._connection.execute(
                "SELECT name, checksum FROM radar_schema_migrations "
                "WHERE version=?",
                (MARKET_ENVIRONMENT_STORAGE_MIGRATION.version,),
            ).fetchone()
            existing_objects = {
                (str(item[0]), str(item[1]))
                for item in self._connection.execute(
                    "SELECT type, name FROM sqlite_master WHERE name IN ("
                    "'market_environment_snapshots', "
                    "'market_index_feature_snapshots', "
                    "'idx_market_environment_as_of', "
                    "'idx_market_index_feature_key_as_of'"
                    ")"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise RepositoryStateError(
                "数据库未完成市场环境仓储迁移版本3"
            ) from exc
        expected = (
            MARKET_ENVIRONMENT_STORAGE_MIGRATION.name,
            MARKET_ENVIRONMENT_STORAGE_MIGRATION.checksum,
        )
        if row is None or tuple(row) != expected:
            raise RepositoryStateError(
                "数据库未完成市场环境仓储迁移版本3或迁移记录已漂移"
            )
        required_objects = {
            ("table", "market_environment_snapshots"),
            ("table", "market_index_feature_snapshots"),
            ("index", "idx_market_environment_as_of"),
            ("index", "idx_market_index_feature_key_as_of"),
        }
        if existing_objects != required_objects:
            raise RepositoryStateError("数据库缺少市场环境仓储结构")

    @staticmethod
    def _classification_key(
        classification_system: str,
        release_period: str,
    ) -> Tuple[str, str]:
        classification_system = classification_system.strip()
        release_period = release_period.strip()
        if not classification_system:
            raise ValueError("classification_system不能为空")
        if not release_period:
            raise ValueError("release_period不能为空")
        return classification_system, release_period

    @contextmanager
    def _transaction(self):
        if self._connection.in_transaction:
            raise RepositoryStateError("仓储写入前连接不能处于未提交事务中")
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except RadarRepositoryError:
            self._connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            self._connection.rollback()
            raise RepositoryWriteError(
                f"雷达仓储写入失败并已回滚：{type(exc).__name__}"
            ) from exc
        except Exception:
            self._connection.rollback()
            raise

    def start_run(
        self,
        radar_run_id: str,
        as_of: datetime,
        *,
        started_at: Optional[datetime] = None,
        shadow_mode: bool = True,
        rule_version_id: Optional[str] = None,
    ) -> bool:
        """创建运行中记录；完全相同的重试不重复写入。"""
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        as_of_text = _datetime_text(as_of, "as_of")
        started_at_text = _datetime_text(
            started_at or self._clock(),
            "started_at",
        )
        created_at_text = self._clock_text()
        rule_version_id = rule_version_id.strip() if rule_version_id else None
        expected = (
            as_of_text,
            "running",
            int(shadow_mode),
            rule_version_id,
            started_at_text,
            None,
        )

        with self._transaction():
            existing = self._connection.execute(
                "SELECT as_of, status, shadow_mode, rule_version_id, "
                "started_at, completed_at FROM radar_runs "
                "WHERE radar_run_id=?",
                (radar_run_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == expected:
                    return False
                raise RepositoryConflictError(
                    f"雷达运行{radar_run_id}已存在且内容不同"
                )

            self._connection.execute(
                "INSERT INTO radar_runs ("
                "radar_run_id, as_of, status, shadow_mode, rule_version_id, "
                "started_at, created_at"
                ") VALUES (?, ?, 'running', ?, ?, ?, ?)",
                (
                    radar_run_id,
                    as_of_text,
                    int(shadow_mode),
                    rule_version_id,
                    started_at_text,
                    created_at_text,
                ),
            )
        return True

    def complete_run(
        self,
        radar_run_id: str,
        *,
        status: str,
        completed_at: Optional[datetime] = None,
        expected_stock_count: Optional[int] = None,
        returned_stock_count: Optional[int] = None,
        expected_etf_count: Optional[int] = None,
        returned_etf_count: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> bool:
        """完成运行并由真实计数计算覆盖率；相同重试不重复更新。"""
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        if status not in FINAL_RUN_STATUSES:
            raise ValueError("运行完成状态只能是succeeded、degraded或failed")
        completed_at_text = _datetime_text(
            completed_at or self._clock(),
            "completed_at",
        )
        stock_coverage = _coverage(
            expected_stock_count,
            returned_stock_count,
            "证券",
        )
        etf_coverage = _coverage(
            expected_etf_count,
            returned_etf_count,
            "ETF",
        )
        error_code = error_code.strip() if error_code else None
        requested_final = (
            status,
            expected_stock_count,
            returned_stock_count,
            stock_coverage,
            expected_etf_count,
            returned_etf_count,
            etf_coverage,
            completed_at_text,
            error_code,
        )

        with self._transaction():
            existing = self._connection.execute(
                "SELECT status, expected_stock_count, returned_stock_count, "
                "stock_coverage, expected_etf_count, returned_etf_count, "
                "etf_coverage, completed_at, error_code, started_at "
                "FROM radar_runs WHERE radar_run_id=?",
                (radar_run_id,),
            ).fetchone()
            if existing is None:
                raise RepositoryConflictError(
                    f"雷达运行{radar_run_id}不存在，不能完成"
                )
            if existing[0] in FINAL_RUN_STATUSES:
                if tuple(existing[:9]) == requested_final:
                    return False
                raise RepositoryConflictError(
                    f"雷达运行{radar_run_id}已经完成且结果不同"
                )
            if existing[0] not in {"pending", "running"}:
                raise RepositoryStateError(
                    f"雷达运行{radar_run_id}当前状态不能完成"
                )
            if _parse_datetime(completed_at_text, "completed_at") < _parse_datetime(
                existing[9],
                "started_at",
            ):
                raise ValueError("completed_at不能早于started_at")

            cursor = self._connection.execute(
                "UPDATE radar_runs SET "
                "status=?, expected_stock_count=?, returned_stock_count=?, "
                "stock_coverage=?, expected_etf_count=?, returned_etf_count=?, "
                "etf_coverage=?, completed_at=?, error_code=? "
                "WHERE radar_run_id=? AND status IN ('pending', 'running')",
                requested_final + (radar_run_id,),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflictError(
                    f"雷达运行{radar_run_id}状态在完成时发生变化"
                )
        return True

    def record_source_status(
        self,
        meta: RadarBatchMeta,
        health: SourceHealthResult,
        *,
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """保存来源批次健康结果；相同批次的完全相同重试保持幂等。"""
        fields_json = _canonical_json(meta.required_field_coverage)
        issues_payload = {
            "allowsNewState": health.allows_new_state,
            "healthReasons": list(health.reasons),
            "ageSeconds": health.age_seconds,
            "sourceIssues": [
                issue.model_dump(mode="json", by_alias=True)
                for issue in meta.issues
            ],
        }
        if diagnostics is not None:
            if not isinstance(diagnostics, dict):
                raise TypeError("diagnostics必须是字典")
            issues_payload["diagnostics"] = diagnostics
        issues_json = _canonical_json(issues_payload)
        values = (
            _datetime_text(meta.as_of, "as_of"),
            _datetime_text(meta.source_time, "source_time")
            if meta.source_time is not None
            else None,
            _datetime_text(meta.fetched_at, "fetched_at"),
            health.status.value,
            meta.expected_count,
            meta.returned_count,
            meta.row_coverage,
            fields_json,
            issues_json,
        )

        with self._transaction():
            run = self._connection.execute(
                "SELECT as_of FROM radar_runs WHERE radar_run_id=?",
                (meta.radar_run_id,),
            ).fetchone()
            if run is None:
                raise RepositoryConflictError(
                    f"雷达运行{meta.radar_run_id}不存在，不能保存来源状态"
                )
            if _parse_datetime(run[0], "运行as_of") != _aware_datetime(
                meta.as_of,
                "批次as_of",
            ).astimezone(UTC):
                raise RepositoryConflictError(
                    "来源批次asOf与所属雷达运行不一致"
                )
            existing = self._connection.execute(
                "SELECT as_of, source_time, fetched_at, status, expected_count, "
                "returned_count, row_coverage, required_field_coverage_json, "
                "issues_json FROM radar_source_status "
                "WHERE radar_run_id=? AND batch_id=? AND source=?",
                (meta.radar_run_id, meta.batch_id, meta.source),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == values:
                    return False
                raise RepositoryConflictError(
                    "同一雷达运行、批次和来源已保存不同健康结果"
                )

            self._connection.execute(
                "INSERT INTO radar_source_status ("
                "radar_run_id, batch_id, source, as_of, source_time, fetched_at, "
                "status, expected_count, returned_count, row_coverage, "
                "required_field_coverage_json, issues_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    meta.radar_run_id,
                    meta.batch_id,
                    meta.source,
                ) + values + (self._clock_text(),),
            )
        return True

    def record_market_feature_snapshot(
        self,
        snapshot: MarketFeatureSnapshot,
    ) -> HistoryWriteResult:
        """事务保存一轮市场聚合及其指数原始特征。"""
        if not isinstance(snapshot, MarketFeatureSnapshot):
            raise TypeError("snapshot必须是MarketFeatureSnapshot")
        self._require_market_storage()
        indices = tuple(snapshot.indices)
        if len(indices) != snapshot.index_completeness.returned_count:
            raise RepositoryConflictError(
                "指数特征数量与市场聚合返回数量不一致"
            )
        index_keys = tuple(index.index_key.value for index in indices)
        if len(set(index_keys)) != len(index_keys):
            raise RepositoryConflictError("同一市场快照包含重复指数身份")

        environment_columns = (
            "radar_run_id",
            "index_batch_id",
            "quote_batch_id",
            "as_of",
            "source_time",
            "fetched_at",
            "index_expected_count",
            "index_returned_count",
            "index_valid_count",
            "index_row_coverage",
            "index_required_field_coverage_json",
            "index_is_complete",
            "index_reasons_json",
            "breadth_expected_count",
            "breadth_returned_count",
            "breadth_valid_count",
            "breadth_row_coverage",
            "breadth_required_field_coverage_json",
            "breadth_is_complete",
            "breadth_reasons_json",
            "advancers",
            "decliners",
            "flat",
            "unavailable",
            "turnover_raw_value",
            "turnover_contributing_count",
            "turnover_unit_status",
            "turnover_expected_count",
            "turnover_returned_count",
            "turnover_valid_count",
            "turnover_row_coverage",
            "turnover_required_field_coverage_json",
            "turnover_is_complete",
            "turnover_reasons_json",
            "excluded_etf_count",
            "duplicate_symbol_count",
            "unknown_symbol_count",
            "evidence_summary_json",
        )
        index_columns = (
            "radar_run_id",
            "as_of",
            "index_key",
            "symbol",
            "name",
            "exchange",
            "source_symbol",
            "source_time",
            "fetched_at",
            "price",
            "change_percent",
            "source",
            "missing_fields_json",
        )
        as_of = _aware_datetime(snapshot.as_of, "as_of").astimezone(UTC)
        as_of_text = as_of.isoformat(timespec="microseconds")
        source_time_text = (
            _datetime_text(snapshot.source_time, "source_time")
            if snapshot.source_time is not None
            else None
        )
        fetched_at_text = _datetime_text(snapshot.fetched_at, "fetched_at")
        evidence_summary = {
            "indexBatchId": snapshot.index_batch_id,
            "quoteBatchId": snapshot.quote_batch_id,
            "indexCount": len(indices),
            "indexKeysSha256": _symbols_checksum(tuple(sorted(index_keys))),
            "duplicateSymbolsSha256": _symbols_checksum(
                tuple(sorted(snapshot.duplicate_symbols))
            ),
            "unknownSymbolsSha256": _symbols_checksum(
                tuple(sorted(snapshot.unknown_symbols))
            ),
        }
        environment_values = (
            snapshot.radar_run_id,
            snapshot.index_batch_id,
            snapshot.quote_batch_id,
            as_of_text,
            source_time_text,
            fetched_at_text,
            snapshot.index_completeness.expected_count,
            snapshot.index_completeness.returned_count,
            snapshot.index_completeness.valid_count,
            snapshot.index_completeness.row_coverage,
            _canonical_json(
                snapshot.index_completeness.required_field_coverage
            ),
            int(snapshot.index_completeness.is_complete),
            _canonical_json(list(snapshot.index_completeness.reasons)),
            snapshot.breadth.completeness.expected_count,
            snapshot.breadth.completeness.returned_count,
            snapshot.breadth.completeness.valid_count,
            snapshot.breadth.completeness.row_coverage,
            _canonical_json(
                snapshot.breadth.completeness.required_field_coverage
            ),
            int(snapshot.breadth.completeness.is_complete),
            _canonical_json(list(snapshot.breadth.completeness.reasons)),
            snapshot.breadth.advancers,
            snapshot.breadth.decliners,
            snapshot.breadth.flat,
            snapshot.breadth.unavailable,
            snapshot.turnover.raw_value,
            snapshot.turnover.contributing_count,
            snapshot.turnover.unit_status.value,
            snapshot.turnover.completeness.expected_count,
            snapshot.turnover.completeness.returned_count,
            snapshot.turnover.completeness.valid_count,
            snapshot.turnover.completeness.row_coverage,
            _canonical_json(
                snapshot.turnover.completeness.required_field_coverage
            ),
            int(snapshot.turnover.completeness.is_complete),
            _canonical_json(list(snapshot.turnover.reasons)),
            snapshot.excluded_etf_count,
            len(snapshot.duplicate_symbols),
            len(snapshot.unknown_symbols),
            _canonical_json(evidence_summary),
        )
        created_at = self._clock_text()
        inserted = 0
        unchanged = 0

        with self._transaction():
            run_row = self._connection.execute(
                "SELECT as_of FROM radar_runs WHERE radar_run_id=?",
                (snapshot.radar_run_id,),
            ).fetchone()
            if run_row is None:
                raise RepositoryConflictError(
                    f"雷达运行{snapshot.radar_run_id}不存在，不能保存市场特征"
                )
            if _parse_datetime(str(run_row[0]), "运行as_of") != as_of:
                raise RepositoryConflictError(
                    "市场特征asOf与所属雷达运行不一致"
                )

            existing_environment = self._connection.execute(
                f"SELECT {', '.join(environment_columns)} "
                "FROM market_environment_snapshots WHERE radar_run_id=?",
                (snapshot.radar_run_id,),
            ).fetchone()
            if existing_environment is None:
                insert_columns = environment_columns + ("created_at",)
                self._connection.execute(
                    "INSERT INTO market_environment_snapshots "
                    f"({', '.join(insert_columns)}) VALUES ("
                    + ", ".join("?" for _ in insert_columns)
                    + ")",
                    environment_values + (created_at,),
                )
                inserted += 1
            elif tuple(existing_environment) == environment_values:
                unchanged += 1
            else:
                raise RepositoryConflictError(
                    "同一雷达运行已保存不同市场环境聚合"
                )

            for index in indices:
                index_values = (
                    snapshot.radar_run_id,
                    as_of_text,
                    index.index_key.value,
                    index.symbol,
                    index.name,
                    index.exchange,
                    index.source_symbol,
                    _datetime_text(index.source_time, "指数source_time")
                    if index.source_time is not None
                    else None,
                    _datetime_text(index.fetched_at, "指数fetched_at"),
                    index.price,
                    index.change_percent,
                    index.source,
                    _canonical_json(list(index.missing_fields())),
                )
                existing_index = self._connection.execute(
                    f"SELECT {', '.join(index_columns)} "
                    "FROM market_index_feature_snapshots "
                    "WHERE radar_run_id=? AND index_key=?",
                    (snapshot.radar_run_id, index.index_key.value),
                ).fetchone()
                if existing_index is not None:
                    if tuple(existing_index) == index_values:
                        unchanged += 1
                        continue
                    raise RepositoryConflictError(
                        "同一雷达运行和指数已保存不同原始特征"
                    )
                insert_columns = index_columns + ("created_at",)
                self._connection.execute(
                    "INSERT INTO market_index_feature_snapshots "
                    f"({', '.join(insert_columns)}) VALUES ("
                    + ", ".join("?" for _ in insert_columns)
                    + ")",
                    index_values + (created_at,),
                )
                inserted += 1

        return HistoryWriteResult(inserted=inserted, unchanged=unchanged)

    def get_market_feature_row(
        self,
        radar_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        """只读返回一轮市场聚合和指数原始特征。"""
        self._require_market_storage()
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        environment_columns = (
            "radar_run_id",
            "index_batch_id",
            "quote_batch_id",
            "as_of",
            "source_time",
            "fetched_at",
            "index_expected_count",
            "index_returned_count",
            "index_valid_count",
            "index_row_coverage",
            "index_required_field_coverage_json",
            "index_is_complete",
            "index_reasons_json",
            "breadth_expected_count",
            "breadth_returned_count",
            "breadth_valid_count",
            "breadth_row_coverage",
            "breadth_required_field_coverage_json",
            "breadth_is_complete",
            "breadth_reasons_json",
            "advancers",
            "decliners",
            "flat",
            "unavailable",
            "turnover_raw_value",
            "turnover_contributing_count",
            "turnover_unit_status",
            "turnover_expected_count",
            "turnover_returned_count",
            "turnover_valid_count",
            "turnover_row_coverage",
            "turnover_required_field_coverage_json",
            "turnover_is_complete",
            "turnover_reasons_json",
            "excluded_etf_count",
            "duplicate_symbol_count",
            "unknown_symbol_count",
            "evidence_summary_json",
        )
        row = self._connection.execute(
            f"SELECT {', '.join(environment_columns)} "
            "FROM market_environment_snapshots WHERE radar_run_id=?",
            (radar_run_id,),
        ).fetchone()
        if row is None:
            return None
        stored = dict(zip(environment_columns, row))
        index_rows = self._connection.execute(
            "SELECT index_key, symbol, name, exchange, source_symbol, "
            "source_time, fetched_at, price, change_percent, source, "
            "missing_fields_json FROM market_index_feature_snapshots "
            "WHERE radar_run_id=? ORDER BY CASE index_key "
            "WHEN 'sse_composite' THEN 1 WHEN 'szse_component' THEN 2 "
            "WHEN 'chinext' THEN 3 WHEN 'star50' THEN 4 END",
            (radar_run_id,),
        ).fetchall()

        def completeness(prefix: str) -> Dict[str, Any]:
            return {
                "expectedCount": stored[f"{prefix}_expected_count"],
                "returnedCount": stored[f"{prefix}_returned_count"],
                "validCount": stored[f"{prefix}_valid_count"],
                "rowCoverage": stored[f"{prefix}_row_coverage"],
                "requiredFieldCoverage": _load_json(
                    stored[f"{prefix}_required_field_coverage_json"],
                    f"市场{prefix}字段覆盖率",
                    dict,
                ),
                "isComplete": bool(stored[f"{prefix}_is_complete"]),
                "reasons": tuple(_load_json(
                    stored[f"{prefix}_reasons_json"],
                    f"市场{prefix}原因",
                    list,
                )),
            }

        return {
            "radarRunId": stored["radar_run_id"],
            "indexBatchId": stored["index_batch_id"],
            "quoteBatchId": stored["quote_batch_id"],
            "asOf": _parse_datetime(str(stored["as_of"]), "市场as_of"),
            "sourceTime": (
                _parse_datetime(
                    str(stored["source_time"]),
                    "市场source_time",
                )
                if stored["source_time"] is not None
                else None
            ),
            "fetchedAt": _parse_datetime(
                str(stored["fetched_at"]),
                "市场fetched_at",
            ),
            "indexCompleteness": completeness("index"),
            "breadth": {
                "advancers": stored["advancers"],
                "decliners": stored["decliners"],
                "flat": stored["flat"],
                "unavailable": stored["unavailable"],
                "completeness": completeness("breadth"),
            },
            "turnover": {
                "rawValue": stored["turnover_raw_value"],
                "contributingCount": stored["turnover_contributing_count"],
                "unitStatus": stored["turnover_unit_status"],
                "completeness": completeness("turnover"),
                "reasons": tuple(_load_json(
                    stored["turnover_reasons_json"],
                    "市场成交额原因",
                    list,
                )),
            },
            "excludedEtfCount": stored["excluded_etf_count"],
            "duplicateSymbolCount": stored["duplicate_symbol_count"],
            "unknownSymbolCount": stored["unknown_symbol_count"],
            "evidenceSummary": _load_json(
                stored["evidence_summary_json"],
                "市场冻结证据摘要",
                dict,
            ),
            "indices": tuple({
                "indexKey": item[0],
                "symbol": item[1],
                "name": item[2],
                "exchange": item[3],
                "sourceSymbol": item[4],
                "sourceTime": (
                    _parse_datetime(str(item[5]), "指数source_time")
                    if item[5] is not None
                    else None
                ),
                "fetchedAt": _parse_datetime(
                    str(item[6]),
                    "指数fetched_at",
                ),
                "price": item[7],
                "changePercent": item[8],
                "source": item[9],
                "missingFields": tuple(_load_json(
                    item[10],
                    "指数缺失字段",
                    list,
                )),
            } for item in index_rows),
        }

    def get_latest_market_feature_row(self) -> Optional[Dict[str, Any]]:
        """读取最近一轮已持久化市场聚合，不把失败运行冒充快照。"""
        self._require_market_storage()
        row = self._connection.execute(
            "SELECT radar_run_id FROM market_environment_snapshots "
            "ORDER BY as_of DESC, radar_run_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self.get_market_feature_row(str(row[0]))

    def record_industry_classification(
        self,
        snapshot: IndustryClassificationSnapshot,
    ) -> HistoryWriteResult:
        """事务保存一个官方行业发布版本及其分类记录。"""
        if not isinstance(snapshot, IndustryClassificationSnapshot):
            raise TypeError("snapshot必须是IndustryClassificationSnapshot")
        self._require_industry_storage()
        release = snapshot.release
        if release is None:
            raise RepositoryStateError("行业分类快照没有可持久化发布版本")
        records = tuple(snapshot.records)
        for record in records:
            if record.classification_system != release.classification_system:
                raise RepositoryConflictError("行业记录与发布版本的分类体系不一致")
            if record.release_period != release.release_period:
                raise RepositoryConflictError("行业记录与发布版本的发布期不一致")

        release_id = _industry_release_id(release)
        release_columns = (
            "industry_release_id",
            "classification_system",
            "scheme_version",
            "release_period",
            "source_page_title",
            "publication_page_url",
            "document_url",
            "document_sha256",
            "published_date",
            "first_observed_at",
            "fetched_at",
            "knowledge_effective_from",
            "knowledge_effective_to",
            "classification_start_date",
            "history_status",
            "source_record_count",
            "unique_source_symbol_count",
            "required_field_coverage_json",
        )
        release_values = (
            release_id,
            release.classification_system,
            release.scheme_version,
            release.release_period,
            release.source_page_title,
            release.publication_page_url,
            release.document_url,
            release.document_sha256,
            release.published_date.isoformat(),
            _datetime_text(release.first_observed_at, "first_observed_at"),
            _datetime_text(release.fetched_at, "fetched_at"),
            _datetime_text(
                release.knowledge_effective_from,
                "knowledge_effective_from",
            ),
            _datetime_text(
                release.knowledge_effective_to,
                "knowledge_effective_to",
            )
            if release.knowledge_effective_to is not None
            else None,
            release.classification_start_date.isoformat(),
            release.history_status.value,
            release.source_record_count,
            release.unique_source_symbol_count,
            _canonical_json(release.required_field_coverage),
        )
        record_columns = (
            "industry_release_id",
            "source_symbol",
            "source_name",
            "security_identity",
            "identity_status",
            "category_code",
            "category_name",
            "division_code",
            "division_name",
            "manufacturing_subclass_code",
            "manufacturing_subclass_name",
            "record_status",
            "issue_codes_json",
            "source_fields_json",
        )
        record_values = tuple((
            release_id,
            record.source_symbol,
            record.source_name,
            record.security_identity,
            record.identity_status.value,
            record.category_code,
            record.category_name,
            record.division_code,
            record.division_name,
            record.manufacturing_subclass_code,
            record.manufacturing_subclass_name,
            record.record_status.value,
            _canonical_json(list(record.issue_codes)),
            _canonical_json(record.source_fields),
        ) for record in records)
        inserted = 0
        unchanged = 0
        created_at = self._clock_text()

        with self._transaction():
            existing_release = self._connection.execute(
                f"SELECT {', '.join(release_columns)} "
                "FROM industry_classification_releases "
                "WHERE classification_system=? AND release_period=?",
                (release.classification_system, release.release_period),
            ).fetchone()
            if existing_release is None:
                columns = release_columns + ("created_at",)
                self._connection.execute(
                    f"INSERT INTO industry_classification_releases "
                    f"({', '.join(columns)}) VALUES ("
                    + ", ".join("?" for _ in columns)
                    + ")",
                    release_values + (created_at,),
                )
                inserted += 1
            elif tuple(existing_release) == release_values:
                unchanged += 1
            else:
                raise RepositoryConflictError(
                    "同一分类体系和发布期已保存不同发布版本"
                )

            for values in record_values:
                existing_record = self._connection.execute(
                    f"SELECT {', '.join(record_columns)} "
                    "FROM industry_classification_records "
                    "WHERE industry_release_id=? AND source_symbol=?",
                    (release_id, values[1]),
                ).fetchone()
                if existing_record is not None:
                    if tuple(existing_record) == values:
                        unchanged += 1
                        continue
                    raise RepositoryConflictError(
                        f"行业发布版本中的来源代码{values[1]}已保存不同记录"
                    )
                columns = record_columns + ("created_at",)
                self._connection.execute(
                    f"INSERT INTO industry_classification_records "
                    f"({', '.join(columns)}) VALUES ("
                    + ", ".join("?" for _ in columns)
                    + ")",
                    values + (created_at,),
                )
                inserted += 1

        return HistoryWriteResult(inserted=inserted, unchanged=unchanged)

    def get_industry_classification_release(
        self,
        classification_system: str,
        release_period: str,
    ) -> Optional[IndustryClassificationRelease]:
        """按分类体系和发布期只读返回官方行业版本。"""
        self._require_industry_storage()
        classification_system, release_period = self._classification_key(
            classification_system,
            release_period,
        )
        row = self._connection.execute(
            "SELECT scheme_version, source_page_title, publication_page_url, "
            "document_url, document_sha256, published_date, first_observed_at, "
            "fetched_at, knowledge_effective_from, knowledge_effective_to, "
            "classification_start_date, history_status, source_record_count, "
            "unique_source_symbol_count, required_field_coverage_json "
            "FROM industry_classification_releases "
            "WHERE classification_system=? AND release_period=?",
            (classification_system, release_period),
        ).fetchone()
        if row is None:
            return None
        return IndustryClassificationRelease(
            classificationSystem=classification_system,
            schemeVersion=row[0],
            releasePeriod=release_period,
            sourcePageTitle=row[1],
            publicationPageUrl=row[2],
            documentUrl=row[3],
            documentSha256=row[4],
            publishedDate=row[5],
            firstObservedAt=row[6],
            fetchedAt=row[7],
            knowledgeEffectiveFrom=row[8],
            knowledgeEffectiveTo=row[9],
            classificationStartDate=row[10],
            historyStatus=row[11],
            sourceRecordCount=row[12],
            uniqueSourceSymbolCount=row[13],
            requiredFieldCoverage=_load_json(
                row[14],
                "行业版本字段覆盖率",
                dict,
            ),
        )

    def list_industry_classification_records(
        self,
        classification_system: str,
        release_period: str,
    ) -> Tuple[IndustryClassificationRecord, ...]:
        """按发布版本只读返回原始行业分类记录。"""
        self._require_industry_storage()
        classification_system, release_period = self._classification_key(
            classification_system,
            release_period,
        )
        release_row = self._connection.execute(
            "SELECT industry_release_id FROM industry_classification_releases "
            "WHERE classification_system=? AND release_period=?",
            (classification_system, release_period),
        ).fetchone()
        if release_row is None:
            return ()
        rows = self._connection.execute(
            "SELECT source_symbol, source_name, security_identity, "
            "identity_status, category_code, category_name, division_code, "
            "division_name, manufacturing_subclass_code, "
            "manufacturing_subclass_name, record_status, issue_codes_json, "
            "source_fields_json FROM industry_classification_records "
            "WHERE industry_release_id=? ORDER BY source_symbol",
            (release_row[0],),
        ).fetchall()
        return tuple(IndustryClassificationRecord(
            classificationSystem=classification_system,
            releasePeriod=release_period,
            sourceSymbol=row[0],
            sourceName=row[1],
            securityIdentity=row[2],
            identityStatus=row[3],
            categoryCode=row[4],
            categoryName=row[5],
            divisionCode=row[6],
            divisionName=row[7],
            manufacturingSubclassCode=row[8],
            manufacturingSubclassName=row[9],
            recordStatus=row[10],
            issueCodes=_load_json(row[11], "行业记录问题代码", list),
            sourceFields=_load_json(row[12], "行业记录原始字段", dict),
        ) for row in rows)

    def record_sector_feature_batch(
        self,
        batch: SectorFeatureBatch,
    ) -> HistoryWriteResult:
        """事务保存一轮按大类聚合的当期原始特征。"""
        if not isinstance(batch, SectorFeatureBatch):
            raise TypeError("batch必须是SectorFeatureBatch")
        self._require_industry_storage()
        for sector in batch.sectors:
            if sector.classification_system != batch.classification_system:
                raise RepositoryConflictError("行业特征与批次分类体系不一致")
            if sector.release_period != batch.release_period:
                raise RepositoryConflictError("行业特征与批次发布期不一致")

        columns = (
            "radar_run_id",
            "industry_release_id",
            "classification_batch_id",
            "quote_batch_id",
            "category_code",
            "category_name",
            "division_code",
            "division_name",
            "as_of",
            "source_time",
            "fetched_at",
            "classification_mapping_coverage",
            "mapped_constituent_count",
            "unconfirmed_stock_count",
            "expected_count",
            "returned_count",
            "fresh_count",
            "valid_return_count",
            "valid_market_cap_count",
            "valid_turnover_count",
            "row_coverage",
            "required_field_coverage_json",
            "is_complete",
            "equal_return",
            "cap_weighted_return",
            "ex_top_return",
            "top_contributor_symbol",
            "top_contribution_percent_points",
            "market_cap_basis",
            "market_cap_unit_status",
            "advancers",
            "decliners",
            "flat",
            "unavailable",
            "up_ratio",
            "turnover_raw_value",
            "turnover_contributing_count",
            "turnover_unit_status",
            "shadow_usable",
            "reasons_json",
            "evidence_summary_json",
        )
        as_of = _aware_datetime(batch.as_of, "as_of").astimezone(UTC)
        as_of_text = as_of.isoformat(timespec="microseconds")
        source_time_text = (
            _datetime_text(batch.source_time, "source_time")
            if batch.source_time is not None
            else None
        )
        fetched_at_text = _datetime_text(batch.fetched_at, "fetched_at")
        created_at = self._clock_text()
        inserted = 0
        unchanged = 0

        with self._transaction():
            release_row = self._connection.execute(
                "SELECT industry_release_id, knowledge_effective_from, "
                "knowledge_effective_to FROM industry_classification_releases "
                "WHERE classification_system=? AND release_period=? "
                "AND document_sha256=?",
                (
                    batch.classification_system,
                    batch.release_period,
                    batch.classification_document_sha256,
                ),
            ).fetchone()
            if release_row is None:
                raise RepositoryConflictError(
                    "行业特征引用的发布版本不存在或文档哈希不一致"
                )
            knowledge_from = _parse_datetime(
                str(release_row[1]),
                "knowledge_effective_from",
            )
            knowledge_to = (
                _parse_datetime(str(release_row[2]), "knowledge_effective_to")
                if release_row[2] is not None
                else None
            )
            if as_of < knowledge_from or (
                knowledge_to is not None and as_of >= knowledge_to
            ):
                raise HistoryAsOfError(
                    "行业特征asOf不在分类版本知识生效区间内"
                )

            run_row = self._connection.execute(
                "SELECT as_of FROM radar_runs WHERE radar_run_id=?",
                (batch.radar_run_id,),
            ).fetchone()
            if run_row is None:
                raise RepositoryConflictError(
                    f"雷达运行{batch.radar_run_id}不存在，不能保存行业特征"
                )
            if _parse_datetime(str(run_row[0]), "运行as_of") != as_of:
                raise RepositoryConflictError(
                    "行业特征asOf与所属雷达运行不一致"
                )

            release_id = str(release_row[0])
            for sector in batch.sectors:
                evidence_summary = {
                    "classificationDocumentSha256": (
                        batch.classification_document_sha256
                    ),
                    "classificationBatchId": batch.classification_batch_id,
                    "quoteBatchId": batch.quote_batch_id,
                    "constituentCount": len(sector.constituent_symbols),
                    "constituentSymbolsSha256": _symbols_checksum(
                        sector.constituent_symbols
                    ),
                }
                values = (
                    batch.radar_run_id,
                    release_id,
                    batch.classification_batch_id,
                    batch.quote_batch_id,
                    sector.category_code,
                    sector.category_name,
                    sector.division_code,
                    sector.division_name,
                    as_of_text,
                    source_time_text,
                    fetched_at_text,
                    batch.classification_mapping_coverage,
                    batch.mapped_constituent_count,
                    batch.unconfirmed_stock_count,
                    sector.completeness.expected_count,
                    sector.completeness.returned_count,
                    sector.completeness.fresh_count,
                    sector.completeness.valid_return_count,
                    sector.completeness.valid_market_cap_count,
                    sector.completeness.valid_turnover_count,
                    sector.completeness.row_coverage,
                    _canonical_json(
                        sector.completeness.required_field_coverage
                    ),
                    int(sector.completeness.is_complete),
                    sector.returns.equal_return.raw_value,
                    sector.returns.cap_weighted_return.raw_value,
                    sector.returns.ex_top_return.raw_value,
                    sector.returns.top_contributor_symbol,
                    sector.returns.top_contribution_percent_points,
                    sector.returns.market_cap_basis,
                    sector.returns.market_cap_unit_status.value,
                    sector.breadth.advancers,
                    sector.breadth.decliners,
                    sector.breadth.flat,
                    sector.breadth.unavailable,
                    sector.breadth.up_ratio.raw_value,
                    sector.turnover.raw_value,
                    sector.turnover.contributing_count,
                    sector.turnover.unit_status.value,
                    int(sector.shadow_usable),
                    _canonical_json(list(sector.reasons)),
                    _canonical_json(evidence_summary),
                )
                existing = self._connection.execute(
                    f"SELECT {', '.join(columns)} "
                    "FROM sector_feature_snapshots "
                    "WHERE radar_run_id=? AND division_code=?",
                    (batch.radar_run_id, sector.division_code),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) == values:
                        unchanged += 1
                        continue
                    raise RepositoryConflictError(
                        "同一雷达运行和行业大类已保存不同当期特征"
                    )
                insert_columns = columns + ("created_at",)
                self._connection.execute(
                    f"INSERT INTO sector_feature_snapshots "
                    f"({', '.join(insert_columns)}) VALUES ("
                    + ", ".join("?" for _ in insert_columns)
                    + ")",
                    values + (created_at,),
                )
                inserted += 1

        return HistoryWriteResult(inserted=inserted, unchanged=unchanged)

    def list_sector_feature_rows(
        self,
        radar_run_id: str,
    ) -> Tuple[Dict[str, Any], ...]:
        """只读返回一轮已冻结的行业聚合字段，不还原逐证券行情。"""
        self._require_industry_storage()
        radar_run_id = radar_run_id.strip()
        if not radar_run_id:
            raise ValueError("radar_run_id不能为空")
        rows = self._connection.execute(
            "SELECT radar_run_id, industry_release_id, classification_batch_id, "
            "quote_batch_id, category_code, category_name, division_code, "
            "division_name, as_of, source_time, fetched_at, "
            "classification_mapping_coverage, mapped_constituent_count, "
            "unconfirmed_stock_count, expected_count, returned_count, "
            "fresh_count, valid_return_count, valid_market_cap_count, "
            "valid_turnover_count, row_coverage, required_field_coverage_json, "
            "is_complete, equal_return, cap_weighted_return, ex_top_return, "
            "top_contributor_symbol, top_contribution_percent_points, "
            "market_cap_basis, market_cap_unit_status, advancers, decliners, "
            "flat, unavailable, up_ratio, turnover_raw_value, "
            "turnover_contributing_count, turnover_unit_status, shadow_usable, "
            "reasons_json, evidence_summary_json "
            "FROM sector_feature_snapshots WHERE radar_run_id=? "
            "ORDER BY division_code",
            (radar_run_id,),
        ).fetchall()
        return tuple({
            "radarRunId": row[0],
            "industryReleaseId": row[1],
            "classificationBatchId": row[2],
            "quoteBatchId": row[3],
            "categoryCode": row[4],
            "categoryName": row[5],
            "divisionCode": row[6],
            "divisionName": row[7],
            "asOf": _parse_datetime(str(row[8]), "行业特征as_of"),
            "sourceTime": (
                _parse_datetime(str(row[9]), "行业特征source_time")
                if row[9] is not None
                else None
            ),
            "fetchedAt": _parse_datetime(str(row[10]), "行业特征fetched_at"),
            "classificationMappingCoverage": row[11],
            "mappedConstituentCount": row[12],
            "unconfirmedStockCount": row[13],
            "expectedCount": row[14],
            "returnedCount": row[15],
            "freshCount": row[16],
            "validReturnCount": row[17],
            "validMarketCapCount": row[18],
            "validTurnoverCount": row[19],
            "rowCoverage": row[20],
            "requiredFieldCoverage": _load_json(
                row[21],
                "行业特征字段覆盖率",
                dict,
            ),
            "isComplete": bool(row[22]),
            "equalReturn": row[23],
            "capWeightedReturn": row[24],
            "exTopReturn": row[25],
            "topContributorSymbol": row[26],
            "topContributionPercentPoints": row[27],
            "marketCapBasis": row[28],
            "marketCapUnitStatus": row[29],
            "advancers": row[30],
            "decliners": row[31],
            "flat": row[32],
            "unavailable": row[33],
            "upRatio": row[34],
            "turnoverRawValue": row[35],
            "turnoverContributingCount": row[36],
            "turnoverUnitStatus": row[37],
            "shadowUsable": bool(row[38]),
            "reasons": tuple(_load_json(row[39], "行业特征原因", list)),
            "evidenceSummary": _load_json(
                row[40],
                "行业特征冻结证据摘要",
                dict,
            ),
        } for row in rows)

    def list_latest_sector_feature_rows(self) -> Tuple[Dict[str, Any], ...]:
        """读取最近一轮已持久化行业聚合，不跨运行拼接。"""
        self._require_industry_storage()
        row = self._connection.execute(
            "SELECT radar_run_id FROM sector_feature_snapshots "
            "ORDER BY as_of DESC, radar_run_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return ()
        return self.list_sector_feature_rows(str(row[0]))

    def sync_security_master(
        self,
        batch: SourceBatch[SecurityMasterRecord],
    ) -> HistoryWriteResult:
        records = self._prepare_records(
            batch,
            SecurityMasterRecord,
            self._security_values,
        )
        return self._sync_history(
            table="security_master_history",
            insert_columns=(
                "symbol",
                "name",
                "exchange",
                "board",
                "listing_date",
                "total_shares",
                "circulating_shares",
                "source_industry",
                "source_report_date",
                "announced_at",
                "source",
                "effective_from",
                "effective_to",
                "source_fields_json",
                "record_checksum",
                "created_at",
            ),
            effective_from=batch.meta.as_of,
            records=records,
        )

    def sync_etf_registry(
        self,
        batch: SourceBatch[EtfRegistryRecord],
    ) -> HistoryWriteResult:
        records = self._prepare_records(
            batch,
            EtfRegistryRecord,
            self._etf_values,
        )
        return self._sync_history(
            table="etf_product_registry",
            insert_columns=(
                "symbol",
                "name",
                "exchange",
                "source_type",
                "investment_type",
                "listing_date",
                "fund_shares",
                "manager",
                "sponsor",
                "custodian",
                "nav",
                "source_report_date",
                "announced_at",
                "source",
                "effective_from",
                "effective_to",
                "source_fields_json",
                "record_checksum",
                "created_at",
            ),
            effective_from=batch.meta.as_of,
            records=records,
        )

    def _prepare_records(
        self,
        batch: SourceBatch,
        record_type: Type,
        mapper: Callable[[Any], Tuple[Tuple[str, str], Tuple[Any, ...]]],
    ) -> Tuple[Tuple[Tuple[str, str], Tuple[Any, ...]], ...]:
        if batch.meta.returned_count != len(batch.items):
            raise RepositoryConflictError(
                "批次returnedCount与实际记录数量不一致"
            )
        prepared = []
        identities = set()
        for record in batch.items:
            if not isinstance(record, record_type):
                raise TypeError(
                    f"批次包含非{record_type.__name__}记录"
                )
            if (
                record.source_report_date is not None
                and record.source_report_date > batch.meta.as_of.date()
            ):
                raise HistoryAsOfError(
                    f"{record.symbol}来源报告日期晚于批次asOf"
                )
            identity, values = mapper(record)
            if identity in identities:
                raise RepositoryConflictError(
                    f"批次包含重复历史身份：{identity[0]} / {identity[1]}"
                )
            identities.add(identity)
            prepared.append((identity, values))
        return tuple(prepared)

    @staticmethod
    def _security_values(
        record: SecurityMasterRecord,
    ) -> Tuple[Tuple[str, str], Tuple[Any, ...]]:
        source_fields_json = _canonical_json(record.source_fields)
        facts = {
            "symbol": record.symbol,
            "name": record.name,
            "exchange": record.exchange,
            "board": record.board,
            "listingDate": _optional_date_text(record.listing_date),
            "totalShares": record.total_shares,
            "circulatingShares": record.circulating_shares,
            "sourceIndustry": record.source_industry,
            "sourceReportDate": _optional_date_text(record.source_report_date),
            "announcedAt": None,
            "source": record.source,
            "sourceFields": record.source_fields,
        }
        values = (
            record.symbol,
            record.name,
            record.exchange,
            record.board,
            _optional_date_text(record.listing_date),
            record.total_shares,
            record.circulating_shares,
            record.source_industry,
            _optional_date_text(record.source_report_date),
            None,
            record.source,
            source_fields_json,
            _checksum(facts),
            _datetime_text(record.fetched_at, "fetched_at"),
        )
        return (record.symbol, record.source), values

    @staticmethod
    def _etf_values(
        record: EtfRegistryRecord,
    ) -> Tuple[Tuple[str, str], Tuple[Any, ...]]:
        source_fields_json = _canonical_json(record.source_fields)
        facts = {
            "symbol": record.symbol,
            "name": record.name,
            "exchange": record.exchange,
            "sourceType": record.source_type,
            "investmentType": record.investment_type,
            "listingDate": _optional_date_text(record.listing_date),
            "fundShares": record.fund_shares,
            "manager": record.manager,
            "sponsor": record.sponsor,
            "custodian": record.custodian,
            "nav": record.nav,
            "sourceReportDate": _optional_date_text(record.source_report_date),
            "announcedAt": None,
            "source": record.source,
            "sourceFields": record.source_fields,
        }
        values = (
            record.symbol,
            record.name,
            record.exchange,
            record.source_type,
            record.investment_type,
            _optional_date_text(record.listing_date),
            record.fund_shares,
            record.manager,
            record.sponsor,
            record.custodian,
            record.nav,
            _optional_date_text(record.source_report_date),
            None,
            record.source,
            source_fields_json,
            _checksum(facts),
            _datetime_text(record.fetched_at, "fetched_at"),
        )
        return (record.symbol, record.source), values

    def _sync_history(
        self,
        *,
        table: str,
        insert_columns: Tuple[str, ...],
        effective_from: datetime,
        records: Iterable[Tuple[Tuple[str, str], Tuple[Any, ...]]],
    ) -> HistoryWriteResult:
        effective_datetime = _aware_datetime(effective_from, "as_of").astimezone(UTC)
        effective_text = effective_datetime.isoformat(timespec="microseconds")
        inserted = 0
        unchanged = 0
        closed = 0
        records = tuple(records)
        placeholders = ", ".join("?" for _ in insert_columns)
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(insert_columns)}) "
            f"VALUES ({placeholders})"
        )

        with self._transaction():
            current_rows = self._connection.execute(
                f"SELECT id, symbol, source, record_checksum, effective_from "
                f"FROM {table} WHERE effective_to IS NULL"
            ).fetchall()
            current = {
                (str(symbol), str(source)): (
                    int(row_id),
                    str(record_checksum),
                    str(current_effective_from),
                )
                for (
                    row_id,
                    symbol,
                    source,
                    record_checksum,
                    current_effective_from,
                ) in current_rows
            }

            for identity, values in records:
                record_checksum = values[-2]
                existing = current.get(identity)
                if existing is not None and existing[1] == record_checksum:
                    unchanged += 1
                    continue
                if existing is not None:
                    current_effective = _parse_datetime(
                        existing[2],
                        "effective_from",
                    )
                    if effective_datetime <= current_effective:
                        raise HistoryAsOfError(
                            f"{identity[0]} / {identity[1]}内容变化时asOf必须晚于当前版本"
                        )
                    cursor = self._connection.execute(
                        f"UPDATE {table} SET effective_to=? "
                        "WHERE id=? AND effective_to IS NULL",
                        (effective_text, existing[0]),
                    )
                    if cursor.rowcount != 1:
                        raise RepositoryConflictError(
                            f"{identity[0]} / {identity[1]}当前版本在写入时发生变化"
                        )
                    closed += 1

                before_source_fields = values[:-3]
                source_fields_json = values[-3]
                checksum_value = values[-2]
                created_at = values[-1]
                row_values = (
                    before_source_fields
                    + (effective_text, None, source_fields_json, checksum_value, created_at)
                )
                self._connection.execute(insert_sql, row_values)
                inserted += 1

        return HistoryWriteResult(
            inserted=inserted,
            unchanged=unchanged,
            closed=closed,
        )
