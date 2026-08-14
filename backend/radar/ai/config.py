from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from radar.config import _read_bool, _read_int


@dataclass(frozen=True)
class RadarAiSettings:
    enabled: bool = False
    manual_enabled: bool = False
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    request_timeout_seconds: int = 60
    daily_call_limit: int = 20
    daily_token_limit: int = 100_000

    def __post_init__(self):
        if self.request_timeout_seconds < 1 or self.request_timeout_seconds > 120:
            raise ValueError("RADAR_LLM_TIMEOUT_SECONDS 必须在1到120之间")
        if self.daily_call_limit < 1:
            raise ValueError("RADAR_AI_DAILY_CALL_LIMIT 必须大于0")
        if self.daily_token_limit < 1:
            raise ValueError("RADAR_AI_DAILY_TOKEN_LIMIT 必须大于0")

    @property
    def configured(self) -> bool:
        return bool(
            (self.api_key or "").strip()
            and (self.base_url or "").strip()
            and (self.model or "").strip()
        )


def load_radar_ai_settings(
    environ: Optional[Mapping[str, str]] = None,
) -> RadarAiSettings:
    values = os.environ if environ is None else environ
    return RadarAiSettings(
        enabled=_read_bool(values, "RADAR_AI_ENABLED", False),
        manual_enabled=_read_bool(values, "RADAR_AI_MANUAL_ENABLED", False),
        api_key=(values.get("RADAR_LLM_API_KEY") or "").strip() or None,
        base_url=(values.get("RADAR_LLM_BASE_URL") or "").strip() or None,
        model=(values.get("RADAR_LLM_MODEL") or "").strip() or None,
        request_timeout_seconds=_read_int(
            values,
            "RADAR_LLM_TIMEOUT_SECONDS",
            60,
        ),
        daily_call_limit=_read_int(
            values,
            "RADAR_AI_DAILY_CALL_LIMIT",
            20,
        ),
        daily_token_limit=_read_int(
            values,
            "RADAR_AI_DAILY_TOKEN_LIMIT",
            100_000,
        ),
    )
