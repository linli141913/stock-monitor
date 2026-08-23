import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_automatic_evidence import (
    LeaderBusinessAutomaticEvidenceSources,
    run_leader_business_automatic_evidence,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
    LeaderBusinessMaterialReviewQueue,
    LeaderBusinessMaterialReviewQueueItem,
    LeaderBusinessMaterialReviewQueueStatus,
)
from radar.leader_business_material_review_submission import (
    build_leader_business_material_review_source_packet,
)
from radar.leader_runtime_candidate_plan import (
    LeaderRuntimeCandidatePlan,
    LeaderRuntimeCandidatePlanItem,
    LeaderRuntimeCandidatePlanStatus,
    _candidate_set_id,
)
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystDiscoveryResult,
    OfficialBusinessCatalystDocument,
    OfficialBusinessCatalystKind,
)
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    OfficialBusinessDocumentPage,
)
from radar.sources.leader_business_official import (
    OfficialBusinessMaterialDocument,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 21, 4, 0, tzinfo=UTC)
VALIDATED_AT = AS_OF + timedelta(hours=1)


def source_packet(count):
    radar_run_id = "run-auto-evidence"
    quote_batch_id = "quote-auto-evidence"
    market_contract = "market-auto-evidence-v1"
    items = tuple(
        LeaderRuntimeCandidatePlanItem(
            index=index,
            symbol=f"{index + 1:06d}",
            as_of=AS_OF,
            industry_code=f"{index // 5 + 1:02d}",
            industry_name=f"行业{index // 5 + 1:02d}",
            industry_release_id="industry-release-auto-v1",
            within_industry_rank=index % 5 + 1,
            quote_source_contract_id=(
                f"tencent-full-market-quote-v1:{quote_batch_id}"
            ),
            sector_source_contract_id="sector-auto-v1",
        )
        for index in range(count)
    )
    candidate_set_id = _candidate_set_id(
        radar_run_id=radar_run_id,
        quote_batch_id=quote_batch_id,
        as_of=AS_OF,
        items=items,
        market_source_contract_id=market_contract,
    )
    plan = LeaderRuntimeCandidatePlan(
        status=LeaderRuntimeCandidatePlanStatus.READY,
        as_of=AS_OF,
        radar_run_id=radar_run_id,
        quote_batch_id=quote_batch_id,
        market_source_contract_id=market_contract,
        items=items,
        scanned_count=count,
        mapped_count=count,
        candidate_set_id=candidate_set_id,
    )
    queue = LeaderBusinessMaterialReviewQueue(
        status=LeaderBusinessMaterialReviewQueueStatus.PENDING_REVIEW,
        candidate_plan_id=candidate_set_id,
        candidate_count=count,
        items=tuple(
            LeaderBusinessMaterialReviewQueueItem(
                index=item.index,
                symbol=item.symbol,
                industry_code=item.industry_code,
                industry_release_id=item.industry_release_id,
                status=LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW,
                documents=(OfficialBusinessMaterialDocument(
                    document_id=f"cninfo:{1_000_000_000 + item.index}",
                    document_version=f"annual:{item.index}:v1",
                    symbol=item.symbol,
                    issuer_identity=f"cninfo-org:{9_000_000_000 + item.index}",
                    title="2025年年度报告",
                    published_at=AS_OF - timedelta(days=100),
                    source_url=(
                        "https://static.cninfo.com.cn/finalpage/2026-04-30/"
                        f"{1_000_000_000 + item.index}.PDF"
                    ),
                ),),
            )
            for item in items
        ),
    )
    return build_leader_business_material_review_source_packet(plan, queue)


def ready_sources(*, missing_index=None, calls=None):
    def discover(query, *, fetched_at):
        index = int(query.symbol) - 1
        if calls is not None:
            calls.append(("discover", index))
        if index == missing_index:
            return OfficialBusinessCatalystDiscoveryResult(
                status=AutomaticBusinessEvidenceStatus.MISSING,
                query=query,
                fetched_at=fetched_at,
                reasons=("business_catalyst_missing",),
            )
        identifier = 2_000_000_000 + index
        return OfficialBusinessCatalystDiscoveryResult(
            status=AutomaticBusinessEvidenceStatus.READY,
            query=query,
            fetched_at=fetched_at,
            documents=(OfficialBusinessCatalystDocument(
                document_id=f"cninfo:{identifier}",
                document_version=f"catalyst:{index}:v1",
                symbol=query.symbol,
                issuer_identity=query.issuer_identity,
                title="关于签订重大合同的公告",
                published_at=AS_OF - timedelta(days=2),
                source_url=(
                    "https://static.cninfo.com.cn/finalpage/2026-08-19/"
                    f"{identifier}.PDF"
                ),
                event_kind=OfficialBusinessCatalystKind.MAJOR_CONTRACT,
            ),),
        )

    def content(document, *, kind, fetched_at):
        if calls is not None:
            calls.append(("content", document.document_id))
        if kind is OfficialBusinessDocumentKind.ANNUAL_REPORT:
            text = (
                f"证券代码{document.symbol} 所属行业"
                f"行业{(int(document.symbol) - 1) // 5 + 1:02d}\n"
                "主营业务 主要产品包括工业软件。"
            )
        else:
            text = "公司签订工业软件项目合同。"
        digest = hashlib.sha256(
            f"{document.document_id}:{text}".encode("utf-8")
        ).hexdigest()
        return OfficialBusinessDocumentContentResult(
            status=AutomaticBusinessEvidenceStatus.READY,
            document_id=document.document_id,
            document_version=document.document_version,
            symbol=document.symbol,
            issuer_identity=document.issuer_identity,
            document_kind=kind,
            content_sha256=digest,
            byte_count=100,
            page_count=1,
            pages=(OfficialBusinessDocumentPage(1, text),),
            fetched_at=fetched_at,
        )

    return LeaderBusinessAutomaticEvidenceSources(
        discover_catalysts=discover,
        fetch_document_content=content,
    )


def object_missing_sources():
    base = ready_sources()

    def content(document, *, kind, fetched_at):
        result = base.fetch_document_content(
            document,
            kind=kind,
            fetched_at=fetched_at,
        )
        if kind is OfficialBusinessDocumentKind.CATALYST:
            text = (
                "三、业绩变动原因说明。"
                "公司整体营业收入增长，主营产品销量提升。"
            )
            return replace(
                result,
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                pages=(OfficialBusinessDocumentPage(1, text),),
            )
        return result

    return LeaderBusinessAutomaticEvidenceSources(
        discover_catalysts=base.discover_catalysts,
        fetch_document_content=content,
    )


class LeaderBusinessAutomaticEvidenceTests(unittest.TestCase):
    def run_batch(self, packet, directory, *, sources=None):
        return run_leader_business_automatic_evidence(
            packet,
            artifact_dir=Path(directory),
            sources=sources or ready_sources(),
            clock=lambda: VALIDATED_AT,
        )

    def test_complete_385_batch_restores_order_and_exports_delivery_packet(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.run_batch(source_packet(385), directory)

            self.assertEqual(
                result.status,
                AutomaticBusinessEvidenceStatus.READY,
            )
            self.assertEqual(result.candidate_count, 385)
            self.assertEqual(result.ready_count, 385)
            self.assertEqual(
                tuple(item.index for item in result.items),
                tuple(range(385)),
            )
            self.assertTrue(result.packet_path.is_file())
            self.assertTrue(result.delivery_packet_path.is_file())
            self.assertFalse(result.formal_gate_ready)
            self.assertEqual(
                tuple(Path(directory).rglob("*.pdf")),
                (),
            )

    def test_one_missing_candidate_keeps_batch_unverified_without_delivery(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.run_batch(
                source_packet(12),
                directory,
                sources=ready_sources(missing_index=7),
            )

            self.assertEqual(
                result.status,
                AutomaticBusinessEvidenceStatus.MISSING,
            )
            self.assertEqual(result.ready_count, 11)
            self.assertEqual(result.missing_count, 1)
            self.assertIsNone(result.delivery_packet_path)
            self.assertTrue(result.packet_path.is_file())

    def test_explicit_gap_diagnostic_records_fresh_bounded_object_missing_corpus(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = run_leader_business_automatic_evidence(
                source_packet(1),
                artifact_dir=Path(directory),
                sources=object_missing_sources(),
                clock=lambda: VALIDATED_AT,
                write_gap_diagnostic=True,
            )

            self.assertEqual(
                result.items[0].reasons,
                ("business_catalyst_fact_object_missing",),
            )
            self.assertTrue(result.gap_diagnostic_path.is_file())
            payload = __import__("json").loads(
                result.gap_diagnostic_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload["contractId"],
                "radar-leader-business-automatic-gap-diagnostic-v1",
            )
            self.assertTrue(payload["diagnosticOnly"])
            self.assertEqual(payload["targetReason"], "business_catalyst_fact_object_missing")
            self.assertEqual(payload["candidateCount"], 1)
            self.assertEqual(payload["items"][0]["annualTerms"], ["工业软件"])
            self.assertEqual(
                payload["items"][0]["catalysts"][0]["contentSha256"],
                hashlib.sha256(
                    "三、业绩变动原因说明。公司整体营业收入增长，主营产品销量提升。".encode(
                        "utf-8"
                    )
                ).hexdigest(),
            )
            self.assertEqual(
                payload["items"][0]["catalysts"][0]["snippets"],
                [{
                    "pageNumber": 1,
                    "text": "公司整体营业收入增长，主营产品销量提升",
                }],
            )
            self.assertFalse(payload["gate"]["formalGateReady"])

    def test_gap_diagnostic_is_not_written_by_default(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.run_batch(
                source_packet(1),
                directory,
                sources=object_missing_sources(),
            )

            self.assertIsNone(result.gap_diagnostic_path)
            self.assertEqual(tuple(Path(directory).glob("gap-diagnostic-*.json")), ())

    def test_generic_catalyst_without_object_does_not_hide_explicit_document(self):
        base = ready_sources()

        def discover(query, *, fetched_at):
            result = base.discover_catalysts(query, fetched_at=fetched_at)
            generic = OfficialBusinessCatalystDocument(
                document_id="cninfo:2999999999",
                document_version="generic-forecast-v1",
                symbol=query.symbol,
                issuer_identity=query.issuer_identity,
                title="2025年度业绩预告",
                published_at=AS_OF - timedelta(days=1),
                source_url=(
                    "https://static.cninfo.com.cn/finalpage/2026-08-20/"
                    "2999999999.PDF"
                ),
                event_kind=OfficialBusinessCatalystKind.EARNINGS_FORECAST,
            )
            return replace(result, documents=(*result.documents, generic))

        def content(document, *, kind, fetched_at):
            if document.document_id != "cninfo:2999999999":
                return base.fetch_document_content(
                    document,
                    kind=kind,
                    fetched_at=fetched_at,
                )
            text = "公司预计本期净利润同比增长。"
            return OfficialBusinessDocumentContentResult(
                status=AutomaticBusinessEvidenceStatus.READY,
                document_id=document.document_id,
                document_version=document.document_version,
                symbol=document.symbol,
                issuer_identity=document.issuer_identity,
                document_kind=kind,
                content_sha256="c" * 64,
                byte_count=100,
                page_count=1,
                pages=(OfficialBusinessDocumentPage(1, text),),
                fetched_at=fetched_at,
            )

        sources = LeaderBusinessAutomaticEvidenceSources(
            discover_catalysts=discover,
            fetch_document_content=content,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.run_batch(
                source_packet(1),
                directory,
                sources=sources,
            )

            self.assertEqual(result.status, AutomaticBusinessEvidenceStatus.READY)

    def test_catalyst_content_failure_preserves_the_real_source_reason(self):
        base = ready_sources()

        def content(document, *, kind, fetched_at):
            if kind is OfficialBusinessDocumentKind.ANNUAL_REPORT:
                return base.fetch_document_content(
                    document,
                    kind=kind,
                    fetched_at=fetched_at,
                )
            return OfficialBusinessDocumentContentResult(
                status=AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
                document_id=document.document_id,
                document_version=document.document_version,
                symbol=document.symbol,
                issuer_identity=document.issuer_identity,
                document_kind=kind,
                fetched_at=fetched_at,
                reasons=("business_document_content_text_missing",),
            )

        sources = LeaderBusinessAutomaticEvidenceSources(
            discover_catalysts=base.discover_catalysts,
            fetch_document_content=content,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = self.run_batch(
                source_packet(1),
                directory,
                sources=sources,
            )

            self.assertEqual(
                result.status,
                AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            )
            self.assertEqual(
                result.items[0].reasons,
                ("business_document_content_text_missing",),
            )

    def test_checkpoint_reuse_requires_exact_document_identity(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            packet = source_packet(2)
            calls = []
            first = self.run_batch(
                packet,
                directory,
                sources=ready_sources(calls=calls),
            )
            initial_call_count = len(calls)
            second = self.run_batch(
                packet,
                directory,
                sources=ready_sources(calls=calls),
            )
            changed = source_packet(2)
            changed["payload"]["reviewQueue"]["items"][0]["documents"][0][
                "documentVersion"
            ] = "annual:0:v2"
            payload = changed["payload"]
            changed["packetSha256"] = hashlib.sha256(__import__(
                "json"
            ).dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            third = self.run_batch(
                changed,
                directory,
                sources=ready_sources(calls=calls),
            )

            self.assertEqual(first.reused_count, 0)
            self.assertEqual(second.reused_count, 2)
            self.assertEqual(len(calls), initial_call_count + 6)
            self.assertEqual(third.reused_count, 1)

    def test_checkpoint_reuse_rejects_a_changed_rule_version(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            packet = source_packet(1)
            calls = []
            first = self.run_batch(
                packet,
                directory,
                sources=ready_sources(calls=calls),
            )
            initial_call_count = len(calls)

            with (
                patch(
                    "radar.leader_business_automatic_evidence."
                    "DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION",
                    "radar-leader-business-deterministic-relation-v24",
                ),
                patch(
                    "radar.leader_business_deterministic_verification."
                    "DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION",
                    "radar-leader-business-deterministic-relation-v24",
                ),
            ):
                second = self.run_batch(
                    packet,
                    directory,
                    sources=ready_sources(calls=calls),
                )

            self.assertEqual(first.reused_count, 0)
            self.assertEqual(second.reused_count, 0)
            self.assertEqual(len(calls), initial_call_count + 3)
            self.assertNotEqual(
                first.items[0].checkpoint_path,
                second.items[0].checkpoint_path,
            )
            self.assertIsNotNone(second.items[0].artifact)
            self.assertEqual(
                second.items[0].artifact.rule_version,
                "radar-leader-business-deterministic-relation-v24",
            )


if __name__ == "__main__":
    unittest.main()
