import json
import hashlib
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class RadarReplayFormalCohortTests(unittest.TestCase):
    @staticmethod
    def write_etf_bundle(
        path: Path,
        *,
        sample_id: str,
        radar_run_id: str,
        as_of: datetime,
        symbols=("515790", "515050"),
        monitoring_ready=True,
        ranking_ready=False,
    ):
        from radar.etf_formal_admission import (
            ETF_FORMAL_ADMISSION_ITEM_KEYS,
            EtfFormalAdmissionBundle,
            EtfFormalAdmissionEvidence,
            EtfFormalAdmissionItem,
            EtfFormalAdmissionStatus,
            build_etf_formal_admission_bundle,
        )

        admissions = []
        for symbol in symbols:
            source_items = []
            for index, key in enumerate(ETF_FORMAL_ADMISSION_ITEM_KEYS):
                if index < 8:
                    reasons = (
                        ()
                        if monitoring_ready
                        else ("formal_source_inputs_incomplete",)
                    )
                else:
                    reasons = (
                        ()
                        if ranking_ready
                        else (
                            "etf_rule_not_frozen",
                            "ranking_calibration_sample_missing",
                        )
                    )
                source_items.append(EtfFormalAdmissionItem(
                    key=key,
                    status=(
                        EtfFormalAdmissionStatus.MISSING
                        if reasons
                        else EtfFormalAdmissionStatus.READY
                    ),
                    reasons=reasons,
                ))
            source_items = tuple(source_items)
            monitoring_status = (
                EtfFormalAdmissionStatus.READY
                if monitoring_ready
                else EtfFormalAdmissionStatus.MISSING
            )
            reasons = tuple(dict.fromkeys(
                reason
                for item in source_items
                for reason in item.reasons
            ))
            admissions.append(EtfFormalAdmissionEvidence(
                symbol=symbol,
                as_of=as_of,
                rule_version="radar-etf-rule-v1",
                status=(
                    EtfFormalAdmissionStatus.READY
                    if not reasons
                    else EtfFormalAdmissionStatus.MISSING
                ),
                monitoring_status=monitoring_status,
                ranking_status=(
                    EtfFormalAdmissionStatus.READY
                    if ranking_ready
                    else EtfFormalAdmissionStatus.MISSING
                ),
                items=source_items,
                reasons=reasons,
            ))
        bundle: EtfFormalAdmissionBundle = build_etf_formal_admission_bundle(
            sample_id=sample_id,
            radar_run_id=radar_run_id,
            as_of=as_of,
            admissions=admissions,
        )
        path.write_text(
            json.dumps(bundle.to_evidence(), ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def ready_output_bundle(
        *,
        sample_id: str,
        radar_run_id: str,
        as_of: datetime,
        include_leader=True,
        etf_symbols=("515790",),
    ):
        from radar.replay_contracts import (
            RadarReplayEvidence,
            RadarReplayOutputBundle,
            RadarReplaySampleOutputSet,
        )

        domain_states = {
            "market": [{"targetId": "a-share", "state": "retreat"}],
            "sector": [{"targetId": "47", "state": "observe"}],
            "etf": [{
                "targetId": symbol,
                "state": "product_ready_for_index_research",
                "monitoringStatus": "ready",
                "rankingStatus": "missing",
                "monitoringReasons": [],
                "rankingReasons": [
                    "etf_rule_not_frozen",
                    "ranking_calibration_sample_missing",
                ],
            } for symbol in etf_symbols],
            "leader": [],
        }
        evidence = []
        for domain, states in domain_states.items():
            if domain == "leader" and not include_leader:
                continue
            payload = {"states": states}
            source_id = f"radar-{domain}-output-v1:" + domain[0] * 64
            if domain == "etf":
                source_id = "radar-etf-product-research-output-v2:" + "e" * 64
                payload.update({
                    "formalAdmissionCount": len(etf_symbols),
                    "monitoringReadyCount": len(etf_symbols),
                    "monitoringMissingCount": 0,
                    "rankingPolicyReadyCount": 0,
                    "rankingPolicyMissingCount": len(etf_symbols),
                })
            evidence.append(RadarReplayEvidence(
                evidenceId=f"{domain}-output",
                domain=domain,
                sourceId=source_id,
                source=f"{domain}确定性输出",
                sourceTime=as_of,
                fetchedAt=as_of,
                effectiveFrom=None,
                status="ready",
                payload=payload,
            ))
        return RadarReplayOutputBundle(
            bundleId="stage9-output-ready",
            createdAt=as_of,
            samples=[RadarReplaySampleOutputSet(
                sampleId=sample_id,
                radarRunId=radar_run_id,
                asOf=as_of,
                evidence=evidence,
            )],
        )

    @staticmethod
    def write_output_result(output_dir: Path, bundle):
        output_dir.mkdir(parents=True)
        bundle_path = output_dir / "output-bundle.json"
        bundle_path.write_text(
            bundle.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "contractId": "radar-replay-output-bridge-manifest-v1",
            "bundleId": bundle.bundle_id,
            "files": {
                "outputBundle": {
                    "path": bundle_path.name,
                    "sha256": bundle_sha256,
                },
            },
        }), encoding="utf-8")
        return SimpleNamespace(
            output_bundle_path=bundle_path,
            manifest_path=manifest_path,
            bundle=bundle,
        )

    @staticmethod
    def write_baseline_result(
        output_dir: Path,
        *,
        sample_id: str,
        radar_run_id: str,
        role: str,
        as_of: datetime,
        etf_path,
        failed=False,
    ):
        from radar.replay_contracts import (
            RadarReplayEvidence,
            RadarReplayInput,
            RadarReplaySample,
        )
        from radar.replay_service import build_replay_quality_report

        evidence = []
        domains = (
            "security_universe",
            "trading_rule",
            "industry",
            "index",
            "etf",
            "corporate_action",
        )
        for domain in domains:
            status = (
                "failed"
                if failed and domain == "corporate_action"
                else "ready"
            )
            evidence.append(RadarReplayEvidence(
                evidenceId=f"{domain}-evidence",
                domain=domain,
                sourceId=f"{domain}-source",
                source=f"{domain}-official",
                sourceTime=as_of,
                fetchedAt=as_of,
                effectiveFrom=None,
                status=status,
                payload={},
            ))
        replay = RadarReplayInput(
            replayRunId="replay-formal-cohort-test",
            createdAt=as_of,
            samples=[RadarReplaySample(
                sampleId=sample_id,
                role=role,
                asOf=as_of,
                radarRunId=radar_run_id,
                ruleVersion="radar-replay-rule-v1",
                evidence=evidence,
            )],
        )
        report = build_replay_quality_report(replay)
        output_dir.mkdir(parents=True, exist_ok=True)
        source_path = output_dir / "source-snapshots.json"
        replay_path = output_dir / "replay-input.json"
        report_path = output_dir / "quality-report.json"
        manifest_path = output_dir / "manifest.json"
        source_path.write_text(json.dumps({
            "radarRunId": radar_run_id,
            "startedAt": as_of.isoformat(),
            "sampleAsOf": as_of.isoformat(),
            "sampleRole": role,
            "sources": {
                "security_universe": {},
                "industry": {},
                "index": {},
                "etf": {},
                "corporate_action": {},
                "etf_formal_admission_collection": {},
            },
        }), encoding="utf-8")
        replay_path.write_text(
            replay.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        report_path.write_text(
            report.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        files = {
            "sourceSnapshots": {
                "path": source_path.name,
                "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            },
            "replayInput": {
                "path": replay_path.name,
                "sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
            },
            "qualityReport": {
                "path": report_path.name,
                "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            },
        }
        if etf_path is not None:
            files["etfFormalAdmission"] = {
                "path": etf_path.name,
                "sha256": hashlib.sha256(etf_path.read_bytes()).hexdigest(),
            }
        manifest_path.write_text(json.dumps({
            "contractId": "radar-replay-forward-baseline-manifest-v1",
            "replayRunId": replay.replay_run_id,
            "radarRunId": radar_run_id,
            "sampleAsOf": as_of.isoformat(),
            "sampleRole": role,
            "createdAt": as_of.isoformat(),
            "status": report.status,
            "files": files,
        }), encoding="utf-8")
        return SimpleNamespace(
            output_dir=output_dir,
            source_snapshots_path=source_path,
            replay_input_path=replay_path,
            quality_report_path=report_path,
            manifest_path=manifest_path,
            etf_formal_admission_path=etf_path,
            replay=replay,
            report=report,
        )

    def create_campaign(self, root: Path):
        from radar.replay_campaign import create_campaign

        campaign_dir = root / "campaign"
        create_campaign(
            campaign_dir=campaign_dir,
            campaign_id="stage9-formal-test",
            mode="formal_sequence",
            updated_at=datetime(2026, 9, 2, 16, 0, tzinfo=SHANGHAI_TZ),
        )
        return campaign_dir

    def write_stage6_artifact(
        self,
        root: Path,
        *,
        observed_at: datetime,
        radar_run_id: str = "stage6-live-20260903",
    ):
        sector_path = root / "sector-state.json"
        sector_path.write_text("{}", encoding="utf-8")
        artifact_path = root / "stage6.json"
        artifact_path.write_text(json.dumps({
            "marketResearchState": {
                "contractId": "radar-market-research-state-v1",
                "radarRunId": radar_run_id,
                "asOf": observed_at.isoformat(),
                "status": "ready",
                "researchUsable": True,
                "formalUsable": False,
            },
            "sectorStateSnapshotPath": str(sector_path),
        }), encoding="utf-8")
        return artifact_path, sector_path

    def test_plan_returns_development_only_in_official_continuous_session(self):
        from radar.replay_formal_cohort import plan_formal_cohort_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = self.create_campaign(Path(directory))
            plan = plan_formal_cohort_collection(
                campaign_dir=campaign_dir,
                now=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ),
                day_kind_provider=lambda _: "full",
            )

        self.assertEqual(plan.role, "development")
        self.assertEqual(plan.trade_date.isoformat(), "2026-09-03")
        self.assertEqual(plan.campaign_revision, 1)

    def test_plan_rejects_non_trading_window_before_any_collection(self):
        from radar.replay_formal_cohort import plan_formal_cohort_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = self.create_campaign(Path(directory))
            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_continuous_session_required",
            ):
                plan_formal_cohort_collection(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 3, 15, 10, tzinfo=SHANGHAI_TZ),
                    day_kind_provider=lambda _: "full",
                )

    def test_plan_rejects_closing_call_auction_before_any_collection(self):
        from radar.replay_formal_cohort import plan_formal_cohort_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = self.create_campaign(Path(directory))
            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_continuous_session_required",
            ):
                plan_formal_cohort_collection(
                    campaign_dir=campaign_dir,
                    now=datetime(
                        2026, 9, 3, 14, 57, tzinfo=SHANGHAI_TZ
                    ),
                    day_kind_provider=lambda _: "full",
                )

    def test_prepare_rejects_concurrent_formal_collection_before_sources(self):
        from radar.replay_formal_cohort import (
            FORMAL_COLLECTION_LOCK_NAME,
            prepare_formal_cohort,
        )
        from radar.run_lock import CrossProcessFileLock

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            lock = CrossProcessFileLock(
                campaign_dir / FORMAL_COLLECTION_LOCK_NAME
            )
            self.assertTrue(lock.acquire(blocking=False))
            try:
                with self.assertRaisesRegex(
                    ValueError,
                    "replay_formal_cohort_collection_locked",
                ):
                    prepare_formal_cohort(
                        confirm_live_cohort=True,
                        campaign_dir=campaign_dir,
                        output_dir=root / "cohort-run",
                        stage6_artifact_path=root / "not-read.json",
                        formal_etf_symbols=("515790",),
                        collect_baseline=lambda **_: self.fail(
                            "锁竞争时不得请求真实来源"
                        ),
                    )
            finally:
                lock.release()

    def test_plan_allows_last_second_of_afternoon_continuous_session(self):
        from radar.replay_formal_cohort import plan_formal_cohort_collection

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            campaign_dir = self.create_campaign(Path(directory))
            plan = plan_formal_cohort_collection(
                campaign_dir=campaign_dir,
                now=datetime(
                    2026, 9, 3, 14, 56, 59, tzinfo=SHANGHAI_TZ
                ),
                day_kind_provider=lambda _: "full",
            )

        self.assertEqual(plan.role, "development")

    def test_plan_rejects_same_trade_date_as_previous_partition(self):
        from radar.replay_formal_cohort import plan_formal_cohort_collection
        from tests.test_radar_replay_campaign import RadarReplayCampaignTests
        from radar.replay_campaign import register_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            task_path, output_path, _, _ = (
                RadarReplayCampaignTests().inputs(
                    root,
                    role="development",
                    day=3,
                )
            )
            RadarReplayCampaignTests().trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(
                    2026, 9, 3, 14, 45, tzinfo=SHANGHAI_TZ
                ),
            )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_new_trade_date_required",
            ):
                plan_formal_cohort_collection(
                    campaign_dir=campaign_dir,
                    now=datetime(
                        2026, 9, 3, 14, 46, tzinfo=SHANGHAI_TZ
                    ),
                    day_kind_provider=lambda _: "full",
                )

    def test_plan_rejects_next_partition_when_previous_close_baseline_missing(self):
        from radar.replay_campaign import register_cohort
        from radar.replay_formal_cohort import plan_formal_cohort_collection
        from tests.test_radar_replay_campaign import RadarReplayCampaignTests

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            task_path, output_path, _, _ = (
                RadarReplayCampaignTests().inputs(
                    root,
                    role="development",
                    day=3,
                )
            )
            RadarReplayCampaignTests().trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                updated_at=datetime(2026, 9, 3, 16, 0, tzinfo=SHANGHAI_TZ),
            )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_previous_baseline_not_ready",
            ):
                plan_formal_cohort_collection(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 4, 10, 0, tzinfo=SHANGHAI_TZ),
                    day_kind_provider=lambda _: "full",
                )

    def test_plan_allows_next_partition_after_ready_sample_close_baseline(self):
        from radar.replay_campaign import register_cohort
        from radar.replay_formal_cohort import plan_formal_cohort_collection
        from tests.test_radar_replay_campaign import RadarReplayCampaignTests
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            helper = RadarReplayCampaignTests()
            task_path, output_path, task, output = helper.inputs(
                root,
                role="development",
                day=3,
            )
            snapshot = helper.matching_snapshot(
                RadarReplayOutcomeDailyProviderTests.snapshot(
                    date(2026, 9, 3),
                    0,
                ),
                task,
                output,
            )
            snapshot_path = helper.write_snapshot(root, snapshot, 0)
            helper.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=[snapshot_path],
                updated_at=datetime(2026, 9, 3, 16, 0, tzinfo=SHANGHAI_TZ),
            )

            plan = plan_formal_cohort_collection(
                campaign_dir=campaign_dir,
                now=datetime(2026, 9, 4, 10, 0, tzinfo=SHANGHAI_TZ),
                day_kind_provider=lambda _: "full",
            )

        self.assertEqual(plan.role, "calibration")

    def test_plan_rejects_tampered_previous_baseline_before_new_sources(self):
        from radar.replay_campaign import register_cohort
        from radar.replay_formal_cohort import plan_formal_cohort_collection
        from tests.test_radar_replay_campaign import RadarReplayCampaignTests
        from tests.test_radar_replay_outcome_daily_provider import (
            RadarReplayOutcomeDailyProviderTests,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            helper = RadarReplayCampaignTests()
            task_path, output_path, task, output = helper.inputs(
                root,
                role="development",
                day=3,
            )
            snapshot = helper.matching_snapshot(
                RadarReplayOutcomeDailyProviderTests.snapshot(
                    date(2026, 9, 3),
                    0,
                ),
                task,
                output,
            )
            snapshot_path = helper.write_snapshot(root, snapshot, 0)
            helper.trusted_formal_register(
                campaign_dir=campaign_dir,
                task_bundle_path=task_path,
                output_bundle_path=output_path,
                daily_snapshot_paths=[snapshot_path],
                updated_at=datetime(2026, 9, 3, 16, 0, tzinfo=SHANGHAI_TZ),
            )
            snapshot_path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "replay_campaign_artifact_hash_mismatch",
            ):
                plan_formal_cohort_collection(
                    campaign_dir=campaign_dir,
                    now=datetime(2026, 9, 4, 10, 0, tzinfo=SHANGHAI_TZ),
                    day_kind_provider=lambda _: "full",
                )

    def test_prepare_runs_one_ordered_chain_and_registers_last(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, sector_path = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )
            calls = []

            def collect(**kwargs):
                calls.append(("collect", kwargs))
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                replay_path = output_dir / "replay-input.json"
                replay_path.write_text("{}", encoding="utf-8")
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                )
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )

            def export_output(**kwargs):
                calls.append(("output", kwargs))
                return self.write_output_result(
                    kwargs["output_dir"],
                    self.ready_output_bundle(
                        sample_id="forward-development-20260903",
                        radar_run_id="stage6-live-20260903",
                        as_of=now,
                        etf_symbols=("515790", "515050"),
                    ),
                )

            def export_tasks(**kwargs):
                from radar.replay_label_tasks import export_replay_label_tasks

                calls.append(("tasks", kwargs))
                return export_replay_label_tasks(**kwargs)

            def register(**kwargs):
                from radar.replay_campaign import ReplayCampaignState

                manifest_path = root / "cohort-run" / "formal-cohort-manifest.json"
                self.assertTrue(
                    manifest_path.is_file(),
                    "正式活动登记前必须已落盘完整运行清单",
                )
                prepared_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    prepared_manifest["campaign"],
                    {
                        "campaignId": "stage9-formal-test",
                        "revision": 2,
                        "cohortCount": 1,
                    },
                )
                calls.append(("register", kwargs))
                task_path = kwargs["task_bundle_path"].resolve()
                output_path = kwargs["output_bundle_path"].resolve()
                return ReplayCampaignState(
                    campaignId="stage9-formal-test",
                    mode="formal_sequence",
                    revision=2,
                    updatedAt=now,
                    cohorts=[{
                        "cohortId": "stage9-cohort-test",
                        "sample": {
                            "sampleId": "forward-development-20260903",
                            "radarRunId": "stage6-live-20260903",
                            "role": "development",
                            "asOf": now,
                        },
                        "taskBundle": {
                            "path": str(task_path),
                            "sha256": hashlib.sha256(
                                task_path.read_bytes()
                            ).hexdigest(),
                            "semanticId": "stage9-label-tasks-test",
                        },
                        "outputBundle": {
                            "path": str(output_path),
                            "sha256": hashlib.sha256(
                                output_path.read_bytes()
                            ).hexdigest(),
                            "semanticId": "stage9-output-test",
                        },
                    }],
                )

            result = prepare_formal_cohort(
                confirm_live_cohort=True,
                campaign_dir=campaign_dir,
                output_dir=root / "cohort-run",
                stage6_artifact_path=stage6_path,
                formal_etf_symbols=("515790", "515050"),
                clock=lambda: now,
                day_kind_provider=lambda _: "full",
                collect_baseline=collect,
                export_output=export_output,
                export_tasks=export_tasks,
                register=register,
            )

            self.assertEqual(
                [name for name, _ in calls],
                ["collect", "output", "tasks", "register"],
            )
            self.assertEqual(calls[0][1]["sample_role"], "development")
            self.assertEqual(
                calls[0][1]["radar_run_id"],
                "stage6-live-20260903",
            )
            self.assertEqual(calls[1][1]["sector_snapshot_path"], sector_path)
            self.assertEqual(
                calls[3][1]["task_bundle_path"].name,
                "label-tasks.json",
            )
            self.assertEqual(calls[3][1]["expected_revision"], 1)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["role"], "development")
            self.assertEqual(manifest["campaign"]["revision"], 2)
            self.assertEqual(manifest["formalEtfSymbols"], ["515790", "515050"])
            self.assertEqual(set(manifest["files"]), {
                "sourceSnapshots",
                "replayInput",
                "qualityReport",
                "etfFormalAdmission",
                "baselineManifest",
                "outputBundle",
                "outputManifest",
                "labelTasks",
                "labelTaskManifest",
            })
            self.assertTrue(all(
                len(item["sha256"]) == 64
                for item in manifest["files"].values()
            ))

    def test_prepare_rejects_previous_session_stage6_before_live_sources(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 10, 30, tzinfo=SHANGHAI_TZ
                ),
            )
            called = False

            def forbidden_collect(**_):
                nonlocal called
                called = True
                raise AssertionError("不得调用真实来源")

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_stage6_session_mismatch",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    clock=lambda: datetime(
                        2026, 9, 3, 13, 30, tzinfo=SHANGHAI_TZ
                    ),
                    day_kind_provider=lambda _: "full",
                    collect_baseline=forbidden_collect,
                )

            self.assertFalse(called)

    def test_prepare_rejects_empty_formal_etf_before_live_sources(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )
            called = False

            def forbidden_collect(**_):
                nonlocal called
                called = True
                raise AssertionError("不得采集不完整正式样本")

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_formal_etf_required",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=forbidden_collect,
                )

            self.assertFalse(called)

    def test_prepare_rejects_invalid_formal_etfs_before_live_sources(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        invalid_sets = (
            ("515790", "515790"),
            ("51579",),
            tuple(f"51{index:04d}" for index in range(11)),
        )
        for formal_etfs in invalid_sets:
            with self.subTest(formal_etfs=formal_etfs):
                with tempfile.TemporaryDirectory(
                    dir="/private/tmp"
                ) as directory:
                    root = Path(directory)
                    campaign_dir = self.create_campaign(root)
                    now = datetime(
                        2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ
                    )
                    stage6_path, _ = self.write_stage6_artifact(
                        root,
                        observed_at=datetime(
                            2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                        ),
                    )
                    called = False

                    def forbidden_collect(**_):
                        nonlocal called
                        called = True
                        raise AssertionError("不得采集非法ETF正式样本")

                    with self.assertRaisesRegex(
                        ValueError,
                        "replay_formal_cohort_formal_etf_invalid",
                    ):
                        prepare_formal_cohort(
                            confirm_live_cohort=True,
                            campaign_dir=campaign_dir,
                            output_dir=root / "cohort-run",
                            stage6_artifact_path=stage6_path,
                            formal_etf_symbols=formal_etfs,
                            clock=lambda: now,
                            day_kind_provider=lambda _: "full",
                            collect_baseline=forbidden_collect,
                        )

                    self.assertFalse(called)

    def test_prepare_rejects_collector_without_formal_etf_bundle(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def collect_without_etf(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                replay_path = output_dir / "replay-input.json"
                replay_path.write_text("{}", encoding="utf-8")
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=None,
                )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_etf_admission_missing",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=collect_without_etf,
                    export_output=lambda **_: self.fail(
                        "正式ETF包缺失后不得继续导出"
                    ),
                )

    def test_prepare_rejects_source_quality_failure_before_outputs(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def failed_collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                replay_path = output_dir / "replay-input.json"
                replay_path.write_text("{}", encoding="utf-8")
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                )
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                    failed=True,
                )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_source_quality_not_ready",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=failed_collect,
                    export_output=lambda **_: self.fail(
                        "来源失败后不得继续导出"
                    ),
                )

    def test_prepare_rejects_etf_monitoring_gap_before_outputs(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def missing_etf_collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                replay_path = output_dir / "replay-input.json"
                replay_path.write_text("{}", encoding="utf-8")
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                    monitoring_ready=False,
                )
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_etf_monitoring_not_ready",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=missing_etf_collect,
                    export_output=lambda **_: self.fail(
                        "ETF监测缺口后不得继续导出"
                    ),
                )

    def test_prepare_rejects_missing_rule_output_domain_before_tasks(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                replay_path = output_dir / "replay-input.json"
                replay_path.write_text("{}", encoding="utf-8")
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                )
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )

            def output_without_leader(**kwargs):
                return self.write_output_result(
                    kwargs["output_dir"],
                    self.ready_output_bundle(
                        sample_id="forward-development-20260903",
                        radar_run_id="stage6-live-20260903",
                        as_of=now,
                        include_leader=False,
                    ),
                )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_output_domains_not_ready",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=collect,
                    export_output=output_without_leader,
                    export_tasks=lambda **_: self.fail(
                        "四域输出不完整后不得生成标签任务"
                    ),
                )

    def test_prepare_rejects_output_file_mismatch_before_tasks(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                replay_path = output_dir / "replay-input.json"
                replay_path.write_text("{}", encoding="utf-8")
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                )
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )

            def mismatched_output(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                bundle_path = output_dir / "output-bundle.json"
                bundle_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    output_bundle_path=bundle_path,
                    manifest_path=output_dir / "manifest.json",
                    bundle=self.ready_output_bundle(
                        sample_id="forward-development-20260903",
                        radar_run_id="stage6-live-20260903",
                        as_of=now,
                    ),
                )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_output_artifact_unverified",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=collect,
                    export_output=mismatched_output,
                    export_tasks=lambda **_: self.fail(
                        "输出落盘不一致后不得生成标签任务"
                    ),
                )

    def test_prepare_rejects_baseline_file_mismatch_before_outputs(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def mismatched_collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                )
                result = self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )
                result.replay_input_path.write_text("{}", encoding="utf-8")
                return result

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_baseline_artifact_unverified",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=mismatched_collect,
                    export_output=lambda **_: self.fail(
                        "前向基线落盘不一致后不得继续导出"
                    ),
                )

    def test_prepare_rejects_source_snapshot_identity_rehashed_together(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def mismatched_collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                )
                result = self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )
                result.source_snapshots_path.write_text(json.dumps({
                    "radarRunId": "other-run",
                    "startedAt": now.isoformat(),
                    "sampleAsOf": now.isoformat(),
                    "sampleRole": "development",
                    "sources": {},
                }), encoding="utf-8")
                manifest = json.loads(
                    result.manifest_path.read_text(encoding="utf-8")
                )
                manifest["files"]["sourceSnapshots"]["sha256"] = (
                    hashlib.sha256(
                        result.source_snapshots_path.read_bytes()
                    ).hexdigest()
                )
                result.manifest_path.write_text(
                    json.dumps(manifest),
                    encoding="utf-8",
                )
                return result

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_baseline_artifact_unverified",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=mismatched_collect,
                    export_output=lambda **_: self.fail(
                        "来源快照身份错配后不得继续导出"
                    ),
                )

    def test_output_gate_rejects_unrequested_formal_etf_state(self):
        from radar.replay_formal_cohort import _validate_rule_output_bundle

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            bundle = self.ready_output_bundle(
                sample_id="forward-development-20260903",
                radar_run_id="stage6-live-20260903",
                as_of=now,
            )
            etf_evidence = next(
                item
                for item in bundle.samples[0].evidence
                if item.domain == "etf"
            )
            etf_evidence.payload["states"].append({
                "targetId": "510300",
                "state": "product_ready_for_index_research",
                "monitoringStatus": "ready",
                "rankingStatus": "missing",
                "monitoringReasons": [],
                "rankingReasons": [
                    "etf_rule_not_frozen",
                    "ranking_calibration_sample_missing",
                ],
            })
            output = self.write_output_result(Path(directory) / "output", bundle)

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_etf_output_not_ready",
            ):
                _validate_rule_output_bundle(
                    output=output,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    sample_as_of=now,
                    requested_etf_symbols=("515790",),
                )

    def test_output_gate_accepts_non_formal_etf_universe_states(self):
        from radar.replay_formal_cohort import _validate_rule_output_bundle

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            bundle = self.ready_output_bundle(
                sample_id="forward-development-20260903",
                radar_run_id="stage6-live-20260903",
                as_of=now,
            )
            etf_evidence = next(
                item
                for item in bundle.samples[0].evidence
                if item.domain == "etf"
            )
            etf_evidence.payload["states"].extend((
                {
                    "targetId": "159999",
                    "state": "active_product_separate_track",
                },
                {
                    "targetId": "513999",
                    "state": "out_of_scope_asset",
                },
                {
                    "targetId": "510999",
                    "state": "product_evidence_incomplete",
                },
            ))
            output = self.write_output_result(Path(directory) / "output", bundle)

            _validate_rule_output_bundle(
                output=output,
                sample_id="forward-development-20260903",
                radar_run_id="stage6-live-20260903",
                sample_as_of=now,
                requested_etf_symbols=("515790",),
            )

    def test_output_gate_rejects_formal_metadata_on_non_ready_etf_state(self):
        from radar.replay_formal_cohort import _validate_rule_output_bundle

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            bundle = self.ready_output_bundle(
                sample_id="forward-development-20260903",
                radar_run_id="stage6-live-20260903",
                as_of=now,
            )
            etf_evidence = next(
                item
                for item in bundle.samples[0].evidence
                if item.domain == "etf"
            )
            etf_evidence.payload["states"][0]["state"] = (
                "active_product_separate_track"
            )
            output = self.write_output_result(Path(directory) / "output", bundle)

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_etf_output_not_ready",
            ):
                _validate_rule_output_bundle(
                    output=output,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    sample_as_of=now,
                    requested_etf_symbols=("515790",),
                )

    def test_output_gate_rejects_wrong_domain_targets_and_states(self):
        from radar.replay_formal_cohort import _validate_rule_output_bundle

        cases = (
            ("market", "targetId", "other-market"),
            ("market", "state", "unknown-market-state"),
            ("sector", "state", "buy"),
            ("etf", "state", "buy"),
            ("leader", "append", {
                "targetId": "000001",
                "state": "candidate",
            }),
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            for index, (domain, field, value) in enumerate(cases):
                with self.subTest(domain=domain, field=field):
                    bundle = self.ready_output_bundle(
                        sample_id="forward-development-20260903",
                        radar_run_id="stage6-live-20260903",
                        as_of=now,
                    )
                    evidence = next(
                        item for item in bundle.samples[0].evidence
                        if item.domain == domain
                    )
                    if field == "append":
                        evidence.payload["states"].append(value)
                    else:
                        evidence.payload["states"][0][field] = value
                    output = self.write_output_result(
                        root / f"output-{index}",
                        bundle,
                    )

                    with self.assertRaisesRegex(
                        ValueError,
                        "replay_formal_cohort_output_domains_not_ready",
                    ):
                        _validate_rule_output_bundle(
                            output=output,
                            sample_id="forward-development-20260903",
                            radar_run_id="stage6-live-20260903",
                            sample_as_of=now,
                            requested_etf_symbols=("515790",),
                        )

    def test_prepare_rejects_missing_task_manifest_before_register(self):
        from radar.replay_formal_cohort import prepare_formal_cohort

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            campaign_dir = self.create_campaign(root)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            stage6_path, _ = self.write_stage6_artifact(
                root,
                observed_at=datetime(
                    2026, 9, 3, 9, 55, tzinfo=SHANGHAI_TZ
                ),
            )

            def collect(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                etf_path = output_dir / "etf-formal-admission.json"
                self.write_etf_bundle(
                    etf_path,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    as_of=now,
                    symbols=("515790",),
                )
                return self.write_baseline_result(
                    output_dir,
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                    etf_path=etf_path,
                )

            def export_output(**kwargs):
                return self.write_output_result(
                    kwargs["output_dir"],
                    self.ready_output_bundle(
                        sample_id="forward-development-20260903",
                        radar_run_id="stage6-live-20260903",
                        as_of=now,
                    ),
                )

            def export_tasks(**kwargs):
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True)
                task_path = output_dir / "label-tasks.json"
                task_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    task_bundle_path=task_path,
                    manifest_path=output_dir / "missing-manifest.json",
                )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_label_task_artifact_unverified",
            ):
                prepare_formal_cohort(
                    confirm_live_cohort=True,
                    campaign_dir=campaign_dir,
                    output_dir=root / "cohort-run",
                    stage6_artifact_path=stage6_path,
                    formal_etf_symbols=("515790",),
                    clock=lambda: now,
                    day_kind_provider=lambda _: "full",
                    collect_baseline=collect,
                    export_output=export_output,
                    export_tasks=export_tasks,
                    register=lambda **_: self.fail(
                        "运行工件不完整时不得登记正式活动"
                    ),
                )

    def test_label_task_gate_rejects_tampered_manifest(self):
        from radar.replay_formal_cohort import (
            _validate_label_task_artifacts,
        )
        from radar.replay_label_tasks import export_replay_label_tasks

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
            baseline = self.write_baseline_result(
                root / "baseline",
                sample_id="forward-development-20260903",
                radar_run_id="stage6-live-20260903",
                role="development",
                as_of=now,
                etf_path=None,
            )
            tasks = export_replay_label_tasks(
                input_dirs=[baseline.output_dir],
                output_dir=root / "tasks",
                clock=lambda: now,
            )
            manifest = json.loads(
                tasks.manifest_path.read_text(encoding="utf-8")
            )
            manifest["files"]["labelTasks"]["sha256"] = "0" * 64
            tasks.manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_label_task_artifact_unverified",
            ):
                _validate_label_task_artifacts(
                    tasks=tasks,
                    baseline=baseline,
                    sample=baseline.replay.samples[0],
                )

    def test_source_artifact_gate_rejects_content_changed_after_read(self):
        from radar.replay_formal_cohort import (
            _formal_run_artifact_ref,
            _validate_unchanged_formal_run_artifact,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            artifact = Path(directory) / "stage6.json"
            artifact.write_text('{"revision": 1}', encoding="utf-8")
            initial = _formal_run_artifact_ref(artifact)
            artifact.write_text('{"revision": 2}', encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "replay_formal_cohort_source_artifact_changed",
            ):
                _validate_unchanged_formal_run_artifact(
                    path=artifact,
                    expected=initial,
                )

    def test_registration_gate_rejects_state_without_prepared_cohort(self):
        from radar.replay_formal_cohort import (
            _validate_registered_campaign_state,
        )

        now = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI_TZ)
        with self.assertRaisesRegex(
            ValueError,
            "replay_formal_cohort_registration_unverified",
        ):
            _validate_registered_campaign_state(
                state=SimpleNamespace(
                    campaign_id="stage9-formal-test",
                    mode="formal_sequence",
                    revision=1,
                    updated_at=now,
                    cohorts=[],
                ),
                plan=SimpleNamespace(
                    campaign_id="stage9-formal-test",
                    campaign_revision=1,
                    role="development",
                ),
                sample=SimpleNamespace(
                    sample_id="forward-development-20260903",
                    radar_run_id="stage6-live-20260903",
                    role="development",
                    as_of=now,
                ),
                task_ref={
                    "path": "/private/tmp/tasks/label-tasks.json",
                    "sha256": "a" * 64,
                },
                output_ref={
                    "path": "/private/tmp/outputs/output-bundle.json",
                    "sha256": "b" * 64,
                },
                updated_at=now,
            )


if __name__ == "__main__":
    unittest.main()
