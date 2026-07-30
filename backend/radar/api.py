import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

from fastapi import APIRouter, HTTPException, Response

import market_calendar
from radar.api_contracts import (
    RadarEtfsResponse,
    RadarLeadersResponse,
    RadarOverviewResponse,
    RadarSectorsResponse,
)
from radar.config import load_radar_settings
from radar.etf_repository import EtfRepository
from radar.leader_repository import LeaderRepository
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
    if settings.leader_stage6_enabled:
        try:
            leader_repository = LeaderRepository(connection)
        except Exception:
            # 阶段6存储未就绪时保持只读not_ready，不影响其他雷达模块。
            leader_repository = None
    return RadarReadService(
        RadarRepository(connection),
        settings=settings,
        clock=lambda: datetime.now(timezone.utc),
        market_status_provider=market_calendar.get_market_status,
        etf_repository=etf_repository,
        leader_repository=leader_repository,
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
