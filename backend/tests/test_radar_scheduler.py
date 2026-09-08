import hashlib
import inspect
import json
import multiprocessing
import os
import sqlite3
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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
FORMAL_CHECKED_AT = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
FORMAL_MODULES = (
    "trendRotation",
    "etfObservation",
    "leaderObservation",
)
_AUTO_STORED_REF = object()


def _formal_report(enabled_module="trendRotation"):
    from radar.formal_readiness_contracts import (
        FORMAL_MODULE_REQUIRED_TRADING_DAYS,
        FROZEN_REQUIRED_FORMAL_GATES,
        FormalEvidenceRef,
        FormalGateState,
        FormalModuleReadiness,
        RadarFormalFreshnessPolicy,
        RadarFormalReadiness,
    )

    evidence = FormalEvidenceRef(
        evidenceType="stage9_quality",
        contractVersion="radar-replay-quality-v2",
        contentSha256="a" * 64,
        subjectId="stage9-run",
        sourceTime=FORMAL_CHECKED_AT - timedelta(minutes=3),
        fetchedAt=FORMAL_CHECKED_AT - timedelta(minutes=2),
        generatedAt=FORMAL_CHECKED_AT - timedelta(minutes=1),
    )
    operational_evidence = FormalEvidenceRef(
        evidenceType="formal_operational_checks",
        contractVersion="radar-formal-operational-checks-v1",
        contentSha256="b" * 64,
        subjectId="stage9-run",
        sourceTime=FORMAL_CHECKED_AT,
        fetchedAt=FORMAL_CHECKED_AT,
        generatedAt=FORMAL_CHECKED_AT,
    )
    modules = []
    for module in FORMAL_MODULES:
        ready = module == enabled_module
        gates = tuple(
            FormalGateState(gate=gate, state="ready")
            for gate in FROZEN_REQUIRED_FORMAL_GATES
        )
        if not ready:
            gates = (
                FormalGateState(
                    gate="stage9_quality",
                    state="not_ready",
                    reasonCodes=("stage9_domain_not_ready",),
                ),
            )
        modules.append(FormalModuleReadiness(
            module=module,
            state="ready_to_enable" if ready else "not_ready",
            requested=False,
            configuredEnabled=False,
            formalEnabled=False,
            observedTradingDays=(
                FORMAL_MODULE_REQUIRED_TRADING_DAYS[module]
                if ready
                else 0
            ),
            requiredTradingDays=FORMAL_MODULE_REQUIRED_TRADING_DAYS[module],
            lastObservedTradingDate=(
                FORMAL_CHECKED_AT.date() if ready else None
            ),
            gates=gates,
            reasonCodes=() if ready else ("stage9_domain_not_ready",),
        ))
    return RadarFormalReadiness(
        checkedAt=FORMAL_CHECKED_AT,
        freshnessPolicy=RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=86400,
            evidenceMaxAgeSeconds=86400,
            operationalChecksMaxAgeSeconds=86400,
        ),
        state="ready_to_enable" if enabled_module is not None else "not_ready",
        anyFormalEnabled=False,
        allModulesFormalEnabled=False,
        stage9ReplayRunId="stage9-run",
        stage9QualitySha256="a" * 64,
        stage9QualityState="ready",
        modules=tuple(modules),
        evidence=(evidence, operational_evidence),
    )


def _formal_load_result(
    report=None,
    *,
    status="available",
    stored_ref=_AUTO_STORED_REF,
):
    from radar.formal_readiness_store import (
        FormalReadinessLoadResult,
        StoredFormalReadinessRef,
    )

    if stored_ref is _AUTO_STORED_REF:
        content_sha256 = (
            _formal_report_sha256(report)
            if status == "available" and report is not None
            else None
        )
        stored_ref = (
            StoredFormalReadinessRef(
                status="available",
                content_sha256=content_sha256,
                relative_path=f"reports/{content_sha256}.json",
                checked_at=report.checked_at,
            )
            if status == "available" and report is not None
            else None
        )

    return FormalReadinessLoadResult(
        status=status,
        reason_codes=(() if status == "available" else (f"{status}_report",)),
        report=report,
        stored_ref=stored_ref,
    )


def _formal_report_sha256(report):
    payload = report.model_dump(mode="json", by_alias=True, warnings="none")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def _hold_private_tmp_lock(lock_path, ready, release):
    from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock

    lock = PrivateTmpNoFollowFileLock(lock_path, protect_root=True)
    if not lock.acquire(blocking=False):
        raise RuntimeError("子进程未能获得正式测试锁")
    ready.set()
    try:
        if not release.wait(5):
            raise RuntimeError("测试未及时释放正式子进程锁")
    finally:
        lock.release()


class _FormalExecutor:
    def __init__(
        self,
        *,
        module="trendRotation",
        executor_id=None,
        contract_version=None,
        error=None,
        result_clock=None,
    ):
        self.module = module
        self.executor_id = executor_id or f"{module}-executor-v1"
        self.contract_version = (
            contract_version or f"{module}-executor-contract-v1"
        )
        self.error = error
        self.result_clock = result_clock
        self.contexts = []

    def __call__(self, context):
        from radar.formal_execution_contracts import FormalExecutionResult

        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        result_time = (
            self.result_clock()
            if self.result_clock is not None
            else context.readiness.checked_at
        )
        return FormalExecutionResult(
            status="completed",
            startedAt=result_time,
            finishedAt=result_time,
        )


class CrossProcessFileLockTests(unittest.TestCase):
    def test_same_instance_reentry_is_rejected_and_release_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
            lock = CrossProcessFileLock(Path(temp_dir) / "radar.lock")

            self.assertTrue(lock.acquire(blocking=False))
            self.assertFalse(lock.acquire(blocking=False))
            lock.release()
            lock.release()
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()

    def test_file_lock_blocks_another_process_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
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
        self.temp_dir = tempfile.TemporaryDirectory(dir="/private/tmp")
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

    def formal_spec(
        self,
        module="trendRotation",
        *,
        executor=None,
        job_id=None,
        config_sha256="c" * 64,
        binding_loader=None,
        lock_path=None,
        interval_seconds=180,
    ):
        from radar.formal_execution_contracts import (
            FORMAL_JOB_IDS,
            build_formal_execution_binding,
        )
        from radar.formal_execution_guard import (
            FormalExecutionBindingLoadResult,
        )
        from radar.scheduler import FormalJobSpec

        executor = executor or _FormalExecutor(module=module)
        actual_job_id = job_id or FORMAL_JOB_IDS[module]
        if binding_loader is None:
            binding = build_formal_execution_binding(
                module=module,
                job_id=FORMAL_JOB_IDS[module],
                executor_id=executor.executor_id,
                executor_contract_version=executor.contract_version,
                config_sha256=config_sha256,
                generated_at=FORMAL_CHECKED_AT - timedelta(minutes=1),
                expires_at=FORMAL_CHECKED_AT + timedelta(days=2),
            )
            binding_loader = lambda binding=binding: (
                FormalExecutionBindingLoadResult(
                    status="available",
                    binding=binding,
                )
            )
        if lock_path is None:
            lock_root = (
                Path(self.temp_dir.name)
                / f"formal-{module}-lock-root"
            )
            lock_root.mkdir(mode=0o700, exist_ok=True)
            lock_path = lock_root / "formal.lock"
        return FormalJobSpec(
            module=module,
            job_id=actual_job_id,
            executor=executor,
            config_sha256=config_sha256,
            binding_loader=binding_loader,
            lock_path=lock_path,
            interval_seconds=interval_seconds,
        )

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

    def test_formal_empty_specs_perform_zero_reads_locks_or_scheduler_calls(self):
        from radar.scheduler import register_formal_jobs

        scheduler = Mock()
        settings_provider = Mock()
        readiness_loader = Mock()

        self.assertEqual(
            register_formal_jobs(
                scheduler,
                (),
                settings_provider=settings_provider,
                readiness_loader=readiness_loader,
            ),
            (),
        )
        settings_provider.assert_not_called()
        readiness_loader.assert_not_called()
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()
        self.assertEqual(tuple(Path(self.temp_dir.name).iterdir()), ())

    def test_formal_bad_spec_does_not_block_other_module_and_fixed_ids_reject_shadow_or_ai(self):
        from radar.scheduler import register_formal_jobs

        scheduler = Mock()
        scheduler.get_job.return_value = None
        settings = RadarSettings(
            formal_trend_requested=True,
            formal_etf_requested=True,
        )
        bad_shadow = self.formal_spec(job_id="radar-shadow-scan")
        good_etf = self.formal_spec(module="etfObservation")

        registrations = register_formal_jobs(
            scheduler,
            (bad_shadow, good_etf),
            settings_provider=lambda: settings,
            readiness_loader=lambda: _formal_load_result(
                _formal_report(enabled_module="etfObservation"),
            ),
            clock=lambda: FORMAL_CHECKED_AT,
        )

        self.assertEqual(
            [item.state for item in registrations],
            [
                ScheduleRegistrationState.DISABLED,
                ScheduleRegistrationState.REGISTERED,
            ],
        )
        self.assertEqual(
            registrations[0].reason_code,
            "formal_execution_job_identity_mismatch",
        )
        self.assertEqual(scheduler.add_job.call_count, 1)
        self.assertEqual(
            scheduler.add_job.call_args.kwargs["id"],
            "radar-formal-etf-observation",
        )

        for reserved in ("radar-shadow-stock-quotes", "radar-ai-explanation"):
            with self.subTest(reserved=reserved):
                isolated = Mock()
                result = register_formal_jobs(
                    isolated,
                    (self.formal_spec(job_id=reserved),),
                    settings_provider=lambda: settings,
                    readiness_loader=lambda: _formal_load_result(
                        _formal_report(),
                    ),
                    clock=lambda: FORMAL_CHECKED_AT,
                )
                self.assertEqual(
                    result[0].reason_code,
                    "formal_execution_job_identity_mismatch",
                )
                isolated.get_job.assert_not_called()
                isolated.add_job.assert_not_called()

    def test_non_spec_object_does_not_block_valid_formal_module(self):
        from radar.scheduler import register_formal_jobs

        scheduler = Mock()
        scheduler.get_job.return_value = None
        registrations = register_formal_jobs(
            scheduler,
            (object(), self.formal_spec(module="etfObservation")),
            settings_provider=lambda: RadarSettings(
                formal_etf_requested=True,
            ),
            readiness_loader=lambda: _formal_load_result(
                _formal_report(enabled_module="etfObservation"),
            ),
            clock=lambda: FORMAL_CHECKED_AT,
        )

        self.assertEqual(
            [item.state for item in registrations],
            [
                ScheduleRegistrationState.DISABLED,
                ScheduleRegistrationState.REGISTERED,
            ],
        )
        self.assertEqual(
            registrations[0].reason_code,
            "formal_spec_invalid",
        )
        scheduler.add_job.assert_called_once()

    def test_formal_execution_lock_contended_across_processes_calls_no_executor(self):
        from radar.scheduler import FormalRunState, register_formal_jobs

        lock_path = Path(self.temp_dir.name) / "formal-cross-process.lock"
        executor = _FormalExecutor()
        scheduler = Mock()
        scheduler.get_job.return_value = None
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(executor=executor, lock_path=lock_path),),
            settings_provider=lambda: RadarSettings(
                formal_trend_requested=True,
            ),
            readiness_loader=lambda: _formal_load_result(_formal_report()),
            clock=lambda: FORMAL_CHECKED_AT,
        )
        self.assertEqual(
            registrations[0].state,
            ScheduleRegistrationState.REGISTERED,
        )
        registered = scheduler.add_job.call_args.args[0]

        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        process = context.Process(
            target=_hold_private_tmp_lock,
            args=(lock_path, ready, release),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            competing_scheduler = Mock()
            competing_registration = register_formal_jobs(
                competing_scheduler,
                (self.formal_spec(executor=executor, lock_path=lock_path),),
                settings_provider=lambda: RadarSettings(
                    formal_trend_requested=True,
                ),
                readiness_loader=lambda: _formal_load_result(_formal_report()),
                clock=lambda: FORMAL_CHECKED_AT,
            )
            self.assertEqual(
                competing_registration[0].reason_code,
                "formal_execution_lock_contended",
            )
            competing_scheduler.get_job.assert_not_called()
            competing_scheduler.add_job.assert_not_called()

            outcome = registered()
            self.assertEqual(outcome.state, FormalRunState.BLOCKED)
            self.assertEqual(outcome.reason_code, "formal_execution_lock_contended")
            self.assertEqual(executor.contexts, [])
        finally:
            release.set()
            process.join(5)
        self.assertEqual(process.exitcode, 0)

    def test_formal_lock_root_and_file_cannot_be_replaced_while_held(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock

        spec = self.formal_spec()
        first = PrivateTmpNoFollowFileLock(
            spec.lock_path,
            protect_root=True,
        )
        self.assertTrue(first.acquire(blocking=False))
        second = PrivateTmpNoFollowFileLock(
            spec.lock_path,
            protect_root=True,
        )
        try:
            with self.assertRaises(PermissionError):
                spec.lock_path.unlink()
            with self.assertRaises(PermissionError):
                spec.lock_path.parent.rename(
                    spec.lock_path.parent.with_name("replacement-root"),
                )
            self.assertFalse(second.acquire(blocking=False))
            first.assert_still_held()
        finally:
            first.release()

        self.assertTrue(second.acquire(blocking=False))
        second.release()

    def test_formal_lock_rejects_preexisting_symlink_and_post_validation_swap(self):
        from radar.scheduler import register_formal_jobs

        for swap_after_validation in (False, True):
            with self.subTest(swap_after_validation=swap_after_validation):
                case_root = Path(self.temp_dir.name) / (
                    "swapped" if swap_after_validation else "preexisting"
                )
                case_root.mkdir()
                outside = case_root / "outside"
                outside.write_text("untouched", encoding="utf-8")
                outside.chmod(0o640)
                lock_path = case_root / "formal.lock"
                base_spec = self.formal_spec(lock_path=lock_path)
                if swap_after_validation:
                    base_loader = base_spec.binding_loader
                    swapped = {"done": False}

                    def binding_loader():
                        if not swapped["done"]:
                            lock_path.symlink_to(outside)
                            swapped["done"] = True
                        return base_loader()

                    spec = replace(base_spec, binding_loader=binding_loader)
                else:
                    lock_path.symlink_to(outside)
                    spec = base_spec

                scheduler = Mock()
                registrations = register_formal_jobs(
                    scheduler,
                    (spec,),
                    settings_provider=lambda: RadarSettings(
                        formal_trend_requested=True,
                    ),
                    readiness_loader=lambda: _formal_load_result(
                        _formal_report(),
                    ),
                    clock=lambda: FORMAL_CHECKED_AT,
                )

                self.assertEqual(
                    registrations[0].state,
                    ScheduleRegistrationState.DISABLED,
                )
                self.assertIn(
                    registrations[0].reason_code,
                    {
                        "formal_execution_lock_path_invalid",
                        "formal_execution_lock_unverified",
                    },
                )
                self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o640)
                self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
                scheduler.get_job.assert_not_called()
                scheduler.add_job.assert_not_called()

    def test_formal_registration_blocks_after_held_lock_inode_is_replaced(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
        from radar.scheduler import register_formal_jobs

        base_spec = self.formal_spec()
        base_loader = base_spec.binding_loader
        calls = {"count": 0}
        competing = {"lock": None, "acquired": False}

        def replacing_loader():
            calls["count"] += 1
            if calls["count"] == 2:
                os.chflags(
                    base_spec.lock_path.parent,
                    0,
                    follow_symlinks=False,
                )
                base_spec.lock_path.parent.chmod(0o700)
                os.chflags(base_spec.lock_path, 0, follow_symlinks=False)
                base_spec.lock_path.unlink()
                second = PrivateTmpNoFollowFileLock(base_spec.lock_path)
                competing["lock"] = second
                competing["acquired"] = second.acquire(blocking=False)
            return base_loader()

        spec = replace(base_spec, binding_loader=replacing_loader)
        scheduler = Mock()
        scheduler.get_job.return_value = None
        try:
            registrations = register_formal_jobs(
                scheduler,
                (spec,),
                settings_provider=lambda: RadarSettings(
                    formal_trend_requested=True,
                ),
                readiness_loader=lambda: _formal_load_result(
                    _formal_report(),
                ),
                clock=lambda: FORMAL_CHECKED_AT,
            )
        finally:
            if competing["lock"] is not None:
                competing["lock"].release()

        self.assertTrue(competing["acquired"])
        self.assertEqual(
            registrations[0].state,
            ScheduleRegistrationState.DISABLED,
        )
        self.assertEqual(
            registrations[0].reason_code,
            "formal_execution_lock_unverified",
        )
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()

    def test_formal_registration_rechecks_lock_after_scheduler_add_job(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
        from radar.scheduler import FormalRunState, register_formal_jobs

        executor = _FormalExecutor()
        spec = self.formal_spec(executor=executor)
        scheduled = []
        competing = {"lock": None, "acquired": False}

        def add_job(job, *_args, **_kwargs):
            scheduled.append(job)
            os.chflags(
                spec.lock_path.parent,
                0,
                follow_symlinks=False,
            )
            spec.lock_path.parent.chmod(0o700)
            os.chflags(spec.lock_path, 0, follow_symlinks=False)
            spec.lock_path.unlink()
            second = PrivateTmpNoFollowFileLock(
                spec.lock_path,
                protect_root=True,
            )
            competing["lock"] = second
            competing["acquired"] = second.acquire(blocking=False)
            return object()

        scheduler = Mock()
        scheduler.get_job.return_value = None
        scheduler.add_job.side_effect = add_job
        try:
            registrations = register_formal_jobs(
                scheduler,
                (spec,),
                settings_provider=lambda: RadarSettings(
                    formal_trend_requested=True,
                ),
                readiness_loader=lambda: _formal_load_result(
                    _formal_report(),
                ),
                clock=lambda: FORMAL_CHECKED_AT,
            )
            self.assertTrue(competing["acquired"])
            self.assertEqual(
                registrations[0].state,
                ScheduleRegistrationState.DISABLED,
            )
            self.assertEqual(
                registrations[0].reason_code,
                "formal_execution_lock_unverified",
            )
            competing["lock"].release()
            competing["lock"] = None

            outcome = scheduled[0]()
            self.assertEqual(outcome.state, FormalRunState.BLOCKED)
            self.assertEqual(
                outcome.reason_code,
                "formal_execution_lock_unverified",
            )
            self.assertEqual(executor.contexts, [])
        finally:
            if competing["lock"] is not None:
                competing["lock"].release()
            if spec.lock_path.exists():
                os.chflags(spec.lock_path, 0, follow_symlinks=False)
            os.chflags(
                spec.lock_path.parent,
                0,
                follow_symlinks=False,
            )
            spec.lock_path.parent.chmod(0o700)

    def test_formal_execution_blocks_after_held_lock_inode_is_replaced(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
        from radar.scheduler import FormalRunState, register_formal_jobs

        base_spec = self.formal_spec()
        base_loader = base_spec.binding_loader
        calls = {"count": 0}
        competing = {"lock": None, "acquired": False}

        def replacing_loader():
            calls["count"] += 1
            if calls["count"] == 4:
                os.chflags(
                    base_spec.lock_path.parent,
                    0,
                    follow_symlinks=False,
                )
                base_spec.lock_path.parent.chmod(0o700)
                os.chflags(base_spec.lock_path, 0, follow_symlinks=False)
                base_spec.lock_path.unlink()
                second = PrivateTmpNoFollowFileLock(base_spec.lock_path)
                competing["lock"] = second
                competing["acquired"] = second.acquire(blocking=False)
            return base_loader()

        executor = _FormalExecutor()
        spec = replace(
            base_spec,
            binding_loader=replacing_loader,
            executor=executor,
        )
        scheduler = Mock()
        scheduler.get_job.return_value = None
        registration = register_formal_jobs(
            scheduler,
            (spec,),
            settings_provider=lambda: RadarSettings(
                formal_trend_requested=True,
            ),
            readiness_loader=lambda: _formal_load_result(_formal_report()),
            clock=lambda: FORMAL_CHECKED_AT,
        )
        self.assertEqual(
            registration[0].state,
            ScheduleRegistrationState.REGISTERED,
        )

        try:
            outcome = scheduler.add_job.call_args.args[0]()
        finally:
            if competing["lock"] is not None:
                competing["lock"].release()

        self.assertTrue(competing["acquired"])
        self.assertEqual(outcome.state, FormalRunState.BLOCKED)
        self.assertEqual(
            outcome.reason_code,
            "formal_execution_lock_unverified",
        )
        self.assertEqual(executor.contexts, [])

    def test_formal_execution_revalidates_request_and_report_inside_lock(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.scheduler import FormalRunState, register_formal_jobs

        for revoked_kind in ("request", "report"):
            with self.subTest(revoked_kind=revoked_kind):
                executor = _FormalExecutor()
                scheduler = Mock()
                scheduler.get_job.return_value = None
                current = {
                    "settings": RadarSettings(formal_trend_requested=True),
                    "report": _formal_report(),
                    "now": FORMAL_CHECKED_AT,
                }
                settings_provider = Mock(
                    side_effect=lambda: current["settings"],
                )
                readiness_loader = Mock(
                    side_effect=lambda: _formal_load_result(current["report"]),
                )
                clock = Mock(side_effect=lambda: current["now"])
                register_formal_jobs(
                    scheduler,
                    (self.formal_spec(executor=executor),),
                    settings_provider=settings_provider,
                    readiness_loader=readiness_loader,
                    clock=clock,
                )
                registered = scheduler.add_job.call_args.args[0]
                settings_provider.reset_mock(side_effect=True)
                readiness_loader.reset_mock(side_effect=True)
                clock.reset_mock(side_effect=True)
                settings_provider.side_effect = (
                    [
                        RadarSettings(formal_trend_requested=True),
                        RadarSettings(formal_trend_requested=False),
                    ]
                    if revoked_kind == "request"
                    else [
                        RadarSettings(formal_trend_requested=True),
                        RadarSettings(formal_trend_requested=True),
                    ]
                )
                readiness_loader.side_effect = [
                    _formal_load_result(current["report"]),
                    _formal_load_result(current["report"]),
                ]
                if revoked_kind == "report":
                    policy = RadarFormalFreshnessPolicy(
                        reportMaxAgeSeconds=1,
                        evidenceMaxAgeSeconds=86400,
                        operationalChecksMaxAgeSeconds=86400,
                    )
                    current["report"] = current["report"].model_copy(
                        update={"freshness_policy": policy},
                    )
                    readiness_loader.side_effect = [
                        _formal_load_result(current["report"]),
                        _formal_load_result(current["report"]),
                    ]
                    clock.side_effect = [
                        FORMAL_CHECKED_AT,
                        FORMAL_CHECKED_AT + timedelta(seconds=2),
                    ]
                else:
                    clock.side_effect = [FORMAL_CHECKED_AT, FORMAL_CHECKED_AT]
                outcome = registered()
                self.assertEqual(outcome.state, FormalRunState.BLOCKED)
                self.assertEqual(
                    outcome.reason_code,
                    (
                        "formal_request_not_enabled"
                        if revoked_kind == "request"
                        else "formal_readiness_report_expired"
                    ),
                )
                self.assertEqual(executor.contexts, [])

    def test_formal_executor_exception_releases_lock(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
        from radar.scheduler import register_formal_jobs

        lock_path = Path(self.temp_dir.name) / "formal-error.lock"
        executor = _FormalExecutor(error=RuntimeError("executor failed"))
        scheduler = Mock()
        scheduler.get_job.return_value = None
        register_formal_jobs(
            scheduler,
            (self.formal_spec(executor=executor, lock_path=lock_path),),
            settings_provider=lambda: RadarSettings(formal_trend_requested=True),
            readiness_loader=lambda: _formal_load_result(_formal_report()),
            clock=lambda: FORMAL_CHECKED_AT,
        )
        registered = scheduler.add_job.call_args.args[0]

        with self.assertRaisesRegex(RuntimeError, "executor failed"):
            registered()
        probe = PrivateTmpNoFollowFileLock(lock_path, protect_root=True)
        self.assertTrue(probe.acquire(blocking=False))
        probe.release()

    def test_existing_formal_job_requires_exact_wrapper_and_bound_identity(self):
        from radar.scheduler import register_formal_jobs

        spec = self.formal_spec()
        settings = lambda: RadarSettings(formal_trend_requested=True)
        readiness = lambda: _formal_load_result(_formal_report())
        first_scheduler = Mock()
        first_scheduler.get_job.return_value = None
        register_formal_jobs(
            first_scheduler,
            (spec,),
            settings_provider=settings,
            readiness_loader=readiness,
            clock=lambda: FORMAL_CHECKED_AT,
        )
        registered = first_scheduler.add_job.call_args.args[0]

        same_scheduler = Mock()
        same_scheduler.get_job.return_value = SimpleNamespace(func=registered)
        same = register_formal_jobs(
            same_scheduler,
            (spec,),
            settings_provider=settings,
            readiness_loader=readiness,
            clock=lambda: FORMAL_CHECKED_AT,
        )
        self.assertEqual(
            same[0].state,
            ScheduleRegistrationState.ALREADY_REGISTERED,
        )
        same_scheduler.add_job.assert_not_called()

        class DisguisedScheduledFormalJob(type(registered)):
            pass

        disguised = DisguisedScheduledFormalJob(
            spec=registered.spec,
            settings_provider=registered.settings_provider,
            readiness_loader=registered.readiness_loader,
            clock=registered.clock,
            registered_binding_sha256=registered.registered_binding_sha256,
            registered_executor_id=registered.registered_executor_id,
            registered_executor_contract_version=(
                registered.registered_executor_contract_version
            ),
            registered_lock_identity=registered.registered_lock_identity,
        )
        conflicting_wrappers = (
            disguised,
            replace(
                registered,
                spec=replace(
                    registered.spec,
                    lock_path=(
                        registered.spec.lock_path.parent
                        / "different-formal.lock"
                    ),
                ),
            ),
            replace(
                registered,
                spec=replace(
                    registered.spec,
                    interval_seconds=registered.spec.interval_seconds + 1,
                ),
            ),
            replace(
                registered,
                spec=replace(
                    registered.spec,
                    next_run_time=FORMAL_CHECKED_AT,
                ),
            ),
        )
        for conflicting_wrapper in conflicting_wrappers:
            with self.subTest(conflicting_wrapper=conflicting_wrapper):
                conflict_scheduler = Mock()
                conflict_scheduler.get_job.return_value = SimpleNamespace(
                    func=conflicting_wrapper,
                )
                conflict = register_formal_jobs(
                    conflict_scheduler,
                    (spec,),
                    settings_provider=settings,
                    readiness_loader=readiness,
                    clock=lambda: FORMAL_CHECKED_AT,
                )
                self.assertEqual(
                    conflict[0].state,
                    ScheduleRegistrationState.CONFLICT,
                )
                conflict_scheduler.add_job.assert_not_called()

        for existing in (
            SimpleNamespace(func=lambda: None),
            SimpleNamespace(func=registered.__class__(
                spec=self.formal_spec(
                    executor=_FormalExecutor(executor_id="other-executor"),
                ),
                settings_provider=settings,
                readiness_loader=readiness,
                clock=lambda: FORMAL_CHECKED_AT,
            )),
        ):
            with self.subTest(existing=existing):
                conflict_scheduler = Mock()
                conflict_scheduler.get_job.return_value = existing
                conflict = register_formal_jobs(
                    conflict_scheduler,
                    (spec,),
                    settings_provider=settings,
                    readiness_loader=readiness,
                    clock=lambda: FORMAL_CHECKED_AT,
                )
                self.assertEqual(
                    conflict[0].state,
                    ScheduleRegistrationState.CONFLICT,
                )
                conflict_scheduler.add_job.assert_not_called()

    def test_formal_registered_wrapper_passes_frozen_context_and_validates_result(self):
        from radar.formal_execution_contracts import FormalExecutionContext
        from radar.scheduler import FormalRunState, register_formal_jobs

        executor = _FormalExecutor()
        scheduler = Mock()
        scheduler.get_job.return_value = None
        register_formal_jobs(
            scheduler,
            (self.formal_spec(executor=executor),),
            settings_provider=lambda: RadarSettings(formal_trend_requested=True),
            readiness_loader=lambda: _formal_load_result(_formal_report()),
            clock=lambda: FORMAL_CHECKED_AT,
        )
        outcome = scheduler.add_job.call_args.args[0]()
        self.assertEqual(outcome.state, FormalRunState.COMPLETED)
        self.assertIsInstance(executor.contexts[0], FormalExecutionContext)
        self.assertEqual(outcome.result.status, "completed")

    def test_formal_executor_result_times_must_stay_inside_this_execution_window(self):
        from radar.formal_execution_contracts import FormalExecutionResult
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock
        from radar.scheduler import register_formal_jobs

        class TimeForgingExecutor(_FormalExecutor):
            def __init__(self, started_at, finished_at):
                super().__init__()
                self.started_at = started_at
                self.finished_at = finished_at

            def __call__(self, context):
                self.contexts.append(context)
                return FormalExecutionResult(
                    status="completed",
                    startedAt=self.started_at,
                    finishedAt=self.finished_at,
                )

        for started_at, finished_at in (
            (
                FORMAL_CHECKED_AT - timedelta(microseconds=1),
                FORMAL_CHECKED_AT,
            ),
            (
                FORMAL_CHECKED_AT,
                FORMAL_CHECKED_AT + timedelta(microseconds=1),
            ),
        ):
            with self.subTest(
                started_at=started_at,
                finished_at=finished_at,
            ):
                executor = TimeForgingExecutor(started_at, finished_at)
                spec = self.formal_spec(executor=executor)
                scheduler = Mock()
                scheduler.get_job.return_value = None
                register_formal_jobs(
                    scheduler,
                    (spec,),
                    settings_provider=lambda: RadarSettings(
                        formal_trend_requested=True,
                    ),
                    readiness_loader=lambda: _formal_load_result(
                        _formal_report(),
                    ),
                    clock=lambda: FORMAL_CHECKED_AT,
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "formal_execution_result_window_mismatch",
                ):
                    scheduler.add_job.call_args.args[0]()

        probe = PrivateTmpNoFollowFileLock(
            spec.lock_path,
            protect_root=True,
        )
        self.assertTrue(probe.acquire(blocking=False))
        probe.release()

    def test_formal_registration_fails_closed_for_default_and_untrusted_inputs(self):
        from radar.scheduler import register_formal_jobs

        report = _formal_report()
        forged_trend = report.modules[0].model_copy(update={"gates": ()})
        forged_report = report.model_copy(update={
            "modules": (forged_trend,) + report.modules[1:],
        })
        forged_boolean_trend = report.modules[0].model_copy(
            update={"requested": "true"},
        )
        forged_boolean_report = report.model_copy(update={
            "modules": (forged_boolean_trend,) + report.modules[1:],
        })
        spec = self.formal_spec(interval_seconds=300)
        cases = (
            (
                RadarSettings(),
                _formal_load_result(_formal_report()),
            ),
            (
                RadarSettings(formal_trend_requested=True),
                _formal_load_result(status="missing"),
            ),
            (
                RadarSettings(formal_trend_requested=True),
                _formal_load_result(status="failed"),
            ),
            (
                RadarSettings(formal_trend_requested=True),
                _formal_load_result(None),
            ),
            (
                RadarSettings(formal_trend_requested=True),
                _formal_load_result(_formal_report(enabled_module=None)),
            ),
            (
                RadarSettings(formal_trend_requested=True),
                _formal_load_result(forged_report),
            ),
            (
                RadarSettings(formal_trend_requested=True),
                _formal_load_result(forged_boolean_report),
            ),
        )
        for settings, load_result in cases:
            with self.subTest(settings=settings, status=load_result.status):
                scheduler = Mock()
                registrations = register_formal_jobs(
                    scheduler,
                    (spec,),
                    settings_provider=lambda settings=settings: settings,
                    readiness_loader=lambda load_result=load_result: load_result,
                )

                self.assertEqual(
                    registrations[0].state,
                    ScheduleRegistrationState.DISABLED,
                )
                scheduler.get_job.assert_not_called()
                scheduler.add_job.assert_not_called()

    def test_formal_registration_selects_only_eligible_module_and_is_single_instance(self):
        from radar.scheduler import register_formal_jobs

        scheduler = Mock()
        scheduler.get_job.return_value = None
        trend_executor = _FormalExecutor(module="trendRotation")
        etf_executor = _FormalExecutor(module="etfObservation")
        settings = RadarSettings(
            formal_trend_requested=True,
            formal_etf_requested=True,
        )

        registrations = register_formal_jobs(
            scheduler,
            (
                self.formal_spec(executor=trend_executor),
                self.formal_spec(
                    module="etfObservation",
                    executor=etf_executor,
                    interval_seconds=300,
                ),
            ),
            settings_provider=lambda: settings,
            readiness_loader=lambda: _formal_load_result(_formal_report()),
            clock=lambda: FORMAL_CHECKED_AT,
        )

        self.assertEqual(
            [item.state for item in registrations],
            [
                ScheduleRegistrationState.REGISTERED,
                ScheduleRegistrationState.DISABLED,
            ],
        )
        scheduler.add_job.assert_called_once()
        call = scheduler.add_job.call_args
        self.assertEqual(call.args[1], "interval")
        self.assertEqual(call.kwargs, {
            "id": "radar-formal-trend-rotation",
            "seconds": 180,
            "max_instances": 1,
            "coalesce": True,
            "replace_existing": False,
            "misfire_grace_time": 180,
        })
        self.assertIsNot(call.args[0], trend_executor)
        self.assertEqual(trend_executor.contexts, [])
        self.assertEqual(etf_executor.contexts, [])

    def test_formal_job_revalidates_withdrawal_before_executor(self):
        from radar.scheduler import (
            FormalRunState,
            register_formal_jobs,
        )

        scheduler = Mock()
        scheduler.get_job.return_value = None
        executor = _FormalExecutor()
        current = {
            "settings": RadarSettings(formal_trend_requested=True),
            "readiness": _formal_load_result(_formal_report()),
        }
        register_formal_jobs(
            scheduler,
            (self.formal_spec(executor=executor),),
            settings_provider=lambda: current["settings"],
            readiness_loader=lambda: current["readiness"],
            clock=lambda: FORMAL_CHECKED_AT,
        )
        registered_job = scheduler.add_job.call_args.args[0]

        completed = registered_job()
        self.assertEqual(completed.state, FormalRunState.COMPLETED)
        self.assertEqual(completed.result.status, "completed")
        self.assertEqual(len(executor.contexts), 1)

        current["readiness"] = _formal_load_result(status="missing")
        outcome = registered_job()

        self.assertEqual(outcome.state, FormalRunState.BLOCKED)
        self.assertEqual(outcome.reason_code, "formal_readiness_unavailable")
        self.assertEqual(len(executor.contexts), 1)

    def test_formal_guard_rejects_future_report_and_unverified_clock(self):
        from radar.scheduler import (
            FormalRunState,
            register_formal_jobs,
        )

        self.assertIn("clock", inspect.signature(register_formal_jobs).parameters)
        report = _formal_report()
        executor = _FormalExecutor()
        spec = self.formal_spec(executor=executor)
        future_scheduler = Mock()
        future_scheduler.get_job.return_value = None
        registration = register_formal_jobs(
            future_scheduler,
            (spec,),
            settings_provider=lambda: RadarSettings(
                formal_trend_requested=True,
            ),
            readiness_loader=lambda: _formal_load_result(report),
            clock=lambda: report.checked_at - timedelta(microseconds=1),
        )
        self.assertEqual(
            registration[0].state,
            ScheduleRegistrationState.DISABLED,
        )
        future_scheduler.add_job.assert_not_called()

        current = {"now": report.checked_at}
        scheduler = Mock()
        scheduler.get_job.return_value = None
        registration = register_formal_jobs(
            scheduler,
            (spec,),
            settings_provider=lambda: RadarSettings(
                formal_trend_requested=True,
            ),
            readiness_loader=lambda: _formal_load_result(report),
            clock=lambda: current["now"],
        )
        self.assertEqual(
            registration[0].state,
            ScheduleRegistrationState.REGISTERED,
        )
        registered_job = scheduler.add_job.call_args.args[0]

        current["now"] = report.checked_at - timedelta(microseconds=1)
        outcome = registered_job()
        self.assertEqual(outcome.state, FormalRunState.BLOCKED)
        self.assertEqual(
            outcome.reason_code,
            "formal_readiness_checked_at_future",
        )
        self.assertEqual(executor.contexts, [])

        current["now"] = report.checked_at.replace(tzinfo=None)
        outcome = registered_job()
        self.assertEqual(outcome.state, FormalRunState.BLOCKED)
        self.assertEqual(outcome.reason_code, "formal_clock_unverified")
        self.assertEqual(executor.contexts, [])

        broken_scheduler = Mock()
        registrations = register_formal_jobs(
            broken_scheduler,
            (spec,),
            settings_provider=lambda: RadarSettings(
                formal_trend_requested=True,
            ),
            readiness_loader=lambda: _formal_load_result(report),
            clock=Mock(side_effect=RuntimeError("clock unavailable")),
        )
        self.assertEqual(
            registrations[0].state,
            ScheduleRegistrationState.DISABLED,
        )
        broken_scheduler.add_job.assert_not_called()

    def test_formal_guard_rejects_missing_and_expired_freshness_on_registration(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.scheduler import register_formal_jobs

        report = _formal_report().model_copy(update={"freshness_policy": None})
        spec_now = report.checked_at
        settings = lambda: RadarSettings(formal_trend_requested=True)
        scheduler = Mock()
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(),),
            settings_provider=settings,
            readiness_loader=lambda: _formal_load_result(report),
            clock=lambda: spec_now,
        )
        self.assertEqual(
            registrations[0].reason_code,
            "formal_freshness_policy_missing",
        )

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=600,
        )
        fresh_report = report.model_copy(update={"freshness_policy": policy})
        scheduler = Mock()
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(),),
            settings_provider=settings,
            readiness_loader=lambda: _formal_load_result(fresh_report),
            clock=lambda: spec_now + timedelta(seconds=300, microseconds=1),
        )
        self.assertEqual(
            registrations[0].reason_code,
            "formal_readiness_report_expired",
        )

    def test_formal_job_rechecks_freshness_on_every_execution(self):
        from radar.formal_readiness_contracts import RadarFormalFreshnessPolicy
        from radar.scheduler import FormalRunState, register_formal_jobs

        policy = RadarFormalFreshnessPolicy(
            reportMaxAgeSeconds=300,
            evidenceMaxAgeSeconds=600,
            operationalChecksMaxAgeSeconds=600,
        )
        report = _formal_report().model_copy(update={"freshness_policy": policy})
        current = {"now": report.checked_at + timedelta(seconds=300)}
        executor = _FormalExecutor(result_clock=lambda: current["now"])
        scheduler = Mock()
        scheduler.get_job.return_value = None
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(executor=executor),),
            settings_provider=lambda: RadarSettings(formal_trend_requested=True),
            readiness_loader=lambda: _formal_load_result(report),
            clock=lambda: current["now"],
        )
        self.assertEqual(registrations[0].state, ScheduleRegistrationState.REGISTERED)
        registered = scheduler.add_job.call_args.args[0]
        self.assertEqual(registered().state, FormalRunState.COMPLETED)

        current["now"] += timedelta(microseconds=1)
        outcome = registered()
        self.assertEqual(outcome.state, FormalRunState.BLOCKED)
        self.assertEqual(outcome.reason_code, "formal_readiness_report_expired")
        self.assertEqual(len(executor.contexts), 1)

    def test_formal_guard_rejects_non_native_request_booleans(self):
        from radar.scheduler import register_formal_jobs

        report = _formal_report()
        spec = self.formal_spec()
        for invalid in ("false", "true", 0, 1):
            with self.subTest(invalid=invalid):
                scheduler = Mock()
                scheduler.get_job.return_value = None
                registrations = register_formal_jobs(
                    scheduler,
                    (spec,),
                    settings_provider=lambda invalid=invalid: RadarSettings(
                        formal_trend_requested=invalid,
                    ),
                    readiness_loader=lambda: _formal_load_result(report),
                    clock=lambda: report.checked_at,
                )

                self.assertEqual(
                    registrations[0].state,
                    ScheduleRegistrationState.DISABLED,
                )
                scheduler.get_job.assert_not_called()
                scheduler.add_job.assert_not_called()

    def test_formal_guard_requires_bound_valid_stored_reference(self):
        from radar.formal_readiness_store import StoredFormalReadinessRef
        from radar.scheduler import (
            FormalRunState,
            ScheduledFormalJob,
            register_formal_jobs,
        )

        report = _formal_report()
        executor = _FormalExecutor()
        spec = self.formal_spec(executor=executor)
        valid_ref = StoredFormalReadinessRef(
            status="available",
            content_sha256="b" * 64,
            relative_path="reports/" + "b" * 64 + ".json",
            checked_at=report.checked_at,
        )
        cases = (
            (None, "formal_readiness_reference_missing"),
            (
                StoredFormalReadinessRef(
                    status="available",
                    content_sha256="not-a-sha",
                    relative_path="reports/not-a-sha.json",
                    checked_at=report.checked_at,
                ),
                "formal_readiness_reference_invalid",
            ),
            (
                StoredFormalReadinessRef(
                    status="available",
                    content_sha256=valid_ref.content_sha256,
                    relative_path=valid_ref.relative_path,
                    checked_at=report.checked_at + timedelta(microseconds=1),
                ),
                "formal_readiness_reference_time_mismatch",
            ),
        )
        for stored_ref, reason in cases:
            with self.subTest(reason=reason):
                load_result = _formal_load_result(
                    report,
                    stored_ref=stored_ref,
                )
                scheduler = Mock()
                scheduler.get_job.return_value = None
                registrations = register_formal_jobs(
                    scheduler,
                    (spec,),
                    settings_provider=lambda: RadarSettings(
                        formal_trend_requested=True,
                    ),
                    readiness_loader=lambda: load_result,
                    clock=lambda: report.checked_at,
                )
                self.assertEqual(
                    registrations[0].state,
                    ScheduleRegistrationState.DISABLED,
                )
                scheduler.add_job.assert_not_called()

                outcome = ScheduledFormalJob(
                    spec=spec,
                    settings_provider=lambda: RadarSettings(
                        formal_trend_requested=True,
                    ),
                    readiness_loader=lambda: load_result,
                    clock=lambda: report.checked_at,
                    registered_binding_sha256=(
                        spec.binding_loader().binding.content_sha256
                    ),
                )()
                self.assertEqual(outcome.state, FormalRunState.BLOCKED)
                self.assertEqual(outcome.reason_code, reason)
                self.assertEqual(executor.contexts, [])

    def test_formal_registration_rejects_reference_hash_not_bound_to_report(self):
        from radar.formal_readiness_store import StoredFormalReadinessRef
        from radar.scheduler import (
            register_formal_jobs,
        )

        report = _formal_report()
        forged_ref = StoredFormalReadinessRef(
            status="available",
            content_sha256="b" * 64,
            relative_path="reports/" + "b" * 64 + ".json",
            checked_at=report.checked_at,
        )
        load_result = _formal_load_result(report, stored_ref=forged_ref)
        settings_provider = lambda: RadarSettings(formal_trend_requested=True)
        scheduler = Mock()
        scheduler.get_job.return_value = None
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(),),
            settings_provider=settings_provider,
            readiness_loader=lambda: load_result,
            clock=lambda: report.checked_at,
        )
        self.assertEqual(
            registrations[0].state,
            ScheduleRegistrationState.DISABLED,
        )
        self.assertEqual(
            registrations[0].reason_code,
            "formal_readiness_reference_hash_mismatch",
        )
        scheduler.add_job.assert_not_called()

    def test_formal_execution_rechecks_reference_hash_on_every_run(self):
        from radar.formal_readiness_store import StoredFormalReadinessRef
        from radar.scheduler import (
            FormalRunState,
            register_formal_jobs,
        )

        report = _formal_report()
        actual_sha256 = _formal_report_sha256(report)
        valid_ref = StoredFormalReadinessRef(
            status="available",
            content_sha256=actual_sha256,
            relative_path=f"reports/{actual_sha256}.json",
            checked_at=report.checked_at,
        )
        forged_ref = StoredFormalReadinessRef(
            status="available",
            content_sha256="b" * 64,
            relative_path="reports/" + "b" * 64 + ".json",
            checked_at=report.checked_at,
        )
        current = {
            "load_result": _formal_load_result(report, stored_ref=valid_ref),
        }
        executor = _FormalExecutor()
        scheduler = Mock()
        scheduler.get_job.return_value = None
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(executor=executor),),
            settings_provider=lambda: RadarSettings(
                formal_trend_requested=True,
            ),
            readiness_loader=lambda: current["load_result"],
            clock=lambda: report.checked_at,
        )
        self.assertEqual(
            registrations[0].state,
            ScheduleRegistrationState.REGISTERED,
        )
        registered_job = scheduler.add_job.call_args.args[0]

        for run_number in range(2):
            with self.subTest(run_number=run_number):
                calls_before_blocked_run = len(executor.contexts)
                current["load_result"] = _formal_load_result(
                    report,
                    stored_ref=forged_ref,
                )
                outcome = registered_job()
                self.assertEqual(outcome.state, FormalRunState.BLOCKED)
                self.assertEqual(
                    outcome.reason_code,
                    "formal_readiness_reference_hash_mismatch",
                )
                self.assertEqual(
                    len(executor.contexts),
                    calls_before_blocked_run,
                )
                current["load_result"] = _formal_load_result(
                    report,
                    stored_ref=valid_ref,
                )
                self.assertEqual(
                    registered_job().state,
                    FormalRunState.COMPLETED,
                )

    def test_formal_duplicate_specs_conflict_without_scheduler_side_effect(self):
        from radar.scheduler import register_formal_jobs

        scheduler = Mock()
        settings_provider = Mock()
        readiness_loader = Mock()
        registrations = register_formal_jobs(
            scheduler,
            (self.formal_spec(), self.formal_spec()),
            settings_provider=settings_provider,
            readiness_loader=readiness_loader,
        )

        self.assertEqual(
            [item.state for item in registrations],
            [
                ScheduleRegistrationState.CONFLICT,
                ScheduleRegistrationState.CONFLICT,
            ],
        )
        self.assertTrue(all(
            item.reason_code == "formal_job_id_duplicate"
            for item in registrations
        ))
        settings_provider.assert_not_called()
        readiness_loader.assert_not_called()
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()

    def test_formal_modules_cannot_share_one_cross_process_lock(self):
        from radar.scheduler import register_formal_jobs

        shared_lock = Path(self.temp_dir.name) / "shared-formal.lock"
        scheduler = Mock()
        settings_provider = Mock()
        readiness_loader = Mock()
        registrations = register_formal_jobs(
            scheduler,
            (
                self.formal_spec(lock_path=shared_lock),
                self.formal_spec(
                    module="etfObservation",
                    lock_path=shared_lock,
                ),
            ),
            settings_provider=settings_provider,
            readiness_loader=readiness_loader,
        )

        self.assertTrue(all(
            item.state == ScheduleRegistrationState.CONFLICT
            and item.reason_code == "formal_lock_path_not_independent"
            for item in registrations
        ))
        settings_provider.assert_not_called()
        readiness_loader.assert_not_called()
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()

    def test_formal_modules_cannot_share_one_immutable_lock_root(self):
        from radar.scheduler import register_formal_jobs

        shared_root = Path(self.temp_dir.name) / "shared-formal-root"
        shared_root.mkdir(mode=0o700)
        scheduler = Mock()
        settings_provider = Mock()
        readiness_loader = Mock()
        registrations = register_formal_jobs(
            scheduler,
            (
                self.formal_spec(lock_path=shared_root / "trend.lock"),
                self.formal_spec(
                    module="etfObservation",
                    lock_path=shared_root / "etf.lock",
                ),
            ),
            settings_provider=settings_provider,
            readiness_loader=readiness_loader,
        )

        self.assertTrue(all(
            item.state == ScheduleRegistrationState.CONFLICT
            and item.reason_code == "formal_lock_root_not_independent"
            for item in registrations
        ))
        settings_provider.assert_not_called()
        readiness_loader.assert_not_called()
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
