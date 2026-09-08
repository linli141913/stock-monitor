"""跨模块不可信 JSON 的统一严格解码器。"""

from __future__ import annotations

import json
from typing import Any


def strict_json_loads(
    raw: bytes | str,
    *,
    error_code: str = "radar_json_unverified",
) -> Any:
    """解析 UTF-8 JSON，并拒绝重复键和非有限数值常量。"""

    if type(raw) is bytes:
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError(error_code) from None
    elif type(raw) is str:
        text = raw
    else:
        raise TypeError(error_code)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(error_code)
            result[key] = value
        return result

    def reject_non_finite(_: str) -> Any:
        raise ValueError(error_code)

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ValueError(error_code) from None


__all__ = ["strict_json_loads"]
