import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_lifecycle_delivery import (
    LeaderRiskLifecycleDeliveryInput,
    LeaderRiskLifecycleDeliveryStatus,
    LeaderRiskLifecycleRealPocStatus,
    deliver_leader_risk_lifecycle,
)
from radar.leader_risk_lifecycle_batch import (
    LeaderRiskLifecycleBatchEntry,
    LeaderRiskLifecycleBatchStatus,
)
from radar.leader_risk_invalidation_features import RiskCategory
from radar.leader_runtime_candidate_plan import _candidate_set_id
from radar.sources.leader_risk_official import CninfoRiskIssuerScope
from tests import test_radar_leader_risk_lifecycle_batch as lifecycle_helpers


class LeaderRiskLifecycleDeliveryTests(unittest.TestCase):
    def setUp(self):
        helper = lifecycle_helpers.LeaderRiskLifecycleBatchTests(
            methodName="test_two_human_versions_replay_into_partial_runtime_batch"
        )
        helper.setUp()
        self.helper = helper
        self.plan = helper.plan
        self.window_until = self.plan.as_of.date()
        self.window_from = self.window_until - timedelta(days=365)

    def input_value(self, **changes):
        value = LeaderRiskLifecycleDeliveryInput(
            candidate_plan=self.plan,
            entries=self.helper._entries(),
            candidate_scopes=self.candidate_scopes(),
            window_from=self.window_from,
            window_until=self.window_until,
            collected_at=self.plan.as_of,
            confirm_live_poc=True,
            max_page_requests=7,
        )
        return replace(value, **changes)

    def candidate_scopes(self, plan=None):
        plan = plan or self.plan
        return tuple(
            CninfoRiskIssuerScope(
                symbol=item.symbol,
                issuer_identity=(
                    self.helper.issuer_identity
                    if index == 0
                    else f"cninfo-org:fixture{item.symbol}"
                ),
                resolved_at=plan.as_of,
            )
            for index, item in enumerate(plan.items)
        )

    def plan_at(self, as_of):
        items = tuple(
            replace(item, as_of=as_of) for item in self.plan.items
        )
        plan = replace(self.plan, as_of=as_of, items=items)
        return replace(
            plan,
            candidate_set_id=_candidate_set_id(
                radar_run_id=plan.radar_run_id,
                quote_batch_id=plan.quote_batch_id,
                as_of=as_of,
                items=items,
                market_source_contract_id=plan.market_source_contract_id,
            ),
        )

    def large_input_value(self, candidate_count, *, max_page_requests):
        base_item = self.plan.items[0]
        items = tuple(
            replace(
                base_item,
                index=index,
                symbol=f"{300000 + index:06d}",
                industry_code=f"I{index:04d}",
                industry_name=f"Industry {index}",
                industry_release_id=f"release-{index}",
            )
            for index in range(candidate_count)
        )
        plan = replace(
            self.plan,
            items=items,
            scanned_count=candidate_count,
            mapped_count=candidate_count,
        )
        plan = replace(
            plan,
            candidate_set_id=_candidate_set_id(
                radar_run_id=plan.radar_run_id,
                quote_batch_id=plan.quote_batch_id,
                as_of=plan.as_of,
                items=items,
                market_source_contract_id=plan.market_source_contract_id,
            ),
        )
        scopes = tuple(
            CninfoRiskIssuerScope(
                symbol=item.symbol,
                issuer_identity=f"cninfo-org:fixture{item.symbol}",
                resolved_at=plan.as_of,
            )
            for item in items
        )
        entries = tuple(
            LeaderRiskLifecycleBatchEntry(
                symbol=item.symbol,
                issuer_identity=scopes[index].issuer_identity,
                versions=(),
            )
            for index, item in enumerate(items)
        )
        return LeaderRiskLifecycleDeliveryInput(
            candidate_plan=plan,
            entries=entries,
            candidate_scopes=scopes,
            window_from=self.window_from,
            window_until=self.window_until,
            collected_at=plan.as_of,
            confirm_live_poc=True,
            max_page_requests=max_page_requests,
        )

    def document_row(self, **changes):
        document = self.helper.document
        row = {
            "secCode": document.symbol,
            "secName": document.issuer_name,
            "orgId": document.issuer_identity.removeprefix(
                "cninfo-org:"
            ),
            "announcementId": document.document_id.removeprefix(
                "cninfo:"
            ),
            "announcementTitle": document.title,
            "announcementTime": int(
                document.published_at.timestamp() * 1000
            ),
            "adjunctUrl": document.source_url.removeprefix(
                "https://static.cninfo.com.cn/"
            ),
            "adjunctType": "PDF",
            "columnId": "",
            "pageColumn": None,
            "announcementType": "",
            "associateAnnouncement": None,
        }
        row.update(changes)
        return row

    @staticmethod
    def payload(rows, *, total=None, total_pages=None, has_more=False):
        count = len(rows) if total is None else total
        pages = (1 if count else 0) if total_pages is None else total_pages
        return {
            "totalRecordNum": count,
            "totalAnnouncement": count,
            "totalpages": pages,
            "hasMore": has_more,
            "announcements": list(rows),
        }

    def complete_transport(self, url, *, data, headers, timeout):
        if data["searchkey"] == "立案告知书":
            return self.payload([self.document_row()])
        return self.payload([])

    def test_large_candidate_plan_is_sharded_without_changing_plan_identity(self):
        input_value = self.large_input_value(
            385,
            max_page_requests=91,
        )
        requested_scope_sizes = []

        def transport(url, *, data, headers, timeout):
            requested_scope_sizes.append(
                len(tuple(filter(None, data["stock"].split(";"))))
            )
            return self.payload([])

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.MISSING,
        )
        self.assertEqual(
            result.candidate_plan_id,
            input_value.candidate_plan.candidate_set_id,
        )
        self.assertEqual(result.candidate_scope_count, 385)
        self.assertEqual(result.shard_count, 13)
        self.assertEqual(result.request_count, 91)
        self.assertEqual(
            requested_scope_sizes,
            [30] * 84 + [25] * 7,
        )
        self.assertEqual(
            tuple(
                item.symbol
                for item in result.lifecycle_result.items
            ),
            tuple(item.symbol for item in input_value.candidate_plan.items),
        )

    def test_large_candidate_plan_budget_is_checked_before_source_calls(self):
        input_value = self.large_input_value(
            385,
            max_page_requests=90,
        )
        calls = []

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.BLOCKED,
        )
        self.assertEqual(result.request_count, 0)
        self.assertEqual(result.shard_count, 13)
        self.assertIn(
            "risk_lifecycle_delivery_page_budget_insufficient",
            result.reasons,
        )
        self.assertEqual(calls, [])

    def test_large_candidate_plan_uses_automatic_minimum_budget(self):
        input_value = replace(
            self.large_input_value(385, max_page_requests=91),
            max_page_requests=None,
        )

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=lambda *args, **kwargs: self.payload([]),
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.MISSING,
        )
        self.assertEqual(result.request_count, 91)
        self.assertEqual(result.shard_count, 13)

    def test_multi_shard_second_pages_share_one_global_budget(self):
        input_value = self.large_input_value(
            31,
            max_page_requests=16,
        )
        calls = []

        def transport(url, *, data, headers, timeout):
            symbol, issuer_identity = data["stock"].split(";", 1)[0].split(
                ",",
                1,
            )
            calls.append((
                data["pageNum"],
                data["searchkey"],
                symbol,
            ))
            if data["searchkey"] != "立案告知书":
                return self.payload([])
            shard_offset = 1000 if symbol == "300000" else 2000
            if data["pageNum"] == "1":
                rows = tuple(
                    self.document_row(
                        secCode=symbol,
                        orgId=issuer_identity,
                        announcementId=str(1225400000 + shard_offset + index),
                        adjunctUrl=(
                            "finalpage/2026-07-27/"
                            f"{1225400000 + shard_offset + index}.PDF"
                        ),
                    )
                    for index in range(30)
                )
                return self.payload(
                    rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            return self.payload(
                [self.document_row(
                    secCode=symbol,
                    orgId=issuer_identity,
                    announcementId=str(1225400000 + shard_offset + 30),
                    adjunctUrl=(
                        "finalpage/2026-07-27/"
                        f"{1225400000 + shard_offset + 30}.PDF"
                    ),
                )],
                total=31,
                total_pages=2,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.MISSING,
        )
        self.assertEqual(result.request_count, 16)
        self.assertEqual(
            [call[:2] for call in calls[-2:]],
            [("2", "立案告知书"), ("2", "立案告知书")],
        )

        exhausted = deliver_leader_risk_lifecycle(
            replace(input_value, max_page_requests=15),
            transport=transport,
        )
        self.assertEqual(
            exhausted.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(exhausted.request_count, 15)
        self.assertIn(
            "risk_lifecycle_delivery_page_budget_exhausted",
            exhausted.reasons,
        )
        self.assertIsNone(exhausted.lifecycle_result.projection_batch)

    def test_duplicate_document_across_same_category_shards_fails_closed(self):
        input_value = self.large_input_value(
            31,
            max_page_requests=14,
        )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] != "立案告知书":
                return self.payload([])
            symbol, issuer_identity = data["stock"].split(";", 1)[0].split(
                ",",
                1,
            )
            return self.payload([
                self.document_row(
                    secCode=symbol,
                    orgId=issuer_identity,
                    announcementId="1225444999",
                    adjunctUrl="finalpage/2026-07-27/1225444999.PDF",
                )
            ])

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.request_count, 14)
        self.assertIsNone(result.lifecycle_result.projection_batch)
        self.assertIn(
            "risk_lifecycle_query_pages_incomplete",
            result.reasons,
        )

    def test_first_shard_source_failure_is_preserved_by_inner_lifecycle(self):
        input_value = self.large_input_value(
            31,
            max_page_requests=14,
        )

        def transport(*args, **kwargs):
            raise requests.ConnectionError("temporary unavailable")

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.request_count, 2)
        self.assertEqual(
            result.lifecycle_result.status,
            LeaderRiskLifecycleBatchStatus.SOURCE_FAILED,
        )
        self.assertIn(
            "risk_lifecycle_delivery_source_retry_attempted",
            result.reasons,
        )

    def test_second_query_scope_failure_is_not_misreported_as_shard_mismatch(
        self,
    ):
        input_value = self.large_input_value(
            31,
            max_page_requests=14,
        )
        calls = 0

        def transport(url, *, data, headers, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.payload([])
            return self.payload([
                self.document_row(
                    secCode="300030",
                    orgId="fixture300030",
                )
            ])

        result = deliver_leader_risk_lifecycle(
            input_value,
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.request_count, 2)
        self.assertIn(
            "risk_lifecycle_discovery_source_unverified",
            result.reasons,
        )
        self.assertNotIn(
            "risk_official_candidate_discovery_scope_mismatch",
            result.reasons,
        )

    def test_confirmation_is_required_before_any_source_call(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return self.payload([])

        result = deliver_leader_risk_lifecycle(
            self.input_value(confirm_live_poc=False),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.NOT_RUN,
        )
        self.assertEqual(
            result.real_poc_status,
            LeaderRiskLifecycleRealPocStatus.NOT_RUN,
        )
        self.assertEqual(result.request_count, 0)
        self.assertEqual(calls, [])
        self.assertIsNone(result.lifecycle_result)

    def test_complete_fixture_pages_replay_human_versions_into_lifecycle(self):
        result = deliver_leader_risk_lifecycle(
            self.input_value(),
            transport=self.complete_transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        )
        self.assertEqual(
            result.real_poc_status,
            LeaderRiskLifecycleRealPocStatus.NOT_RUN,
        )
        self.assertEqual(result.request_count, 7)
        self.assertEqual(result.fetched_page_count, 7)
        self.assertEqual(result.category_count, 7)
        self.assertEqual(
            result.candidate_scope_count,
            self.plan.candidate_count,
        )
        self.assertEqual(
            result.lifecycle_result.status,
            LeaderRiskLifecycleBatchStatus.PARTIAL,
        )
        self.assertEqual(
            result.lifecycle_result.items[0].status,
            ResearchFeatureStatus.READY,
        )
        self.assertEqual(
            result.lifecycle_result.projection_batch.ready_count,
            1,
        )
        self.assertFalse(result.formal_usable)
        self.assertFalse(result.state_transition_allowed)

    def test_collection_one_second_after_plan_remains_valid(self):
        result = deliver_leader_risk_lifecycle(
            self.input_value(
                collected_at=self.plan.as_of + timedelta(seconds=1),
            ),
            transport=self.complete_transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        )
        self.assertEqual(result.request_count, 7)

    def test_window_until_uses_shanghai_trade_date(self):
        cross_date_as_of = datetime(
            2026,
            7,
            26,
            16,
            30,
            tzinfo=timezone.utc,
        )
        plan = self.plan_at(cross_date_as_of)
        shanghai_date = cross_date_as_of.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date()
        result = deliver_leader_risk_lifecycle(
            self.input_value(
                candidate_plan=plan,
                window_from=shanghai_date - timedelta(days=365),
                window_until=shanghai_date,
                collected_at=cross_date_as_of,
                candidate_scopes=self.candidate_scopes(plan),
            ),
            transport=self.complete_transport,
        )

        self.assertEqual(result.request_count, 7)
        self.assertNotIn(
            "risk_lifecycle_delivery_window_unverified",
            result.reasons,
        )

    def test_page_budget_exhaustion_cannot_be_hidden_by_manual_entries(self):
        rows = tuple(
            self.document_row(
                announcementId=str(1225444000 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444000 + index}.PDF"
                ),
            )
            for index in range(30)
        )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] == "减持计划":
                return self.payload(
                    rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            return self.complete_transport(
                url,
                data=data,
                headers=headers,
                timeout=timeout,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.request_count, 7)
        self.assertIn(
            "risk_lifecycle_delivery_page_budget_exhausted",
            result.reasons,
        )
        self.assertEqual(
            result.lifecycle_result.status,
            LeaderRiskLifecycleBatchStatus.SOURCE_UNVERIFIED,
        )
        self.assertIsNone(result.lifecycle_result.projection_batch)

    def test_second_page_is_collected_when_budget_allows(self):
        first_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444100 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444100 + index}.PDF"
                ),
            )
            for index in range(30)
        )
        second_page_row = self.document_row(
            announcementId="1225444130",
            adjunctUrl="finalpage/2026-07-27/1225444130.PDF",
        )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] != "减持计划":
                return self.complete_transport(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            if data["pageNum"] == "1":
                return self.payload(
                    first_page_rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            return self.payload(
                [second_page_row],
                total=31,
                total_pages=2,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=8),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        )
        self.assertEqual(result.request_count, 8)
        self.assertEqual(result.fetched_page_count, 8)
        self.assertNotIn(
            "risk_lifecycle_delivery_page_budget_exhausted",
            result.reasons,
        )
        self.assertEqual(
            result.lifecycle_result.projection_batch.ready_count,
            1,
        )

    def test_cross_page_duplicate_uses_two_matching_date_partition_sweeps(self):
        first_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444400 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444400 + index}.PDF"
                ),
            )
            for index in range(30)
        )
        full_window = f"{self.window_from.isoformat()}~{self.window_until.isoformat()}"
        midpoint = self.window_from + timedelta(
            days=(self.window_until - self.window_from).days // 2
        )
        target_calls = []

        def partition_rows(start, end):
            indexes = range(15) if end == midpoint else range(15, 31)
            published_at = datetime.combine(
                end,
                datetime.min.time(),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
            return tuple(
                self.document_row(
                    announcementId=str(1225444400 + index),
                    announcementTime=int(published_at.timestamp() * 1000),
                    adjunctUrl=(
                        "finalpage/2026-07-27/"
                        f"{1225444400 + index}.PDF"
                    ),
                )
                for index in indexes
            )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] != "减持计划":
                return self.complete_transport(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            target_calls.append((data["seDate"], data["pageNum"]))
            if data["seDate"] == full_window:
                if data["pageNum"] == "2":
                    return self.payload(
                        [first_page_rows[-1]],
                        total=31,
                        total_pages=2,
                        has_more=False,
                    )
                return self.payload(
                    first_page_rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            start_text, end_text = data["seDate"].split("~", 1)
            rows = partition_rows(
                datetime.fromisoformat(start_text).date(),
                datetime.fromisoformat(end_text).date(),
            )
            return self.payload(
                rows,
                total=len(rows),
                total_pages=1,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=14),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        )
        self.assertEqual(result.request_count, 14)
        self.assertEqual(len(target_calls), 8)
        self.assertIn(
            "risk_lifecycle_delivery_date_partition_check_attempted",
            result.reasons,
        )
        self.assertIn(
            "risk_lifecycle_delivery_date_partition_check_succeeded",
            result.reasons,
        )
        self.assertNotIn(
            "risk_lifecycle_query_pages_incomplete",
            result.reasons,
        )

    def test_date_partition_check_fails_closed_when_sweeps_differ(self):
        first_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444500 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444500 + index}.PDF"
                ),
            )
            for index in range(30)
        )
        full_window = f"{self.window_from.isoformat()}~{self.window_until.isoformat()}"
        midpoint = self.window_from + timedelta(
            days=(self.window_until - self.window_from).days // 2
        )
        partition_round = 0

        def partition_rows(end):
            indexes = list(
                range(15) if end == midpoint else range(15, 31)
            )
            if partition_round == 3 and end != midpoint:
                indexes[-1] = 31
            published_at = datetime.combine(
                end,
                datetime.min.time(),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
            return tuple(
                self.document_row(
                    announcementId=str(1225444500 + index),
                    announcementTime=int(published_at.timestamp() * 1000),
                    adjunctUrl=(
                        "finalpage/2026-07-27/"
                        f"{1225444500 + index}.PDF"
                    ),
                )
                for index in indexes
            )

        def transport(url, *, data, headers, timeout):
            nonlocal partition_round
            if data["searchkey"] != "减持计划":
                return self.complete_transport(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            if data["seDate"] == full_window:
                if data["pageNum"] == "1":
                    partition_round += 1
                if data["pageNum"] == "2":
                    return self.payload(
                        [first_page_rows[-1]],
                        total=31,
                        total_pages=2,
                        has_more=False,
                    )
                return self.payload(
                    first_page_rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            _, end_text = data["seDate"].split("~", 1)
            end = datetime.fromisoformat(end_text).date()
            rows = partition_rows(end)
            return self.payload(
                rows,
                total=len(rows),
                total_pages=1,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=14),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.request_count, 14)
        self.assertEqual(partition_round, 3)
        self.assertIn(
            "risk_lifecycle_delivery_date_partition_unverified",
            result.reasons,
        )
        self.assertIsNone(result.lifecycle_result.projection_batch)

    def test_date_partition_source_failure_stops_after_network_retry(self):
        first_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444600 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444600 + index}.PDF"
                ),
            )
            for index in range(30)
        )
        full_window = f"{self.window_from.isoformat()}~{self.window_until.isoformat()}"
        target_calls = 0

        def transport(url, *, data, headers, timeout):
            nonlocal target_calls
            if data["searchkey"] != "减持计划":
                return self.complete_transport(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            target_calls += 1
            if data["seDate"] != full_window:
                raise requests.ConnectionError("temporary")
            if data["pageNum"] == "1":
                return self.payload(
                    first_page_rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            return self.payload(
                [first_page_rows[-1]],
                total=31,
                total_pages=2,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=12),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.request_count, 11)
        self.assertEqual(target_calls, 5)
        self.assertIn(
            "risk_lifecycle_delivery_source_retry_attempted",
            result.reasons,
        )
        self.assertNotIn(
            "risk_lifecycle_delivery_date_partition_check_succeeded",
            result.reasons,
        )

    def test_cross_page_total_drift_is_source_unverified(self):
        first_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444200 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444200 + index}.PDF"
                ),
            )
            for index in range(30)
        )
        second_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444230 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444230 + index}.PDF"
                ),
            )
            for index in range(2)
        )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] != "减持计划":
                return self.complete_transport(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            if data["pageNum"] == "1":
                return self.payload(
                    first_page_rows,
                    total=31,
                    total_pages=2,
                    has_more=True,
                )
            return self.payload(
                second_page_rows,
                total=32,
                total_pages=2,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=8),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.request_count, 8)
        self.assertIsNone(result.lifecycle_result.projection_batch)

    def test_cross_page_reported_page_count_drift_is_source_unverified(self):
        first_page_rows = tuple(
            self.document_row(
                announcementId=str(1225444300 + index),
                adjunctUrl=(
                    "finalpage/2026-07-27/"
                    f"{1225444300 + index}.PDF"
                ),
            )
            for index in range(30)
        )
        second_page_row = self.document_row(
            announcementId="1225444330",
            adjunctUrl="finalpage/2026-07-27/1225444330.PDF",
        )

        def transport(url, *, data, headers, timeout):
            if data["searchkey"] != "减持计划":
                return self.complete_transport(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            if data["pageNum"] == "1":
                return self.payload(
                    first_page_rows,
                    total=31,
                    total_pages=1,
                    has_more=True,
                )
            return self.payload(
                [second_page_row],
                total=31,
                total_pages=2,
                has_more=False,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=8),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_UNVERIFIED,
        )
        self.assertEqual(result.request_count, 8)
        self.assertIsNone(result.lifecycle_result.projection_batch)

    def test_source_request_failure_retries_once_within_page_budget(self):
        calls = 0

        def transport(url, *, data, headers, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise requests.ConnectionError("temporary")
            return self.complete_transport(
                url,
                data=data,
                headers=headers,
                timeout=timeout,
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(max_page_requests=8),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.PARTIAL,
        )
        self.assertEqual(result.request_count, 8)
        self.assertEqual(calls, 8)
        self.assertIn(
            "risk_lifecycle_delivery_source_retry_attempted",
            result.reasons,
        )
        self.assertIn(
            "risk_lifecycle_delivery_source_retry_succeeded",
            result.reasons,
        )

    def test_source_failure_stops_after_one_retry_without_sensitive_echo(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            raise requests.ConnectionError(
                "https://example.invalid/?token=secret"
            )

        result = deliver_leader_risk_lifecycle(
            self.input_value(),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.request_count, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.fetched_page_count, 0)
        self.assertEqual(result.category_count, 0)
        self.assertNotIn("secret", str(result.to_evidence()))
        self.assertFalse(result.formal_usable)

    def test_unexpected_transport_error_is_sanitized_without_retry(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            raise RuntimeError("https://example.invalid/?token=secret")

        result = deliver_leader_risk_lifecycle(
            self.input_value(),
            transport=transport,
        )

        self.assertEqual(
            result.status,
            LeaderRiskLifecycleDeliveryStatus.SOURCE_FAILED,
        )
        self.assertEqual(result.request_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.fetched_page_count, 0)
        self.assertNotIn("secret", str(result.to_evidence()))

    def test_candidate_order_and_collection_time_fail_before_source_call(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return self.payload([])

        reversed_entries = tuple(reversed(self.helper._entries()))
        cases = (
            self.input_value(entries=reversed_entries),
            self.input_value(
                collected_at=self.plan.as_of - timedelta(seconds=1),
            ),
            self.input_value(
                window_until=self.window_until - timedelta(days=1),
            ),
        )
        for input_value in cases:
            with self.subTest(input_value=input_value):
                result = deliver_leader_risk_lifecycle(
                    input_value,
                    transport=transport,
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskLifecycleDeliveryStatus.BLOCKED,
                )
                self.assertEqual(result.request_count, 0)
        self.assertEqual(calls, [])

    def test_candidate_scope_fails_before_source_call_when_incomplete_or_wrong(self):
        calls = []

        def transport(*args, **kwargs):
            calls.append((args, kwargs))
            return self.payload([])

        scopes = self.candidate_scopes()
        mismatched = (
            replace(
                scopes[0],
                issuer_identity="cninfo-org:wrongissuer",
            ),
            *scopes[1:],
        )
        cases = (
            self.input_value(candidate_scopes=()),
            self.input_value(candidate_scopes=tuple(reversed(scopes))),
            self.input_value(candidate_scopes=mismatched),
        )
        for input_value in cases:
            with self.subTest(scope_count=len(input_value.candidate_scopes)):
                result = deliver_leader_risk_lifecycle(
                    input_value,
                    transport=transport,
                )
                self.assertEqual(
                    result.status,
                    LeaderRiskLifecycleDeliveryStatus.BLOCKED,
                )
                self.assertEqual(result.request_count, 0)
                self.assertIn(
                    "risk_lifecycle_delivery_candidate_scope_unverified",
                    result.reasons,
                )
        self.assertEqual(calls, [])

    def test_report_is_compact_and_does_not_claim_fixture_is_real(self):
        result = deliver_leader_risk_lifecycle(
            self.input_value(),
            transport=self.complete_transport,
        )

        evidence = result.to_evidence()
        serialized = str(evidence)
        self.assertEqual(evidence["realPocStatus"], "not_run")
        self.assertNotIn(self.helper.document.title, serialized)
        self.assertNotIn(self.helper.document.source_url, serialized)
        self.assertNotIn("decision_summary", serialized)
        self.assertFalse(evidence["gate"]["formalUsable"])
        self.assertFalse(evidence["gate"]["stateTransitionAllowed"])

    def test_report_rejects_unsafe_run_identifiers(self):
        result = deliver_leader_risk_lifecycle(
            self.input_value(),
            transport=self.complete_transport,
        )
        unsafe = replace(
            result,
            candidate_plan_id="candidate?token=secret",
            radar_run_id="https://example.invalid/private",
            quote_batch_id="quote\nprivate",
        )

        evidence = unsafe.to_evidence()
        self.assertIsNone(evidence["candidatePlanId"])
        self.assertIsNone(evidence["radarRunId"])
        self.assertIsNone(evidence["quoteBatchId"])
        self.assertNotIn("secret", str(evidence))


if __name__ == "__main__":
    unittest.main()
