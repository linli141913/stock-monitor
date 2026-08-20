import unittest
from datetime import date, timedelta

from radar.leader_business_material_batch_collection import (
    collect_leader_business_material_review_queue,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceStatus,
    run_leader_business_material_live_acceptance,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceStatus,
)
from radar.sources.leader_risk_official import (
    CninfoIssuerResolutionStatus,
    CninfoIssuerRosterResolutionResult,
    CninfoRiskIssuerScope,
)
from tests.test_radar_leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceTests,
)


class LeaderBusinessMaterialLiveAcceptanceTests(unittest.TestCase):
    def setUp(self):
        helper = LeaderTradabilityLiveAcceptanceTests(
            methodName=(
                "test_complete_scope_rebases_once_and_enters_production_collector"
            )
        )
        helper.setUp()
        self.tradability = helper.run_acceptance()
        self.window_from = date(2025, 1, 1)

    def issuer_result(self, symbols):
        return CninfoIssuerRosterResolutionResult(
            status=CninfoIssuerResolutionStatus.READY,
            fetched_at=self.tradability.as_of,
            scopes=tuple(
                CninfoRiskIssuerScope(
                    symbol=symbol,
                    issuer_identity=f"cninfo-org:org-{symbol}",
                    resolved_at=self.tradability.as_of,
                )
                for symbol in symbols
            ),
        )

    def payload(self, query):
        symbol = query.scope.symbol
        return {
            "totalAnnouncement": 1,
            "totalpages": 1,
            "hasMore": False,
            "announcements": [{
                "secCode": symbol,
                "orgId": f"org-{symbol}",
                "announcementId": f"report-{symbol}",
                "announcementTitle": "2025年年度报告",
                "announcementTime": int(
                    (self.tradability.as_of - timedelta(days=30)).timestamp()
                    * 1000
                ),
                "adjunctUrl": (
                    "finalpage/2026-07-01/"
                    f"report-{symbol}.PDF"
                ),
                "adjunctType": "PDF",
            }],
        }

    def test_complete_candidate_universe_enters_pending_human_review(self):
        issuer_calls = []
        material_calls = []

        def issuer_loader(symbols, *, fetched_at):
            issuer_calls.append((symbols, fetched_at))
            return self.issuer_result(symbols)

        def material_collector(plan, *, issuer_scopes, window_from):
            material_calls.append((plan, issuer_scopes, window_from))
            return collect_leader_business_material_review_queue(
                plan,
                issuer_scopes=issuer_scopes,
                window_from=window_from,
                transport=self.payload,
                clock=lambda: self.tradability.as_of,
                max_workers=2,
            )

        result = run_leader_business_material_live_acceptance(
            self.tradability,
            window_from=self.window_from,
            issuer_scope_loader=issuer_loader,
            material_collector=material_collector,
        )

        symbols = tuple(
            item.symbol for item in self.tradability.candidate_plan.items
        )
        self.assertEqual(
            result.status,
            LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED,
        )
        self.assertEqual(issuer_calls, [(symbols, self.tradability.as_of)])
        self.assertEqual(len(material_calls), 1)
        self.assertEqual(result.review_summary["pendingReview"], len(symbols))
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

        evidence = result.to_evidence()
        self.assertNotIn(symbols[0], repr(evidence))
        self.assertNotIn("items", evidence)
        self.assertEqual(evidence["candidateCount"], len(symbols))
        self.assertFalse(evidence["gate"]["formalGateReady"])

    def test_unready_tradability_does_not_call_cninfo(self):
        calls = []
        tradability = self.tradability.__class__(
            status=LeaderTradabilityLiveAcceptanceStatus.NOT_READY,
            radar_run_id=self.tradability.radar_run_id,
            as_of=self.tradability.as_of,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("candidate_collection_not_ready",),
        )

        result = run_leader_business_material_live_acceptance(
            tradability,
            window_from=self.window_from,
            issuer_scope_loader=lambda *args, **kwargs: calls.append(args),
            material_collector=lambda *args, **kwargs: calls.append(args),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialLiveAcceptanceStatus.NOT_READY,
        )
        self.assertEqual(calls, [])

    def test_issuer_failure_does_not_collect_materials(self):
        material_calls = []

        result = run_leader_business_material_live_acceptance(
            self.tradability,
            window_from=self.window_from,
            issuer_scope_loader=lambda symbols, *, fetched_at: (
                CninfoIssuerRosterResolutionResult(
                    status=CninfoIssuerResolutionStatus.SOURCE_FAILED,
                    fetched_at=fetched_at,
                    reasons=("cninfo_issuer_roster_source_request_failed",),
                )
            ),
            material_collector=lambda *args, **kwargs: material_calls.append(args),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED,
        )
        self.assertEqual(material_calls, [])
        self.assertIn(
            "cninfo_issuer_roster_source_request_failed",
            result.reasons,
        )

    def test_one_material_source_failure_closes_acceptance(self):
        failed_symbol = self.tradability.candidate_plan.items[0].symbol

        def transport(query):
            if query.scope.symbol == failed_symbol:
                raise RuntimeError("source unavailable")
            return self.payload(query)

        def collector(plan, *, issuer_scopes, window_from):
            return collect_leader_business_material_review_queue(
                plan,
                issuer_scopes=issuer_scopes,
                window_from=window_from,
                transport=transport,
                clock=lambda: self.tradability.as_of,
                max_workers=2,
            )

        result = run_leader_business_material_live_acceptance(
            self.tradability,
            window_from=self.window_from,
            issuer_scope_loader=lambda symbols, *, fetched_at: (
                self.issuer_result(symbols)
            ),
            material_collector=collector,
        )

        self.assertEqual(
            result.status,
            LeaderBusinessMaterialLiveAcceptanceStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.review_summary["sourceFailed"], 1)
        self.assertFalse(result.formal_gate_ready)


if __name__ == "__main__":
    unittest.main()
