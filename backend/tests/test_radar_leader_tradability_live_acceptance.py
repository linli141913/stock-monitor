import unittest
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pandas as pd
import run_leader_tradability_live_acceptance as cli

from radar.leader_formal_research_production_provider import (
    LeaderFormalResearchProductionSourceStatus,
)
from radar.leader_tradability_features import (
    PriceLimitMode,
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceSources,
    LeaderTradabilityLiveAcceptanceStatus,
    run_leader_tradability_live_acceptance,
)
from radar.leader_tradability_sources import PriceLimitSpecialSession
from radar.sources.leader_tradability_exchange_official import (
    ExchangeOfficialObservationBatch,
)
from radar.sources.leader_tradability_public_live_poc import (
    PublicCalendarDocument,
)
from radar.sources.leader_tradability_public_poc import (
    PublicSourceKind,
    PublicTradabilityObservation,
)
from tests import test_radar_leader_live_candidate_collection_batch as helpers


EVIDENCE_COMPLETED_AT = helpers.COLLECTION_COMPLETED_AT + timedelta(seconds=2)


class LeaderTradabilityLiveAcceptanceTests(unittest.TestCase):
    def setUp(self):
        helper = helpers.LeaderLiveCandidateCollectionBatchTests(
            methodName=(
                "test_ready_batch_freezes_completion_time_and_hides_raw_payload"
            )
        )
        self.helper = helper

    def candidate_sources(self):
        sources = self.helper.sources()

        def quotes(symbols, run_id, batch_id, as_of):
            batch = sources.quote_loader(symbols, run_id, batch_id, as_of)
            return batch.model_copy(update={
                "items": [
                    item.model_copy(update={
                        "previous_close": 10.0,
                        "open_price": 10.0,
                        "high_price": 10.2,
                        "low_price": 9.9,
                        "upper_limit_price_source": 11.0,
                        "lower_limit_price_source": 9.0,
                    })
                    for item in batch.items
                ]
            })

        return sources.__class__(
            security_master_loader=sources.security_master_loader,
            classification_loader=sources.classification_loader,
            quote_loader=quotes,
            index_loader=sources.index_loader,
            classification_release_loader=(
                sources.classification_release_loader
            ),
        )

    def official_batch(self, contexts, trading_date):
        def observation(context):
            is_sse = context.exchange == "sse"
            values = {
                "symbol": context.symbol,
                "exchange": context.exchange,
                "board": context.board,
                "trading_date": trading_date,
                "source_kind": PublicSourceKind.EXCHANGE_OFFICIAL,
                "source_contract_id": (
                    "sse-public-status-v1"
                    if is_sse
                    else "szse-public-status-v1"
                ),
                "source_name": (
                    "上海证券交易所" if is_sse else "深圳证券交易所"
                ),
                "source_url": (
                    "https://yunhq.sse.com.cn:32042/v1/sh1/snap/"
                    f"{context.symbol}"
                    if is_sse
                    else "https://www.szse.cn/api/market/ssjjhq/getTimeData"
                ),
                "document_id": (
                    f"{context.exchange}-status-{context.symbol}"
                ),
                "source_time": (
                    EVIDENCE_COMPLETED_AT - timedelta(seconds=1)
                ),
                "fetched_at": EVIDENCE_COMPLETED_AT,
                "content_sha256": "sha256:" + "a" * 64,
                "effective_from": trading_date,
                "effective_until": trading_date,
                "lifecycle_status": SecurityLifecycleStatus.NORMAL,
                "trading_status": TradingSessionStatus.TRADING,
                "special_session": PriceLimitSpecialSession.NONE,
            }
            if is_sse:
                values.update({
                    "price_limit_mode": PriceLimitMode.BOUNDED,
                    "upper_limit_price": 11.0,
                    "lower_limit_price": 9.0,
                })
            return PublicTradabilityObservation(**values)

        observations = tuple(
            observation(context)
            for context in contexts
        )
        return ExchangeOfficialObservationBatch(
            status="completed",
            observations=observations,
            source_time=max(item.source_time for item in observations),
            fetched_at=max(item.fetched_at for item in observations),
        )

    def lifecycle_frame(self, contexts):
        return pd.DataFrame([
            {
                "代码": context.symbol,
                "名称": f"证券{context.symbol}",
                "上游时间": EVIDENCE_COMPLETED_AT - timedelta(seconds=1),
                "抓取时间": EVIDENCE_COMPLETED_AT,
                "状态代码": "00",
            }
            for context in contexts
        ])

    def calendar_document(self, as_of):
        return PublicCalendarDocument(
            source_url="https://www.sse.com.cn/services/tradingservice/",
            document_id="sse-a-share-calendar-2026",
            text="""
                <strong>2026年休市安排</strong><table>
                <tr><td>元旦：1月1日至1月3日休市</td></tr>
                <tr><td>春节：2月15日至2月23日休市</td></tr>
                <tr><td>清明节：4月4日至4月6日休市</td></tr>
                <tr><td>劳动节：5月1日至5月5日休市</td></tr>
                <tr><td>端午节：6月19日至6月21日休市</td></tr>
                <tr><td>中秋节：9月25日至9月27日休市</td></tr>
                <tr><td>国庆节：10月1日至10月7日休市</td></tr>
                </table>
            """,
            source_time=datetime(
                2025,
                12,
                30,
                tzinfo=helpers.SHANGHAI_TZ,
            ),
            fetched_at=EVIDENCE_COMPLETED_AT,
        )

    def evidence_sources(self, **changes):
        values = {
            "official_loader": self.official_batch,
            "lifecycle_loader": self.lifecycle_frame,
            "calendar_loader": self.calendar_document,
        }
        values.update(changes)
        return LeaderTradabilityLiveAcceptanceSources(**values)

    def run_acceptance(self, *, evidence_sources=None):
        clock_values = iter((helpers.DISCOVERY_AS_OF, helpers.COLLECTION_AS_OF))
        return run_leader_tradability_live_acceptance(
            self.helper.request(),
            self.candidate_sources(),
            evidence_sources=evidence_sources or self.evidence_sources(),
            clock=lambda: next(clock_values),
        )

    def test_complete_scope_rebases_once_and_enters_production_collector(self):
        result = self.run_acceptance()

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
        )
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(result.as_of, EVIDENCE_COMPLETED_AT)
        self.assertEqual(
            result.collected_source.status,
            LeaderFormalResearchProductionSourceStatus.COMPLETED,
        )
        self.assertEqual(result.report.expected_count, 2)
        self.assertEqual(result.report.returned_count, 2)
        self.assertTrue(all(
            coverage == 1.0
            for coverage in result.report.field_coverage.values()
        ))
        self.assertFalse(result.formal_usable)
        self.assertNotIn("000001", repr(result.to_evidence()))

    def test_official_source_failure_closes_the_whole_batch(self):
        result = self.run_acceptance(evidence_sources=self.evidence_sources(
            official_loader=lambda *_: ExchangeOfficialObservationBatch(
                status="source_failed",
                reasons=("exchange_official_request_failed",),
            )
        ))

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
        )
        self.assertIn("exchange_official_request_failed", result.reasons)
        self.assertIsNone(result.frozen_batch)

    def test_incomplete_lifecycle_crosscheck_is_source_unverified(self):
        result = self.run_acceptance(evidence_sources=self.evidence_sources(
            lifecycle_loader=lambda contexts: self.lifecycle_frame(contexts).iloc[:1]
        ))

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("sina_lifecycle_scope_unverified", result.reasons)
        self.assertIsNone(result.frozen_batch)

    def test_slow_evidence_collection_does_not_reuse_stale_quotes(self):
        slow_time = helpers.COLLECTION_COMPLETED_AT + timedelta(seconds=91)

        def slow_calendar(as_of):
            document = self.calendar_document(as_of)
            return PublicCalendarDocument(
                source_url=document.source_url,
                document_id=document.document_id,
                text=document.text,
                source_time=document.source_time,
                fetched_at=slow_time,
            )

        result = self.run_acceptance(evidence_sources=self.evidence_sources(
            calendar_loader=slow_calendar
        ))

        self.assertEqual(
            result.status,
            LeaderTradabilityLiveAcceptanceStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("quote_source_not_healthy_after_evidence", result.reasons)
        self.assertIsNone(result.frozen_batch)

    def test_cli_outputs_only_sanitized_evidence(self):
        completed = self.run_acceptance()
        with (
            patch.object(
                cli,
                "build_default_leader_live_candidate_collection_sources",
                return_value=object(),
            ),
            patch.object(
                cli,
                "run_leader_tradability_live_acceptance",
                return_value=completed,
            ),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        self.assertIn('"status": "completed"', output.getvalue())
        self.assertNotIn("000001", output.getvalue())


if __name__ == "__main__":
    unittest.main()
