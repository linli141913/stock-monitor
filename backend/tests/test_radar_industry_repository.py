import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.contracts import (
    IndustryClassificationCompleteness,
    IndustryClassificationRecord,
    IndustryClassificationRelease,
    IndustryClassificationSnapshot,
    RadarBatchMeta,
    SectorFeatureBatch,
    SourceStatus,
)
from radar.migrations import RADAR_MIGRATIONS, apply_pending_migrations
from radar.repository import (
    HistoryAsOfError,
    RadarRepository,
    RepositoryConflictError,
    RepositoryStateError,
    RepositoryWriteError,
)


UTC = timezone.utc
UTC_PLUS_8 = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 7, 21, 9, 30, tzinfo=UTC_PLUS_8)
AS_OF_2 = datetime(2026, 7, 22, 9, 30, tzinfo=UTC_PLUS_8)
FIRST_OBSERVED = datetime(2026, 7, 21, 8, 0, tzinfo=UTC_PLUS_8)
FETCHED_AT = datetime(2026, 7, 21, 9, 31, tzinfo=UTC_PLUS_8)
WRITTEN_AT = datetime(2026, 7, 21, 1, 32, tzinfo=UTC)


class RadarIndustryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        apply_pending_migrations(self.connection)
        self.repository = RadarRepository(
            self.connection,
            clock=lambda: WRITTEN_AT,
        )

    def tearDown(self):
        self.connection.close()

    def release(
        self,
        *,
        record_count=1,
        knowledge_effective_from=FIRST_OBSERVED,
        document_sha256="a" * 64,
    ):
        return IndustryClassificationRelease(
            schemeVersion="capco-guideline-2023",
            releasePeriod="2025H2",
            sourcePageTitle="2025年下半年行业分类结果",
            publicationPageUrl="https://www.capco.org.cn/release.html",
            documentUrl="https://www.capco.org.cn/result.pdf",
            documentSha256=document_sha256,
            publishedDate="2026-04-03",
            firstObservedAt=knowledge_effective_from,
            fetchedAt=knowledge_effective_from + timedelta(seconds=2),
            knowledgeEffectiveFrom=knowledge_effective_from,
            classificationStartDate="2025-12-20",
            historyStatus="retrospective_unverified",
            sourceRecordCount=record_count,
            uniqueSourceSymbolCount=record_count,
            requiredFieldCoverage={"sourceSymbol": 1.0},
        )

    def record(
        self,
        *,
        source_symbol="000001",
        security_identity="000001",
        source_name="平安银行",
    ):
        return IndustryClassificationRecord(
            releasePeriod="2025H2",
            sourceSymbol=source_symbol,
            sourceName=source_name,
            securityIdentity=security_identity,
            identityStatus="exact",
            categoryCode="C",
            categoryName="制造业",
            divisionCode="39",
            divisionName="计算机、通信和其他电子设备制造业",
            manufacturingSubclassCode="CE",
            manufacturingSubclassName="计算机、通信和其他电子设备制造业",
            recordStatus="accepted",
            issueCodes=(),
            sourceFields={"证券代码": source_symbol, "证券简称": source_name},
        )

    def classification_snapshot(self, *, release=None, records=None):
        records = list(records or [self.record()])
        release = release or self.release(record_count=len(records))
        mapped_count = len(records)
        snapshot_as_of = max(AS_OF, release.knowledge_effective_from)
        return IndustryClassificationSnapshot(
            meta=RadarBatchMeta(
                radarRunId="classification-run",
                batchId="classification-batch",
                source="capco_industry_classification",
                asOf=snapshot_as_of,
                fetchedAt=max(FETCHED_AT, release.fetched_at),
                expectedCount=len(records),
                returnedCount=len(records),
                rowCoverage=1.0 if records else 0.0,
                requiredFieldCoverage={"sourceSymbol": 1.0},
                issues=[],
            ),
            status=SourceStatus.DEGRADED,
            release=release,
            records=records,
            currentMasterGaps=[],
            completeness=IndustryClassificationCompleteness(
                sourceRecordCount=len(records),
                uniqueSourceSymbolCount=len(records),
                currentMasterCount=mapped_count,
                mappedCount=mapped_count,
                unconfirmedCount=0,
                excludedSourceCount=0,
                mappingCoverage=1.0 if mapped_count else None,
                requiredFieldCoverage={"sourceSymbol": 1.0},
                shadowUsable=True,
                formalUsable=False,
                reasons=("history_is_retrospective",),
            ),
            issues=[],
        )

    def sector_batch(self, *, as_of=AS_OF, division_name=None):
        return SectorFeatureBatch.model_validate({
            "radarRunId": "sector-run",
            "classificationBatchId": "classification-batch",
            "quoteBatchId": "quote-batch",
            "releasePeriod": "2025H2",
            "classificationDocumentSha256": "a" * 64,
            "asOf": as_of,
            "sourceTime": as_of - timedelta(seconds=1),
            "fetchedAt": as_of + timedelta(seconds=2),
            "classificationMappingCoverage": 1.0,
            "mappedConstituentCount": 1,
            "unconfirmedStockCount": 0,
            "sectors": [{
                "releasePeriod": "2025H2",
                "categoryCode": "C",
                "categoryName": "制造业",
                "divisionCode": "39",
                "divisionName": division_name
                or "计算机、通信和其他电子设备制造业",
                "constituentSymbols": ["000001"],
                "completeness": {
                    "expectedCount": 1,
                    "returnedCount": 1,
                    "freshCount": 1,
                    "validReturnCount": 1,
                    "validMarketCapCount": 0,
                    "validTurnoverCount": 1,
                    "rowCoverage": 1.0,
                    "requiredFieldCoverage": {
                        "changePercent": 1.0,
                        "totalMarketCapSource": 0.0,
                        "turnoverAmountSource": 1.0,
                    },
                    "isComplete": False,
                    "reasons": ["market_cap_unit_unverified"],
                },
                "returns": {
                    "equalReturn": {
                        "rawValue": 0.0,
                        "available": True,
                        "formalUsable": False,
                    },
                    "capWeightedReturn": {
                        "rawValue": None,
                        "available": False,
                        "formalUsable": False,
                        "reasons": ["market_cap_unavailable"],
                    },
                    "exTopReturn": {
                        "rawValue": None,
                        "available": False,
                        "formalUsable": False,
                        "reasons": ["market_cap_unavailable"],
                    },
                    "marketCapUnitStatus": "unverified",
                    "formalUsable": False,
                    "reasons": ["market_cap_unit_unverified"],
                },
                "breadth": {
                    "advancers": 0,
                    "decliners": 1,
                    "flat": 0,
                    "unavailable": 0,
                    "upRatio": {
                        "rawValue": 0.0,
                        "available": True,
                        "formalUsable": False,
                    },
                    "formalUsable": False,
                },
                "turnover": {
                    "rawValue": 0.0,
                    "contributingCount": 1,
                    "unitStatus": "unverified",
                    "available": True,
                    "formalUsable": False,
                    "reasons": ["turnover_unit_unverified"],
                },
                "shadowUsable": True,
                "formalUsable": False,
                "reasons": ["unit_unverified"],
            }],
            "excludedEtfCount": 0,
            "status": "degraded",
            "shadowUsable": True,
            "formalUsable": False,
            "reasons": ["unit_unverified"],
        })

    def prepare_sector_dependencies(self, *, release=None, run_as_of=AS_OF):
        self.repository.record_industry_classification(
            self.classification_snapshot(release=release)
        )
        self.repository.start_run(
            "sector-run",
            run_as_of,
            started_at=run_as_of,
            shadow_mode=True,
        )

    def test_industry_snapshot_write_and_queries_are_idempotent(self):
        snapshot = self.classification_snapshot()

        self.assertEqual(
            self.repository.record_industry_classification(snapshot).as_tuple(),
            (2, 0, 0),
        )
        self.assertEqual(
            self.repository.record_industry_classification(snapshot).as_tuple(),
            (0, 2, 0),
        )

        stored_release = self.repository.get_industry_classification_release(
            "capco_listed_company_industry",
            "2025H2",
        )
        self.assertEqual(stored_release, snapshot.release)
        self.assertEqual(
            self.repository.list_industry_classification_records(
                "capco_listed_company_industry",
                "2025H2",
            ),
            tuple(snapshot.records),
        )

    def test_industry_release_conflict_rolls_back_without_overwrite(self):
        snapshot = self.classification_snapshot()
        self.repository.record_industry_classification(snapshot)
        changed_release = snapshot.release.model_copy(update={
            "document_url": "https://www.capco.org.cn/changed.pdf",
        })

        with self.assertRaises(RepositoryConflictError):
            self.repository.record_industry_classification(
                snapshot.model_copy(update={"release": changed_release})
            )

        stored = self.repository.get_industry_classification_release(
            snapshot.release.classification_system,
            snapshot.release.release_period,
        )
        self.assertEqual(stored.document_url, snapshot.release.document_url)

    def test_industry_batch_sqlite_failure_rolls_back_release_and_records(self):
        first = self.record()
        duplicate_identity = self.record(
            source_symbol="000002",
            security_identity="000001",
            source_name="重复身份",
        )
        snapshot = self.classification_snapshot(
            release=self.release(record_count=2),
            records=[first, duplicate_identity],
        )

        with self.assertRaises(RepositoryWriteError):
            self.repository.record_industry_classification(snapshot)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM industry_classification_releases"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM industry_classification_records"
            ).fetchone()[0],
            0,
        )

    def test_industry_methods_reject_database_without_migration_version_two(self):
        connection = sqlite3.connect(":memory:")
        try:
            apply_pending_migrations(
                connection,
                migrations=(RADAR_MIGRATIONS[0],),
            )
            repository = RadarRepository(connection, clock=lambda: WRITTEN_AT)
            with self.assertRaises(RepositoryStateError):
                repository.record_industry_classification(
                    self.classification_snapshot()
                )
            with self.assertRaises(RepositoryStateError):
                repository.get_industry_classification_release(
                    "capco_listed_company_industry",
                    "2025H2",
                )
        finally:
            connection.close()

    def test_sector_write_is_idempotent_and_preserves_real_zero_and_null(self):
        self.prepare_sector_dependencies()
        batch = self.sector_batch()

        self.assertEqual(
            self.repository.record_sector_feature_batch(batch).as_tuple(),
            (1, 0, 0),
        )
        self.assertEqual(
            self.repository.record_sector_feature_batch(batch).as_tuple(),
            (0, 1, 0),
        )

        rows = self.repository.list_sector_feature_rows("sector-run")
        latest_rows = self.repository.list_latest_sector_feature_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(latest_rows, rows)
        self.assertEqual(rows[0]["equalReturn"], 0.0)
        self.assertIsNone(rows[0]["capWeightedReturn"])
        self.assertIsNone(rows[0]["exTopReturn"])
        self.assertEqual(rows[0]["upRatio"], 0.0)
        self.assertEqual(rows[0]["turnoverRawValue"], 0.0)
        self.assertNotIn(
            "constituentSymbols",
            rows[0]["evidenceSummary"],
        )
        self.assertIn(
            "constituentSymbolsSha256",
            rows[0]["evidenceSummary"],
        )

    def test_sector_conflict_does_not_overwrite_existing_row(self):
        self.prepare_sector_dependencies()
        self.repository.record_sector_feature_batch(self.sector_batch())

        with self.assertRaises(RepositoryConflictError):
            self.repository.record_sector_feature_batch(
                self.sector_batch(division_name="冲突名称")
            )

        rows = self.repository.list_sector_feature_rows("sector-run")
        self.assertEqual(
            rows[0]["divisionName"],
            "计算机、通信和其他电子设备制造业",
        )

    def test_sector_write_requires_existing_release_and_run(self):
        with self.assertRaises(RepositoryConflictError):
            self.repository.record_sector_feature_batch(self.sector_batch())

        self.repository.record_industry_classification(
            self.classification_snapshot()
        )
        with self.assertRaises(RepositoryConflictError):
            self.repository.record_sector_feature_batch(self.sector_batch())

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sector_feature_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_sector_write_rejects_future_classification_knowledge(self):
        future_release = self.release(knowledge_effective_from=AS_OF_2)
        self.prepare_sector_dependencies(
            release=future_release,
            run_as_of=AS_OF,
        )

        with self.assertRaises(HistoryAsOfError):
            self.repository.record_sector_feature_batch(self.sector_batch())

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sector_feature_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_industry_records_can_be_read_after_temporary_database_reopen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "radar-industry-repository.db"
            connection = sqlite3.connect(path)
            apply_pending_migrations(connection)
            repository = RadarRepository(connection, clock=lambda: WRITTEN_AT)
            snapshot = self.classification_snapshot()
            repository.record_industry_classification(snapshot)
            connection.close()

            reopened = sqlite3.connect(path)
            try:
                reopened_repository = RadarRepository(
                    reopened,
                    clock=lambda: WRITTEN_AT,
                )
                self.assertEqual(
                    reopened_repository.get_industry_classification_release(
                        "capco_listed_company_industry",
                        "2025H2",
                    ),
                    snapshot.release,
                )
                self.assertEqual(
                    reopened_repository.list_industry_classification_records(
                        "capco_listed_company_industry",
                        "2025H2",
                    ),
                    tuple(snapshot.records),
                )
                self.assertEqual(
                    reopened.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
