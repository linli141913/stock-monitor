import hashlib
import json
import os
import stat
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.test_radar_formal_shadow_observation_collector import (
    AS_OF as COLLECTOR_AS_OF,
    OBSERVED_AT as COLLECTOR_OBSERVED_AT,
    closed_gate,
    etf_admission_artifact,
    etf_artifact,
    receipt,
    leader_artifact,
    sector_snapshot,
    trend_artifact,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
STARTED_AT = datetime(2026, 9, 7, 10, 0, tzinfo=SHANGHAI)
OBSERVED_AT = datetime(2026, 9, 7, 10, 0, 2, tzinfo=SHANGHAI)


def _json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _policy_bytes():
    return _json_bytes({
        "contractId": "radar-formal-shadow-collection-policy-v1",
        "policyId": "stage10-shadow-collection-policy-v1",
        "policySha256": (
            "d8e02f3ec075ed8c82a195ccd7f93ec80760e0a83cb98a3b709f9eaf182640a4"
        ),
        "receiptContractId": "radar-formal-shadow-run-receipt-v1",
        "maximumSourceAgeSecondsByModule": {
            "trendRotation": 90,
            "leaderObservation": 90,
            "etfObservation": 90,
        },
        "maximumCollectionDelaySecondsByModule": {
            "trendRotation": 300,
            "leaderObservation": 300,
            "etfObservation": 300,
        },
    })


def _calendar_inputs():
    raw = (
        b"<html><strong>2026\xe5\xb9\xb4\xe4\xbc\x91\xe5\xb8\x82\xe5\xae\x89\xe6\x8e\x92</strong>"
        b"<table><tr><td>\xe5\x9b\xbd\xe5\xba\x86\xe8\x8a\x82</td><td>10\xe6\x9c\x881\xe6\x97\xa5\xe8\x87\xb37\xe6\x97\xa5\xe4\xbc\x91\xe5\xb8\x82</td></tr></table></html>"
    )
    return _json_bytes({
        "contractVersion": "radar-formal-shadow-calendar-input-v1",
        "market": "cn",
        "sourceName": "上海证券交易所",
        "sourceUrl": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
        "year": 2026,
        "fetchedAt": "2026-09-04T11:09:04+08:00",
        "observedThrough": "2026-09-04",
        "sourceDocumentSha256": hashlib.sha256(raw).hexdigest(),
    }), raw


def _stage6_artifact_bytes():
    payload = trend_artifact()
    payload.update(leader_artifact())
    _, calendar = _calendar_inputs()
    payload["prepared"].update({
        "calendarDocumentRawContentAvailable": True,
        "calendarDocumentRawContentSha256": (
            "sha256:" + hashlib.sha256(calendar).hexdigest()
        ),
        "preparedAt": "2026-09-04T11:09:04+08:00",
    })
    return _json_bytes(payload)


def _sector_snapshot_bytes():
    return _json_bytes(sector_snapshot())


class Stage6FormalShadowReceiptTests(unittest.TestCase):
    def test_derives_both_receipts_from_current_run_bytes_with_exact_counts(self):
        from radar.formal_shadow_input_bundle import derive_stage6_run_receipts

        artifact = _stage6_artifact_bytes()
        receipts = derive_stage6_run_receipts(
            source_artifact_json_bytes=artifact,
            sector_snapshot_json_bytes=_sector_snapshot_bytes(),
            observed_at=COLLECTOR_OBSERVED_AT,
            duration_ms=123,
            lock_acquired=True,
        )

        trend = json.loads(receipts["trendRotation"])
        leader = json.loads(receipts["leaderObservation"])
        self.assertEqual(trend["expectedCount"], 2)
        self.assertEqual(trend["observedCount"], 2)
        self.assertEqual(leader["expectedCount"], 2)
        self.assertEqual(leader["observedCount"], 2)
        self.assertTrue(trend["sourceReady"])
        self.assertTrue(leader["sourceReady"])
        self.assertEqual(
            trend["sourceArtifactSha256"], hashlib.sha256(artifact).hexdigest()
        )
        self.assertEqual(trend["lockState"], "acquired")
        self.assertEqual(leader["durationMs"], 123)

    def test_empty_or_contended_run_never_returns_ready_receipt(self):
        from radar.formal_shadow_input_bundle import derive_stage6_run_receipts

        empty = json.loads(_stage6_artifact_bytes())
        empty["evidenceCandidateSelection"]["status"] = "empty"
        empty["evidenceCandidateSelection"]["evidencePlan"]["status"] = "empty"
        empty["evidenceCandidateSelection"]["evidencePlan"]["items"] = []
        empty["collection"]["status"] = "empty"
        empty["collection"]["stateDecisionReview"]["parentCandidateCount"] = 0
        receipts = derive_stage6_run_receipts(
            source_artifact_json_bytes=_json_bytes(empty),
            sector_snapshot_json_bytes=_sector_snapshot_bytes(),
            observed_at=COLLECTOR_OBSERVED_AT,
            duration_ms=0,
            lock_acquired=False,
        )

        self.assertIn("trendRotation", receipts)
        self.assertNotIn("leaderObservation", receipts)
        self.assertFalse(json.loads(receipts["trendRotation"])["sourceReady"])

    def test_does_not_emit_ready_receipt_for_contract_gate_or_identity_tampering(self):
        from radar.formal_shadow_input_bundle import derive_stage6_run_receipts

        for field, value in (
            ("contractId", "wrong-collection"),
            ("status", "not_ready"),
            ("gate", {**closed_gate(), "formalUsable": True}),
        ):
            with self.subTest(field=field):
                artifact = json.loads(_stage6_artifact_bytes())
                artifact["collection"][field] = value
                receipts = derive_stage6_run_receipts(
                    source_artifact_json_bytes=_json_bytes(artifact),
                    sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                    observed_at=COLLECTOR_OBSERVED_AT,
                    duration_ms=1,
                    lock_acquired=True,
                )
                self.assertNotIn("leaderObservation", receipts)

    def test_rejects_global_prepared_or_tradability_identity_tampering(self):
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            derive_stage6_run_receipts,
        )

        for parent, field, value in (
            ("prepared", "contractId", "wrong-prepared"),
            ("tradability", "radarRunId", "wrong-run"),
            ("tradability", "status", "source_failed"),
        ):
            with self.subTest(parent=parent, field=field):
                artifact = json.loads(_stage6_artifact_bytes())
                artifact[parent][field] = value
                with self.assertRaisesRegex(
                    Stage6FormalShadowInputBundleError,
                    "formal_shadow_stage6_artifact_unverified",
                ):
                    derive_stage6_run_receipts(
                        source_artifact_json_bytes=_json_bytes(artifact),
                        sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                        observed_at=COLLECTOR_OBSERVED_AT,
                        duration_ms=1,
                        lock_acquired=True,
                    )


class Stage6FormalShadowInputPublicationTests(unittest.TestCase):
    def test_publishes_two_complete_private_bundles_atomically_with_private_modes(self):
        from radar.formal_shadow_input_bundle import (
            CALENDAR_DOCUMENT_FILENAME,
            CALENDAR_INPUT_FILENAME,
            COLLECTION_POLICY_FILENAME,
            RECEIPT_FILENAME,
            SOURCE_ARTIFACT_FILENAME,
            TREND_SUPPORTING_FILENAME,
            publish_stage6_formal_shadow_input_bundles,
        )

        envelope, calendar = _calendar_inputs()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = publish_stage6_formal_shadow_input_bundles(
                output_root=Path(directory),
                source_artifact_json_bytes=_stage6_artifact_bytes(),
                sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                collection_policy_json_bytes=_policy_bytes(),
                calendar_envelope_json_bytes=envelope,
                calendar_document_bytes=calendar,
                observed_at=COLLECTOR_OBSERVED_AT,
                duration_ms=12,
                lock_acquired=True,
            )
            self.assertEqual(set(result.input_dirs), {"trendRotation", "leaderObservation"})
            self.assertFalse(list(Path(directory).glob("*.staging")))
            for module, path in result.input_dirs.items():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
                expected = {
                    RECEIPT_FILENAME, SOURCE_ARTIFACT_FILENAME,
                    COLLECTION_POLICY_FILENAME, CALENDAR_INPUT_FILENAME,
                    CALENDAR_DOCUMENT_FILENAME,
                }
                if module == "trendRotation":
                    expected.add(TREND_SUPPORTING_FILENAME)
                self.assertEqual({child.name for child in path.iterdir()}, expected)
                self.assertTrue(all(
                    stat.S_IMODE(child.stat().st_mode) == 0o600
                    for child in path.iterdir()
                ))

    def test_missing_calendar_bytes_fails_closed_without_publishing_half_bundle(self):
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            publish_stage6_formal_shadow_input_bundles,
        )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                Stage6FormalShadowInputBundleError,
                "formal_shadow_calendar_document_bytes_required",
            ):
                publish_stage6_formal_shadow_input_bundles(
                    output_root=Path(directory),
                    source_artifact_json_bytes=_stage6_artifact_bytes(),
                    sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                    collection_policy_json_bytes=_policy_bytes(),
                    calendar_envelope_json_bytes=b"{}",
                    calendar_document_bytes=None,
                    observed_at=COLLECTOR_OBSERVED_AT,
                    duration_ms=12,
                    lock_acquired=True,
                )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_stage6_oversized_calendar_fails_before_any_publication(self):
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            publish_stage6_formal_shadow_input_bundles,
        )

        envelope, calendar = _calendar_inputs()
        oversized = calendar + b" " * (2 * 1024 * 1024)
        calendar_sha = hashlib.sha256(oversized).hexdigest()
        envelope_payload = json.loads(envelope)
        envelope_payload["sourceDocumentSha256"] = calendar_sha
        artifact = json.loads(_stage6_artifact_bytes())
        artifact["prepared"]["calendarDocumentRawContentSha256"] = (
            "sha256:" + calendar_sha
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                Stage6FormalShadowInputBundleError,
                "formal_shadow_calendar_document_too_large",
            ):
                publish_stage6_formal_shadow_input_bundles(
                    output_root=Path(directory),
                    source_artifact_json_bytes=_json_bytes(artifact),
                    sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                    collection_policy_json_bytes=_policy_bytes(),
                    calendar_envelope_json_bytes=_json_bytes(envelope_payload),
                    calendar_document_bytes=oversized,
                    observed_at=COLLECTOR_OBSERVED_AT,
                    duration_ms=12,
                    lock_acquired=True,
                )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_rejects_output_root_outside_private_tmp(self):
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            publish_stage6_formal_shadow_input_bundles,
        )

        envelope, calendar = _calendar_inputs()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                Stage6FormalShadowInputBundleError,
                "formal_shadow_input_bundle_root_unverified",
            ):
                publish_stage6_formal_shadow_input_bundles(
                    output_root=Path(directory),
                    source_artifact_json_bytes=_stage6_artifact_bytes(),
                    sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                    collection_policy_json_bytes=_policy_bytes(),
                    calendar_envelope_json_bytes=envelope,
                    calendar_document_bytes=calendar,
                    observed_at=COLLECTOR_OBSERVED_AT,
                    duration_ms=12,
                    lock_acquired=True,
                )

    def test_rejects_calendar_not_bound_to_prepared_raw_sha_or_run_day(self):
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            publish_stage6_formal_shadow_input_bundles,
        )

        envelope, calendar = _calendar_inputs()
        cases = []
        wrong_sha = json.loads(_stage6_artifact_bytes())
        wrong_sha["prepared"]["calendarDocumentRawContentSha256"] = (
            "sha256:" + "0" * 64
        )
        cases.append((_json_bytes(wrong_sha), envelope))
        stale = json.loads(envelope)
        stale["fetchedAt"] = "2026-09-03T11:09:04+08:00"
        stale["observedThrough"] = "2026-09-03"
        cases.append((_stage6_artifact_bytes(), _json_bytes(stale)))
        for artifact, calendar_envelope in cases:
            with self.subTest(calendar_envelope=calendar_envelope):
                with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
                    with self.assertRaisesRegex(
                        Stage6FormalShadowInputBundleError,
                        "formal_shadow_calendar_prepared_binding_mismatch",
                    ):
                        publish_stage6_formal_shadow_input_bundles(
                            output_root=Path(directory),
                            source_artifact_json_bytes=artifact,
                            sector_snapshot_json_bytes=_sector_snapshot_bytes(),
                            collection_policy_json_bytes=_policy_bytes(),
                            calendar_envelope_json_bytes=calendar_envelope,
                            calendar_document_bytes=calendar,
                            observed_at=COLLECTOR_OBSERVED_AT,
                            duration_ms=12,
                            lock_acquired=True,
                        )

    def test_secure_lock_rejects_symlink_without_chmodding_target(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_text("untouched", encoding="utf-8")
            outside.chmod(0o640)
            lock_path = root / "runner.lock"
            lock_path.symlink_to(outside)
            with self.assertRaisesRegex(
                ValueError, "formal_shadow_input_bundle_lock_unverified"
            ):
                PrivateTmpNoFollowFileLock(lock_path).acquire(blocking=False)
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o640)

    def test_secure_lock_detects_forced_unlink_recreate_before_side_effect(self):
        from radar.formal_shadow_input_bundle import PrivateTmpNoFollowFileLock

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            lock_root = Path(directory) / "single-entry-lock-root"
            lock_root.mkdir(mode=0o700)
            lock_path = lock_root / "formal.lock"
            first = PrivateTmpNoFollowFileLock(
                lock_path,
                protect_root=True,
            )
            second = None
            try:
                self.assertTrue(first.acquire(blocking=False))
                with self.assertRaises(PermissionError):
                    lock_path.unlink()

                # 模拟同用户进程恶意清除不可变保护并换件：旧 fd
                # 上的 flock 不能阻止新 inode 被第二个实例获取。
                os.chflags(lock_root, 0, follow_symlinks=False)
                lock_root.chmod(0o700)
                os.chflags(lock_path, 0, follow_symlinks=False)
                lock_path.unlink()
                second = PrivateTmpNoFollowFileLock(
                    lock_path,
                    protect_root=True,
                )
                self.assertTrue(second.acquire(blocking=False))
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_shadow_input_bundle_lock_unverified",
                ):
                    first.assert_still_held()
            finally:
                if second is not None:
                    second.release()
                first.release()
                if lock_path.exists():
                    os.chflags(lock_path, 0, follow_symlinks=False)
                os.chflags(lock_root, 0, follow_symlinks=False)
                lock_root.chmod(0o700)


class EtfFormalShadowInputPublicationTests(unittest.TestCase):
    @staticmethod
    def capture():
        from radar.etf_live_shadow_capture import EtfLiveShadowCapture
        from radar.formal_shadow_observation_collector import (
            EtfLiveShadowObservationArtifact,
            FormalShadowRunReceipt,
        )

        source = _json_bytes(etf_artifact())
        receipt_bytes = _json_bytes(receipt("etfObservation", source))
        return EtfLiveShadowCapture(
            artifact=EtfLiveShadowObservationArtifact.model_validate(
                json.loads(source)
            ),
            receipt=FormalShadowRunReceipt.model_validate(
                json.loads(receipt_bytes)
            ),
            source_json_bytes=source,
            receipt_json_bytes=receipt_bytes,
        )

    def test_publishes_single_complete_etf_input_accepted_by_existing_collector(self):
        from radar.formal_shadow_input_bundle import (
            CALENDAR_DOCUMENT_FILENAME,
            CALENDAR_INPUT_FILENAME,
            COLLECTION_POLICY_FILENAME,
            ETF_ADMISSION_FILENAME,
            RECEIPT_FILENAME,
            SOURCE_ARTIFACT_FILENAME,
            publish_etf_formal_shadow_input_bundle,
        )
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        envelope, calendar = _calendar_inputs()
        admission = _json_bytes(etf_admission_artifact())
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = publish_etf_formal_shadow_input_bundle(
                output_root=Path(directory),
                capture=self.capture(),
                formal_admission_json_bytes=admission,
                collection_policy_json_bytes=_policy_bytes(),
                calendar_envelope_json_bytes=envelope,
                calendar_document_bytes=calendar,
                evaluated_at=COLLECTOR_OBSERVED_AT,
            )
            self.assertEqual(result.module, "etfObservation")
            self.assertEqual(
                {child.name for child in result.input_dir.iterdir()},
                {
                    RECEIPT_FILENAME,
                    SOURCE_ARTIFACT_FILENAME,
                    COLLECTION_POLICY_FILENAME,
                    CALENDAR_INPUT_FILENAME,
                    CALENDAR_DOCUMENT_FILENAME,
                    ETF_ADMISSION_FILENAME,
                },
            )
            self.assertEqual(
                stat.S_IMODE(result.input_dir.stat().st_mode),
                0o700,
            )
            self.assertTrue(all(
                stat.S_IMODE(child.stat().st_mode) == 0o600
                for child in result.input_dir.iterdir()
            ))
            observation = adapt_etf_observation(
                (result.input_dir / RECEIPT_FILENAME).read_bytes(),
                (result.input_dir / SOURCE_ARTIFACT_FILENAME).read_bytes(),
                (result.input_dir / ETF_ADMISSION_FILENAME).read_bytes(),
                evaluated_at=COLLECTOR_OBSERVED_AT,
                collection_policy_json_bytes=(
                    result.input_dir / COLLECTION_POLICY_FILENAME
                ).read_bytes(),
            )
        self.assertEqual(observation.observation_status, "ready")

    def test_rejects_capture_tampering_and_cross_day_calendar_without_output(self):
        from radar.etf_live_shadow_capture import EtfLiveShadowCapture
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            publish_etf_formal_shadow_input_bundle,
        )

        envelope, calendar = _calendar_inputs()
        capture = self.capture()
        bad_capture = EtfLiveShadowCapture(
            artifact=capture.artifact,
            receipt=capture.receipt,
            source_json_bytes=capture.source_json_bytes + b" ",
            receipt_json_bytes=capture.receipt_json_bytes,
        )
        stale = json.loads(envelope)
        stale["fetchedAt"] = "2026-09-03T11:09:04+08:00"
        stale["observedThrough"] = "2026-09-03"
        for current, current_envelope, reason in (
            (bad_capture, envelope, "etf_live_shadow_capture_unverified"),
            (capture, _json_bytes(stale), "formal_shadow_calendar_observation_date_mismatch"),
        ):
            with self.subTest(reason=reason):
                with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
                    with self.assertRaisesRegex(
                        Stage6FormalShadowInputBundleError, reason
                    ):
                        publish_etf_formal_shadow_input_bundle(
                            output_root=Path(directory),
                            capture=current,
                            formal_admission_json_bytes=_json_bytes(
                                etf_admission_artifact()
                            ),
                            collection_policy_json_bytes=_policy_bytes(),
                            calendar_envelope_json_bytes=current_envelope,
                            calendar_document_bytes=calendar,
                            evaluated_at=COLLECTOR_OBSERVED_AT,
                        )
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_write_failure_never_exposes_partial_etf_input_directory(self):
        import radar.formal_shadow_input_bundle as bundle_module

        envelope, calendar = _calendar_inputs()
        original_write = bundle_module._write_bytes_at
        call_count = 0

        def fail_second_write(directory_fd, name, payload):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise bundle_module.Stage6FormalShadowInputBundleError(
                    "injected_etf_write_failure"
                )
            return original_write(directory_fd, name, payload)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with patch.object(
                bundle_module,
                "_write_bytes_at",
                side_effect=fail_second_write,
            ):
                with self.assertRaisesRegex(
                    bundle_module.Stage6FormalShadowInputBundleError,
                    "injected_etf_write_failure",
                ):
                    bundle_module.publish_etf_formal_shadow_input_bundle(
                        output_root=Path(directory),
                        capture=self.capture(),
                        formal_admission_json_bytes=_json_bytes(
                            etf_admission_artifact()
                        ),
                        collection_policy_json_bytes=_policy_bytes(),
                        calendar_envelope_json_bytes=envelope,
                        calendar_document_bytes=calendar,
                        evaluated_at=COLLECTOR_OBSERVED_AT,
                    )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_oversized_calendar_is_rejected_before_any_directory_exists(self):
        from radar.formal_shadow_input_bundle import (
            Stage6FormalShadowInputBundleError,
            publish_etf_formal_shadow_input_bundle,
        )

        envelope, calendar = _calendar_inputs()
        oversized = calendar + b" " * (2 * 1024 * 1024)
        payload = json.loads(envelope)
        payload["sourceDocumentSha256"] = hashlib.sha256(oversized).hexdigest()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            with self.assertRaisesRegex(
                Stage6FormalShadowInputBundleError,
                "formal_shadow_calendar_document_too_large",
            ):
                publish_etf_formal_shadow_input_bundle(
                    output_root=Path(directory),
                    capture=self.capture(),
                    formal_admission_json_bytes=_json_bytes(
                        etf_admission_artifact()
                    ),
                    collection_policy_json_bytes=_policy_bytes(),
                    calendar_envelope_json_bytes=_json_bytes(payload),
                    calendar_document_bytes=oversized,
                    evaluated_at=COLLECTOR_OBSERVED_AT,
                )
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
