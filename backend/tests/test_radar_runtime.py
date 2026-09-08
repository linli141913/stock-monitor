import sqlite3
import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import market_calendar
import monitoring_health
from radar.config import RadarSettings
from radar.contracts import (
    EtfRegistryRecord,
    IndexQuoteSnapshot,
    IndustryClassificationCompleteness,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceStatus,
)
from radar.etf_repository import EtfRepository
from radar.migrations import (
    STAGE5_RADAR_MIGRATIONS,
    STAGE6_RADAR_MIGRATIONS,
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    apply_pending_migrations,
)
from radar.repository import RadarRepository
from radar.leader_research_runtime_provider import (
    LeaderResearchRuntimeSourceContext,
    build_verified_leader_research_provider_input,
)
from radar.runtime import (
    RADAR_ETF_QUOTES_JOB_ID,
    RADAR_ETF_PRODUCT_MASTER_JOB_ID,
    RADAR_MARKET_FEATURES_JOB_ID,
    RADAR_REGISTRY_JOB_ID,
    RADAR_SECTOR_FEATURES_JOB_ID,
    RADAR_STOCK_QUOTES_JOB_ID,
    HealthTrackedJob,
    LOGGER as RUNTIME_LOGGER,
    RadarRuntime,
    register_production_shadow_jobs,
)
from radar.run_lock import CrossProcessFileLock
from radar.scheduler import (
    ScheduleRegistrationState,
    ScheduledRunOutcome,
    ScheduledRunState,
)
from radar.scoped_runner import RadarTaskScope
from radar.shadow_runner import ShadowRunExecutionError, ShadowSources
from radar.sources.market_indices import MARKET_INDEX_IDENTITIES


UTC = timezone.utc
TRADE_AS_OF = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
LUNCH_AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
REGISTRY_AS_OF = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)


def market_provider(status_code="trading", day_kind="full"):
    def provider(_market, now):
        return (
            market_calendar.MarketStatus(status_code, status_code),
            market_calendar.CalendarDay(
                day_kind,
                "https://example.test/calendar",
                now.isoformat(),
            ),
        )
    return provider


class RadarRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.database_path = Path(self.temp_dir.name) / "radar-runtime.db"
        self.lock_path = Path(self.temp_dir.name) / "radar-shadow.lock"
        self.sector_lock_path = (
            Path(self.temp_dir.name) / "radar-sector-shadow.lock"
        )
        self.market_lock_path = (
            Path(self.temp_dir.name) / "radar-market-shadow.lock"
        )
        with sqlite3.connect(self.database_path) as connection:
            apply_pending_migrations(connection)
        self.settings = RadarSettings(enabled=True, shadow_mode=True)
        self.security_fetcher = Mock(side_effect=self.security_batch)
        self.etf_fetcher = Mock(side_effect=self.etf_batch)
        self.quote_fetcher = Mock(side_effect=self.quote_batch)
        self.market_index_fetcher = Mock(side_effect=self.market_index_batch)
        self.market_quote_fetcher = Mock(side_effect=self.market_quote_batch)
        self.sources = ShadowSources(
            security_master=self.security_fetcher,
            etf_registry=self.etf_fetcher,
            quotes=self.quote_fetcher,
        )
        monitoring_health.reset_runtime_health()

    def test_health_tracked_job_logs_structured_degraded_outcome(self):
        tracked = HealthTrackedJob(
            "radarMarketFeatures",
            Mock(return_value=ScheduledRunOutcome(
                state=ScheduledRunState.COMPLETED,
                radar_run_id="radar-market-test",
                result_status="degraded",
                item_count=17,
                gate_passed=False,
                gate_reasons=("source_time_stale",),
                duration_seconds=1.234,
            )),
        )

        with self.assertLogs("radar.runtime", level="WARNING") as logs:
            tracked()

        message = "\n".join(logs.output)
        self.assertIn("task=radarMarketFeatures", message)
        self.assertIn("state=completed", message)
        self.assertIn("status=degraded", message)
        self.assertIn("gate_passed=false", message)
        self.assertIn("gate_reasons=source_time_stale", message)
        self.assertIn("item_count=17", message)
        self.assertIn("duration_ms=1234", message)

    def test_runtime_outcome_logger_keeps_info_results_visible(self):
        self.assertTrue(RUNTIME_LOGGER.isEnabledFor(20))
        self.assertFalse(RUNTIME_LOGGER.propagate)
        self.assertTrue(any(
            handler.level <= 20
            for handler in RUNTIME_LOGGER.handlers
        ))

    def tearDown(self):
        monitoring_health.reset_runtime_health()
        self.temp_dir.cleanup()

    @staticmethod
    def security_batch(run_id, batch_id, as_of):
        fetched_at = as_of + timedelta(seconds=2)
        record = SecurityMasterRecord(
            symbol="000001",
            name="平安银行",
            exchange="szse",
            board="主板",
            listingDate="1991-04-03",
            sourceReportDate=as_of.date(),
            source="szse",
            fetchedAt=fetched_at,
            sourceFields={"证券代码": "000001"},
        )
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=run_id,
                batchId=batch_id,
                source="official_exchange_security_master",
                asOf=as_of,
                fetchedAt=fetched_at,
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

    @staticmethod
    def etf_batch(run_id, batch_id, as_of):
        fetched_at = as_of + timedelta(seconds=2)
        record = EtfRegistryRecord(
            symbol="510300",
            name="沪深300ETF",
            exchange="sse",
            sourceType="股票ETF",
            sourceReportDate=as_of.date(),
            source="sse",
            fetchedAt=fetched_at,
            sourceFields={"基金代码": "510300"},
        )
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=run_id,
                batchId=batch_id,
                source="official_exchange_etf_registry",
                asOf=as_of,
                fetchedAt=fetched_at,
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

    @staticmethod
    def quote_batch(symbols, run_id, batch_id, as_of):
        fetched_at = as_of + timedelta(seconds=2)
        records = [
            QuoteSnapshot(
                symbol=symbol,
                name=f"证券{symbol}",
                sourceTime=as_of - timedelta(seconds=30),
                fetchedAt=fetched_at,
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
                asOf=as_of,
                sourceTime=as_of - timedelta(seconds=30),
                fetchedAt=fetched_at,
                expectedCount=len(records),
                returnedCount=len(records),
                rowCoverage=1.0 if records else 0.0,
                requiredFieldCoverage={
                    "price": 1.0 if records else 0.0,
                    "source_time": 1.0 if records else 0.0,
                    "change_percent": 1.0 if records else 0.0,
                    "turnover_amount_source": 1.0 if records else 0.0,
                    "market_cap_source": 1.0 if records else 0.0,
                },
            ),
            items=records,
        )

    @staticmethod
    def market_index_batch(run_id, batch_id, as_of):
        source_time = as_of - timedelta(seconds=30)
        items = [
            IndexQuoteSnapshot(
                indexKey=identity.index_key,
                symbol=identity.symbol,
                name=identity.name,
                exchange=identity.exchange,
                sourceSymbol=identity.source_symbol,
                sourceTime=source_time,
                fetchedAt=as_of,
                price=0.0,
                changePercent=0.0,
            )
            for identity in MARKET_INDEX_IDENTITIES
        ]
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=run_id,
                batchId=batch_id,
                source="tencent_finance_indices",
                asOf=as_of,
                sourceTime=source_time,
                fetchedAt=as_of,
                expectedCount=4,
                returnedCount=4,
                rowCoverage=1.0,
                requiredFieldCoverage={
                    "price": 1.0,
                    "change_percent": 1.0,
                    "source_time": 1.0,
                },
            ),
            items=items,
        )

    @staticmethod
    def market_quote_batch(symbols, run_id, batch_id, as_of):
        source_time = as_of - timedelta(seconds=30)
        items = [
            QuoteSnapshot(
                symbol=symbol,
                name=f"证券{symbol}",
                sourceTime=source_time,
                fetchedAt=as_of,
                price=0.0,
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
                asOf=as_of,
                sourceTime=source_time,
                fetchedAt=as_of,
                expectedCount=len(symbols),
                returnedCount=len(items),
                rowCoverage=1.0 if items else 0.0,
                requiredFieldCoverage={
                    "price": 1.0 if items else 0.0,
                    "source_time": 1.0 if items else 0.0,
                    "change_percent": 1.0 if items else 0.0,
                    "turnover_amount_source": 1.0 if items else 0.0,
                },
            ),
            items=items,
        )

    def runtime(
        self,
        *,
        now=TRADE_AS_OF,
        provider=None,
        connection_factory=None,
        settings=None,
        leader_history_input_provider=None,
        leader_business_catalyst_input_provider=None,
        leader_tradability_input_provider=None,
        leader_sector_rule_input_provider=None,
        leader_research_input_provider=None,
        leader_formal_research_source_provenance_provider=None,
        leader_observation_publisher=None,
    ):
        kwargs = {}
        if connection_factory is not None:
            kwargs["connection_factory"] = connection_factory
        return RadarRuntime(
            database_path=self.database_path,
            lock_path=self.lock_path,
            sector_lock_path=self.sector_lock_path,
            market_lock_path=self.market_lock_path,
            settings=settings or self.settings,
            sources=self.sources,
            sector_quote_fetcher=self.quote_fetcher,
            market_index_fetcher=self.market_index_fetcher,
            market_quote_fetcher=self.market_quote_fetcher,
            clock=lambda: now,
            market_status_provider=provider or market_provider(),
            leader_research_input_provider=(
                leader_research_input_provider
            ),
            leader_history_input_provider=leader_history_input_provider,
            leader_business_catalyst_input_provider=(
                leader_business_catalyst_input_provider
            ),
            leader_tradability_input_provider=(
                leader_tradability_input_provider
            ),
            leader_sector_rule_input_provider=(
                leader_sector_rule_input_provider
            ),
            leader_formal_research_source_provenance_provider=(
                leader_formal_research_source_provenance_provider
            ),
            leader_observation_publisher=leader_observation_publisher,
            **kwargs,
        )

    def sector_settings(self):
        return RadarSettings(
            enabled=True,
            shadow_mode=True,
            sector_shadow_enabled=True,
        )

    def market_settings(self):
        return RadarSettings(
            enabled=True,
            shadow_mode=True,
            market_shadow_enabled=True,
        )

    def all_feature_settings(self):
        return RadarSettings(
            enabled=True,
            shadow_mode=True,
            sector_shadow_enabled=True,
            market_shadow_enabled=True,
        )

    def leader_stage6_settings(self):
        return RadarSettings(
            enabled=True,
            shadow_mode=True,
            sector_shadow_enabled=True,
            market_shadow_enabled=True,
            leader_stage6_enabled=True,
        )

    def seed_universes(self, as_of=None):
        as_of = as_of or (TRADE_AS_OF - timedelta(days=1))
        with sqlite3.connect(self.database_path) as connection:
            repository = RadarRepository(connection, clock=lambda: as_of)
            repository.sync_security_master(
                self.security_batch("seed", "seed-security", as_of)
            )
            repository.sync_etf_registry(
                self.etf_batch("seed", "seed-etf", as_of)
            )

    def seed_industry_classification(self, as_of=None):
        as_of = as_of or (TRADE_AS_OF - timedelta(days=1))
        observed_at = as_of - timedelta(days=1)
        release = IndustryClassificationRelease(
            schemeVersion="capco-guideline-2023",
            releasePeriod="2025H2",
            sourcePageTitle="2025年下半年上市公司行业分类结果",
            publicationPageUrl="https://example.test/release.html",
            documentUrl="https://example.test/result.pdf",
            documentSha256="a" * 64,
            publishedDate="2026-04-03",
            firstObservedAt=observed_at,
            fetchedAt=observed_at + timedelta(seconds=1),
            knowledgeEffectiveFrom=observed_at,
            classificationStartDate="2025-12-20",
            historyStatus="retrospective_unverified",
            sourceRecordCount=1,
            uniqueSourceSymbolCount=1,
            requiredFieldCoverage={"divisionCode": 1.0},
        )
        record = IndustryClassificationRecord(
            releasePeriod="2025H2",
            sourceSymbol="000001",
            sourceName="平安银行",
            securityIdentity="000001",
            identityStatus="exact",
            categoryCode="J",
            categoryName="金融业",
            divisionCode="66",
            divisionName="货币金融服务",
            recordStatus="accepted",
            issueCodes=(),
            sourceFields={"证券代码": "000001"},
        )
        snapshot = IndustryClassificationSnapshot(
            meta=RadarBatchMeta(
                radarRunId="classification-seed",
                batchId="classification-seed-batch",
                source="capco_industry_classification",
                asOf=as_of,
                fetchedAt=as_of,
                expectedCount=1,
                returnedCount=1,
                rowCoverage=1.0,
                requiredFieldCoverage={"divisionCode": 1.0},
            ),
            status=SourceStatus.HEALTHY,
            release=release,
            records=[record],
            currentMasterGaps=[],
            completeness=IndustryClassificationCompleteness(
                sourceRecordCount=1,
                uniqueSourceSymbolCount=1,
                currentMasterCount=1,
                mappedCount=1,
                unconfirmedCount=0,
                excludedSourceCount=0,
                mappingCoverage=1.0,
                requiredFieldCoverage={"divisionCode": 1.0},
                shadowUsable=True,
                formalUsable=False,
                reasons=("formal_use_not_approved",),
            ),
        )
        with sqlite3.connect(self.database_path) as connection:
            RadarRepository(connection, clock=lambda: as_of).record_industry_classification(
                snapshot
            )

    def test_lunch_break_skips_before_lock_database_and_sources(self):
        runtime = self.runtime(
            now=LUNCH_AS_OF,
            provider=market_provider("lunch_break"),
        )

        outcome = runtime.build_job(RadarTaskScope.STOCK_QUOTES)()

        self.assertEqual(outcome.state, ScheduledRunState.SKIPPED)
        self.assertEqual(outcome.skip_reason, "lunch_break")
        self.assertFalse(self.lock_path.exists())
        self.quote_fetcher.assert_not_called()
        state = monitoring_health.get_task_states()["radarStockQuotes"]
        self.assertEqual(state["status"], "skipped")
        self.assertEqual(state["lastSkipReason"], "lunch_break")

    def test_sector_job_is_disabled_by_its_independent_default(self):
        outcome = self.runtime().build_sector_job()()

        self.assertEqual(outcome.state, ScheduledRunState.DISABLED)
        self.assertEqual(outcome.skip_reason, "feature_disabled")
        self.assertFalse(self.sector_lock_path.exists())
        self.quote_fetcher.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                0,
            )

    def test_market_job_is_disabled_by_its_independent_default(self):
        outcome = self.runtime().build_market_job()()

        self.assertEqual(outcome.state, ScheduledRunState.DISABLED)
        self.assertEqual(outcome.skip_reason, "feature_disabled")
        self.assertFalse(self.market_lock_path.exists())
        self.market_index_fetcher.assert_not_called()
        self.market_quote_fetcher.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                0,
            )

    def test_sector_job_lunch_gate_skips_before_lock_database_and_source(self):
        runtime = self.runtime(
            now=LUNCH_AS_OF,
            provider=market_provider("lunch_break"),
            settings=self.sector_settings(),
        )

        outcome = runtime.build_sector_job()()

        self.assertEqual(outcome.state, ScheduledRunState.SKIPPED)
        self.assertEqual(outcome.skip_reason, "lunch_break")
        self.assertFalse(self.sector_lock_path.exists())
        self.quote_fetcher.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                0,
            )

    def test_market_job_lunch_gate_skips_before_lock_database_and_sources(self):
        runtime = self.runtime(
            now=LUNCH_AS_OF,
            provider=market_provider("lunch_break"),
            settings=self.market_settings(),
        )

        outcome = runtime.build_market_job()()

        self.assertEqual(outcome.state, ScheduledRunState.SKIPPED)
        self.assertEqual(outcome.skip_reason, "lunch_break")
        self.assertFalse(self.market_lock_path.exists())
        self.market_index_fetcher.assert_not_called()
        self.market_quote_fetcher.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                0,
            )

    def test_sector_job_uses_temp_database_and_tracks_passed_gate_as_healthy(self):
        self.seed_universes()
        self.seed_industry_classification()
        runtime = self.runtime(settings=self.sector_settings())

        with self.assertLogs("radar.scheduler", level="INFO") as logs:
            outcome = runtime.build_sector_job()()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(outcome.result_status, "degraded")
        self.assertTrue(outcome.gate_passed)
        self.assertEqual(outcome.item_count, 1)
        state = monitoring_health.get_task_states()["radarSectorFeatures"]
        self.assertEqual(state["status"], "healthy")
        self.assertEqual(state["itemCount"], 1)
        safe_log = "\n".join(logs.output)
        self.assertNotIn("000001", safe_log)
        self.assertNotIn("平安银行", safe_log)
        self.assertNotIn("changePercent", safe_log)
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sector_feature_snapshots"
                ).fetchone()[0],
                1,
            )

    def test_market_job_uses_temp_database_and_safe_health_logging(self):
        self.seed_universes()
        runtime = self.runtime(settings=self.market_settings())

        with self.assertLogs("radar.scheduler", level="INFO") as logs:
            outcome = runtime.build_market_job()()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(outcome.result_status, "degraded")
        self.assertTrue(outcome.gate_passed)
        self.assertEqual(outcome.item_count, 1)
        state = monitoring_health.get_task_states()["radarMarketFeatures"]
        self.assertEqual(state["status"], "healthy")
        self.assertEqual(state["itemCount"], 1)
        safe_log = "\n".join(logs.output)
        self.assertNotIn("000001", safe_log)
        self.assertNotIn("510300", safe_log)
        self.assertNotIn("changePercent", safe_log)
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM market_environment_snapshots"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM market_index_feature_snapshots"
                ).fetchone()[0],
                4,
            )

    def test_stage6_default_missing_stops_before_stage6_storage(self):
        self.seed_universes()
        self.seed_industry_classification()
        runtime = self.runtime(settings=self.leader_stage6_settings())

        runtime.execute_sector("sector-run", TRADE_AS_OF)
        with patch(
            "radar.leader_research_single_pass_orchestration."
            "build_leader_runtime_evidence",
            side_effect=AssertionError("missing input built assembly"),
        ) as assembly_builder, patch(
            "radar.leader_research_single_pass_orchestration."
            "build_leader_research_readiness_runtime_batch",
            side_effect=AssertionError("missing input entered F4"),
        ) as readiness_builder:
            market_result = runtime.execute_market(
                "market-run",
                TRADE_AS_OF,
            )

        self.assertEqual(market_result.status, "degraded")
        self.assertEqual(market_result.leader_stage6_status, "missing")
        self.assertIsInstance(market_result.leader_stage6_status, str)
        self.assertIn(
            "leader_research_single_pass_provider_missing",
            market_result.leader_stage6_reasons,
        )
        self.assertIn(
            "leader_formal_research_runtime_component_history_not_configured",
            market_result.leader_stage6_reasons,
        )
        self.assertIn(
            "leader_formal_research_runtime_component_risk_source_failed",
            market_result.leader_stage6_reasons,
        )
        self.assertEqual(self.market_quote_fetcher.call_count, 1)
        assembly_builder.assert_not_called()
        readiness_builder.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name LIKE "
                    "'radar_leader_candidate_%'"
                ).fetchone()[0],
                0,
            )
        leader_health = monitoring_health.get_task_states()[
            "radarLeaderStage6"
        ]
        self.assertEqual(leader_health["status"], "degraded")
        self.assertEqual(leader_health["itemCount"], 0)
        self.assertIn(
            "leader_research_single_pass_provider_missing",
            leader_health["lastDegradationReasons"],
        )
        self.assertIn(
            "leader_formal_research_runtime_component_history_not_configured",
            leader_health["lastDegradationReasons"],
        )
        self.assertIn(
            "leader_formal_research_production_acceptance_missing",
            leader_health["lastDegradationReasons"],
        )

    def test_stage6_publishes_observation_before_formal_research_is_ready(self):
        self.seed_universes()
        self.seed_industry_classification()
        observation_publisher = Mock()
        runtime = self.runtime(
            settings=self.leader_stage6_settings(),
            leader_observation_publisher=observation_publisher,
        )

        runtime.execute_sector("sector-run", TRADE_AS_OF)
        market_result = runtime.execute_market("market-run", TRADE_AS_OF)

        self.assertEqual(market_result.leader_stage6_status, "missing")
        observation_publisher.assert_called_once()
        plan, quote_items, security_records, published_at = (
            observation_publisher.call_args.args
        )
        self.assertEqual(plan.status.value, "ready")
        self.assertEqual(tuple(item.symbol for item in plan.items), ("000001",))
        self.assertIn("000001", tuple(item.symbol for item in quote_items))
        self.assertEqual(
            tuple(item.symbol for item in security_records),
            ("000001",),
        )
        self.assertEqual(published_at, TRADE_AS_OF)
        self.assertFalse(plan.formal_usable)
        self.assertFalse(plan.state_transition_allowed)

    def test_stage6_default_provider_uses_read_only_d8_bridge_when_available(self):
        from radar.leader_formal_research_runtime_bridge import (
            build_leader_formal_research_runtime_bridge,
        )

        with sqlite3.connect(self.database_path) as connection:
            apply_pending_migrations(
                connection,
                migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            )
        self.seed_universes()
        self.seed_industry_classification()
        history_provider = Mock(return_value=None)
        business_provider = Mock(return_value=None)
        tradability_provider = Mock(return_value=None)
        sector_rule_provider = Mock(return_value=None)
        runtime = self.runtime(
            settings=self.leader_stage6_settings(),
            leader_history_input_provider=history_provider,
            leader_business_catalyst_input_provider=business_provider,
            leader_tradability_input_provider=tradability_provider,
            leader_sector_rule_input_provider=sector_rule_provider,
        )

        runtime.execute_sector("sector-run", TRADE_AS_OF)
        with patch(
            "radar.leader_formal_research_runtime_assembly."
            "build_leader_formal_research_runtime_bridge",
            wraps=build_leader_formal_research_runtime_bridge,
        ) as bridge_builder:
            market_result = runtime.execute_market(
                "market-run",
                TRADE_AS_OF,
            )

        bridge_builder.assert_called_once()
        history_provider.assert_called_once()
        business_provider.assert_called_once()
        tradability_provider.assert_called_once()
        sector_rule_provider.assert_called_once()
        self.assertEqual(market_result.leader_stage6_status, "missing")
        self.assertIn(
            "leader_research_single_pass_provider_missing",
            market_result.leader_stage6_reasons,
        )
        self.assertIn(
            "leader_formal_research_runtime_component_history_missing",
            market_result.leader_stage6_reasons,
        )
        self.assertNotIn(
            "leader_formal_research_runtime_component_history_not_configured",
            market_result.leader_stage6_reasons,
        )

    def test_stage6_provenance_provider_failure_is_degraded_without_gate_open(self):
        self.seed_universes()
        self.seed_industry_classification()
        provenance_provider = Mock(
            side_effect=RuntimeError("private provenance detail")
        )
        runtime = self.runtime(
            settings=self.leader_stage6_settings(),
            leader_formal_research_source_provenance_provider=(
                provenance_provider
            ),
        )

        runtime.execute_sector("sector-run", TRADE_AS_OF)
        market_result = runtime.execute_market(
            "market-run",
            TRADE_AS_OF,
        )

        self.assertEqual(market_result.leader_stage6_status, "missing")
        self.assertIn(
            "leader_formal_research_production_provenance_provider_failed",
            market_result.leader_stage6_reasons,
        )
        self.assertIn(
            "leader_formal_research_production_acceptance_missing",
            market_result.leader_stage6_reasons,
        )
        provenance_provider.assert_called_once()

    def test_stage6_external_provenance_cannot_replace_bound_source_proofs(self):
        self.seed_universes()
        self.seed_industry_classification()
        provenance_provider = Mock(return_value=())
        runtime = self.runtime(
            settings=self.leader_stage6_settings(),
            leader_formal_research_source_provenance_provider=(
                provenance_provider
            ),
        )

        runtime.execute_sector("sector-run", TRADE_AS_OF)
        market_result = runtime.execute_market(
            "market-run",
            TRADE_AS_OF,
        )

        self.assertEqual(market_result.leader_stage6_status, "missing")
        self.assertIn(
            "leader_formal_research_production_provenance_unbound",
            market_result.leader_stage6_reasons,
        )
        provenance_provider.assert_called_once()

    def test_stage6_granular_provider_failure_has_stable_health_reason(self):
        self.seed_universes()
        self.seed_industry_classification()
        history_provider = Mock(
            side_effect=RuntimeError("private upstream detail")
        )
        runtime = self.runtime(
            settings=self.leader_stage6_settings(),
            leader_history_input_provider=history_provider,
        )

        runtime.execute_sector("sector-run", TRADE_AS_OF)
        market_result = runtime.execute_market(
            "market-run",
            TRADE_AS_OF,
        )

        history_provider.assert_called_once()
        self.assertEqual(market_result.leader_stage6_status, "missing")
        self.assertIn(
            "leader_formal_research_runtime_component_history_source_failed",
            market_result.leader_stage6_reasons,
        )
        self.assertNotIn(
            "private upstream detail",
            str(market_result.leader_stage6_reasons),
        )
        leader_health = monitoring_health.get_task_states()[
            "radarLeaderStage6"
        ]
        self.assertEqual(leader_health["status"], "degraded")
        self.assertIn(
            "leader_formal_research_runtime_component_history_source_failed",
            leader_health["lastDegradationReasons"],
        )

    def test_stage6_provider_failure_does_not_change_market_task_result(self):
        self.seed_universes()
        self.seed_industry_classification()
        failing_provider = Mock(side_effect=RuntimeError("provider failed"))
        runtime = self.runtime(
            settings=self.leader_stage6_settings(),
            leader_research_input_provider=failing_provider,
        )
        runtime.build_sector_job()()

        with self.assertLogs(
            "radar.market_shadow_runner",
            level="ERROR",
        ):
            outcome = runtime.build_market_job()()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertTrue(outcome.gate_passed)
        failing_provider.assert_called_once()
        self.assertEqual(
            monitoring_health.get_task_states()[
                "radarMarketFeatures"
            ]["status"],
            "healthy",
        )
        self.assertEqual(
            monitoring_health.get_task_states()[
                "radarLeaderStage6"
            ]["status"],
            "failed",
        )
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM market_environment_snapshots"
                ).fetchone()[0],
                1,
            )

    def test_stage6_verified_provider_writes_analysis_only_snapshot(self):
        from tests import test_radar_leader_runtime_inputs as leader_helpers

        with sqlite3.connect(self.database_path) as connection:
            apply_pending_migrations(
                connection,
                migrations=STAGE6_RADAR_MIGRATIONS,
            )
        self.seed_universes()
        self.seed_industry_classification()
        leader_inputs = leader_helpers.LeaderRuntimeInputsTests(
            methodName=(
                "test_future_market_snapshot_is_rejected_before_"
                "candidate_build"
            )
        )
        def verified_provider(context):
            self.assertIsInstance(
                context,
                LeaderResearchRuntimeSourceContext,
            )
            plan = context.candidate_plan
            return build_verified_leader_research_provider_input(
                context,
                provider_contract_id="fixture-research-provider-v1",
                history_inputs_by_symbol={
                    item.symbol: leader_inputs.history_input(item.symbol)
                    for item in plan.items
                },
            )

        provider = Mock(side_effect=verified_provider)
        business_provider = Mock(
            side_effect=AssertionError("explicit provider must win")
        )
        tradability_provider = Mock(
            side_effect=AssertionError("explicit provider must win")
        )
        sector_rule_provider = Mock(return_value=None)
        runtime = self.runtime(
            now=leader_helpers.AS_OF,
            settings=self.leader_stage6_settings(),
            leader_business_catalyst_input_provider=business_provider,
            leader_tradability_input_provider=tradability_provider,
            leader_sector_rule_input_provider=sector_rule_provider,
            leader_research_input_provider=provider,
        )

        runtime.execute_sector("sector-run", leader_helpers.AS_OF)
        market_result = runtime.execute_market(
            "market-run",
            leader_helpers.AS_OF,
        )

        provider.assert_called_once()
        business_provider.assert_not_called()
        tradability_provider.assert_not_called()
        sector_rule_provider.assert_called_once()
        provider_context = provider.call_args.args[0]
        self.assertEqual(
            provider_context.quote_batch_id,
            provider_context.candidate_plan.quote_batch_id,
        )
        self.assertEqual(
            tuple(provider_context.quotes_by_symbol),
            tuple(
                item.symbol
                for item in provider_context.candidate_plan.items
            ),
        )
        self.assertEqual(self.market_quote_fetcher.call_count, 1)
        self.assertEqual(market_result.leader_stage6_status, "partial")
        self.assertIsInstance(market_result.leader_stage6_status, str)
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) "
                    "FROM radar_leader_candidate_snapshots"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) "
                    "FROM radar_leader_candidate_entries"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT SUM(formal_usable) "
                    "FROM radar_leader_candidate_entries"
                ).fetchone()[0],
                0,
            )
        leader_health = monitoring_health.get_task_states()[
            "radarLeaderStage6"
        ]
        self.assertEqual(leader_health["status"], "degraded")
        self.assertEqual(leader_health["itemCount"], 1)

    def test_stage6_disabled_never_calls_research_provider(self):
        self.seed_universes()
        provider = Mock(side_effect=AssertionError("stage6 is disabled"))
        runtime = self.runtime(
            settings=self.market_settings(),
            leader_research_input_provider=provider,
        )

        outcome = runtime.build_market_job()()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        provider.assert_not_called()
        self.assertNotIn(
            "radarLeaderStage6",
            monitoring_health.get_task_states(),
        )

    def test_market_job_uses_independent_cross_process_lock(self):
        runtime = self.runtime(settings=self.market_settings())
        runtime.execute_market = Mock(return_value=Mock(
            status="degraded",
            gate_passed=True,
            item_count=1,
        ))
        main_lock = CrossProcessFileLock(self.lock_path)
        self.assertTrue(main_lock.acquire(blocking=False))
        try:
            outcome = runtime.build_market_job()()
        finally:
            main_lock.release()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        runtime.execute_market.assert_called_once()

        market_lock = CrossProcessFileLock(self.market_lock_path)
        self.assertTrue(market_lock.acquire(blocking=False))
        try:
            locked = runtime.build_market_job()()
        finally:
            market_lock.release()
        self.assertEqual(locked.state, ScheduledRunState.LOCKED)
        self.assertEqual(runtime.execute_market.call_count, 1)

    def test_sector_gate_rejection_is_tracked_as_degraded_without_snapshot(self):
        self.seed_universes()
        runtime = self.runtime(settings=self.sector_settings())

        outcome = runtime.build_sector_job()()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(outcome.result_status, "degraded")
        self.assertFalse(outcome.gate_passed)
        self.quote_fetcher.assert_not_called()
        state = monitoring_health.get_task_states()["radarSectorFeatures"]
        self.assertEqual(state["status"], "degraded")
        self.assertEqual(
            state["lastDegradationReasons"],
            ["classification_release_missing"],
        )
        self.assertEqual(state["consecutiveDegradations"], 1)
        self.assertEqual(state["consecutiveFailures"], 0)
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sector_feature_snapshots"
                ).fetchone()[0],
                0,
            )

    def test_sector_job_uses_independent_cross_process_lock(self):
        runtime = self.runtime(settings=self.sector_settings())
        runtime.execute_sector = Mock(return_value=Mock(
            status="degraded",
            gate_passed=True,
            item_count=1,
        ))
        main_lock = CrossProcessFileLock(self.lock_path)
        self.assertTrue(main_lock.acquire(blocking=False))
        try:
            outcome = runtime.build_sector_job()()
        finally:
            main_lock.release()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        runtime.execute_sector.assert_called_once()

        sector_lock = CrossProcessFileLock(self.sector_lock_path)
        self.assertTrue(sector_lock.acquire(blocking=False))
        try:
            locked = runtime.build_sector_job()()
        finally:
            sector_lock.release()
        self.assertEqual(locked.state, ScheduledRunState.LOCKED)
        self.assertEqual(runtime.execute_sector.call_count, 1)

    def test_trading_stock_quote_uses_only_as_of_stock_universe(self):
        self.seed_universes()
        opened_connections = []

        def connection_factory(path, read_only):
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys = ON")
            opened_connections.append(connection)
            return connection

        outcome = self.runtime(
            connection_factory=connection_factory,
        ).build_job(RadarTaskScope.STOCK_QUOTES)()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(outcome.result_status, "succeeded")
        self.assertEqual(outcome.item_count, 1)
        self.quote_fetcher.assert_called_once()
        self.assertEqual(self.quote_fetcher.call_args.args[0], ("000001",))
        self.security_fetcher.assert_not_called()
        self.etf_fetcher.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT expected_stock_count, expected_etf_count "
                    "FROM radar_runs"
                ).fetchone(),
                (1, None),
            )
        self.assertEqual(len(opened_connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened_connections[0].execute("SELECT 1")

    def test_etf_job_does_not_include_stock_symbols(self):
        self.seed_universes()

        outcome = self.runtime().build_job(RadarTaskScope.ETF_QUOTES)()

        self.assertEqual(outcome.state, ScheduledRunState.COMPLETED)
        self.assertEqual(self.quote_fetcher.call_args.args[0], ("510300",))
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT expected_stock_count, expected_etf_count "
                    "FROM radar_runs"
                ).fetchone(),
                (None, 1),
            )

    def test_registry_runs_once_after_both_sources_are_healthy(self):
        runtime = self.runtime(
            now=REGISTRY_AS_OF,
            provider=market_provider("pre_open"),
        )

        first = runtime.build_job(RadarTaskScope.REGISTRY)()
        second = runtime.build_job(RadarTaskScope.REGISTRY)()

        self.assertEqual(first.state, ScheduledRunState.COMPLETED)
        self.assertEqual(second.state, ScheduledRunState.SKIPPED)
        self.assertEqual(second.skip_reason, "registry_already_current")
        self.security_fetcher.assert_called_once()
        self.etf_fetcher.assert_called_once()
        self.quote_fetcher.assert_not_called()

    def test_calendar_unknown_fails_closed_without_source_request(self):
        runtime = self.runtime(
            provider=market_provider("unknown", "unknown"),
        )

        outcome = runtime.build_job(RadarTaskScope.ETF_QUOTES)()

        self.assertEqual(outcome.state, ScheduledRunState.SKIPPED)
        self.assertEqual(outcome.skip_reason, "market_calendar_unknown")
        self.quote_fetcher.assert_not_called()

    def test_missing_lock_parent_fails_closed_before_database_write(self):
        self.seed_universes()
        runtime = RadarRuntime(
            database_path=self.database_path,
            lock_path=Path(self.temp_dir.name) / "missing-runtime" / "radar.lock",
            settings=self.settings,
            sources=self.sources,
            clock=lambda: TRADE_AS_OF,
            market_status_provider=market_provider(),
        )

        with self.assertRaises(FileNotFoundError):
            runtime.build_job(RadarTaskScope.STOCK_QUOTES)()

        self.quote_fetcher.assert_not_called()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM radar_runs").fetchone()[0],
                0,
            )

    def test_execution_failure_closes_connection_and_records_failure(self):
        self.seed_universes()
        opened_connections = []

        def connection_factory(path, read_only):
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys = ON")
            opened_connections.append(connection)
            return connection

        valid_batch = self.quote_batch(
            ("000001",),
            "wrong-run",
            "wrong-batch",
            TRADE_AS_OF,
        )
        self.quote_fetcher.side_effect = None
        self.quote_fetcher.return_value = valid_batch
        job = self.runtime(
            connection_factory=connection_factory,
        ).build_job(RadarTaskScope.STOCK_QUOTES)

        with self.assertLogs("radar.scheduler", level="ERROR"):
            with self.assertRaises(ShadowRunExecutionError):
                job()

        self.assertEqual(
            monitoring_health.get_task_states()["radarStockQuotes"]["status"],
            "failed",
        )
        self.assertEqual(len(opened_connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened_connections[0].execute("SELECT 1")

    def test_default_settings_register_nothing_and_touch_no_database(self):
        scheduler = Mock()

        registrations = register_production_shadow_jobs(
            scheduler,
            database_path=Path(self.temp_dir.name) / "missing.db",
            settings=RadarSettings(),
        )

        self.assertEqual(
            [item.state for item in registrations],
            [ScheduleRegistrationState.DISABLED] * 5,
        )
        self.assertEqual(registrations[-2].job_id, RADAR_SECTOR_FEATURES_JOB_ID)
        self.assertEqual(registrations[-1].job_id, RADAR_MARKET_FEATURES_JOB_ID)
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()
        scheduler.start.assert_not_called()

    def test_enabled_registration_keeps_sector_job_independently_disabled(self):
        scheduler = Mock()
        scheduler.get_job.return_value = None

        registrations = register_production_shadow_jobs(
            scheduler,
            database_path=self.database_path,
            lock_path=self.lock_path,
            settings=self.settings,
            sources=self.sources,
            clock=lambda: TRADE_AS_OF,
            market_status_provider=market_provider(),
        )

        self.assertEqual(
            [item.job_id for item in registrations],
            [
                RADAR_REGISTRY_JOB_ID,
                RADAR_STOCK_QUOTES_JOB_ID,
                RADAR_ETF_QUOTES_JOB_ID,
                RADAR_SECTOR_FEATURES_JOB_ID,
                RADAR_MARKET_FEATURES_JOB_ID,
            ],
        )
        self.assertEqual(
            [item.state for item in registrations],
            [ScheduleRegistrationState.REGISTERED] * 3
            + [ScheduleRegistrationState.DISABLED] * 2,
        )
        calls = scheduler.add_job.call_args_list
        self.assertEqual([call.kwargs["seconds"] for call in calls], [1800, 180, 300])
        self.assertTrue(all(call.kwargs["max_instances"] == 1 for call in calls))
        self.assertEqual(
            [call.kwargs["next_run_time"] for call in calls],
            [
                TRADE_AS_OF + timedelta(seconds=90),
                TRADE_AS_OF,
                TRADE_AS_OF + timedelta(seconds=30),
            ],
        )
        scheduler.start.assert_not_called()

    def test_stage5_registration_adds_only_one_daily_product_master_job(self):
        stage5_database_path = (
            Path(self.temp_dir.name) / "radar-stage5-runtime.db"
        )
        with sqlite3.connect(stage5_database_path) as connection:
            apply_pending_migrations(
                connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
            )
        scheduler = Mock()
        scheduler.get_job.return_value = None
        stage5_settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            etf_stage5_enabled=True,
        )
        product_fetcher = Mock()

        registrations = register_production_shadow_jobs(
            scheduler,
            database_path=stage5_database_path,
            lock_path=self.lock_path,
            etf_product_master_lock_path=(
                Path(self.temp_dir.name) / "product-master.lock"
            ),
            settings=stage5_settings,
            sources=self.sources,
            etf_product_master_fetcher=product_fetcher,
            clock=lambda: TRADE_AS_OF,
            market_status_provider=market_provider(),
        )

        self.assertEqual(
            [item.job_id for item in registrations],
            [
                RADAR_REGISTRY_JOB_ID,
                RADAR_STOCK_QUOTES_JOB_ID,
                RADAR_ETF_QUOTES_JOB_ID,
                RADAR_SECTOR_FEATURES_JOB_ID,
                RADAR_MARKET_FEATURES_JOB_ID,
                RADAR_ETF_PRODUCT_MASTER_JOB_ID,
            ],
        )
        self.assertEqual(
            [call.kwargs["seconds"] for call in scheduler.add_job.call_args_list],
            [1800, 180, 300, 86400],
        )
        self.assertEqual(
            [
                call.kwargs["next_run_time"]
                for call in scheduler.add_job.call_args_list
            ],
            [
                TRADE_AS_OF + timedelta(seconds=90),
                TRADE_AS_OF,
                TRADE_AS_OF + timedelta(seconds=30),
                TRADE_AS_OF + timedelta(seconds=150),
            ],
        )
        product_fetcher.assert_not_called()

    def test_stage5_product_master_readiness_is_once_per_shanghai_day(self):
        stage5_database_path = (
            Path(self.temp_dir.name) / "radar-stage5-readiness.db"
        )
        with sqlite3.connect(stage5_database_path) as connection:
            apply_pending_migrations(
                connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
            )
        stage5_settings = RadarSettings(
            enabled=True,
            shadow_mode=True,
            etf_stage5_enabled=True,
        )
        runtime = RadarRuntime(
            database_path=stage5_database_path,
            lock_path=self.lock_path,
            settings=stage5_settings,
            sources=self.sources,
            clock=lambda: TRADE_AS_OF,
            market_status_provider=market_provider(),
        )
        self.assertIsNone(
            runtime.etf_product_master_readiness_reason(TRADE_AS_OF)
        )
        with sqlite3.connect(stage5_database_path) as connection:
            repository = EtfRepository(
                connection,
                clock=lambda: TRADE_AS_OF,
            )
            repository.sync_product_master_batch(
                product_master_run_id="product-run",
                as_of=TRADE_AS_OF,
                source="official_exchange_listed_fund_product_master",
                source_time=None,
                fetched_at=TRADE_AS_OF,
                status="failed",
                expected_count=None,
                returned_count=0,
                row_coverage=None,
                required_field_coverage={},
                issues=[{"code": "source_request_failed"}],
                transitions=(),
            )
        self.assertEqual(
            runtime.etf_product_master_readiness_reason(TRADE_AS_OF),
            "etf_product_master_already_attempted_today",
        )

    def test_explicit_sector_enable_registers_staggered_fourth_job(self):
        scheduler = Mock()
        scheduler.get_job.return_value = None

        registrations = register_production_shadow_jobs(
            scheduler,
            database_path=self.database_path,
            lock_path=self.lock_path,
            sector_lock_path=self.sector_lock_path,
            settings=self.sector_settings(),
            sources=self.sources,
            sector_quote_fetcher=self.quote_fetcher,
            clock=lambda: TRADE_AS_OF,
            market_status_provider=market_provider(),
        )

        self.assertEqual(
            [item.job_id for item in registrations],
            [
                RADAR_REGISTRY_JOB_ID,
                RADAR_STOCK_QUOTES_JOB_ID,
                RADAR_ETF_QUOTES_JOB_ID,
                RADAR_SECTOR_FEATURES_JOB_ID,
                RADAR_MARKET_FEATURES_JOB_ID,
            ],
        )
        calls = scheduler.add_job.call_args_list
        self.assertEqual(
            [call.kwargs["seconds"] for call in calls],
            [1800, 180, 300, 180],
        )
        self.assertEqual(
            [call.kwargs["next_run_time"] for call in calls],
            [
                TRADE_AS_OF + timedelta(seconds=90),
                TRADE_AS_OF,
                TRADE_AS_OF + timedelta(seconds=30),
                TRADE_AS_OF + timedelta(seconds=60),
            ],
        )
        self.assertTrue(all(call.kwargs["max_instances"] == 1 for call in calls))
        scheduler.start.assert_not_called()

        specs = self.runtime(settings=self.sector_settings()).job_specs()
        occurrences = [
            {
                spec.next_run_time
                + timedelta(seconds=spec.interval_seconds * index)
                for index in range(120)
            }
            for spec in specs
        ]
        for left in range(len(occurrences)):
            for right in range(left + 1, len(occurrences)):
                self.assertFalse(occurrences[left] & occurrences[right])

    def test_sector_and_market_enable_register_staggered_five_jobs(self):
        scheduler = Mock()
        scheduler.get_job.return_value = None

        registrations = register_production_shadow_jobs(
            scheduler,
            database_path=self.database_path,
            lock_path=self.lock_path,
            sector_lock_path=self.sector_lock_path,
            market_lock_path=self.market_lock_path,
            settings=self.all_feature_settings(),
            sources=self.sources,
            sector_quote_fetcher=self.quote_fetcher,
            market_index_fetcher=self.market_index_fetcher,
            market_quote_fetcher=self.market_quote_fetcher,
            clock=lambda: TRADE_AS_OF,
            market_status_provider=market_provider(),
        )

        self.assertEqual(
            [item.job_id for item in registrations],
            [
                RADAR_REGISTRY_JOB_ID,
                RADAR_STOCK_QUOTES_JOB_ID,
                RADAR_ETF_QUOTES_JOB_ID,
                RADAR_SECTOR_FEATURES_JOB_ID,
                RADAR_MARKET_FEATURES_JOB_ID,
            ],
        )
        calls = scheduler.add_job.call_args_list
        self.assertEqual(
            [call.kwargs["seconds"] for call in calls],
            [1800, 180, 300, 180, 180],
        )
        self.assertEqual(
            [call.kwargs["next_run_time"] for call in calls],
            [
                TRADE_AS_OF + timedelta(seconds=90),
                TRADE_AS_OF,
                TRADE_AS_OF + timedelta(seconds=30),
                TRADE_AS_OF + timedelta(seconds=60),
                TRADE_AS_OF + timedelta(seconds=120),
            ],
        )
        self.assertTrue(all(call.kwargs["max_instances"] == 1 for call in calls))
        scheduler.start.assert_not_called()

        specs = self.runtime(settings=self.all_feature_settings()).job_specs()
        occurrences = [
            {
                spec.next_run_time
                + timedelta(seconds=spec.interval_seconds * index)
                for index in range(120)
            }
            for spec in specs
        ]
        for left in range(len(occurrences)):
            for right in range(left + 1, len(occurrences)):
                self.assertFalse(occurrences[left] & occurrences[right])

    def test_job_phases_never_have_nominal_lock_collision(self):
        specs = self.runtime().job_specs()
        occurrences = []
        for spec in specs:
            occurrences.append({
                spec.next_run_time + timedelta(seconds=spec.interval_seconds * index)
                for index in range(120)
            })

        self.assertFalse(occurrences[0] & occurrences[1])
        self.assertFalse(occurrences[0] & occurrences[2])
        self.assertFalse(occurrences[1] & occurrences[2])

    def test_skipped_health_keeps_last_confirmed_success(self):
        monitoring_health.record_task_success("radarStockQuotes", item_count=1)

        monitoring_health.record_task_skipped("radarStockQuotes", "market_closed")

        state = monitoring_health.get_task_states()["radarStockQuotes"]
        self.assertEqual(state["status"], "healthy")
        self.assertEqual(state["lastOutcome"], "skipped")
        self.assertEqual(state["lastSkipReason"], "market_closed")


class RadarFormalRuntimeSeamTests(unittest.TestCase):
    def test_formal_request_settings_default_false_and_parse_strict_booleans(self):
        from radar.config import load_radar_settings

        defaults = load_radar_settings({})
        self.assertFalse(defaults.formal_trend_requested)
        self.assertFalse(defaults.formal_etf_requested)
        self.assertFalse(defaults.formal_leader_requested)

        enabled = load_radar_settings({
            "RADAR_FORMAL_TREND_REQUESTED": "true",
            "RADAR_FORMAL_ETF_REQUESTED": "1",
            "RADAR_FORMAL_LEADER_REQUESTED": "yes",
        })
        self.assertTrue(enabled.formal_trend_requested)
        self.assertTrue(enabled.formal_etf_requested)
        self.assertTrue(enabled.formal_leader_requested)

        disabled = load_radar_settings({
            "RADAR_FORMAL_TREND_REQUESTED": "false",
            "RADAR_FORMAL_ETF_REQUESTED": "0",
            "RADAR_FORMAL_LEADER_REQUESTED": "no",
        })
        self.assertFalse(disabled.formal_trend_requested)
        self.assertFalse(disabled.formal_etf_requested)
        self.assertFalse(disabled.formal_leader_requested)

        for name in (
            "RADAR_FORMAL_TREND_REQUESTED",
            "RADAR_FORMAL_ETF_REQUESTED",
            "RADAR_FORMAL_LEADER_REQUESTED",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, name):
                    load_radar_settings({name: "enabled"})

    def test_runtime_formal_seam_registers_no_default_jobs_or_executors(self):
        from radar.formal_readiness_store import FormalReadinessLoadResult
        from radar.runtime import register_production_formal_jobs
        from radar.scheduler import FormalJobSpec

        self.assertIn(
            "clock",
            inspect.signature(register_production_formal_jobs).parameters,
        )

        scheduler = Mock()
        executor = Mock()
        settings_provider = Mock()
        readiness_loader = Mock(return_value=FormalReadinessLoadResult(
            status="missing",
            reason_codes=("formal_readiness_report_missing",),
        ))

        self.assertEqual(
            register_production_formal_jobs(
                scheduler,
                specs=(),
                settings_provider=settings_provider,
                readiness_loader=readiness_loader,
            ),
            (),
        )
        settings_provider.assert_not_called()
        readiness_loader.assert_not_called()
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            registrations = register_production_formal_jobs(
                scheduler,
                specs=(FormalJobSpec(
                    module="trendRotation",
                    job_id="test-explicit-formal-trend",
                    executor=executor,
                    config_sha256="c" * 64,
                    binding_loader=Mock(),
                    lock_path=Path(directory) / "formal.lock",
                    interval_seconds=180,
                ),),
                settings_provider=settings_provider,
                readiness_loader=readiness_loader,
            )

        self.assertEqual(
            [item.state for item in registrations],
            [ScheduleRegistrationState.DISABLED],
        )
        executor.assert_not_called()
        settings_provider.assert_not_called()
        readiness_loader.assert_not_called()
        scheduler.get_job.assert_not_called()
        scheduler.add_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
