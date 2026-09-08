import unittest
from datetime import datetime, timedelta, timezone

from radar.leader_business_annual_report_selector import (
    LeaderBusinessAnnualReportSelectionStatus,
    select_latest_official_annual_report,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueueItem,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlanItem,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 21, 2, 0, tzinfo=UTC)


def plan_item(**changes):
    values = {
        "index": 0,
        "symbol": "000001",
        "as_of": AS_OF,
        "industry_code": "65",
        "industry_name": "软件和信息技术服务业",
        "industry_release_id": "industry-release-2026h1",
        "within_industry_rank": 1,
        "quote_source_contract_id": "quote-v1",
        "sector_source_contract_id": "sector-v1",
    }
    values.update(changes)
    return LeaderRuntimeCandidatePlanItem(**values)


def document(
    title,
    suffix,
    *,
    published_at=None,
    symbol="000001",
):
    actual_published_at = published_at or AS_OF - timedelta(days=30)
    return OfficialBusinessMaterialDocument(
        document_id=f"cninfo:{suffix}",
        document_version=f"cninfo:{suffix}:{int(actual_published_at.timestamp())}",
        symbol=symbol,
        issuer_identity="cninfo-org:9900000001",
        title=title,
        published_at=actual_published_at,
        source_url=(
            "https://static.cninfo.com.cn/finalpage/"
            f"2026-04-30/{suffix}.PDF"
        ),
    )


def queue_item(*documents, symbol="000001", status=None):
    return LeaderBusinessMaterialReviewQueueItem(
        index=0,
        symbol=symbol,
        industry_code="65",
        industry_release_id="industry-release-2026h1",
        status=status or LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW,
        documents=tuple(documents),
    )


class LeaderBusinessAnnualReportSelectorTests(unittest.TestCase):
    def test_latest_full_chinese_report_wins_over_summary_and_english(self):
        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document("2025年年度报告摘要", "1001"),
                document("2025年年度报告（英文版）", "1002"),
                document("2024年年度报告", "1003"),
                document("2025年年度报告", "1004"),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.READY,
        )
        self.assertEqual(result.report_year, 2025)
        self.assertEqual(result.document.document_id, "cninfo:1004")
        self.assertEqual(result.replaced_document_ids, ())
        self.assertFalse(result.formal_gate_ready)

    def test_unique_latest_revision_replaces_original(self):
        original_time = AS_OF - timedelta(days=40)
        revision_time = AS_OF - timedelta(days=20)

        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document(
                    "2025年年度报告",
                    "2001",
                    published_at=original_time,
                ),
                document(
                    "2025年年度报告（修订版）",
                    "2002",
                    published_at=revision_time,
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.READY,
        )
        self.assertEqual(result.document.document_id, "cninfo:2002")
        self.assertEqual(result.replaced_document_ids, ("cninfo:2001",))

    def test_cninfo_full_report_title_suffix_is_accepted(self):
        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document("公司2025年年度报告摘要", "2101"),
                document("公司2025年年度报告全文", "2102"),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.READY,
        )
        self.assertEqual(result.report_year, 2025)
        self.assertEqual(result.document.document_id, "cninfo:2102")

    def test_cninfo_repeated_annual_word_full_report_is_accepted(self):
        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document("亚翔集成2024年度年度报告全文", "2201"),
                document("亚翔集成-公司2024年度报告全文及摘要", "2202"),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.READY,
        )
        self.assertEqual(result.report_year, 2024)
        self.assertEqual(result.document.document_id, "cninfo:2201")

    def test_same_time_full_reports_are_ambiguous(self):
        published_at = AS_OF - timedelta(days=20)

        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document(
                    "2025年年度报告",
                    "3001",
                    published_at=published_at,
                ),
                document(
                    "2025年年度报告",
                    "3002",
                    published_at=published_at,
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.document)
        self.assertEqual(
            result.reasons,
            ("annual_report_selection_ambiguous",),
        )

    def test_inquiry_reply_mentioning_annual_report_is_not_a_full_report(self):
        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document(
                    "关于2025年年度报告的信息披露问询函回复公告",
                    "3501",
                    published_at=AS_OF - timedelta(days=5),
                ),
                document(
                    "某公司2025年年度报告",
                    "3502",
                    published_at=AS_OF - timedelta(days=30),
                ),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.READY,
        )
        self.assertEqual(result.document.document_id, "cninfo:3502")

    def test_non_ready_queue_and_no_full_report_preserve_missing_semantics(self):
        source_failed = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                status=LeaderBusinessMaterialReviewItemStatus.SOURCE_FAILED,
            ),
        )
        missing = select_latest_official_annual_report(
            plan_item(),
            queue_item(document("2025年年度报告摘要", "4001")),
        )

        self.assertEqual(
            source_failed.status,
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_FAILED,
        )
        self.assertEqual(
            missing.status,
            LeaderBusinessAnnualReportSelectionStatus.MISSING,
        )
        self.assertEqual(missing.reasons, ("annual_report_missing",))

    def test_candidate_identity_drift_is_rejected(self):
        result = select_latest_official_annual_report(
            plan_item(),
            queue_item(
                document("2025年年度报告", "5001", symbol="000002"),
            ),
        )

        self.assertEqual(
            result.status,
            LeaderBusinessAnnualReportSelectionStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(
            result.reasons,
            ("annual_report_scope_unverified",),
        )


if __name__ == "__main__":
    unittest.main()
