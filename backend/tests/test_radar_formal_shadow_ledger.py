import unittest
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError


UTC = timezone.utc
BASE_TIME = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
SHA256 = "b" * 64


class CalendarProvider:
    def __init__(self, trading_dates):
        self.trading_dates = set(trading_dates)

    def is_trading_day(self, value):
        if value == date(2026, 9, 9):
            return None
        return value in self.trading_dates


class FormalShadowLedgerTests(unittest.TestCase):
    def calendar_evidence(self, *, observed_through=date(2026, 9, 4)):
        from radar.formal_shadow_calendar import FormalShadowCalendarEvidence

        return FormalShadowCalendarEvidence(
            year=2026,
            fetchedAt=datetime.combine(
                observed_through,
                datetime.min.time(),
                tzinfo=timezone(timedelta(hours=8)),
            ),
            observedThrough=observed_through,
            sourceDocumentSha256="f" * 64,
            closedDays=(date(2026, 1, 1),),
        )

    def test_v2_binds_official_calendar_evidence_and_cutoff(self):
        from radar.formal_shadow_calendar import OfficialSseCalendarProvider
        from radar.formal_shadow_ledger import build_shadow_ledger

        evidence = self.calendar_evidence()
        provider = OfficialSseCalendarProvider((evidence,))
        ledger = build_shadow_ledger(
            [self.observation(observed_date=date(2026, 9, 4))],
            calendar_provider=provider,
            calendar_evidence=(evidence,),
        )

        self.assertEqual(ledger.calendar_evidence, (evidence,))
        self.assertEqual(ledger.calendar_observed_through, date(2026, 9, 4))

    def test_v2_rejects_calendar_cutoff_or_verified_dates_tamper(self):
        from pydantic import ValidationError
        from radar.formal_shadow_calendar import OfficialSseCalendarProvider
        from radar.formal_shadow_ledger import FormalShadowLedgerV2, build_shadow_ledger

        evidence = self.calendar_evidence()
        ledger = build_shadow_ledger(
            [self.observation(observed_date=date(2026, 9, 4))],
            calendar_provider=OfficialSseCalendarProvider((evidence,)),
            calendar_evidence=(evidence,),
        )
        for field, value, reason in (
            ("calendarObservedThrough", "2026-09-03", "formal_shadow_calendar_cutoff_conflict"),
            (
                "verifiedTradingDatesByModule",
                {**ledger.model_dump(mode="json", by_alias=True)["verifiedTradingDatesByModule"], "trendRotation": ["2026-09-03", "2026-09-04"]},
                "verified_trading_dates_observations_conflict",
            ),
        ):
            payload = ledger.model_dump(mode="json", by_alias=True)
            payload[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, reason):
                    FormalShadowLedgerV2.model_validate(payload)

        forged_calendar = evidence.model_copy(update={
            "closed_days": (date(2027, 1, 1),),
        })
        forged_ledger = ledger.model_copy(update={
            "calendar_evidence": (forged_calendar,),
        })
        with self.assertRaisesRegex(ValidationError, "formal_shadow_calendar_year_mismatch"):
            FormalShadowLedgerV2.model_validate(
                forged_ledger.model_dump(mode="python", by_alias=True),
            )
    def observation(self, module="trendRotation", observed_date=date(2026, 9, 4), **changes):
        observed_at = datetime.combine(
            observed_date,
            datetime.min.time(),
            UTC,
        ).replace(hour=8)
        payload = {
            "module": module,
            "runId": f"{module}-{observed_date.isoformat()}",
            "observedAt": observed_at,
            "sourceTime": observed_at - timedelta(minutes=2),
            "fetchedAt": observed_at - timedelta(minutes=1),
            "coverage": 1.0,
            "missingCount": 0,
            "failedCount": 0,
            "staleCount": 0,
            "lockState": "acquired",
            "durationMs": 0,
            "evidenceSha256": SHA256,
        }
        payload.update(changes)
        return payload

    def provider(self):
        return CalendarProvider({
            date(2026, 9, 1),
            date(2026, 9, 2),
            date(2026, 9, 3),
            date(2026, 9, 4),
            date(2026, 9, 5),
        })

    def test_same_module_same_day_is_deduplicated_without_increasing_ready_days(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation(runId="first"),
            self.observation(runId="first"),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 1)
        self.assertEqual(len(ledger.observations), 1)
        self.assertEqual(ledger.observations[0].run_id, "first")

    def test_same_module_same_day_different_content_fails_closed(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        with self.assertRaisesRegex(
            ValueError,
            "shadow_observation_identity_conflict",
        ):
            build_shadow_ledger([
                self.observation(runId="first"),
                self.observation(runId="replay"),
            ], calendar_provider=self.provider())

    def test_non_trading_day_is_retained_but_not_counted(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation(observed_date=date(2026, 9, 6)),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 0)
        self.assertEqual(ledger.observations[0].observation_status, "non_trading_day")

    def test_observed_trading_date_is_derived_in_shanghai_timezone(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        observed_at = datetime(2026, 9, 4, 16, 30, tzinfo=UTC)
        payload = self.observation(observed_date=date(2026, 9, 4))
        payload.update({
            "observedAt": observed_at,
            "sourceTime": observed_at - timedelta(minutes=2),
            "fetchedAt": observed_at - timedelta(minutes=1),
        })
        ledger = build_shadow_ledger(
            [payload],
            calendar_provider=CalendarProvider({date(2026, 9, 4)}),
        )

        self.assertEqual(ledger.observations[0].observed_date, date(2026, 9, 5))
        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 0)
        self.assertIsNone(
            ledger.latest_ready_trading_date_by_module["trendRotation"],
        )
        self.assertEqual(ledger.observations[0].observation_status, "non_trading_day")

    def test_unknown_calendar_day_fails_closed(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        with self.assertRaisesRegex(ValueError, "trading_day_unknown"):
            build_shadow_ledger([
                self.observation(observed_date=date(2026, 9, 9)),
            ], calendar_provider=self.provider())

    def test_modules_count_independently_and_keep_own_requirements(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation("trendRotation", date(2026, 9, 1)),
            self.observation("etfObservation", date(2026, 9, 1)),
            self.observation("etfObservation", date(2026, 9, 2)),
            self.observation("etfObservation", date(2026, 9, 3)),
            self.observation("etfObservation", date(2026, 9, 4)),
            self.observation("etfObservation", date(2026, 9, 5)),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 1)
        self.assertEqual(ledger.ready_trading_days_by_module["etfObservation"], 5)
        self.assertEqual(ledger.required_trading_days_by_module["etfObservation"], 5)
        self.assertEqual(ledger.required_trading_days_by_module["trendRotation"], 20)
        self.assertEqual(ledger.required_trading_days_by_module["leaderObservation"], 20)

    def test_v2_derives_latest_ready_dates_and_etf_latest_streak(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation("trendRotation", date(2026, 9, 1)),
            self.observation("trendRotation", date(2026, 9, 2)),
            self.observation("etfObservation", date(2026, 9, 1)),
            self.observation("etfObservation", date(2026, 9, 2)),
            self.observation("etfObservation", date(2026, 9, 3)),
            self.observation("etfObservation", date(2026, 9, 5)),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.contract_version, "radar-formal-shadow-ledger-v2")
        self.assertEqual(
            ledger.latest_ready_trading_date_by_module["trendRotation"],
            date(2026, 9, 2),
        )
        self.assertEqual(
            ledger.latest_ready_trading_date_by_module["etfObservation"],
            date(2026, 9, 5),
        )
        self.assertEqual(ledger.latest_ready_streak_by_module["etfObservation"], 1)
        with self.assertRaises(TypeError):
            ledger.latest_ready_trading_date_by_module["etfObservation"] = date(2026, 9, 4)
        with self.assertRaises(TypeError):
            ledger.latest_ready_streak_by_module["etfObservation"] = 5

    def test_etf_failed_or_missing_day_interrupts_latest_ready_streak(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation("etfObservation", date(2026, 9, 1)),
            self.observation("etfObservation", date(2026, 9, 2)),
            self.observation("etfObservation", date(2026, 9, 3), failedCount=1),
            self.observation("etfObservation", date(2026, 9, 4)),
            self.observation("etfObservation", date(2026, 9, 5)),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.ready_trading_days_by_module["etfObservation"], 4)
        self.assertEqual(ledger.latest_ready_streak_by_module["etfObservation"], 2)

    def test_etf_latest_failed_observed_trading_day_resets_streak_to_zero(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation("etfObservation", date(2026, 9, 1)),
            self.observation("etfObservation", date(2026, 9, 2)),
            self.observation("etfObservation", date(2026, 9, 3)),
            self.observation("etfObservation", date(2026, 9, 4), failedCount=1),
        ], calendar_provider=self.provider())

        self.assertEqual(
            ledger.observed_trading_date_range_by_module["etfObservation"],
            (date(2026, 9, 1), date(2026, 9, 4)),
        )
        self.assertEqual(
            ledger.verified_trading_dates_by_module["etfObservation"],
            (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)),
        )
        self.assertEqual(
            ledger.latest_ready_trading_date_by_module["etfObservation"],
            date(2026, 9, 3),
        )
        self.assertEqual(ledger.latest_ready_streak_by_module["etfObservation"], 0)

    def test_v2_rejects_raw_or_model_copy_forged_etf_streak(self):
        from radar.formal_shadow_ledger import (
            FormalShadowLedgerV2,
            build_shadow_ledger,
        )

        class Calendar:
            def is_trading_day(self, value):
                return date(2026, 9, 1) <= value <= date(2026, 9, 6)

        ledger = build_shadow_ledger([
            self.observation("etfObservation", date(2026, 9, 1)),
            self.observation("etfObservation", date(2026, 9, 2)),
            self.observation("etfObservation", date(2026, 9, 3)),
            self.observation("etfObservation", date(2026, 9, 5)),
            self.observation("etfObservation", date(2026, 9, 6)),
        ], calendar_provider=Calendar())
        self.assertEqual(ledger.ready_trading_days_by_module["etfObservation"], 5)
        self.assertEqual(ledger.latest_ready_streak_by_module["etfObservation"], 2)
        raw = ledger.model_dump(mode="python", by_alias=True)
        raw["latestReadyStreakByModule"] = {
            **raw["latestReadyStreakByModule"],
            "etfObservation": 5,
        }
        forged = ledger.model_copy(update={
            "latest_ready_streak_by_module": raw["latestReadyStreakByModule"],
        })

        for value in (raw, forged.model_dump(mode="python", by_alias=True)):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(
                    ValidationError,
                    "latest_ready_streak_observations_conflict",
                ):
                    FormalShadowLedgerV2.model_validate(value)

    def test_zero_coverage_cannot_be_ready_after_nested_model_copy_revalidation(self):
        from radar.formal_shadow_ledger import (
            FormalShadowObservation,
            FormalShadowLedgerV2,
            build_shadow_ledger,
        )

        ledger = build_shadow_ledger(
            [self.observation(observationStatus="ready")],
            calendar_provider=self.provider(),
        )
        forged_observation = ledger.observations[0].model_copy(
            update={"coverage": 0},
        )
        forged_ledger = ledger.model_copy(update={
            "observations": (forged_observation,),
        })

        with self.assertRaisesRegex(
            ValidationError,
            "observation_status_fields_conflict",
        ):
            FormalShadowLedgerV2.model_validate(
                forged_ledger.model_dump(mode="python", by_alias=True),
            )
        with self.assertRaisesRegex(
            ValidationError,
            "observation_status_fields_conflict",
        ):
            FormalShadowObservation.model_validate(
                self.observation(coverage=0, observationStatus="ready"),
            )

    def test_missing_or_failed_trading_days_do_not_count_as_ready(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation(observed_date=date(2026, 9, 1), missingCount=1),
            self.observation(observed_date=date(2026, 9, 2), failedCount=1),
            self.observation(observed_date=date(2026, 9, 3), staleCount=1),
            self.observation(observed_date=date(2026, 9, 4)),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 1)
        statuses = [item.observation_status for item in ledger.observations]
        self.assertEqual(statuses, ["missing", "failed", "stale", "ready"])

    def test_zero_duration_is_a_valid_observation(self):
        from radar.formal_shadow_ledger import FormalShadowObservation

        value = FormalShadowObservation.model_validate(self.observation())
        self.assertEqual(value.duration_ms, 0)

    def test_observation_rejects_fetch_after_observation_time(self):
        from radar.formal_shadow_ledger import FormalShadowObservation

        payload = self.observation()
        payload["fetchedAt"] = payload["observedAt"] + timedelta(microseconds=1)
        with self.assertRaisesRegex(ValidationError, "fetchedAt_after_observedAt"):
            FormalShadowObservation.model_validate(payload)

    def test_final_observation_status_must_match_its_intrinsic_fields(self):
        from radar.formal_shadow_ledger import FormalShadowObservation

        cases = (
            {"observationStatus": "ready", "missingCount": 1},
            {"observationStatus": "ready", "lockState": "contended"},
            {"observationStatus": "ready", "lockState": "failed"},
            {"observationStatus": "missing", "missingCount": 0},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    ValidationError,
                    "observation_status_fields_conflict",
                ):
                    FormalShadowObservation.model_validate(
                        self.observation(**changes),
                    )

    def test_pending_observation_is_still_derived_by_ledger_builder(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation(observationStatus="pending", staleCount=1),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.observations[0].observation_status, "stale")
        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 0)

    def test_contended_or_failed_lock_cannot_count_as_ready(self):
        from radar.formal_shadow_ledger import build_shadow_ledger

        ledger = build_shadow_ledger([
            self.observation(lockState="contended"),
            self.observation(observed_date=date(2026, 9, 5), lockState="failed"),
        ], calendar_provider=self.provider())

        self.assertEqual(ledger.ready_trading_days_by_module["trendRotation"], 0)
        self.assertEqual(
            [item.observation_status for item in ledger.observations],
            ["failed", "failed"],
        )

    def test_ledger_counts_are_immutable_and_must_match_ready_observations(self):
        from radar.formal_shadow_ledger import (
            FormalShadowLedger,
            FormalShadowObservation,
            build_shadow_ledger,
        )

        ledger = build_shadow_ledger([self.observation()], calendar_provider=self.provider())
        with self.assertRaises(TypeError):
            ledger.ready_trading_days_by_module["trendRotation"] = 20
        with self.assertRaises(TypeError):
            ledger.required_trading_days_by_module["trendRotation"] = 1

        observation = FormalShadowObservation.model_validate(self.observation())
        with self.assertRaisesRegex(ValidationError, "ready_trading_days_observations_conflict"):
            FormalShadowLedger(
                observations=(observation.model_copy(update={"observation_status": "ready"}),),
                readyTradingDaysByModule={"trendRotation": 0, "etfObservation": 0, "leaderObservation": 0},
            )

    def test_ledger_revalidates_model_copy_forged_nested_observation(self):
        from radar.formal_shadow_ledger import (
            FormalShadowLedger,
            FormalShadowObservation,
        )

        observation = FormalShadowObservation.model_validate(
            self.observation(observationStatus="ready"),
        )
        forged = observation.model_copy(update={"missing_count": 1})

        with self.assertRaisesRegex(
            ValidationError,
            "observation_status_fields_conflict",
        ):
            FormalShadowLedger(
                observations=(forged,),
                readyTradingDaysByModule={
                    "trendRotation": 1,
                    "etfObservation": 0,
                    "leaderObservation": 0,
                },
            )
