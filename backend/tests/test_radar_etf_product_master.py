import unittest
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    ListedFundProductType,
)
from radar.sources.etf_product_master import (
    ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION,
    EtfProductMasterProviders,
    fetch_etf_product_master,
    fetch_sse_etf_product_record,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 24, 10, 0, tzinfo=SHANGHAI_TZ)
FETCHED_AT = datetime(2026, 7, 24, 10, 0, 2, tzinfo=SHANGHAI_TZ)


def sse_categories():
    return pd.DataFrame([
        {
            "CATEGORY_CODE": "F112",
            "CATEGORY_PARENT_CODE": "F110",
            "CATEGORY_NAME": "跨市场股票（沪深京）ETF",
        },
        {
            "CATEGORY_CODE": "F113",
            "CATEGORY_PARENT_CODE": "F110",
            "CATEGORY_NAME": "跨市场股票（沪港深京）ETF",
        },
        {
            "CATEGORY_CODE": "F131",
            "CATEGORY_PARENT_CODE": "F130",
            "CATEGORY_NAME": "跨境ETF",
        },
        {
            "CATEGORY_CODE": "F213",
            "CATEGORY_PARENT_CODE": "F210",
            "CATEGORY_NAME": "混合型LOF",
        },
        {
            "CATEGORY_CODE": "F600",
            "CATEGORY_PARENT_CODE": "F000",
            "CATEGORY_NAME": "REITs",
        },
    ])


def sse_products():
    return pd.DataFrame([
        {
            "FUND_CODE": "510300",
            "FUND_ABBR": "沪深300ETF",
            "CATEGORY": "F112",
            "INDEX_NAME": "沪深300指数",
            "LISTING_DATE": "2012-05-28",
            "COMPANY_NAME": "测试基金公司",
            "SCALE": "100.00",
        },
        {
            "FUND_CODE": "513100",
            "FUND_ABBR": "沪港深ETF",
            "CATEGORY": "F113",
            "INDEX_NAME": "跨境测试指数",
            "LISTING_DATE": "2013-05-15",
            "COMPANY_NAME": "测试基金公司",
            "SCALE": "20.00",
        },
        {
            "FUND_CODE": "513500",
            "FUND_ABBR": "跨境资产ETF",
            "CATEGORY": "F131",
            "INDEX_NAME": "海外测试指数",
            "LISTING_DATE": "2014-01-15",
            "COMPANY_NAME": "测试基金公司",
            "SCALE": "15.00",
        },
        {
            "FUND_CODE": "501001",
            "FUND_ABBR": "混合LOF",
            "CATEGORY": "F213",
            "INDEX_NAME": "-",
            "LISTING_DATE": "2015-09-25",
            "COMPANY_NAME": "测试基金公司",
            "SCALE": "1.00",
        },
        {
            "FUND_CODE": "508001",
            "FUND_ABBR": "测试REIT",
            "CATEGORY": "F600",
            "INDEX_NAME": "-",
            "LISTING_DATE": "2021-06-21",
            "COMPANY_NAME": "测试基金公司",
            "SCALE": "10.00",
        },
    ])


def szse_products():
    return pd.DataFrame([
        {
            "基金代码": "159915",
            "基金简称": "创业板ETF",
            "基金类别": "ETF",
            "投资类别": "股票基金",
            "上市日期": "2011-12-09",
            "基金管理人": "测试基金公司",
            "基金份额": 80.0,
        },
        {
            "基金代码": "159999",
            "基金简称": "测试主动ETF",
            "基金类别": "ETF",
            "投资类别": "股票基金",
            "上市日期": "2026-07-01",
            "基金管理人": "测试基金公司",
            "基金份额": 5.0,
        },
        {
            "基金代码": "159816",
            "基金简称": "测试债券ETF",
            "基金类别": "ETF",
            "投资类别": "债券基金",
            "上市日期": "2020-06-01",
            "基金管理人": "测试基金公司",
            "基金份额": 10.0,
        },
        {
            "基金代码": "160001",
            "基金简称": "测试LOF",
            "基金类别": "LOF",
            "投资类别": "混合基金",
            "上市日期": "2004-05-10",
            "基金管理人": "测试基金公司",
            "基金份额": 3.0,
        },
        {
            "基金代码": "180001",
            "基金简称": "测试REIT",
            "基金类别": "不动产基金",
            "投资类别": "ABS",
            "上市日期": "2021-06-21",
            "基金管理人": "测试基金公司",
            "基金份额": 2.0,
        },
    ])


class EtfProductMasterSourceTests(unittest.TestCase):
    def providers(self):
        return EtfProductMasterProviders(
            sse_products=sse_products,
            sse_categories=sse_categories,
            szse_products=szse_products,
        )

    def fetch(self, providers=None):
        return fetch_etf_product_master(
            radar_run_id="stage5b-test",
            batch_id="etf-product-master-1",
            as_of=AS_OF,
            providers=providers or self.providers(),
            clock=lambda: FETCHED_AT,
        )

    def test_official_products_are_classified_without_using_scale_as_identity(self):
        batch = self.fetch()

        self.assertEqual(batch.meta.expected_count, 10)
        self.assertEqual(batch.meta.returned_count, 10)
        self.assertEqual(batch.meta.row_coverage, 1.0)
        self.assertEqual(batch.meta.issues, [])
        self.assertEqual(
            batch.meta.required_field_coverage["product_type_known"],
            1.0,
        )

        by_symbol = {item.symbol: item for item in batch.items}
        domestic = by_symbol["510300"]
        self.assertEqual(domestic.product_type, ListedFundProductType.ETF)
        self.assertEqual(domestic.asset_class, EtfAssetClass.DOMESTIC_EQUITY)
        self.assertEqual(
            domestic.management_style,
            EtfManagementStyle.PASSIVE_INDEX,
        )
        self.assertEqual(domestic.source_category_code, "F112")
        self.assertEqual(
            domestic.source_category_name,
            "跨市场股票（沪深京）ETF",
        )
        self.assertEqual(domestic.target_index_name, "沪深300指数")
        self.assertEqual(
            domestic.classification_mapping_version,
            ETF_PRODUCT_CLASSIFICATION_MAPPING_VERSION,
        )
        self.assertNotIn(
            "management_style_unverified",
            domestic.classification_reasons,
        )
        self.assertNotIn("fund_size", type(domestic).model_fields)
        self.assertEqual(
            by_symbol["513100"].asset_class,
            EtfAssetClass.CROSS_BORDER_EQUITY,
        )
        self.assertEqual(
            by_symbol["513500"].asset_class,
            EtfAssetClass.UNKNOWN,
        )
        self.assertIn(
            "cross_border_asset_unverified",
            by_symbol["513500"].classification_reasons,
        )
        self.assertEqual(
            by_symbol["501001"].product_type,
            ListedFundProductType.LOF,
        )
        self.assertEqual(
            by_symbol["508001"].product_type,
            ListedFundProductType.REIT,
        )

    def test_single_sse_product_fetcher_preserves_official_target_index(self):
        record = fetch_sse_etf_product_record(
            "510300",
            product_fetcher=sse_products,
            category_fetcher=sse_categories,
            clock=lambda: FETCHED_AT,
        )

        self.assertEqual(record.symbol, "510300")
        self.assertEqual(record.target_index_name, "沪深300指数")
        self.assertEqual(record.exchange, "sse")
        self.assertEqual(record.fetched_at, FETCHED_AT)

    def test_single_sse_product_fetcher_rejects_missing_and_duplicate(self):
        with self.assertRaisesRegex(ValueError, "sse_etf_product_not_found"):
            fetch_sse_etf_product_record(
                "588999",
                product_fetcher=sse_products,
                category_fetcher=sse_categories,
                clock=lambda: FETCHED_AT,
            )
        duplicate = pd.concat([sse_products(), sse_products().iloc[[0]]])
        with self.assertRaisesRegex(ValueError, "sse_etf_product_ambiguous"):
            fetch_sse_etf_product_record(
                "510300",
                product_fetcher=lambda: duplicate,
                category_fetcher=sse_categories,
                clock=lambda: FETCHED_AT,
            )

    def test_szse_equity_scope_stays_unknown_but_official_active_name_is_used(self):
        batch = self.fetch()
        by_symbol = {item.symbol: item for item in batch.items}

        equity = by_symbol["159915"]
        self.assertEqual(equity.product_type, ListedFundProductType.ETF)
        self.assertEqual(equity.asset_class, EtfAssetClass.UNKNOWN)
        self.assertIn(
            "equity_region_unverified",
            equity.classification_reasons,
        )

        active_name = by_symbol["159999"]
        self.assertEqual(
            active_name.management_style,
            EtfManagementStyle.ACTIVE,
        )
        self.assertNotIn(
            "management_style_unverified",
            active_name.classification_reasons,
        )
        self.assertEqual(
            by_symbol["159816"].asset_class,
            EtfAssetClass.BOND,
        )
        self.assertEqual(
            by_symbol["160001"].product_type,
            ListedFundProductType.LOF,
        )
        self.assertEqual(
            by_symbol["180001"].product_type,
            ListedFundProductType.REIT,
        )

    def test_sse_active_name_takes_precedence_over_missing_target_index(self):
        products = sse_products()
        products.loc[len(products)] = {
            "FUND_CODE": "561999",
            "FUND_ABBR": "创新主动ETF",
            "CATEGORY": "F112",
            "INDEX_NAME": "-",
            "LISTING_DATE": "2026-07-01",
            "COMPANY_NAME": "测试基金公司",
            "SCALE": "2.00",
        }
        batch = self.fetch(EtfProductMasterProviders(
            sse_products=lambda: products,
            sse_categories=sse_categories,
            szse_products=lambda: pd.DataFrame(),
        ))

        record = {item.symbol: item for item in batch.items}["561999"]
        self.assertEqual(record.management_style, EtfManagementStyle.ACTIVE)
        self.assertNotIn(
            "management_style_unverified",
            record.classification_reasons,
        )

    def test_sse_etf_without_active_name_or_target_index_stays_unknown(self):
        products = sse_products()
        products.loc[0, "INDEX_NAME"] = "-"
        batch = self.fetch(EtfProductMasterProviders(
            sse_products=lambda: products,
            sse_categories=sse_categories,
            szse_products=lambda: pd.DataFrame(),
        ))

        record = {item.symbol: item for item in batch.items}["510300"]
        self.assertEqual(record.management_style, EtfManagementStyle.UNKNOWN)
        self.assertIn(
            "management_style_unverified",
            record.classification_reasons,
        )

    def test_unknown_exchange_category_is_preserved_and_not_guessed(self):
        products = sse_products()
        products.loc[0, "CATEGORY"] = "F999"
        categories = sse_categories()
        categories.loc[len(categories)] = {
            "CATEGORY_CODE": "F999",
            "CATEGORY_PARENT_CODE": "F000",
            "CATEGORY_NAME": "未来新类别",
        }

        batch = self.fetch(EtfProductMasterProviders(
            sse_products=lambda: products,
            sse_categories=lambda: categories,
            szse_products=lambda: pd.DataFrame(),
        ))

        record = {item.symbol: item for item in batch.items}["510300"]
        self.assertEqual(record.product_type, ListedFundProductType.UNKNOWN)
        self.assertEqual(record.asset_class, EtfAssetClass.UNKNOWN)
        self.assertIn("product_type_unknown", record.classification_reasons)
        self.assertEqual(record.source_category_code, "F999")
        self.assertEqual(record.source_category_name, "未来新类别")

    def test_duplicate_symbol_is_explicit_and_reduces_coverage(self):
        duplicates = szse_products()
        duplicates.loc[0, "基金代码"] = "510300"

        batch = self.fetch(EtfProductMasterProviders(
            sse_products=sse_products,
            sse_categories=sse_categories,
            szse_products=lambda: duplicates,
        ))

        self.assertEqual(batch.meta.expected_count, 10)
        self.assertEqual(batch.meta.returned_count, 9)
        self.assertAlmostEqual(batch.meta.row_coverage, 9 / 10)
        duplicate = [
            issue
            for issue in batch.meta.issues
            if issue.code == "duplicate_symbol"
        ]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0].symbols, ["510300"])

    def test_source_failure_is_preserved_without_partial_success_claim(self):
        batch = self.fetch(EtfProductMasterProviders(
            sse_products=Mock(side_effect=RuntimeError("unavailable")),
            sse_categories=sse_categories,
            szse_products=szse_products,
        ))

        self.assertIsNone(batch.meta.expected_count)
        self.assertIsNone(batch.meta.row_coverage)
        self.assertEqual(batch.meta.returned_count, 5)
        self.assertIn(
            "source_request_failed",
            {issue.code for issue in batch.meta.issues},
        )

    def test_sse_category_source_failure_does_not_guess_from_code_prefix(self):
        batch = self.fetch(EtfProductMasterProviders(
            sse_products=sse_products,
            sse_categories=Mock(side_effect=RuntimeError("unavailable")),
            szse_products=szse_products,
        ))

        self.assertIsNone(batch.meta.expected_count)
        record = {item.symbol: item for item in batch.items}["510300"]
        self.assertEqual(record.product_type, ListedFundProductType.UNKNOWN)
        self.assertEqual(record.asset_class, EtfAssetClass.UNKNOWN)
        self.assertEqual(record.source_category_code, "F112")
        self.assertIsNone(record.source_category_name)
        self.assertIn(
            "source_request_failed",
            {issue.code for issue in batch.meta.issues},
        )

    def test_empty_sse_category_source_is_explicit(self):
        batch = self.fetch(EtfProductMasterProviders(
            sse_products=sse_products,
            sse_categories=lambda: pd.DataFrame(),
            szse_products=szse_products,
        ))

        self.assertIsNone(batch.meta.expected_count)
        self.assertIn(
            "empty_source_result",
            {issue.code for issue in batch.meta.issues},
        )
        record = {item.symbol: item for item in batch.items}["510300"]
        self.assertEqual(record.product_type, ListedFundProductType.UNKNOWN)

    def test_empty_official_source_is_not_a_real_empty_registry(self):
        batch = self.fetch(EtfProductMasterProviders(
            sse_products=lambda: pd.DataFrame(),
            sse_categories=sse_categories,
            szse_products=szse_products,
        ))

        self.assertIsNone(batch.meta.expected_count)
        self.assertIn(
            "empty_source_result",
            {issue.code for issue in batch.meta.issues},
        )


if __name__ == "__main__":
    unittest.main()
