import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from radar.contracts import (
    IndexQuoteSnapshot,
    MarketIndexKey,
    QuoteTradingStatus,
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
)
from radar.replay_contracts import RadarReplayOutputBundle
from radar.replay_label_tasks import RadarReplayLabelTaskBundle


UTC = timezone.utc
CAPTURED_AT = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


class RadarReplayOutcomeDailyCaptureTests(unittest.TestCase):
    def inputs(self, root: Path):
        from tests.test_radar_replay_objective_outcomes import (
            RadarReplayObjectiveOutcomeTests,
        )

        helper = RadarReplayObjectiveOutcomeTests()
        source_payload = {
            "radarRunId": "radar-1",
            "sampleAsOf": "2026-09-01T14:30:00+08:00",
            "sources": {
                "industry": {
                    "records": [
                        {
                            "recordStatus": "accepted",
                            "identityStatus": "exact",
                            "divisionCode": "C39",
                            "securityIdentity": "000001",
                        },
                        {
                            "recordStatus": "accepted",
                            "identityStatus": "verified_alias",
                            "divisionCode": "C39",
                            "securityIdentity": "000002",
                        },
                        {
                            "recordStatus": "accepted",
                            "identityStatus": "exact",
                            "divisionCode": "C40",
                            "securityIdentity": "000003",
                        },
                    ],
                },
            },
        }
        source_path = root / "source-snapshots.json"
        source_path.write_text(
            json.dumps(source_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        task_payload = helper.task_bundle().model_dump(
            mode="python", by_alias=True
        )
        task_payload["samples"][0]["sourceSnapshots"] = {
            "path": str(source_path),
            "sha256": source_sha,
        }
        return (
            RadarReplayLabelTaskBundle.model_validate(task_payload),
            helper.output_bundle(),
        )

    @staticmethod
    def quote_batch(
        symbols,
        *,
        missing=(),
        fetched_at=CAPTURED_AT,
        price=10.0,
        source_time=datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
    ):
        returned = [symbol for symbol in symbols if symbol not in set(missing)]
        items = [QuoteSnapshot(
            symbol=symbol,
            name=f"证券{symbol}",
            sourceTime=source_time,
            fetchedAt=fetched_at,
            price=price,
            previousClose=9.8,
            highPrice=10.2,
            lowPrice=9.7,
            changePercent=2.04,
        ) for symbol in returned]
        return SourceBatch[QuoteSnapshot](
            meta=RadarBatchMeta(
                radarRunId="outcome-daily-20260901",
                batchId="quotes-1",
                source="tencent_finance",
                asOf=CAPTURED_AT,
                sourceTime=datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
                fetchedAt=fetched_at,
                expectedCount=len(symbols),
                returnedCount=len(items),
                rowCoverage=len(items) / len(symbols),
            ),
            items=items,
        )

    @staticmethod
    def index_batch(
        *,
        fetched_at=CAPTURED_AT,
        source_time=datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
    ):
        identities = (
            (MarketIndexKey.SSE_COMPOSITE, "000001", "sse", "sh000001"),
            (MarketIndexKey.SZSE_COMPONENT, "399001", "szse", "sz399001"),
            (MarketIndexKey.CHINEXT, "399006", "szse", "sz399006"),
            (MarketIndexKey.STAR50, "000688", "sse", "sh000688"),
        )
        items = [IndexQuoteSnapshot(
            indexKey=index_key,
            symbol=symbol,
            name=source_symbol,
            exchange=exchange,
            sourceSymbol=source_symbol,
            sourceTime=source_time,
            fetchedAt=fetched_at,
            price=3000.0,
            changePercent=1.0,
        ) for index_key, symbol, exchange, source_symbol in identities]
        return SourceBatch[IndexQuoteSnapshot](
            meta=RadarBatchMeta(
                radarRunId="outcome-daily-20260901",
                batchId="indices-1",
                source="tencent_finance_indices",
                asOf=CAPTURED_AT,
                sourceTime=datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
                fetchedAt=fetched_at,
                expectedCount=4,
                returnedCount=4,
                rowCoverage=1.0,
            ),
            items=items,
        )

    def test_after_close_capture_freezes_required_symbols_and_memberships(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)
            quote_calls = []

            def quotes(symbols, **_kwargs):
                quote_calls.append(tuple(symbols))
                return self.quote_batch(symbols)

            result = capture_replay_outcome_day(
                task_bundle=tasks,
                output_bundle=outputs,
                output_dir=root / "daily",
                trade_date=date(2026, 9, 1),
                captured_at=CAPTURED_AT,
                day_kind_provider=lambda _day: "full",
                sources=DailyOutcomeCaptureSources(
                    security_quotes=quotes,
                    market_indices=lambda **_kwargs: self.index_batch(),
                ),
            )

            self.assertTrue(result.snapshot_path.is_file())
            self.assertTrue(result.manifest_path.is_file())

        self.assertEqual(
            quote_calls,
            [("000001", "000002", "000003", "510300")],
        )
        self.assertEqual(result.snapshot.status, "ready")
        self.assertEqual(result.snapshot.expected_security_count, 4)
        self.assertEqual(result.snapshot.ready_security_count, 4)
        self.assertEqual(len(result.snapshot.market_indices), 4)
        self.assertEqual(
            result.snapshot.sector_memberships_by_sample[
                "sample-1"
            ]["C39"],
            ["000001", "000002"],
        )
        self.assertTrue(all(
            ":sha256:" in source_id
            for source_id in result.snapshot.source_ids
        ))

    def test_partial_quote_coverage_is_explicit(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)
            result = capture_replay_outcome_day(
                task_bundle=tasks,
                output_bundle=outputs,
                output_dir=root / "daily",
                trade_date=date(2026, 9, 1),
                captured_at=CAPTURED_AT,
                day_kind_provider=lambda _day: "full",
                sources=DailyOutcomeCaptureSources(
                    security_quotes=lambda symbols, **_kwargs: (
                        self.quote_batch(symbols, missing=("000002",))
                    ),
                    market_indices=lambda **_kwargs: self.index_batch(),
                ),
            )

        self.assertEqual(result.snapshot.status, "partial")
        self.assertEqual(result.snapshot.ready_security_count, 3)
        self.assertEqual(result.snapshot.missing_symbols, ["000002"])
        self.assertIn("daily_security_quote_incomplete", result.snapshot.reasons)

    def test_intraday_security_quote_cannot_be_frozen_as_close_fact(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)
            result = capture_replay_outcome_day(
                task_bundle=tasks,
                output_bundle=outputs,
                output_dir=root / "daily",
                trade_date=date(2026, 9, 1),
                captured_at=CAPTURED_AT,
                day_kind_provider=lambda _day: "full",
                sources=DailyOutcomeCaptureSources(
                    security_quotes=lambda symbols, **_kwargs: (
                        self.quote_batch(
                            symbols,
                            source_time=datetime(
                                2026, 9, 1, 6, 30, tzinfo=UTC
                            ),
                        )
                    ),
                    market_indices=lambda **_kwargs: self.index_batch(),
                ),
            )

        self.assertEqual(result.snapshot.status, "failed")
        self.assertEqual(result.snapshot.ready_security_count, 0)
        self.assertEqual(
            len(result.snapshot.missing_symbols),
            result.snapshot.expected_security_count,
        )
        self.assertIn("daily_security_quote_incomplete", result.snapshot.reasons)

    def test_intraday_indices_cannot_be_frozen_as_close_facts(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)
            result = capture_replay_outcome_day(
                task_bundle=tasks,
                output_bundle=outputs,
                output_dir=root / "daily",
                trade_date=date(2026, 9, 1),
                captured_at=CAPTURED_AT,
                day_kind_provider=lambda _day: "full",
                sources=DailyOutcomeCaptureSources(
                    security_quotes=lambda symbols, **_kwargs: (
                        self.quote_batch(symbols)
                    ),
                    market_indices=lambda **_kwargs: self.index_batch(
                        source_time=datetime(
                            2026, 9, 1, 6, 30, tzinfo=UTC
                        ),
                    ),
                ),
            )

        self.assertEqual(result.snapshot.status, "failed")
        self.assertEqual(result.snapshot.market_indices, [])
        self.assertIn("daily_market_index_incomplete", result.snapshot.reasons)

    def test_snapshot_time_is_final_source_completion_not_request_start(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        quote_completed = CAPTURED_AT + timedelta(seconds=2)
        index_completed = CAPTURED_AT + timedelta(seconds=4)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)
            result = capture_replay_outcome_day(
                task_bundle=tasks,
                output_bundle=outputs,
                output_dir=root / "daily",
                trade_date=date(2026, 9, 1),
                captured_at=CAPTURED_AT,
                day_kind_provider=lambda _day: "full",
                sources=DailyOutcomeCaptureSources(
                    security_quotes=lambda symbols, **_kwargs: (
                        self.quote_batch(symbols, fetched_at=quote_completed)
                    ),
                    market_indices=lambda **_kwargs: self.index_batch(
                        fetched_at=index_completed
                    ),
                ),
            )

        self.assertEqual(result.snapshot.status, "ready")
        self.assertEqual(result.snapshot.captured_at, index_completed)

    def test_explicit_non_trading_quote_preserves_real_zero_high_low(self):
        from radar.replay_outcome_daily_capture import DailySecurityOutcomePoint

        point = DailySecurityOutcomePoint(
            symbol="000001",
            name="停牌证券",
            sourceTime=datetime(2026, 8, 31, 7, 0, tzinfo=UTC),
            fetchedAt=CAPTURED_AT,
            price=10.0,
            previousClose=10.0,
            highPrice=0.0,
            lowPrice=0.0,
            tradingStatus=QuoteTradingStatus.SUSPENDED.value,
        )

        self.assertEqual(point.high_price, 0.0)
        self.assertEqual(point.low_price, 0.0)

    def test_arbitrary_trading_status_cannot_bypass_close_time_gate(self):
        from radar.replay_outcome_daily_capture import DailySecurityOutcomePoint

        for status in ("active", "unknown", " "):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    DailySecurityOutcomePoint(
                        symbol="000001",
                        name="平安银行",
                        sourceTime=datetime(
                            2026, 9, 1, 6, 30, tzinfo=UTC
                        ),
                        fetchedAt=CAPTURED_AT,
                        price=10.0,
                        previousClose=10.0,
                        tradingStatus=status,
                    )

    def test_snapshot_identity_changes_when_source_content_changes(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)

            def capture(output_name, price):
                return capture_replay_outcome_day(
                    task_bundle=tasks,
                    output_bundle=outputs,
                    output_dir=root / output_name,
                    trade_date=date(2026, 9, 1),
                    captured_at=CAPTURED_AT,
                    day_kind_provider=lambda _day: "full",
                    sources=DailyOutcomeCaptureSources(
                        security_quotes=lambda symbols, **_kwargs: (
                            self.quote_batch(symbols, price=price)
                        ),
                        market_indices=lambda **_kwargs: self.index_batch(),
                    ),
                )

            first = capture("daily-1", 10.0)
            second = capture("daily-2", 11.0)

        self.assertNotEqual(
            first.snapshot.snapshot_id,
            second.snapshot.snapshot_id,
        )

    def test_daily_fact_contract_rejects_timezone_naive_times(self):
        from radar.replay_outcome_daily_capture import DailySecurityOutcomePoint

        with self.assertRaisesRegex(ValueError, "sourceTime_timezone_required"):
            DailySecurityOutcomePoint(
                symbol="000001",
                name="平安银行",
                sourceTime=datetime(2026, 9, 1, 15, 0),
                fetchedAt=CAPTURED_AT,
                price=10.0,
            )

    def test_daily_fact_contract_rejects_source_time_after_fetch(self):
        from radar.replay_outcome_daily_capture import (
            DailyMarketIndexOutcomePoint,
            DailySecurityOutcomePoint,
        )

        fetched_at = datetime(2026, 9, 1, 7, 5, tzinfo=UTC)
        source_time = fetched_at + timedelta(microseconds=1)
        with self.assertRaisesRegex(
            ValueError,
            "sourceTime_after_fetchedAt",
        ):
            DailySecurityOutcomePoint(
                symbol="000001",
                name="平安银行",
                sourceTime=source_time,
                fetchedAt=fetched_at,
                price=10.0,
            )
        with self.assertRaisesRegex(
            ValueError,
            "sourceTime_after_fetchedAt",
        ):
            DailyMarketIndexOutcomePoint(
                indexKey="sse_composite",
                symbol="000001",
                sourceSymbol="sh000001",
                sourceTime=source_time,
                fetchedAt=fetched_at,
                price=3000.0,
                changePercent=1.0,
            )

    def test_daily_index_contract_rejects_wrong_official_identity(self):
        from radar.replay_outcome_daily_capture import (
            DailyMarketIndexOutcomePoint,
        )

        common = {
            "sourceTime": datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
            "fetchedAt": CAPTURED_AT,
            "price": 3000.0,
            "changePercent": 1.0,
        }
        for identity in (
            {
                "indexKey": "sse_50",
                "symbol": "000016",
                "sourceSymbol": "sh000016",
            },
            {
                "indexKey": "sse_composite",
                "symbol": "000016",
                "sourceSymbol": "sh000016",
            },
        ):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(
                    ValueError,
                    "daily_outcome_market_index_identity_mismatch",
                ):
                    DailyMarketIndexOutcomePoint(**identity, **common)

    def test_snapshot_contract_rejects_intraday_security_as_close_fact(self):
        from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["securityQuotes"][0]["sourceTime"] = datetime(
            2026, 9, 1, 6, 30, tzinfo=UTC
        )

        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_security_close_time_invalid",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_snapshot_contract_rejects_intraday_index_as_close_fact(self):
        from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["marketIndices"][0]["sourceTime"] = datetime(
            2026, 9, 1, 6, 30, tzinfo=UTC
        )

        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_index_close_time_invalid",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_snapshot_contract_rejects_before_safe_close_capture_time(self):
        from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["capturedAt"] = datetime(
            2026, 9, 1, 7, 4, 59, tzinfo=UTC
        )

        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_snapshot_before_safe_close",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_snapshot_contract_allows_explicit_suspension_old_source_time(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeSnapshot,
            DailySecurityOutcomePoint,
            _daily_snapshot_id,
            _membership_digest,
            _outcome_points_digest,
        )
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["securityQuotes"][0].update({
            "sourceTime": datetime(2026, 8, 31, 7, 0, tzinfo=UTC),
            "tradingStatus": QuoteTradingStatus.SUSPENDED.value,
        })
        security_points = [
            DailySecurityOutcomePoint.model_validate(item)
            for item in payload["securityQuotes"]
        ]
        security_digest = _outcome_points_digest(security_points)
        index_digest = _outcome_points_digest(snapshot.market_indices)
        payload["sourceIds"] = [
            "tencent_quotes:suspension:sha256:"
            f"{security_digest}",
            "tencent_indices:close:sha256:"
            f"{index_digest}",
        ]
        payload["snapshotId"] = _daily_snapshot_id(
            task_bundle_id=snapshot.task_bundle_id,
            output_bundle_id=snapshot.output_bundle_id,
            trade_date=snapshot.trade_date,
            security_digest=security_digest,
            index_digest=index_digest,
            membership_digest=_membership_digest(
                payload["sectorMembershipsBySample"]
            ),
        )

        parsed = DailyOutcomeSnapshot.model_validate(payload)

        self.assertEqual(
            parsed.security_quotes[0].trading_status,
            QuoteTradingStatus.SUSPENDED.value,
        )

    def test_snapshot_contract_rejects_content_hash_not_matching_points(self):
        from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["securityQuotes"][0]["price"] = 123.45

        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_source_content_hash_mismatch",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_snapshot_contract_rejects_snapshot_id_not_matching_content(self):
        from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["snapshotId"] = "stage9-outcome-daily-incorrect"

        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_snapshot_identity_mismatch",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_snapshot_contract_binds_frozen_sector_memberships_to_identity(self):
        from radar.replay_outcome_daily_capture import DailyOutcomeSnapshot
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        snapshot = RadarReplayOutcomeDailyProviderTests.snapshot(
            date(2026, 9, 1),
            0,
        )
        payload = snapshot.model_dump(mode="python", by_alias=True)
        payload["sectorMembershipsBySample"]["sample-1"]["C39"] = [
            "000001",
        ]

        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_snapshot_identity_mismatch",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_before_safe_close_rejects_without_source_calls(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeCaptureSources,
            capture_replay_outcome_day,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            tasks, outputs = self.inputs(root)
            calls = []
            sources = DailyOutcomeCaptureSources(
                security_quotes=lambda *_args, **_kwargs: calls.append("quotes"),
                market_indices=lambda **_kwargs: calls.append("indices"),
            )
            with self.assertRaisesRegex(
                ValueError,
                "daily_outcome_capture_before_safe_close",
            ):
                capture_replay_outcome_day(
                    task_bundle=tasks,
                    output_bundle=outputs,
                    output_dir=root / "daily",
                    trade_date=date(2026, 9, 1),
                    captured_at=datetime(2026, 9, 1, 6, 59, tzinfo=UTC),
                    day_kind_provider=lambda _day: "full",
                    sources=sources,
                )

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
