"""阶段6三级龙头临时仓储。

本模块只接受调用方显式提供且已完成阶段6可选迁移的连接，
不会查找数据库路径、自动迁移或连接生产数据库。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from radar.migrations import (
    LEADER_STORAGE_MIGRATION,
    STAGE6_RADAR_MIGRATIONS,
    validate_applied_migrations,
)
from radar.leader_state_machine import LeaderState, LeaderStateRecord
from radar.repository import (
    RadarRepositoryError,
    RepositoryConflictError,
    RepositoryStateError,
    RepositoryWriteError,
    _canonical_json,
    _datetime_text,
    _parse_datetime,
)


UTC = timezone.utc
LEADER_STATES = frozenset({"out", "preliminary", "candidate", "confirmed"})
LEADER_ACTIONS = frozenset({
    "blocked",
    "hold",
    "enter",
    "upgrade",
    "downgrade",
    "remove",
})
LEADER_DATA_STATUSES = frozenset({
    "healthy",
    "missing",
    "stale",
    "source_failed",
})
BUSINESS_EXPOSURE_STATUSES = frozenset({
    "verified",
    "unconfirmed",
    "missing",
    "disproved",
})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _symbol(value: Any) -> str:
    text = _required_text(value, "symbol")
    if len(text) != 6 or not text.isdigit():
        raise ValueError("symbol必须是6位数字")
    return text


def _enum_text(value: Any, field_name: str, allowed: frozenset[str]) -> str:
    text = _required_text(
        getattr(value, "value", value),
        field_name,
    )
    if text not in allowed:
        raise ValueError(f"{field_name}值无效: {text}")
    return text


def _json_value(value: Any, default: Any) -> Any:
    return default if value is None else value


def _checksum(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _int_value(value: Any, field_name: str, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是整数") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name}不能小于{minimum}")
    return parsed


def _float_value(
    value: Any,
    field_name: str,
    *,
    minimum: float = 0,
    maximum: Optional[float] = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是数字") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        if maximum is None:
            raise ValueError(f"{field_name}不能小于{minimum}")
        raise ValueError(f"{field_name}必须在{minimum}到{maximum}之间")
    return parsed


def _bool_value(value: Any, field_name: str) -> bool:
    if not isinstance(value, (bool, int)):
        raise ValueError(f"{field_name}必须是布尔值")
    return bool(value)


class LeaderRepository:
    """在调用方连接上执行阶段6龙头仓储读写。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._connection = connection
        self._clock = clock
        self._require_storage()

    def _clock_text(self) -> str:
        return _datetime_text(self._clock(), "仓储写入时间")

    def _require_storage(self) -> None:
        try:
            validate_applied_migrations(
                self._connection,
                migrations=STAGE6_RADAR_MIGRATIONS,
            )
        except Exception as exc:
            if isinstance(exc, RadarRepositoryError):
                raise
            raise RepositoryStateError(
                "数据库未完成阶段6可选迁移版本5或迁移记录已漂移"
            ) from exc

        row = self._connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations "
            "WHERE version=?",
            (LEADER_STORAGE_MIGRATION.version,),
        ).fetchone()
        expected = (
            LEADER_STORAGE_MIGRATION.name,
            LEADER_STORAGE_MIGRATION.checksum,
        )
        if row is None or tuple(row) != expected:
            raise RepositoryStateError(
                "数据库未完成阶段6可选迁移版本5或迁移记录已漂移"
            )

    @contextmanager
    def _transaction(self):
        if self._connection.in_transaction:
            raise RepositoryStateError("龙头仓储写入前连接不能处于未提交事务中")
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except RadarRepositoryError:
            self._connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryConflictError(
                f"龙头仓储约束冲突并已回滚：{exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            self._connection.rollback()
            raise RepositoryWriteError(
                f"龙头仓储写入失败并已回滚：{type(exc).__name__}"
            ) from exc
        except Exception:
            self._connection.rollback()
            raise

    @staticmethod
    def _prepare_snapshot(
        snapshot: Mapping[str, Any],
        entries: Iterable[Mapping[str, Any]],
    ):
        payload = dict(snapshot)
        radar_run_id = _required_text(
            payload.get("radarRunId"),
            "radarRunId",
        )
        as_of = _datetime_text(payload["asOf"], "as_of")
        normalized_snapshot = {
            "radarRunId": radar_run_id,
            "asOf": as_of,
            "ruleVersion": _required_text(
                payload.get("ruleVersion"),
                "ruleVersion",
            ),
            "ruleVersionId": _optional_text(payload.get("ruleVersionId")),
            "eligibleCount": _int_value(
                payload.get("eligibleCount"),
                "eligibleCount",
            ),
            "preliminaryCount": _int_value(
                payload.get("preliminaryCount"),
                "preliminaryCount",
            ),
            "candidateCount": _int_value(
                payload.get("candidateCount"),
                "candidateCount",
            ),
            "confirmedCount": _int_value(
                payload.get("confirmedCount"),
                "confirmedCount",
            ),
            "removedCount": _int_value(
                payload.get("removedCount"),
                "removedCount",
            ),
            "coverage": _float_value(
                payload.get("coverage"),
                "coverage",
                maximum=1,
            ),
            "quality": _required_text(payload.get("quality"), "quality"),
            "reasonCounts": _json_value(payload.get("reasonCounts"), {}),
            "formalUsable": _bool_value(
                payload.get("formalUsable", False),
                "formalUsable",
            ),
        }
        normalized_entries = []
        seen_symbols = set()
        for entry in entries:
            item = dict(entry)
            symbol = _symbol(item.get("symbol"))
            if symbol in seen_symbols:
                raise RepositoryConflictError(
                    f"同一龙头批次包含重复证券：{symbol}"
                )
            seen_symbols.add(symbol)
            normalized_entries.append({
                "symbol": symbol,
                "name": _required_text(item.get("name"), "name"),
                "industryCode": _optional_text(item.get("industryCode")),
                "industryName": _optional_text(item.get("industryName")),
                "state": _enum_text(
                    item.get("state"),
                    "state",
                    LEADER_STATES,
                ),
                "score": _float_value(
                    item.get("score"),
                    "score",
                    maximum=100,
                ),
                "businessExposureStatus": _enum_text(
                    item.get("businessExposureStatus"),
                    "businessExposureStatus",
                    BUSINESS_EXPOSURE_STATUSES,
                ),
                "dataStatus": _enum_text(
                    item.get("dataStatus"),
                    "dataStatus",
                    LEADER_DATA_STATUSES,
                ),
                "firstRejectionReason": _optional_text(
                    item.get("firstRejectionReason")
                ),
                "reasons": _json_value(item.get("reasons"), []),
                "evidence": _json_value(item.get("evidence"), {}),
                "invalidation": _json_value(item.get("invalidation"), {}),
                "stateAgePeriods": _int_value(
                    item.get("stateAgePeriods", 0),
                    "stateAgePeriods",
                ),
                "formalUsable": _bool_value(
                    item.get("formalUsable", False),
                    "formalUsable",
                ),
            })
        combined = {
            "snapshot": normalized_snapshot,
            "entries": normalized_entries,
        }
        return (
            normalized_snapshot,
            normalized_entries,
            _checksum(combined),
        )

    def _insert_candidate_snapshot_in_transaction(
        self,
        normalized_snapshot: Mapping[str, Any],
        normalized_entries: Sequence[Mapping[str, Any]],
        record_checksum: str,
    ) -> bool:
        radar_run_id = normalized_snapshot["radarRunId"]
        existing = self._connection.execute(
            "SELECT record_checksum FROM radar_leader_candidate_snapshots "
            "WHERE radar_run_id=?",
            (radar_run_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] == record_checksum:
                return False
            raise RepositoryConflictError(
                f"龙头候选批次{radar_run_id}内容冲突"
            )
        self._connection.execute(
            """
            INSERT INTO radar_leader_candidate_snapshots (
                radar_run_id, as_of, rule_version, rule_version_id,
                eligible_count, preliminary_count, candidate_count,
                confirmed_count, removed_count, coverage, quality,
                reason_counts_json, formal_usable, record_checksum,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                radar_run_id,
                normalized_snapshot["asOf"],
                normalized_snapshot["ruleVersion"],
                normalized_snapshot["ruleVersionId"],
                normalized_snapshot["eligibleCount"],
                normalized_snapshot["preliminaryCount"],
                normalized_snapshot["candidateCount"],
                normalized_snapshot["confirmedCount"],
                normalized_snapshot["removedCount"],
                normalized_snapshot["coverage"],
                normalized_snapshot["quality"],
                _canonical_json(normalized_snapshot["reasonCounts"]),
                int(normalized_snapshot["formalUsable"]),
                record_checksum,
                self._clock_text(),
            ),
        )
        self._connection.executemany(
            """
            INSERT INTO radar_leader_candidate_entries (
                radar_run_id, symbol, name, industry_code, industry_name,
                state, score, business_exposure_status, data_status,
                first_rejection_reason, reasons_json, evidence_json,
                invalidation_json, state_age_periods, formal_usable,
                record_checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    radar_run_id,
                    entry["symbol"],
                    entry["name"],
                    entry["industryCode"],
                    entry["industryName"],
                    entry["state"],
                    entry["score"],
                    entry["businessExposureStatus"],
                    entry["dataStatus"],
                    entry["firstRejectionReason"],
                    _canonical_json(entry["reasons"]),
                    _canonical_json(entry["evidence"]),
                    _canonical_json(entry["invalidation"]),
                    entry["stateAgePeriods"],
                    int(entry["formalUsable"]),
                    _checksum({
                        "radarRunId": radar_run_id,
                        **entry,
                    }),
                    self._clock_text(),
                )
                for entry in normalized_entries
            ),
        )
        return True

    def save_candidate_snapshot(
        self,
        snapshot: Mapping[str, Any],
        entries: Iterable[Mapping[str, Any]],
    ) -> bool:
        (
            normalized_snapshot,
            normalized_entries,
            record_checksum,
        ) = self._prepare_snapshot(snapshot, entries)
        with self._transaction():
            return self._insert_candidate_snapshot_in_transaction(
                normalized_snapshot,
                normalized_entries,
                record_checksum,
            )

    def get_candidate_snapshot(
        self,
        radar_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        radar_run_id = _required_text(radar_run_id, "radarRunId")
        row = self._connection.execute(
            """
            SELECT as_of, rule_version, rule_version_id, eligible_count,
                   preliminary_count, candidate_count, confirmed_count,
                   removed_count, coverage, quality, reason_counts_json,
                   formal_usable, created_at
            FROM radar_leader_candidate_snapshots
            WHERE radar_run_id=?
            """,
            (radar_run_id,),
        ).fetchone()
        if row is None:
            return None
        entries = self._connection.execute(
            """
            SELECT symbol, name, industry_code, industry_name, state,
                   score, business_exposure_status, data_status,
                   first_rejection_reason, reasons_json, evidence_json,
                   invalidation_json, state_age_periods, formal_usable
            FROM radar_leader_candidate_entries
            WHERE radar_run_id=?
            ORDER BY CASE state
                WHEN 'confirmed' THEN 3
                WHEN 'candidate' THEN 2
                WHEN 'preliminary' THEN 1
                ELSE 0
            END DESC, score DESC, symbol
            """,
            (radar_run_id,),
        ).fetchall()
        return {
            "radarRunId": radar_run_id,
            "asOf": _parse_datetime(row[0], "龙头候选as_of"),
            "ruleVersion": row[1],
            "ruleVersionId": row[2],
            "eligibleCount": row[3],
            "preliminaryCount": row[4],
            "candidateCount": row[5],
            "confirmedCount": row[6],
            "removedCount": row[7],
            "coverage": row[8],
            "quality": row[9],
            "reasonCounts": json.loads(row[10]),
            "formalUsable": bool(row[11]),
            "createdAt": _parse_datetime(row[12], "龙头候选created_at"),
            "entries": [
                {
                    "symbol": entry[0],
                    "name": entry[1],
                    "industryCode": entry[2],
                    "industryName": entry[3],
                    "state": entry[4],
                    "score": entry[5],
                    "businessExposureStatus": entry[6],
                    "dataStatus": entry[7],
                    "firstRejectionReason": entry[8],
                    "reasons": json.loads(entry[9]),
                    "evidence": json.loads(entry[10]),
                    "invalidation": json.loads(entry[11]),
                    "stateAgePeriods": entry[12],
                    "formalUsable": bool(entry[13]),
                }
                for entry in entries
            ],
        }

    def get_latest_candidate_snapshot(self) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT radar_run_id
            FROM radar_leader_candidate_snapshots
            ORDER BY as_of DESC, radar_run_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return self.get_candidate_snapshot(str(row[0]))

    @staticmethod
    def _prepare_transition(transition: Mapping[str, Any]):
        payload = dict(transition)
        normalized = {
            "transitionId": _required_text(
                payload.get("transitionId"),
                "transitionId",
            ),
            "symbol": _symbol(payload.get("symbol")),
            "radarRunId": _required_text(
                payload.get("radarRunId"),
                "radarRunId",
            ),
            "asOf": _datetime_text(payload["asOf"], "as_of"),
            "fromState": _enum_text(
                payload.get("fromState"),
                "fromState",
                LEADER_STATES,
            ),
            "toState": _enum_text(
                payload.get("toState"),
                "toState",
                LEADER_STATES,
            ),
            "action": _enum_text(
                payload.get("action"),
                "action",
                LEADER_ACTIONS,
            ),
            "ruleVersion": _required_text(
                payload.get("ruleVersion"),
                "ruleVersion",
            ),
            "ruleVersionId": _optional_text(payload.get("ruleVersionId")),
            "reasons": _json_value(payload.get("reasons"), []),
            "firstRejectionReason": _optional_text(
                payload.get("firstRejectionReason")
            ),
            "stateAgePeriods": _int_value(
                payload.get("stateAgePeriods", 0),
                "stateAgePeriods",
            ),
            "cooldownUntil": (
                _datetime_text(payload["cooldownUntil"], "cooldown_until")
                if payload.get("cooldownUntil") is not None
                else None
            ),
            "formalUsable": _bool_value(
                payload.get("formalUsable", False),
                "formalUsable",
            ),
        }
        return normalized, _checksum(normalized)

    def _insert_state_transition_in_transaction(
        self,
        normalized: Mapping[str, Any],
        record_checksum: str,
    ) -> bool:
        existing = self._connection.execute(
            "SELECT record_checksum FROM radar_leader_state_history "
            "WHERE transition_id=?",
            (normalized["transitionId"],),
        ).fetchone()
        if existing is not None:
            if existing[0] == record_checksum:
                return False
            raise RepositoryConflictError(
                f"龙头状态转移{normalized['transitionId']}内容冲突"
            )
        self._connection.execute(
            """
            INSERT INTO radar_leader_state_history (
                transition_id, symbol, radar_run_id, as_of,
                from_state, to_state, action, rule_version,
                rule_version_id, reasons_json, first_rejection_reason,
                state_age_periods, cooldown_until, formal_usable,
                record_checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["transitionId"],
                normalized["symbol"],
                normalized["radarRunId"],
                normalized["asOf"],
                normalized["fromState"],
                normalized["toState"],
                normalized["action"],
                normalized["ruleVersion"],
                normalized["ruleVersionId"],
                _canonical_json(normalized["reasons"]),
                normalized["firstRejectionReason"],
                normalized["stateAgePeriods"],
                normalized["cooldownUntil"],
                int(normalized["formalUsable"]),
                record_checksum,
                self._clock_text(),
            ),
        )
        return True

    def save_state_transition(self, transition: Mapping[str, Any]) -> bool:
        normalized, record_checksum = self._prepare_transition(transition)
        with self._transaction():
            return self._insert_state_transition_in_transaction(
                normalized,
                record_checksum,
            )

    def save_shadow_run(
        self,
        snapshot: Mapping[str, Any],
        entries: Iterable[Mapping[str, Any]],
        transitions: Iterable[Mapping[str, Any]],
    ) -> Tuple[bool, int]:
        """原子保存一轮龙头候选快照及其状态历史。"""
        (
            normalized_snapshot,
            normalized_entries,
            snapshot_checksum,
        ) = self._prepare_snapshot(snapshot, entries)
        prepared_transitions = tuple(
            self._prepare_transition(transition)
            for transition in transitions
        )
        with self._transaction():
            snapshot_inserted = (
                self._insert_candidate_snapshot_in_transaction(
                    normalized_snapshot,
                    normalized_entries,
                    snapshot_checksum,
                )
            )
            transition_inserted = sum(
                self._insert_state_transition_in_transaction(
                    normalized,
                    record_checksum,
                )
                for normalized, record_checksum in prepared_transitions
            )
        return snapshot_inserted, transition_inserted

    def list_state_history(self, symbol: str) -> list[Dict[str, Any]]:
        symbol = _symbol(symbol)
        rows = self._connection.execute(
            """
            SELECT transition_id, radar_run_id, as_of, from_state,
                   to_state, action, rule_version, rule_version_id,
                   reasons_json, first_rejection_reason,
                   state_age_periods, cooldown_until, formal_usable,
                   created_at
            FROM radar_leader_state_history
            WHERE symbol=?
            ORDER BY as_of ASC, created_at ASC, transition_id ASC
            """,
            (symbol,),
        ).fetchall()
        return [
            {
                "transitionId": row[0],
                "symbol": symbol,
                "radarRunId": row[1],
                "asOf": _parse_datetime(row[2], "龙头状态as_of"),
                "fromState": row[3],
                "toState": row[4],
                "action": row[5],
                "ruleVersion": row[6],
                "ruleVersionId": row[7],
                "reasons": json.loads(row[8]),
                "firstRejectionReason": row[9],
                "stateAgePeriods": row[10],
                "cooldownUntil": (
                    _parse_datetime(row[11], "龙头冷却截止时间")
                    if row[11] is not None
                    else None
                ),
                "formalUsable": bool(row[12]),
                "createdAt": _parse_datetime(row[13], "龙头状态created_at"),
            }
            for row in rows
        ]

    def get_latest_state_records_before(
        self,
        as_of: datetime,
    ) -> Dict[str, LeaderStateRecord]:
        """读取冻结时点前每只证券最后一条状态，不使用本轮或未来记录。"""
        as_of_text = _datetime_text(as_of, "as_of")
        rows = self._connection.execute(
            """
            SELECT symbol, to_state, state_age_periods, rule_version,
                   as_of, cooldown_until
            FROM (
                SELECT symbol, to_state, state_age_periods, rule_version,
                       as_of, cooldown_until,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol
                           ORDER BY as_of DESC, created_at DESC,
                                    transition_id DESC
                       ) AS row_number
                FROM radar_leader_state_history
                WHERE as_of < ?
            )
            WHERE row_number=1
            ORDER BY symbol
            """,
            (as_of_text,),
        ).fetchall()
        return {
            str(row[0]): LeaderStateRecord(
                symbol=str(row[0]),
                state=LeaderState(row[1]),
                state_age_periods=int(row[2]),
                rule_version=str(row[3]),
                state_since=_parse_datetime(
                    row[4],
                    "龙头状态state_since",
                ),
                last_evaluated_at=_parse_datetime(
                    row[4],
                    "龙头状态last_evaluated_at",
                ),
                cooldown_until=(
                    _parse_datetime(row[5], "龙头状态cooldown_until")
                    if row[5] is not None
                    else None
                ),
            )
            for row in rows
        }
