import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone

from radar.contracts import (
    EtfRegistryRecord,
    IndexQuoteSnapshot,
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
)
from radar.migrations import apply_pending_migrations
from radar.repository import RadarRepository
from radar.market_shadow_runner import (
    MarketShadowRunAlreadyExistsError,
    MarketShadowRunExecutionError,
    MarketShadowRunInProgressError,
    MarketShadowRunner,
)
from radar.sources.market_indices import MARKET_INDEX_IDENTITIES


UTC = timezone.utc
MASTER_AS_OF = datetime(2026, 7, 22, 1, 30, tzinfo=UTC)
AS_OF = datetime(2026, 7, 23, 1, 45, tzinfo=UTC)
WRITTEN_AT = AS_OF + timedelta(seconds=10)


class RadarMarketShadowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(self.connection)
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: WRITTEN_AT,
        )
        self.index_calls = []
        self.quote_calls = []
        self.seed_universes()

    def tearDown(self):
        self.connection.close()

    def seed_universes(self):
        securities = [
            SecurityMasterRecord(
                symbol="000001",
                name="平安银行",
                exchange="szse",
                board="主板",
                listingDate="1991-04-03",
                sourceReportDate="2026-07-22",
                source="szse",
                fetchedAt=MASTER_AS_OF,
                sourceFields={"证券代码": "000001"},
            ),
            SecurityMasterRecord(
                symbol="000002",
                name="万科A",
                exchange="szse",
                board="主板",
                listingDate="1991-01-29",
                sourceReportDate="2026-07-22",
                source="szse",
                fetchedAt=MASTER_AS_OF,
                sourceFields={"证券代码": "000002"},
            ),
        ]
        self.repository.sync_security_master(SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="master-run",
                batchId="master-batch",
                source="official_exchange_security_master",
                asOf=MASTER_AS_OF,
                fetchedAt=MASTER_AS_OF,
                expectedCount=2,
                returnedCount=2,
                rowCoverage=1.0,
                requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
            ),
            items=securities,
        ))
        etf = EtfRegistryRecord(
            symbol="510300",
            name="沪深300ETF",
            exchange="sse",
            sourceType="股票ETF",
            sourceReportDate="2026-07-22",
            source="sse",
            fetchedAt=MASTER_AS_OF,
            sourceFields={"基金代码": "510300"},
        )
        self.repository.sync_etf_registry(SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="etf-run",
                batchId="etf-batch",
                source="official_exchange_etf_registry",
                asOf=MASTER_AS_OF,
                fetchedAt=MASTER_AS_OF,
                expectedCount=1,
                returnedCount=1,
                rowCoverage=1.0,
                requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
            ),
            items=[etf],
        ))

    def index_batch(
        self,
        radar_run_id,
        batch_id,
        as_of,
        *,
        source_time=None,
        wrong_run_id=False,
    ):
        self.index_calls.append((radar_run_id, batch_id, as_of))
        source_time = source_time or (as_of - timedelta(seconds=1))
        items = [
            IndexQuoteSnapshot(
                indexKey=identity.index_key,
                symbol=identity.symbol,
                name=identity.name,
                exchange=identity.exchange,
                sourceSymbol=identity.source_symbol,
                sourceTime=source_time,
                fetchedAt=as_of + timedelta(seconds=1),
                price=0.0,
                changePercent=0.0,
            )
            for identity in MARKET_INDEX_IDENTITIES
        ]
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=("wrong-run" if wrong_run_id else radar_run_id),
                batchId=batch_id,
                source="tencent_finance_indices",
                asOf=as_of,
                sourceTime=source_time,
                fetchedAt=as_of + timedelta(seconds=1),
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

    def quote_batch(
        self,
        symbols,
        radar_run_id,
        batch_id,
        as_of,
        *,
        source_time=None,
        missing_turnover=False,
    ):
        self.quote_calls.append((tuple(symbols), radar_run_id, batch_id, as_of))
        source_time = source_time or (as_of - timedelta(seconds=1))
        items = [
            QuoteSnapshot(
                symbol=symbol,
                name=symbol,
                sourceTime=source_time,
                fetchedAt=as_of + timedelta(seconds=1),
                price=0.0,
                changePercent=0.0,
                turnoverAmountSource=(
                    None if missing_turnover and index == 0 else 0.0
                ),
                turnoverRatePercent=0.0,
                volumeRatio=0.0,
                marketCapSource=0.0,
            )
            for index, symbol in enumerate(symbols)
        ]
        turnover_coverage = (
            sum(item.turnover_amount_source is not None for item in items)
            / len(items)
        )
        return SourceBatch(
            meta=RadarBatchMeta(
                radarRunId=radar_run_id,
                batchId=batch_id,
                source="tencent_finance",
                asOf=as_of,
                sourceTime=source_time,
                fetchedAt=as_of + timedelta(seconds=1),
                expectedCount=len(symbols),
                returnedCount=len(items),
                rowCoverage=1.0,
                requiredFieldCoverage={
                    "price": 1.0,
                    "source_time": 1.0,
                    "change_percent": 1.0,
                    "turnover_amount_source": turnover_coverage,
                },
            ),
            items=items,
        )

    def runner(self, *, index_fetcher=None, quote_fetcher=None, run_lock=None):
        return MarketShadowRunner(
            self.repository,
            index_fetcher=index_fetcher or self.index_batch,
            quote_fetcher=quote_fetcher or self.quote_batch,
            clock=lambda: WRITTEN_AT,
            run_lock=run_lock,
        )

    def test_success_persists_only_aggregate_and_preserves_real_zero(self):
        result = self.runner().run_once("market-run", AS_OF)

        self.assertTrue(result.gate_passed)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.stock_count, 2)
        self.assertEqual(result.etf_count, 1)
        self.assertEqual(result.persisted_environment_count, 1)
        self.assertEqual(result.persisted_index_count, 4)
        self.assertEqual(result.item_count, 1)
        self.assertEqual(
            self.quote_calls[0][0],
            ("000001", "000002", "510300"),
        )

        row = self.repository.get_market_feature_row("market-run")
        self.assertEqual(row["breadth"]["flat"], 2)
        self.assertEqual(row["turnover"]["rawValue"], 0.0)
        self.assertEqual(row["turnover"]["unitStatus"], "unverified")
        self.assertEqual(row["excludedEtfCount"], 1)
        self.assertEqual(len(row["indices"]), 4)
        self.assertTrue(all(index["price"] == 0.0 for index in row["indices"]))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_source_status "
                "WHERE radar_run_id='market-run'"
            ).fetchone()[0],
            2,
        )
        market_tables = {
            value[0]
            for value in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND (name LIKE '%market%' OR name LIKE '%quote%')"
            )
        }
        self.assertEqual(market_tables, {
            "market_environment_snapshots",
            "market_index_feature_snapshots",
        })

    def test_stale_or_incomplete_sources_do_not_write_market_snapshot(self):
        stale_indices = lambda run_id, batch_id, as_of: self.index_batch(
            run_id,
            batch_id,
            as_of,
            source_time=as_of - timedelta(seconds=91),
        )
        incomplete_quotes = lambda symbols, run_id, batch_id, as_of: (
            self.quote_batch(
                symbols,
                run_id,
                batch_id,
                as_of,
                missing_turnover=True,
            )
        )

        result = self.runner(
            index_fetcher=stale_indices,
            quote_fetcher=incomplete_quotes,
        ).run_once("rejected-run", AS_OF)

        self.assertFalse(result.gate_passed)
        self.assertIn("index_health:source_time_stale", result.gate_reasons)
        self.assertIn(
            "quote_health:required_field_coverage_below_threshold:turnover_amount_source",
            result.gate_reasons,
        )
        self.assertEqual(result.persisted_environment_count, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_environment_snapshots"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT status, error_code FROM radar_runs "
                "WHERE radar_run_id='rejected-run'"
            ).fetchone(),
            ("degraded", "market_feature_gate_rejected"),
        )

    def test_cross_source_skew_is_rejected(self):
        indices = lambda run_id, batch_id, as_of: self.index_batch(
            run_id,
            batch_id,
            as_of,
            source_time=as_of - timedelta(seconds=1),
        )
        quotes = lambda symbols, run_id, batch_id, as_of: self.quote_batch(
            symbols,
            run_id,
            batch_id,
            as_of,
            source_time=as_of - timedelta(seconds=30),
        )

        result = self.runner(
            index_fetcher=indices,
            quote_fetcher=quotes,
        ).run_once("skew-run", AS_OF)

        self.assertFalse(result.gate_passed)
        self.assertIn("source_time_skew_above_threshold", result.gate_reasons)
        self.assertIsNone(self.repository.get_market_feature_row("skew-run"))

    def test_source_exception_is_audited_without_market_snapshot(self):
        def failed_indices(*_args):
            raise TimeoutError("upstream timeout")

        result = self.runner(index_fetcher=failed_indices).run_once(
            "source-failed-run",
            AS_OF,
        )

        self.assertFalse(result.gate_passed)
        self.assertIn("index_health:source_returned_no_rows", result.gate_reasons)
        self.assertIsNone(self.repository.get_market_feature_row("source-failed-run"))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_source_status "
                "WHERE radar_run_id='source-failed-run'"
            ).fetchone()[0],
            2,
        )

    def test_missing_etf_pool_stops_before_external_sources(self):
        self.connection.execute("DELETE FROM etf_product_registry")
        self.connection.commit()

        result = self.runner().run_once("missing-etf-run", AS_OF)

        self.assertFalse(result.gate_passed)
        self.assertEqual(result.gate_reasons, ("etf_registry_empty",))
        self.assertEqual(self.index_calls, [])
        self.assertEqual(self.quote_calls, [])

    def test_batch_identity_mismatch_marks_run_failed(self):
        indices = lambda run_id, batch_id, as_of: self.index_batch(
            run_id,
            batch_id,
            as_of,
            wrong_run_id=True,
        )

        with self.assertRaises(MarketShadowRunExecutionError):
            self.runner(index_fetcher=indices).run_once("identity-run", AS_OF)

        self.assertEqual(
            self.connection.execute(
                "SELECT status, error_code FROM radar_runs "
                "WHERE radar_run_id='identity-run'"
            ).fetchone(),
            ("failed", "market_shadow_index_batch_identity"),
        )
        self.assertIsNone(self.repository.get_market_feature_row("identity-run"))

    def test_duplicate_run_id_is_rejected_without_duplicate_rows(self):
        runner = self.runner()
        runner.run_once("duplicate-run", AS_OF)

        with self.assertRaises(MarketShadowRunAlreadyExistsError):
            runner.run_once("duplicate-run", AS_OF)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM market_environment_snapshots "
                "WHERE radar_run_id='duplicate-run'"
            ).fetchone()[0],
            1,
        )

    def test_in_process_lock_and_historical_as_of_fail_before_write(self):
        run_lock = threading.Lock()
        run_lock.acquire()
        try:
            with self.assertRaises(MarketShadowRunInProgressError):
                self.runner(run_lock=run_lock).run_once("locked-run", AS_OF)
        finally:
            run_lock.release()

        with self.assertRaises(ValueError):
            self.runner().run_once(
                "historical-run",
                AS_OF - timedelta(minutes=10),
            )

        self.assertEqual(self.index_calls, [])
        self.assertEqual(self.quote_calls, [])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM radar_runs WHERE radar_run_id IN "
                "('locked-run', 'historical-run')"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
