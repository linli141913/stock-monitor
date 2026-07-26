import hashlib
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from radar.contracts import (
    EtfManagementStyle,
    IndexEvidenceStatus,
)
from radar.sources.etf_index_evidence import (
    build_constituent_set_evidence,
    build_methodology_evidence,
    parse_efunds_product_page,
    verify_etf_index_identity,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 24, 15, 30, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 24, 15, 31, tzinfo=SHANGHAI_TZ)
EVIDENCE_HASH = hashlib.sha256(b"official evidence").hexdigest()


class EtfIndexIdentityEvidenceTests(unittest.TestCase):
    def relation(self, **overrides):
        values = {
            "symbol": "159915",
            "management_style": EtfManagementStyle.PASSIVE_INDEX,
            "fund_index_code": "399006",
            "fund_index_name": "创业板指数",
            "provider": "cnindex",
            "provider_index_code": "399006",
            "provider_index_name": "创业板指数",
            "published_at": datetime(
                2011,
                9,
                20,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_from": datetime(
                2011,
                9,
                20,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_to": None,
            "as_of": AS_OF,
            "fund_evidence_url": "https://fund.example/159915",
            "fund_evidence_sha256": EVIDENCE_HASH,
            "provider_evidence_url": "https://index.example/399006",
            "provider_evidence_sha256": EVIDENCE_HASH,
            "first_observed_at": FETCHED_AT,
            "fetched_at": FETCHED_AT,
        }
        values.update(overrides)
        return verify_etf_index_identity(**values)

    def test_exact_code_and_name_can_be_formally_ready(self):
        result = self.relation()

        self.assertEqual(result.status, IndexEvidenceStatus.VERIFIED)
        self.assertTrue(result.identity_matched)
        self.assertTrue(result.formal_ready)
        self.assertEqual(result.reasons, ())

    def test_name_only_never_guesses_index_code(self):
        result = self.relation(fund_index_code=None)

        self.assertEqual(result.status, IndexEvidenceStatus.PENDING_EVIDENCE)
        self.assertFalse(result.identity_matched)
        self.assertFalse(result.formal_ready)
        self.assertIn("index_identity_missing", result.reasons)

    def test_code_or_name_conflict_blocks_relation(self):
        result = self.relation(fund_index_code="000300")

        self.assertEqual(result.status, IndexEvidenceStatus.CONFLICT)
        self.assertFalse(result.identity_matched)
        self.assertFalse(result.formal_ready)
        self.assertIn("index_identity_conflict", result.reasons)

    def test_active_etf_does_not_receive_a_fake_index_relation(self):
        result = self.relation(
            management_style=EtfManagementStyle.ACTIVE,
            fund_index_code=None,
            fund_index_name=None,
        )

        self.assertEqual(result.status, IndexEvidenceStatus.PENDING_EVIDENCE)
        self.assertFalse(result.formal_ready)
        self.assertIn("active_etf_registry_only", result.reasons)

    def test_missing_relation_dates_preserves_verified_identity_only(self):
        result = self.relation(published_at=None, effective_from=None)

        self.assertEqual(result.status, IndexEvidenceStatus.VERIFIED)
        self.assertTrue(result.identity_matched)
        self.assertFalse(result.formal_ready)
        self.assertIn("relation_published_at_missing", result.reasons)
        self.assertIn("relation_effective_from_missing", result.reasons)

    def test_efunds_page_parser_uses_structured_fields(self):
        html = b"""
        <table class="baseinfo-table"
               data-level1desc="\xe8\xa2\xab\xe5\x8a\xa8\xe8\x82\xa1\xe7\xa5\xa8\xe6\x8c\x87\xe6\x95\xb0">
          <tr><td>\xe6\xa0\x87\xe7\x9a\x84\xe6\x8c\x87\xe6\x95\xb0\xe5\x90\x8d\xe7\xa7\xb0\xef\xbc\x9a</td>
              <td>\xe5\x88\x9b\xe4\xb8\x9a\xe6\x9d\xbf\xe6\x8c\x87\xe6\x95\xb0</td></tr>
          <tr><td id="UNDERLYINGSECURITYID">399006</td></tr>
        </table>
        """

        parsed = parse_efunds_product_page(html)

        self.assertEqual(
            parsed.management_style,
            EtfManagementStyle.PASSIVE_INDEX,
        )
        self.assertEqual(parsed.index_name, "创业板指数")
        self.assertEqual(parsed.index_code, "399006")


class IndexVersionEvidenceTests(unittest.TestCase):
    def methodology(self, **overrides):
        values = {
            "provider": "csindex",
            "index_code": "000300",
            "index_name": "沪深300指数",
            "provider_version": "2023-09",
            "published_at": datetime(
                2023,
                9,
                1,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_from": datetime(
                2023,
                9,
                1,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_to": None,
            "universe_rule": "沪深A股和符合条件的存托凭证",
            "selection_rule": "流动性筛选后选取总市值前300名",
            "weighting_method": "调整市值加权",
            "constituent_cap": 300,
            "rebalance_frequency": "semiannual",
            "as_of": AS_OF,
            "evidence_url": "https://index.example/methodology.pdf",
            "evidence_sha256": EVIDENCE_HASH,
            "first_observed_at": FETCHED_AT,
            "fetched_at": FETCHED_AT,
        }
        values.update(overrides)
        return build_methodology_evidence(**values)

    def test_methodology_future_and_historical_versions_are_not_current(self):
        future = self.methodology(
            effective_from=datetime(
                2026,
                7,
                25,
                tzinfo=SHANGHAI_TZ,
            ),
        )
        expired = self.methodology(
            effective_to=datetime(
                2026,
                7,
                24,
                15,
                tzinfo=SHANGHAI_TZ,
            ),
        )

        self.assertFalse(future.formal_ready)
        self.assertIn("methodology_future_effective", future.reasons)
        self.assertFalse(expired.formal_ready)
        self.assertIn("methodology_historical_expired", expired.reasons)

    def test_methodology_missing_exact_dates_remains_pending(self):
        result = self.methodology(published_at=None, effective_from=None)

        self.assertFalse(result.formal_ready)
        self.assertIn("methodology_published_at_missing", result.reasons)
        self.assertIn("methodology_effective_from_missing", result.reasons)

    def constituents(self, items, **overrides):
        values = {
            "provider": "csindex",
            "index_code": "000300",
            "index_name": "沪深300指数",
            "announced_at": datetime(
                2026,
                7,
                23,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_from": datetime(
                2026,
                7,
                24,
                tzinfo=SHANGHAI_TZ,
            ),
            "effective_to": None,
            "expected_count": len(items),
            "as_of": AS_OF,
            "evidence_url": "https://index.example/weights.xls",
            "evidence_sha256": EVIDENCE_HASH,
            "first_observed_at": FETCHED_AT,
            "fetched_at": FETCHED_AT,
            "items": items,
        }
        values.update(overrides)
        return build_constituent_set_evidence(**values)

    def test_complete_weights_accept_true_zero(self):
        result = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 60.0},
            {"stockCode": "000002", "stockName": "乙", "weight": 40.0},
            {"stockCode": "000003", "stockName": "丙", "weight": 0.0},
        ])

        self.assertTrue(result.formal_ready)
        self.assertEqual(result.weight_count, 3)
        self.assertEqual(result.weight_total, 100.0)
        self.assertEqual(result.items[-1].weight, 0.0)

    def test_duplicate_missing_weight_abnormal_total_and_empty_are_explicit(self):
        duplicate = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 50.0},
            {"stockCode": "000001", "stockName": "甲", "weight": 50.0},
        ])
        missing = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
            {"stockCode": "000002", "stockName": "乙", "weight": None},
        ])
        abnormal = self.constituents([
            {"stockCode": "000001", "stockName": "甲", "weight": 80.0},
        ])
        empty = self.constituents([], expected_count=100)

        self.assertIn("constituent_duplicate", duplicate.reasons)
        self.assertIn("constituent_weight_missing", missing.reasons)
        self.assertIn("constituent_weight_total_abnormal", abnormal.reasons)
        self.assertIn("constituent_empty", empty.reasons)
        self.assertFalse(duplicate.formal_ready)
        self.assertFalse(missing.formal_ready)
        self.assertFalse(abnormal.formal_ready)
        self.assertFalse(empty.formal_ready)

    def test_future_and_expired_constituent_sets_are_not_current(self):
        items = [
            {"stockCode": "000001", "stockName": "甲", "weight": 100.0},
        ]
        future = self.constituents(
            items,
            effective_from=datetime(
                2026,
                7,
                25,
                tzinfo=SHANGHAI_TZ,
            ),
        )
        expired = self.constituents(
            items,
            effective_to=datetime(
                2026,
                7,
                24,
                15,
                tzinfo=SHANGHAI_TZ,
            ),
        )

        self.assertIn("constituent_future_effective", future.reasons)
        self.assertIn("constituent_historical_expired", expired.reasons)
        self.assertFalse(future.formal_ready)
        self.assertFalse(expired.formal_ready)
