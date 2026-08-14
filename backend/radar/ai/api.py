from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Response

import alert_repository
from radar.ai.client import OpenAiCompatibleRadarClient
from radar.ai.config import load_radar_ai_settings
from radar.ai.contracts import (
    FrozenRadarEvidencePackage,
    RadarAiAnalyzeRequest,
    RadarAiHealthResponse,
    RadarAiHistoryResponse,
    RadarAiResponse,
    RadarNotificationPreferences,
    RadarNotificationPreferencesResponse,
)
from radar.ai.prompts import prompt_version_for_scope
from radar.ai.repository import RadarAiRepository
from radar.ai.service import RadarAiAnalysisResult
from radar.ai.service import RadarAiService
from radar.migrations import STAGE8_RADAR_MIGRATIONS, validate_applied_migrations


UTC = timezone.utc
router = APIRouter(prefix="/api/radar", tags=["Mainline Radar AI"])


def _database_path():
    import database

    return database.DB_PATH


def resolve_frozen_evidence(
    scope_type: str,
    scope_id: str,
) -> Optional[FrozenRadarEvidencePackage]:
    """从服务端确定性存储解析正式冻结证据。

    当前市场、行业、ETF和龙头正式状态门均未开放，因此没有可以诚实
    返回的正式证据包。该边界拒绝浏览器上传证据；未来正式状态仓储完成后
    只需在这里接入相应只读适配器，不改变公开POST合同。
    """
    del scope_type, scope_id
    return None


def analyze_frozen_evidence(
    package: FrozenRadarEvidencePackage,
    *,
    manual: bool = False,
) -> RadarAiAnalysisResult:
    settings = load_radar_ai_settings()
    if not settings.configured:
        raise RuntimeError("radar_ai_not_configured")
    resolved = Path(_database_path()).expanduser().resolve(strict=True)
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=rw",
        uri=True,
        timeout=30,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        validate_applied_migrations(
            connection,
            migrations=STAGE8_RADAR_MIGRATIONS,
        )
        repository = RadarAiRepository(connection)
        client = OpenAiCompatibleRadarClient(
            api_key=str(settings.api_key),
            base_url=str(settings.base_url),
            timeout_seconds=settings.request_timeout_seconds,
        )
        return RadarAiService(
            settings=settings,
            client=client,
            store=repository,
            clock=lambda: datetime.now(UTC),
        ).analyze(package, manual=manual)
    finally:
        connection.close()


@contextmanager
def _read_repository() -> Iterator[RadarAiRepository]:
    resolved = Path(_database_path()).expanduser().resolve(strict=True)
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield RadarAiRepository(connection)
    finally:
        connection.close()


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"


def _response_from_result(
    result: RadarAiAnalysisResult,
    *,
    checked_at: datetime,
) -> RadarAiResponse:
    status = result.analysis_status
    error_category = result.error_category
    if status == "running":
        status = "not_run"
        error_category = "analysis_running"
    return RadarAiResponse(
        checkedAt=checked_at,
        radarRunId=result.radar_run_id,
        asOf=result.as_of,
        scopeType=result.scope_type,
        scopeId=result.scope_id,
        analysisStatus=status,
        evidenceFingerprint=result.evidence_fingerprint,
        promptVersion=result.prompt_version,
        model=result.model,
        analysisAt=result.analysis_at,
        durationMs=result.duration_ms,
        usage={
            "promptTokens": result.usage.prompt_tokens,
            "completionTokens": result.usage.completion_tokens,
            "totalTokens": result.usage.total_tokens,
        },
        output=result.output,
        errorCategory=error_category,
        reused=result.reused,
    )


def _empty_response(
    scope_type: str,
    scope_id: str,
    *,
    reason: str = "analysis_not_available",
) -> RadarAiResponse:
    settings = load_radar_ai_settings()
    status = "not_run"
    effective_reason = reason
    if reason == "analysis_not_available" and settings.enabled and not settings.configured:
        status = "not_configured"
        effective_reason = "radar_ai_not_configured"
    return RadarAiResponse(
        checkedAt=datetime.now(UTC),
        scopeType=scope_type,
        scopeId=scope_id,
        analysisStatus=status,
        promptVersion=prompt_version_for_scope(scope_type),
        model=settings.model,
        errorCategory=effective_reason,
    )


def _latest(scope_type: str, scope_id: str, response: Response) -> RadarAiResponse:
    _no_store(response)
    try:
        with _read_repository() as repository:
            rows = repository.list_history(scope_type, scope_id, limit=1)
    except Exception:
        return _empty_response(
            scope_type,
            scope_id,
            reason="stage8_storage_not_ready",
        )
    if not rows:
        return _empty_response(scope_type, scope_id)
    return _response_from_result(rows[0], checked_at=datetime.now(UTC))


@router.get("/ai/health", response_model=RadarAiHealthResponse)
def get_radar_ai_health(response: Response):
    _no_store(response)
    settings = load_radar_ai_settings()
    try:
        with _read_repository():
            storage_ready = True
    except Exception:
        storage_ready = False
    if not settings.enabled:
        status = "disabled"
    elif not settings.configured:
        status = "not_configured"
    elif not storage_ready:
        status = "storage_not_ready"
    else:
        status = "ready"
    return RadarAiHealthResponse(
        checkedAt=datetime.now(UTC),
        status=status,
        enabled=settings.enabled,
        manualEnabled=settings.manual_enabled,
        configured=settings.configured,
        storageReady=storage_ready,
        dailyCallLimit=settings.daily_call_limit,
        dailyTokenLimit=settings.daily_token_limit,
    )


@router.get("/ai/overview", response_model=RadarAiResponse)
def get_radar_ai_overview(response: Response):
    return _latest("market", "overview", response)


@router.get("/ai/sectors/{sector_id}", response_model=RadarAiResponse)
def get_radar_ai_sector(
    response: Response,
    sector_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,32}$"),
):
    return _latest("sector", sector_id, response)


@router.get("/ai/etfs/{etf_code}", response_model=RadarAiResponse)
def get_radar_ai_etf(
    response: Response,
    etf_code: str = ApiPath(pattern=r"^\d{6}$"),
):
    return _latest("etf", etf_code, response)


@router.get("/ai/leaders/{symbol}", response_model=RadarAiResponse)
def get_radar_ai_leader(
    response: Response,
    symbol: str = ApiPath(pattern=r"^\d{6}$"),
):
    return _latest("leader", symbol, response)


@router.get("/ai/history", response_model=RadarAiHistoryResponse)
def get_radar_ai_history(
    response: Response,
    scope_type: str = Query(pattern=r"^(market|sector|etf|leader)$"),
    scope_id: str = Query(min_length=1, max_length=160),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    _no_store(response)
    checked_at = datetime.now(UTC)
    try:
        with _read_repository() as repository:
            rows = repository.list_history(
                scope_type,
                scope_id,
                limit=limit,
                offset=offset,
            )
            total = repository.count_history(scope_type, scope_id)
    except Exception:
        rows = []
        total = 0
    return RadarAiHistoryResponse(
        checkedAt=checked_at,
        scopeType=scope_type,
        scopeId=scope_id,
        total=total,
        limit=limit,
        offset=offset,
        items=[
            _response_from_result(row, checked_at=checked_at)
            for row in rows
        ],
    )


@router.post("/ai/analyze", response_model=RadarAiResponse)
def post_radar_ai_analyze(request: RadarAiAnalyzeRequest):
    settings = load_radar_ai_settings()
    if not settings.manual_enabled:
        raise HTTPException(status_code=403, detail="雷达AI手动核对未启用")
    if not settings.enabled:
        raise HTTPException(status_code=409, detail="雷达AI自动能力未启用")
    if not settings.configured:
        raise HTTPException(status_code=503, detail="雷达AI模型尚未配置")
    # 只允许服务端从确定性雷达存储构造冻结证据，不接受浏览器提交证据包。
    package = resolve_frozen_evidence(request.scope_type, request.scope_id)
    if package is None:
        raise HTTPException(status_code=409, detail="正式冻结证据暂不可用")
    try:
        result = analyze_frozen_evidence(package, manual=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="雷达AI调用暂不可用") from exc
    return _response_from_result(result, checked_at=datetime.now(UTC))


@router.get(
    "/alerts/preferences",
    response_model=RadarNotificationPreferencesResponse,
)
def get_radar_alert_preferences():
    return {"data": alert_repository.get_radar_notification_preferences()}


@router.put(
    "/alerts/preferences",
    response_model=RadarNotificationPreferencesResponse,
)
def put_radar_alert_preferences(request: RadarNotificationPreferences):
    return {
        "data": alert_repository.save_radar_notification_preferences(
            site_enabled=request.site_enabled,
            email_enabled=request.email_enabled,
            p2_email=request.p2_email,
            p3_email=request.p3_email,
        )
    }
