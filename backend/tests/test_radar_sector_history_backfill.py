import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests

from radar.contracts import (
    IndustryClassificationRelease,
    IndustryHistoryStatus,
)
from radar.sector_history_backfill import (
    HistoricalDailyTradingProof,
    HistoricalMinuteBar,
    HistoricalMinuteSeries,
    SectorHistoryBackfillQuery,
    build_sector_history_backfill,
    parse_eastmoney_minute_payload,
    parse_sina_minute_payload,
)
from radar.sector_history_backfill_collector import (
    fetch_sector_history_minute_series_batch,
)
from radar.sector_history_trading_presence import (
    fetch_historical_trading_presence_batch,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 21, 16, 0, tzinfo=SHANGHAI_TZ)


def trade_dates(count=21, start=date(2026, 7, 20)):
    values = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def archived_release(knowledge_date=date(2026, 4, 3)):
    return IndustryClassificationRelease(
        schemeVersion="capco-guideline-2023-shadow",
        releasePeriod="2025H2",
        sourcePageTitle="2025年下半年上市公司行业分类结果",
        publicationPageUrl="https://www.capco.org.cn/result.html",
        documentUrl="https://sp.capco.org.cn:82/result.pdf",
        documentSha256="a" * 64,
        publishedDate=date(2026, 4, 3),
        firstObservedAt=datetime(2026, 8, 21, tzinfo=SHANGHAI_TZ),
        fetchedAt=AS_OF,
        knowledgeEffectiveFrom=datetime.combine(
            knowledge_date,
            time(),
            tzinfo=SHANGHAI_TZ,
        ),
        classificationStartDate=date(2025, 12, 20),
        historyStatus=IndustryHistoryStatus.OFFICIAL_ARCHIVE_VERIFIED,
        sourceRecordCount=40,
        uniqueSourceSymbolCount=40,
        requiredFieldCoverage={"division_code": 1.0},
    )


def fixtures(
    *,
    count=21,
    start=date(2026, 7, 20),
    alternating_market=False,
):
    dates = trade_dates(count, start)
    memberships = {}
    series = {}
    shares = {}
    for sector_index in range(20):
        division_code = f"{sector_index + 10:02d}"
        symbols = (
            f"{sector_index * 2 + 1:06d}",
            f"{sector_index * 2 + 2:06d}",
        )
        memberships[division_code] = symbols
        for member_index, symbol in enumerate(symbols):
            bars = []
            market_level = 1.0
            for day_index, trade_day in enumerate(dates):
                if day_index and alternating_market:
                    market_level *= 1.02 if day_index % 2 else 0.97
                close = (
                    100.0
                    * market_level
                    * (1.0 + (sector_index + 1) * 0.001) ** day_index
                )
                bars.extend((
                    HistoricalMinuteBar(
                        occurred_at=datetime.combine(
                            trade_day,
                            time(9, 35),
                            tzinfo=SHANGHAI_TZ,
                        ),
                        close=close,
                        turnover_amount_cny=0.0,
                    ),
                    HistoricalMinuteBar(
                        occurred_at=datetime.combine(
                            trade_day,
                            time(10, 0),
                            tzinfo=SHANGHAI_TZ,
                        ),
                        close=close,
                        turnover_amount_cny=(
                            1_000.0 + sector_index * 10 + member_index
                        ),
                    ),
                    HistoricalMinuteBar(
                        occurred_at=datetime.combine(
                            trade_day,
                            time(15, 0),
                            tzinfo=SHANGHAI_TZ,
                        ),
                        close=close,
                        turnover_amount_cny=9_000_000.0,
                    ),
                ))
            series[symbol] = HistoricalMinuteSeries(
                symbol=symbol,
                source_contract_id="eastmoney-a-share-5m-history-v1",
                source_url="https://push2his.eastmoney.com/api/qt/stock/kline/get",
                fetched_at=AS_OF - timedelta(seconds=1),
                content_sha256="sha256:" + f"{sector_index * 2 + member_index + 1:064x}",
                bars=tuple(bars),
            )
            shares[symbol] = float(1_000_000 + sector_index * 1000)
    return dates, memberships, series, shares


def query():
    dates, memberships, series, shares = fixtures()
    return SectorHistoryBackfillQuery(
        radar_run_id="sector-history-backfill-1",
        as_of=AS_OF,
        comparable_time=time(10, 0),
        expected_trade_dates=dates,
        classification_release=archived_release(),
        memberships_by_division=memberships,
        series_by_symbol=series,
        total_shares_by_symbol=shares,
        source_batch_ids=("eastmoney-minute-batch-1",),
    )


class SectorHistoryBackfillTests(unittest.TestCase):
    def test_parser_preserves_incremental_amount_and_source_time(self):
        payload = {
            "data": {
                "klines": [
                    "2026-08-20 09:35,10,10.1,10.2,9.9,100,1234.5,3,1,0.1,0.2",
                    "2026-08-20 09:40,10.1,10.2,10.3,10,110,1500,3,1,0.1,0.2",
                ]
            }
        }

        result = parse_eastmoney_minute_payload(
            symbol="000001",
            payload=payload,
            expected_trade_dates=(date(2026, 8, 20),),
            fetched_at=AS_OF,
        )

        self.assertEqual(len(result.bars), 2)
        self.assertEqual(result.bars[0].turnover_amount_cny, 1234.5)
        self.assertEqual(result.bars[-1].occurred_at.hour, 9)
        self.assertEqual(result.bars[-1].occurred_at.minute, 40)

    def test_sina_parser_preserves_real_five_minute_amount(self):
        result = parse_sina_minute_payload(
            symbol="000001",
            payload=[{
                "day": "2026-08-20 09:35:00",
                "open": "10.0",
                "high": "10.2",
                "low": "9.9",
                "close": "10.1",
                "volume": "100",
                "amount": "1234.5",
            }],
            expected_trade_dates=(date(2026, 8, 20),),
            fetched_at=AS_OF,
        )

        self.assertEqual(
            result.source_contract_id,
            "sina-a-share-5m-history-v1",
        )
        self.assertEqual(result.bars[0].turnover_amount_cny, 1234.5)
        self.assertEqual(result.bars[0].volume_shares, 100)

    def test_sina_parser_missing_volume_fails_closed_without_crashing(self):
        result = parse_sina_minute_payload(
            symbol="000001",
            payload=[{
                "day": "2026-08-20 09:35:00",
                "close": "10.1",
                "amount": "1234.5",
            }],
            expected_trade_dates=(date(2026, 8, 20),),
            fetched_at=AS_OF,
        )

        self.assertEqual(result.bars, ())

    def test_online_history_immediately_builds_20_day_and_5_day_evidence(self):
        result = build_sector_history_backfill(query())

        self.assertEqual(result.status, "ready", result.reasons)
        self.assertEqual(len(result.history_evidence.rows), 20)
        first = result.sector_analyses[0]
        self.assertEqual(len(first.same_minute_turnover_samples), 21)
        self.assertEqual(len(first.persistence_samples), 5)
        self.assertEqual(first.same_minute_turnover_samples[-1].value, 2001.0)
        self.assertEqual(len(result.market_samples), 20)
        self.assertFalse(result.formal_gate_ready)
        self.assertFalse(result.calibration_proposal.formal_approval)

    def test_long_online_window_builds_train_holdout_calibration_proposal(self):
        value = query()
        dates, memberships, series, shares = fixtures(
            count=45,
            start=date(2026, 6, 1),
            alternating_market=True,
        )

        result = build_sector_history_backfill(replace(
            value,
            expected_trade_dates=dates,
            memberships_by_division=memberships,
            series_by_symbol=series,
            total_shares_by_symbol=shares,
        ))

        proposal = result.calibration_proposal
        self.assertEqual(result.status, "ready", result.reasons)
        self.assertEqual(proposal.status, "proposal_ready", proposal.reasons)
        self.assertEqual(proposal.observation_date_count, 25)
        self.assertEqual(set(proposal.market_regimes), {"positive", "non_positive"})
        self.assertEqual(
            proposal.metric_sample_counts["turnoverRatio20d"],
            340,
        )
        self.assertEqual(
            proposal.holdout_metric_sample_counts["turnoverRatio20d"],
            160,
        )
        self.assertEqual(proposal.train_observation_date_count, 17)
        self.assertEqual(proposal.holdout_observation_date_count, 8)
        self.assertIsNotNone(proposal.train_end_date)
        self.assertIsNotNone(proposal.holdout_start_date)
        self.assertFalse(proposal.formal_approval)

    def test_missing_member_day_never_becomes_complete_sector_history(self):
        value = query()
        symbol = next(iter(value.series_by_symbol))
        series = value.series_by_symbol[symbol]
        missing_day = value.expected_trade_dates[5]
        incomplete = replace(
            series,
            bars=tuple(
                bar for bar in series.bars
                if bar.occurred_at.date() != missing_day
            ),
        )

        result = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: incomplete},
        ))

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.history_evidence)
        self.assertIn("sector_history_member_dates_incomplete", result.reasons)
        self.assertIn(
            f"sector_history_member_date_missing:{symbol}:{missing_day.isoformat()}",
            result.reasons,
        )

    def test_daily_presence_alone_cannot_verify_sparse_intraday_history(self):
        value = query()
        symbol = next(iter(value.series_by_symbol))
        series = value.series_by_symbol[symbol]
        target_day = value.expected_trade_dates[0]
        truncated = replace(
            series,
            bars=tuple(
                bar for bar in series.bars
                if not (
                    bar.occurred_at.date() == target_day
                    and bar.occurred_at.time() == time(9, 35)
                )
            ),
        )

        result = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: truncated},
            verified_trading_dates_by_symbol={symbol: (target_day,)},
        ))

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.history_evidence)
        self.assertIn("sector_history_member_dates_incomplete", result.reasons)
        self.assertIn(
            (
                "sector_history_member_intraday_unverified:"
                f"{symbol}:{target_day.isoformat()}"
            ),
            result.reasons,
        )

    def test_daily_presence_keeps_internal_zero_trade_interval_compatible(self):
        value = query()
        symbol = next(iter(value.series_by_symbol))
        series = value.series_by_symbol[symbol]
        target_day = value.expected_trade_dates[0]
        internal_gap = replace(
            series,
            bars=tuple(
                bar for bar in series.bars
                if not (
                    bar.occurred_at.date() == target_day
                    and bar.occurred_at.time() == value.comparable_time
                )
            ),
        )

        result = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: internal_gap},
            verified_trading_dates_by_symbol={symbol: (target_day,)},
        ))

        self.assertEqual(result.status, "ready", result.reasons)

    def test_exact_independent_daily_proof_verifies_sparse_intraday_history(self):
        value = query()
        symbol = next(iter(value.series_by_symbol))
        series = value.series_by_symbol[symbol]
        target_day = value.expected_trade_dates[0]
        sparse_bars = tuple(
            replace(bar, volume_shares=100)
            for bar in series.bars
            if not (
                bar.occurred_at.date() == target_day
                and bar.occurred_at.time() == time(9, 35)
            )
        )
        sparse = replace(
            series,
            source_contract_id="sina-a-share-5m-history-v1",
            source_url=(
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/"
                "CN_MarketDataService.getKLineData"
            ),
            bars=sparse_bars,
        )
        proof = HistoricalDailyTradingProof(
            trade_date=target_day,
            close=Decimal(str(sparse_bars[1].close)),
            volume_shares=200,
            source_contract_id="tencent-qfq-daily-trading-presence-v1",
            source_url="https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
            fetched_at=AS_OF - timedelta(seconds=1),
            content_sha256="sha256:" + "f" * 64,
        )

        result = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: sparse},
            verified_trading_dates_by_symbol={symbol: (target_day,)},
            daily_trading_proofs_by_symbol={symbol: (proof,)},
        ))

        self.assertEqual(result.status, "ready", result.reasons)
        self.assertEqual(
            result.sector_analyses[0].same_minute_turnover_samples[0].trade_date,
            target_day,
        )

    def test_sparse_intraday_proof_mismatch_remains_fail_closed(self):
        value = query()
        symbol = next(iter(value.series_by_symbol))
        series = value.series_by_symbol[symbol]
        target_day = value.expected_trade_dates[0]
        sparse_bars = tuple(
            replace(bar, volume_shares=100)
            for bar in series.bars
            if not (
                bar.occurred_at.date() == target_day
                and bar.occurred_at.time() == time(9, 35)
            )
        )
        sparse = replace(
            series,
            source_contract_id="sina-a-share-5m-history-v1",
            source_url=(
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/"
                "CN_MarketDataService.getKLineData"
            ),
            bars=sparse_bars,
        )
        proof = HistoricalDailyTradingProof(
            trade_date=target_day,
            close=Decimal(str(sparse_bars[1].close)),
            volume_shares=201,
            source_contract_id="tencent-qfq-daily-trading-presence-v1",
            source_url="https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
            fetched_at=AS_OF - timedelta(seconds=1),
            content_sha256="sha256:" + "e" * 64,
        )

        result = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: sparse},
            verified_trading_dates_by_symbol={symbol: (target_day,)},
            daily_trading_proofs_by_symbol={symbol: (proof,)},
        ))

        self.assertEqual(result.status, "partial")
        self.assertIn("sector_history_member_dates_incomplete", result.reasons)

        close_mismatch = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: sparse},
            verified_trading_dates_by_symbol={symbol: (target_day,)},
            daily_trading_proofs_by_symbol={
                symbol: (replace(
                    proof,
                    close=proof.close + Decimal("0.01"),
                    volume_shares=200,
                ),),
            },
        ))

        self.assertEqual(close_mismatch.status, "partial")
        self.assertIn(
            "sector_history_member_dates_incomplete",
            close_mismatch.reasons,
        )

    def test_daily_absence_verifies_suspension_and_excludes_member_day(self):
        value = query()
        symbol = next(iter(value.series_by_symbol))
        series = value.series_by_symbol[symbol]
        target_day = value.expected_trade_dates[5]
        incomplete = replace(
            series,
            bars=tuple(
                bar for bar in series.bars
                if bar.occurred_at.date() != target_day
            ),
        )

        result = build_sector_history_backfill(replace(
            value,
            series_by_symbol={**value.series_by_symbol, symbol: incomplete},
            verified_non_trading_dates_by_symbol={symbol: (target_day,)},
        ))

        self.assertEqual(result.status, "ready", result.reasons)

    def test_single_member_industry_cannot_enter_comparable_history(self):
        value = query()
        memberships = dict(value.memberships_by_division)
        target = next(iter(memberships))
        memberships[target] = memberships[target][:1]

        result = build_sector_history_backfill(replace(
            value,
            memberships_by_division=memberships,
        ))

        self.assertEqual(result.status, "partial")
        self.assertIn("sector_history_membership_unverified", result.reasons)

    def test_archive_effective_after_window_is_rejected_as_future_leakage(self):
        value = query()
        release = value.classification_release.model_copy(update={
            "published_date": date(2026, 7, 30),
            "knowledge_effective_from": datetime(
                2026, 7, 30, tzinfo=SHANGHAI_TZ,
            ),
        })

        result = build_sector_history_backfill(replace(
            value,
            classification_release=release,
        ))

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.history_evidence)
        self.assertIn("sector_history_predates_classification_knowledge", result.reasons)

    def test_minute_collector_deduplicates_symbols_and_freezes_complete_batch(self):
        calls = []
        dates = (date(2026, 8, 20),)

        def requester(symbol):
            calls.append(symbol)
            return {
                "data": {
                    "klines": [
                        "2026-08-20 09:35,10,10.1,10.2,9.9,100,1234.5,3,1,0.1,0.2"
                    ]
                }
            }

        batch = fetch_sector_history_minute_series_batch(
            ("000001", "000002", "000001"),
            dates,
            requester=requester,
            clock=lambda: AS_OF,
            max_workers=2,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(batch.failure_count, 0)
        self.assertEqual(set(batch.series_by_symbol), {"000001", "000002"})
        self.assertEqual(sorted(calls), ["000001", "000002"])

    def test_minute_collector_keeps_success_checkpoints_but_closes_failed_batch(self):
        def requester(symbol):
            if symbol == "000002":
                raise RuntimeError("secret-upstream-response")
            return {
                "data": {
                    "klines": [
                        "2026-08-20 09:35,10,10.1,10.2,9.9,100,1234.5,3,1,0.1,0.2"
                    ]
                }
            }

        batch = fetch_sector_history_minute_series_batch(
            ("000001", "000002"),
            (date(2026, 8, 20),),
            requester=requester,
            clock=lambda: AS_OF,
            max_workers=2,
        )

        self.assertEqual(batch.source_status, "source_failed")
        self.assertEqual(batch.failure_count, 1)
        self.assertEqual(
            batch.failure_reason_counts,
            {"request_failed": 1},
        )
        self.assertEqual(set(batch.series_by_symbol), {"000001"})
        self.assertNotIn("secret-upstream-response", repr(batch.to_evidence()))

    def test_minute_collector_classifies_rate_limit_without_response_body(self):
        response = requests.Response()
        response.status_code = 429

        def requester(_symbol):
            raise requests.HTTPError(
                "secret response body",
                response=response,
            )

        batch = fetch_sector_history_minute_series_batch(
            ("000001",),
            (date(2026, 8, 20),),
            requester=requester,
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.failure_reason_counts, {"rate_limited": 1})
        self.assertNotIn("secret response body", repr(batch.to_evidence()))

    def test_sina_nonstandard_456_is_classified_as_rate_limited(self):
        response = requests.Response()
        response.status_code = 456

        def requester(_symbol):
            raise requests.HTTPError(
                "private limit page",
                response=response,
            )

        batch = fetch_sector_history_minute_series_batch(
            ("000001",),
            (date(2026, 8, 20),),
            requester=requester,
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.failure_reason_counts, {"rate_limited": 1})
        self.assertNotIn("private limit page", repr(batch.to_evidence()))

    def test_default_collector_path_can_freeze_sina_fallback_contract(self):
        batch = fetch_sector_history_minute_series_batch(
            ("000001",),
            (date(2026, 8, 20),),
            requester=None,
            sina_requester=lambda _symbol: [{
                "day": "2026-08-20 09:35:00",
                "open": "10.0",
                "high": "10.2",
                "low": "9.9",
                "close": "10.1",
                "volume": "100",
                "amount": "1234.5",
            }],
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(
            batch.series_by_symbol["000001"].source_contract_id,
            "sina-a-share-5m-history-v1",
        )

    def test_tencent_daily_presence_separates_trading_and_non_trading_dates(self):
        dates = (date(2026, 8, 19), date(2026, 8, 20))

        def requester(_query_symbol):
            return {
                "code": 0,
                "data": {
                    "sz000001": {
                        "qfqday": [
                            ["2026-08-18", "10", "10", "10", "10", "1"],
                            ["2026-08-20", "10", "10.1", "10.2", "9.9", "1"]
                            ,
                            ["2026-08-21", "10", "10", "10", "10", "1"],
                        ]
                    }
                },
            }

        batch = fetch_historical_trading_presence_batch(
            {"000001": dates},
            expected_trade_dates=dates,
            requester=requester,
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(
            batch.verified_trading_dates_by_symbol["000001"],
            (date(2026, 8, 20),),
        )
        self.assertEqual(
            batch.verified_non_trading_dates_by_symbol["000001"],
            (date(2026, 8, 19),),
        )
        proof = batch.daily_trading_proofs_by_symbol["000001"][0]
        self.assertEqual(proof.trade_date, date(2026, 8, 20))
        self.assertEqual(proof.close, Decimal("10.1"))
        self.assertEqual(proof.volume_shares, 100)

    def test_daily_presence_accepts_target_date_explicitly_returned_at_window_edge(self):
        target = date(2026, 8, 20)
        batch = fetch_historical_trading_presence_batch(
            {"000001": (target,)},
            expected_trade_dates=(target,),
            requester=lambda _query_symbol: {
                "code": 0,
                "data": {
                    "sz000001": {
                        "qfqday": [
                            ["2026-08-20", "10", "10", "10", "10", "1"]
                        ]
                    }
                },
            },
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(
            batch.verified_trading_dates_by_symbol["000001"],
            (target,),
        )
        self.assertEqual(
            batch.verified_non_trading_dates_by_symbol["000001"],
            (),
        )

    def test_star_market_daily_proof_preserves_tencent_share_unit(self):
        target = date(2026, 8, 20)
        batch = fetch_historical_trading_presence_batch(
            {"688001": (target,)},
            expected_trade_dates=(target,),
            requester=lambda _query_symbol: {
                "code": 0,
                "data": {
                    "sh688001": {
                        "qfqday": [[
                            "2026-08-20", "10", "10.1", "10.2", "9.9",
                            "445576",
                        ]],
                    },
                },
            },
            clock=lambda: AS_OF,
        )

        proof = batch.daily_trading_proofs_by_symbol["688001"][0]
        self.assertEqual(proof.volume_shares, 445576)

    def test_daily_presence_still_rejects_absent_target_without_enclosure(self):
        target = date(2026, 8, 20)
        batch = fetch_historical_trading_presence_batch(
            {"000001": (target,)},
            expected_trade_dates=(target,),
            requester=lambda _query_symbol: {
                "code": 0,
                "data": {
                    "sz000001": {
                        "qfqday": [
                            ["2026-08-19", "10", "10", "10", "10", "1"]
                        ]
                    }
                },
            },
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "source_failed")
        self.assertEqual(
            batch.failure_reason_counts,
            {"window_not_enclosed": 1},
        )
        self.assertEqual(batch.failed_symbols, ("000001",))

    def test_current_suspension_closes_trailing_daily_absence_window(self):
        targets = (
            date(2026, 8, 17),
            date(2026, 8, 18),
            date(2026, 8, 19),
            date(2026, 8, 20),
        )
        batch = fetch_historical_trading_presence_batch(
            {"000001": targets},
            expected_trade_dates=targets,
            terminal_non_trading_symbols=("000001",),
            requester=lambda _query_symbol: {
                "code": 0,
                "data": {
                    "sz000001": {
                        "qfqday": [
                            ["2026-08-14", "10", "10", "10", "10", "1"],
                        ]
                    }
                },
            },
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(
            batch.verified_non_trading_dates_by_symbol["000001"],
            targets,
        )

    def test_current_suspension_closes_only_tail_after_last_trading_day(self):
        targets = (
            date(2026, 8, 17),
            date(2026, 8, 18),
            date(2026, 8, 19),
            date(2026, 8, 20),
        )
        batch = fetch_historical_trading_presence_batch(
            {"000001": targets},
            expected_trade_dates=targets,
            terminal_non_trading_symbols=("000001",),
            requester=lambda _query_symbol: {
                "code": 0,
                "data": {
                    "sz000001": {
                        "qfqday": [
                            ["2026-08-16", "10", "10", "10", "10", "1"],
                            ["2026-08-17", "10", "10", "10", "10", "1"],
                            ["2026-08-18", "10", "10", "10", "10", "1"],
                        ]
                    }
                },
            },
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(
            batch.verified_trading_dates_by_symbol["000001"],
            targets[:2],
        )
        self.assertEqual(
            batch.verified_non_trading_dates_by_symbol["000001"],
            targets[2:],
        )

    def test_sina_daily_fallback_verifies_gap_when_tencent_has_no_rows(self):
        targets = (date(2026, 7, 16), date(2026, 7, 17))
        batch = fetch_historical_trading_presence_batch(
            {"688277": targets},
            expected_trade_dates=targets,
            requester=lambda _query_symbol: {
                "code": 0,
                "data": {"sh688277": {}},
            },
            sina_daily_requester=lambda _symbol: (
                date(2026, 7, 15),
                date(2026, 7, 30),
            ),
            clock=lambda: AS_OF,
        )

        self.assertEqual(batch.source_status, "ready")
        self.assertEqual(
            batch.verified_non_trading_dates_by_symbol["688277"],
            targets,
        )
        self.assertEqual(
            batch.source_contract_ids_by_symbol["688277"],
            "sina-daily-trading-presence-v1",
        )


if __name__ == "__main__":
    unittest.main()
