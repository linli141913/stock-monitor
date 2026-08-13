import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Request, Response

import market_calendar
from radar.api_contracts import (
    RadarEtfsResponse,
    RadarLeadersResponse,
    RadarLeaderReviewQueueResponse,
    RadarLeaderReviewDocumentResponse,
    RadarLeaderReviewFormResponse,
    RadarLeaderReviewPage,
    RadarLeaderReviewCandidate,
    RadarLeaderReviewVersionRequest,
    RadarLeaderReviewVersionResponse,
    RadarOverviewResponse,
    RadarSectorsResponse,
    RadarStockResponse,
)
from radar.config import load_radar_settings
from radar.etf_repository import EtfRepository
from radar.leader_repository import LeaderRepository
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.leader_risk_review_service import (
    build_manual_review_version,
    build_review_form_data,
)
from radar.migrations import validate_applied_migrations
from radar.read_service import RadarReadService
from radar.repository import RadarRepository


router = APIRouter(prefix="/api/radar", tags=["Mainline Radar"])


def _database_path() -> Union[str, Path]:
    import database

    return database.DB_PATH


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
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"


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
