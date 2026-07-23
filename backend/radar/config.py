import os
from dataclasses import dataclass
from typing import Mapping, Optional


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class RadarSettings:
    enabled: bool = False
    shadow_mode: bool = False
    sector_shadow_enabled: bool = False
    market_shadow_enabled: bool = False
    stock_scan_interval_seconds: int = 180
    etf_scan_interval_seconds: int = 300
    sector_scan_interval_seconds: int = 180
    market_scan_interval_seconds: int = 180
    quote_batch_size: int = 100
    quote_timeout_seconds: float = 5.0
    minimum_row_coverage: float = 0.995
    minimum_required_field_coverage: float = 0.99
    maximum_quote_age_seconds: int = 90


def _read_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} 必须是 true/false、1/0、yes/no 或 on/off")


def _read_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _read_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc


def load_radar_settings(
    environ: Optional[Mapping[str, str]] = None,
) -> RadarSettings:
    """读取雷达配置，不修改进程或项目环境变量。"""
    values = os.environ if environ is None else environ
    settings = RadarSettings(
        enabled=_read_bool(values, "RADAR_ENABLED", False),
        shadow_mode=_read_bool(values, "RADAR_SHADOW_MODE", False),
        sector_shadow_enabled=_read_bool(
            values,
            "RADAR_SECTOR_SHADOW_ENABLED",
            False,
        ),
        market_shadow_enabled=_read_bool(
            values,
            "RADAR_MARKET_SHADOW_ENABLED",
            False,
        ),
        stock_scan_interval_seconds=_read_int(
            values,
            "RADAR_SCAN_INTERVAL_SECONDS",
            180,
        ),
        etf_scan_interval_seconds=_read_int(
            values,
            "RADAR_ETF_SCAN_INTERVAL_SECONDS",
            300,
        ),
        sector_scan_interval_seconds=_read_int(
            values,
            "RADAR_SECTOR_SCAN_INTERVAL_SECONDS",
            180,
        ),
        market_scan_interval_seconds=_read_int(
            values,
            "RADAR_MARKET_SCAN_INTERVAL_SECONDS",
            180,
        ),
        quote_batch_size=_read_int(values, "RADAR_QUOTE_BATCH_SIZE", 100),
        quote_timeout_seconds=_read_float(
            values,
            "RADAR_QUOTE_TIMEOUT_SECONDS",
            5.0,
        ),
        minimum_row_coverage=_read_float(
            values,
            "RADAR_MINIMUM_ROW_COVERAGE",
            0.995,
        ),
        minimum_required_field_coverage=_read_float(
            values,
            "RADAR_MINIMUM_REQUIRED_FIELD_COVERAGE",
            0.99,
        ),
        maximum_quote_age_seconds=_read_int(
            values,
            "RADAR_MAXIMUM_QUOTE_AGE_SECONDS",
            90,
        ),
    )

    if settings.stock_scan_interval_seconds < 60:
        raise ValueError("RADAR_SCAN_INTERVAL_SECONDS 不得小于60")
    if settings.etf_scan_interval_seconds < 60:
        raise ValueError("RADAR_ETF_SCAN_INTERVAL_SECONDS 不得小于60")
    if settings.sector_scan_interval_seconds < 60:
        raise ValueError("RADAR_SECTOR_SCAN_INTERVAL_SECONDS 不得小于60")
    if settings.market_scan_interval_seconds < 60:
        raise ValueError("RADAR_MARKET_SCAN_INTERVAL_SECONDS 不得小于60")
    if not 1 <= settings.quote_batch_size <= 100:
        raise ValueError("RADAR_QUOTE_BATCH_SIZE 必须在1到100之间")
    if not 0 < settings.quote_timeout_seconds <= 30:
        raise ValueError("RADAR_QUOTE_TIMEOUT_SECONDS 必须在0到30秒之间")
    if not 0 <= settings.minimum_row_coverage <= 1:
        raise ValueError("RADAR_MINIMUM_ROW_COVERAGE 必须在0到1之间")
    if not 0 <= settings.minimum_required_field_coverage <= 1:
        raise ValueError("RADAR_MINIMUM_REQUIRED_FIELD_COVERAGE 必须在0到1之间")
    if settings.maximum_quote_age_seconds <= 0:
        raise ValueError("RADAR_MAXIMUM_QUOTE_AGE_SECONDS 必须大于0")
    return settings
