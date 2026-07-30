import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import main
from backup_database import create_database_backup
from radar.leader_input_gate import (
    LeaderDimensionEvidence,
    LeaderInputEvidence,
    LeaderSourceEvidence,
    LeaderSourceKind,
)
from radar.leader_repository import LeaderRepository
from radar.leader_scoring import LeaderGateInput
from radar.leader_shadow_runner import LeaderShadowRunner
from radar.leader_state_machine import BusinessExposureStatus
from radar.migrations import (
    STAGE5_RADAR_MIGRATIONS,
    STAGE6_RADAR_MIGRATIONS,
    apply_pending_migrations,
    validate_applied_migrations,
)


UTC = timezone.utc
SOURCE_BY_FIELD = {
    "industry_strength": "sector-1",
    "market_leadership": "market-1",
    "relative_strength_continuity": "quote-1",
    "liquidity_tradability": "quote-1",
    "business_exposure": "business-1",
    "auxiliary": "etf-1",
}


def complete_gates():
    return LeaderGateInput(
        industry_gate_passed=True,
        stock_gate_passed=True,
        market_leadership_passed=True,
        industry_contribution_passed=True,
        liquidity_passed=True,
        tradability_passed=True,
        continuity_passed=True,
        recovery_passed=True,
        risk_filter_passed=True,
        business_exposure_status=BusinessExposureStatus.VERIFIED,
    )


def complete_evidence(as_of):
    source_time = as_of - timedelta(seconds=10)
    source_specs = (
        ("market-1", LeaderSourceKind.MARKET),
        ("sector-1", LeaderSourceKind.SECTOR),
        ("quote-1", LeaderSourceKind.QUOTE),
        ("etf-1", LeaderSourceKind.ETF),
        ("business-1", LeaderSourceKind.BUSINESS_EXPOSURE),
    )
    dimensions = {
        "industry_strength": 25,
        "market_leadership": 25,
        "relative_strength_continuity": 20,
        "liquidity_tradability": 15,
        "business_exposure": 10,
        "auxiliary": 0,
    }
    return LeaderInputEvidence(
        symbol="000725",
        name="京东方A",
        as_of=as_of,
        dimensions=tuple(
            LeaderDimensionEvidence(
                field_name=field_name,
                score=score,
                source_contract_id=SOURCE_BY_FIELD[field_name],
            )
            for field_name, score in dimensions.items()
        ),
        gates=complete_gates(),
        sources=tuple(
            LeaderSourceEvidence(
                source_contract_id=source_contract_id,
                source_kind=source_kind,
                source_name=f"{source_kind.value}-stage6i",
                source_time=source_time,
                fetched_at=as_of,
            )
            for source_contract_id, source_kind in source_specs
        ),
        industry_code="C39",
        industry_name="计算机、通信和其他电子设备制造业",
        business_exposure_source_contract_id="business-1",
        consecutive_signal_periods=2,
        evidence={"scenario": "stage6i-end-to-end"},
        invalidation={"scoreFloor": 75},
    )


class RadarLeaderEndToEndTests(unittest.TestCase):
    def test_v4_online_backup_copy_migrates_to_v5_and_keeps_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "stage5-runtime.sqlite"
            as_of = datetime(2026, 7, 27, 1, 30, tzinfo=UTC)
            with sqlite3.connect(source_path) as connection:
                self.assertEqual(
                    apply_pending_migrations(
                        connection,
                        migrations=STAGE5_RADAR_MIGRATIONS,
                        clock=lambda: as_of,
                    ),
                    [1, 2, 3, 4],
                )
                connection.execute(
                    """
                    INSERT INTO radar_runs (
                        radar_run_id, as_of, status, shadow_mode,
                        started_at, completed_at, created_at
                    ) VALUES (?, ?, 'succeeded', 1, ?, ?, ?)
                    """,
                    (
                        "stage5-existing-run",
                        as_of.isoformat(),
                        as_of.isoformat(),
                        as_of.isoformat(),
                        as_of.isoformat(),
                    ),
                )
                connection.commit()

            backup = create_database_backup(
                source_path,
                root / "backups",
                now=datetime(2026, 7, 27, 9, 45),
            )
            backup_path = Path(backup["backupPath"])
            rehearsal_path = root / "stage6-rehearsal.sqlite"
            shutil.copy2(backup_path, rehearsal_path)

            with sqlite3.connect(rehearsal_path) as rehearsal:
                self.assertEqual(
                    validate_applied_migrations(
                        rehearsal,
                        migrations=STAGE5_RADAR_MIGRATIONS,
                    ),
                    [1, 2, 3, 4],
                )
                self.assertEqual(
                    apply_pending_migrations(
                        rehearsal,
                        migrations=STAGE6_RADAR_MIGRATIONS,
                        clock=lambda: as_of,
                    ),
                    [5],
                )
                self.assertEqual(
                    validate_applied_migrations(
                        rehearsal,
                        migrations=STAGE6_RADAR_MIGRATIONS,
                    ),
                    [1, 2, 3, 4, 5],
                )
                self.assertEqual(
                    rehearsal.execute(
                        "SELECT COUNT(*) FROM radar_runs "
                        "WHERE radar_run_id='stage5-existing-run'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    rehearsal.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    rehearsal.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall(),
                    [],
                )

            for rollback_source in (source_path, backup_path):
                with sqlite3.connect(rollback_source) as rollback:
                    self.assertEqual(
                        validate_applied_migrations(
                            rollback,
                            migrations=STAGE5_RADAR_MIGRATIONS,
                        ),
                        [1, 2, 3, 4],
                    )
                    self.assertNotIn(
                        "radar_leader_candidate_snapshots",
                        {
                            row[0]
                            for row in rollback.execute(
                                "SELECT name FROM sqlite_master "
                                "WHERE type='table'"
                            )
                        },
                    )
                    self.assertEqual(
                        rollback.execute(
                            "SELECT COUNT(*) FROM radar_runs "
                            "WHERE radar_run_id='stage5-existing-run'"
                        ).fetchone()[0],
                        1,
                    )

    def test_temp_v5_shadow_run_is_returned_by_read_only_api(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "stage6i.sqlite"
            as_of = datetime.now(UTC).replace(microsecond=0)
            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(
                    apply_pending_migrations(
                        connection,
                        migrations=STAGE6_RADAR_MIGRATIONS,
                        clock=lambda: as_of,
                    ),
                    [1, 2, 3, 4, 5],
                )
                connection.execute(
                    """
                    INSERT INTO radar_runs (
                        radar_run_id, as_of, status, shadow_mode,
                        started_at, completed_at, created_at
                    ) VALUES (?, ?, 'succeeded', 1, ?, ?, ?)
                    """,
                    (
                        "stage6i-run",
                        as_of.isoformat(),
                        as_of.isoformat(),
                        as_of.isoformat(),
                        as_of.isoformat(),
                    ),
                )
                connection.commit()
                result = LeaderShadowRunner(
                    LeaderRepository(connection, clock=lambda: as_of),
                    clock=lambda: as_of,
                ).run_evidence_once(
                    "stage6i-run",
                    as_of,
                    [complete_evidence(as_of)],
                )
                self.assertTrue(result.persisted)
                self.assertEqual(result.preliminary_count, 1)
                self.assertFalse(result.formal_usable)
            finally:
                connection.close()

            environment = {
                "RADAR_ENABLED": "true",
                "RADAR_SHADOW_MODE": "true",
                "RADAR_ETF_STAGE5_ENABLED": "false",
                "RADAR_LEADER_STAGE6_ENABLED": "true",
            }
            with (
                patch.object(database, "DB_PATH", database_path),
                patch.dict(os.environ, environment, clear=False),
            ):
                response = TestClient(main.app).get(
                    "/api/radar/leaders"
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers["cache-control"],
                "no-store, max-age=0",
            )
            payload = response.json()
            self.assertEqual(payload["schemaVersion"], "radar-leaders-v1")
            self.assertEqual(payload["mode"], "shadow")
            self.assertEqual(payload["module"]["state"], "available")
            self.assertEqual(
                payload["module"]["preliminary"][0]["symbol"],
                "000725",
            )
            self.assertEqual(
                payload["module"]["preliminary"][0]["evidence"][
                    "scenario"
                ],
                "stage6i-end-to-end",
            )
            self.assertFalse(
                payload["module"]["summary"]["formalStateEnabled"]
            )
            self.assertEqual(
                payload["module"]["summary"]["formalUsableCount"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
