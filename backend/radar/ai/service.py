from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional, Protocol, Tuple

import requests

from radar.ai.config import RadarAiSettings
from radar.ai.contracts import (
    FrozenRadarEvidencePackage,
    RadarAiStructuredOutput,
)
from radar.ai.prompts import prompt_version_for_scope, system_prompt_for_scope
from radar.ai.validator import (
    RadarAiEvidenceError,
    RadarAiOutputError,
    validate_evidence_package,
    validate_model_output,
)


@dataclass(frozen=True)
class RadarAiUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class RadarAiAnalysisResult:
    radar_run_id: str
    as_of: datetime
    scope_type: str
    scope_id: str
    analysis_status: str
    evidence_fingerprint: Optional[str]
    prompt_version: str
    model: Optional[str]
    analysis_at: datetime
    duration_ms: int
    usage: RadarAiUsage = RadarAiUsage()
    output: Optional[RadarAiStructuredOutput] = None
    error_category: Optional[str] = None
    reused: bool = False


class RadarAiRunStore(Protocol):
    def find_success(self, reuse_key: str) -> Optional[RadarAiAnalysisResult]: ...

    def usage_for_day(self, day: str) -> Tuple[int, int]: ...

    def start_run(self, record: dict) -> None: ...

    def finish_success(
        self,
        reuse_key: str,
        result: RadarAiAnalysisResult,
    ) -> None: ...

    def finish_failure(
        self,
        reuse_key: str,
        result: RadarAiAnalysisResult,
    ) -> None: ...


class RadarAiClient(Protocol):
    def analyze(self, **kwargs: Any): ...


class RadarAiRunConflict(RuntimeError):
    """同一复用键已由另一进程取得。"""


class RadarAiService:
    _active_keys = set()
    _active_lock = threading.Lock()

    def __init__(
        self,
        *,
        settings: RadarAiSettings,
        client: RadarAiClient,
        store: RadarAiRunStore,
        clock,
    ):
        self.settings = settings
        self.client = client
        self.store = store
        self.clock = clock

    def _result(
        self,
        package: FrozenRadarEvidencePackage,
        *,
        status: str,
        prompt_version: str,
        fingerprint: Optional[str] = None,
        error_category: Optional[str] = None,
        duration_ms: int = 0,
        usage: RadarAiUsage = RadarAiUsage(),
        output: Optional[RadarAiStructuredOutput] = None,
    ) -> RadarAiAnalysisResult:
        return RadarAiAnalysisResult(
            radar_run_id=package.radar_run_id,
            as_of=package.as_of,
            scope_type=package.scope_type,
            scope_id=package.scope_id,
            analysis_status=status,
            evidence_fingerprint=fingerprint,
            prompt_version=prompt_version,
            model=self.settings.model,
            analysis_at=self.clock(),
            duration_ms=duration_ms,
            usage=usage,
            output=output,
            error_category=error_category,
        )

    def analyze(
        self,
        package: FrozenRadarEvidencePackage,
        *,
        manual: bool = False,
    ) -> RadarAiAnalysisResult:
        prompt_version = prompt_version_for_scope(package.scope_type)
        if not self.settings.enabled:
            return self._result(
                package,
                status="not_run",
                prompt_version=prompt_version,
                error_category="radar_ai_disabled",
            )
        if manual and not self.settings.manual_enabled:
            return self._result(
                package,
                status="not_run",
                prompt_version=prompt_version,
                error_category="radar_ai_manual_disabled",
            )
        if not self.settings.configured:
            return self._result(
                package,
                status="not_configured",
                prompt_version=prompt_version,
                error_category="radar_ai_not_configured",
            )
        try:
            fingerprint = validate_evidence_package(package)
        except RadarAiEvidenceError as exc:
            return self._result(
                package,
                status="evidence_insufficient",
                prompt_version=prompt_version,
                error_category=str(exc),
            )
        model = str(self.settings.model)
        raw_key = "|".join((
            package.radar_run_id,
            package.scope_type,
            package.scope_id,
            fingerprint,
            prompt_version,
            model,
        ))
        reuse_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        cached = self.store.find_success(reuse_key)
        if cached is not None:
            return replace(cached, reused=True)
        day_key = self.clock().date().isoformat()
        calls, tokens = self.store.usage_for_day(day_key)
        if calls >= self.settings.daily_call_limit:
            return self._result(
                package,
                status="failed",
                prompt_version=prompt_version,
                fingerprint=fingerprint,
                error_category="daily_call_quota_exceeded",
            )
        if tokens >= self.settings.daily_token_limit:
            return self._result(
                package,
                status="failed",
                prompt_version=prompt_version,
                fingerprint=fingerprint,
                error_category="daily_token_quota_exceeded",
            )
        with self._active_lock:
            if reuse_key in self._active_keys:
                return self._result(
                    package,
                    status="not_run",
                    prompt_version=prompt_version,
                    fingerprint=fingerprint,
                    error_category="analysis_already_running",
                )
            self._active_keys.add(reuse_key)
        started = time.monotonic()
        try:
            self.store.start_run({
                "reuseKey": reuse_key,
                "radarRunId": package.radar_run_id,
                "asOf": package.as_of,
                "scopeType": package.scope_type,
                "scopeId": package.scope_id,
                "evidenceFingerprint": fingerprint,
                "promptVersion": prompt_version,
                "model": model,
                "manual": manual,
                "analysisAt": self.clock(),
            })
        except RadarAiRunConflict:
            with self._active_lock:
                self._active_keys.discard(reuse_key)
            cached = self.store.find_success(reuse_key)
            if cached is not None:
                return replace(cached, reused=True)
            return self._result(
                package,
                status="not_run",
                prompt_version=prompt_version,
                fingerprint=fingerprint,
                error_category="analysis_already_running",
            )
        except Exception:
            with self._active_lock:
                self._active_keys.discard(reuse_key)
            return self._result(
                package,
                status="failed",
                prompt_version=prompt_version,
                fingerprint=fingerprint,
                error_category="analysis_storage_failed",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        try:
            try:
                response = self.client.analyze(
                    package=package,
                    system_prompt=system_prompt_for_scope(package.scope_type),
                    prompt_version=prompt_version,
                    model=model,
                )
                output = validate_model_output(response.payload, package)
                usage = RadarAiUsage(
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                )
            except (TimeoutError, requests.Timeout):
                category = "model_timeout"
            except RadarAiOutputError:
                category = "invalid_model_output"
            except Exception:
                category = "model_call_failed"
            else:
                success = self._result(
                    package,
                    status="success",
                    prompt_version=prompt_version,
                    fingerprint=fingerprint,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    usage=usage,
                    output=output,
                )
                try:
                    self.store.finish_success(reuse_key, success)
                except Exception:
                    failure = self._result(
                        package,
                        status="failed",
                        prompt_version=prompt_version,
                        fingerprint=fingerprint,
                        error_category="analysis_storage_failed",
                        duration_ms=success.duration_ms,
                        usage=usage,
                    )
                    try:
                        self.store.finish_failure(reuse_key, failure)
                    except Exception:
                        pass
                    return failure
                return success

            failure = self._result(
                package,
                status="failed",
                prompt_version=prompt_version,
                fingerprint=fingerprint,
                error_category=category,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            try:
                self.store.finish_failure(reuse_key, failure)
            except Exception:
                return replace(
                    failure,
                    error_category="analysis_storage_failed",
                )
            return failure
        finally:
            with self._active_lock:
                self._active_keys.discard(reuse_key)
