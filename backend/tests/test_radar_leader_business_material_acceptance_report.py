import unittest
from dataclasses import replace
from datetime import date

from radar.leader_business_material_acceptance_report import (
    render_leader_business_material_acceptance_html,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceResult,
    LeaderBusinessMaterialLiveAcceptanceStatus,
    run_leader_business_material_live_acceptance,
)
from radar.leader_business_material_batch_collection import (
    collect_leader_business_material_review_queue,
)
from radar.sources.leader_risk_official import (
    CninfoIssuerResolutionStatus,
    CninfoIssuerRosterResolutionResult,
)
from tests.test_radar_leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceTests,
)


class LeaderBusinessMaterialAcceptanceReportTests(unittest.TestCase):
    def setUp(self):
        helper = LeaderBusinessMaterialLiveAcceptanceTests(
            methodName=(
                "test_complete_candidate_universe_enters_pending_human_review"
            )
        )
        helper.setUp()
        self.helper = helper

    def completed_result(self):
        def issuer_loader(symbols, *, fetched_at):
            result = self.helper.issuer_result(symbols)
            return CninfoIssuerRosterResolutionResult(
                status=CninfoIssuerResolutionStatus.READY,
                fetched_at=fetched_at,
                scopes=result.scopes,
            )

        def collector(plan, *, issuer_scopes, window_from):
            return collect_leader_business_material_review_queue(
                plan,
                issuer_scopes=issuer_scopes,
                window_from=window_from,
                transport=self.helper.payload,
                clock=lambda: self.helper.tradability.as_of,
                max_workers=2,
            )

        return run_leader_business_material_live_acceptance(
            self.helper.tradability,
            window_from=date(2025, 1, 1),
            issuer_scope_loader=issuer_loader,
            material_collector=collector,
        )

    def test_report_shows_batch_counts_documents_and_gate_boundary(self):
        result = self.completed_result()

        html = render_leader_business_material_acceptance_html(
            result,
            review_template_href="acceptance-review.json",
            source_packet_href="acceptance-source.json",
        )

        self.assertIn("阶段6 · 主营证据验收台", html)
        self.assertIn("待人工复核", html)
        self.assertIn(
            str(result.review_summary["pendingReview"]),
            html,
        )
        self.assertIn(result.review_queue.items[0].symbol, html)
        self.assertIn("2025年年度报告", html)
        self.assertIn("https://static.cninfo.com.cn/", html)
        self.assertIn("人工复核前不进入正式门", html)
        self.assertIn("不构成投资建议", html)
        self.assertIn("formalGateReady · false", html)
        self.assertIn('href="acceptance-review.json"', html)
        self.assertIn('href="acceptance-source.json"', html)
        self.assertIn("下载人工复核文件", html)
        self.assertIn("来源清单只读保留", html)
        self.assertIn("import_leader_business_material_review.py", html)
        self.assertIn(
            "acceptance-source.json acceptance-review.json",
            html,
        )

    def test_report_escapes_untrusted_document_text(self):
        result = self.completed_result()
        queue = result.review_queue
        first = queue.items[0]
        document = replace(
            first.documents[0],
            title='<script>alert("x")</script>',
        )
        changed_item = replace(first, documents=(document,))
        changed_queue = replace(
            queue,
            items=(changed_item, *queue.items[1:]),
        )
        changed_result = replace(result, review_queue=changed_queue)

        html = render_leader_business_material_acceptance_html(changed_result)

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_not_ready_report_is_explicit_and_has_no_fake_rows(self):
        result = LeaderBusinessMaterialLiveAcceptanceResult(
            status=LeaderBusinessMaterialLiveAcceptanceStatus.NOT_READY,
            radar_run_id="run-not-ready",
            as_of=None,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("tradability_acceptance_not_completed",),
        )

        html = render_leader_business_material_acceptance_html(result)

        self.assertIn("本轮未就绪", html)
        self.assertIn("未生成任何候选或材料替代数据", html)
        self.assertNotIn("data-candidate-row", html)
        self.assertIn("tradability_acceptance_not_completed", html)


if __name__ == "__main__":
    unittest.main()
