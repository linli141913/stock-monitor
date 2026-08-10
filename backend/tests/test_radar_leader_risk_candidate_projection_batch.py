import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from unittest.mock import patch

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_candidate_projection import (
    LeaderRiskCandidateProjectionResult,
    build_leader_risk_candidate_projection,
)
from radar.leader_risk_candidate_projection_batch import (
    LEADER_RISK_CANDIDATE_PROJECTION_BATCH_CONTRACT_ID,
    LeaderRiskEvidenceBundleBatchEntry,
    LeaderRiskCandidateProjectionBatchEntry,
    LeaderRiskCandidateProjectionBatchInput,
    LeaderRiskCandidateProjectionBatchStatus,
    build_leader_risk_candidate_projection_batch,
    build_leader_risk_projection_batch_from_bundles,
)
from tests.test_radar_leader_risk_candidate_projection import (
    AS_OF,
    make_bundle,
    make_input,
    make_second_bundle,
)


SECOND_SYMBOL = "000001"
SECOND_ISSUER = "cninfo-org:9900000001"


def make_candidate_input(
    symbol,
    issuer_identity,
    *,
    as_of=AS_OF,
    history_ready=True,
):
    bundles = (
        make_bundle(
            symbol=symbol,
            issuer_identity=issuer_identity,
        ),
    )
    if history_ready:
        bundles += (
            make_second_bundle(
                symbol=symbol,
                issuer_identity=issuer_identity,
            ),
        )
    return make_input(
        *bundles,
        as_of=as_of,
        symbol=symbol,
        issuer_identity=issuer_identity,
    )


def make_batch_entry(
    symbol,
    issuer_identity,
    *,
    mapped_symbol=None,
    as_of=AS_OF,
    history_ready=True,
):
    return LeaderRiskCandidateProjectionBatchEntry(
        symbol=mapped_symbol or symbol,
        projection_input=make_candidate_input(
            symbol,
            issuer_identity,
            as_of=as_of,
            history_ready=history_ready,
        ),
    )


def make_batch(*entries, as_of=AS_OF):
    return LeaderRiskCandidateProjectionBatchInput(
        as_of=as_of,
        entries=tuple(entries),
    )


class LeaderRiskCandidateProjectionBatchTests(unittest.TestCase):
    def test_bundle_builder_recomputes_audit_and_preserves_order(self):
        result = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol="300081",
                    issuer_identity="cninfo-org:9900012108",
                    bundles=(make_bundle(), make_second_bundle()),
                ),
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol=SECOND_SYMBOL,
                    issuer_identity=SECOND_ISSUER,
                    bundles=(
                        make_bundle(
                            symbol=SECOND_SYMBOL,
                            issuer_identity=SECOND_ISSUER,
                        ),
                        make_second_bundle(
                            symbol=SECOND_SYMBOL,
                            issuer_identity=SECOND_ISSUER,
                        ),
                    ),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.READY,
        )
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081", SECOND_SYMBOL),
        )
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)

    def test_bundle_builder_keeps_short_history_missing_per_symbol(self):
        result = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol="300081",
                    issuer_identity="cninfo-org:9900012108",
                    bundles=(make_bundle(), make_second_bundle()),
                ),
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol=SECOND_SYMBOL,
                    issuer_identity=SECOND_ISSUER,
                    bundles=(make_bundle(
                        symbol=SECOND_SYMBOL,
                        issuer_identity=SECOND_ISSUER,
                    ),),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(
            result.items[1].reasons,
            ("risk_evidence_bundle_audit_history_insufficient",),
        )

    def test_bundle_builder_allows_missing_issuer_only_for_empty_history(self):
        result = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol="300081",
                    issuer_identity="cninfo-org:9900012108",
                    bundles=(make_bundle(), make_second_bundle()),
                ),
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol=SECOND_SYMBOL,
                    issuer_identity=None,
                    bundles=(),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(
            result.items[1].status,
            ResearchFeatureStatus.MISSING,
        )
        self.assertEqual(
            result.items[1].reasons,
            ("risk_evidence_bundle_audit_history_insufficient",),
        )

        invalid = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(LeaderRiskEvidenceBundleBatchEntry(
                symbol="300081",
                issuer_identity=None,
                bundles=(make_bundle(), make_second_bundle()),
            ),),
        )
        self.assertEqual(
            invalid.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
        )

    def test_bundle_builder_preserves_explicit_empty_source_status(self):
        result = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(LeaderRiskEvidenceBundleBatchEntry(
                symbol="300081",
                issuer_identity=None,
                bundles=(),
                source_status=ResearchFeatureStatus.SOURCE_FAILED,
                reasons=("risk_lifecycle_upstream_failed",),
            ),),
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            result.items[0].status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            result.items[0].reasons,
            ("risk_lifecycle_upstream_failed",),
        )

        unsafe = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(LeaderRiskEvidenceBundleBatchEntry(
                symbol="300081",
                issuer_identity=None,
                bundles=(),
                source_status=ResearchFeatureStatus.SOURCE_FAILED,
                reasons=("https://example.invalid/?token=secret",),
            ),),
        )
        self.assertEqual(
            unsafe.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertNotIn("secret", str(unsafe.to_evidence()))

    def test_bundle_builder_rejects_symbol_outside_stage6_scope(self):
        result = build_leader_risk_projection_batch_from_bundles(
            as_of=AS_OF,
            entries=(
                LeaderRiskEvidenceBundleBatchEntry(
                    symbol="920023",
                    issuer_identity="bse-issuer:920023",
                    bundles=(),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_batch_symbol_out_of_scope",),
        )
        self.assertEqual(result.items, ())

    def test_direct_batch_rejects_symbol_outside_stage6_scope(self):
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry("920023", "bse-issuer:920023"),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_batch_symbol_out_of_scope",),
        )
        self.assertEqual(result.items, ())

    def test_builds_ordered_ready_projection_map_and_compact_audit(self):
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry("300081", "cninfo-org:9900012108"),
                make_batch_entry(SECOND_SYMBOL, SECOND_ISSUER),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.READY,
        )
        self.assertEqual(result.input_count, 2)
        self.assertEqual(result.ready_count, 2)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081", SECOND_SYMBOL),
        )
        with self.assertRaises(TypeError):
            result.projections_by_symbol["000002"] = (
                result.items[0].projection
            )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            ("300081", SECOND_SYMBOL),
        )
        self.assertTrue(
            all(
                item.status == ResearchFeatureStatus.READY
                and item.projection is not None
                for item in result.items
            )
        )

        evidence = result.to_evidence()
        self.assertEqual(
            evidence["batchContractId"],
            LEADER_RISK_CANDIDATE_PROJECTION_BATCH_CONTRACT_ID,
        )
        self.assertEqual(evidence["status"], "ready")
        self.assertEqual(evidence["readyCount"], 2)
        self.assertEqual(evidence["items"][0]["index"], 0)
        self.assertNotIn("history", evidence["items"][0])
        self.assertNotIn("current", evidence["items"][0])
        self.assertEqual(
            evidence["gate"],
            {
                "riskFilterPassed": False,
                "formalGateReady": False,
                "formalUsable": False,
                "appliedToD3": False,
                "appliedToD1": False,
            },
        )

    def test_single_missing_item_keeps_other_projection_ready(self):
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry("300081", "cninfo-org:9900012108"),
                make_batch_entry(
                    SECOND_SYMBOL,
                    SECOND_ISSUER,
                    history_ready=False,
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(result.rejected_count, 1)
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081",),
        )
        self.assertEqual(
            result.items[1].status,
            ResearchFeatureStatus.MISSING,
        )
        self.assertEqual(
            result.items[1].reasons,
            ("risk_evidence_bundle_audit_history_insufficient",),
        )

    def test_all_missing_items_keep_batch_missing(self):
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry(
                    "300081",
                    "cninfo-org:9900012108",
                    history_ready=False,
                ),
                make_batch_entry(
                    SECOND_SYMBOL,
                    SECOND_ISSUER,
                    history_ready=False,
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.MISSING,
        )
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_batch_no_ready_items",),
        )
        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.rejected_count, 2)
        self.assertEqual(result.projections_by_symbol, {})

    def test_duplicate_symbol_rejects_whole_batch(self):
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry("300081", "cninfo-org:9900012108"),
                make_batch_entry(
                    SECOND_SYMBOL,
                    SECOND_ISSUER,
                    mapped_symbol="300081",
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_batch_duplicate_symbol",),
        )
        self.assertEqual(result.items, ())
        self.assertEqual(result.projections_by_symbol, {})

    def test_duplicate_input_symbol_rejects_whole_batch(self):
        duplicate_input = make_candidate_input(
            "300081",
            "cninfo-org:9900012108",
        )
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry(
                    "300081",
                    "cninfo-org:9900012108",
                ),
                LeaderRiskCandidateProjectionBatchEntry(
                    symbol="000002",
                    projection_input=duplicate_input,
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("risk_candidate_projection_batch_duplicate_symbol",),
        )
        self.assertEqual(result.items, ())
        self.assertEqual(result.projections_by_symbol, {})

    def test_identity_mismatch_only_rejects_matching_entry(self):
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry("300081", "cninfo-org:9900012108"),
                make_batch_entry(
                    SECOND_SYMBOL,
                    SECOND_ISSUER,
                    mapped_symbol="000002",
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].reasons,
            (
                "risk_candidate_projection_"
                "batch_mapping_identity_mismatch",
            ),
        )
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081",),
        )

    def test_non_e1_input_only_rejects_matching_entry(self):
        malformed = LeaderRiskCandidateProjectionResult(
            status=ResearchFeatureStatus.READY,
            projection=None,
        )
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry(
                    "300081",
                    "cninfo-org:9900012108",
                ),
                LeaderRiskCandidateProjectionBatchEntry(
                    symbol=SECOND_SYMBOL,
                    projection_input=malformed,
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].reasons,
            (
                "risk_candidate_projection_"
                "batch_item_contract_unverified",
            ),
        )
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081",),
        )

    def test_item_as_of_mismatch_only_rejects_matching_entry(self):
        for item_as_of in (
            AS_OF - timedelta(minutes=1),
            AS_OF.replace(tzinfo=None),
        ):
            with self.subTest(item_as_of=item_as_of):
                result = build_leader_risk_candidate_projection_batch(
                    make_batch(
                        make_batch_entry(
                            "300081",
                            "cninfo-org:9900012108",
                        ),
                        make_batch_entry(
                            SECOND_SYMBOL,
                            SECOND_ISSUER,
                            as_of=item_as_of,
                        ),
                    )
                )

                self.assertEqual(
                    result.status,
                    LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
                )
                self.assertEqual(
                    result.items[1].reasons,
                    (
                        "risk_candidate_projection_"
                        "batch_as_of_mismatch",
                    ),
                )
                self.assertEqual(
                    tuple(result.projections_by_symbol),
                    ("300081",),
                )

    def test_forged_d9_result_is_isolated_to_one_item(self):
        forged_input = make_candidate_input(
            SECOND_SYMBOL,
            SECOND_ISSUER,
        )
        forged_input = replace(
            forged_input,
            audit_result=replace(
                forged_input.audit_result,
                current_bundle_id="forged-bundle",
            ),
        )
        result = build_leader_risk_candidate_projection_batch(
            make_batch(
                make_batch_entry("300081", "cninfo-org:9900012108"),
                LeaderRiskCandidateProjectionBatchEntry(
                    symbol=SECOND_SYMBOL,
                    projection_input=forged_input,
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].reasons,
            ("risk_candidate_projection_audit_replay_mismatch",),
        )
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081",),
        )

    def test_unexpected_e1_exception_is_isolated_and_redacted(self):
        first = make_batch_entry(
            "300081",
            "cninfo-org:9900012108",
        )
        second = make_batch_entry(SECOND_SYMBOL, SECOND_ISSUER)
        real_builder = (
            __import__(
                "radar.leader_risk_candidate_projection",
                fromlist=["build_leader_risk_candidate_projection"],
            ).build_leader_risk_candidate_projection
        )

        def build_or_raise(input_value):
            if input_value.symbol == SECOND_SYMBOL:
                raise RuntimeError("secret upstream detail")
            return real_builder(input_value)

        with patch(
            "radar.leader_risk_candidate_projection_batch."
            "build_leader_risk_candidate_projection",
            side_effect=build_or_raise,
        ):
            result = build_leader_risk_candidate_projection_batch(
                make_batch(first, second)
            )

        self.assertEqual(
            result.status,
            LeaderRiskCandidateProjectionBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].status,
            ResearchFeatureStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            result.items[1].reasons,
            (
                "risk_candidate_projection_"
                "batch_item_recompute_failed",
            ),
        )
        self.assertNotIn("secret upstream detail", repr(result))
        self.assertEqual(
            tuple(result.projections_by_symbol),
            ("300081",),
        )

    def test_forged_ready_e1_result_is_rejected_defensively(self):
        first = make_batch_entry(
            "300081",
            "cninfo-org:9900012108",
        )
        valid = build_leader_risk_candidate_projection(
            first.projection_input
        )
        forged_results = (
            (
                "ready_without_projection",
                LeaderRiskCandidateProjectionResult(
                    status=ResearchFeatureStatus.READY,
                    projection=None,
                ),
            ),
            (
                "formal_flag_enabled",
                replace(valid, formal_usable=True),
            ),
            (
                "audit_contract_forged",
                replace(
                    valid,
                    projection=replace(
                        valid.projection,
                        audit_contract_id="forged-audit-contract",
                    ),
                ),
            ),
            (
                "bundle_contract_forged",
                replace(
                    valid,
                    projection=replace(
                        valid.projection,
                        bundle_contract_id="forged-bundle-contract",
                    ),
                ),
            ),
            (
                "malformed_reasons",
                replace(
                    valid,
                    status=ResearchFeatureStatus.SOURCE_UNVERIFIED,
                    projection=None,
                    reasons=None,
                ),
            ),
        )
        for case_name, forged in forged_results:
            with self.subTest(case_name=case_name):
                with patch(
                    "radar.leader_risk_candidate_projection_batch."
                    "build_leader_risk_candidate_projection",
                    return_value=forged,
                ):
                    result = (
                        build_leader_risk_candidate_projection_batch(
                            make_batch(first)
                        )
                    )

                self.assertEqual(
                    result.status,
                    (
                        LeaderRiskCandidateProjectionBatchStatus
                        .SOURCE_UNVERIFIED
                    ),
                )
                self.assertEqual(
                    result.items[0].reasons,
                    (
                        "risk_candidate_projection_"
                        "batch_result_unverified",
                    ),
                )
                self.assertEqual(
                    result.projections_by_symbol,
                    {},
                )

    def test_empty_naive_and_malformed_batches_fail_stably(self):
        cases = (
            (
                make_batch(),
                LeaderRiskCandidateProjectionBatchStatus.MISSING,
                "risk_candidate_projection_batch_empty",
            ),
            (
                make_batch(as_of=AS_OF.replace(tzinfo=None)),
                (
                    LeaderRiskCandidateProjectionBatchStatus
                    .SOURCE_UNVERIFIED
                ),
                "risk_candidate_projection_batch_as_of_timezone_missing",
            ),
            (
                None,
                (
                    LeaderRiskCandidateProjectionBatchStatus
                    .SOURCE_UNVERIFIED
                ),
                "risk_candidate_projection_batch_contract_unverified",
            ),
        )
        for input_value, expected_status, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_leader_risk_candidate_projection_batch(
                    input_value
                )

                self.assertEqual(result.status, expected_status)
                self.assertEqual(
                    result.reasons,
                    (expected_reason,),
                )
                self.assertEqual(result.projections_by_symbol, {})

    def test_contracts_are_frozen_and_hide_projection_inputs(self):
        entry = make_batch_entry(
            "300081",
            "cninfo-org:9900012108",
        )
        input_value = make_batch(entry)
        result = build_leader_risk_candidate_projection_batch(
            input_value
        )

        with self.assertRaises(FrozenInstanceError):
            entry.symbol = "000001"
        with self.assertRaises(FrozenInstanceError):
            result.status = (
                LeaderRiskCandidateProjectionBatchStatus.MISSING
            )
        self.assertNotIn(
            "risk-research-evidence-bundle:bundle-1",
            repr(input_value),
        )
        self.assertFalse(result.risk_filter_passed)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.applied_to_d3)
        self.assertFalse(result.applied_to_d1)


if __name__ == "__main__":
    unittest.main()
