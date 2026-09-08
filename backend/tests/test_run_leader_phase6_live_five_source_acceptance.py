import importlib
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from radar.contracts import MarketFeatureSnapshot
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
    def market_snapshot():
        identities = (
            ("sse_composite", "000001", "sse", "sh000001", "上证指数"),
            ("szse_component", "399001", "szse", "sz399001", "深证成指"),
            ("chinext", "399006", "szse", "sz399006", "创业板指"),
            ("star50", "000688", "sse", "sh000688", "科创50"),
        )
        completeness = {
            "expectedCount": 100,
            "returnedCount": 100,
            "validCount": 100,
            "rowCoverage": 1.0,
            "requiredFieldCoverage": {"change_percent": 1.0},
            "isComplete": True,
            "reasons": [],
        }
        return MarketFeatureSnapshot.model_validate({
            "radarRunId": "run-1",
            "indexBatchId": "run-1-indices",
            "quoteBatchId": "run-1-quotes",
            "asOf": NOW,
            "sourceTime": NOW,
            "fetchedAt": NOW,
            "indices": [{
                "indexKey": key,
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "sourceSymbol": source_symbol,
                "sourceTime": NOW,
                "fetchedAt": NOW,
                "price": 1000 + index,
                "changePercent": (1.0, 0.5, 0.8, -0.1)[index],
                "source": "tencent_finance",
            } for index, (key, symbol, exchange, source_symbol, name)
                in enumerate(identities)],
            "indexCompleteness": {
                **completeness,
                "expectedCount": 4,
                "returnedCount": 4,
                "validCount": 4,
            },
            "breadth": {
                "advancers": 70,
                "decliners": 25,
                "flat": 5,
                "unavailable": 0,
                "completeness": completeness,
            },
            "turnover": {
                "rawValue": 12345.0,
                "contributingCount": 100,
                "unitStatus": "verified",
                "formalUsable": True,
                "completeness": completeness,
                "reasons": [],
            },
            "excludedEtfCount": 10,
            "duplicateSymbols": [],
            "unknownSymbols": [],
        })

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

    def test_secure_artifact_read_rejects_regular_file_replacement(self):
        module = self.module()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "artifact.json"
            identity = module._write_new_json(path, {"runId": "original"})
            replacement = Path(directory) / "replacement.json"
            replacement.write_bytes(b'{"runId":"replacement"}')
            os.replace(replacement, path)

            with self.assertRaisesRegex(
                ValueError,
                "leader_phase6_formal_shadow_context_unverified",
            ):
                module._read_private_tmp_regular_bytes(
                    path,
                    expected_identity=identity,
                )

    def test_sector_identity_rejects_replaced_ancestor_and_symlink_ancestor(self):
        module = self.module()

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            parent = root / "sector-parent"
            parent.mkdir()
            path = parent / "sector.json"
            path.write_bytes(b'{"runId":"original"}')
            identity = module._capture_private_tmp_regular_file_identity(path)

            detached = root / "detached"
            os.replace(parent, detached)
            parent.mkdir()
            (parent / "sector.json").write_bytes(b'{"runId":"replacement"}')
            with self.assertRaisesRegex(
                ValueError,
                "leader_phase6_formal_shadow_context_unverified",
            ):
                module._read_private_tmp_regular_bytes(
                    path,
                    expected_identity=identity,
                )

            link = root / "sector-link"
            link.symlink_to(detached, target_is_directory=True)
            with self.assertRaisesRegex(
                ValueError,
                "leader_phase6_formal_shadow_context_unverified",
            ):
                module._capture_private_tmp_regular_file_identity(
                    link / "sector.json"
                )

    def test_market_research_generator_emits_full_replayable_snapshot(self):
        module = self.module()
        snapshot = self.market_snapshot()
        tradability = LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            radar_run_id="run-1",
            as_of=NOW,
            candidate_plan_id="parent-plan",
            candidate_count=383,
            runtime_inputs=SimpleNamespace(
                market_snapshot=snapshot.model_dump(
                    mode="json",
                    by_alias=True,
                )
            ),
        )

        state, snapshot_evidence = (
            module._market_research_evidence_bundle(tradability)
        )
        snapshot_payload = snapshot_evidence["snapshot"]
        canonical = json.dumps(
            snapshot_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["state"], "strong")
        self.assertEqual(
            snapshot_evidence["contractId"],
            "radar-market-feature-snapshot-evidence-v1",
        )
        self.assertEqual(
            snapshot_evidence["snapshotSha256"],
            hashlib.sha256(canonical).hexdigest(),
        )
        replayed = MarketFeatureSnapshot.model_validate(snapshot_payload)
        self.assertEqual(replayed.index_batch_id, "run-1-indices")
        self.assertEqual(replayed.quote_batch_id, "run-1-quotes")

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
        tradability = LeaderTradabilityLiveAcceptanceResult(
            status=LeaderTradabilityLiveAcceptanceStatus.COMPLETED,
            radar_run_id="run-1",
            as_of=NOW,
            candidate_plan_id="parent-plan",
            candidate_count=383,
            runtime_inputs=SimpleNamespace(
                market_snapshot=self.market_snapshot().model_dump(
                    mode="json",
                    by_alias=True,
                )
            ),
        )
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
            payload["formalShadowInputPublishStatus"], "not_requested"
        )
        self.assertEqual(payload["formalShadowInputDirs"], {})
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
        self.assertEqual(evidence["marketResearchState"]["status"], "ready")
        self.assertEqual(
            evidence["marketFeatureSnapshotEvidence"]["contractId"],
            "radar-market-feature-snapshot-evidence-v1",
        )
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

    def test_explicit_shadow_input_context_uses_real_lock_and_monotonic_duration(self):
        module = self.module()
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleInputs,
            Stage6FormalShadowInputBundlePublication,
        )

        output = StringIO()
        acquired = []
        published = []

        class Lock:
            def acquire(self, *, blocking):
                acquired.append(blocking)
                return True

            def release(self):
                acquired.append("released")

        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.EMPTY,
            candidate_source_packet_sha256=None,
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        selection = LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=1,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.EMPTY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=1,
                reasons=("leader_evidence_candidate_scope_empty",),
                selection_policy_id="policy",
            ),
            reasons=("leader_evidence_candidate_scope_empty",),
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            sector_state_path = Path(directory) / "sector-state.json"
            sector_state_path.write_text("{}", encoding="utf-8")
            context = Stage6FormalShadowInputBundleInputs(
                output_root=Path(directory),
                collection_policy_json_bytes=b"policy",
                calendar_envelope_json_bytes=b"envelope",
                calendar_document_bytes=b"calendar",
            )

            def publish(**kwargs):
                published.append(kwargs)
                return Stage6FormalShadowInputBundlePublication(
                    input_dirs={"trendRotation": Path(directory) / "trend"}
                )

            moments = iter((100.0, 101.25))
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir",
                    directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                monotonic_clock=lambda: next(moments),
                lock_factory=lambda _path: Lock(),
                formal_shadow_input_context=context,
                formal_shadow_input_publisher=publish,
                live_runner=lambda **kwargs: (
                    SimpleNamespace(to_evidence=lambda: {"status": "ready"}),
                    SimpleNamespace(
                        radar_run_id="run-1", as_of=NOW,
                        to_evidence=lambda: {"status": "completed"},
                    ),
                    selection,
                    sector_state_path,
                    collection,
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(acquired, [False, "released"])
        self.assertEqual(published[0]["duration_ms"], 1250)
        self.assertTrue(published[0]["lock_acquired"])
        self.assertEqual(
            published[0]["sector_snapshot_json_bytes"], b"{}"
        )
        self.assertEqual(
            payload["formalShadowInputPublishStatus"], "published"
        )
        self.assertEqual(
            payload["formalShadowInputDirs"],
            {"trendRotation": str(Path(directory) / "trend")},
        )

    def test_cli_shadow_input_root_builds_calendar_and_policy_from_same_prepared_run(self):
        module = self.module()
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundlePublication,
        )

        raw_calendar = b"official-sse-calendar"
        raw_sha = "sha256:" + hashlib.sha256(raw_calendar).hexdigest()
        prepared_evidence = {
            "contractId": "radar-leader-phase6-prepared-historical-inputs-v1",
            "radarRunId": "run-1",
            "classificationDocumentSha256": "a" * 64,
            "calendarDocumentRawContentAvailable": True,
            "calendarDocumentRawContentSha256": raw_sha,
            "preparedAt": NOW.isoformat(),
        }
        prepared = SimpleNamespace(
            calendar_document_raw_content=raw_calendar,
            calendar_document_raw_content_sha256=raw_sha,
            calendar_evidence=SimpleNamespace(
                source_url=(
                    "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
                ),
                fetched_at=NOW,
                content_sha256=raw_sha,
            ),
            to_evidence=lambda: prepared_evidence,
        )
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.EMPTY,
            candidate_source_packet_sha256=None,
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        selection = LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=1,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.EMPTY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=1,
                reasons=("leader_evidence_candidate_scope_empty",),
                selection_policy_id="policy",
            ),
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        published = []

        class Lock:
            def acquire(self, *, blocking):
                return True

            def release(self):
                pass

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            sector_state_path = Path(directory) / "sector-state.json"
            sector_state_path.write_text("{}", encoding="utf-8")
            output = StringIO()
            code = module.run_cli(
                [
                    "--confirm-live-five-source",
                    "--initialize-sector-state",
                    "--output-dir", directory,
                    "--formal-shadow-input-root", directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                monotonic_clock=iter((10.0, 10.2)).__next__,
                lock_factory=lambda _path: Lock(),
                formal_shadow_input_publisher=lambda **kwargs: (
                    published.append(kwargs)
                    or Stage6FormalShadowInputBundlePublication(input_dirs={})
                ),
                live_runner=lambda **_kwargs: (
                    prepared,
                    SimpleNamespace(
                        radar_run_id="run-1", as_of=NOW,
                        to_evidence=lambda: {"status": "completed"},
                    ),
                    selection,
                    sector_state_path,
                    collection,
                ),
            )
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(published[0]["calendar_document_bytes"], raw_calendar)
        envelope = json.loads(published[0]["calendar_envelope_json_bytes"])
        self.assertEqual(envelope["sourceDocumentSha256"], raw_sha.removeprefix("sha256:"))
        self.assertEqual(envelope["observedThrough"], "2026-08-25")
        policy = json.loads(published[0]["collection_policy_json_bytes"])
        self.assertEqual(
            policy["contractId"],
            "radar-formal-shadow-collection-policy-v1",
        )
        self.assertEqual(payload["formalShadowInputPublishStatus"], "not_published")

    def test_requested_shadow_publication_missing_raw_fails_closed_without_breaking_stage6(self):
        module = self.module()
        output = StringIO()
        calls = []
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.EMPTY,
            candidate_source_packet_sha256=None,
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        selection = LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=1,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.EMPTY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=1,
                reasons=("leader_evidence_candidate_scope_empty",),
                selection_policy_id="policy",
            ),
            reasons=("leader_evidence_candidate_scope_empty",),
        )

        class Lock:
            def acquire(self, *, blocking):
                return True

            def release(self):
                pass

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            sector = Path(directory) / "sector.json"
            sector.write_text("{}", encoding="utf-8")
            code = module.run_cli(
                [
                    "--confirm-live-five-source", "--initialize-sector-state",
                    "--output-dir", directory,
                    "--formal-shadow-input-root", directory,
                ],
                stdout=output,
                now_provider=lambda: NOW,
                monotonic_clock=iter((1.0, 2.0)).__next__,
                lock_factory=lambda _path: Lock(),
                formal_shadow_input_publisher=lambda **kwargs: calls.append(kwargs),
                live_runner=lambda **_kwargs: (
                    SimpleNamespace(
                        calendar_document_raw_content=None,
                        calendar_document_raw_content_sha256=None,
                        calendar_evidence=None,
                        to_evidence=lambda: {"status": "ready"},
                    ),
                    SimpleNamespace(to_evidence=lambda: {}),
                    selection, sector, collection,
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(payload["formalShadowInputPublishStatus"], "not_published")
        self.assertEqual(
            payload["formalShadowInputPublishReason"],
            "leader_phase6_formal_shadow_calendar_raw_unavailable",
        )

    def test_shadow_lock_contention_skips_publication_but_preserves_stage6_result(self):
        module = self.module()
        output = StringIO()
        live_calls = []
        publish_calls = []
        collection = LeaderPhase6LiveSourceCollectionResult(
            status=LeaderPhase6LiveSourceReadinessStatus.EMPTY,
            candidate_source_packet_sha256=None,
            reasons=("leader_evidence_candidate_scope_empty",),
        )
        selection = LeaderEvidenceCandidateAcceptanceResult(
            status=LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
            preliminary_candidate_plan_id="parent-plan",
            preliminary_candidate_count=1,
            evidence_plan=LeaderEvidenceCandidatePlan(
                status=LeaderEvidenceCandidatePlanStatus.EMPTY,
                preliminary_candidate_plan_id="parent-plan",
                preliminary_candidate_count=1,
                reasons=("leader_evidence_candidate_scope_empty",),
                selection_policy_id="policy",
            ),
            reasons=("leader_evidence_candidate_scope_empty",),
        )

        class ContendedLock:
            def acquire(self, *, blocking):
                return False

            def release(self):
                self.fail("unacquired lock must not be released")

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            sector = Path(directory) / "sector.json"
            sector.write_text("{}", encoding="utf-8")
            code = module.run_cli(
                [
                    "--confirm-live-five-source", "--initialize-sector-state",
                    "--output-dir", directory,
                    "--formal-shadow-input-root", directory,
                ],
                stdout=output,
                lock_factory=lambda _path: ContendedLock(),
                formal_shadow_input_publisher=lambda **kwargs: publish_calls.append(kwargs),
                live_runner=lambda **kwargs: (
                    live_calls.append(kwargs)
                    or (
                        SimpleNamespace(to_evidence=lambda: {}),
                        SimpleNamespace(to_evidence=lambda: {}),
                        selection, sector, collection,
                    )
                ),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(live_calls), 1)
        self.assertEqual(publish_calls, [])
        self.assertEqual(payload["formalShadowInputPublishStatus"], "contended")

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
