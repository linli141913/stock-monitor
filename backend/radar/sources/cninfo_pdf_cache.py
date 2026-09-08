"""巨潮官方PDF的显式、不可变本地缓存。

缓存只允许位于 ``/private/tmp`` 子目录，并以完整官方URL寻址。公告查询
结果不在这里缓存，避免旧分页结果被误当成新的前向观测。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import urlsplit


CNINFO_PDF_CACHE_CONTRACT_ID = "cninfo-official-pdf-cache-v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _private_tmp_child(value: Path) -> Path:
    resolved = value.expanduser().resolve()
    private_tmp = Path("/private/tmp").resolve()
    try:
        resolved.relative_to(private_tmp)
    except ValueError as exc:
        raise ValueError("cninfo_pdf_cache_dir_must_be_private_tmp") from exc
    if resolved == private_tmp:
        raise ValueError("cninfo_pdf_cache_dir_must_be_private_tmp_child")
    return resolved


def _validated_source_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("cninfo_pdf_cache_source_url_unverified")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "static.cninfo.com.cn"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not parsed.path.startswith("/finalpage/")
        or not parsed.path.lower().endswith(".pdf")
    ):
        raise ValueError("cninfo_pdf_cache_source_url_unverified")
    return value


def _atomic_write(path: Path, content: bytes) -> None:
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class CninfoPdfCache:
    """按官方URL缓存PDF，并在每次命中时重新验证内容哈希。"""

    def __init__(self, cache_dir: Path):
        self.cache_dir = _private_tmp_child(Path(cache_dir))

    def _paths(self, source_url: str) -> Tuple[Path, Path, str]:
        cache_key = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        return (
            self.cache_dir / f"{cache_key}.pdf",
            self.cache_dir / f"{cache_key}.json",
            cache_key,
        )

    @staticmethod
    def _manifest(
        path: Path,
        *,
        source_url: str,
        cache_key: str,
    ) -> Optional[Mapping[str, Any]]:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("cninfo_pdf_cache_manifest_unverified") from exc
        if (
            not isinstance(value, Mapping)
            or value.get("contractId") != CNINFO_PDF_CACHE_CONTRACT_ID
            or value.get("sourceUrl") != source_url
            or value.get("cacheKey") != cache_key
            or not isinstance(value.get("sha256"), str)
            or _SHA256_PATTERN.fullmatch(value["sha256"]) is None
        ):
            raise ValueError("cninfo_pdf_cache_manifest_unverified")
        return value

    def load(self, session: Any, source_url: str) -> Tuple[bytes, str]:
        source_url = _validated_source_url(source_url)
        pdf_path, manifest_path, cache_key = self._paths(source_url)
        manifest = self._manifest(
            manifest_path,
            source_url=source_url,
            cache_key=cache_key,
        )
        expected_digest = manifest.get("sha256") if manifest else None

        if manifest is not None and pdf_path.is_file():
            try:
                cached_content = pdf_path.read_bytes()
            except OSError:
                cached_content = b""
            cached_digest = hashlib.sha256(cached_content).hexdigest()
            if (
                cached_content.startswith(b"%PDF")
                and cached_digest == expected_digest
            ):
                return cached_content, cached_digest

        response = session.get(
            source_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
            },
            timeout=20.0,
        )
        response.raise_for_status()
        content = getattr(response, "content", None)
        if not isinstance(content, bytes) or not content.startswith(b"%PDF"):
            raise ValueError("cninfo_corporate_action_pdf_invalid")
        digest = hashlib.sha256(content).hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise ValueError("cninfo_pdf_cache_source_hash_changed")

        self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_write(pdf_path, content)
        manifest_content = (
            json.dumps(
                {
                    "contractId": CNINFO_PDF_CACHE_CONTRACT_ID,
                    "cacheKey": cache_key,
                    "sourceUrl": source_url,
                    "sha256": digest,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        _atomic_write(manifest_path, manifest_content)
        return content, digest

    def load_text(
        self,
        session: Any,
        source_url: str,
        *,
        extractor: Callable[[bytes], str],
        extractor_id: str,
    ) -> Tuple[str, str]:
        """复用与PDF哈希和提取器版本同时绑定的正文文本。"""

        if not isinstance(extractor_id, str) or not extractor_id.strip():
            raise ValueError("cninfo_pdf_cache_extractor_unverified")
        source_url = _validated_source_url(source_url)
        content, pdf_digest = self.load(session, source_url)
        pdf_path, manifest_path, cache_key = self._paths(source_url)
        text_path = pdf_path.with_suffix(".txt")
        manifest = self._manifest(
            manifest_path,
            source_url=source_url,
            cache_key=cache_key,
        )
        assert manifest is not None
        expected_text_digest = manifest.get("textSha256")
        if (
            manifest.get("extractorId") == extractor_id
            and isinstance(expected_text_digest, str)
            and _SHA256_PATTERN.fullmatch(expected_text_digest) is not None
            and text_path.is_file()
        ):
            try:
                text_content = text_path.read_bytes()
                text = text_content.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                text_content = b""
                text = ""
            if hashlib.sha256(text_content).hexdigest() == expected_text_digest:
                return text, pdf_digest

        text = extractor(content)
        if not isinstance(text, str):
            raise ValueError("cninfo_pdf_cache_extracted_text_unverified")
        text_content = text.encode("utf-8")
        text_digest = hashlib.sha256(text_content).hexdigest()
        _atomic_write(text_path, text_content)
        updated_manifest = dict(manifest)
        updated_manifest.update({
            "extractorId": extractor_id,
            "textSha256": text_digest,
        })
        _atomic_write(
            manifest_path,
            (
                json.dumps(
                    updated_manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
        )
        return text, pdf_digest
