import multiprocessing
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from radar.config import RadarSettings
from radar.contracts import (
    EtfRegistryRecord,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
)
from radar.migrations import apply_pending_migrations
from radar.repository import RadarRepository
from radar.run_lock import CrossProcessFileLock
from radar.scheduler import (
    RADAR_SHADOW_JOB_ID,
    ScheduleRegistrationState,
    ScheduledRunState,
    ScheduledShadowJob,
    ShadowJobSpec,
    register_shadow_job,
    register_shadow_jobs,
)
from radar.shadow_runner import OneShotShadowRunner, ShadowSources


UTC = timezone.utc
AS_OF = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)
FETCHED_AT = AS_OF + timedelta(seconds=2)


def _hold_file_lock(lock_path, ready, release):
    lock = CrossProcessFileLock(lock_path)
    if not lock.acquire(blocking=False):
        raise RuntimeError("子进程未能获得测试锁")
    ready.set()
    try:
        if not release.wait(5):
            raise RuntimeError("测试未及时释放子进程锁")
    finally:
        lock.release()


class CrossProcessFileLockTests(unittest.TestCase):
    def test_same_instance_reentry_is_rejected_and_release_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock = CrossProcessFileLock(Path(temp_dir) / "radar.lock")

            self.assertTrue(lock.acquire(blocking=False))
            self.assertFalse(lock.acquire(blocking=False))
            lock.release()
            lock.release()
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()

    def test_file_lock_blocks_another_process_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "radar.lock"
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            process = context.Process(
                target=_hold_file_lock,
                args=(lock_path, ready, release),
            )
            process.start()
            try:
                self.assertTrue(ready.wait(5), "子进程未及时持有文件锁")
                competing = CrossProcessFileLock(lock_path)
                self.assertFalse(competing.acquire(blocking=False))
            finally:
                release.set()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)

            self.assertEqual(process.exitcode, 0)
            reacquired = CrossProcessFileLock(lock_path)
            self.assertTrue(reacquired.acquire(blocking=False))
            reacquired.release()
            self.assertEqual(
                stat.S_IMODE(lock_path.stat().st_mode),
                0o600,
            )


class RadarSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temp_dir.name) / "radar-shadow.lock"
        self.enabled_settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def enabled_job(self, executor, **overrides):
        values = {
            "settings": self.enabled_settings,
            "execute_once": executor,
            "lock_path": self.lock_path,
            "clock": lambda: AS_OF,
        }
        values.update(overrides)
        return ScheduledShadowJob(**values)

    def test_disabled_job_does_not_create_lock_or_call_executor(self):
        for settings in (
            RadarSettings(enabled=False, shadow_mode=False),
            RadarSettings(enabled=False, shadow_mode=True),
            RadarSettings(enabled=True, shadow_mode=False),
        ):
            with self.subTest(settings=settings):
                executor = Mock()
                outcome = ScheduledShadowJob(
                    settings=settings,
                    execute_once=executor,
                    lock_path=self.lock_path,
                    clock=lambda: AS_OF,
                )()

                self.assertEqual(outcome.state, ScheduledRunState.DISABLED)
                self.assertIsNone(outcome.radar_run_id)
                executor.assert_not_called()
                self.assertFalse(self.lock_path.exists())

    def test_locked_job_skips_without_calling_executor(self):
        holder = CrossProcessFileLock(self.lock_path)
        self.assertTrue(holder.acquire(blocking=False))
        executor = Mock()
        try:
            with self.assertLogs("radar.scheduler", level="WARNING") as logs:
                outcome = self.enabled_job(executor)()
        finally:
            holder.release()

        self.assertEqual(outcome.state, ScheduledRunState.LOCKED)
        self.assertIsNone(outcome.radar_run_id)
        executor.assert_not_called()
        self.assertIn("已有进程持有任务锁", logs.output[0])

    def test_readiness_gate_skips_before_lock_and_executor(self):
        executor = Mock()
        readiness = Mock(return_value="lunch_break")
        job = self.enabled_job(
            executor,
            readiness_check=readiness,
        )

        outcome = job()

        self.assertEqual(outcome.state, ScheduledRunState.SKIPPED)
        self.assertEqual(outcome.skip_reason, "lunch_break")
        readiness.assert_called_once_with(AS_OF)
        executor.assert_not_called()
        self.assertFalse(self.lock_path.exists())

    def test_enabled_job_uses_frozen_utc_as_of_and_releases_lock(self):
        result = Mock(status="succeeded")
        executor = Mock(return_value=result)

        outcome = self.enabled_job(executor)()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(
            outcome.radar_run_id,
            "radar-shadow-20260718T013000000000Z",
        )
        self.assertEqual(outcome.result_status, "succeeded")
        self.assertGreaterEqual(outcome.duration_seconds, 0)
        executor.assert_called_once_with(outcome.radar_run_id, AS_OF)
        probe = CrossProcessFileLock(self.lock_path)
        self.assertTrue(probe.acquire(blocking=False))
        probe.release()

    def test_scoped_job_uses_distinct_run_id_prefix(self):
        executor = Mock(return_value=Mock(status="succeeded", item_count=2))

        outcome = self.enabled_job(
            executor,
            run_id_prefix="radar-shadow-stock-quotes",
        )()

        self.assertEqual(
            outcome.radar_run_id,
            "radar-shadow-stock-quotes-20260718T013000000000Z",
        )
        self.assertEqual(outcome.item_count, 2)

    def test_gate_reasons_are_propagated_and_logged_without_item_details(self):
        executor = Mock(return_value=Mock(
            status="degraded",
            item_count=0,
            gate_passed=False,
            gate_reasons=(
                "quote_item_source_time_stale",
                "eligible_sector_features_incomplete",
            ),
        ))

        with self.assertLogs("radar.scheduler", level="WARNING") as logs:
            outcome = self.enabled_job(executor)()

        self.assertFalse(outcome.gate_passed)
        self.assertEqual(
            outcome.gate_reasons,
            (
                "quote_item_source_time_stale",
                "eligible_sector_features_incomplete",
            ),
        )
        self.assertIn(
            "quote_item_source_time_stale,"
            "eligible_sector_features_incomplete",
            logs.output[0],
        )
        self.assertNotIn("000001", logs.output[0])

    def test_executor_failure_releases_lock_and_is_raised(self):
        executor = Mock(side_effect=RuntimeError("boom"))

        with self.assertLogs("radar.scheduler", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.enabled_job(executor)()

        self.assertIn("雷达影子调度失败", logs.output[0])
        probe = CrossProcessFileLock(self.lock_path)
        self.assertTrue(probe.acquire(blocking=False))
        probe.release()

    def test_naive_clock_is_rejected_before_executor_and_releases_lock(self):
        executor = Mock()
        job = self.enabled_job(
            executor,
            clock=lambda: datetime(2026, 7, 18, 1, 30),
        )

        with self.assertLogs("radar.scheduler", level="ERROR") as logs:
            with self.assertRaisesRegex(ValueError, "时区"):
                job()

        executor.assert_not_called()
        self.assertIn("run_id=unassigned", logs.output[0])
        probe = CrossProcessFileLock(self.lock_path)
        self.assertTrue(probe.acquire(blocking=False))
        probe.release()

    def test_temp_database_deterministic_shadow_run(self):
        db_path = Path(self.temp_dir.name) / "radar-shadow.db"
        connection = sqlite3.connect(db_path)
        apply_pending_migrations(connection)
        connection.close()

        def execute_once(radar_run_id, as_of):
            run_connection = sqlite3.connect(db_path)
            try:
                repository = RadarRepository(
                    run_connection,
                    clock=lambda: FETCHED_AT,
                )

                def security(run_id, batch_id, batch_as_of):
                    record = SecurityMasterRecord(
                        symbol="000001",
                        name="平安银行",
                        exchange="szse",
                        board="主板",
                        listingDate="1991-04-03",
                        sourceReportDate="2026-07-18",
                        source="szse",
                        fetchedAt=FETCHED_AT,
                        sourceFields={"A股代码": "000001"},
                    )
                    return SourceBatch(
                        meta=RadarBatchMeta(
                            radarRunId=run_id,
                            batchId=batch_id,
                            source="official_exchange_security_master",
                            asOf=batch_as_of,
                            fetchedAt=FETCHED_AT,
                            expectedCount=1,
                            returnedCount=1,
                            rowCoverage=1.0,
                            requiredFieldCoverage={
                                "symbol": 1.0,
                                "name": 1.0,
                                "listing_date": 1.0,
                            },
                        ),
                        items=[record],
                    )

                def etf(run_id, batch_id, batch_as_of):
                    record = EtfRegistryRecord(
                        symbol="510300",
                        name="沪深300ETF",
                        exchange="sse",
                        sourceType="股票ETF",
                        sourceReportDate="2026-07-18",
                        source="sse",
                        fetchedAt=FETCHED_AT,
                        sourceFields={"基金代码": "510300"},
                    )
                    return SourceBatch(
                        meta=RadarBatchMeta(
                            radarRunId=run_id,
                            batchId=batch_id,
                            source="official_exchange_etf_registry",
                            asOf=batch_as_of,
                            fetchedAt=FETCHED_AT,
                            expectedCount=1,
                            returnedCount=1,
                            rowCoverage=1.0,
                            requiredFieldCoverage={
                                "symbol": 1.0,
                                "name": 1.0,
                                "source_type": 1.0,
                            },
                        ),
                        items=[record],
                    )

                def quotes(symbols, run_id, batch_id, batch_as_of):
                    records = [
                        QuoteSnapshot(
                            symbol=symbol,
                            name=f"证券{symbol}",
                            sourceTime=batch_as_of - timedelta(seconds=30),
                            fetchedAt=FETCHED_AT,
                            price=10.0,
                            changePercent=0.0,
                            turnoverAmountSource=0.0,
                            turnoverRatePercent=0.0,
                            volumeRatio=0.0,
                            marketCapSource=0.0,
                        )
                        for symbol in symbols
                    ]
                    return SourceBatch(
                        meta=RadarBatchMeta(
                            radarRunId=run_id,
                            batchId=batch_id,
                            source="tencent_finance",
                            asOf=batch_as_of,
                            sourceTime=batch_as_of - timedelta(seconds=30),
                            fetchedAt=FETCHED_AT,
                            expectedCount=len(records),
                            returnedCount=len(records),
                            rowCoverage=1.0,
                            requiredFieldCoverage={
                                "price": 1.0,
                                "source_time": 1.0,
                            },
                        ),
                        items=records,
                    )

                runner = OneShotShadowRunner(
                    repository=repository,
                    settings=self.enabled_settings,
                    sources=ShadowSources(
                        security_master=security,
                        etf_registry=etf,
                        quotes=quotes,
                    ),
                    clock=lambda: FETCHED_AT,
                )
                return runner.run_once(radar_run_id, as_of)
            finally:
                run_connection.close()

        outcome = self.enabled_job(execute_once)()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(outcome.result_status, "succeeded")
        reopened = sqlite3.connect(db_path)
        try:
            self.assertEqual(
                reopened.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                1,
            )
            self.assertEqual(
                reopened.execute(
                    "SELECT COUNT(*) FROM radar_source_status"
                ).fetchone()[0],
                3,
            )
            self.assertEqual(reopened.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            reopened.close()

    def test_default_settings_do_not_register_job(self):
        scheduler = Mock()

        registration = register_shadow_job(
            scheduler,
            Mock(),
            RadarSettings(),
        )

        self.assertEqual(
            registration.state,
            ScheduleRegistrationState.DISABLED,
        )
        self.assertEqual(registration.job_id, RADAR_SHADOW_JOB_ID)
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()
        scheduler.start.assert_not_called()

    def test_enabled_registration_uses_single_instance_options(self):
        scheduler = Mock()
        scheduler.get_job.return_value = None
        job = Mock()

        registration = register_shadow_job(
            scheduler,
            job,
            self.enabled_settings,
        )

        self.assertEqual(
            registration.state,
            ScheduleRegistrationState.REGISTERED,
        )
        scheduler.get_job.assert_called_once_with(RADAR_SHADOW_JOB_ID)
        scheduler.add_job.assert_called_once_with(
            job,
            "interval",
            id=RADAR_SHADOW_JOB_ID,
            seconds=180,
            max_instances=1,
            coalesce=True,
            replace_existing=False,
            misfire_grace_time=180,
        )
        scheduler.start.assert_not_called()

    def test_existing_job_is_not_registered_twice(self):
        scheduler = Mock()
        scheduler.get_job.return_value = object()

        registration = register_shadow_job(
            scheduler,
            Mock(),
            self.enabled_settings,
        )

        self.assertEqual(
            registration.state,
            ScheduleRegistrationState.ALREADY_REGISTERED,
        )
        scheduler.add_job.assert_not_called()
        scheduler.start.assert_not_called()

    def test_multiple_scoped_jobs_keep_independent_intervals(self):
        scheduler = Mock()
        scheduler.get_job.return_value = None
        stock_job = Mock()
        etf_job = Mock()

        registrations = register_shadow_jobs(
            scheduler,
            (
                ShadowJobSpec("radar-stock", stock_job, 180),
                ShadowJobSpec("radar-etf", etf_job, 300),
            ),
            self.enabled_settings,
        )

        self.assertEqual(
            [registration.state for registration in registrations],
            [
                ScheduleRegistrationState.REGISTERED,
                ScheduleRegistrationState.REGISTERED,
            ],
        )
        self.assertEqual(scheduler.add_job.call_count, 2)
        calls = scheduler.add_job.call_args_list
        self.assertEqual(calls[0].kwargs["id"], "radar-stock")
        self.assertEqual(calls[0].kwargs["seconds"], 180)
        self.assertEqual(calls[1].kwargs["id"], "radar-etf")
        self.assertEqual(calls[1].kwargs["seconds"], 300)
        scheduler.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
