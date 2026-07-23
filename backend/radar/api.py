import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

from fastapi import APIRouter, HTTPException, Response

import market_calendar
from radar.api_contracts import RadarOverviewResponse, RadarSectorsResponse
from radar.config import load_radar_settings
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
    validate_applied_migrations(connection)
    return RadarReadService(
        RadarRepository(connection),
        settings=load_radar_settings(),
        clock=lambda: datetime.now(timezone.utc),
        market_status_provider=market_calendar.get_market_status,
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
