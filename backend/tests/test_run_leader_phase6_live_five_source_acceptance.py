import importlib
import json
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from radar.leader_evidence_candidate_plan import (
    LeaderEvidenceCandidateAcceptanceResult,
    LeaderEvidenceCandidateAcceptanceStatus,
    LeaderEvidenceCandidatePlan,
    LeaderEvidenceCandidatePlanItem,
    LeaderEvidenceCandidatePlanStatus,
)
from radar.leader_phase6_live_source_readiness import (
    LeaderPhase6LiveSourceCollectionResult,
    LeaderPhase6LiveSourceReadinessStatus,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceResult,
    LeaderTradabilityLiveAcceptanceStatus,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 25, 13, 30, tzinfo=SHANGHAI_TZ)


class LeaderPhase6LiveFiveSourceAcceptanceCliTests(unittest.TestCase):
    @staticmethod
    def completed_tradability():
        return LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            radar_run_id="run-1",
            as_of=NOW,
            candidate_plan_id="parent-plan",
            candidate_count=383,
        )

    @staticmethod
    def selection(tradability):
        return LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.READY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=383,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.READY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=383,
                candidate_plan=SimpleNamespace(
                    candidate_set_id="child-plan",
                ),
                items=(LeaderEvidenceCandidatePlanItem(
                    index=0,
                    symbol="000001",
                    industry_code="C39",
                    parent_index=12,
                    selection_kind="new",
                    research_partial_score=0.8,
                ),),
                selection_policy_id=(
                    "radar-leader-evidence-candidate-selection-v1:"
                    "new-15:incumbent-15"
                ),
            ),
            tradability=tradability,
            industry_scope=SimpleNamespace(name="bound-industry-scope"),
            industry_scope_snapshot_id="industry-snapshot",
            previous_state_snapshot_id="previous-state-snapshot",
        )

    def module(self):
        try:
            return importlib.import_module(
                "run_leader_phase6_live_five_source_acceptance"
            )
        except ModuleNotFoundError as exc:
            self.fail(f"phase6 five-source CLI missing: {exc}")

    def test_live_orchestration_sends_only_child_plan_to_slow_sources(self):
        module = self.module()
        prepared = object()
        parent_tradability = self.completed_tradability()
        child_tradability = SimpleNamespace(name="child")
        selection = self.selection(child_tradability)
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.NOT_READY,
            candidate_source_packet_sha256=None,
        )
        received = []
        selection_inputs = []

        def select(value, **kwargs):
            selection_inputs.append(kwargs)
            return selection

        def collect(value, **kwargs):
            received.append((value, kwargs.get("industry_scope")))
            return collection

        result = module._run_live(
            started_at=NOW,
            output_dir=Path("/private/tmp/stage6-child-plan-test"),
            prefreeze_runner=lambda **_: (
                prepared,
                parent_tradability,
            ),
            candidate_selection_runner=select,
            source_collection_runner=collect,
            clock=lambda: NOW,
        )

        self.assertEqual(
            received,
            [(child_tradability, selection.industry_scope)],
        )
        expected_state_path = Path(
            "/private/tmp/stage6-child-plan-test/"
            "sector-state-20260825T133000000000.json"
        )
        self.assertEqual(selection_inputs, [{
            "previous_sector_state_path": None,
            "next_sector_state_path": expected_state_path,
        }])
        self.assertEqual(
            result,
            (
                prepared,
                parent_tradability,
                selection,
                expected_state_path,
                collection,
            ),
        )

    def test_legitimate_empty_evidence_scope_skips_slow_sources_without_error(
        self,
    ):
        module = self.module()
        prepared = object()
        parent_tradability = self.completed_tradability()
        selection = LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=383,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.EMPTY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=383,
                reasons=("leader_evidence_candidate_scope_empty",),
                selection_policy_id=(
                    "radar-leader-evidence-candidate-selection-v1:"
                    "new-15:incumbent-15"
                ),
            ),
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        calls = []

        result = module._run_live(
            started_at=NOW,
            output_dir=Path("/private/tmp/stage6-empty-plan-test"),
            prefreeze_runner=lambda **_: (
                prepared,
                parent_tradability,
            ),
            candidate_selection_runner=lambda value, **_: selection,
            source_collection_runner=lambda value, **_: calls.append(value),
            clock=lambda: NOW,
        )

        collection = result[4]
        self.assertEqual(calls, [])
        self.assertEqual(
            collection.status,
            LeaderPhase6LiveSourceReadinessStatus.EMPTY,
        )
        self.assertEqual(
            collection.reasons,
            ("leader_evidence_candidate_scope_empty",),
        )
        self.assertTrue(collection.to_evidence()["validEmptyResult"])

    def test_unfinished_tradability_skips_candidate_and_slow_sources(self):
        module = self.module()
        prepared = object()
        tradability = LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
            radar_run_id="run-1",
            as_of=NOW,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("exchange_official_source_failed",),
        )
        selection_calls = []
        source_calls = []

        result = module._run_live(
            started_at=NOW,
            output_dir=Path("/private/tmp/stage6-upstream-failed-test"),
            prefreeze_runner=lambda **_: (prepared, tradability),
            candidate_selection_runner=lambda value, **_: (
                selection_calls.append(value)
            ),
            source_collection_runner=lambda value, **_: (
                source_calls.append(value)
            ),
            clock=lambda: NOW,
        )

        self.assertEqual(selection_calls, [])
        self.assertEqual(source_calls, [])
        self.assertIsNone(result[2])
        self.assertEqual(
            result[4].reasons,
            ("leader_phase6_tradability_not_completed",),
        )

    def test_cli_persists_upstream_failure_without_fake_state_snapshot(self):
        module = self.module()
        output = StringIO()
        prepared = SimpleNamespace(to_evidence=lambda: {"status": "ready"})
        tradability = LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.SOURCE_FAILED,
            radar_run_id="run-1",
            as_of=NOW,
            candidate_plan_id=None,
            candidate_count=0,
            reasons=("exchange_official_source_failed",),
        )
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED,
            candidate_source_packet_sha256=None,
            reasons=("leader_phase6_tradability_not_completed",),
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **_: (
                    prepared,
                    tradability,
                    None,
                    None,
                    collection,
                ),
            )
            payload = json.loads(output.getvalue())
            evidence = json.loads(
                Path(payload["artifactPath"]).read_text("utf-8")
            )

        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "source_unverified")
        self.assertIsNone(evidence["evidenceCandidateSelection"])
        self.assertIsNone(evidence["sectorStateSnapshotPath"])

    def test_confirmation_is_required_before_live_sources(self):
        module = self.module()
        calls = []
        output = StringIO()

        code = module.run_cli(
            [],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_live_five_source_confirmation_missing",
        )

    def test_output_outside_private_tmp_is_rejected_before_sources(self):
        module = self.module()
        calls = []
        output = StringIO()

        code = module.run_cli(
            [
                "--confirm-live-five-source",
                "--output-dir",
                "/Volumes/HermesSSD/AntigravityData/量化监测-股票",
            ],
            stdout=output,
            now_provider=lambda: NOW,
            live_runner=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(code, 3)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_live_five_source_output_path_unverified",
        )

    def test_sector_state_lineage_is_required_before_live_sources(self):
        module = self.module()
        calls = []
        output = StringIO()

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **kwargs: calls.append(kwargs),
            )

        self.assertEqual(code, 3)
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(output.getvalue())["reason"],
            "leader_phase6_evidence_sector_state_lineage_unverified",
        )

    def test_not_ready_result_writes_auditable_private_artifact(self):
        module = self.module()
        output = StringIO()
        prepared = SimpleNamespace(
            to_evidence=lambda: {"status": "ready"}
        )
        tradability = self.completed_tradability()
        selection = self.selection(tradability)
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.NOT_READY,
            candidate_source_packet_sha256="a" * 64,
            candidate_source_packet={
                "packetSha256": "a" * 64,
                "payload": {"candidatePlan": {}, "reviewQueue": {}},
            },
            verification_candidate_source_packet_sha256="c" * 64,
            verification_candidate_source_packet={
                "packetSha256": "c" * 64,
                "payload": {"candidatePlan": {"parent": True}, "reviewQueue": {}},
            },
            reasons=("business_not_ready",),
        )

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            sector_state_path = Path(directory) / "sector-state.json"
            sector_state_path.write_text("{}", encoding="utf-8")
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **kwargs: (
                    prepared,
                    tradability,
                    selection,
                    sector_state_path,
                    collection,
                ),
            )
            payload = json.loads(output.getvalue())
            artifact = Path(payload["artifactPath"])
            candidate_source = Path(payload["candidateSourcePacketPath"])
            verification_source = Path(
                payload["verificationCandidateSourcePacketPath"]
            )
            evidence = json.loads(artifact.read_text("utf-8"))
            mode = artifact.stat().st_mode & 0o777
            candidate_mode = candidate_source.stat().st_mode & 0o777
            candidate_payload = json.loads(
                candidate_source.read_text("utf-8")
            )
            verification_payload = json.loads(
                verification_source.read_text("utf-8")
            )

        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["validEmptyResult"])
        self.assertEqual(
            evidence["evidenceCandidateSelection"]["candidateCount"],
            1,
        )
        self.assertEqual(
            evidence["evidenceCandidateSelection"][
                "preliminaryCandidateCount"
            ],
            383,
        )
        self.assertEqual(
            evidence["sectorStateSnapshotPath"],
            str(sector_state_path),
        )
        self.assertEqual(
            evidence["sectorStateLineageMode"],
            "initialized",
        )
        self.assertEqual(evidence["collection"]["status"], "not_ready")
        self.assertEqual(mode, 0o600)
        self.assertEqual(candidate_mode, 0o600)
        self.assertEqual(candidate_payload, collection.candidate_source_packet)
        self.assertEqual(
            verification_payload,
            collection.verification_candidate_source_packet,
        )
        self.assertEqual(
            evidence["verificationCandidateSourcePacketPath"],
            str(verification_source),
        )

    def test_ready_for_review_result_returns_success_without_opening_gate(self):
        module = self.module()
        output = StringIO()
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=(
                LeaderPhase6LiveSourceReadinessStatus.READY_FOR_REVIEW
            ),
            candidate_source_packet_sha256="b" * 64,
            candidate_source_packet={
                "packetSha256": "b" * 64,
                "payload": {"candidatePlan": {}, "reviewQueue": {}},
            },
        )
        tradability = SimpleNamespace(to_evidence=lambda: {})
        selection = self.selection(tradability)

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            sector_state_path = Path(directory) / "sector-state.json"
            sector_state_path.write_text("{}", encoding="utf-8")
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **kwargs: (
                    SimpleNamespace(to_evidence=lambda: {}),
                    tradability,
                    selection,
                    sector_state_path,
                    collection,
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ready_for_review")
        self.assertFalse(payload["validEmptyResult"])
        self.assertFalse(payload["gate"]["formalGateReady"])

    def test_valid_empty_result_returns_success_without_claiming_five_sources(
        self,
    ):
        module = self.module()
        output = StringIO()
        prepared = SimpleNamespace(
            to_evidence=lambda: {"status": "ready"}
        )
        tradability = SimpleNamespace(
            to_evidence=lambda: {"status": "completed"}
        )
        selection = LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=383,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.EMPTY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=383,
                reasons=("leader_evidence_candidate_scope_empty",),
                selection_policy_id=(
                    "radar-leader-evidence-candidate-selection-v1:"
                    "new-15:incumbent-15"
                ),
            ),
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.EMPTY,
            candidate_source_packet_sha256=None,
            reasons=("leader_evidence_candidate_scope_empty",),
        )

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            sector_state_path = Path(directory) / "sector-state.json"
            sector_state_path.write_text("{}", encoding="utf-8")
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=lambda **kwargs: (
                    prepared,
                    tradability,
                    selection,
                    sector_state_path,
                    collection,
                ),
            )
            payload = json.loads(output.getvalue())
            evidence = json.loads(
                Path(payload["artifactPath"]).read_text("utf-8")
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "empty")
        self.assertTrue(payload["validEmptyResult"])
        self.assertFalse(payload["fiveSourceReadyForReview"])
        self.assertFalse(payload["gate"]["formalGateReady"])
        self.assertFalse(payload["gate"]["stateTransitionAllowed"])
        self.assertTrue(evidence["collection"]["validEmptyResult"])

    def test_known_prefreeze_failure_is_not_hidden_as_generic_execution_error(self):
        module = self.module()
        output = StringIO()

        def fail(**_kwargs):
            raise ValueError("leader_phase6_public_prepare_sector_unverified")

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=fail,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(
            payload["reason"],
            "leader_phase6_public_prepare_sector_unverified",
        )
        self.assertFalse(payload["fiveSourceReadyForReview"])

    def test_security_master_prefreeze_failure_is_not_hidden_as_generic_error(self):
        module = self.module()
        output = StringIO()

        def fail(**_kwargs):
            raise ValueError(
                "leader_phase6_public_prepare_security_master_unverified"
            )

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory:
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                live_runner=fail,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(
            payload["reason"],
            "leader_phase6_public_prepare_security_master_unverified",
        )
        self.assertFalse(payload["fiveSourceReadyForReview"])


if __name__ == "__main__":
    unittest.main()
