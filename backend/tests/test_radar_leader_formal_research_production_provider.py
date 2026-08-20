import unittest
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from radar.leader_formal_research_runtime_assembly import (
    LeaderFormalResearchRuntimeComponentStatus,
    build_leader_formal_research_runtime_assembly,
)
from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionDeliveryResolutionStatus,
    LeaderFormalResearchProductionCollectedSource,
    LeaderFormalResearchProductionProviderSet,
    LeaderFormalResearchProductionSourceDelivery,
    LeaderFormalResearchProductionSourceProof,
    LeaderFormalResearchProductionSourceStatus,
    build_leader_formal_research_risk_source_proof,
    build_leader_formal_research_production_provider_set,
    resolve_leader_formal_research_production_delivery,
)
from radar.leader_research_runtime_provider import (
    build_leader_research_runtime_source_context,
)
from tests import test_radar_leader_risk_lifecycle_batch as risk_helpers
from tests import test_radar_leader_research_source_admission as source_helpers


class _EmptyReviewRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        del symbols, as_of
        return ()


class LeaderFormalResearchProductionProviderTests(unittest.TestCase):
    def setUp(self):
        helper = risk_helpers.LeaderRiskLifecycleBatchTests(
            methodName=(
                "test_two_human_versions_replay_into_partial_runtime_batch"
            )
        )
        helper.setUp()
        self.helper = helper
        self.plan = helper.plan
        self.context = build_leader_research_runtime_source_context(
            candidate_plan=self.plan,
            quote_batch=helper.raw["quote_batch"],
            quote_health=helper.raw["quote_health"],
            security_records=helper.raw["security_records"],
            industry_records=helper.raw["industry_records"],
        )

    def proof(self, *, status="completed", returned_count=None):
        return LeaderFormalResearchProductionSourceProof(
            component_name="history",
            source_contract_id="tencent-qfq-daily-history-v1",
            status=LeaderFormalResearchProductionSourceStatus(status),
            radar_run_id=self.plan.radar_run_id,
            candidate_plan_id=self.plan.candidate_set_id,
            quote_batch_id=self.plan.quote_batch_id,
            as_of=self.plan.as_of,
            source_time=self.plan.as_of - timedelta(seconds=2),
            fetched_at=self.plan.as_of + timedelta(seconds=2),
            expected_count=self.plan.candidate_count,
            returned_count=(
                self.plan.candidate_count
                if returned_count is None
                else returned_count
            ),
        )

    def delivery(self, **changes):
        proof = changes.pop("proof", self.proof())
        payload = changes.pop("payload", ("real-history-payload",))
        return LeaderFormalResearchProductionSourceDelivery(
            proof=proof,
            payload=payload,
            **changes,
        )

    def resolve(self, value):
        return resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="history",
            value=value,
        )

    def test_complete_same_round_delivery_returns_original_payload(self):
        delivery = self.delivery()

        result = self.resolve(delivery)

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )
        self.assertIs(result.payload, delivery.payload)
        self.assertEqual(result.proof, delivery.proof)
        evidence = str(result.to_evidence())
        self.assertNotIn("real-history-payload", evidence)
        self.assertNotIn(self.plan.items[0].symbol, evidence)

    def test_one_second_source_server_clock_lead_is_accepted(self):
        proof = replace(
            self.proof(),
            source_time=self.plan.as_of,
            fetched_at=self.plan.as_of - timedelta(seconds=1),
        )

        result = self.resolve(self.delivery(proof=proof))

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )

    def test_six_second_source_server_clock_lead_is_rejected(self):
        proof = replace(
            self.proof(),
            source_time=self.plan.as_of,
            fetched_at=self.plan.as_of - timedelta(seconds=6),
        )

        result = self.resolve(self.delivery(proof=proof))

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
        )

    def test_cross_plan_or_quote_batch_is_source_unverified(self):
        cases = (
            replace(self.proof(), candidate_plan_id="other-plan"),
            replace(self.proof(), quote_batch_id="other-quotes"),
            replace(
                self.proof(),
                as_of=self.plan.as_of + timedelta(minutes=1),
            ),
        )

        for proof in cases:
            with self.subTest(proof=proof):
                result = self.resolve(self.delivery(proof=proof))
                self.assertEqual(
                    result.status,
                    LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(result.payload)

    def test_completed_status_cannot_hide_incomplete_coverage(self):
        result = self.resolve(self.delivery(
            proof=self.proof(returned_count=self.plan.candidate_count - 1),
        ))

        self.assertEqual(
            result.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn(
            "leader_formal_research_production_delivery_coverage_unverified",
            result.reasons,
        )
        self.assertIsNone(result.payload)

    def test_failed_delivery_rejects_payload_and_keeps_stable_reason(self):
        failed = replace(
            self.proof(status="source_failed", returned_count=0),
            source_time=None,
            fetched_at=None,
        )
        malformed = self.resolve(self.delivery(proof=failed))
        valid = self.resolve(self.delivery(proof=failed, payload=None))

        self.assertEqual(
            malformed.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            valid.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            valid.reasons,
            ("leader_formal_research_production_delivery_source_failed",),
        )
        self.assertIsNone(valid.payload)

    def test_runtime_assembly_consumes_payload_and_retains_bound_proof(self):
        helper = source_helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_in_one_batch"
            )
        )
        helper.setUp()
        proof = replace(
            self.proof(),
            radar_run_id=helper.plan.radar_run_id,
            candidate_plan_id=helper.plan.candidate_set_id,
            quote_batch_id=helper.plan.quote_batch_id,
            as_of=helper.plan.as_of,
            source_time=helper.plan.as_of - timedelta(seconds=2),
            fetched_at=helper.plan.as_of + timedelta(seconds=2),
            expected_count=helper.plan.candidate_count,
            returned_count=helper.plan.candidate_count,
        )
        provider = Mock(return_value=self.delivery(
            proof=proof,
            payload=helper.history_entries(),
        ))

        result = build_leader_formal_research_runtime_assembly(
            helper.context,
            repository=_EmptyReviewRepository(),
            history_provider=provider,
        )

        history = next(
            item for item in result.components if item.name == "history"
        )
        self.assertEqual(
            history.status,
            LeaderFormalResearchRuntimeComponentStatus.READY,
        )
        self.assertEqual(result.production_source_proofs[0], proof)
        self.assertEqual(
            tuple(item.component_name for item in result.production_source_proofs),
            ("history", "risk"),
        )
        provider.assert_called_once_with(helper.context)

    def test_runtime_assembly_marks_invalid_delivery_source_unverified(self):
        bad = self.delivery(proof=replace(
            self.proof(),
            quote_batch_id="other-quotes",
        ))

        result = build_leader_formal_research_runtime_assembly(
            self.context,
            repository=_EmptyReviewRepository(),
            history_provider=Mock(return_value=bad),
        )

        history = next(
            item for item in result.components if item.name == "history"
        )
        self.assertEqual(
            history.status,
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.production_source_proofs[0], bad.proof)
        self.assertEqual(
            result.production_source_proofs[-1].component_name,
            "risk",
        )

    def test_cross_component_delivery_cannot_fill_another_source_proof(self):
        cross_component = replace(
            self.proof(),
            component_name="sector_rule",
        )
        result = build_leader_formal_research_runtime_assembly(
            self.context,
            repository=_EmptyReviewRepository(),
            history_provider=Mock(return_value=self.delivery(
                proof=cross_component,
            )),
        )

        history = next(
            item for item in result.components if item.name == "history"
        )
        self.assertEqual(
            history.status,
            LeaderFormalResearchRuntimeComponentStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            tuple(item.component_name for item in result.production_source_proofs),
            ("risk",),
        )

    def test_d8_review_chains_produce_complete_bound_risk_proof(self):
        chains = tuple(
            SimpleNamespace(
                symbol=item.symbol,
                versions=(SimpleNamespace(
                    document=SimpleNamespace(
                        published_at=self.plan.as_of - timedelta(minutes=5)
                    ),
                    content=SimpleNamespace(
                        fetched_at=self.plan.as_of - timedelta(minutes=1)
                    ),
                ),),
            )
            for item in self.plan.items
        )
        bridge = SimpleNamespace(
            review_chains=chains,
            ready_count=self.plan.candidate_count,
            reasons=(),
        )

        proof = build_leader_formal_research_risk_source_proof(
            self.context,
            bridge=bridge,
        )

        self.assertEqual(proof.component_name, "risk")
        self.assertEqual(
            proof.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(proof.returned_count, self.plan.candidate_count)

    def test_missing_d8_review_chains_remain_not_run(self):
        proof = build_leader_formal_research_risk_source_proof(
            self.context,
            bridge=SimpleNamespace(
                review_chains=(),
                ready_count=0,
                reasons=(
                    "leader_formal_research_bridge_review_versions_missing",
                ),
            ),
        )

        self.assertEqual(
            proof.status,
            LeaderFormalResearchProductionSourceStatus.NOT_RUN,
        )
        self.assertIsNone(proof.source_time)
        self.assertIsNone(proof.fetched_at)

    def collected_source(self, **changes):
        values = {
            "component_name": "history",
            "source_contract_id": "tencent-qfq-daily-history-v1",
            "status": LeaderFormalResearchProductionSourceStatus.COMPLETED,
            "source_time": self.plan.as_of - timedelta(seconds=2),
            "fetched_at": self.plan.as_of + timedelta(seconds=2),
            "symbols": tuple(item.symbol for item in self.plan.items),
            "payload": ("real-history-payload",),
        }
        values.update(changes)
        return LeaderFormalResearchProductionCollectedSource(**values)

    def test_provider_set_binds_complete_collector_to_current_plan(self):
        collector = Mock(return_value=self.collected_source())
        providers = build_leader_formal_research_production_provider_set(
            history_collector=collector,
        )

        delivery = providers.history_provider(self.context)
        resolution = self.resolve(delivery)

        self.assertIsInstance(
            providers,
            LeaderFormalResearchProductionProviderSet,
        )
        self.assertEqual(
            resolution.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.READY,
        )
        self.assertEqual(
            delivery.proof.returned_count,
            self.plan.candidate_count,
        )
        collector.assert_called_once_with(self.context)

    def test_provider_set_rejects_partial_or_cross_component_payload(self):
        symbols = tuple(item.symbol for item in self.plan.items[:-1])
        cases = (
            self.collected_source(symbols=symbols),
            self.collected_source(component_name="tradability"),
            self.collected_source(symbols=(["unhashable"],)),
        )

        for collected in cases:
            with self.subTest(component=collected.component_name):
                providers = build_leader_formal_research_production_provider_set(
                    history_collector=Mock(return_value=collected),
                )
                delivery = providers.history_provider(self.context)
                resolution = self.resolve(delivery)
                self.assertEqual(
                    delivery.proof.status,
                    LeaderFormalResearchProductionSourceStatus.SOURCE_UNVERIFIED,
                )
                self.assertIsNone(delivery.payload)
                self.assertEqual(
                    resolution.status,
                    LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_UNVERIFIED,
                )

    def test_provider_set_maps_exception_and_unconfigured_source(self):
        providers = build_leader_formal_research_production_provider_set(
            history_collector=Mock(side_effect=RuntimeError("private detail")),
        )
        failed = self.resolve(providers.history_provider(self.context))
        not_run = resolve_leader_formal_research_production_delivery(
            self.context,
            component_name="business_catalyst",
            value=providers.business_catalyst_provider(self.context),
        )

        self.assertEqual(
            failed.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            not_run.status,
            LeaderFormalResearchProductionDeliveryResolutionStatus.NOT_RUN,
        )
        self.assertNotIn("private detail", str(failed.to_evidence()))

    def test_provider_set_exposes_existing_runtime_callback_names(self):
        providers = build_leader_formal_research_production_provider_set()

        callbacks = providers.to_runtime_provider_kwargs()

        self.assertEqual(set(callbacks), {
            "leader_history_input_provider",
            "leader_business_catalyst_input_provider",
            "leader_tradability_input_provider",
            "leader_sector_rule_input_provider",
        })
        self.assertTrue(all(callable(value) for value in callbacks.values()))

    def test_provider_set_callback_enters_existing_runtime_assembly(self):
        helper = source_helpers.LeaderResearchSourceAdmissionTests(
            methodName=(
                "test_all_verified_sources_enter_existing_provider_in_one_batch"
            )
        )
        helper.setUp()
        symbols = tuple(item.symbol for item in helper.plan.items)
        providers = build_leader_formal_research_production_provider_set(
            history_collector=Mock(return_value=(
                LeaderFormalResearchProductionCollectedSource(
                    component_name="history",
                    source_contract_id="tencent-qfq-daily-history-v1",
                    status=(
                        LeaderFormalResearchProductionSourceStatus.COMPLETED
                    ),
                    source_time=helper.plan.as_of - timedelta(seconds=2),
                    fetched_at=helper.plan.as_of + timedelta(seconds=2),
                    symbols=symbols,
                    payload=helper.history_entries(),
                )
            )),
        )

        result = build_leader_formal_research_runtime_assembly(
            helper.context,
            repository=_EmptyReviewRepository(),
            history_provider=providers.history_provider,
        )

        history = next(
            item for item in result.components if item.name == "history"
        )
        self.assertEqual(
            history.status,
            LeaderFormalResearchRuntimeComponentStatus.READY,
        )
        self.assertEqual(
            result.production_source_proofs[0].returned_count,
            helper.plan.candidate_count,
        )


if __name__ == "__main__":
    unittest.main()
