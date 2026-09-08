import unittest
from datetime import datetime, timezone

from pydantic import ValidationError


NOW = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)


class RadarFormalShadowProgressContractTests(unittest.TestCase):
    def _modules(self):
        from radar.formal_readiness_contracts import FormalShadowProgressModule

        return tuple(
            FormalShadowProgressModule(
                module=module,
                observedTradingDays=observed,
                requiredTradingDays=required,
                latestReadyStreak=observed,
                latestReadyTradingDate="2026-09-08" if observed else None,
            )
            for module, observed, required in (
                ("trendRotation", 2, 20),
                ("etfObservation", 2, 5),
                ("leaderObservation", 1, 20),
            )
        )

    def test_available_contract_requires_exact_modules_and_content_hash(self):
        from radar.formal_readiness_contracts import RadarFormalShadowProgress

        progress = RadarFormalShadowProgress(
            checkedAt=NOW,
            state="available",
            modules=self._modules(),
            ledgerSha256="a" * 64,
        )

        self.assertEqual(len(progress.modules), 3)

    def test_missing_contract_cannot_carry_numeric_modules(self):
        from radar.formal_readiness_contracts import RadarFormalShadowProgress

        with self.assertRaisesRegex(ValidationError, "unavailable_modules_must_be_empty"):
            RadarFormalShadowProgress(
                checkedAt=NOW,
                state="missing",
                modules=self._modules(),
                reasonCodes=("formal_shadow_progress_store_unconfigured",),
            )

    def test_module_required_days_are_frozen_by_product_contract(self):
        from radar.formal_readiness_contracts import FormalShadowProgressModule

        with self.assertRaisesRegex(ValidationError, "requiredTradingDays_contract_conflict"):
            FormalShadowProgressModule(
                module="leaderObservation",
                observedTradingDays=1,
                requiredTradingDays=5,
                latestReadyStreak=1,
                latestReadyTradingDate="2026-09-08",
            )


if __name__ == "__main__":
    unittest.main()
