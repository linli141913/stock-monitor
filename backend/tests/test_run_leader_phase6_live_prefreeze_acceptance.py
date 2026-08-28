import importlib
import json
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 25, 13, 30, tzinfo=SHANGHAI_TZ)


class LeaderPhase6LivePrefreezeAcceptanceCliTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module(
                "run_leader_phase6_live_prefreeze_acceptance"
            )
        except ModuleNotFoundError as exc:
            self.fail(f"phase6 live prefreeze CLI missing: {exc}")

    def test_confirmation_is_required_before_any_public_source_call(self):
        module = self.module()
        calls = []
        output = StringIO()

        exit_code = module.run_cli(
            [],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **_: calls.append("live"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_live_prefreeze_confirmation_missing",
        )

    def test_output_outside_private_tmp_is_rejected_before_live_run(self):
        module = self.module()
        calls = []
        output = StringIO()

        exit_code = module.run_cli(
            [
                "--confirm-live-prefreeze",
                "--output-dir",
                "/Volumes/HermesSSD/AntigravityData/量化监测-股票",
            ],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **_: calls.append("live"),
        )

        self.assertEqual(exit_code, 3)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_live_prefreeze_output_path_unverified",
        )

    def test_previous_state_outside_private_tmp_is_rejected_before_live_run(self):
        module = self.module()
        calls = []
        output = StringIO()

        exit_code = module.run_cli(
            [
                "--confirm-live-prefreeze",
                "--previous-sector-state",
                "/tmp/forged-sector-state.json",
            ],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **_: calls.append("live"),
        )

        self.assertEqual(exit_code, 3)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_live_prefreeze_state_path_unverified",
        )

    def test_sector_state_lineage_requires_explicit_init_or_previous_snapshot(self):
        module = self.module()
        calls = []
        output = StringIO()

        exit_code = module.run_cli(
            ["--confirm-live-prefreeze"],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **_: calls.append("live"),
        )

        self.assertEqual(exit_code, 3)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_sector_state_lineage_unverified",
        )

    def test_sector_state_init_and_previous_snapshot_are_mutually_exclusive(self):
        module = self.module()
        calls = []
        output = StringIO()

        exit_code = module.run_cli(
            [
                "--confirm-live-prefreeze",
                "--initialize-sector-state",
                "--previous-sector-state",
                "/private/tmp/sector-state.json",
            ],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **_: calls.append("live"),
        )

        self.assertEqual(exit_code, 3)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_sector_state_lineage_unverified",
        )

    def test_success_writes_non_overwriting_private_artifact(self):
        module = self.module()
        output = StringIO()
        prepared = SimpleNamespace(
            to_evidence=lambda: {
                "contractId": "prepared-v1",
                "preparedAt": NOW.isoformat(),
            }
        )
        acceptance = SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            phase6_prefrozen_inputs=object(),
            to_evidence=lambda: {
                "contractId": "acceptance-v1",
                "status": "completed",
                "phase6PrefrozenInputsReady": True,
            },
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            exit_code = module.run_cli(
                [
                    "--confirm-live-prefreeze",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **_: (prepared, acceptance),
                sector_state_finalizer=lambda **_: {
                    "status": "ready",
                    "stateSnapshotPath": (
                        f"{directory}/sector-state.json"
                    ),
                    "industryScopeSnapshotId": "scope-1",
                    "evidence": {"status": "ready"},
                },
            )
            payload = json.loads(output.getvalue())
            artifact = Path(payload["artifactPath"])
            artifact_payload = json.loads(artifact.read_text("utf-8"))
            artifact_mode = artifact.stat().st_mode & 0o777

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["phase6PrefrozenInputsReady"])
        self.assertTrue(payload["sectorStateReady"])
        self.assertEqual(payload["industryScopeSnapshotId"], "scope-1")
        self.assertEqual(artifact_payload["acceptance"]["status"], "completed")
        self.assertEqual(artifact_payload["sectorState"]["status"], "ready")
        self.assertEqual(artifact_mode, 0o600)

    def test_prefreeze_success_stays_not_ready_when_sector_state_blocks(self):
        module = self.module()
        output = StringIO()
        prepared = SimpleNamespace(to_evidence=lambda: {"status": "ready"})
        acceptance = SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            phase6_prefrozen_inputs=object(),
            to_evidence=lambda: {"status": "completed"},
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            exit_code = module.run_cli(
                [
                    "--confirm-live-prefreeze",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **_: (prepared, acceptance),
                sector_state_finalizer=lambda **_: {
                    "status": "blocked",
                    "reasons": ["sector_state_metrics_unverified"],
                },
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertTrue(payload["phase6PrefrozenInputsReady"])
        self.assertFalse(payload["sectorStateReady"])


if __name__ == "__main__":
    unittest.main()
