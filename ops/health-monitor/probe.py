#!/usr/bin/env python3
"""从外部检查股票监测助手的脱敏健康端点。"""

import argparse
import json
import time
from json import JSONDecodeError
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REQUIRED_COMPONENTS = (
    "vercel",
    "tunnel",
    "fastapi",
    "backgroundTasks",
)
MAX_RESPONSE_BYTES = 64 * 1024


class ProbeResult(NamedTuple):
    ok: bool
    reason: str


def evaluate_payload(payload: Any) -> ProbeResult:
    if not isinstance(payload, Mapping):
        return ProbeResult(False, "invalid_contract")
    components = payload.get("components")
    if not isinstance(components, Mapping):
        return ProbeResult(False, "invalid_contract")
    if not isinstance(payload.get("checkedAt"), str):
        return ProbeResult(False, "invalid_contract")
    if any(component not in components for component in REQUIRED_COMPONENTS):
        return ProbeResult(False, "invalid_contract")
    if payload.get("status") != "healthy":
        return ProbeResult(False, "health_not_healthy")
    if any(components.get(component) != "healthy" for component in REQUIRED_COMPONENTS):
        return ProbeResult(False, "component_not_healthy")
    return ProbeResult(True, "healthy")


def probe_once(url: str, timeout_seconds: int) -> ProbeResult:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "stock-monitor-external-health/1.0",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                return ProbeResult(False, "http_unhealthy")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError:
        return ProbeResult(False, "http_unhealthy")
    except (TimeoutError, URLError, OSError):
        return ProbeResult(False, "network_unavailable")

    if len(body) > MAX_RESPONSE_BYTES:
        return ProbeResult(False, "invalid_contract")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        return ProbeResult(False, "invalid_json")
    return evaluate_payload(payload)


def run_probe(
    url: str,
    attempts: int,
    delay_seconds: int,
    timeout_seconds: int,
    probe_func: Callable[[str, int], ProbeResult] = probe_once,
    sleep_func: Callable[[float], None] = time.sleep,
    output_func: Callable[[str], None] = print,
) -> int:
    for attempt in range(1, attempts + 1):
        result = probe_func(url, timeout_seconds)
        output_func(f"第{attempt}次检查：{result.reason}")
        if result.ok:
            return 0
        if attempt < attempts:
            sleep_func(delay_seconds)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="股票监测助手外部健康探针")
    parser.add_argument("--url", required=True, help="脱敏健康端点")
    parser.add_argument("--attempts", type=int, default=2, help="连续检查次数")
    parser.add_argument("--delay", type=int, default=60, help="两次检查间隔秒数")
    parser.add_argument("--timeout", type=int, default=15, help="单次请求超时秒数")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.attempts < 2:
        parser.error("连续检查次数不得少于2")
    if args.delay < 0 or args.timeout <= 0:
        parser.error("检查间隔和超时必须有效")
    return run_probe(
        args.url,
        attempts=args.attempts,
        delay_seconds=args.delay,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
