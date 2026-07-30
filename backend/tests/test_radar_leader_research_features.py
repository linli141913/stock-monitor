import unittest
from datetime import datetime, timedelta, timezone

from radar.contracts import QuoteSnapshot, SecurityMasterRecord
from radar.leader_research_features import (
    LEADER_RESEARCH_FEATURE_VERSION,
    LeaderResearchDimension,
    LeaderResearchFeatureResult,
    ResearchFeatureStatus,
    average_tie_percentile,
    build_leader_research_features,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 27, 2, 30, tzinfo=UTC)


class LeaderResearchFeatureContractTests(unittest.TestCase):
    @staticmethod
    def fixture(
        *,
        candidate_volume_ratio=2.0,
        candidate_sector_equal_return=3.0,
        candidate_sector_ex_top_return=2.0,
        candidate_sector_up_ratio=0.8,
    ):
        market_quotes = []
        security_by_symbol = {}
        for index in range(1, 101):
            symbol = f"{index:06d}"
            change_percent = (
                6.0 - index
                if index <= 6
                else 0.1
            )
            volume_ratio = (
                candidate_volume_ratio
                if index == 1
                else 1.0
            )
            market_quotes.append(QuoteSnapshot(
                symbol=symbol,
                name=f"证券{index}",
                sourceTime=AS_OF - timedelta(seconds=20),
                fetchedAt=AS_OF,
                price=10.0,
                changePercent=change_percent,
                turnoverAmountSource=1000.0,
                turnoverRatePercent=1.0,
                volumeRatio=volume_ratio,
                marketCapSource=100.0,
            ))
            security_by_symbol[symbol] = SecurityMasterRecord(
                symbol=symbol,
                name=f"证券{index}",
                exchange="szse",
                board="主板",
                listingDate="2000-01-01",
                source="szse",
                fetchedAt=AS_OF,
            )

        sector_rows = []
        for index in range(20):
            is_candidate_sector = index == 0
            sector_rows.append({
                "radarRunId": "sector-run",
                "divisionCode": f"{index + 1:02d}",
                "divisionName": f"行业{index + 1}",
                "asOf": AS_OF,
                "sourceTime": AS_OF - timedelta(seconds=20),
                "fetchedAt": AS_OF,
                "isComplete": True,
                "shadowUsable": True,
                "equalReturn": (
                    candidate_sector_equal_return
                    if is_candidate_sector
                    else index / 10
                ),
                "upRatio": (
                    candidate_sector_up_ratio
                    if is_candidate_sector
                    else 0.5
                ),
                "exTopReturn": (
                    candidate_sector_ex_top_return
                    if is_candidate_sector
                    else index / 20
                ),
            })

        market_snapshot = {
            "radarRunId": "market-run",
            "asOf": AS_OF,
            "sourceTime": AS_OF - timedelta(seconds=20),
            "fetchedAt": AS_OF,
            "indices": (
                {"indexKey": "sse_composite", "changePercent": 0.0},
                {"indexKey": "szse_component", "changePercent": 0.0},
                {"indexKey": "chinext", "changePercent": 0.0},
                {"indexKey": "star50", "changePercent": 0.0},
            ),
        }
        return {
            "as_of": AS_OF,
            "candidate_quote": market_quotes[0],
            "candidate_security": security_by_symbol["000001"],
            "industry_quotes": tuple(market_quotes[:6]),
            "market_quotes": tuple(market_quotes),
            "security_by_symbol": security_by_symbol,
            "sector": sector_rows[0],
            "sector_rows": tuple(sector_rows),
            "market_snapshot": market_snapshot,
            "sector_source_contract_id": "sector:01",
            "market_source_contract_id": "market:run",
            "quote_source_contract_id": "quote:run",
        }

    def test_average_tie_percentile_preserves_ties_and_real_zero(self):
        self.assertEqual(
            average_tie_percentile(0.0, (0.0, 1.0, 2.0)),
            0.0,
        )
        self.assertEqual(
            average_tie_percentile(1.0, (0.0, 1.0, 1.0, 2.0)),
            0.5,
        )
        self.assertEqual(
            average_tie_percentile(1.0, (1.0, 1.0, 1.0)),
            0.5,
        )
        self.assertIsNone(
            average_tie_percentile(1.0, (1.0,))
        )

    def test_partial_research_result_is_never_formal_score_ready(self):
        result = LeaderResearchFeatureResult(
            dimensions=(
                LeaderResearchDimension(
                    field_name="industry_strength",
                    maximum_score=25.0,
                    research_score=0.0,
                    status=ResearchFeatureStatus.READY,
                ),
            ),
        )

        payload = result.to_evidence()

        self.assertEqual(
            payload["formulaVersion"],
            LEADER_RESEARCH_FEATURE_VERSION,
        )
        self.assertEqual(payload["researchPartialScore"], 0.0)
        self.assertEqual(payload["participatingWeight"], 55.0)
        self.assertEqual(payload["requiredFormalWeight"], 95.0)
        self.assertFalse(payload["scoreReady"])

    def test_current_cross_section_builds_three_research_dimensions(self):
        result = build_leader_research_features(**self.fixture())

        self.assertAlmostEqual(
            result.dimension("industry_strength").research_score,
            23.4,
        )
        self.assertEqual(
            result.dimension("market_leadership").research_score,
            25.0,
        )
        self.assertEqual(
            result.dimension("auxiliary").research_score,
            5.0,
        )
        self.assertFalse(result.to_evidence()["scoreReady"])

    def test_negative_sector_returns_do_not_receive_rank_score(self):
        result = build_leader_research_features(**self.fixture(
            candidate_sector_equal_return=-0.1,
            candidate_sector_ex_top_return=-0.2,
            candidate_sector_up_ratio=0.25,
        ))

        dimension = result.dimension("industry_strength")

        self.assertEqual(dimension.research_score, 2.0)
        components = {
            component.name: component
            for component in dimension.components
        }
        self.assertEqual(components["equal_return"].score, 0.0)
        self.assertEqual(components["ex_top_return"].score, 0.0)

    def test_volume_ratio_at_or_below_one_is_real_zero(self):
        result = build_leader_research_features(**self.fixture(
            candidate_volume_ratio=1.0,
        ))

        dimension = result.dimension("auxiliary")

        self.assertEqual(dimension.status, ResearchFeatureStatus.READY)
        self.assertEqual(dimension.research_score, 0.0)

    def test_insufficient_sector_population_keeps_industry_missing(self):
        fixture = self.fixture()
        fixture["sector_rows"] = fixture["sector_rows"][:19]

        result = build_leader_research_features(**fixture)

        dimension = result.dimension("industry_strength")
        self.assertEqual(dimension.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(dimension.research_score)
        self.assertIn("sector_population_below_20", dimension.reasons)

    def test_stale_market_snapshot_keeps_leadership_missing(self):
        fixture = self.fixture()
        fixture["market_snapshot"] = {
            **fixture["market_snapshot"],
            "sourceTime": AS_OF - timedelta(seconds=91),
        }

        result = build_leader_research_features(**fixture)

        dimension = result.dimension("market_leadership")
        self.assertEqual(dimension.status, ResearchFeatureStatus.STALE)
        self.assertIsNone(dimension.research_score)
        self.assertIn("market_snapshot_stale", dimension.reasons)

    def test_non_finite_sector_value_is_missing_not_zero(self):
        result = build_leader_research_features(**self.fixture(
            candidate_sector_up_ratio=float("nan"),
        ))

        dimension = result.dimension("industry_strength")

        self.assertEqual(dimension.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(dimension.research_score)
        self.assertIn("target_sector_fields_missing", dimension.reasons)

    def test_duplicate_sector_code_does_not_satisfy_unique_population(self):
        fixture = self.fixture()
        rows = list(fixture["sector_rows"])
        rows[-1] = {
            **rows[-1],
            "divisionCode": rows[-2]["divisionCode"],
        }
        fixture["sector_rows"] = tuple(rows)

        result = build_leader_research_features(**fixture)

        dimension = result.dimension("industry_strength")
        self.assertEqual(dimension.status, ResearchFeatureStatus.MISSING)
        self.assertIn("sector_division_duplicated", dimension.reasons)

    def test_mixed_sector_run_is_source_unverified(self):
        fixture = self.fixture()
        rows = list(fixture["sector_rows"])
        rows[-1] = {
            **rows[-1],
            "radarRunId": "different-sector-run",
        }
        fixture["sector_rows"] = tuple(rows)

        result = build_leader_research_features(**fixture)

        dimension = result.dimension("industry_strength")
        self.assertEqual(
            dimension.status,
            ResearchFeatureStatus.SOURCE_UNVERIFIED,
        )
        self.assertIn("sector_run_mixed", dimension.reasons)

    def test_unknown_board_does_not_fall_back_to_broad_index(self):
        fixture = self.fixture()
        unknown = fixture["candidate_security"].model_copy(
            update={"board": "未知板块"}
        )
        fixture["candidate_security"] = unknown
        fixture["security_by_symbol"] = {
            **fixture["security_by_symbol"],
            unknown.symbol: unknown,
        }

        result = build_leader_research_features(**fixture)

        dimension = result.dimension("market_leadership")
        self.assertEqual(dimension.status, ResearchFeatureStatus.MISSING)
        self.assertIn("board_unrecognized", dimension.reasons)

    def test_missing_market_cap_does_not_shrink_return_rank_population(self):
        fixture = self.fixture()
        industry_quotes = list(fixture["industry_quotes"])
        industry_quotes[1] = industry_quotes[1].model_copy(
            update={"market_cap_source": None}
        )
        fixture["industry_quotes"] = tuple(industry_quotes)

        result = build_leader_research_features(**fixture)

        component = {
            item.name: item
            for item in result.dimension(
                "market_leadership"
            ).components
        }["industry_return_rank"]
        self.assertEqual(component.population_size, 6)

    def test_small_industry_or_market_population_keeps_dimensions_missing(self):
        fixture = self.fixture()
        fixture["industry_quotes"] = fixture["industry_quotes"][:1]
        fixture["market_quotes"] = fixture["market_quotes"][:99]

        result = build_leader_research_features(**fixture)

        self.assertIn(
            "industry_population_below_2",
            result.dimension("market_leadership").reasons,
        )
        self.assertIn(
            "volume_ratio_population_below_100",
            result.dimension("auxiliary").reasons,
        )

    def test_missing_volume_ratio_is_not_real_zero(self):
        fixture = self.fixture()
        missing_quote = fixture["candidate_quote"].model_copy(
            update={"volume_ratio": None}
        )
        fixture["candidate_quote"] = missing_quote

        result = build_leader_research_features(**fixture)

        dimension = result.dimension("auxiliary")
        self.assertEqual(dimension.status, ResearchFeatureStatus.MISSING)
        self.assertIsNone(dimension.research_score)

    def test_four_exchange_board_pairs_use_their_own_index(self):
        cases = (
            ("sse", "主板A股", 1.0, 4.0),
            ("sse", "科创板", 2.0, 3.0),
            ("szse", "主板", 0.0, 5.0),
            ("szse", "创业板", 3.0, 2.0),
        )
        for exchange, board, index_change, expected_excess in cases:
            with self.subTest(exchange=exchange, board=board):
                fixture = self.fixture()
                security = fixture["candidate_security"].model_copy(
                    update={"exchange": exchange, "board": board}
                )
                fixture["candidate_security"] = security
                fixture["security_by_symbol"] = {
                    **fixture["security_by_symbol"],
                    security.symbol: security,
                }
                index_key = {
                    ("sse", "主板A股"): "sse_composite",
                    ("sse", "科创板"): "star50",
                    ("szse", "主板"): "szse_component",
                    ("szse", "创业板"): "chinext",
                }[(exchange, board)]
                fixture["market_snapshot"] = {
                    **fixture["market_snapshot"],
                    "indices": tuple(
                        {
                            **item,
                            "changePercent": (
                                index_change
                                if item["indexKey"] == index_key
                                else item["changePercent"]
                            ),
                        }
                        for item in fixture["market_snapshot"]["indices"]
                    ),
                }

                result = build_leader_research_features(**fixture)

                components = {
                    item.name: item
                    for item in result.dimension(
                        "market_leadership"
                    ).components
                }
                self.assertEqual(
                    components["board_excess_rank"].raw_value,
                    expected_excess,
                )


if __name__ == "__main__":
    unittest.main()
