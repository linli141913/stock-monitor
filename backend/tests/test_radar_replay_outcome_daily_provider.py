import tempfile
import unittest
from datetime import date, datetime, timezone

from radar.replay_objective_outcomes import ObjectiveOutcomeRequest
from radar.replay_outcome_daily_capture import (
    DailyMarketIndexOutcomePoint,
    DailyOutcomeSnapshot,
    DailySecurityOutcomePoint,
    _daily_snapshot_id,
    _membership_digest,
    _outcome_points_digest,
)


UTC = timezone.utc
DATES = (
    date(2026, 9, 1),
    date(2026, 9, 2),
    date(2026, 9, 3),
    date(2026, 9, 4),
    date(2026, 9, 7),
    date(2026, 9, 8),
)


class RadarReplayOutcomeDailyProviderTests(unittest.TestCase):
    @staticmethod
    def snapshot(day, index):
        captured_at = datetime(
            day.year, day.month, day.day, 7, 10, tzinfo=UTC
        )
        quotes = []
        for symbol, base, factor in (
            ("000001", 10.0, 1.02),
            ("000002", 10.0, 1.01),
            ("510300", 100.0, 1.015),
        ):
            price = base * factor ** index
            quotes.append(DailySecurityOutcomePoint(
                symbol=symbol,
                name=symbol,
                sourceTime=captured_at,
                fetchedAt=captured_at,
                price=price,
                previousClose=(base * factor ** max(0, index - 1)),
                highPrice=price,
                lowPrice=price,
                turnoverAmountCny=1_000_000.0 + index,
            ))
        indices = []
        for key, symbol, source_symbol in (
            ("sse_composite", "000001", "sh000001"),
            ("szse_component", "399001", "sz399001"),
            ("chinext", "399006", "sz399006"),
            ("star50", "000688", "sh000688"),
        ):
            price = 3000.0 * 1.005 ** index
            indices.append(DailyMarketIndexOutcomePoint(
                indexKey=key,
                symbol=symbol,
                sourceSymbol=source_symbol,
                sourceTime=captured_at,
                fetchedAt=captured_at,
                price=price,
                changePercent=0.5,
            ))
        security_digest = _outcome_points_digest(quotes)
        index_digest = _outcome_points_digest(indices)
        return DailyOutcomeSnapshot(
            snapshotId=_daily_snapshot_id(
                task_bundle_id="tasks-1",
                output_bundle_id="outputs-1",
                trade_date=day,
                security_digest=security_digest,
                index_digest=index_digest,
                membership_digest=_membership_digest({
                    "sample-1": {
                        "C39": ["000001", "000002"],
                        "C40": ["000002"],
                    },
                }),
            ),
            taskBundleId="tasks-1",
            outputBundleId="outputs-1",
            tradeDate=day,
            capturedAt=captured_at,
            status="ready",
            expectedSecurityCount=3,
            readySecurityCount=3,
            sectorMembershipsBySample={
                "sample-1": {
                    "C39": ["000001", "000002"],
                    "C40": ["000002"],
                },
            },
            securityQuotes=quotes,
            marketIndices=indices,
            missingSymbols=[],
            sourceIds=[
                "tencent_quotes:"
                f"day-{day}:sha256:{security_digest}",
                "tencent_indices:"
                f"day-{day}:sha256:{index_digest}",
            ],
            reasons=[],
        )

    def provider(self, *, omit_last=False):
        from radar.replay_outcome_daily_provider import (
            DailySnapshotObjectiveOutcomeProvider,
        )

        snapshots = [
            self.snapshot(day, index) for index, day in enumerate(DATES)
        ]
        if omit_last:
            snapshots.pop()
        return DailySnapshotObjectiveOutcomeProvider(snapshots)

    @staticmethod
    def request(domain, target_id):
        return ObjectiveOutcomeRequest(
            task_bundle_id="tasks-1",
            output_bundle_id="outputs-1",
            sample_id="sample-1",
            radar_run_id="radar-1",
            role="development",
            as_of=datetime(2026, 9, 1, 6, 30, tzinfo=UTC),
            domain=domain,
            target_id=target_id,
            maturity_date=date(2026, 9, 8),
            evaluation_trade_dates=DATES[1:],
            output_source_ids=(f"rule-output-{domain}-1",),
        )

    def test_etf_provider_computes_five_day_return_drawdown_and_turnover(self):
        result = self.provider()(self.request("etf", "510300"))

        self.assertEqual(result.status, "ready")
        self.assertAlmostEqual(
            result.outcome_metrics["return5d"],
            1.015 ** 5 - 1,
        )
        self.assertEqual(result.outcome_metrics["maxDrawdown"], 0.0)
        self.assertGreater(result.outcome_metrics["turnover"], 0)

    def test_official_previous_close_prevents_corporate_action_false_loss(self):
        from radar.replay_outcome_daily_provider import (
            DailySnapshotObjectiveOutcomeProvider,
        )

        snapshots = []
        for index, day in enumerate(DATES):
            snapshot = self.snapshot(day, index)
            if index:
                payload = snapshot.model_dump(mode="python", by_alias=True)
                for quote in payload["securityQuotes"]:
                    if quote["symbol"] == "510300":
                        for field in (
                            "price",
                            "previousClose",
                            "highPrice",
                            "lowPrice",
                        ):
                            quote[field] /= 2
                security_points = [
                    DailySecurityOutcomePoint.model_validate(item)
                    for item in payload["securityQuotes"]
                ]
                security_digest = _outcome_points_digest(security_points)
                index_digest = _outcome_points_digest(snapshot.market_indices)
                payload["sourceIds"] = [
                    "tencent_quotes:corporate-action:sha256:"
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
                snapshot = DailyOutcomeSnapshot.model_validate(payload)
            snapshots.append(snapshot)

        result = DailySnapshotObjectiveOutcomeProvider(snapshots)(
            self.request("etf", "510300")
        )

        self.assertEqual(result.status, "ready")
        self.assertAlmostEqual(
            result.outcome_metrics["return5d"],
            1.015 ** 5 - 1,
        )
        self.assertEqual(result.outcome_metrics["maxDrawdown"], 0.0)

    def test_sector_and_market_use_independent_future_price_facts(self):
        provider = self.provider()

        sector = provider(self.request("sector", "C39"))
        market = provider(self.request("market", "a-share"))

        self.assertEqual(sector.status, "ready")
        self.assertEqual(sector.outcome_metrics["positiveReturnDays5d"], 5.0)
        self.assertEqual(sector.outcome_metrics["constituentCoverage"], 1.0)
        self.assertIn("relativeMarketReturn5d", sector.outcome_metrics)
        self.assertEqual(market.status, "ready")
        self.assertAlmostEqual(
            market.outcome_metrics["meanIndexReturn5d"],
            1.005 ** 5 - 1,
        )
        self.assertIn("marketBreadthReturn5d", market.outcome_metrics)

    def test_leader_is_compared_with_its_frozen_sector_members(self):
        result = self.provider()(self.request("leader", "000001"))

        self.assertEqual(result.status, "ready")
        self.assertGreater(
            result.outcome_metrics["relativeSectorReturn5d"],
            0.0,
        )
        self.assertEqual(result.outcome_metrics["maxAdverseExcursion"], 0.0)

    def test_missing_daily_snapshot_stays_missing(self):
        result = self.provider(omit_last=True)(
            self.request("leader", "000001")
        )

        self.assertEqual(result.status, "missing")
        self.assertEqual(result.outcome_metrics, {})
        self.assertIn("objective_daily_snapshot_missing", result.reasons)

    def test_baseline_trade_date_uses_shanghai_timezone(self):
        request = self.request("etf", "510300")
        request = ObjectiveOutcomeRequest(
            **{
                **request.__dict__,
                "as_of": datetime(2026, 8, 31, 16, 30, tzinfo=UTC),
            }
        )

        result = self.provider()(request)

        self.assertEqual(result.status, "ready")

    def test_daily_snapshot_bundle_identity_must_match_request(self):
        request = self.request("etf", "510300")
        request = ObjectiveOutcomeRequest(
            **{
                **request.__dict__,
                "output_bundle_id": "other-outputs",
            }
        )

        result = self.provider()(request)

        self.assertEqual(result.status, "unverifiable")
        self.assertIn(
            "objective_daily_bundle_identity_mismatch",
            result.reasons,
        )

    def test_provider_rejects_membership_drift_across_daily_snapshots(self):
        from radar.replay_outcome_daily_capture import (
            DailyOutcomeSnapshot,
            _daily_snapshot_id,
            _membership_digest,
        )
        from radar.replay_outcome_daily_provider import (
            DailySnapshotObjectiveOutcomeProvider,
        )

        snapshots = [
            self.snapshot(day, index) for index, day in enumerate(DATES)
        ]
        payload = snapshots[1].model_dump(mode="python", by_alias=True)
        payload["sectorMembershipsBySample"]["sample-1"]["C39"] = [
            "000001",
        ]
        payload["snapshotId"] = _daily_snapshot_id(
            task_bundle_id=snapshots[1].task_bundle_id,
            output_bundle_id=snapshots[1].output_bundle_id,
            trade_date=snapshots[1].trade_date,
            security_digest=snapshots[1].source_ids[0].rsplit(
                ":sha256:", 1
            )[1],
            index_digest=snapshots[1].source_ids[1].rsplit(
                ":sha256:", 1
            )[1],
            membership_digest=_membership_digest(
                payload["sectorMembershipsBySample"]
            ),
        )
        snapshots[1] = DailyOutcomeSnapshot.model_validate(payload)

        with self.assertRaisesRegex(
            ValueError,
            "objective_daily_membership_drift",
        ):
            DailySnapshotObjectiveOutcomeProvider(snapshots)

    def test_ready_daily_snapshot_cannot_hide_missing_index_or_member(self):
        payload = self.snapshot(DATES[0], 0).model_dump(
            mode="python", by_alias=True
        )
        payload["marketIndices"].pop()

        with self.assertRaisesRegex(
            ValueError,
            "ready_daily_outcome_snapshot_incomplete",
        ):
            DailyOutcomeSnapshot.model_validate(payload)

    def test_mature_daily_snapshots_generate_complete_four_domain_labels(self):
        from pathlib import Path

        from radar.replay_objective_outcomes import (
            collect_replay_objective_outcomes,
        )
        from tests.test_radar_replay_objective_outcomes import (
            RadarReplayObjectiveOutcomeTests,
        )

        helper = RadarReplayObjectiveOutcomeTests()
        provider = self.provider()
        providers = {
            domain: provider for domain in ("market", "sector", "etf", "leader")
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = collect_replay_objective_outcomes(
                task_bundle=helper.task_bundle(),
                output_bundle=helper.output_bundle(),
                output_dir=Path(directory) / "outcomes",
                evaluated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
                day_kind_provider=lambda day: (
                    "closed" if day.weekday() >= 5 else "full"
                ),
                providers=providers,
                daily_snapshot_ids=provider.snapshot_ids,
            )

        self.assertEqual(result.bundle.status, "ready")
        self.assertEqual(result.bundle.ready_observation_count, 5)
        self.assertEqual(result.bundle.missing_observation_count, 0)
        self.assertEqual(
            result.bundle.daily_snapshot_ids,
            list(provider.snapshot_ids),
        )
        self.assertEqual(
            {
                (label.domain, label.target_id)
                for label in result.label_bundle.samples[0].labels
            },
            {
                ("market", "a-share"),
                ("sector", "C39"),
                ("sector", "C40"),
                ("etf", "510300"),
                ("leader", "000001"),
            },
        )

        payload = self.snapshot(DATES[0], 0).model_dump(
            mode="python", by_alias=True
        )
        payload["sectorMembershipsBySample"]["sample-1"]["C39"].append(
            "000003"
        )
        with self.assertRaisesRegex(
            ValueError,
            "daily_outcome_membership_quote_missing",
        ):
            DailyOutcomeSnapshot.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
