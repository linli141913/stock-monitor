import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfWriter


def _pdf_bytes() -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    return buffer.getvalue()


class _Response:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.content)


class CninfoPdfCacheTests(unittest.TestCase):
    def test_extracted_text_is_reused_with_version_and_hash(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        content = _pdf_bytes()
        url = "https://static.cninfo.com.cn/finalpage/x/1226000000.PDF"
        extractor_calls = []

        def extract(value):
            extractor_calls.append(value)
            return "官方公告正文"

        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            first_session = _Session(content)
            first = cache.load_text(
                first_session,
                url,
                extractor=extract,
                extractor_id="test-extractor-v1",
            )
            second = cache.load_text(
                _Session(b"network must not be used"),
                url,
                extractor=lambda _: (_ for _ in ()).throw(
                    AssertionError("extractor must not be used")
                ),
                extractor_id="test-extractor-v1",
            )
            manifest_path = next(
                (Path(parent) / "pdf-cache").glob("*.json")
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(first, second)
        self.assertEqual(first[0], "官方公告正文")
        self.assertEqual(extractor_calls, [content])
        self.assertEqual(manifest["extractorId"], "test-extractor-v1")
        self.assertEqual(
            manifest["textSha256"],
            hashlib.sha256("官方公告正文".encode("utf-8")).hexdigest(),
        )

    def test_corrupted_extracted_text_is_rebuilt_without_network(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        content = _pdf_bytes()
        url = "https://static.cninfo.com.cn/finalpage/x/1226000006.PDF"
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            cache.load_text(
                _Session(content),
                url,
                extractor=lambda _: "首次文本",
                extractor_id="test-extractor-v1",
            )
            text_path = next((Path(parent) / "pdf-cache").glob("*.txt"))
            text_path.write_text("已损坏", encoding="utf-8")
            no_network = _Session(b"network must not be used")
            rebuilt = cache.load_text(
                no_network,
                url,
                extractor=lambda _: "重新提取文本",
                extractor_id="test-extractor-v1",
            )

        self.assertEqual(rebuilt[0], "重新提取文本")
        self.assertEqual(no_network.calls, [])

    def test_second_load_reuses_verified_pdf_without_network(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        content = _pdf_bytes()
        url = (
            "https://static.cninfo.com.cn/finalpage/2026-09-02/"
            "1226000001.PDF"
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            first_session = _Session(content)
            first = cache.load(first_session, url)
            second_session = _Session(b"network must not be used")
            second = cache.load(second_session, url)

            manifests = list((Path(parent) / "pdf-cache").glob("*.json"))
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))

        expected_digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(first, (content, expected_digest))
        self.assertEqual(second, first)
        self.assertEqual(len(first_session.calls), 1)
        self.assertEqual(second_session.calls, [])
        self.assertEqual(manifest["sourceUrl"], url)
        self.assertEqual(manifest["sha256"], expected_digest)

    def test_corrupted_cached_pdf_is_refetched_and_repaired(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        content = _pdf_bytes()
        url = "https://static.cninfo.com.cn/finalpage/x/1226000002.PDF"
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            cache.load(_Session(content), url)
            pdf_path = next((Path(parent) / "pdf-cache").glob("*.pdf"))
            pdf_path.write_bytes(b"%PDF-corrupted")

            repair_session = _Session(content)
            repaired = cache.load(repair_session, url)

        self.assertEqual(repaired[1], hashlib.sha256(content).hexdigest())
        self.assertEqual(len(repair_session.calls), 1)

    def test_changed_content_for_same_official_url_fails_closed(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        original = _pdf_bytes()
        changed = original + b"changed"
        url = "https://static.cninfo.com.cn/finalpage/x/1226000003.PDF"
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            cache.load(_Session(original), url)
            pdf_path = next((Path(parent) / "pdf-cache").glob("*.pdf"))
            pdf_path.write_bytes(b"%PDF-corrupted")

            with self.assertRaisesRegex(
                ValueError,
                "cninfo_pdf_cache_source_hash_changed",
            ):
                cache.load(_Session(changed), url)

    def test_different_urls_never_share_cache_entry(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        content = _pdf_bytes()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            first_session = _Session(content)
            second_session = _Session(content)
            cache.load(
                first_session,
                "https://static.cninfo.com.cn/finalpage/x/1226000004.PDF",
            )
            cache.load(
                second_session,
                "https://static.cninfo.com.cn/finalpage/x/1226000005.PDF",
            )
            pdf_files = list((Path(parent) / "pdf-cache").glob("*.pdf"))

        self.assertEqual(len(first_session.calls), 1)
        self.assertEqual(len(second_session.calls), 1)
        self.assertEqual(len(pdf_files), 2)

    def test_cache_rejects_non_private_tmp_and_non_cninfo_url(self):
        from radar.sources.cninfo_pdf_cache import CninfoPdfCache

        with self.assertRaisesRegex(
            ValueError,
            "cninfo_pdf_cache_dir_must_be_private_tmp",
        ):
            CninfoPdfCache(Path("/Volumes/HermesSSD/cache"))

        with tempfile.TemporaryDirectory(dir="/private/tmp") as parent:
            cache = CninfoPdfCache(Path(parent) / "pdf-cache")
            with self.assertRaisesRegex(
                ValueError,
                "cninfo_pdf_cache_source_url_unverified",
            ):
                cache.load(_Session(_pdf_bytes()), "https://example.com/a.PDF")


if __name__ == "__main__":
    unittest.main()
