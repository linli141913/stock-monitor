import hashlib
import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SSE_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"


class RadarStage10FrozenScheduleCliTests(unittest.TestCase):
    @staticmethod
    def _calendar_document(year=2026):
        return (
            f"<html><strong>{year}年休市安排</strong><table>"
            "<tr><td>元旦</td><td>1月1日至1月3日休市</td></tr>"
            "<tr><td>国庆</td><td>10月1日至10月7日休市</td></tr>"
            "</table></html>"
        ).encode("utf-8")

    def _make_input(self, root, *, schedule_override=None):
        input_root = root / "input"
        input_root.mkdir(mode=0o700)
        document = self._calendar_document()
        envelope = {
            "contractVersion": "radar-formal-shadow-calendar-input-v1",
            "market": "cn",
            "sourceName": "上海证券交易所",
            "sourceUrl": SSE_URL,
            "year": 2026,
            "fetchedAt": "2026-12-31T16:00:00+08:00",
            "observedThrough": "2026-12-31",
            "sourceDocumentSha256": hashlib.sha256(document).hexdigest(),
        }
        envelope_raw = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        schedule = {
            "contractVersion": "radar-stage10-frozen-schedule-input-v1",
            "scheduleId": "stage10-week-2026-09-08",
            "policyVersion": "radar-stage10-schedule-policy-v1",
            "evidenceScope": "live",
            "officialMarket": "cn",
            "calendarSources": [{
                "year": 2026,
                "envelopeFile": "calendar-2026-envelope.json",
                "envelopeSha256": hashlib.sha256(envelope_raw).hexdigest(),
                "sourceDocumentFile": "calendar-2026-source.html",
                "sourceDocumentSha256": hashlib.sha256(document).hexdigest(),
            }],
            "slots": [{
                "slotId": "cn-20260908-1000",
                "tradeDate": "2026-09-08",
                "scheduleSlot": "2026-09-08T10:00:00+08:00",
                "effectiveFrom": "2026-09-08T09:59:00+08:00",
                "effectiveUntil": "2026-09-08T10:01:00+08:00",
                "moduleScope": [
                    "stage9", "trendRotation",
                    "leaderObservation", "etfObservation",
                ],
            }],
        }
        if schedule_override:
            schedule.update(schedule_override)
        schedule_raw = json.dumps(
            schedule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        for name, raw in (
            ("calendar-2026-envelope.json", envelope_raw),
            ("calendar-2026-source.html", document),
            ("schedule-input.json", schedule_raw),
        ):
            path = input_root / name
            path.write_bytes(raw)
            path.chmod(0o600)
        return input_root, hashlib.sha256(schedule_raw).hexdigest()

    def test_cli_reparses_official_calendar_and_publishes_replayable_manifest(self):
        import run_radar_stage10_frozen_schedule as module
        from radar.stage10_live_collection import load_stage10_frozen_schedule

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            input_root, input_sha = self._make_input(root)
            output_root = root / "schedule-store"
            stdout = StringIO()
            code = module.run_cli([
                "--input-dir", str(input_root),
                "--input-sha256", input_sha,
                "--output-store-root", str(output_root),
            ], stdout=stdout)
            payload = json.loads(stdout.getvalue())
            replayed = load_stage10_frozen_schedule(
                output_root, payload["manifestSha256"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "published")
        self.assertEqual(payload["slotId"], "cn-20260908-1000")
        self.assertEqual(payload["slotIds"], ["cn-20260908-1000"])
        self.assertNotIn("/private/tmp", stdout.getvalue())
        self.assertEqual(replayed.official_trading_dates[0].isoformat(), "2026-09-08")
        self.assertEqual(
            replayed.source_evidence[0].source_document_sha256,
            hashlib.sha256(self._calendar_document()).hexdigest(),
        )

    def test_cli_rejects_non_strict_or_oversized_input_before_output(self):
        import run_radar_stage10_frozen_schedule as module

        bad_inputs = (
            b'{"contractVersion":"radar-stage10-frozen-schedule-input-v1","x":1,"x":2}',
            b'{"contractVersion":"radar-stage10-frozen-schedule-input-v1","x":NaN}',
            b"\xff",
            b" " * (1024 * 1024 + 1),
        )
        for raw in bad_inputs:
            with self.subTest(prefix=raw[:20]), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                input_root = root / "input"
                input_root.mkdir(mode=0o700)
                source = input_root / "schedule-input.json"
                source.write_bytes(raw)
                source.chmod(0o600)
                output_root = root / "output"
                stdout = StringIO()
                code = module.run_cli([
                    "--input-dir", str(input_root),
                    "--input-sha256", hashlib.sha256(raw).hexdigest(),
                    "--output-store-root", str(output_root),
                ], stdout=stdout)

                self.assertNotEqual(code, 0)
                self.assertFalse(output_root.exists())
                self.assertEqual(
                    json.loads(stdout.getvalue())["reason"],
                    "stage10_frozen_schedule_input_unverified",
                )

    def test_cli_rejects_hash_path_symlink_and_calendar_mismatch(self):
        import run_radar_stage10_frozen_schedule as module

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            input_root, input_sha = self._make_input(root)
            cases = (
                (["--input-dir", str(input_root), "--input-sha256", "0" * 64], root / "hash-output"),
                (["--input-dir", str(input_root), "--input-sha256", input_sha,
                  "--output-store-root", str(input_root / "nested")], input_root / "nested"),
            )
            for arguments, output_root in cases:
                with self.subTest(output=str(output_root)):
                    stdout = StringIO()
                    code = module.run_cli([
                        *arguments,
                        *([] if "--output-store-root" in arguments else [
                            "--output-store-root", str(output_root)
                        ]),
                    ], stdout=stdout)
                    self.assertNotEqual(code, 0)
                    self.assertFalse(output_root.exists())

            link = root / "input-link"
            link.symlink_to(input_root, target_is_directory=True)
            output_root = root / "link-output"
            code = module.run_cli([
                "--input-dir", str(link),
                "--input-sha256", input_sha,
                "--output-store-root", str(output_root),
            ], stdout=StringIO())
            self.assertNotEqual(code, 0)
            self.assertFalse(output_root.exists())

            schedule = json.loads((input_root / "schedule-input.json").read_text())
            schedule["slots"][0]["tradeDate"] = "2026-10-01"
            schedule["slots"][0]["scheduleSlot"] = "2026-10-01T10:00:00+08:00"
            schedule["slots"][0]["effectiveFrom"] = "2026-10-01T09:59:00+08:00"
            schedule["slots"][0]["effectiveUntil"] = "2026-10-01T10:01:00+08:00"
            raw = json.dumps(schedule, ensure_ascii=False).encode()
            (input_root / "schedule-input.json").write_bytes(raw)
            (input_root / "schedule-input.json").chmod(0o600)
            holiday_output = root / "holiday-output"
            code = module.run_cli([
                "--input-dir", str(input_root),
                "--input-sha256", hashlib.sha256(raw).hexdigest(),
                "--output-store-root", str(holiday_output),
            ], stdout=StringIO())
            self.assertNotEqual(code, 0)
            self.assertFalse(holiday_output.exists())

    def test_cli_rejects_input_contract_and_calendar_file_hash_mismatch(self):
        import run_radar_stage10_frozen_schedule as module

        for mutation in ("contract", "calendar-hash"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                input_root, _input_sha = self._make_input(root)
                schedule_path = input_root / "schedule-input.json"
                schedule = json.loads(schedule_path.read_text())
                if mutation == "contract":
                    schedule["contractVersion"] = "untrusted-v0"
                else:
                    schedule["calendarSources"][0]["sourceDocumentSha256"] = "0" * 64
                raw = json.dumps(schedule, ensure_ascii=False).encode("utf-8")
                schedule_path.write_bytes(raw)
                schedule_path.chmod(0o600)
                output_root = root / "output"
                code = module.run_cli([
                    "--input-dir", str(input_root),
                    "--input-sha256", hashlib.sha256(raw).hexdigest(),
                    "--output-store-root", str(output_root),
                ], stdout=StringIO())

                self.assertNotEqual(code, 0)
                self.assertFalse(output_root.exists())

    def test_cli_detects_input_root_replacement_before_publish(self):
        import run_radar_stage10_frozen_schedule as module

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            input_root, input_sha = self._make_input(root)
            moved = root / "moved-input"
            output_root = root / "output"
            original_read = module._read_regular_at
            replaced = []

            def replace_after_schedule(parent_fd, name, **kwargs):
                raw = original_read(parent_fd, name, **kwargs)
                if name == "schedule-input.json" and not replaced:
                    input_root.rename(moved)
                    input_root.mkdir(mode=0o700)
                    replaced.append(True)
                return raw

            with patch.object(
                module, "_read_regular_at", side_effect=replace_after_schedule
            ):
                code = module.run_cli([
                    "--input-dir", str(input_root),
                    "--input-sha256", input_sha,
                    "--output-store-root", str(output_root),
                ], stdout=StringIO())

            self.assertNotEqual(code, 0)
            self.assertFalse(output_root.exists())

    def test_publish_directory_replacement_never_changes_or_creates_latest(self):
        import radar.stage10_live_collection as stage10
        import run_radar_stage10_frozen_schedule as module

        for has_existing in (True, False):
            with self.subTest(has_existing=has_existing), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                input_root, input_sha = self._make_input(root)
                output_root = root / "output"
                old_latest = None
                if has_existing:
                    self.assertEqual(module.run_cli([
                        "--input-dir", str(input_root),
                        "--input-sha256", input_sha,
                        "--output-store-root", str(output_root),
                    ], stdout=StringIO()), 0)
                    old_latest = (output_root / "latest.json").read_bytes()
                    schedule_path = input_root / "schedule-input.json"
                    schedule = json.loads(schedule_path.read_text())
                    schedule["scheduleId"] = "stage10-week-2026-09-08-revision-2"
                    raw = json.dumps(schedule, ensure_ascii=False).encode("utf-8")
                    schedule_path.write_bytes(raw)
                    schedule_path.chmod(0o600)
                    input_sha = hashlib.sha256(raw).hexdigest()

                original_write = stage10._write_new_at
                replaced = []

                def replace_manifests_after_write(parent_fd, name, payload):
                    original_write(parent_fd, name, payload)
                    if (
                        not replaced
                        and name.endswith(".json")
                        and len(name) == 69
                    ):
                        (output_root / "manifests").rename(
                            output_root / "moved-manifests"
                        )
                        (output_root / "manifests").mkdir(mode=0o700)
                        replaced.append(True)

                with patch.object(
                    stage10,
                    "_write_new_at",
                    side_effect=replace_manifests_after_write,
                ):
                    code = module.run_cli([
                        "--input-dir", str(input_root),
                        "--input-sha256", input_sha,
                        "--output-store-root", str(output_root),
                    ], stdout=StringIO())

                self.assertNotEqual(code, 0)
                if old_latest is None:
                    self.assertFalse((output_root / "latest.json").exists())
                else:
                    self.assertEqual(
                        (output_root / "latest.json").read_bytes(), old_latest
                    )

    def test_loader_rejects_manifest_directory_or_latest_inode_replacement(self):
        import radar.stage10_live_collection as stage10
        import run_radar_stage10_frozen_schedule as module

        for replacement in (
            "manifests",
            "manifests-after-reread",
            "manifests-after-latest-reread",
            "latest",
        ):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                input_root, input_sha = self._make_input(root)
                output_root = root / "output"
                stdout = StringIO()
                self.assertEqual(module.run_cli([
                    "--input-dir", str(input_root),
                    "--input-sha256", input_sha,
                    "--output-store-root", str(output_root),
                ], stdout=stdout), 0)
                manifest_sha = json.loads(stdout.getvalue())["manifestSha256"]
                original_read = stage10._read_regular_at
                replaced = []
                manifest_reads = []
                latest_reads = []

                def replace_after_manifest_read(parent_fd, name, **kwargs):
                    raw = original_read(parent_fd, name, **kwargs)
                    if name == f"{manifest_sha}.json":
                        manifest_reads.append(True)
                    if name == "latest.json":
                        latest_reads.append(True)
                    should_replace = (
                        not replaced
                        and (
                            name == f"{manifest_sha}.json"
                            and replacement in {"manifests", "latest"}
                            and len(manifest_reads) == 1
                            or name == f"{manifest_sha}.json"
                            and replacement == "manifests-after-reread"
                            and len(manifest_reads) == 2
                            or name == "latest.json"
                            and replacement == "manifests-after-latest-reread"
                            and len(latest_reads) == 2
                        )
                    )
                    if should_replace:
                        if replacement.startswith("manifests"):
                            manifests = output_root / "manifests"
                            manifests.rename(output_root / "moved-manifests")
                            manifests.mkdir(mode=0o700)
                            copied = manifests / f"{manifest_sha}.json"
                            copied.write_bytes(raw)
                            copied.chmod(0o600)
                        else:
                            latest = output_root / "latest.json"
                            latest.rename(output_root / "old-latest.json")
                            latest.write_bytes(
                                (output_root / "old-latest.json").read_bytes()
                            )
                            latest.chmod(0o600)
                        replaced.append(True)
                    return raw

                with patch.object(
                    stage10,
                    "_read_regular_at",
                    side_effect=replace_after_manifest_read,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "stage10_frozen_schedule_unverified",
                    ):
                        stage10.load_stage10_frozen_schedule(
                            output_root, manifest_sha
                        )

    def test_publish_rejects_manifest_directory_replacement_after_each_reread(self):
        import radar.stage10_live_collection as stage10
        import run_radar_stage10_frozen_schedule as module

        for replace_after_read in (2, 3):
            with self.subTest(read=replace_after_read), tempfile.TemporaryDirectory(
                dir="/private/tmp"
            ) as directory:
                root = Path(directory)
                input_root, input_sha = self._make_input(root)
                output_root = root / "output"
                self.assertEqual(module.run_cli([
                    "--input-dir", str(input_root),
                    "--input-sha256", input_sha,
                    "--output-store-root", str(output_root),
                ], stdout=StringIO()), 0)
                old_latest = (output_root / "latest.json").read_bytes()
                schedule_path = input_root / "schedule-input.json"
                schedule = json.loads(schedule_path.read_text())
                schedule["scheduleId"] = f"stage10-reread-{replace_after_read}"
                raw = json.dumps(schedule, ensure_ascii=False).encode("utf-8")
                schedule_path.write_bytes(raw)
                schedule_path.chmod(0o600)
                input_sha = hashlib.sha256(raw).hexdigest()
                original_read = stage10._read_regular_at
                manifest_reads = []

                def replace_after_nth_read(parent_fd, name, **kwargs):
                    payload = original_read(parent_fd, name, **kwargs)
                    if name.endswith(".json") and len(name) == 69:
                        manifest_reads.append(True)
                        if len(manifest_reads) == replace_after_read:
                            manifests = output_root / "manifests"
                            manifests.rename(output_root / "moved-manifests")
                            manifests.mkdir(mode=0o700)
                    return payload

                with patch.object(
                    stage10,
                    "_read_regular_at",
                    side_effect=replace_after_nth_read,
                ):
                    code = module.run_cli([
                        "--input-dir", str(input_root),
                        "--input-sha256", input_sha,
                        "--output-store-root", str(output_root),
                    ], stdout=StringIO())

                self.assertNotEqual(code, 0)
                self.assertEqual(
                    (output_root / "latest.json").read_bytes(), old_latest
                )


if __name__ == "__main__":
    unittest.main()
