import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch


class RunRadarReplayFormalCohortTests(unittest.TestCase):
    def test_cli_requires_explicit_live_confirmation(self):
        from run_radar_replay_formal_cohort import main

        with patch(
            "run_radar_replay_formal_cohort.prepare_formal_cohort"
        ) as prepare:
            code = main([
                "--campaign-dir", "/private/tmp/campaign",
                "--output-dir", "/private/tmp/cohort",
                "--stage6-artifact", "/private/tmp/stage6.json",
            ])

        self.assertEqual(code, 2)
        prepare.assert_not_called()

    def test_cli_forwards_one_formal_cohort_chain(self):
        from run_radar_replay_formal_cohort import main

        result = SimpleNamespace(
            plan=SimpleNamespace(role="development"),
            baseline=SimpleNamespace(
                replay=SimpleNamespace(
                    samples=[SimpleNamespace(
                        sample_id="forward-development-1",
                        radar_run_id="stage6-live-1",
                        as_of="2026-09-03T10:05:00+08:00",
                    )]
                ),
            ),
            output=SimpleNamespace(bundle=object()),
            campaign_state=SimpleNamespace(
                campaign_id="stage9-formal-forward-v1",
                revision=2,
                cohorts=[1],
            ),
            manifest_path=Path(
                "/private/tmp/cohort/formal-cohort-manifest.json"
            ),
        )
        with patch(
            "run_radar_replay_formal_cohort.prepare_formal_cohort",
            return_value=result,
        ) as prepare, patch(
            "run_radar_replay_formal_cohort.publish_replay_etf_research",
            return_value=SimpleNamespace(evidence_sha256="a" * 64),
        ) as publish:
            code = main([
                "--confirm-live-cohort",
                "--campaign-dir", "/private/tmp/campaign",
                "--output-dir", "/private/tmp/cohort",
                "--stage6-artifact", "/private/tmp/stage6.json",
                "--cninfo-pdf-cache-dir", "/private/tmp/pdf-cache",
                "--formal-etf", "515790",
                "--formal-etf", "515050",
            ])

        self.assertEqual(code, 0)
        self.assertEqual(
            prepare.call_args.kwargs["formal_etf_symbols"],
            ("515790", "515050"),
        )
        self.assertEqual(
            prepare.call_args.kwargs["stage6_artifact_path"],
            Path("/private/tmp/stage6.json"),
        )
        publish.assert_called_once_with(
            result.output.bundle,
            ANY,
        )


if __name__ == "__main__":
    unittest.main()
