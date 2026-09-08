import importlib
import unittest
from dataclasses import replace
from datetime import timedelta

from tests import test_radar_leader_risk_official_lifecycle as lifecycle_helpers


try:
    coverage_module = importlib.import_module(
        "radar.leader_risk_official_coverage"
    )
except ModuleNotFoundError:
    coverage_module = None


class LeaderOfficialRiskCoverageReadinessTests(unittest.TestCase):
    def setUp(self):
        helper = lifecycle_helpers.LeaderOfficialRiskLifecycleTests(
            methodName=(
                "test_open_d8_event_is_carried_across_continuous_"
                "official_windows"
            )
        )
        helper.setUp()
        self.helper = helper
        self.windows = helper.windows()
        self.plan = self.windows[-1].candidate_plan
        self.lifecycle = helper.build(self.windows)

    def evidence_type(self):
        value = getattr(
            coverage_module,
            "LeaderOfficialRiskIssuerListingEvidence",
            None,
        )
        self.assertTrue(callable(value))
        return value

    def policy_type(self):
        value = getattr(
            coverage_module,
            "LeaderOfficialRiskCoveragePolicyApproval",
            None,
        )
        self.assertTrue(callable(value))
        return value

    def listing_evidence(self):
        evidence_type = self.evidence_type()
        return tuple(
            evidence_type(
                symbol=item.symbol,
                issuer_identity=item.issuer_identity,
                issuer_listed_at=self.helper.issuer_listed_at,
                source_contract_id="official_exchange_security_master",
                source_name=(
                    "深圳证券交易所"
                    if item.symbol.startswith(("0", "3"))
                    else "上海证券交易所"
                ),
                source_url=(
                    "https://www.szse.cn/market/product/stock/list/"
                    "index.html"
                    if item.symbol.startswith(("0", "3"))
                    else "https://www.sse.com.cn/assortment/stock/list/"
                    "share/"
                ),
                fetched_at=self.plan.as_of,
                record_checksum=("a" * 64),
            )
            for item in self.lifecycle.items
        )

    def build(self, *, listing_evidence=None, policy_approval=None,
              lifecycle=None, plan=None):
        builder = getattr(
            coverage_module,
            "build_leader_official_risk_coverage_readiness",
            None,
        )
        self.assertTrue(callable(builder))
        return builder(
            candidate_plan=self.plan if plan is None else plan,
            lifecycle=self.lifecycle if lifecycle is None else lifecycle,
            listing_evidence=(
                self.listing_evidence()
                if listing_evidence is None
                else listing_evidence
            ),
            policy_approval=policy_approval,
        )

    def test_complete_structural_evidence_remains_policy_unapproved(self):
        result = self.build()

        self.assertEqual(result.status.value, "policy_unapproved")
        self.assertEqual(
            result.reasons,
            (
                "risk_official_coverage_lifecycle_relationships_incomplete",
                "risk_official_coverage_policy_unapproved",
                "risk_official_keyword_discovery_not_coverage_proof",
            ),
        )
        self.assertTrue(result.listing_evidence_structurally_valid)
        self.assertFalse(result.lifecycle_relationships_complete)
        self.assertFalse(result.policy_approved)
        self.assertFalse(result.issuer_listing_evidence_approved)
        self.assertFalse(result.coverage_complete)
        self.assertFalse(result.risk_filter_passed)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)
        self.assertTrue(all(
            item.status.value == "policy_unapproved"
            and item.listing_evidence_structurally_valid
            and not item.coverage_complete
            for item in result.items
        ))
        self.assertTrue(result.items[0].lifecycle_relationships_complete)
        self.assertTrue(all(
            not item.lifecycle_relationships_complete
            for item in result.items[1:]
        ))
        evidence = result.to_evidence()
        self.assertFalse(evidence["policy"]["approved"])
        self.assertFalse(evidence["gate"]["formalGateReady"])
        self.assertNotIn("sourceUrl", str(evidence))

    def test_caller_supplied_policy_objects_cannot_self_approve(self):
        approval = self.policy_type()(
            policy_id="caller-policy",
            policy_version="v1",
            approved_by="caller",
            approved_at=self.plan.as_of,
            source_contract_ids=("official_exchange_security_master",),
            category_query_versions=tuple(
                (category, "v1")
                for category in getattr(
                    coverage_module,
                    "REQUIRED_RISK_CATEGORIES",
                )
            ),
        )

        for supplied in (
            approval,
            approval.__dict__,
            replace(approval, policy_id=[]),
        ):
            with self.subTest(supplied_type=type(supplied).__name__):
                result = self.build(policy_approval=supplied)
                self.assertEqual(result.status.value, "policy_unapproved")
                self.assertFalse(result.policy_approved)
                self.assertFalse(result.coverage_complete)

    def test_missing_listing_evidence_is_explicit_and_fail_closed(self):
        result = self.build(listing_evidence=())

        self.assertEqual(result.status.value, "missing")
        self.assertIn(
            "risk_official_coverage_listing_evidence_missing",
            result.reasons,
        )
        self.assertIn(
            "risk_official_coverage_policy_unapproved",
            result.reasons,
        )
        self.assertFalse(result.coverage_complete)

    def test_listing_evidence_identity_and_uniqueness_are_enforced(self):
        valid = self.listing_evidence()
        mutations = (
            (replace(valid[0], symbol="600000"), *valid[1:]),
            (
                replace(
                    valid[0],
                    issuer_identity="cninfo-org:wrong",
                ),
                *valid[1:],
            ),
            (valid[0], valid[0], *valid[2:]),
        )

        for listing_evidence in mutations:
            with self.subTest(listing_evidence=listing_evidence):
                result = self.build(listing_evidence=listing_evidence)
                self.assertEqual(result.status.value, "source_unverified")
                self.assertIn(
                    "risk_official_coverage_listing_identity_unverified",
                    result.reasons,
                )
                self.assertFalse(result.coverage_complete)

    def test_listing_evidence_time_source_and_checksum_are_enforced(self):
        valid = self.listing_evidence()
        mutations = (
            (
                replace(
                    valid[0],
                    issuer_listed_at=(
                        self.plan.as_of + timedelta(days=1)
                    ),
                ),
                "risk_official_coverage_listing_time_unverified",
            ),
            (
                replace(
                    valid[0],
                    fetched_at=self.plan.as_of.replace(tzinfo=None),
                ),
                "risk_official_coverage_listing_time_unverified",
            ),
            (
                replace(
                    valid[0],
                    fetched_at=self.plan.as_of + timedelta(seconds=1),
                ),
                "risk_official_coverage_listing_time_unverified",
            ),
            (
                replace(
                    valid[0],
                    source_url="http://www.szse.cn/list",
                ),
                "risk_official_coverage_listing_source_unverified",
            ),
            (
                replace(
                    valid[0],
                    source_url="https://example.com/list",
                ),
                "risk_official_coverage_listing_source_unverified",
            ),
            (
                replace(valid[0], record_checksum="not-a-sha256"),
                "risk_official_coverage_listing_checksum_unverified",
            ),
        )

        for listing_evidence, expected_reason in mutations:
            with self.subTest(listing_evidence=listing_evidence):
                result = self.build(
                    listing_evidence=(listing_evidence, *valid[1:])
                )
                self.assertEqual(result.status.value, "source_unverified")
                self.assertIn(expected_reason, result.reasons)
                self.assertFalse(
                    result.listing_evidence_structurally_valid
                )
                self.assertFalse(result.coverage_complete)

    def test_lifecycle_must_be_same_round_and_producer_verified(self):
        cases = (
            (self.lifecycle, self.windows[0].candidate_plan),
            (replace(self.lifecycle), self.plan),
            (
                self.helper.build(
                    self.helper.windows(carry_in_second=False)
                ),
                self.plan,
            ),
        )

        for lifecycle, plan in cases:
            with self.subTest(lifecycle=lifecycle, plan=plan):
                result = self.build(lifecycle=lifecycle, plan=plan)
                self.assertEqual(result.status.value, "source_unverified")
                self.assertIn(
                    "risk_official_coverage_lifecycle_unverified",
                    result.reasons,
                )
                self.assertFalse(result.coverage_complete)

    def test_result_replay_validator_rejects_replacement_and_gate_tampering(self):
        result = self.build()
        validator = getattr(
            coverage_module,
            "is_leader_official_risk_coverage_readiness_valid",
            None,
        )
        self.assertTrue(callable(validator))
        self.assertTrue(validator(result, candidate_plan=self.plan))
        self.assertFalse(
            validator(replace(result), candidate_plan=self.plan)
        )
        self.assertFalse(validator(
            replace(result, coverage_complete=True),
            candidate_plan=self.plan,
        ))
        self.assertFalse(validator(
            replace(result, formal_gate_ready=True),
            candidate_plan=self.plan,
        ))


if __name__ == "__main__":
    unittest.main()
