import dataclasses
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from unittest.mock import patch

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_research_readiness_audit import (
    LeaderResearchReadinessAuditInput,
    LeaderResearchReadinessAuditStatus,
    build_leader_research_readiness_audit,
)
from radar.leader_research_readiness_audit_batch import (
    LEADER_RESEARCH_READINESS_AUDIT_BATCH_CONTRACT_ID,
    LeaderResearchReadinessAuditBatchEntry,
    LeaderResearchReadinessAuditBatchInput,
    LeaderResearchReadinessAuditBatchStatus,
    build_leader_research_readiness_audit_batch,
)
from tests.test_radar_leader_research_readiness_audit import (
    AS_OF,
    audit_input,
    cross_sectional_features,
    risk_item,
)


SECOND_SYMBOL = "000519"
THIRD_SYMBOL = "000021"


def make_audit_input(
    symbol="000725",
    *,
    as_of=AS_OF,
    partial=False,
):
    overrides = {
        "symbol": symbol,
        "as_of": as_of,
        "risk_projection_item": risk_item(symbol=symbol),
    }
    if partial:
        overrides["cross_sectional_features"] = (
            cross_sectional_features(
                industry_status=ResearchFeatureStatus.STALE,
            )
        )
    return audit_input(**overrides)


def make_entry(
    symbol="000725",
    *,
    audit_value=None,
):
    return LeaderResearchReadinessAuditBatchEntry(
        symbol=symbol,
        audit_input=(
            make_audit_input(symbol)
            if audit_value is None
            else audit_value
        ),
    )


def make_batch(*entries, as_of=AS_OF):
    return LeaderResearchReadinessAuditBatchInput(
        as_of=as_of,
        entries=tuple(entries),
    )


class LeaderResearchReadinessAuditBatchTests(unittest.TestCase):
    def test_ready_and_partial_audits_keep_order_and_enter_read_only_map(self):
        result = build_leader_research_readiness_audit_batch(
            make_batch(
                make_entry(),
                make_entry(
                    SECOND_SYMBOL,
                    audit_value=make_audit_input(
                        SECOND_SYMBOL,
                        partial=True,
                    ),
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditBatchStatus.PARTIAL,
        )
        self.assertEqual(
            tuple(item.symbol for item in result.items),
            ("000725", SECOND_SYMBOL),
        )
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(result.partial_count, 1)
        self.assertEqual(result.blocked_count, 0)
        self.assertEqual(result.audit_count, 2)
        self.assertEqual(
            tuple(result.audits_by_symbol),
            ("000725", SECOND_SYMBOL),
        )
        self.assertEqual(
            result.items[1].audit.status,
            LeaderResearchReadinessAuditStatus.PARTIAL,
        )
        self.assertEqual(
            result.batch_contract_id,
            LEADER_RESEARCH_READINESS_AUDIT_BATCH_CONTRACT_ID,
        )

    def test_all_ready_and_equivalent_timezone_are_accepted(self):
        result = build_leader_research_readiness_audit_batch(
            make_batch(
                make_entry(),
                make_entry(SECOND_SYMBOL),
                as_of=AS_OF.astimezone(
                    timezone(timedelta(hours=8))
                ),
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditBatchStatus.READY,
        )
        self.assertEqual(result.ready_count, 2)
        self.assertEqual(result.audit_count, 2)
        self.assertEqual(
            tuple(result.audits_by_symbol),
            ("000725", SECOND_SYMBOL),
        )
        with self.assertRaises(TypeError):
            result.audits_by_symbol["000001"] = (
                result.audits_by_symbol["000725"]
            )

    def test_one_blocked_audit_is_isolated_from_valid_candidate(self):
        malformed = replace(
            make_audit_input(SECOND_SYMBOL),
            cross_sectional_features=None,
        )
        result = build_leader_research_readiness_audit_batch(
            make_batch(
                make_entry(),
                make_entry(SECOND_SYMBOL, audit_value=malformed),
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].status,
            LeaderResearchReadinessAuditStatus.BLOCKED,
        )
        self.assertEqual(
            result.items[1].reasons,
            ("leader_research_audit_contract_unverified",),
        )
        self.assertEqual(
            tuple(result.audits_by_symbol),
            ("000725",),
        )

    def test_all_candidate_audits_blocked_marks_batch_blocked(self):
        malformed = replace(
            make_audit_input(),
            cross_sectional_features=None,
        )
        result = build_leader_research_readiness_audit_batch(
            make_batch(make_entry(audit_value=malformed))
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditBatchStatus.BLOCKED,
        )
        self.assertEqual(result.blocked_count, 1)
        self.assertEqual(result.audits_by_symbol, {})

    def test_duplicate_mapping_or_input_symbols_block_whole_batch(self):
        cases = (
            make_batch(
                make_entry(),
                make_entry(),
            ),
            make_batch(
                make_entry(),
                make_entry(
                    SECOND_SYMBOL,
                    audit_value=make_audit_input(),
                ),
            ),
        )
        for input_value in cases:
            with self.subTest(input_value=input_value):
                result = build_leader_research_readiness_audit_batch(
                    input_value
                )

                self.assertEqual(
                    result.status,
                    LeaderResearchReadinessAuditBatchStatus.BLOCKED,
                )
                self.assertEqual(
                    result.reasons,
                    (
                        "leader_research_readiness_"
                        "audit_batch_duplicate_symbol",
                    ),
                )
                self.assertEqual(result.items, ())

    def test_mapping_identity_and_as_of_mismatch_only_block_one_item(self):
        cases = (
            (
                make_entry(
                    SECOND_SYMBOL,
                    audit_value=make_audit_input(THIRD_SYMBOL),
                ),
                (
                    "leader_research_readiness_"
                    "audit_batch_mapping_identity_mismatch"
                ),
            ),
            (
                make_entry(
                    SECOND_SYMBOL,
                    audit_value=make_audit_input(
                        SECOND_SYMBOL,
                        as_of=AS_OF + timedelta(minutes=1),
                    ),
                ),
                (
                    "leader_research_readiness_"
                    "audit_batch_as_of_mismatch"
                ),
            ),
        )
        for second_entry, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_leader_research_readiness_audit_batch(
                    make_batch(make_entry(), second_entry)
                )

                self.assertEqual(
                    result.status,
                    LeaderResearchReadinessAuditBatchStatus.PARTIAL,
                )
                self.assertEqual(
                    result.items[1].status,
                    LeaderResearchReadinessAuditStatus.BLOCKED,
                )
                self.assertEqual(
                    result.items[1].reasons,
                    (expected_reason,),
                )
                self.assertEqual(
                    tuple(result.audits_by_symbol),
                    ("000725",),
                )

    def test_non_audit_input_is_a_candidate_local_contract_failure(self):
        result = build_leader_research_readiness_audit_batch(
            make_batch(
                make_entry(),
                make_entry(SECOND_SYMBOL, audit_value={"symbol": SECOND_SYMBOL}),
            )
        )

        self.assertEqual(
            result.status,
            LeaderResearchReadinessAuditBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.items[1].reasons,
            (
                "leader_research_readiness_"
                "audit_batch_item_contract_unverified",
            ),
        )

    def test_unexpected_audit_exception_is_isolated_and_redacted(self):
        real_builder = build_leader_research_readiness_audit

        def build_or_raise(input_value):
            if input_value.symbol == SECOND_SYMBOL:
                raise RuntimeError("secret upstream detail")
            return real_builder(input_value)

        with patch(
            "radar.leader_research_readiness_audit_batch."
            "build_leader_research_readiness_audit",
            side_effect=build_or_raise,
        ):
            result = build_leader_research_readiness_audit_batch(
                make_batch(
                    make_entry(),
                    make_entry(SECOND_SYMBOL),
                )
            )

        self.assertEqual(
            result.items[1].reasons,
            (
                "leader_research_readiness_"
                "audit_batch_item_audit_failed",
            ),
        )
        self.assertNotIn("secret upstream detail", repr(result))
        self.assertEqual(
            tuple(result.audits_by_symbol),
            ("000725",),
        )

    def test_forged_audit_result_is_rejected_defensively(self):
        valid = build_leader_research_readiness_audit(
            make_audit_input()
        )
        forged_results = (
            replace(valid, formal_usable=True),
            replace(valid, symbol=SECOND_SYMBOL),
            replace(valid, contract_id="forged-contract"),
            replace(valid, evidence_available_count=0),
            replace(
                valid,
                status="ready",
            ),
        )
        for forged in forged_results:
            with self.subTest(forged=forged):
                with patch(
                    "radar.leader_research_readiness_audit_batch."
                    "build_leader_research_readiness_audit",
                    return_value=forged,
                ):
                    result = (
                        build_leader_research_readiness_audit_batch(
                            make_batch(make_entry())
                        )
                    )

                self.assertEqual(
                    result.status,
                    LeaderResearchReadinessAuditBatchStatus.BLOCKED,
                )
                self.assertEqual(
                    result.items[0].reasons,
                    (
                        "leader_research_readiness_"
                        "audit_batch_result_unverified",
                    ),
                )
                self.assertEqual(result.audits_by_symbol, {})

    def test_forged_nested_item_semantics_are_rejected(self):
        valid = build_leader_research_readiness_audit(
            make_audit_input()
        )
        first = valid.items[0]
        forged_items = (
            replace(first, status=ResearchFeatureStatus.STALE),
            replace(
                first,
                evidence_available=False,
                requirement_satisfied=True,
            ),
            replace(
                first,
                requirement_satisfied=True,
                veto_reason="leader_cross_section_evidence_unavailable",
            ),
            replace(
                first,
                requirement_satisfied=False,
                veto_reason=None,
            ),
        )
        for forged_item in forged_items:
            with self.subTest(forged_item=forged_item):
                forged = replace(
                    valid,
                    items=(forged_item, *valid.items[1:]),
                )
                with patch(
                    "radar.leader_research_readiness_audit_batch."
                    "build_leader_research_readiness_audit",
                    return_value=forged,
                ):
                    result = (
                        build_leader_research_readiness_audit_batch(
                            make_batch(make_entry())
                        )
                    )

                self.assertEqual(
                    result.items[0].reasons,
                    (
                        "leader_research_readiness_"
                        "audit_batch_result_unverified",
                    ),
                )
                self.assertEqual(result.audits_by_symbol, {})

    def test_unstable_formal_reason_codes_are_rejected_and_redacted(self):
        ready = build_leader_research_readiness_audit(
            make_audit_input()
        )
        blocked = build_leader_research_readiness_audit(replace(
            make_audit_input(),
            cross_sectional_features=None,
        ))
        secret = "secret upstream exception detail"
        for source_result in (ready, blocked):
            with self.subTest(status=source_result.status):
                forged = replace(
                    source_result,
                    first_veto_reason=secret,
                    ordered_blocker_codes=(secret,),
                )
                with patch(
                    "radar.leader_research_readiness_audit_batch."
                    "build_leader_research_readiness_audit",
                    return_value=forged,
                ):
                    result = (
                        build_leader_research_readiness_audit_batch(
                            make_batch(make_entry())
                        )
                    )

                self.assertEqual(
                    result.items[0].reasons,
                    (
                        "leader_research_readiness_"
                        "audit_batch_result_unverified",
                    ),
                )
                self.assertNotIn(secret, repr(result))
                self.assertNotIn(secret, str(result.to_evidence()))

    def test_empty_naive_and_malformed_batches_fail_stably(self):
        malformed_entries = dataclasses.replace(
            make_batch(make_entry()),
            entries=[make_entry()],
        )
        cases = (
            (
                make_batch(),
                LeaderResearchReadinessAuditBatchStatus.MISSING,
                "leader_research_readiness_audit_batch_empty",
            ),
            (
                make_batch(as_of=AS_OF.replace(tzinfo=None)),
                LeaderResearchReadinessAuditBatchStatus.BLOCKED,
                (
                    "leader_research_readiness_"
                    "audit_batch_as_of_timezone_missing"
                ),
            ),
            (
                malformed_entries,
                LeaderResearchReadinessAuditBatchStatus.BLOCKED,
                (
                    "leader_research_readiness_"
                    "audit_batch_contract_unverified"
                ),
            ),
            (
                None,
                LeaderResearchReadinessAuditBatchStatus.BLOCKED,
                (
                    "leader_research_readiness_"
                    "audit_batch_contract_unverified"
                ),
            ),
        )
        for input_value, expected_status, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_leader_research_readiness_audit_batch(
                    input_value
                )

                self.assertEqual(result.status, expected_status)
                self.assertEqual(
                    result.reasons,
                    (expected_reason,),
                )
                self.assertEqual(result.audits_by_symbol, {})

    def test_contracts_are_frozen_hide_inputs_and_keep_formal_gates_closed(self):
        entry = make_entry()
        input_value = make_batch(entry)
        result = build_leader_research_readiness_audit_batch(
            input_value
        )

        with self.assertRaises(FrozenInstanceError):
            entry.symbol = SECOND_SYMBOL
        with self.assertRaises(FrozenInstanceError):
            result.status = (
                LeaderResearchReadinessAuditBatchStatus.BLOCKED
            )
        self.assertNotIn(
            "LeaderResearchFeatureResult",
            repr(input_value),
        )
        self.assertFalse(result.formal_score_ready)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        self.assertFalse(result.items[0].formal_score_ready)
        self.assertFalse(result.items[0].formal_gate_ready)
        self.assertFalse(result.items[0].formal_usable)
        self.assertFalse(result.items[0].state_transition_allowed)

        evidence = result.to_evidence()
        self.assertEqual(
            evidence["gate"],
            {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        )
        self.assertNotIn("items", evidence["auditsBySymbol"]["000725"])


if __name__ == "__main__":
    unittest.main()
