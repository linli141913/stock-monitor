import hashlib
import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar.contracts import (
    QuoteSnapshot,
    RadarBatchMeta,
    SourceBatch,
    SourceHealthResult,
    SourceStatus,
    UnitVerificationStatus,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 9, 7, 10, 0, tzinfo=SHANGHAI)
FETCHED_AT = AS_OF + timedelta(seconds=2)
OBSERVED_AT = AS_OF + timedelta(seconds=3)


def quote(
    symbol: str,
    *,
    source_time=AS_OF,
    fetched_at=FETCHED_AT,
    turnover=0.0,
    price=1.25,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        name=f"ETF-{symbol}",
        sourceTime=source_time,
        fetchedAt=fetched_at,
        price=price,
        changePercent=0.0,
        turnoverAmountSource=turnover,
        turnoverAmountCny=turnover,
        turnoverAmountUnitStatus=UnitVerificationStatus.VERIFIED,
        turnoverRatePercent=1.0,
        volumeRatio=1.0,
        marketCapSource=1.0,
    )


def batch(
    *items: QuoteSnapshot,
    source_time=AS_OF,
    batch_id="live-run-1-stage10-etf-live-quotes",
    as_of=AS_OF,
    fetched_at=FETCHED_AT,
) -> SourceBatch[QuoteSnapshot]:
    return SourceBatch[QuoteSnapshot](
        meta=RadarBatchMeta(
            radarRunId="live-run-1",
            batchId=batch_id,
            source="tencent_finance",
            asOf=as_of,
            sourceTime=source_time,
            fetchedAt=fetched_at,
            expectedCount=2,
            returnedCount=len(items),
            rowCoverage=len(items) / 2,
            requiredFieldCoverage={
                "price": 1.0 if len(items) == 2 else 0.0,
                "source_time": 1.0 if len(items) == 2 else 0.0,
            },
        ),
        items=list(items),
    )


def health(status=SourceStatus.HEALTHY) -> SourceHealthResult:
    return SourceHealthResult(
        status=status,
        allowsNewState=status == SourceStatus.HEALTHY,
        reasons=() if status == SourceStatus.HEALTHY else ("upstream",),
        ageSeconds=2.0 if status != SourceStatus.FAILED else None,
    )


def admission_bundle(
    symbols,
    *,
    as_of=FETCHED_AT,
    run_id="live-run-1",
    product_identity_status="ready",
):
    admissions = []
    for symbol in symbols:
        items = []
        for key in (
            "product_identity", "product_lifecycle", "industry_scope",
            "index_relation", "index_methodology", "index_constituents",
            "industry_exposure", "ranking_inputs", "rule_policy",
        ):
            status = product_identity_status if key == "product_identity" else "ready"
            items.append({
                "key": key,
                "status": status,
                "reasons": [] if status == "ready" else ["unverified"],
            })
        reasons = ["unverified"] if product_identity_status != "ready" else []
        admissions.append({
            "contractId": "radar-etf-formal-admission-evidence-v1",
            "symbol": symbol,
            "asOf": as_of.isoformat(),
            "ruleVersion": "radar-etf-rule-v1",
            "status": "ready" if not reasons else "missing",
            "monitoringStatus": "ready" if not reasons else "missing",
            "rankingStatus": "ready",
            "reasons": reasons,
            "items": items,
        })
    semantic = {
        "contractId": "radar-etf-formal-admission-bundle-v1",
        "sampleId": "sample-independent",
        "radarRunId": run_id,
        "asOf": as_of.isoformat(),
        "admissions": admissions,
    }
    return {
        **semantic,
        "snapshotSha256": __import__("hashlib").sha256(
            json.dumps(semantic, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def collection_policy():
    return {
        "contractId": "radar-formal-shadow-collection-policy-v1",
        "policyId": "stage10-shadow-collection-policy-v1",
        "policySha256": "d8e02f3ec075ed8c82a195ccd7f93ec80760e0a83cb98a3b709f9eaf182640a4",
        "receiptContractId": "radar-formal-shadow-run-receipt-v1",
        "maximumSourceAgeSecondsByModule": {
            "trendRotation": 90, "etfObservation": 90, "leaderObservation": 90,
        },
        "maximumCollectionDelaySecondsByModule": {
            "trendRotation": 300, "etfObservation": 300, "leaderObservation": 300,
        },
    }


class EtfLiveShadowCaptureTests(unittest.TestCase):
    def test_batch_id_and_request_as_of_are_strictly_bound(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        for bad_batch in (
            batch(
                quote("510300"), quote("515050"),
                batch_id="old-run-etf-quotes",
            ),
            batch(
                quote("510300"), quote("515050"),
                as_of=AS_OF - timedelta(days=7),
            ),
        ):
            with self.subTest(meta=bad_batch.meta), self.assertRaisesRegex(
                ValueError,
                "quote_batch_identity_mismatch",
            ):
                build_etf_live_shadow_capture(
                    symbols=("510300", "515050"),
                    radar_run_id="live-run-1",
                    as_of=AS_OF,
                    quote_batch=bad_batch,
                    quote_health=health(),
                    observed_at=OBSERVED_AT,
                    lock_state="acquired",
                    duration_ms=20,
                )

    def test_partial_degraded_batch_preserves_record_and_missing_partition(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300")),
            quote_health=health(SourceStatus.DEGRADED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )

        self.assertEqual(result.artifact.status, "missing")
        self.assertEqual(tuple(item.symbol for item in result.artifact.records), ("510300",))
        self.assertEqual(result.artifact.missing_symbols, ("515050",))
        self.assertIsNotNone(result.artifact.source_time)

    def test_stale_health_preserves_records_and_marks_each_record_stale(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(SourceStatus.STALE),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )

        self.assertEqual(result.artifact.status, "stale")
        self.assertEqual(result.artifact.stale_symbols, ("510300", "515050"))
        self.assertIsNotNone(result.artifact.source_time)

    def test_missing_prices_keep_real_source_time_and_missing_partition(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(
                quote("510300", price=None),
                quote("515050", price=None),
            ),
            quote_health=health(SourceStatus.DEGRADED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )

        self.assertEqual(result.artifact.status, "missing")
        self.assertEqual(result.artifact.missing_symbols, ("510300", "515050"))
        self.assertEqual(result.artifact.failed_symbols, ())
        self.assertEqual(result.artifact.source_time, AS_OF)

    def test_monotonic_clock_rollback_is_rejected_not_coerced_to_zero(self):
        from radar.etf_live_shadow_capture import run_live_etf_shadow_capture

        class Lock:
            def acquire(self, blocking=False):
                return True

            def release(self):
                return None

        wall_times = iter((AS_OF, OBSERVED_AT))
        monotonic_times = iter((100.0, 99.0))
        with self.assertRaisesRegex(ValueError, "monotonic"):
            run_live_etf_shadow_capture(
                symbols=("510300", "515050"),
                radar_run_id="live-run-1",
                lock_path=__import__("pathlib").Path("/private/tmp/unused.lock"),
                quote_fetcher=lambda *_args, **_kwargs: batch(
                    quote("510300"), quote("515050")
                ),
                clock=lambda: next(wall_times),
                monotonic_clock=lambda: next(monotonic_times),
                lock_factory=lambda _path: Lock(),
            )

    def test_collector_rejects_individually_stale_record_claiming_ready(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )
        source_payload = result.artifact.model_dump(mode="json", by_alias=True)
        source_payload["records"][0]["sourceTime"] = (
            AS_OF - timedelta(minutes=10)
        ).isoformat()
        source_raw = json.dumps(
            source_payload, sort_keys=True, separators=(",", ":")
        ).encode()
        receipt_payload = result.receipt.model_dump(mode="json", by_alias=True)
        receipt_payload["sourceArtifactSha256"] = __import__("hashlib").sha256(
            source_raw
        ).hexdigest()
        receipt_raw = json.dumps(
            receipt_payload, sort_keys=True, separators=(",", ":")
        ).encode()

        with self.assertRaisesRegex(ValueError, "record_freshness"):
            adapt_etf_observation(
                receipt_raw,
                source_raw,
                json.dumps(
                    admission_bundle(("510300", "515050")),
                    sort_keys=True, separators=(",", ":"),
                ).encode(),
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=json.dumps(
                    collection_policy(), sort_keys=True, separators=(",", ":"),
                ).encode(),
            )

    def test_null_source_time_still_enforces_request_fetch_observed_order(self):
        from pydantic import ValidationError
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import (
            EtfLiveShadowObservationArtifact,
            FormalShadowRunReceipt,
        )

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=None,
            quote_health=health(SourceStatus.FAILED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )
        artifact_payload = result.artifact.model_dump(mode="python", by_alias=True)
        artifact_payload["fetchedAt"] = AS_OF - timedelta(seconds=1)
        with self.assertRaisesRegex(ValidationError, "time_order"):
            EtfLiveShadowObservationArtifact.model_validate(artifact_payload)
        receipt_payload = result.receipt.model_dump(mode="python", by_alias=True)
        receipt_payload["fetchedAt"] = AS_OF - timedelta(seconds=1)
        with self.assertRaisesRegex(ValidationError, "time_order"):
            FormalShadowRunReceipt.model_validate(receipt_payload)

    def test_partial_quote_coverage_is_missing_not_ready(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )

        self.assertEqual(result.artifact.status, "missing")
        self.assertEqual(result.artifact.missing_symbols, ("515050",))
        self.assertFalse(result.receipt.source_ready)

    def test_future_quote_timestamps_fail_closed_without_becoming_ready(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        during_request = AS_OF + timedelta(seconds=6)
        valid = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(
                quote("510300", source_time=during_request,
                      fetched_at=during_request),
                quote("515050"),
                source_time=during_request,
                fetched_at=during_request,
            ),
            quote_health=health(),
            observed_at=AS_OF + timedelta(seconds=7),
            lock_state="acquired",
            duration_ms=20,
        )
        self.assertEqual(valid.artifact.records[0].status, "ready")

        future = OBSERVED_AT + timedelta(seconds=6)
        with self.assertRaisesRegex(ValueError, "observed_before_fetched"):
            build_etf_live_shadow_capture(
                symbols=("510300", "515050"),
                radar_run_id="live-run-1",
                as_of=AS_OF,
                quote_batch=batch(
                    quote("510300", source_time=future, fetched_at=future),
                    quote("515050"),
                    fetched_at=future,
                ),
                quote_health=health(),
                observed_at=OBSERVED_AT,
                lock_state="acquired",
                duration_ms=20,
            )

    def test_duplicate_or_out_of_scope_quotes_are_rejected(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_etf_live_shadow_capture(
                symbols=("510300", "515050"),
                radar_run_id="live-run-1",
                as_of=AS_OF,
                quote_batch=batch(quote("510300"), quote("510300")),
                quote_health=health(),
                observed_at=OBSERVED_AT,
                lock_state="acquired",
                duration_ms=20,
            )
        with self.assertRaisesRegex(ValueError, "scope"):
            build_etf_live_shadow_capture(
                symbols=("510300", "515050"),
                radar_run_id="live-run-1",
                as_of=AS_OF,
                quote_batch=batch(quote("510300"), quote("510500")),
                quote_health=health(),
                observed_at=OBSERVED_AT,
                lock_state="acquired",
                duration_ms=20,
            )

    def test_lock_contention_does_not_call_quote_source_or_create_ready_receipt(self):
        from radar.etf_live_shadow_capture import run_live_etf_shadow_capture

        class ContendedLock:
            def acquire(self, blocking=False):
                return False

            def release(self):
                raise AssertionError("contended lock must not be released")

        calls = []
        wall_times = iter((AS_OF, OBSERVED_AT))
        monotonic_times = iter((100.0, 100.015))
        result = run_live_etf_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            lock_path=__import__("pathlib").Path("/private/tmp/unused.lock"),
            quote_fetcher=lambda *_args, **_kwargs: calls.append(True),
            clock=lambda: next(wall_times),
            monotonic_clock=lambda: next(monotonic_times),
            lock_factory=lambda _path: ContendedLock(),
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.artifact.lock_state, "contended")
        self.assertFalse(result.receipt.source_ready)

    def test_null_source_time_failure_registers_and_breaks_etf_streak(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation
        from radar.formal_shadow_ledger import (
            FormalShadowObservation,
            build_shadow_ledger,
        )

        class Calendar:
            def is_trading_day(self, value):
                return value.weekday() < 5

        first_source = AS_OF - timedelta(days=3)
        first_observed = OBSERVED_AT - timedelta(days=3)
        ready = FormalShadowObservation(
            module="etfObservation",
            runId="ready-day",
            observedAt=first_observed,
            sourceTime=first_source,
            fetchedAt=first_observed,
            coverage=1.0,
            missingCount=0,
            failedCount=0,
            staleCount=0,
            lockState="acquired",
            durationMs=1,
            evidenceSha256="a" * 64,
            observationStatus="ready",
        )
        capture = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="failed-day",
            as_of=AS_OF,
            quote_batch=None,
            quote_health=health(SourceStatus.FAILED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=1,
        )
        failed = adapt_etf_observation(
            capture.receipt_json_bytes,
            capture.source_json_bytes,
            json.dumps(
                admission_bundle(
                    ("510300", "515050"), run_id="failed-day"
                ),
                sort_keys=True, separators=(",", ":"),
            ).encode(),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=json.dumps(
                collection_policy(), sort_keys=True, separators=(",", ":"),
            ).encode(),
        )
        self.assertIsNone(failed.source_time)
        self.assertEqual(failed.observation_status, "failed")

        ledger = build_shadow_ledger((ready, failed), calendar_provider=Calendar())

        self.assertEqual(ledger.ready_trading_days_by_module["etfObservation"], 1)
        self.assertEqual(ledger.latest_ready_streak_by_module["etfObservation"], 0)
        self.assertEqual(
            ledger.verified_trading_dates_by_module["etfObservation"],
            (first_observed.date(), OBSERVED_AT.date()),
        )

    def test_runner_uses_one_quote_call_and_measured_lock_lifecycle(self):
        from radar.etf_live_shadow_capture import run_live_etf_shadow_capture

        class Lock:
            def __init__(self):
                self.released = False

            def acquire(self, blocking=False):
                self.blocking = blocking
                return True

            def release(self):
                self.released = True

        lock = Lock()
        calls = []
        wall_times = iter((AS_OF, OBSERVED_AT))
        monotonic_times = iter((100.0, 100.037))

        def fetch_once(symbols, **kwargs):
            calls.append((tuple(symbols), kwargs))
            return batch(quote("510300"), quote("515050"))

        result = run_live_etf_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            lock_path=__import__("pathlib").Path("/private/tmp/unused.lock"),
            quote_fetcher=fetch_once,
            clock=lambda: next(wall_times),
            monotonic_clock=lambda: next(monotonic_times),
            lock_factory=lambda _path: lock,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ("510300", "515050"))
        self.assertEqual(result.receipt.duration_ms, 37)
        self.assertTrue(lock.released)

    def test_ready_capture_preserves_zero_turnover_and_binds_receipt_hash(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=37,
        )

        self.assertEqual(result.artifact.status, "ready")
        self.assertEqual(result.artifact.records[0].turnover_amount_cny, 0.0)
        self.assertTrue(result.receipt.source_ready)
        self.assertEqual(result.receipt.source_artifact_sha256, result.source_sha256)
        self.assertEqual(result.receipt.observed_count, 2)

    def test_complete_upstream_failure_keeps_source_time_null_and_fails_closed(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=None,
            quote_health=health(SourceStatus.FAILED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=12,
        )

        self.assertEqual(result.artifact.status, "failed")
        self.assertIsNone(result.artifact.source_time)
        self.assertEqual(result.artifact.failed_symbols, ("510300", "515050"))
        self.assertIsNone(result.receipt.source_time)
        self.assertFalse(result.receipt.source_ready)

    def test_quote_staleness_is_classified_per_symbol(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(
                quote("510300", source_time=AS_OF - timedelta(minutes=3)),
                quote("515050"),
            ),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )

        self.assertEqual(result.artifact.stale_symbols, ("510300",))
        self.assertEqual(result.artifact.records[0].status, "stale")
        self.assertEqual(result.artifact.status, "stale")
        self.assertFalse(result.receipt.source_ready)

    def test_degraded_upstream_cannot_turn_complete_quotes_into_ready_observation(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(SourceStatus.DEGRADED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )

        self.assertEqual(result.artifact.status, "failed")
        self.assertEqual(result.artifact.failed_count, 0)
        self.assertEqual(len(result.artifact.records), 2)
        self.assertEqual(result.artifact.failed_symbols, ())
        self.assertFalse(result.receipt.source_ready)

    def test_admission_run_id_must_match_live_artifact(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=37,
        )
        with self.assertRaisesRegex(
            ValueError, "etf_admission_identity_mismatch"
        ):
            adapt_etf_observation(
                result.receipt_json_bytes,
                result.source_json_bytes,
                json.dumps(
                    admission_bundle(
                        ("510300", "515050"),
                        run_id="formal-admission-run-99",
                    ),
                    sort_keys=True, separators=(",", ":"),
                ).encode(),
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=json.dumps(
                    collection_policy(), sort_keys=True, separators=(",", ":"),
                ).encode(),
            )

    def test_admission_cross_day_or_unverified_identity_is_rejected(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=37,
        )
        for invalid, reason in (
            (
                admission_bundle(
                    ("510300", "515050"),
                    as_of=FETCHED_AT + timedelta(days=1),
                ),
                "etf_admission_identity_mismatch",
            ),
            (
                admission_bundle(
                    ("510300", "515050"),
                    product_identity_status="missing",
                ),
                "etf_admission_product_identity_unverified",
            ),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                adapt_etf_observation(
                    result.receipt_json_bytes,
                    result.source_json_bytes,
                    json.dumps(invalid, sort_keys=True, separators=(",", ":")).encode(),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=json.dumps(
                        collection_policy(), sort_keys=True, separators=(",", ":"),
                    ).encode(),
                )

    def test_same_day_admission_after_capture_end_is_rejected(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )
        future = admission_bundle(
            ("510300", "515050"),
            as_of=OBSERVED_AT + timedelta(hours=4),
        )
        with self.assertRaisesRegex(ValueError, "admission_identity_mismatch"):
            adapt_etf_observation(
                result.receipt_json_bytes,
                result.source_json_bytes,
                json.dumps(future, sort_keys=True, separators=(",", ":")).encode(),
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=json.dumps(
                    collection_policy(), sort_keys=True, separators=(",", ":"),
                ).encode(),
            )

    def test_runner_propagates_programming_errors_but_classifies_request_errors(self):
        import requests
        from radar.etf_live_shadow_capture import run_live_etf_shadow_capture

        class Lock:
            released = False

            def acquire(self, blocking=False):
                return True

            def release(self):
                self.released = True

        programming_lock = Lock()
        with self.assertRaisesRegex(ValueError, "programming_contract_bug"):
            run_live_etf_shadow_capture(
                symbols=("510300", "515050"),
                radar_run_id="live-run-1",
                lock_path=__import__("pathlib").Path("/private/tmp/unused.lock"),
                quote_fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    ValueError("programming_contract_bug")
                ),
                clock=lambda: AS_OF,
                monotonic_clock=lambda: 100.0,
                lock_factory=lambda _path: programming_lock,
            )
        self.assertTrue(programming_lock.released)

        request_lock = Lock()
        wall_times = iter((AS_OF, OBSERVED_AT))
        monotonic_times = iter((100.0, 100.01))
        failed = run_live_etf_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            lock_path=__import__("pathlib").Path("/private/tmp/unused.lock"),
            quote_fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                requests.ConnectionError("source unavailable")
            ),
            clock=lambda: next(wall_times),
            monotonic_clock=lambda: next(monotonic_times),
            lock_factory=lambda _path: request_lock,
        )
        self.assertEqual(failed.artifact.status, "failed")
        self.assertIsNone(failed.artifact.source_time)
        self.assertTrue(request_lock.released)

    def test_complete_time_controls_each_record_freshness_at_90_second_boundary(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture

        exactly_fresh = OBSERVED_AT - timedelta(seconds=90)
        just_stale = OBSERVED_AT - timedelta(seconds=90, microseconds=1)
        for source_time, expected in (
            (exactly_fresh, "ready"),
            (just_stale, "stale"),
        ):
            with self.subTest(source_time=source_time):
                result = build_etf_live_shadow_capture(
                    symbols=("510300", "515050"),
                    radar_run_id="live-run-1",
                    as_of=AS_OF,
                    quote_batch=batch(
                        quote("510300", source_time=source_time),
                        quote("515050"),
                    ),
                    quote_health=health(),
                    observed_at=OBSERVED_AT,
                    lock_state="acquired",
                    duration_ms=20,
                )
                self.assertEqual(result.artifact.records[0].status, expected)

    def test_collector_rechecks_each_record_against_receipt_completion_time(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation

        result = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )
        payload = result.artifact.model_dump(mode="json", by_alias=True)
        payload["records"][0]["sourceTime"] = (
            OBSERVED_AT - timedelta(seconds=90, microseconds=1)
        ).isoformat()
        source_raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode()
        receipt = result.receipt.model_dump(mode="json", by_alias=True)
        receipt["sourceArtifactSha256"] = __import__("hashlib").sha256(
            source_raw,
        ).hexdigest()
        receipt_raw = json.dumps(
            receipt, sort_keys=True, separators=(",", ":"),
        ).encode()
        with self.assertRaisesRegex(ValueError, "record_freshness"):
            adapt_etf_observation(
                receipt_raw,
                source_raw,
                json.dumps(
                    admission_bundle(("510300", "515050")),
                    sort_keys=True, separators=(",", ":"),
                ).encode(),
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=json.dumps(
                    collection_policy(), sort_keys=True, separators=(",", ":"),
                ).encode(),
            )

    def test_degraded_complete_capture_registers_failed_and_resets_streak(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation
        from radar.formal_shadow_ledger import FormalShadowObservation, build_shadow_ledger

        class Calendar:
            def is_trading_day(self, value):
                return value.weekday() < 5

        prior_time = OBSERVED_AT - timedelta(days=3)
        prior = FormalShadowObservation(
            module="etfObservation",
            runId="prior",
            observedAt=prior_time,
            sourceTime=prior_time,
            fetchedAt=prior_time,
            coverage=1.0,
            missingCount=0,
            failedCount=0,
            staleCount=0,
            lockState="acquired",
            durationMs=1,
            sourceReady=True,
            evidenceSha256="a" * 64,
            observationStatus="ready",
        )
        capture = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(SourceStatus.DEGRADED),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )
        degraded = adapt_etf_observation(
            capture.receipt_json_bytes,
            capture.source_json_bytes,
            json.dumps(
                admission_bundle(("510300", "515050")),
                sort_keys=True, separators=(",", ":"),
            ).encode(),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=json.dumps(
                collection_policy(), sort_keys=True, separators=(",", ":"),
            ).encode(),
        )
        self.assertFalse(degraded.source_ready)
        self.assertEqual(degraded.observation_status, "failed")
        self.assertEqual(degraded.failed_count, 0)
        ledger = build_shadow_ledger((prior, degraded), calendar_provider=Calendar())
        self.assertEqual(ledger.ready_trading_days_by_module["etfObservation"], 1)
        self.assertEqual(ledger.latest_ready_streak_by_module["etfObservation"], 0)

    def test_etf_source_health_fields_are_mandatory_and_self_consistent(self):
        from radar.etf_live_shadow_capture import build_etf_live_shadow_capture
        from radar.formal_shadow_observation_collector import adapt_etf_observation

        capture = build_etf_live_shadow_capture(
            symbols=("510300", "515050"),
            radar_run_id="live-run-1",
            as_of=AS_OF,
            quote_batch=batch(quote("510300"), quote("515050")),
            quote_health=health(),
            observed_at=OBSERVED_AT,
            lock_state="acquired",
            duration_ms=20,
        )
        admission_raw = json.dumps(
            admission_bundle(("510300", "515050")),
            sort_keys=True, separators=(",", ":"),
        ).encode()
        policy_raw = json.dumps(
            collection_policy(), sort_keys=True, separators=(",", ":"),
        ).encode()
        cases = (
            ("artifact", "sourceHealthStatus"),
            ("artifact", "sourceHealthReasons"),
            ("receipt", "sourceHealthStatus"),
            ("receipt", "sourceHealthReasons"),
        )
        for target, field in cases:
            with self.subTest(target=target, field=field):
                source = capture.artifact.model_dump(mode="json", by_alias=True)
                receipt_payload = capture.receipt.model_dump(
                    mode="json", by_alias=True,
                )
                if target == "artifact":
                    del source[field]
                else:
                    del receipt_payload[field]
                source_raw = json.dumps(
                    source, sort_keys=True, separators=(",", ":"),
                ).encode()
                receipt_payload["sourceArtifactSha256"] = hashlib.sha256(
                    source_raw,
                ).hexdigest()
                receipt_raw = json.dumps(
                    receipt_payload, sort_keys=True, separators=(",", ":"),
                ).encode()
                with self.assertRaisesRegex(ValueError, "receipt|artifact"):
                    adapt_etf_observation(
                        receipt_raw,
                        source_raw,
                        admission_raw,
                        evaluated_at=OBSERVED_AT,
                        collection_policy_json_bytes=policy_raw,
                    )

        for status, reasons in (
            ("healthy", ["unexpected"]),
            ("degraded", []),
        ):
            with self.subTest(status=status, reasons=reasons):
                source = capture.artifact.model_dump(mode="json", by_alias=True)
                source["sourceHealthStatus"] = status
                source["sourceHealthReasons"] = reasons
                if status != "healthy":
                    source["status"] = "failed"
                source_raw = json.dumps(
                    source, sort_keys=True, separators=(",", ":"),
                ).encode()
                receipt_payload = capture.receipt.model_dump(
                    mode="json", by_alias=True,
                )
                receipt_payload.update({
                    "sourceArtifactSha256": hashlib.sha256(source_raw).hexdigest(),
                    "sourceHealthStatus": status,
                    "sourceHealthReasons": reasons,
                    "sourceReady": status == "healthy",
                })
                receipt_raw = json.dumps(
                    receipt_payload, sort_keys=True, separators=(",", ":"),
                ).encode()
                with self.assertRaisesRegex(ValueError, "receipt|artifact"):
                    adapt_etf_observation(
                        receipt_raw,
                        source_raw,
                        admission_raw,
                        evaluated_at=OBSERVED_AT,
                        collection_policy_json_bytes=policy_raw,
                    )


if __name__ == "__main__":
    unittest.main()
