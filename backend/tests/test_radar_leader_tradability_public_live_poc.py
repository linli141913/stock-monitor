import unittest
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from radar.contracts import (
    QuoteSnapshot,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
)
from radar.leader_tradability_features import (
    SecurityLifecycleStatus,
    TradingSessionStatus,
)
from radar.sources.leader_tradability_public_live_poc import (
    PublicCalendarDocument,
    PublicLivePocSourceBundle,
    PublicLivePocSourceError,
    build_public_aggregator_observations,
    build_public_calendar_evidence,
    build_public_quote_evidence,
    build_public_security_contexts,
    run_public_live_poc,
    validate_public_live_symbols,
    _fetch_calendar_document,
    _fetch_eastmoney_st_frame,
    _fetch_sina_lifecycle_frame,
)
from radar.sources.leader_tradability_public_poc import (
    PublicCompositePocStatus,
    PublicCompositeTradabilityQuery,
    PublicQuoteBatchEvidence,
    PublicSecurityContext,
    quote_batch_content_sha256,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 3, 10, 0, tzinfo=SHANGHAI_TZ)
SYMBOL = "000725"


class PublicLiveSymbolScopeTests(unittest.TestCase):
    def test_beijing_exchange_symbol_is_rejected_before_collection(self):
        self.assertIsNone(validate_public_live_symbols(("920023",)))


def digest(value="fixture"):
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def context():
    return PublicSecurityContext(
        symbol=SYMBOL,
        exchange="szse",
        board="主板",
        listing_date=date(2001, 1, 12),
        identity_source_contract_id="szse-public-security-list-v1",
        identity_source_name="深圳证券交易所",
        identity_source_url=(
            "https://www.szse.cn/market/product/stock/list/index.html"
        ),
        identity_document_id="szse-security-list-20260803",
        identity_source_time=AS_OF,
        identity_fetched_at=AS_OF,
        identity_content_sha256=digest("identity"),
    )


def calendar():
    return build_public_calendar_evidence(
        document=PublicCalendarDocument(
            source_url=(
                "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
            ),
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
            source_time=AS_OF,
            fetched_at=AS_OF,
        ),
        trading_date=AS_OF.date(),
        exchanges=("szse",),
    )[0]


def quote():
    return QuoteSnapshot(
        symbol=SYMBOL,
        name="京东方A",
        sourceTime=AS_OF,
        fetchedAt=AS_OF,
        source="tencent_finance",
        price=4.2,
        previousClose=4.1,
        upperLimitPriceSource=4.51,
        lowerLimitPriceSource=3.69,
    )


def bundle(source_statuses=None):
    quote_value = quote()
    batch_id = "public-live-quote-20260803T100000"
    return PublicLivePocSourceBundle(
        query=PublicCompositeTradabilityQuery(
            trading_date=AS_OF.date(),
            as_of=AS_OF,
            securities=(context(),),
            trading_calendars=(calendar(),),
            quote_batch_evidence=PublicQuoteBatchEvidence(
                source_contract_id="tencent-quote-snapshot-v1",
                source_name="腾讯财经",
                source_url="https://qt.gtimg.cn/",
                batch_id=batch_id,
                content_sha256=quote_batch_content_sha256(
                    batch_id=batch_id,
                    quotes=(quote_value,),
                ),
            ),
        ),
        quotes=(quote_value,),
        source_statuses=source_statuses or {
            "securityMaster": "completed",
            "tradingCalendar": "completed",
            "tencentQuote": "completed",
            "publicAggregator": "not_available",
        },
    )


class PublicLivePocTests(unittest.TestCase):
    def test_quote_evidence_can_select_the_candidate_subset(self):
        quotes = [
            quote(),
            QuoteSnapshot(
                symbol="600000",
                name="浦发银行",
                sourceTime=AS_OF,
                fetchedAt=AS_OF,
                source="tencent_finance",
                price=8.5,
            ),
        ]
        batch = SourceBatch(
            meta=RadarBatchMeta(
                radarRunId="run-1",
                batchId="quote-1",
                source="tencent_finance",
                asOf=AS_OF,
                sourceTime=AS_OF,
                fetchedAt=AS_OF,
                expectedCount=2,
                returnedCount=2,
                rowCoverage=1.0,
            ),
            items=quotes,
        )

        selected, evidence = build_public_quote_evidence(
            batch,
            symbols=(SYMBOL,),
        )

        self.assertEqual(tuple(item.symbol for item in selected), (SYMBOL,))
        self.assertEqual(
            evidence.content_sha256,
            quote_batch_content_sha256(
                batch_id="quote-1",
                quotes=selected,
            ),
        )

    def test_invalid_query_never_calls_collector(self):
        calls = []

        result = run_public_live_poc(
            symbols=(SYMBOL, SYMBOL),
            as_of=AS_OF,
            collector=lambda symbols, as_of: calls.append(symbols),
        )

        self.assertEqual(result.execution_status, "blocked")
        self.assertEqual(result.real_poc_status, "failed")
        self.assertEqual(result.reason, "public_live_query_invalid")
        self.assertEqual(calls, [])

    def test_source_error_is_redacted_and_stable(self):
        def failed_collector(symbols, as_of):
            raise PublicLivePocSourceError(
                "public_live_security_master_incomplete",
                private_detail="https://upstream/?token=secret-value",
            )

        result = run_public_live_poc(
            symbols=(SYMBOL,),
            as_of=AS_OF,
            collector=failed_collector,
        )
        evidence = result.to_evidence()

        self.assertEqual(result.execution_status, "blocked")
        self.assertEqual(
            result.reason,
            "public_live_security_master_incomplete",
        )
        self.assertNotIn("secret-value", str(evidence))
        self.assertNotIn("upstream", str(evidence))

    def test_fixture_collection_cannot_claim_real_poc_completion(self):
        result = run_public_live_poc(
            symbols=(SYMBOL,),
            as_of=AS_OF,
            collector=lambda symbols, as_of: bundle(),
        )
        evidence = result.to_evidence()

        self.assertEqual(result.execution_status, "completed")
        self.assertEqual(result.real_poc_status, "not_run")
        self.assertIsNotNone(result.report)
        self.assertIn(
            result.report.fixture_resolution_status,
            {
                PublicCompositePocStatus.PARTIAL,
                PublicCompositePocStatus.BLOCKED,
            },
        )
        self.assertEqual(
            evidence["resolutionStatus"],
            result.report.fixture_resolution_status.value,
        )
        self.assertFalse(evidence["formalScoreReady"])
        self.assertFalse(evidence["formalGateReady"])
        self.assertFalse(evidence["formalUsable"])
        self.assertFalse(evidence["stateTransitionAllowed"])
        self.assertNotIn("records", str(evidence))

    def test_collector_contract_mismatch_is_blocked(self):
        wrong = bundle()
        wrong = PublicLivePocSourceBundle(
            query=wrong.query,
            quotes=wrong.quotes,
            requested_symbols=("600000",),
            source_statuses=wrong.source_statuses,
        )

        result = run_public_live_poc(
            symbols=(SYMBOL,),
            as_of=AS_OF,
            collector=lambda symbols, as_of: wrong,
        )

        self.assertEqual(result.execution_status, "blocked")
        self.assertEqual(
            result.reason,
            "public_live_collector_contract_mismatch",
        )

    def test_collection_completion_time_may_follow_gate_time(self):
        later = AS_OF.replace(second=10)
        collected = bundle()
        collected = replace(
            collected,
            query=replace(collected.query, as_of=later),
        )

        result = run_public_live_poc(
            symbols=(SYMBOL,),
            as_of=AS_OF,
            collector=lambda symbols, as_of: collected,
        )

        self.assertEqual(result.execution_status, "completed")
        self.assertEqual(result.as_of, later)
        self.assertEqual(result.to_evidence()["asOf"], later.isoformat())

    def test_live_module_does_not_import_rqdata_or_database(self):
        from pathlib import Path
        import radar.sources.leader_tradability_public_live_poc as module

        source = Path(module.__file__).read_text(encoding="utf-8")

        self.assertNotIn("leader_tradability_rqdata", source)
        self.assertNotIn("sqlite", source.lower())

    def test_security_master_row_is_wrapped_with_actual_official_endpoint(self):
        meta = RadarBatchMeta(
            radarRunId="run-1",
            batchId="master-1",
            source="official_exchange_security_master",
            asOf=AS_OF,
            sourceTime=None,
            fetchedAt=AS_OF,
            expectedCount=1,
            returnedCount=1,
            rowCoverage=1.0,
        )
        batch = SourceBatch(
            meta=meta,
            items=[SecurityMasterRecord(
                symbol="600000",
                name="浦发银行",
                exchange="sse",
                board="主板A股",
                listingDate=date(1999, 11, 10),
                source="sse",
                fetchedAt=AS_OF,
                sourceFields={"证券代码": "600000"},
            )],
        )

        values = build_public_security_contexts(
            batch=batch,
            symbols=("600000",),
        )

        self.assertEqual(
            values[0].identity_source_url,
            "https://query.sse.com.cn/sseQuery/commonQuery.do",
        )
        self.assertEqual(
            values[0].identity_source_contract_id,
            "sse-public-security-list-v1",
        )
        self.assertIsNone(values[0].identity_source_time)
        self.assertTrue(values[0].identity_content_sha256.startswith(
            "sha256:"
        ))

    def test_security_master_batch_cannot_self_label_as_official(self):
        meta = RadarBatchMeta(
            radarRunId="run-1",
            batchId="master-1",
            source="aggregator",
            asOf=AS_OF,
            sourceTime=None,
            fetchedAt=AS_OF,
            expectedCount=1,
            returnedCount=1,
            rowCoverage=1.0,
        )
        batch = SourceBatch(
            meta=meta,
            items=[SecurityMasterRecord(
                symbol="600000",
                name="浦发银行",
                exchange="sse",
                board="主板A股",
                listingDate=date(1999, 11, 10),
                source="sse",
                fetchedAt=AS_OF,
                sourceFields={"证券代码": "600000"},
            )],
        )

        with self.assertRaisesRegex(
            PublicLivePocSourceError,
            "public_live_security_master_contract_unverified",
        ):
            build_public_security_contexts(
                batch=batch,
                symbols=("600000",),
            )

    def test_security_master_row_must_retain_official_source_identity(self):
        meta = RadarBatchMeta(
            radarRunId="run-1",
            batchId="master-1",
            source="official_exchange_security_master",
            asOf=AS_OF,
            sourceTime=None,
            fetchedAt=AS_OF,
            expectedCount=1,
            returnedCount=1,
            rowCoverage=1.0,
        )
        batch = SourceBatch(
            meta=meta,
            items=[SecurityMasterRecord(
                symbol="600000",
                name="浦发银行",
                exchange="sse",
                board="主板A股",
                listingDate=date(1999, 11, 10),
                source="akshare",
                fetchedAt=AS_OF,
                sourceFields={"证券代码": "000001"},
            )],
        )

        with self.assertRaisesRegex(
            PublicLivePocSourceError,
            "public_live_security_master_contract_unverified",
        ):
            build_public_security_contexts(
                batch=batch,
                symbols=("600000",),
            )

    def test_calendar_without_upstream_timestamp_keeps_source_time_missing(self):
        class Response:
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            content = b"official calendar"
            headers = {}

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            trust_env = True

            @staticmethod
            def get(*args, **kwargs):
                return Response()

        document = _fetch_calendar_document(
            as_of=AS_OF,
            session=Session(),
        )

        self.assertIsNone(document.source_time)

    def test_calendar_uses_official_annual_notice_date_when_header_missing(self):
        class Response:
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            content = """
                <strong>2026年休市安排</strong>
                <a title="关于上海证券交易所2026年部分节假日休市安排的通知">
                    关于上海证券交易所2026年部分节假日休市安排的通知
                </a>
                <span>2025-12-22</span>
            """.encode("utf-8")
            headers = {}

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            trust_env = True

            @staticmethod
            def get(*args, **kwargs):
                return Response()

        document = _fetch_calendar_document(
            as_of=AS_OF,
            session=Session(),
        )

        self.assertEqual(
            document.source_time,
            datetime(2025, 12, 22, tzinfo=SHANGHAI_TZ),
        )

    def test_calendar_retries_once_when_first_page_lacks_source_time(self):
        incomplete = b"<strong>2026\xe5\xb9\xb4\xe4\xbc\x91\xe5\xb8\x82\xe5\xae\x89\xe6\x8e\x92</strong>"
        complete = """
            <a title="关于上海证券交易所2026年部分节假日休市安排的通知">
                关于上海证券交易所2026年部分节假日休市安排的通知
            </a>
            <span>2025-12-22</span>
        """.encode("utf-8")

        class Response:
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            headers = {}

            def __init__(self, content):
                self.content = content

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            trust_env = True

            def __init__(self):
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return Response(
                    incomplete if self.calls == 1 else complete
                )

        session = Session()
        document = _fetch_calendar_document(
            as_of=AS_OF,
            session=session,
        )

        self.assertEqual(session.calls, 2)
        self.assertEqual(
            document.source_time,
            datetime(2025, 12, 22, tzinfo=SHANGHAI_TZ),
        )

    def test_eastmoney_st_fetch_retains_row_source_time(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "rc": 0,
                    "data": {
                        "total": 1,
                        "diff": [{
                            "f12": SYMBOL,
                            "f14": "*ST京东方",
                            "f124": int(AS_OF.timestamp()),
                        }],
                    },
                }

        class Session:
            def __init__(self):
                self.trust_env = True

            @staticmethod
            def get(*args, **kwargs):
                return Response()

        session = Session()
        frame = _fetch_eastmoney_st_frame(session=session)

        self.assertFalse(session.trust_env)
        self.assertEqual(frame.iloc[0]["代码"], SYMBOL)
        self.assertEqual(frame.iloc[0]["名称"], "*ST京东方")
        self.assertEqual(frame.iloc[0]["上游时间"], AS_OF)

    def test_sina_lifecycle_fetch_preserves_full_scope_and_source_time(self):
        class Response:
            encoding = None
            text = (
                'var hq_str_sz000725="京东方Ａ,0,0,0,0,0,0,0,0,0,0,'
                '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
                '2026-08-03,10:00:00,00";\n'
                'var hq_str_sh600000="ST浦发,0,0,0,0,0,0,0,0,0,0,'
                '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
                '2026-08-03,10:00:01,03";'
            )

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            def __init__(self):
                self.trust_env = True
                self.params = None

            def get(self, *args, **kwargs):
                self.params = kwargs.get("params")
                return Response()

        session = Session()
        contexts = (
            context(),
            replace(
                context(),
                symbol="600000",
                exchange="sse",
                identity_source_contract_id=(
                    "sse-public-security-list-v1"
                ),
            ),
        )

        frame = _fetch_sina_lifecycle_frame(
            contexts=contexts,
            session=session,
            clock=lambda: AS_OF,
        )

        self.assertFalse(session.trust_env)
        self.assertEqual(tuple(frame["代码"]), (SYMBOL, "600000"))
        self.assertEqual(tuple(frame["名称"]), ("京东方Ａ", "ST浦发"))
        self.assertEqual(tuple(frame["状态代码"]), ("00", "03"))
        self.assertEqual(frame.iloc[0]["上游时间"], AS_OF)
        self.assertEqual(
            session.params,
            "list=sz000725,sh600000",
        )

    def test_sina_lifecycle_fetch_fails_closed_on_missing_symbol(self):
        class Response:
            encoding = None
            text = (
                'var hq_str_sz000725="京东方Ａ,0,0,0,0,0,0,0,0,0,0,'
                '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
                '2026-08-03,10:00:00,00";'
            )

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            trust_env = True

            @staticmethod
            def get(*args, **kwargs):
                return Response()

        contexts = (
            context(),
            replace(
                context(),
                symbol="600000",
                exchange="sse",
                identity_source_contract_id=(
                    "sse-public-security-list-v1"
                ),
            ),
        )

        with self.assertRaisesRegex(
            PublicLivePocSourceError,
            "public_live_sina_lifecycle_incomplete",
        ):
            _fetch_sina_lifecycle_frame(
                contexts=contexts,
                session=Session(),
                clock=lambda: AS_OF,
            )

    def test_sina_names_and_status_emit_verified_fields_only(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=None,
            suspension_frame=None,
            lifecycle_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "*ST京东方",
                "上游时间": AS_OF,
                "抓取时间": AS_OF,
                "状态代码": "03",
            }]),
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(
            values[0].source_contract_id,
            "sina-public-quote-status-v1",
        )
        self.assertEqual(
            values[0].lifecycle_status,
            SecurityLifecycleStatus.STAR_ST,
        )
        self.assertEqual(
            values[0].trading_status,
            TradingSessionStatus.SUSPENDED,
        )
        self.assertIsNone(values[0].special_session)

    def test_sina_normal_status_maps_to_trading(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=None,
            suspension_frame=None,
            lifecycle_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "京东方Ａ",
                "上游时间": AS_OF,
                "抓取时间": AS_OF,
                "状态代码": "00",
            }]),
        )

        self.assertEqual(
            values[0].trading_status,
            TradingSessionStatus.TRADING,
        )
        self.assertEqual(
            values[0].lifecycle_status,
            SecurityLifecycleStatus.NORMAL,
        )

    def test_sina_lifecycle_fetch_rejects_unknown_status_code(self):
        class Response:
            encoding = None
            text = (
                'var hq_str_sz000725="京东方Ａ,0,0,0,0,0,0,0,0,0,0,'
                '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
                '2026-08-03,10:00:00,99";'
            )

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            trust_env = True

            @staticmethod
            def get(*args, **kwargs):
                return Response()

        with self.assertRaisesRegex(
            PublicLivePocSourceError,
            "public_live_sina_lifecycle_incomplete",
        ):
            _fetch_sina_lifecycle_frame(
                contexts=(context(),),
                session=Session(),
                clock=lambda: AS_OF,
            )

    def test_static_aggregator_rows_keep_conservative_field_scope(self):
        suspension = pd.DataFrame([{
            "代码": SYMBOL,
            "名称": "京东方A",
            "停牌时间": date(2026, 8, 3),
            "停牌截止时间": date(2026, 8, 3),
            "预计复牌时间": date(2026, 8, 4),
        }])
        st = pd.DataFrame([{
            "代码": SYMBOL,
            "名称": "*ST京东方",
        }])

        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=st,
            suspension_frame=suspension,
            source_time=AS_OF,
            upstream_source_time=AS_OF,
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(
            values[0].source_contract_id,
            "akshare-public-static-status-v1",
        )
        self.assertEqual(
            values[0].trading_status,
            TradingSessionStatus.SUSPENDED,
        )
        self.assertIsNone(values[0].lifecycle_status)
        self.assertNotIn("?", values[0].upstream_source_url)

    def test_st_board_row_never_implies_trading_status(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "*ST京东方",
            }]),
            suspension_frame=pd.DataFrame(),
            source_time=AS_OF,
            upstream_source_time=AS_OF,
        )

        self.assertEqual(
            values[0].lifecycle_status,
            SecurityLifecycleStatus.STAR_ST,
        )
        self.assertIsNone(values[0].trading_status)

    def test_st_board_row_can_use_its_embedded_upstream_time(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "*ST京东方",
                "上游时间": AS_OF,
            }]),
            suspension_frame=pd.DataFrame(),
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].source_time, AS_OF)
        self.assertEqual(values[0].upstream_source_time, AS_OF)
        self.assertEqual(
            values[0].source_contract_id,
            "eastmoney-public-static-status-v1",
        )

    def test_aggregator_without_source_times_emits_no_observation(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "*ST京东方",
            }]),
            suspension_frame=pd.DataFrame(),
        )

        self.assertEqual(values, ())

    def test_resuming_today_is_not_reported_as_suspended(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=pd.DataFrame(),
            suspension_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "京东方A",
                "停牌时间": date(2026, 8, 1),
                "停牌截止时间": date(2026, 8, 3),
                "预计复牌时间": date(2026, 8, 3),
            }]),
            source_time=AS_OF,
            upstream_source_time=AS_OF,
        )

        self.assertEqual(values, ())

    def test_same_day_suspension_without_resume_time_stays_unknown(self):
        values = build_public_aggregator_observations(
            contexts=(context(),),
            trading_date=AS_OF.date(),
            fetched_at=AS_OF,
            st_frame=pd.DataFrame(),
            suspension_frame=pd.DataFrame([{
                "代码": SYMBOL,
                "名称": "京东方A",
                "停牌时间": date(2026, 8, 3),
                "停牌截止时间": date(2026, 8, 3),
                "预计复牌时间": None,
            }]),
            source_time=AS_OF,
            upstream_source_time=AS_OF,
        )

        self.assertEqual(values, ())

    def test_calendar_builder_emits_six_dates_for_both_exchanges(self):
        document = PublicCalendarDocument(
            source_url=(
                "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
            ),
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
            source_time=AS_OF,
            fetched_at=AS_OF,
        )

        values = build_public_calendar_evidence(
            document=document,
            trading_date=AS_OF.date(),
            exchanges=("sse", "szse"),
        )

        self.assertEqual(tuple(item.exchange for item in values), (
            "sse", "szse"
        ))
        self.assertEqual(values[0].trading_dates, (
            date(2026, 7, 27),
            date(2026, 7, 28),
            date(2026, 7, 29),
            date(2026, 7, 30),
            date(2026, 7, 31),
            date(2026, 8, 3),
        ))
        self.assertEqual(
            values[0].content_sha256,
            values[1].content_sha256,
        )
        self.assertEqual(
            values[0].source_contract_id,
            "sse-a-share-trading-calendar-v1",
        )


if __name__ == "__main__":
    unittest.main()
