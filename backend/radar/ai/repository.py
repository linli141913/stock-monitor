from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from radar.ai.contracts import RadarAiStructuredOutput
from radar.ai.service import (
    RadarAiAnalysisResult,
    RadarAiRunConflict,
    RadarAiUsage,
)
from radar.migrations import STAGE8_RADAR_MIGRATIONS, validate_applied_migrations


UTC = timezone.utc


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("雷达AI时间必须包含时区")
    return value.isoformat(timespec="seconds")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class RadarAiRepository:
    """只访问调用方提供的已迁移连接，不查找或迁移生产数据库。"""

    def __init__(self, connection: sqlite3.Connection, *, clock=None):
        validate_applied_migrations(
            connection,
            migrations=STAGE8_RADAR_MIGRATIONS,
        )
        self.connection = connection
        self.clock = clock or (lambda: datetime.now(UTC))

    def start_run(self, record: dict) -> None:
        analysis_run_id = uuid.uuid4().hex
        analysis_at = record["analysisAt"]
        try:
            self.connection.execute(
                """
                INSERT INTO radar_ai_analysis_runs (
                    analysis_run_id, reuse_key, radar_run_id, as_of,
                    scope_type, scope_id, evidence_fingerprint,
                    prompt_version, model, analysis_status, manual,
                    analysis_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    analysis_run_id,
                    record["reuseKey"],
                    record["radarRunId"],
                    _datetime_text(record.get("asOf") or analysis_at),
                    record["scopeType"],
                    record["scopeId"],
                    record["evidenceFingerprint"],
                    record["promptVersion"],
                    record["model"],
                    int(bool(record.get("manual", False))),
                    _datetime_text(analysis_at),
                    _datetime_text(self.clock()),
                ),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            if "radar_ai_analysis_runs.reuse_key" in str(exc):
                raise RadarAiRunConflict("radar_ai_reuse_conflict") from exc
            raise

    def _running_id(self, reuse_key: str) -> str:
        row = self.connection.execute(
            "SELECT analysis_run_id FROM radar_ai_analysis_runs "
            "WHERE reuse_key=? AND analysis_status='running' "
            "ORDER BY created_at DESC LIMIT 1",
            (reuse_key,),
        ).fetchone()
        if row is None:
            raise ValueError("radar_ai_running_task_missing")
        return str(row[0])

    def finish_success(
        self,
        reuse_key: str,
        result: RadarAiAnalysisResult,
    ) -> None:
        if result.output is None:
            raise ValueError("radar_ai_success_output_missing")
        analysis_run_id = self._running_id(reuse_key)
        output = result.output
        now = _datetime_text(self.clock())
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                UPDATE radar_ai_analysis_runs SET
                    analysis_status='success', duration_ms=?,
                    usage_prompt_tokens=?, usage_completion_tokens=?,
                    usage_total_tokens=?, error_category=NULL, completed_at=?
                WHERE analysis_run_id=? AND analysis_status='running'
                """,
                (
                    result.duration_ms,
                    result.usage.prompt_tokens,
                    result.usage.completion_tokens,
                    result.usage.total_tokens,
                    now,
                    analysis_run_id,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO radar_ai_outputs (
                    analysis_run_id, confirmed_facts_json, inferences_json,
                    unknowns_json, counter_evidence_json,
                    conditional_scenarios_json, plain_english_summary,
                    source_ids_json, invalidating_conditions_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_run_id,
                    _json([
                        fact.model_dump(mode="json", by_alias=True)
                        for fact in output.confirmed_facts
                    ]),
                    _json(output.inferences),
                    _json(output.unknowns),
                    _json(output.counter_evidence),
                    _json(output.conditional_scenarios),
                    output.plain_english_summary,
                    _json(output.source_ids),
                    _json(output.invalidating_conditions),
                    now,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def finish_failure(
        self,
        reuse_key: str,
        result: RadarAiAnalysisResult,
    ) -> None:
        analysis_run_id = self._running_id(reuse_key)
        self.connection.execute(
            """
            UPDATE radar_ai_analysis_runs SET
                analysis_status='failed', duration_ms=?,
                usage_prompt_tokens=?, usage_completion_tokens=?,
                usage_total_tokens=?, error_category=?, completed_at=?
            WHERE analysis_run_id=? AND analysis_status='running'
            """,
            (
                result.duration_ms,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.usage.total_tokens,
                result.error_category,
                _datetime_text(self.clock()),
                analysis_run_id,
            ),
        )
        self.connection.commit()

    def _result_from_row(self, row) -> RadarAiAnalysisResult:
        output = None
        if row[9] == "success":
            output = RadarAiStructuredOutput.model_validate({
                "analysisStatus": "success",
                "confirmedFacts": json.loads(row[17]),
                "inferences": json.loads(row[18]),
                "unknowns": json.loads(row[19]),
                "counterEvidence": json.loads(row[20]),
                "conditionalScenarios": json.loads(row[21]),
                "plainEnglishSummary": row[22],
                "sourceIds": json.loads(row[23]),
                "invalidatingConditions": json.loads(row[24]),
            })
        return RadarAiAnalysisResult(
            radar_run_id=row[1],
            as_of=_parse_datetime(row[2]),
            scope_type=row[3],
            scope_id=row[4],
            analysis_status=row[9],
            evidence_fingerprint=row[5],
            prompt_version=row[6],
            model=row[7],
            analysis_at=_parse_datetime(row[8]),
            duration_ms=int(row[10]),
            usage=RadarAiUsage(int(row[11]), int(row[12])),
            output=output,
            error_category=row[13],
        )

    @staticmethod
    def _select_sql() -> str:
        return """
            SELECT r.reuse_key, r.radar_run_id, r.as_of, r.scope_type,
                   r.scope_id, r.evidence_fingerprint, r.prompt_version,
                   r.model, r.analysis_at, r.analysis_status, r.duration_ms,
                   r.usage_prompt_tokens, r.usage_completion_tokens,
                   r.error_category, r.completed_at, r.analysis_run_id,
                   r.created_at, o.confirmed_facts_json, o.inferences_json,
                   o.unknowns_json, o.counter_evidence_json,
                   o.conditional_scenarios_json, o.plain_english_summary,
                   o.source_ids_json, o.invalidating_conditions_json
            FROM radar_ai_analysis_runs r
            LEFT JOIN radar_ai_outputs o
              ON o.analysis_run_id = r.analysis_run_id
        """

    def find_success(self, reuse_key: str) -> Optional[RadarAiAnalysisResult]:
        row = self.connection.execute(
            self._select_sql()
            + " WHERE r.reuse_key=? AND r.analysis_status='success' "
              "ORDER BY r.completed_at DESC LIMIT 1",
            (reuse_key,),
        ).fetchone()
        return None if row is None else self._result_from_row(row)

    def list_history(
        self,
        scope_type: str,
        scope_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[RadarAiAnalysisResult]:
        rows = self.connection.execute(
            self._select_sql()
            + " WHERE r.scope_type=? AND r.scope_id=? "
              "ORDER BY r.analysis_at DESC, r.created_at DESC LIMIT ? OFFSET ?",
            (scope_type, scope_id, limit, offset),
        ).fetchall()
        return [self._result_from_row(row) for row in rows]

    def count_history(self, scope_type: str, scope_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM radar_ai_analysis_runs "
            "WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        ).fetchone()
        return int(row[0])

    def usage_for_day(self, day: str) -> Tuple[int, int]:
        row = self.connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(usage_total_tokens), 0)
            FROM radar_ai_analysis_runs
            WHERE substr(analysis_at, 1, 10)=?
            """,
            (day,),
        ).fetchone()
        return int(row[0]), int(row[1])
