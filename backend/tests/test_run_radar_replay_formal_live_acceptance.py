import json
import hashlib
import tempfile
import unittest
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)


class RadarReplayFormalLiveAcceptanceCliTests(unittest.TestCase):
    @staticmethod
    def module():
        import run_radar_replay_formal_live_acceptance as module

        return module

    def test_partial_stage6_artifact_requires_prepared_run_identity(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            artifact = Path(directory) / "stage6.json"
            artifact.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_live_stage6_artifact_unverified",
            ):
                module._stage6_artifact_radar_run_id(artifact)

    def test_confirmation_is_required_before_preflight_or_sources(self):
        module = self.module()
        output = StringIO()
        calls = []

        code = module.run_cli(
            [
                "--campaign-dir", "/private/tmp/campaign",
                "--stage6-output-dir", "/private/tmp/stage6",
                "--cohort-output-dir", "/private/tmp/cohort",
                "--initialize-sector-state",
                "--formal-etf", "515790",
            ],
            stdout=output,
            planner=lambda **_: calls.append("plan"),
            stage6_runner=lambda *_, **__: calls.append("stage6"),
            formal_runner=lambda **_: calls.append("formal"),
        )

        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "confirmation_required",
        )

    def test_campaign_and_both_output_trees_must_be_pairwise_disjoint(self):
        module = self.module()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            cases = (
                (campaign, root / "cohort"),
                (root / "stage6", campaign / "cohort"),
            )
            for stage6_output, cohort_output in cases:
                with self.subTest(
                    stage6=str(stage6_output),
                    cohort=str(cohort_output),
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "replay_formal_live_output_dirs_overlap",
                    ):
                        module._validate_dirs(
                            campaign_dir=campaign,
                            stage6_output_dir=stage6_output,
                            cohort_output_dir=cohort_output,
                        )

    def test_cninfo_cache_must_be_disjoint_from_all_mutable_trees(self):
        module = self.module()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            cohort_output = root / "cohort"
            for cache_dir in (
                campaign / "pdf-cache",
                stage6_output / "pdf-cache",
                cohort_output / "pdf-cache",
            ):
                with self.subTest(cache=str(cache_dir)):
                    with self.assertRaisesRegex(
                        ValueError,
                        "replay_formal_live_output_dirs_overlap",
                    ):
                        module._validate_dirs(
                            campaign_dir=campaign,
                            stage6_output_dir=stage6_output,
                            cohort_output_dir=cohort_output,
                            cninfo_pdf_cache_dir=cache_dir,
                        )

    def test_failed_preflight_never_runs_stage6_sources(self):
        module = self.module()
        output = StringIO()
        calls = []

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(root / "stage6"),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--initialize-sector-state",
                    "--formal-etf", "515790",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                planner=lambda **_: (_ for _ in ()).throw(
                    ValueError("replay_formal_cohort_continuous_session_required")
                ),
                stage6_runner=lambda *_, **__: calls.append("stage6"),
                formal_runner=lambda **_: calls.append("formal"),
            )

        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "replay_formal_cohort_continuous_session_required",
        )

    def test_stage6_not_ready_keeps_formal_campaign_unchanged(self):
        module = self.module()
        output = StringIO()
        formal_calls = []

        def stage6_runner(argv, *, stdout, **_):
            self.assertIn("--confirm-live-five-source", argv)
            stdout.write(json.dumps({
                "status": "source_unverified",
                "reason": "official_source_unavailable",
                "artifactPath": None,
            }))
            return 2

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(root / "stage6"),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--initialize-sector-state",
                    "--formal-etf", "515790",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=lambda **kwargs: formal_calls.append(kwargs),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "stage6_not_ready")
        self.assertEqual(payload["stage6Status"], "source_unverified")
        self.assertEqual(formal_calls, [])

    def test_stage6_stdout_rejects_non_strict_json(self):
        module = self.module()
        malicious_payloads = (
            '{"status":"failed","status":"ready_for_review"}',
            '{"status":"ready_for_review","nested":{"x":1,"x":2}}',
            '{"status":"ready_for_review","value":NaN}',
            '{"status":"ready_for_review","value":Infinity}',
            '{"status":"ready_for_review","value":-Infinity}',
        )

        for malicious in malicious_payloads:
            with self.subTest(payload=malicious), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                campaign = root / "campaign"
                campaign.mkdir()
                output = StringIO()
                formal_calls = []

                def stage6_runner(_argv, *, stdout, **_kwargs):
                    stdout.write(malicious)
                    return 0

                code = module.run_cli(
                    [
                        "--confirm-live-formal-sequence",
                        "--campaign-dir", str(campaign),
                        "--stage6-output-dir", str(root / "stage6"),
                        "--cohort-output-dir", str(root / "cohort"),
                        "--initialize-sector-state",
                        "--formal-etf", "515790",
                    ],
                    stdout=output,
                    now_provider=lambda: NOW,
                    planner=lambda **_: SimpleNamespace(role="development"),
                    stage6_runner=stage6_runner,
                    formal_runner=lambda **kwargs: formal_calls.append(kwargs),
                )

                self.assertEqual(code, 1)
                self.assertEqual(
                    json.loads(output.getvalue())["reason"],
                    "replay_formal_live_stage6_result_unverified",
                )
                self.assertEqual(formal_calls, [])

    def test_stage6_stdout_rejects_invalid_utf8(self):
        module = self.module()
        output = StringIO()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()

            def stage6_runner(_argv, *, stdout, **_kwargs):
                stdout.write(b"\xff")
                return 0

            from unittest.mock import patch
            with patch.object(module, "StringIO", BytesIO):
                code = module.run_cli(
                    [
                        "--confirm-live-formal-sequence",
                        "--campaign-dir", str(campaign),
                        "--stage6-output-dir", str(root / "stage6"),
                        "--cohort-output-dir", str(root / "cohort"),
                        "--initialize-sector-state",
                        "--formal-etf", "515790",
                    ],
                    stdout=output,
                    now_provider=lambda: NOW,
                    planner=lambda **_: SimpleNamespace(role="development"),
                    stage6_runner=stage6_runner,
                    formal_runner=lambda **_: self.fail("formal must not run"),
                )

        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "replay_formal_live_stage6_result_unverified",
        )

    def test_success_passes_only_new_stage6_artifact_to_formal_entry(self):
        module = self.module()
        output = StringIO()
        received = []

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            cninfo_cache = root / "cninfo-cache"
            formal_shadow_inputs = root / "formal-shadow-inputs"
            stage6_artifact = (
                stage6_output
                / "stage6-live-five-source-20260903T100000000000.json"
                )

            def stage6_runner(argv, *, stdout, now_provider, **_):
                self.assertEqual(now_provider(), NOW)
                self.assertIn("--initialize-sector-state", argv)
                self.assertEqual(
                    argv[argv.index("--formal-shadow-input-root") + 1],
                    str(formal_shadow_inputs),
                )
                stage6_output.mkdir()
                stage6_artifact.write_text(json.dumps({
                    "prepared": {
                        "contractId": (
                            "radar-leader-phase6-prepared-historical-inputs-v1"
                        ),
                        "radarRunId": "stage6-live-20260903",
                    },
                }), encoding="utf-8")
                stdout.write(json.dumps({
                    "status": "ready_for_review",
                    "artifactPath": str(stage6_artifact),
                    "formalShadowInputDirs": {
                        "trendRotation": str(formal_shadow_inputs / "trend"),
                        "leaderObservation": str(formal_shadow_inputs / "leader"),
                    },
                }))
                return 0

            def formal_runner(**kwargs):
                received.append(kwargs)
                sample = SimpleNamespace(
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=NOW,
                )
                manifest = root / "cohort" / "formal-cohort-manifest.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(json.dumps({
                    "contractId": "radar-replay-formal-cohort-run-v1",
                    "role": "development",
                    "sampleId": sample.sample_id,
                    "radarRunId": sample.radar_run_id,
                }))
                return SimpleNamespace(
                    plan=SimpleNamespace(role="development"),
                    baseline=SimpleNamespace(
                        replay=SimpleNamespace(samples=[sample]),
                        etf_formal_admission_path=(
                            root / "cohort" / "baseline" / "etf-admission.json"
                        ),
                    ),
                    campaign_state=SimpleNamespace(
                        campaign_id="stage9-formal-forward-v1",
                        revision=2,
                        cohorts=[object()],
                    ),
                    manifest_path=root / "cohort" / "formal-cohort-manifest.json",
                )

            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(stage6_output),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--initialize-sector-state",
                    "--cninfo-pdf-cache-dir", str(cninfo_cache),
                    "--formal-shadow-input-root", str(formal_shadow_inputs),
                    "--formal-etf", "515790",
                    "--formal-etf", "515050",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                day_kind_provider=lambda _: "full",
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=formal_runner,
            )
            manifest_path = root / "cohort" / "formal-cohort-manifest.json"
            manifest_relative = str(manifest_path.relative_to("/private/tmp"))
            manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "registered")
        self.assertEqual(payload["stage6Status"], "ready_for_review")
        self.assertEqual(payload["stage6ArtifactPath"], str(stage6_artifact))
        self.assertEqual(
            payload["formalShadowInputDirs"],
            {
                "trendRotation": str(formal_shadow_inputs / "trend"),
                "leaderObservation": str(formal_shadow_inputs / "leader"),
            },
        )
        self.assertEqual(
            payload["etfFormalAdmissionPath"],
            "baseline/etf-admission.json",
        )
        self.assertEqual(payload["manifestPath"], "formal-cohort-manifest.json")
        self.assertEqual(
            payload["manifestRelativePath"],
            manifest_relative,
        )
        self.assertEqual(
            payload["manifestSha256"],
            manifest_sha,
        )
        self.assertNotIn("/private/tmp", payload["manifestPath"])
        self.assertEqual(payload["campaignRevision"], 2)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["stage6_artifact_path"], stage6_artifact)
        self.assertEqual(
            received[0]["cninfo_pdf_cache_dir"],
            cninfo_cache,
        )
        self.assertEqual(
            received[0]["formal_etf_symbols"],
            ("515790", "515050"),
        )

    def test_formal_failure_preserves_verified_stage6_shadow_inputs(self):
        module = self.module()
        output = StringIO()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            shadow_root = root / "shadow"
            artifact = stage6_output / "stage6-live-five-source-20260903T100000.json"
            dirs = {
                "trendRotation": str(shadow_root / "trend"),
                "leaderObservation": str(shadow_root / "leader"),
            }

            def stage6_runner(_argv, *, stdout, **_kwargs):
                stage6_output.mkdir()
                artifact.write_text(json.dumps({
                    "prepared": {
                        "contractId": (
                            "radar-leader-phase6-prepared-historical-inputs-v1"
                        ),
                        "radarRunId": "run-verified",
                    },
                }), encoding="utf-8")
                stdout.write(json.dumps({
                    "status": "ready_for_review",
                    "artifactPath": str(artifact),
                    "prepared": {"radarRunId": "run-verified"},
                    "formalShadowInputDirs": dirs,
                }))
                return 0

            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(stage6_output),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--formal-shadow-input-root", str(shadow_root),
                    "--initialize-sector-state",
                    "--formal-etf", "515050",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                day_kind_provider=lambda _: "full",
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=lambda **_: (_ for _ in ()).throw(
                    ValueError("etf_source_failed")
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "etf_source_failed")
        self.assertEqual(payload["stage6Status"], "ready_for_review")
        self.assertEqual(payload["stage6ArtifactPath"], str(artifact))
        self.assertEqual(payload["radarRunId"], "run-verified")
        self.assertEqual(payload["formalShadowInputDirs"], dirs)

    def test_formal_failure_recovers_run_identity_from_real_stage6_artifact(self):
        module = self.module()
        output = StringIO()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            shadow_root = root / "shadow"
            artifact = (
                stage6_output
                / "stage6-live-five-source-20260908T101112546517.json"
            )
            dirs = {
                "trendRotation": str(shadow_root / "trend"),
                "leaderObservation": str(shadow_root / "leader"),
            }

            def stage6_runner(_argv, *, stdout, **_kwargs):
                stage6_output.mkdir()
                artifact.write_text(json.dumps({
                    "prepared": {
                        "contractId": (
                            "radar-leader-phase6-prepared-historical-inputs-v1"
                        ),
                        "radarRunId": "run-from-stage6-artifact",
                    },
                }), encoding="utf-8")
                # 真实阶段6 CLI只输出摘要，run id保存在工件中。
                stdout.write(json.dumps({
                    "status": "ready_for_review",
                    "artifactPath": str(artifact),
                    "formalShadowInputDirs": dirs,
                }))
                return 0

            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(stage6_output),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--formal-shadow-input-root", str(shadow_root),
                    "--initialize-sector-state",
                    "--formal-etf", "515050",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                day_kind_provider=lambda _: "full",
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=lambda **_: (_ for _ in ()).throw(
                    ValueError("replay_formal_cohort_source_quality_not_ready")
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["reason"],
            "replay_formal_cohort_source_quality_not_ready",
        )
        self.assertEqual(payload["stage6Status"], "ready_for_review")
        self.assertEqual(
            payload["radarRunId"],
            "run-from-stage6-artifact",
        )
        self.assertEqual(payload["formalShadowInputDirs"], dirs)

    def test_stage6_not_ready_preserves_verified_partial_trend_input(self):
        module = self.module()
        output = StringIO()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            shadow_root = root / "shadow"
            trend = shadow_root / "published" / "trendRotation"
            trend.mkdir(parents=True)
            artifact = (
                stage6_output
                / "stage6-live-five-source-20260903T100000000002.json"
            )

            def stage6_runner(_argv, *, stdout, **_kwargs):
                stage6_output.mkdir()
                artifact.write_text(json.dumps({
                    "prepared": {
                        "contractId": (
                            "radar-leader-phase6-prepared-historical-inputs-v1"
                        ),
                        "radarRunId": "run-partial-trend",
                    },
                }), encoding="utf-8")
                stdout.write(json.dumps({
                    "status": "not_ready",
                    "artifactPath": str(artifact),
                    "formalShadowInputDirs": {
                        "trendRotation": str(trend),
                    },
                    "reasons": ["leader_business_evidence_qualification_empty"],
                }))
                return 2

            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(stage6_output),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--formal-shadow-input-root", str(shadow_root),
                    "--initialize-sector-state",
                    "--formal-etf", "515050",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                day_kind_provider=lambda _: "full",
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=lambda **_: self.fail(
                    "not-ready leader evidence must not enter formal cohort"
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "stage6_not_ready")
        self.assertEqual(payload["stage6Status"], "not_ready")
        self.assertEqual(
            payload["reason"],
            "leader_business_evidence_qualification_empty",
        )
        self.assertEqual(payload["stage6ArtifactPath"], str(artifact))
        self.assertEqual(payload["radarRunId"], "run-partial-trend")
        self.assertEqual(
            payload["formalShadowInputDirs"],
            {"trendRotation": str(trend)},
        )

    def test_preexisting_stage6_artifact_cannot_be_reused(self):
        module = self.module()
        output = StringIO()
        formal_calls = []

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            stage6_output.mkdir()
            old_artifact = (
                stage6_output
                / "stage6-live-five-source-20260903T095900000000.json"
            )
            old_artifact.write_text("{}", encoding="utf-8")

            def stage6_runner(argv, *, stdout, **_):
                stdout.write(json.dumps({
                    "status": "ready_for_review",
                    "artifactPath": str(old_artifact),
                }))
                return 0

            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(stage6_output),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--initialize-sector-state",
                    "--formal-etf", "515790",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                day_kind_provider=lambda _: "full",
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=lambda **kwargs: formal_calls.append(kwargs),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(
            payload["reason"],
            "replay_formal_live_stage6_artifact_not_new",
        )
        self.assertEqual(formal_calls, [])

    def test_legal_empty_stage6_result_still_reaches_formal_entry(self):
        module = self.module()
        output = StringIO()
        formal_calls = []

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            stage6_output = root / "stage6"
            artifact = (
                stage6_output
                / "stage6-live-five-source-20260903T100000000001.json"
            )

            def stage6_runner(argv, *, stdout, **_):
                stage6_output.mkdir()
                artifact.write_text("{}", encoding="utf-8")
                stdout.write(json.dumps({
                    "status": "empty",
                    "artifactPath": str(artifact),
                }))
                return 0

            def formal_runner(**kwargs):
                formal_calls.append(kwargs)
                sample = SimpleNamespace(
                    sample_id="forward-development-empty",
                    radar_run_id="stage6-live-empty",
                    as_of=NOW,
                )
                return SimpleNamespace(
                    plan=SimpleNamespace(role="development"),
                    baseline=SimpleNamespace(
                        replay=SimpleNamespace(samples=[sample]),
                    ),
                    campaign_state=SimpleNamespace(
                        campaign_id="stage9-formal-forward-v1",
                        revision=2,
                        cohorts=[object()],
                    ),
                    manifest_path=root / "cohort" / "formal-cohort-manifest.json",
                )

            code = module.run_cli(
                [
                    "--confirm-live-formal-sequence",
                    "--campaign-dir", str(campaign),
                    "--stage6-output-dir", str(stage6_output),
                    "--cohort-output-dir", str(root / "cohort"),
                    "--initialize-sector-state",
                    "--formal-etf", "515790",
                ],
                stdout=output,
                now_provider=lambda: NOW,
                day_kind_provider=lambda _: "full",
                planner=lambda **_: SimpleNamespace(role="development"),
                stage6_runner=stage6_runner,
                formal_runner=formal_runner,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["stage6Status"], "empty")
        self.assertEqual(len(formal_calls), 1)


if __name__ == "__main__":
    unittest.main()
