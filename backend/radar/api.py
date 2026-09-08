import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Union

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Request, Response

import market_calendar
from radar.api_contracts import (
    RadarEtfsResponse,
    RadarFormalReadiness,
    RadarFormalShadowProgress,
    RadarLeadersResponse,
    RadarLeaderReviewQueueResponse,
    RadarLeaderReviewDocumentResponse,
    RadarLeaderReviewFormResponse,
    RadarLeaderReviewPage,
    RadarLeaderReviewCandidate,
    RadarLeaderReviewVersionRequest,
    RadarLeaderReviewVersionPreflightResponse,
    RadarLeaderReviewVersionResponse,
    RadarOverviewResponse,
    RadarReplayEtfResearchResponse,
    RadarReplayQualityResponse,
    RadarSectorsResponse,
    RadarSectorHistoryResponse,
    RadarStockResponse,
)
from radar.config import load_radar_settings
from radar.etf_repository import EtfRepository
from radar.formal_readiness_contracts import (
    FORMAL_MODULE_REQUIRED_TRADING_DAYS,
    FROZEN_REQUIRED_FORMAL_GATES,
    FormalGateState,
    FormalModuleReadiness,
    FormalShadowProgressModule,
    RadarFormalFreshnessPolicy,
    formal_readiness_freshness_reason,
)
from radar.formal_readiness_store import load_latest_formal_readiness
from radar.formal_shadow_ledger_store import load_latest_formal_shadow_ledger
from radar.leader_repository import LeaderRepository
from radar.leader_observation_store import load_latest_leader_observation
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_risk_review_service import (
    build_manual_review_version,
    build_review_form_data,
    preflight_manual_review_version,
)
from radar.migrations import validate_applied_migrations
from radar.read_service import RadarReadService
from radar.repository import RadarRepository
from radar.replay_store import (
    DEFAULT_RADAR_REPLAY_STORE_DIR,
    load_latest_replay_report,
)
from radar.replay_etf_research_store import (
    load_latest_replay_etf_research,
)
from radar.sector_history_store import (
    DEFAULT_SECTOR_HISTORY_STORE_DIR,
    load_latest_sector_history_evidence,
)
from radar.sector_threshold_review import (
    build_sector_threshold_review_draft,
    load_sector_threshold_approval,
)


router = APIRouter(prefix="/api/radar", tags=["Mainline Radar"])
DEFAULT_RADAR_FORMAL_READINESS_STORE_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "radar-formal-readiness"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _database_path() -> Union[str, Path]:
    import database

    return database.DB_PATH


def _sector_history_store_path() -> Path:
    return DEFAULT_SECTOR_HISTORY_STORE_DIR


def _replay_store_path() -> Path:
    return DEFAULT_RADAR_REPLAY_STORE_DIR


def _formal_readiness_store_path() -> Path:
    return DEFAULT_RADAR_FORMAL_READINESS_STORE_DIR


def _formal_shadow_ledger_store_path() -> Optional[Path]:
    """读取显式临时台账路径；未配置不回退到任何生产目录。"""

    raw = os.environ.get("RADAR_FORMAL_SHADOW_LEDGER_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("formal_shadow_progress_store_unverified")
    resolved = path.resolve(strict=False)
    private_tmp = Path("/private/tmp").resolve(strict=True)
    if resolved != private_tmp and private_tmp not in resolved.parents:
        raise ValueError("formal_shadow_progress_store_unverified")
    return resolved


@contextmanager
def open_radar_read_connection(
    database_path: Union[str, Path],
) -> Iterator[sqlite3.Connection]:
    resolved = Path(database_path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError("雷达数据库路径不是现有文件")
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
    finally:
        connection.close()


def _service(connection: sqlite3.Connection) -> RadarReadService:
    settings = load_radar_settings()
    validate_applied_migrations(connection)
    etf_repository = None
    if settings.etf_stage5_enabled:
        try:
            etf_repository = EtfRepository(connection)
        except Exception:
            # 阶段5存储尚未迁移时，API返回not_ready而不是把整个总览打成503。
            etf_repository = None
    leader_repository = None
    risk_review_repository = None
    if settings.leader_stage6_enabled:
        try:
            leader_repository = LeaderRepository(connection)
        except Exception:
            # 阶段6存储未就绪时保持只读not_ready，不影响其他雷达模块。
            leader_repository = None
        try:
            risk_review_repository = LeaderRiskReviewRepository(connection)
        except Exception:
            # 真实D2审核存储未迁移时仅隐藏审核队列，不影响既有龙头读取。
            risk_review_repository = None
    return RadarReadService(
        RadarRepository(connection),
        settings=settings,
        clock=lambda: datetime.now(timezone.utc),
        market_status_provider=market_calendar.get_market_status,
        etf_repository=etf_repository,
        leader_repository=leader_repository,
        risk_review_repository=risk_review_repository,
        leader_observation_loader=load_latest_leader_observation,
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"


def _unavailable_formal_readiness(
    *,
    checked_at: datetime,
    state: str,
    reason_codes: tuple[str, ...],
    freshness_policy: Optional[RadarFormalFreshnessPolicy] = None,
) -> RadarFormalReadiness:
    gate_state = "failed" if state == "failed" else "not_ready"
    modules = tuple(
        FormalModuleReadiness(
            module=module,
            state=state,
            requested=False,
            configuredEnabled=False,
            formalEnabled=False,
            observedTradingDays=0,
            requiredTradingDays=FORMAL_MODULE_REQUIRED_TRADING_DAYS[module],
            gates=tuple(
                FormalGateState(
                    gate=gate,
                    state=gate_state,
                    reasonCodes=reason_codes,
                )
                for gate in FROZEN_REQUIRED_FORMAL_GATES
            ),
            reasonCodes=reason_codes,
        )
        for module in FORMAL_MODULE_REQUIRED_TRADING_DAYS
    )
    return RadarFormalReadiness(
        checkedAt=checked_at,
        freshnessPolicy=freshness_policy,
        state=state,
        anyFormalEnabled=False,
        allModulesFormalEnabled=False,
        stage9QualityState=gate_state,
        modules=modules,
        reasonCodes=reason_codes,
    )


def _unavailable_formal_shadow_progress(
    *,
    checked_at: datetime,
    state: str,
    reason_codes: tuple[str, ...],
) -> RadarFormalShadowProgress:
    return RadarFormalShadowProgress(
        checkedAt=checked_at,
        state=state,
        modules=(),
        reasonCodes=reason_codes,
    )


@router.get(
    "/formal-shadow-progress",
    response_model=RadarFormalShadowProgress,
)
def get_radar_formal_shadow_progress(response: Response):
    """只读投影已验证的 v2 影子台账，不表达正式启用结论。"""

    _no_store(response)
    checked_at = _utc_now()
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        return _unavailable_formal_shadow_progress(
            checked_at=datetime.fromtimestamp(0, timezone.utc),
            state="failed",
            reason_codes=("formal_clock_unverified",),
        )
    try:
        store_path = _formal_shadow_ledger_store_path()
    except Exception:
        return _unavailable_formal_shadow_progress(
            checked_at=checked_at,
            state="failed",
            reason_codes=("formal_shadow_progress_store_unverified",),
        )
    if store_path is None:
        return _unavailable_formal_shadow_progress(
            checked_at=checked_at,
            state="missing",
            reason_codes=("formal_shadow_progress_store_unconfigured",),
        )
    try:
        stored = load_latest_formal_shadow_ledger(store_path, now=checked_at)
    except Exception:
        return _unavailable_formal_shadow_progress(
            checked_at=checked_at,
            state="failed",
            reason_codes=("formal_shadow_progress_store_unverified",),
        )
    if stored.status == "missing":
        return _unavailable_formal_shadow_progress(
            checked_at=checked_at,
            state="missing",
            reason_codes=stored.reason_codes or ("formal_shadow_ledger_missing",),
        )
    if (
        stored.status != "available"
        or stored.ledger is None
        or stored.stored_ref is None
        or not stored.stored_ref.content_sha256
    ):
        return _unavailable_formal_shadow_progress(
            checked_at=checked_at,
            state="failed",
            reason_codes=(
                stored.reason_codes
                or ("formal_shadow_ledger_report_unverified",)
            ),
        )
    ledger = stored.ledger
    return RadarFormalShadowProgress(
        checkedAt=checked_at,
        state="available",
        ledgerSha256=stored.stored_ref.content_sha256,
        modules=tuple(
            FormalShadowProgressModule(
                module=module,
                observedTradingDays=ledger.ready_trading_days_by_module[module],
                requiredTradingDays=ledger.required_trading_days_by_module[module],
                latestReadyStreak=ledger.latest_ready_streak_by_module[module],
                latestReadyTradingDate=(
                    ledger.latest_ready_trading_date_by_module[module]
                ),
            )
            for module in FORMAL_MODULE_REQUIRED_TRADING_DAYS
        ),
    )


@router.get(
    "/formal-readiness",
    response_model=RadarFormalReadiness,
)
def get_radar_formal_readiness(response: Response):
    """只读取内容寻址正式就绪仓，不访问SQLite或外部来源。"""

    _no_store(response)
    checked_at = _utc_now()
    clock_verified = (
        checked_at.tzinfo is not None
        and checked_at.utcoffset() is not None
    )
    if not clock_verified:
        return _unavailable_formal_readiness(
            checked_at=datetime.fromtimestamp(0, timezone.utc),
            state="failed",
            reason_codes=("formal_clock_unverified",),
        )
    try:
        stored = load_latest_formal_readiness(
            _formal_readiness_store_path(),
        )
    except Exception:
        return _unavailable_formal_readiness(
            checked_at=checked_at,
            state="failed",
            reason_codes=("formal_readiness_store_unverified",),
        )
    if stored.status == "missing":
        return _unavailable_formal_readiness(
            checked_at=checked_at,
            state="not_ready",
            reason_codes=(
                stored.reason_codes
                or ("formal_readiness_report_missing",)
            ),
        )
    if stored.status != "available" or stored.report is None:
        return _unavailable_formal_readiness(
            checked_at=checked_at,
            state="failed",
            reason_codes=(
                stored.reason_codes
                or ("formal_readiness_report_unverified",)
            ),
        )
    freshness_reason = formal_readiness_freshness_reason(
        stored.report,
        now=checked_at,
    )
    if freshness_reason:
        return _unavailable_formal_readiness(
            checked_at=checked_at,
            state="failed",
            reason_codes=(freshness_reason,),
            freshness_policy=stored.report.freshness_policy,
        )
    return stored.report


@router.get("/overview", response_model=RadarOverviewResponse)
def get_radar_overview(response: Response):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_overview()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="雷达只读数据暂不可用",
        ) from exc


@router.get("/sectors", response_model=RadarSectorsResponse)
def get_radar_sectors(response: Response):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_sectors()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="雷达只读数据暂不可用",
        ) from exc


@router.get("/etfs", response_model=RadarEtfsResponse)
def get_radar_etfs(response: Response):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_etfs()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="行业ETF只读数据暂不可用",
        ) from exc


@router.get("/leaders", response_model=RadarLeadersResponse)
def get_radar_leaders(response: Response):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_leaders()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="三级龙头只读数据暂不可用",
        ) from exc


@router.get(
    "/sector-history",
    response_model=RadarSectorHistoryResponse,
)
def get_radar_sector_history(response: Response):
    """读取已持久化的真实行业历史汇总，不访问生产SQLite。"""

    _no_store(response)
    checked_at = datetime.now(timezone.utc)
    stored = load_latest_sector_history_evidence(
        store_dir=_sector_history_store_path(),
    )
    if stored.status != "available" or stored.payload is None:
        return RadarSectorHistoryResponse(
            checkedAt=checked_at,
            state=("failed" if stored.status == "failed" else "not_ready"),
            quality="unavailable",
            reasonCodes=list(stored.reasons),
        )
    payload = stored.payload
    analysis = payload["analysis"]
    presence = payload["tradingPresence"]
    calibration = analysis["calibrationProposal"]
    try:
        review = build_sector_threshold_review_draft(stored)
        approval = load_sector_threshold_approval(
            store_dir=_sector_history_store_path(),
            history_evidence=stored,
        )
        approval_evidence = approval.to_evidence()
        threshold_review_state = (
            "approved"
            if approval.status == "approved"
            else (
                "failed"
                if approval.status == "failed"
                else "review_ready"
            )
        )
        threshold_review_reasons = list(approval.reasons)
    except ValueError:
        review = None
        approval = None
        approval_evidence = {}
        threshold_review_state = "failed"
        threshold_review_reasons = [
            "sector_threshold_review_calibration_unverified"
        ]
    return RadarSectorHistoryResponse(
        checkedAt=checked_at,
        state="available",
        quality="complete",
        asOf=payload["asOf"],
        publishedAt=stored.published_at,
        requestedCount=payload["requestedCount"],
        fetchedCount=payload["fetchedCount"],
        reusedCount=payload["reusedCount"],
        failureCount=payload["failureCount"],
        sectorCount=analysis["sectorCount"],
        marketSampleCount=analysis["marketSampleCount"],
        historyCoverageReady=analysis["historyCoverageReady"],
        tradingPresenceRequestedCount=presence["requestedCount"],
        tradingPresenceReturnedCount=presence["returnedCount"],
        calibrationStatus=calibration["status"],
        observationDateCount=calibration["observationDateCount"],
        industryCount=calibration["industryCount"],
        marketRegimes=calibration["marketRegimes"],
        trainEndDate=calibration["trainEndDate"],
        holdoutStartDate=calibration["holdoutStartDate"],
        metricQuantiles=calibration["metricQuantiles"],
        metricSampleCounts=calibration["metricSampleCounts"],
        trainObservationDateCount=(
            calibration["trainObservationDateCount"]
        ),
        holdoutObservationDateCount=(
            calibration["holdoutObservationDateCount"]
        ),
        holdoutMetricSampleCounts=(
            calibration["holdoutMetricSampleCounts"]
        ),
        thresholdReviewState=threshold_review_state,
        calibrationIdentity=(
            review.calibration_identity if review else None
        ),
        thresholdSetId=approval_evidence.get("thresholdSetId"),
        approvalId=approval_evidence.get("approvalId"),
        approvedBy=approval_evidence.get("approvedBy"),
        approvedAt=approval_evidence.get("approvedAt"),
        thresholdReviewReasonCodes=threshold_review_reasons,
        formalApproval=bool(
            approval is not None and approval.status == "approved"
        ),
        gate=payload["gate"],
        reasonCodes=list(payload["reasons"]),
    )


@router.get(
    "/replays/latest/etfs",
    response_model=RadarReplayEtfResearchResponse,
)
def get_latest_radar_replay_etfs(response: Response):
    """读取已发布的ETF逐产品研究分流，不访问SQLite。"""

    _no_store(response)
    checked_at = datetime.now(timezone.utc)
    stored = load_latest_replay_etf_research(
        _replay_store_path(),
        checked_at=checked_at,
    )
    if stored.status != "available" or stored.snapshot is None:
        return RadarReplayEtfResearchResponse(
            checkedAt=checked_at,
            state=("failed" if stored.status == "failed" else "not_ready"),
            quality="unavailable",
            evidenceSha256=stored.evidence_sha256,
            snapshot=None,
            reasonCodes=list(stored.reasons),
        )
    snapshot = stored.snapshot
    quality = (
        "complete"
        if snapshot.evidence_incomplete_count == 0
        else "partial"
    )
    return RadarReplayEtfResearchResponse(
        checkedAt=checked_at,
        state="available",
        quality=quality,
        evidenceSha256=stored.evidence_sha256,
        snapshot=snapshot,
        reasonCodes=list(snapshot.reason_codes),
    )


@router.get(
    "/replays/latest",
    response_model=RadarReplayQualityResponse,
)
def get_latest_radar_replay(response: Response):
    """读取已发布的严格时点回放质量报告，不访问SQLite。"""

    _no_store(response)
    checked_at = datetime.now(timezone.utc)
    stored = load_latest_replay_report(
        store_dir=_replay_store_path(),
        checked_at=checked_at,
    )
    if stored.status != "available" or stored.report is None:
        store_failed = stored.status == "failed"
        return RadarReplayQualityResponse(
            checkedAt=checked_at,
            state=("failed" if store_failed else "not_ready"),
            quality="unavailable",
            engineeringState=("failed" if store_failed else "not_ready"),
            validationState=("failed" if store_failed else "not_started"),
            shadowCollectionAllowed=False,
            stage9QualityGatePassed=False,
            reasonCodes=list(stored.reasons),
        )

    report = stored.report
    quality = (
        "complete"
        if report.status == "ready"
        else ("partial" if report.status == "not_ready" else "unavailable")
    )
    return RadarReplayQualityResponse(
        checkedAt=checked_at,
        state=report.status,
        quality=quality,
        engineeringState={
            "ready": "complete",
            "not_ready": "not_ready",
            "failed": "failed",
        }[report.pipeline_status],
        validationState=(
            "validated"
            if report.effectiveness_status == "ready"
            else ("failed" if report.status == "failed" else "collecting")
        ),
        shadowCollectionAllowed=(report.pipeline_status == "ready"),
        stage9QualityGatePassed=(report.status == "ready"),
        replayRunId=report.replay_run_id,
        createdAt=report.created_at,
        evidenceSha256=stored.evidence_sha256,
        sampleCounts=report.sample_counts,
        includedCount=report.included_count,
        excludedCount=report.excluded_count,
        scopedExclusionCount=report.scoped_exclusion_count,
        scopedExclusionCounts=report.scoped_exclusion_counts,
        missingCount=report.missing_count,
        unverifiableCount=report.unverifiable_count,
        failedCount=report.failed_count,
        futureViolationCount=report.future_violation_count,
        duplicateStateViolationCount=(
            report.duplicate_state_violation_count
        ),
        multiStateViolationCount=report.multi_state_violation_count,
        labelCounts=report.label_counts,
        outputCounts=report.output_counts,
        etfReadinessCounts=report.etf_readiness_counts,
        comparableLabelCount=report.comparable_label_count,
        incomparableLabelCount=report.incomparable_label_count,
        disputedLabelCount=report.disputed_label_count,
        unverifiableLabelCount=report.unverifiable_label_count,
        unlabeledOutputTargetCount=report.unlabeled_output_target_count,
        unlabeledOutputTargetCounts=report.unlabeled_output_target_counts,
        partitionChronologyValid=report.partition_chronology_valid,
        missingDomains=report.missing_domains,
        missingPartitions=report.missing_partitions,
        missingLabelDomains=report.missing_label_domains,
        missingOutputDomains=report.missing_output_domains,
        unverifiableOutputDomains=report.unverifiable_output_domains,
        failedOutputDomains=report.failed_output_domains,
        readyOutputDomains=report.ready_output_domains,
        missingLabelPartitions=report.missing_label_partitions,
        reasonCodes=report.reason_codes,
        metrics=report.metrics,
    )


@router.get("/stocks/{symbol}", response_model=RadarStockResponse)
def get_radar_stock(
    response: Response,
    symbol: str = ApiPath(pattern=r"^(?:(?:sh|sz|hk|bj))?\d{5,6}$"),
):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_stock(symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="股票雷达只读数据暂不可用",
        ) from exc


@router.get(
    "/leaders/review-queue",
    response_model=RadarLeaderReviewQueueResponse,
)
def get_radar_leader_review_queue(
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_leader_review_queue(
                limit=limit,
                offset=offset,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="龙头风险审核队列暂不可用",
        ) from exc


@router.get(
    "/leaders/review-queue/document",
    response_model=RadarLeaderReviewDocumentResponse,
)
def get_radar_leader_review_document(
    response: Response,
    review_batch_id: str = Query(alias="reviewBatchId", min_length=1),
    document_id: str = Query(alias="documentId", min_length=1),
    candidate_category: str = Query(
        alias="candidateCategory",
        min_length=1,
    ),
):
    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            return _service(connection).build_leader_review_document(
                review_batch_id=review_batch_id,
                document_id=document_id,
                candidate_category=candidate_category,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="龙头风险公告详情暂不可用",
        ) from exc


@router.get(
    "/leaders/review-queue/review-form",
    response_model=RadarLeaderReviewFormResponse,
)
def get_radar_leader_review_form(
    response: Response,
    review_batch_id: str = Query(alias="reviewBatchId", min_length=1),
    document_id: str = Query(alias="documentId", min_length=1),
    candidate_category: str = Query(
        alias="candidateCategory",
        min_length=1,
    ),
):
    """读取真实正文与自动事实状态，页面不抓取、不写审核版本。"""

    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            service = _service(connection)
            queue = service.build_leader_review_queue(limit=1, offset=0)
            if queue.summary.status != "ready":
                raise ValueError("D2审核队列当前不可读")
            item_response = service.build_leader_review_document(
                review_batch_id=review_batch_id,
                document_id=document_id,
                candidate_category=candidate_category,
            )
            if item_response.item.content_status != "available":
                raise ValueError("正文快照尚未交付")
            context = build_review_form_data(
                service.risk_review_repository,
                review_batch_id=review_batch_id,
                document_id=document_id,
                candidate_category=candidate_category,
                as_of=datetime.now(timezone.utc),
                write_enabled=load_radar_settings().leader_d8_review_write_enabled,
            )
            content = context["content"]
            candidate = context["candidate"]
            return RadarLeaderReviewFormResponse(
                checkedAt=datetime.now(timezone.utc),
                mode=service._mode(),
                marketSession=item_response.market_session,
                summary=queue.summary,
                item=item_response.item,
                contentSha256=content.content_sha256,
                contentFetchedAt=content.fetched_at,
                pageCount=content.page_count,
                pages=[
                    RadarLeaderReviewPage(
                        pageNumber=page.page_number,
                        text=page.text,
                    )
                    for page in content.pages
                ],
                candidate=RadarLeaderReviewCandidate(
                    candidateId=candidate.candidate_id,
                    candidateKind=candidate.candidate_kind.value,
                    autoFactCount=len(context["autoFacts"].facts),
                    requiredFactKinds=context["requiredFactKinds"],
                    reasonCodes=list(context["autoFacts"].reasons),
                ),
                replayDiagnostic=context["replayDiagnostic"],
                nextReviewVersion=f"manual-review-v{len(context['versions']) + 1}",
                supersedesReviewVersion=(
                    context["versions"][-1].submission.review_version
                    if context["versions"] else None
                ),
                writeEnabled=context["writeEnabled"],
                writeReasonCode=context["writeReasonCode"],
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="龙头风险人工审核预览暂不可用",
        ) from exc


@router.post(
    "/leaders/review-queue/review-version/preflight",
    response_model=RadarLeaderReviewVersionPreflightResponse,
)
def post_radar_leader_review_version_preflight(
    response: Response,
    payload: RadarLeaderReviewVersionRequest,
):
    """只读构建候选D8版本并审查实质变化，不保存任何版本。"""

    _no_store(response)
    try:
        with open_radar_read_connection(_database_path()) as connection:
            service = _service(connection)
            repository = service.risk_review_repository
            if repository is None:
                raise ValueError("D8审核存储当前不可读")
            summary = repository.get_latest_review_batch_summary()
            if (
                summary is None
                or summary["reviewBatchId"] != payload.review_batch_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="审核批次已变化，请重新打开表单",
                )
            if not all((
                summary["queryCategoriesComplete"],
                summary["queryPagesComplete"],
                summary["queryWindowContinuous"],
            )):
                raise HTTPException(
                    status_code=409,
                    detail="审核批次完整性门禁未通过",
                )
            checked_at = datetime.now(timezone.utc)
            result = preflight_manual_review_version(
                repository,
                payload,
                as_of=checked_at,
            )
            write_enabled = (
                load_radar_settings().leader_d8_review_write_enabled
            )
            return RadarLeaderReviewVersionPreflightResponse(
                checkedAt=checked_at,
                reviewBatchId=payload.review_batch_id,
                documentId=payload.document_id,
                candidateCategory=payload.candidate_category,
                writeEnabled=write_enabled,
                submissionAllowed=(
                    result["status"] == "ready"
                    and write_enabled
                ),
                **result,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="人工审核草稿未通过正文、事件或关系预检",
        ) from exc


@router.post(
    "/leaders/review-queue/review-version",
    response_model=RadarLeaderReviewVersionResponse,
)
def post_radar_leader_review_version(
    request: Request,
    response: Response,
    payload: RadarLeaderReviewVersionRequest,
):
    """受保护地写入一条严格校验的D8研究性人工审核版本。"""

    del request
    _no_store(response)
    settings = load_radar_settings()
    if not settings.leader_d8_review_write_enabled:
        raise HTTPException(
            status_code=409,
            detail="D8人工审核写入开关未启用，当前只允许预览",
        )
    connection = sqlite3.connect(_database_path(), timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout = 10000")
        validate_applied_migrations(connection)
        repository = LeaderRiskReviewRepository(
            connection,
            clock=lambda: datetime.now(timezone.utc),
        )
        summary = repository.get_latest_review_batch_summary()
        if summary is None or summary["reviewBatchId"] != payload.review_batch_id:
            raise HTTPException(status_code=409, detail="审核批次已变化，请重新打开表单")
        if not all((
            summary["queryCategoriesComplete"],
            summary["queryPagesComplete"],
            summary["queryWindowContinuous"],
        )):
            raise HTTPException(status_code=409, detail="审核批次完整性门禁未通过")
        as_of = datetime.now(timezone.utc)
        version, _ = build_manual_review_version(
            repository,
            payload,
            as_of=as_of,
        )
        created = repository.save_review_version(
            payload.review_batch_id,
            version,
        )
        versions = repository.list_review_versions(
            payload.review_batch_id,
            payload.document_id,
            payload.candidate_category,
        )
        return RadarLeaderReviewVersionResponse(
            acceptedAt=as_of,
            created=created,
            reviewBatchId=payload.review_batch_id,
            documentId=payload.document_id,
            reviewVersion=version.submission.review_version,
            supersedesReviewVersion=version.submission.supersedes_review_version,
            reviewVersionCount=len(versions),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="人工审核版本未通过正文、事件或关系校验",
        ) from exc
    finally:
        connection.close()
