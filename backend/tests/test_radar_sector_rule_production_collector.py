import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from radar.leader_formal_research_production_collectors import (
    LeaderFormalResearchProductionSourceLoaders,
    build_leader_formal_research_validated_provider_set,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    LeaderFormalResearchProductionSourceStatus,
    resolve_leader_formal_research_production_delivery,
)
from radar.sector_rule_production_collector import (
    SectorRuleProductionFrozenBatch,
    build_sector_rule_production_loader,
    collect_sector_rule_production_source,
)
from radar.sector_threshold_review import SectorThresholdApprovalLoadResult
from tests import test_radar_sector_rule_runtime_bridge as helpers


class SectorRuleProductionCollectorTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.SectorRuleRuntimeBridgeTests(
            methodName="test_complete_versioned_evidence_returns_ready_contract"
        )
        helper.setUp()
        self.helper = helper
        self.context = helper.context
        self.source_batch = helper.source_batch()

    def frozen(self, *, source_batch=None, status=None, fetched_at=None):
        return SectorRuleProductionFrozenBatch(
            source_batch=(
                self.source_batch if source_batch is None else source_batch
            ),
            fetched_at=(
                self.context.as_of + timedelta(seconds=2)
                if fetched_at is None
                else fetched_at
            ),
            source_status=(
                status
                or LeaderFormalResearchProductionSourceStatus.COMPLETED
            ),
        )

    @staticmethod
    def approved_binder(source_batch):
        return SectorThresholdApprovalLoadResult(
            status="approved",
            reasons=(),
            source_batch=source_batch,
        )

    def collect(self, frozen):
        return collect_sector_rule_production_source(
            self.context,
            frozen,
            threshold_approval_binder=self.approved_binder,
        )

    def test_complete_rule_evidence_replays_into_existing_provider(self):
        frozen = self.frozen()
        source = self.collect(frozen)

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(
            source.symbols,
            tuple(item.symbol for item in self.context.candidate_plan.items),
        )
        providers = build_leader_formal_research_validated_provider_set(
            LeaderFormalResearchProductionSourceLoaders(
                sector_rule_loader=build_sector_rule_production_loader(
                    frozen,
                    threshold_approval_binder=self.approved_binder,
                ),
            )
        )
        delivery = providers.sector_rule_provider(self.context)
        resolution = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="sector_rule",
            value=delivery,
        )
        self.assertEqual(
            resolution.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )

    def test_missing_threshold_is_bound_before_production_replay(self):
        source_without_threshold = replace(
            self.source_batch,
            threshold_approval_evidence=None,
        )
        binder = Mock(return_value=SectorThresholdApprovalLoadResult(
            status="approved",
            reasons=(),
            source_batch=self.source_batch,
        ))

        source = collect_sector_rule_production_source(
            self.context,
            self.frozen(source_batch=source_without_threshold),
            threshold_approval_binder=binder,
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertIs(source.payload, self.source_batch)
        binder.assert_called_once_with(source_without_threshold)

    def test_unavailable_latest_threshold_fails_closed_with_reason(self):
        source_without_threshold = replace(
            self.source_batch,
            threshold_approval_evidence=None,
        )
        binder = Mock(return_value=SectorThresholdApprovalLoadResult(
            status="not_ready",
            reasons=("sector_threshold_approval_snapshot_missing",),
        ))

        source = collect_sector_rule_production_source(
            self.context,
            self.frozen(source_batch=source_without_threshold),
            threshold_approval_binder=binder,
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            source.reasons,
            ("sector_threshold_approval_snapshot_missing",),
        )
        self.assertEqual(source.symbols, ())
        self.assertIsNone(source.payload)

    def test_prebound_threshold_is_reverified_against_latest_store(self):
        latest_batch = replace(
            self.source_batch,
            threshold_approval_evidence=replace(
                self.source_batch.threshold_approval_evidence,
                approval_id="latest-approved-threshold",
            ),
        )
        binder = Mock(return_value=SectorThresholdApprovalLoadResult(
            status="approved",
            reasons=(),
            source_batch=latest_batch,
        ))

        source = collect_sector_rule_production_source(
            self.context,
            self.frozen(),
            threshold_approval_binder=binder,
        )

        binder.assert_called_once_with(self.source_batch)
        self.assertIs(source.payload, latest_batch)

    def test_malformed_threshold_binder_result_fails_closed(self):
        source = collect_sector_rule_production_source(
            self.context,
            self.frozen(),
            threshold_approval_binder=Mock(return_value=None),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            source.reasons,
            ("sector_threshold_approval_binding_unverified",),
        )
        self.assertIsNone(source.payload)

    def test_one_second_source_server_clock_lead_reaches_provider(self):
        feature_batch = self.source_batch.feature_batch.model_copy(update={
            "source_time": self.context.as_of,
            "fetched_at": self.context.as_of - timedelta(seconds=1),
        })
        frozen = self.frozen(
            source_batch=replace(
                self.source_batch,
                feature_batch=feature_batch,
            ),
            fetched_at=self.context.as_of - timedelta(seconds=1),
        )

        source = self.collect(frozen)

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )

    def test_missing_formal_rule_evidence_closes_whole_batch(self):
        source = self.collect(
            self.frozen(source_batch=self.helper.source_batch(
                with_gaps=True,
            )),
        )

        self.assertEqual(
            source.status,
            LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(source.symbols, ())
        self.assertIsNone(source.payload)

    def test_cross_release_and_late_fetch_are_rejected(self):
        cases = (
            self.frozen(source_batch=replace(
                self.source_batch,
                industry_release_id="other-release",
            )),
            self.frozen(
                fetched_at=self.context.as_of + timedelta(seconds=6),
            ),
        )

        for frozen in cases:
            with self.subTest(fetched_at=frozen.fetched_at):
                source = self.collect(frozen)
                self.assertEqual(
                    source.status,
                    LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(source.payload)

    def test_source_failed_and_not_run_preserve_distinct_statuses(self):
        for status in (
            LeaderFormalResearchProductionSourceStatus.SOURCE_FAILED,
            LeaderFormalResearchProductionSourceStatus.NOT_RUN,
        ):
            with self.subTest(status=status):
                frozen = SectorRuleProductionFrozenBatch(
                    source_batch=None,
                    fetched_at=None,
                    source_status=status,
                )
                source = self.collect(frozen)
                self.assertEqual(source.status, status)
                self.assertEqual(source.symbols, ())
                self.assertIsNone(source.payload)

    def test_evidence_hides_features_thresholds_and_symbols(self):
        evidence = str(self.frozen().to_evidence())
        source_evidence = str(
            self.collect(self.frozen()).to_evidence()
        )

        self.assertNotIn("threshold", evidence.lower())
        self.assertNotIn("features", evidence.lower())
        for item in self.context.candidate_plan.items:
            self.assertNotIn(item.symbol, evidence)
            self.assertNotIn(item.symbol, source_evidence)


if __name__ == "__main__":
    unittest.main()
