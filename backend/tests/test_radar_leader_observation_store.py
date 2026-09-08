import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.leader_observation_store import (
    LeaderObservationItem,
    LeaderObservationSnapshot,
    load_latest_leader_observation,
    publish_leader_observation_snapshot,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 31, 3, 27, tzinfo=UTC)


class LeaderObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def snapshot():
        return LeaderObservationSnapshot(
            radar_run_id="market-run-1",
            candidate_plan_id="radar-leader-runtime-candidate-plan-v1:" + "a" * 64,
            as_of=AS_OF,
            published_at=AS_OF + timedelta(seconds=2),
            scanned_count=5157,
            mapped_count=383,
            items=(LeaderObservationItem(
                symbol="000001",
                name="平安银行",
                industry_code="66",
                industry_name="货币金融服务",
                within_industry_rank=1,
                price=12.34,
                change_percent=3.21,
                source_time=AS_OF - timedelta(seconds=1),
                quote_source_contract_id="tencent-full-market-quote-v1:batch-1",
                sector_source_contract_id="radar-sector-aggregate-v1:sector-run:66",
            ),),
        )

    def test_publish_and_load_preserves_real_observation_without_approval(self):
        published = publish_leader_observation_snapshot(
            self.snapshot(),
            store_dir=self.store_dir,
        )
        loaded = load_latest_leader_observation(store_dir=self.store_dir)

        self.assertEqual(published.status, "available")
        self.assertEqual(loaded.status, "available")
        self.assertEqual(loaded.snapshot.items[0].symbol, "000001")
        self.assertEqual(loaded.snapshot.items[0].price, 12.34)
        self.assertFalse(loaded.snapshot.human_approval_required)
        self.assertFalse(loaded.snapshot.formal_usable)
        self.assertFalse(loaded.snapshot.state_transition_allowed)
        self.assertEqual(loaded.snapshot.coverage_scope, "当轮健康沪深A股行情与已确认行业映射；每行业涨幅前5只")

    def test_missing_manifest_is_not_ready(self):
        loaded = load_latest_leader_observation(store_dir=self.store_dir)

        self.assertEqual(loaded.status, "not_ready")
        self.assertEqual(
            loaded.reasons,
            ("leader_observation_snapshot_missing",),
        )

    def test_tampered_evidence_fails_closed(self):
        publish_leader_observation_snapshot(
            self.snapshot(),
            store_dir=self.store_dir,
        )
        manifest = json.loads(
            (self.store_dir / "latest.json").read_text(encoding="utf-8")
        )
        evidence_path = self.store_dir / manifest["evidenceRelativePath"]
        evidence_path.write_text("{}", encoding="utf-8")

        loaded = load_latest_leader_observation(store_dir=self.store_dir)

        self.assertEqual(loaded.status, "failed")
        self.assertEqual(
            loaded.reasons,
            ("leader_observation_evidence_hash_mismatch",),
        )

    def test_snapshot_rejects_invalid_symbol(self):
        with self.assertRaisesRegex(ValueError, "symbol"):
            LeaderObservationItem(
                symbol="123",
                name="无效证券",
                industry_code="66",
                industry_name="货币金融服务",
                within_industry_rank=1,
                price=1.0,
                change_percent=0.0,
                source_time=AS_OF,
                quote_source_contract_id="quote-contract",
                sector_source_contract_id="sector-contract",
            )

    def test_snapshot_rejects_publication_before_market_as_of(self):
        with self.assertRaisesRegex(ValueError, "published_at"):
            LeaderObservationSnapshot(
                radar_run_id="market-run-1",
                candidate_plan_id="candidate-plan-1",
                as_of=AS_OF,
                published_at=AS_OF - timedelta(seconds=1),
                scanned_count=1,
                mapped_count=1,
                items=(self.snapshot().items[0],),
            )

    def test_snapshot_accepts_source_time_captured_during_the_run(self):
        item = LeaderObservationItem(
            symbol="000001",
            name="平安银行",
            industry_code="66",
            industry_name="货币金融服务",
            within_industry_rank=1,
            price=12.34,
            change_percent=3.21,
            source_time=AS_OF + timedelta(seconds=4),
            quote_source_contract_id="tencent-full-market-quote-v1:batch-1",
            sector_source_contract_id="radar-sector-aggregate-v1:sector-run:66",
        )

        snapshot = LeaderObservationSnapshot(
            radar_run_id="market-run-1",
            candidate_plan_id="radar-leader-runtime-candidate-plan-v1:" + "a" * 64,
            as_of=AS_OF,
            published_at=AS_OF + timedelta(seconds=10),
            scanned_count=5157,
            mapped_count=383,
            items=(item,),
        )

        self.assertEqual(snapshot.items[0].source_time, AS_OF + timedelta(seconds=4))


if __name__ == "__main__":
    unittest.main()
