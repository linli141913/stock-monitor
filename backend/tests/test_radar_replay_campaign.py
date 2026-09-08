import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_radar_replay_objective_outcomes import (
    RadarReplayObjectiveOutcomeTests as _ObjectiveOutcomeHelper,
)
from tests.test_radar_replay_outcome_daily_provider import (
    DATES,
    RadarReplayOutcomeDailyProviderTests as _DailyProviderHelper,
)


UTC = timezone.utc


class RadarReplayCampaignTests(unittest.TestCase):
    @staticmethod
    def trusted_formal_register(**kwargs):
        from radar.replay_campaign import (
            _FORMAL_REGISTRATION_TOKEN,
            register_cohort,
        )

        return register_cohort(
            _formal_entry_token=_FORMAL_REGISTRATION_TOKEN,
            **kwargs,
        )

    def write_model(self, directory: Path, name: str, model, manifest_key: str):
        directory.mkdir()
        path = directory / name
        path.write_text(
            model.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (directory / "manifest.json").write_text(json.dumps({
            "files": {manifest_key: {"path": name, "sha256": digest}},
        }), encoding="utf-8")
        return path

    def inputs(self, root: Path, *, role="development", day=1):
        from radar.replay_assembly import FORWARD_MANIFEST_ID
        from radar.replay_contracts import RadarReplayInput
        from tests.test_radar_replay_service import RadarReplayServiceTests

        helper = _ObjectiveOutcomeHelper()
        task = helper.task_bundle()
        output = helper.output_bundle()
        as_of = datetime(2026, 9, day, 6, 30, tzinfo=UTC)
        task_payload = task.model_dump(mode="python", by_alias=True)
        task_payload["taskBundleId"] = f"tasks-{role}-{day}"
        task_payload["createdAt"] = as_of
        task_payload["samples"][0].update({
            "sampleId": f"sample-{role}-{day}",
            "radarRunId": f"radar-{role}-{day}",
            "role": role,
            "asOf": as_of,
        })
        forward_dir = root / f"forward-{role}-{day}"
        forward_dir.mkdir()
        source_path = forward_dir / "source-snapshots.json"
        source_path.write_text(json.dumps({
            "radarRunId": f"radar-{role}-{day}",
            "sampleAsOf": as_of.isoformat(),
            "sources": {
                "industry": {
                    "records": [
                        {
                            "recordStatus": "accepted",
                            "identityStatus": "exact",
                            "divisionCode": "C39",
                            "securityIdentity": "000001",
                        },
                        {
                            "recordStatus": "accepted",
                            "identityStatus": "verified_alias",
                            "divisionCode": "C39",
                            "securityIdentity": "000002",
                        },
                        {
                            "recordStatus": "accepted",
                            "identityStatus": "exact",
                            "divisionCode": "C40",
                            "securityIdentity": "000002",
                        },
                    ],
                },
            },
        }), encoding="utf-8")
        task_payload["samples"][0]["sourceSnapshots"] = {
            "path": str(source_path),
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        }
        replay_payload = RadarReplayServiceTests().payload()
        replay_payload["replayRunId"] = f"forward-replay-{role}-{day}"
        replay_payload["createdAt"] = as_of.isoformat()
        replay_payload["samples"][0].update({
            "sampleId": f"sample-{role}-{day}",
            "radarRunId": f"radar-{role}-{day}",
            "role": role,
            "asOf": as_of.isoformat(),
        })
        replay_payload["samples"][0]["evidence"] = [
            item for item in replay_payload["samples"][0]["evidence"]
            if item["evidenceId"] != "leader-output-1"
        ]
        replay = RadarReplayInput.model_validate(replay_payload)
        replay_path = forward_dir / "replay-input.json"
        replay_path.write_text(
            replay.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        replay_sha = hashlib.sha256(replay_path.read_bytes()).hexdigest()
        (forward_dir / "manifest.json").write_text(json.dumps({
            "contractId": FORWARD_MANIFEST_ID,
            "replayRunId": replay.replay_run_id,
            "sampleRole": role,
            "files": {
                "replayInput": {
                    "path": replay_path.name,
                    "sha256": replay_sha,
                },
            },
        }), encoding="utf-8")
        task_payload["samples"][0]["replayInput"] = {
            "path": str(replay_path),
            "sha256": replay_sha,
        }
        output_payload = output.model_dump(mode="python", by_alias=True)
        output_payload["bundleId"] = f"outputs-{role}-{day}"
        output_payload["createdAt"] = as_of
        output_payload["samples"][0].update({
            "sampleId": f"sample-{role}-{day}",
            "radarRunId": f"radar-{role}-{day}",
            "asOf": as_of,
        })
        for evidence in output_payload["samples"][0]["evidence"]:
            evidence["sourceTime"] = as_of
            evidence["fetchedAt"] = as_of
        task = type(task).model_validate(task_payload)
        output = type(output).model_validate(output_payload)
        task_path = self.write_model(
            root / f"task-{role}-{day}",
            "label-tasks.json",
            task,
            "labelTasks",
        )
        output_path = self.write_model(
            root / f"output-{role}-{day}",
            "output-bundle.json",
            output,
            "outputBundle",
        )
        return task_path, output_path, task, output

    def write_snapshot(self, root: Path, snapshot, index: int):
        return self.write_model(
            root / f"daily-{index}",
            "daily-outcome-snapshot.json",
            snapshot,
            "dailyOutcomeSnapshot",
        )

    @staticmethod
    def matching_snapshot(snapshot, task, output):
        from radar.replay_outcome_daily_capture import (
            _daily_snapshot_id,
            _membership_digest,
        )

        payload = snapshot.model_dump(mode="python", by_alias=True)
        sample_id = task.samples[0].sample_id
        memberships = payload["sectorMembershipsBySample"].pop("sample-1")
        payload.update({
            "taskBundleId": task.task_bundle_id,
            "outputBundleId": output.bundle_id,
            "sectorMembershipsBySample": {sample_id: memberships},
        })
        payload["snapshotId"] = _daily_snapshot_id(
            task_bundle_id=task.task_bundle_id,
            output_bundle_id=output.bundle_id,
            trade_date=snapshot.trade_date,
            security_digest=snapshot.source_ids[0].rsplit(
                ":sha256:", 1
            )[1],
            index_digest=snapshot.source_ids[1].rsplit(
                ":sha256:", 1
            )[1],
            membership_digest=_membership_digest(
                payload["sectorMembershipsBySample"]
            ),
        )
        return type(snapshot).model_validate(payload)

    @staticmethod
    def collect_ready_outcome(**kwargs):
        from radar.replay_objective_outcomes import (
            ObjectiveOutcomeObservation,
            collect_replay_objective_outcomes,
            load_replay_objective_inputs,
        )

        task, output = load_replay_objective_inputs(
            task_bundle_path=kwargs["task_bundle_path"],
            output_bundle_path=kwargs["output_bundle_path"],
        )

        def provider(request):
            metrics = {
                "market": {"environmentCorrect": True},
                "sector": {"mainlineDurationDays": 3.0},
                "etf": {"return5d": 0.04},
                "leader": {"relativeSectorReturn5d": 0.02},
            }[request.domain]
            return ObjectiveOutcomeObservation(
                status="ready",
                observedThrough=kwargs["evaluated_at"],
                sourceIds=[
                    f"official-{request.domain}-{request.target_id}"
                ],
                outcomeMetrics=metrics,
                reasons=[],
            )

        return collect_replay_objective_outcomes(
            task_bundle=task,
            output_bundle=output,
            output_dir=kwargs["output_dir"],
            evaluated_at=kwargs["evaluated_at"],
            providers=kwargs.get("providers") or {
                domain: provider
                for domain in ("market", "sector", "etf", "leader")
            },
            day_kind_provider=kwargs["day_kind_provider"],
            daily_snapshot_ids=kwargs["daily_snapshot_ids"],
        )

    @staticmethod
    def write_valid_assembly(**kwargs):
        from radar.replay_assembly import ASSEMBLY_MANIFEST_ID
        from radar.replay_contracts import RadarReplayInput
        from radar.replay_service import build_replay_quality_report
        from tests.test_radar_replay_service import RadarReplayServiceTests

        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True)
        replay = RadarReplayInput.model_validate(
            RadarReplayServiceTests().partitioned_payload()
        )
        report = build_replay_quality_report(replay)
        replay_path = output_dir / "replay-input.json"
        quality_path = output_dir / "quality-report.json"
        replay_path.write_text(
            replay.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        quality_path.write_text(
            report.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        replay_sha = hashlib.sha256(replay_path.read_bytes()).hexdigest()
        quality_sha = hashlib.sha256(quality_path.read_bytes()).hexdigest()
        (output_dir / "manifest.json").write_text(json.dumps({
            "contractId": ASSEMBLY_MANIFEST_ID,
            "replayRunId": replay.replay_run_id,
            "createdAt": replay.created_at.isoformat(),
            "status": report.status,
            "files": {
                "replayInput": {
                    "path": replay_path.name,
                    "sha256": replay_sha,
                },
                "qualityReport": {
                    "path": quality_path.name,
                    "sha256": quality_sha,
                },
            },
        }), encoding="utf-8")
        return SimpleNamespace(
            replay=replay,
            report=report,
            replay_input_path=replay_path,
            quality_report_path=quality_path,
        )

    def test_diagnostic_campaign_registers_exact_pair_and_existing_snapshot(self):
        from radar.replay_campaign import create_campaign, register_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="diagnostic-20260902",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            snapshot = self.matching_snapshot(
                _DailyProviderHelper.snapshot(
                    date(2026, 9, 1), 0
                ),
                task,
                output,
            )
            snapshot_path = self.write_snapshot(root, snapshot, 0)
            state = register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=[snapshot_path],
                updated_at=datetime(2026, 9, 2, 8, 1, tzinfo=UTC),
            )

            self.assertEqual(state.revision, 2)
            self.assertEqual(len(state.cohorts), 1)
            self.assertEqual(state.cohorts[0].task_bundle.semantic_id,
                             task.task_bundle_id)
            self.assertEqual(state.cohorts[0].output_bundle.semantic_id,
                             output.bundle_id)
            self.assertEqual(len(state.cohorts[0].daily_snapshots), 1)
            self.assertEqual(
                state.cohorts[0].daily_snapshots[0].captured_at,
                snapshot.captured_at,
            )

    def test_registration_time_cannot_predate_daily_snapshot_capture(self):
        from radar.replay_campaign import (
            create_campaign,
            load_campaign,
            register_cohort,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="snapshot-time-causal",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            snapshot = self.matching_snapshot(
                _DailyProviderHelper.snapshot(date(2026, 9, 1), 0),
                task,
                output,
            )
            snapshot_path = self.write_snapshot(root, snapshot, 0)
            before_capture = snapshot.captured_at - timedelta(minutes=1)
            self.assertGreater(before_capture, task.created_at)
            self.assertGreater(before_capture, output.created_at)

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_updatedAt_before_snapshot",
            ):
                register_cohort(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    daily_snapshot_paths=[snapshot_path],
                    updated_at=before_capture,
                )

            state = load_campaign(campaign_dir)
            self.assertEqual(state.revision, 1)
            self.assertEqual(state.cohorts, [])

    def test_formal_campaign_rejects_direct_low_level_registration(self):
        from radar.replay_campaign import create_campaign, load_campaign, register_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="formal-direct-registration",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, _, _ = self.inputs(root)

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_formal_registration_entrypoint_required",
            ):
                register_cohort(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    updated_at=datetime(2026, 9, 2, 8, 1, tzinfo=UTC),
                )

            state = load_campaign(campaign_dir)
            self.assertEqual(state.revision, 1)
            self.assertEqual(state.cohorts, [])

    def test_register_rejects_reidentified_membership_not_from_frozen_source(self):
        from radar.replay_campaign import create_campaign, register_cohort
        from radar.replay_outcome_daily_capture import (
            _daily_snapshot_id,
            _membership_digest,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="forged-membership",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            snapshot = self.matching_snapshot(
                _DailyProviderHelper.snapshot(date(2026, 9, 1), 0),
                task,
                output,
            )
            payload = snapshot.model_dump(mode="python", by_alias=True)
            payload["sectorMembershipsBySample"][
                task.samples[0].sample_id
            ]["C39"] = ["000001"]
            payload["snapshotId"] = _daily_snapshot_id(
                task_bundle_id=task.task_bundle_id,
                output_bundle_id=output.bundle_id,
                trade_date=snapshot.trade_date,
                security_digest=snapshot.source_ids[0].rsplit(
                    ":sha256:", 1
                )[1],
                index_digest=snapshot.source_ids[1].rsplit(
                    ":sha256:", 1
                )[1],
                membership_digest=_membership_digest(
                    payload["sectorMembershipsBySample"]
                ),
            )
            forged = type(snapshot).model_validate(payload)
            snapshot_path = self.write_snapshot(root, forged, 99)

            with self.assertRaisesRegex(
                ValueError,
                "daily_outcome_scope_mismatch",
            ):
                register_cohort(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    daily_snapshot_paths=[snapshot_path],
                    updated_at=datetime(2026, 9, 2, 8, 1, tzinfo=UTC),
                )

    def test_registry_detects_content_tampering(self):
        from radar.replay_campaign import create_campaign, load_campaign

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = Path(directory) / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="tamper-test",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            latest = json.loads(
                (campaign_dir / "latest.json").read_text(encoding="utf-8")
            )
            state_path = campaign_dir / latest["statePath"]
            state_path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError, "replay_campaign_state_hash_mismatch"
            ):
                load_campaign(campaign_dir)

    def test_latest_pointer_updated_at_must_match_state(self):
        from radar.replay_campaign import create_campaign, load_campaign

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = Path(directory) / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="latest-time-mismatch",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            latest_path = campaign_dir / "latest.json"
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            latest["updatedAt"] = "2026-09-01T07:59:00+00:00"
            latest_path.write_text(json.dumps(latest), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_state_identity_mismatch",
            ):
                load_campaign(campaign_dir)

    def test_campaign_state_updated_at_cannot_predate_registered_sample(self):
        from radar.replay_campaign import (
            ReplayCampaignState,
            create_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="state-time-causal",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
            )
            task_path, output_path, task, _ = self.inputs(root)
            registered = self.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            payload = registered.model_dump(mode="python", by_alias=True)
            payload["updatedAt"] = task.samples[0].as_of.replace(minute=29)

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_updatedAt_before_sample",
            ):
                ReplayCampaignState.model_validate(payload)

    def test_invalid_campaign_identity_does_not_leave_partial_directory(self):
        from radar.replay_campaign import create_campaign

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = Path(directory) / "invalid-campaign"

            with self.assertRaises(ValueError):
                create_campaign(
                    campaign_dir=campaign_dir,
                    campaign_id="   ",
                    mode="formal_sequence",
                    updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
                )

            self.assertFalse(campaign_dir.exists())

    def test_cohort_verification_rejects_hash_valid_invalid_outcome_bundle(self):
        from radar.replay_campaign import (
            CampaignOutcome,
            _artifact_ref,
            _verify_cohort_artifacts,
            create_campaign,
            register_cohort,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="invalid-outcome-content",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, _, _ = self.inputs(root)
            state = register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
            )
            artifact_dir = root / "invalid-outcome"
            artifact_dir.mkdir()
            outcome_path = artifact_dir / "objective-outcomes.json"
            outcome_path.write_text("{}", encoding="utf-8")
            cohort = state.cohorts[0].model_copy(update={
                "outcome": CampaignOutcome(
                    status="ready",
                    collectionId="collection-claimed",
                    dailySnapshotIds=["snapshot-claimed"],
                    outcomeArtifact=_artifact_ref(
                        outcome_path,
                        "collection-claimed",
                    ),
                    labelArtifact=None,
                ),
            })

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_outcome_artifact_unverified",
            ):
                _verify_cohort_artifacts(cohort)

    def test_assembly_verification_rejects_hash_valid_invalid_payloads(self):
        from radar.replay_campaign import (
            CampaignAssembly,
            _artifact_ref,
            _verify_assembly_artifacts,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            replay_path = root / "replay-input.json"
            quality_path = root / "quality-report.json"
            replay_path.write_text("{}", encoding="utf-8")
            quality_path.write_text("{}", encoding="utf-8")
            assembly = CampaignAssembly(
                inputDigest="a" * 64,
                replayRunId="replay-claimed",
                qualityStatus="ready",
                replayArtifact=_artifact_ref(
                    replay_path,
                    "replay-claimed",
                ),
                qualityArtifact=_artifact_ref(
                    quality_path,
                    "replay-claimed:quality",
                ),
            )

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_assembly_artifact_unverified",
            ):
                _verify_assembly_artifacts(assembly)

    def test_assembly_verification_recomputes_quality_from_replay(self):
        from radar.replay_campaign import (
            CampaignAssembly,
            _artifact_ref,
            _verify_assembly_artifacts,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            result = self.write_valid_assembly(output_dir=root / "assembly")
            quality_payload = json.loads(
                result.quality_report_path.read_text(encoding="utf-8")
            )
            quality_payload["includedCount"] += 1
            result.quality_report_path.write_text(
                json.dumps(quality_payload),
                encoding="utf-8",
            )
            manifest_path = result.quality_report_path.parent / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["qualityReport"]["sha256"] = hashlib.sha256(
                result.quality_report_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            assembly = CampaignAssembly(
                inputDigest="a" * 64,
                replayRunId=result.replay.replay_run_id,
                qualityStatus=result.report.status,
                replayArtifact=_artifact_ref(
                    result.replay_input_path,
                    result.replay.replay_run_id,
                ),
                qualityArtifact=_artifact_ref(
                    result.quality_report_path,
                    f"{result.replay.replay_run_id}:quality",
                ),
            )

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_assembly_artifact_unverified",
            ):
                _verify_assembly_artifacts(assembly)

    def test_outcome_verification_recomputes_metrics_from_daily_snapshots(self):
        from radar.replay_campaign import (
            CampaignOutcome,
            _artifact_ref,
            _verify_cohort_artifacts,
            create_campaign,
            register_cohort,
        )
        from radar.replay_outcome_daily_provider import (
            build_daily_snapshot_outcome_providers,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="coordinated-outcome-forgery",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            snapshot_paths = [
                self.write_snapshot(
                    root,
                    self.matching_snapshot(
                        _DailyProviderHelper.snapshot(day, index),
                        task,
                        output,
                    ),
                    index,
                )
                for index, day in enumerate(DATES)
            ]
            state = register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=snapshot_paths,
                updated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
            )
            result = self.collect_ready_outcome(
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                output_dir=root / "outcomes",
                evaluated_at=datetime(2026, 9, 8, 8, 1, tzinfo=UTC),
                day_kind_provider=lambda day: (
                    "closed" if day.weekday() >= 5 else "full"
                ),
                daily_snapshot_ids=[
                    item.snapshot_id
                    for item in state.cohorts[0].daily_snapshots
                ],
                providers=build_daily_snapshot_outcome_providers(
                    snapshot_paths
                ),
            )
            outcome_payload = json.loads(
                result.outcome_bundle_path.read_text(encoding="utf-8")
            )
            label_payload = json.loads(
                result.label_bundle_path.read_text(encoding="utf-8")
            )
            observation = outcome_payload["samples"][0]["observations"][0]
            metric_name = next(iter(observation["outcomeMetrics"]))
            forged_value = (
                not observation["outcomeMetrics"][metric_name]
                if isinstance(
                    observation["outcomeMetrics"][metric_name], bool
                )
                else observation["outcomeMetrics"][metric_name] + 0.5
            )
            observation["outcomeMetrics"][metric_name] = forged_value
            matching_label = next(
                item
                for item in label_payload["samples"][0]["labels"]
                if item["domain"] == observation["domain"]
                and item["targetId"] == observation["targetId"]
            )
            matching_label["outcomeMetrics"][metric_name] = forged_value
            result.outcome_bundle_path.write_text(
                json.dumps(outcome_payload),
                encoding="utf-8",
            )
            result.label_bundle_path.write_text(
                json.dumps(label_payload),
                encoding="utf-8",
            )
            manifest_path = result.outcome_bundle_path.parent / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["objectiveOutcomes"]["sha256"] = (
                hashlib.sha256(
                    result.outcome_bundle_path.read_bytes()
                ).hexdigest()
            )
            manifest["files"]["labelBundle"]["sha256"] = hashlib.sha256(
                result.label_bundle_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            outcome_state = CampaignOutcome(
                status=result.bundle.status,
                collectionId=result.bundle.collection_id,
                dailySnapshotIds=[
                    item.snapshot_id
                    for item in state.cohorts[0].daily_snapshots
                ],
                outcomeArtifact=_artifact_ref(
                    result.outcome_bundle_path,
                    result.bundle.collection_id,
                ),
                labelArtifact=_artifact_ref(
                    result.label_bundle_path,
                    result.label_bundle.bundle_id,
                ),
            )
            forged_cohort = state.cohorts[0].model_copy(update={
                "outcome": outcome_state,
            })

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_outcome_artifact_unverified",
            ):
                _verify_cohort_artifacts(forged_cohort)

    def test_mutating_entrypoints_reject_concurrent_campaign_lock(self):
        from radar.replay_campaign import (
            CAMPAIGN_MUTATION_LOCK_NAME,
            create_campaign,
            register_cohort,
            resume_campaign,
        )
        from radar.run_lock import CrossProcessFileLock

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="lock-test",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, _, _ = self.inputs(root)
            lock = CrossProcessFileLock(
                campaign_dir / CAMPAIGN_MUTATION_LOCK_NAME
            )
            self.assertTrue(lock.acquire(blocking=False))
            try:
                with self.assertRaisesRegex(
                    ValueError,
                    "replay_campaign_mutation_locked",
                ):
                    register_cohort(
                        campaign_dir=campaign_dir,
                        task_bundle_path=task_path,
                        output_bundle_path=output_path,
                        updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
                    )
                with self.assertRaisesRegex(
                    ValueError,
                    "replay_campaign_mutation_locked",
                ):
                    resume_campaign(
                        campaign_dir=campaign_dir,
                        now=datetime(2026, 9, 1, 8, 2, tzinfo=UTC),
                    )
            finally:
                lock.release()

    def test_register_rejects_stale_expected_revision_without_mutation(self):
        from radar.replay_campaign import (
            create_campaign,
            load_campaign,
            register_cohort,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="revision-test",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, _, _ = self.inputs(root)
            registered = self.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
            )
            self.assertEqual(registered.revision, 2)

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_revision_conflict",
            ):
                self.trusted_formal_register(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    updated_at=datetime(2026, 9, 1, 8, 2, tzinfo=UTC),
                    expected_revision=1,
                )

            self.assertEqual(load_campaign(campaign_dir).revision, 2)

    def test_campaign_mutations_reject_clock_regression(self):
        from radar.replay_campaign import (
            create_campaign,
            load_campaign,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            created_at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="time-monotonic",
                mode="formal_sequence",
                updated_at=created_at,
            )
            task_path, output_path, _, _ = self.inputs(root)
            regressed_at = datetime(2026, 9, 1, 7, 59, tzinfo=UTC)

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_time_regression",
            ):
                self.trusted_formal_register(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    updated_at=regressed_at,
                )
            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_time_regression",
            ):
                resume_campaign(
                    campaign_dir=campaign_dir,
                    now=regressed_at,
                )

            state = load_campaign(campaign_dir)
            self.assertEqual(state.revision, 1)
            self.assertEqual(state.updated_at, created_at)
            self.assertEqual(state.cohorts, [])

    def test_registration_time_cannot_predate_sample_or_artifacts(self):
        from radar.replay_campaign import create_campaign, load_campaign

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            created_at = datetime(2026, 9, 1, 5, 0, tzinfo=UTC)
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="artifact-time-causal",
                mode="formal_sequence",
                updated_at=created_at,
            )
            task_path, output_path, task, output = self.inputs(root)
            before_artifacts = datetime(2026, 9, 1, 6, 29, tzinfo=UTC)
            self.assertGreater(before_artifacts, created_at)
            self.assertLess(before_artifacts, task.created_at)
            self.assertLess(before_artifacts, output.created_at)

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_registration_time_before_artifact",
            ):
                self.trusted_formal_register(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    updated_at=before_artifacts,
                )

            state = load_campaign(campaign_dir)
            self.assertEqual(state.revision, 1)
            self.assertEqual(state.updated_at, created_at)
            self.assertEqual(state.cohorts, [])

    def test_formal_roles_must_be_registered_in_strict_chronology(self):
        from radar.replay_campaign import create_campaign, register_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="formal-202609",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, _, _ = self.inputs(
                root, role="calibration", day=2
            )

            with self.assertRaisesRegex(
                ValueError, "replay_campaign_role_sequence_invalid"
            ):
                self.trusted_formal_register(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    updated_at=datetime(2026, 9, 2, 8, 1, tzinfo=UTC),
                )

    def test_formal_partitions_cannot_share_the_same_shanghai_trade_date(self):
        from radar.replay_campaign import create_campaign, register_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="formal-same-day",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            development = self.inputs(root, role="development", day=1)
            calibration = self.inputs(root, role="calibration", day=1)
            self.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=development[0],
                output_bundle_path=development[1],
                updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
            )

            with self.assertRaisesRegex(
                ValueError, "replay_campaign_partition_chronology_invalid"
            ):
                self.trusted_formal_register(
                    campaign_dir=campaign_dir,
                    task_bundle_path=calibration[0],
                    output_bundle_path=calibration[1],
                    updated_at=datetime(2026, 9, 1, 8, 2, tzinfo=UTC),
                )

    def test_resume_is_idempotent_when_today_snapshot_already_exists(self):
        from radar.replay_campaign import (
            create_campaign,
            register_cohort,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="diagnostic-idempotent",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            snapshot_path = self.write_snapshot(
                root,
                self.matching_snapshot(
                    _DailyProviderHelper.snapshot(
                        date(2026, 9, 1), 0
                    ),
                    task,
                    output,
                ),
                0,
            )
            self.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=[snapshot_path],
                updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
            )
            capture = Mock(side_effect=AssertionError("不得重复采集"))

            report = resume_campaign(
                campaign_dir=campaign_dir,
                now=datetime(2026, 9, 1, 8, 2, tzinfo=UTC),
                confirm_live_close_capture=True,
                day_kind_provider=lambda day: (
                    "closed" if day.weekday() >= 5 else "full"
                ),
                capture=capture,
            )

            capture.assert_not_called()
            self.assertEqual(report.status, "pending_maturity")
            self.assertIn("daily_snapshot_verified", report.actions)

    def test_resume_does_not_require_future_calendar_dates_to_be_published(self):
        from radar.replay_campaign import (
            create_campaign,
            register_cohort,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="future-calendar-not-required",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            snapshot_path = self.write_snapshot(
                root,
                self.matching_snapshot(
                    _DailyProviderHelper.snapshot(date(2026, 9, 1), 0),
                    task,
                    output,
                ),
                0,
            )
            register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=[snapshot_path],
                updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
            )

            report = resume_campaign(
                campaign_dir=campaign_dir,
                now=datetime(2026, 9, 1, 8, 2, tzinfo=UTC),
                day_kind_provider=lambda day: (
                    "full" if day == date(2026, 9, 1) else "unknown"
                ),
            )

            self.assertEqual(report.status, "pending_maturity")
            self.assertIn("daily_snapshot_verified", report.actions)

    def test_resume_captures_required_close_once(self):
        from radar.replay_campaign import (
            create_campaign,
            register_cohort,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="diagnostic-capture",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 7, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 1, 7, 1, tzinfo=UTC),
            )
            snapshot = self.matching_snapshot(
                _DailyProviderHelper.snapshot(
                    date(2026, 9, 1), 0
                ),
                task,
                output,
            )

            def capture(**kwargs):
                path = self.write_snapshot(root, snapshot, 1)
                return SimpleNamespace(
                    snapshot=snapshot,
                    snapshot_path=path,
                    manifest_path=path.parent / "manifest.json",
                )

            report = resume_campaign(
                campaign_dir=campaign_dir,
                now=datetime(2026, 9, 1, 8, 2, tzinfo=UTC),
                confirm_live_close_capture=True,
                day_kind_provider=lambda day: (
                    "closed" if day.weekday() >= 5 else "full"
                ),
                capture=capture,
            )

            self.assertIn("daily_snapshot_captured", report.actions)
            self.assertEqual(len(report.state.cohorts[0].daily_snapshots), 1)

    def test_resume_revision_time_covers_capture_completion(self):
        from radar.replay_campaign import (
            create_campaign,
            register_cohort,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="diagnostic-capture-completion",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 1, 6, 31, tzinfo=UTC),
            )
            snapshot = self.matching_snapshot(
                _DailyProviderHelper.snapshot(date(2026, 9, 1), 0),
                task,
                output,
            )
            started_at = datetime(2026, 9, 1, 7, 5, tzinfo=UTC)
            self.assertGreater(snapshot.captured_at, started_at)

            def capture(**kwargs):
                path = self.write_model(
                    kwargs["output_dir"],
                    "daily-outcome-snapshot.json",
                    snapshot,
                    "dailyOutcomeSnapshot",
                )
                return SimpleNamespace(snapshot_path=path)

            report = resume_campaign(
                campaign_dir=campaign_dir,
                now=started_at,
                confirm_live_close_capture=True,
                day_kind_provider=lambda day: "full",
                capture=capture,
            )

            self.assertIn("daily_snapshot_captured", report.actions)
            self.assertEqual(report.state.updated_at, snapshot.captured_at)
            self.assertEqual(report.state.revision, 3)

    def test_formal_resume_exposes_irrecoverable_missing_past_close(self):
        from radar.replay_campaign import (
            create_campaign,
            register_cohort,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="formal-missed-baseline",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, _, _ = self.inputs(root)
            self.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 1, 8, 1, tzinfo=UTC),
            )

            report = resume_campaign(
                campaign_dir=campaign_dir,
                now=datetime(2026, 9, 2, 8, 2, tzinfo=UTC),
                day_kind_provider=lambda day: (
                    "closed" if day.weekday() >= 5 else "full"
                ),
            )

        self.assertEqual(report.status, "history_incomplete")
        self.assertIn("historical_daily_snapshot_missing", report.actions)

    def test_mature_six_day_series_collects_labels_once(self):
        from radar.replay_campaign import (
            _verify_outcome_artifacts,
            create_campaign,
            register_cohort,
            resume_campaign,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="diagnostic-mature",
                mode="standalone_diagnostic",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            task_path, output_path, task, output = self.inputs(root)
            helper = _DailyProviderHelper()
            snapshot_paths = [
                self.write_snapshot(
                    root,
                    self.matching_snapshot(
                        helper.snapshot(day, index), task, output
                    ),
                    index,
                )
                for index, day in enumerate(DATES)
            ]
            register_cohort(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=snapshot_paths,
                updated_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
            )
            collect = Mock()

            def collect_side_effect(**kwargs):
                return self.collect_ready_outcome(**kwargs)

            collect.side_effect = collect_side_effect
            with patch(
                "radar.replay_campaign._verify_outcome_artifacts",
                wraps=_verify_outcome_artifacts,
            ) as verify_outcome:
                report = resume_campaign(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 8, 8, 2, tzinfo=UTC),
                    confirm_live_close_capture=True,
                    day_kind_provider=lambda day: (
                        "closed" if day.weekday() >= 5 else "full"
                    ),
                    collect=collect,
                )
                self.assertEqual(verify_outcome.call_count, 1)
                second = resume_campaign(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 8, 8, 3, tzinfo=UTC),
                    confirm_live_close_capture=True,
                    day_kind_provider=lambda day: (
                        "closed" if day.weekday() >= 5 else "full"
                    ),
                    collect=collect,
                )
                self.assertEqual(verify_outcome.call_count, 2)

            self.assertIn("objective_outcomes_collected", report.actions)
            self.assertEqual(report.status, "ready")
            self.assertEqual(second.status, "ready")
            self.assertEqual(collect.call_count, 1)

    def test_three_mature_formal_cohorts_are_assembled_once(self):
        from radar.replay_assembly import assemble_replay_artifacts
        from radar.replay_campaign import (
            CampaignAssembly,
            _artifact_ref,
            _verify_assembly_artifacts,
            create_campaign,
            register_cohort,
            resume_campaign,
        )
        from radar.replay_contracts import RadarReplayInput
        from radar.replay_service import build_replay_quality_report

        schedules = {
            "development": (
                date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
                date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8),
            ),
            "calibration": (
                date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4),
                date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9),
            ),
            "holdout": (
                date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7),
                date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10),
            ),
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = root / "campaign"
            create_campaign(
                campaign_dir=campaign_dir,
                campaign_id="formal-mature",
                mode="formal_sequence",
                updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            )
            for day, role in enumerate(
                ("development", "calibration", "holdout"), start=1
            ):
                task_path, output_path, task, output = self.inputs(
                    root, role=role, day=day
                )
                snapshot_paths = []
                for index, trade_date in enumerate(schedules[role]):
                    snapshot = self.matching_snapshot(
                        _DailyProviderHelper.snapshot(trade_date, index),
                        task,
                        output,
                    )
                    snapshot_paths.append(self.write_model(
                        root / f"daily-{role}-{index}",
                        "daily-outcome-snapshot.json",
                        snapshot,
                        "dailyOutcomeSnapshot",
                    ))
                self.trusted_formal_register(
                    campaign_dir=campaign_dir,
                    task_bundle_path=task_path,
                    output_bundle_path=output_path,
                    daily_snapshot_paths=snapshot_paths,
                    updated_at=snapshot.captured_at + timedelta(minutes=1),
                )

            collect = Mock()

            def collect_side_effect(**kwargs):
                return self.collect_ready_outcome(**kwargs)

            collect.side_effect = collect_side_effect
            assemble = Mock(wraps=assemble_replay_artifacts)
            with patch(
                "radar.replay_campaign._verify_assembly_artifacts",
                wraps=_verify_assembly_artifacts,
            ) as verify_assembly:
                report = resume_campaign(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 10, 8, 2, tzinfo=UTC),
                    confirm_live_close_capture=True,
                    day_kind_provider=lambda day: (
                        "closed" if day.weekday() >= 5 else "full"
                    ),
                    collect=collect,
                    assemble=assemble,
                )
                self.assertEqual(verify_assembly.call_count, 1)
                second = resume_campaign(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 10, 8, 3, tzinfo=UTC),
                    confirm_live_close_capture=True,
                    day_kind_provider=lambda day: (
                        "closed" if day.weekday() >= 5 else "full"
                    ),
                    collect=collect,
                    assemble=assemble,
                )
                self.assertEqual(verify_assembly.call_count, 2)

            self.assertEqual(report.status, "ready")
            self.assertIn("formal_quality_assembled", report.actions)
            self.assertEqual(report.state.assembly.quality_status, "ready")
            self.assertEqual(len(assemble.call_args.kwargs["input_dirs"]), 3)
            self.assertEqual(len(assemble.call_args.kwargs["label_bundle_paths"]), 3)
            self.assertEqual(collect.call_count, 3)
            self.assertEqual(assemble.call_count, 1)
            self.assertEqual(second.status, "ready")
            self.assertIn("formal_quality_verified", second.actions)

            persisted = second.state.assembly
            self.assertIsNotNone(persisted)
            replay_path = Path(persisted.replay_artifact.path)
            quality_path = Path(persisted.quality_artifact.path)
            replay_payload = json.loads(replay_path.read_text(encoding="utf-8"))
            replay_payload["samples"][0]["evidence"][0]["source"] = (
                "coordinated-but-not-from-cohort"
            )
            replay_path.write_text(json.dumps(replay_payload), encoding="utf-8")
            forged_replay = RadarReplayInput.model_validate(replay_payload)
            forged_quality = build_replay_quality_report(forged_replay)
            quality_path.write_text(
                forged_quality.model_dump_json(by_alias=True),
                encoding="utf-8",
            )
            manifest_path = replay_path.parent / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["replayInput"]["sha256"] = hashlib.sha256(
                replay_path.read_bytes()
            ).hexdigest()
            manifest["files"]["qualityReport"]["sha256"] = hashlib.sha256(
                quality_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            forged_assembly = CampaignAssembly(
                inputDigest=persisted.input_digest,
                replayRunId=forged_replay.replay_run_id,
                qualityStatus=forged_quality.status,
                replayArtifact=_artifact_ref(
                    replay_path,
                    forged_replay.replay_run_id,
                ),
                qualityArtifact=_artifact_ref(
                    quality_path,
                    f"{forged_replay.replay_run_id}:quality",
                ),
            )
            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_assembly_artifact_unverified",
            ):
                _verify_assembly_artifacts(
                    forged_assembly,
                    second.state.cohorts,
                )


if __name__ == "__main__":
    unittest.main()
