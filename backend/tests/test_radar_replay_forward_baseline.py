import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import requests

from radar.contracts import (
    EtfAssetClass,
    EtfManagementStyle,
    EtfProductMasterRecord,
    ListedFundProductType,
    RadarBatchMeta,
    SecurityMasterRecord,
    SourceBatch,
    SourceStatus,
)
from radar.replay_contracts import RadarReplayInput
from radar.replay_service import RadarReplayQualityReport


STARTED_AT = datetime(2026, 9, 1, 6, 58, tzinfo=timezone.utc)
FETCHED_AT = datetime(2026, 9, 1, 6, 59, tzinfo=timezone.utc)
SAMPLE_AS_OF = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)
CREATED_AT = datetime(2026, 9, 1, 7, 0, 1, tzinfo=timezone.utc)


class Clock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def meta(source):
    return RadarBatchMeta(
        radarRunId="stage9-forward-test",
        batchId=f"{source}-batch",
        source=source,
        asOf=STARTED_AT,
        sourceTime=FETCHED_AT,
        fetchedAt=FETCHED_AT,
        expectedCount=1,
        returnedCount=1,
        rowCoverage=1.0,
        requiredFieldCoverage={"symbol": 1.0, "name": 1.0},
        issues=[],
    )


def security_batch():
    return SourceBatch[SecurityMasterRecord](
        meta=meta("official-security"),
        items=[SecurityMasterRecord(
            symbol="000001",
            name="平安银行",
            exchange="szse",
            board="main",
            listingDate=date(1991, 4, 3),
            source="深圳证券交易所",
            fetchedAt=FETCHED_AT,
        )],
    )


def etf_batch():
    return SourceBatch[EtfProductMasterRecord](
        meta=meta("official-etf"),
        items=[EtfProductMasterRecord(
            symbol="510300",
            officialName="沪深300ETF",
            exchange="sse",
            productType=ListedFundProductType.ETF,
            managementStyle=EtfManagementStyle.PASSIVE_INDEX,
            assetClass=EtfAssetClass.DOMESTIC_EQUITY,
            classificationMappingVersion="etf-product-classification-v1",
            source="上海证券交易所",
            fetchedAt=FETCHED_AT,
        )],
    )


def industry_snapshot():
    release = SimpleNamespace(
        first_observed_at=FETCHED_AT,
        fetched_at=FETCHED_AT,
        knowledge_effective_from=FETCHED_AT,
        document_sha256="a" * 64,
    )
    return SimpleNamespace(
        meta=meta("capco-official"),
        status=SourceStatus.HEALTHY,
        release=release,
        records=[SimpleNamespace(symbol="000001")],
        current_master_gaps=[],
        completeness=SimpleNamespace(
            mapping_coverage=1.0,
            formal_usable=True,
            reasons=(),
        ),
        issues=[],
    )


def index_snapshot():
    methodology = SimpleNamespace(
        formal_ready=False,
        fetched_at=FETCHED_AT,
        effective_from=None,
        reasons=("index_methodology_effective_date_missing",),
    )
    return SimpleNamespace(
        provider="csindex",
        index_code="000300",
        index_name="沪深300",
        identity_evidence_url="https://www.csindex.com.cn/",
        identity_evidence_sha256="b" * 64,
        methodology=methodology,
        constituent_sets=(),
        fetched_at=FETCHED_AT,
    )


def source_functions():
    from radar.replay_forward_baseline import ForwardBaselineSources

    return ForwardBaselineSources(
        security=lambda **_: security_batch(),
        industry=lambda **_: industry_snapshot(),
        index=lambda **_: index_snapshot(),
        etf=lambda **_: etf_batch(),
    )


def formal_etf_hooks(received):
    from radar.etf_formal_admission import (
        build_etf_formal_admission_bundle,
        provide_etf_formal_admission_evidence,
    )
    from radar.replay_forward_baseline import ForwardEtfFormalAdmissionHooks

    def collect(**kwargs):
        received["collect"] = kwargs
        return {"symbols": tuple(kwargs["symbols"])}

    def finalize(**kwargs):
        received["finalize"] = kwargs
        item = etf_batch().items[0].model_copy(update={
            "fetched_at": FETCHED_AT,
        })
        admission = provide_etf_formal_admission_evidence(
            product=item,
            as_of=kwargs["as_of"],
        )
        return build_etf_formal_admission_bundle(
            sample_id=kwargs["sample_id"],
            radar_run_id=kwargs["radar_run_id"],
            as_of=kwargs["as_of"],
            admissions=(admission,),
        )

    return ForwardEtfFormalAdmissionHooks(
        collect=collect,
        finalize=finalize,
    )


def corporate_action_snapshot():
    from radar.replay_source_adapters import CorporateActionForwardSnapshot

    return CorporateActionForwardSnapshot(
        sourceId="official-corporate-actions-test",
        source="沪深北交易所官方公司行为",
        fetchedAt=FETCHED_AT,
        coveredExchanges=["sse", "szse", "bse"],
        missingExchanges=[],
        expectedCount=0,
        returnedCountByExchange={"sse": 0, "szse": 0, "bse": 0},
        coverageFromByExchange={
            "sse": SAMPLE_AS_OF.date(),
            "szse": SAMPLE_AS_OF.date(),
            "bse": SAMPLE_AS_OF.date(),
        },
        coverageThroughByExchange={
            "sse": SAMPLE_AS_OF.date(),
            "szse": SAMPLE_AS_OF.date(),
            "bse": SAMPLE_AS_OF.date(),
        },
        items=[],
    )


class RadarReplayForwardBaselineTests(unittest.TestCase):
    def test_index_source_retries_one_transient_network_failure(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        calls = []

        def index_source(**_):
            calls.append("index")
            if len(calls) == 1:
                raise requests.ReadTimeout("temporary")
            return index_snapshot()

        sources = source_functions()
        sources = sources.__class__(
            security=sources.security,
            industry=sources.industry,
            index=index_source,
            etf=sources.etf,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path(parent) / "stage9-run",
                sources=sources,
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )

        evidence = next(
            item
            for item in result.replay.samples[0].evidence
            if item.domain == "index"
        )
        self.assertEqual(calls, ["index", "index"])
        self.assertEqual(evidence.status, "unverifiable")
        self.assertNotEqual(evidence.payload.get("errorType"), "ReadTimeout")

    def test_index_source_stops_after_one_transient_network_retry(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        calls = []

        def index_source(**_):
            calls.append("index")
            raise requests.ReadTimeout("stable")

        sources = source_functions()
        sources = sources.__class__(
            security=sources.security,
            industry=sources.industry,
            index=index_source,
            etf=sources.etf,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path(parent) / "stage9-run",
                sources=sources,
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )

        evidence = next(
            item
            for item in result.replay.samples[0].evidence
            if item.domain == "index"
        )
        self.assertEqual(calls, ["index", "index"])
        self.assertEqual(evidence.status, "failed")
        self.assertEqual(evidence.payload["errorType"], "ReadTimeout")

    def test_collects_formal_etf_materials_before_freezing_sample_and_writes_bundle(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        received = {}
        sources = source_functions()
        sources = sources.__class__(
            security=sources.security,
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
            etf_formal_admission=formal_etf_hooks(received),
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path(parent) / "stage9-run",
                radar_run_id="stage9-forward-test",
                formal_etf_symbols=("510300",),
                sources=sources,
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )
            payload = json.loads(
                result.etf_formal_admission_path.read_text(encoding="utf-8")
            )
            manifest = json.loads(
                result.manifest_path.read_text(encoding="utf-8")
            )

        self.assertEqual(received["collect"]["symbols"], ("510300",))
        self.assertEqual(received["collect"]["started_at"], STARTED_AT)
        self.assertEqual(received["finalize"]["as_of"], SAMPLE_AS_OF)
        self.assertEqual(
            received["finalize"]["sample_id"],
            result.replay.samples[0].sample_id,
        )
        self.assertEqual(payload["sampleId"], result.replay.samples[0].sample_id)
        self.assertEqual(payload["radarRunId"], "stage9-forward-test")
        self.assertEqual(payload["asOf"], SAMPLE_AS_OF.isoformat())
        self.assertIn("etfFormalAdmission", manifest["files"])

    def test_formal_etf_symbols_require_explicit_collector_before_source_calls(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        called = []
        sources = source_functions()
        sources = sources.__class__(
            security=lambda **_: called.append("security"),
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            with self.assertRaisesRegex(
                ValueError,
                "formal_etf_collector_unavailable",
            ):
                collect_forward_replay_baseline(
                    confirm_live_baseline=True,
                    output_dir=Path(parent) / "stage9-run",
                    formal_etf_symbols=("510300",),
                    sources=sources,
                )

        self.assertEqual(called, [])

    def test_explicit_radar_run_identity_is_preserved_for_output_bridge(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path(parent) / "stage9-run",
                radar_run_id="stage6-prefreeze-20260902T130000000000",
                sources=source_functions(),
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )

        sample = result.replay.samples[0]
        self.assertEqual(
            sample.radar_run_id,
            "stage6-prefreeze-20260902T130000000000",
        )
        self.assertEqual(
            result.replay.replay_run_id,
            "replay-stage6-prefreeze-20260902T130000000000",
        )

    def test_explicit_cninfo_pdf_cache_is_injected_only_into_corporate_source(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        received = {}
        sources = source_functions()

        def corporate_source(**kwargs):
            received.update(kwargs)
            return corporate_action_snapshot()

        sources = sources.__class__(
            security=sources.security,
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
            corporate_action=corporate_source,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            root = Path(parent)
            collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=root / "stage9-run",
                cninfo_pdf_cache_dir=root / "cninfo-pdf-cache",
                sources=sources,
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )

        self.assertEqual(received["as_of"], STARTED_AT)
        self.assertTrue(callable(received["cninfo_document_loader"]))

    def test_injected_corporate_action_source_is_collected_and_adapted(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        sources = source_functions()
        sources = sources.__class__(
            security=sources.security,
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
            corporate_action=lambda **_: corporate_action_snapshot(),
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path(parent) / "stage9-run",
                sources=sources,
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )

        evidence = next(
            item
            for item in result.replay.samples[0].evidence
            if item.domain == "corporate_action"
        )
        self.assertEqual(evidence.status, "ready")
        self.assertEqual(evidence.payload["recordCount"], 0)
    def test_confirmation_is_required_before_any_source_call(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        called = []
        sources = source_functions()
        sources = sources.__class__(
            security=lambda **_: called.append("security"),
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as output_dir:
            with self.assertRaisesRegex(ValueError, "confirmation_required"):
                collect_forward_replay_baseline(
                    confirm_live_baseline=False,
                    output_dir=Path(output_dir) / "run",
                    sources=sources,
                )

        self.assertEqual(called, [])

    def test_output_must_be_new_directory_under_private_tmp(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        with self.assertRaisesRegex(ValueError, "output_dir_must_be_private_tmp"):
            collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path("/Volumes/HermesSSD/not-allowed"),
                sources=source_functions(),
            )

    def test_collects_one_forward_development_sample_and_atomic_artifacts(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            output_dir = Path(parent) / "stage9-run"
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=output_dir,
                sources=source_functions(),
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )

            replay = RadarReplayInput.model_validate_json(
                result.replay_input_path.read_text(encoding="utf-8")
            )
            report = RadarReplayQualityReport.model_validate_json(
                result.quality_report_path.read_text(encoding="utf-8")
            )
            manifest = json.loads(
                result.manifest_path.read_text(encoding="utf-8")
            )

        self.assertEqual(len(replay.samples), 1)
        self.assertEqual(replay.samples[0].role, "development")
        self.assertEqual(replay.samples[0].as_of, SAMPLE_AS_OF)
        self.assertEqual(
            {item.domain for item in replay.samples[0].evidence},
            {
                "security_universe", "trading_rule", "industry",
                "index", "etf", "corporate_action",
            },
        )
        self.assertEqual(report.status, "not_ready")
        self.assertEqual(report.missing_partitions, ["calibration", "holdout"])
        self.assertEqual(manifest["replayRunId"], replay.replay_run_id)
        self.assertEqual(set(manifest["files"]), {
            "sourceSnapshots", "replayInput", "qualityReport",
        })
        self.assertEqual(list(output_dir.glob("*.tmp")), [])

    def test_sample_partition_is_frozen_before_collection(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            result = collect_forward_replay_baseline(
                confirm_live_baseline=True,
                output_dir=Path(parent) / "stage9-holdout-run",
                sample_role="holdout",
                sources=source_functions(),
                clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(result.replay.samples[0].role, "holdout")
        self.assertEqual(manifest["sampleRole"], "holdout")

    def test_unknown_sample_partition_is_rejected_before_source_calls(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        called = []
        sources = source_functions()
        sources = sources.__class__(
            security=lambda **_: called.append("security"),
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            with self.assertRaisesRegex(ValueError, "sample_role_invalid"):
                collect_forward_replay_baseline(
                    confirm_live_baseline=True,
                    output_dir=Path(parent) / "stage9-invalid-run",
                    sample_role="relabelled",
                    sources=sources,
                )

        self.assertEqual(called, [])

    def test_future_source_time_is_rejected_without_writing_report(self):
        from radar.replay_forward_baseline import collect_forward_replay_baseline

        bad = security_batch()
        bad.meta.fetched_at = SAMPLE_AS_OF + timedelta(seconds=1)
        sources = source_functions()
        sources = sources.__class__(
            security=lambda **_: bad,
            industry=sources.industry,
            index=sources.index,
            etf=sources.etf,
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            output_dir = Path(parent) / "stage9-run"
            with self.assertRaisesRegex(ValueError, "future_fetchedAt"):
                collect_forward_replay_baseline(
                    confirm_live_baseline=True,
                    output_dir=output_dir,
                    sources=sources,
                    clock=Clock([STARTED_AT, SAMPLE_AS_OF, CREATED_AT]),
                )
            self.assertFalse((output_dir / "quality-report.json").exists())


if __name__ == "__main__":
    unittest.main()
