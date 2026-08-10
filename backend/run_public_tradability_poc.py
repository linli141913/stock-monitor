"""阶段6L-C4C-2免费公开源真实POC单次入口。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import multiprocessing
import os
from queue import Empty
import re
import sys
import time
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from market_calendar import (
    SHANGHAI_TZ,
    calculate_market_status,
    get_calendar_day_kind,
)
from radar.sources.leader_tradability_public_live_poc import (
    PUBLIC_LIVE_POC_CONTRACT_ID,
    run_public_live_poc,
    validate_public_live_symbols,
)


PUBLIC_LIVE_POC_CLI_CONTRACT_ID = (
    "radar-leader-tradability-public-live-poc-cli-v1"
)
PUBLIC_LIVE_WORKER_TIMEOUT_SECONDS = 35
PUBLIC_LIVE_WORKER_TERMINATION_GRACE_SECONDS = 1
_SAFE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _failure(reason: str, *, execution_status: str = "blocked") -> dict:
    return {
        "contractId": PUBLIC_LIVE_POC_CONTRACT_ID,
        "executionStatus": execution_status,
        "realPocStatus": (
            "not_run" if execution_status == "not_run" else "failed"
        ),
        "reason": reason,
        "resolutionStatus": None,
        "expectedCount": None,
        "returnedCount": None,
        "fieldCoverage": {},
        "reasons": [],
        "sourceStatuses": {},
        "sourceFailures": [],
        "elapsedMs": None,
        "asOf": None,
        "tradingDate": None,
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def _calendar_evidence(calendar: Any) -> Optional[dict]:
    if calendar is None:
        return None
    return {
        "kind": getattr(calendar, "kind", None),
        "sourceUrl": getattr(calendar, "source_url", None),
        "checkedAt": getattr(calendar, "checked_at", None),
    }


def _write(
    output_writer: Callable[[str], Any],
    payload: Mapping[str, Any],
    *,
    calendar: Any = None,
) -> None:
    value = dict(payload)
    value["cliContractId"] = PUBLIC_LIVE_POC_CLI_CONTRACT_ID
    value["calendar"] = _calendar_evidence(calendar)
    output_writer(json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ))


def _safe_string(value: Any, maximum: int = 120) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("invalid string")
    return value


def _safe_code(value: Any, maximum: int = 120) -> str:
    text = _safe_string(value, maximum)
    if not text or _SAFE_CODE_PATTERN.fullmatch(text) is None:
        raise ValueError("invalid code")
    return text


def _sanitize_worker_evidence(value: Any) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("contractId") != PUBLIC_LIVE_POC_CONTRACT_ID
        or value.get("executionStatus") not in {
            "completed", "blocked", "not_run"
        }
        or value.get("realPocStatus") not in {
            "completed", "failed", "not_run"
        }
    ):
        raise ValueError("invalid worker evidence")
    statuses = value.get("sourceStatuses", {})
    failures = value.get("sourceFailures", [])
    reasons = value.get("reasons", [])
    coverage = value.get("fieldCoverage", {})
    if not isinstance(statuses, dict) or not isinstance(coverage, dict):
        raise ValueError("invalid worker mapping")
    if not isinstance(failures, list) or not isinstance(reasons, list):
        raise ValueError("invalid worker list")
    sanitized_statuses = {
        _safe_code(key, 40): _safe_code(status, 40)
        for key, status in statuses.items()
    }
    sanitized_coverage = {}
    for key, amount in coverage.items():
        safe_key = _safe_code(key, 40)
        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not 0 <= float(amount) <= 1
        ):
            raise ValueError("invalid coverage")
        sanitized_coverage[safe_key] = float(amount)
    sanitized_failures = [
        _safe_code(item, 120) for item in failures[:20]
    ]
    sanitized_reasons = [
        _safe_code(item, 120) for item in reasons[:40]
    ]
    elapsed = value.get("elapsedMs")
    if elapsed is not None and (
        not isinstance(elapsed, int)
        or isinstance(elapsed, bool)
        or elapsed < 0
    ):
        raise ValueError("invalid elapsed")
    expected = value.get("expectedCount")
    returned = value.get("returnedCount")
    for count in (expected, returned):
        if count is not None and (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ValueError("invalid count")
    return {
        "contractId": PUBLIC_LIVE_POC_CONTRACT_ID,
        "executionStatus": value["executionStatus"],
        "realPocStatus": value["realPocStatus"],
        "reason": (
            _safe_code(value["reason"], 120)
            if value.get("reason") is not None
            else None
        ),
        "resolutionStatus": (
            _safe_code(value["resolutionStatus"], 40)
            if value.get("resolutionStatus") is not None
            else None
        ),
        "expectedCount": expected,
        "returnedCount": returned,
        "fieldCoverage": sanitized_coverage,
        "reasons": sanitized_reasons,
        "sourceStatuses": sanitized_statuses,
        "sourceFailures": sanitized_failures,
        "elapsedMs": elapsed,
        "asOf": _safe_string(value.get("asOf"), 50),
        "tradingDate": _safe_string(value.get("tradingDate"), 20),
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def _collect_worker_payload(
    symbols: Tuple[str, ...],
    as_of: datetime,
    output_queue: Any,
) -> None:
    try:
        payload = run_public_live_poc(
            symbols=symbols,
            as_of=as_of,
        ).to_evidence()
    except Exception:
        payload = _failure("public_live_worker_execution_failed")
    try:
        output_queue.put(json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ))
    except Exception:
        return


def _worker_process_entry(
    worker_target: Callable[..., Any],
    symbols: Tuple[str, ...],
    as_of: datetime,
    output_queue: Any,
) -> None:
    null_fd = None
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
    except Exception:
        try:
            output_queue.put(json.dumps(_failure(
                "public_live_worker_output_isolation_failed"
            )))
        except Exception:
            pass
        return
    finally:
        if null_fd is not None:
            os.close(null_fd)
    try:
        worker_target(symbols, as_of, output_queue)
    except Exception:
        try:
            output_queue.put(json.dumps(_failure(
                "public_live_worker_execution_failed"
            )))
        except Exception:
            return


def _close_worker_queue(output_queue: Any) -> None:
    try:
        output_queue.close()
        output_queue.cancel_join_thread()
    except Exception:
        return


def _run_bounded_worker(
    symbols: Tuple[str, ...],
    as_of: datetime,
    *,
    worker_target: Callable[..., Any] = _collect_worker_payload,
    timeout_seconds: float = PUBLIC_LIVE_WORKER_TIMEOUT_SECONDS,
) -> dict:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        return _failure("public_live_worker_timeout_invalid")
    started = time.monotonic()
    total_deadline = started + float(timeout_seconds)
    termination_grace = min(
        PUBLIC_LIVE_WORKER_TERMINATION_GRACE_SECONDS,
        float(timeout_seconds) / 4,
    )
    execution_deadline = total_deadline - termination_grace
    try:
        context = multiprocessing.get_context("spawn")
        output_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_worker_process_entry,
            args=(worker_target, symbols, as_of, output_queue),
            daemon=True,
        )
        process.start()
    except Exception:
        return _failure("public_live_worker_start_failed")
    process.join(max(0.0, execution_deadline - time.monotonic()))
    if process.is_alive():
        try:
            process.kill()
        except Exception:
            try:
                process.terminate()
            except Exception:
                _close_worker_queue(output_queue)
                return _failure(
                    "public_live_worker_termination_failed"
                )
        process.join(max(0.0, total_deadline - time.monotonic()))
        still_alive = process.is_alive()
        _close_worker_queue(output_queue)
        if still_alive:
            return _failure("public_live_worker_termination_failed")
        return _failure("public_live_worker_timeout")
    try:
        remaining_seconds = max(
            0.0,
            total_deadline - time.monotonic(),
        )
        output = (
            output_queue.get(timeout=remaining_seconds)
            if remaining_seconds > 0
            else output_queue.get_nowait()
        )
    except Empty:
        output = None
    except Exception:
        output = None
    finally:
        _close_worker_queue(output_queue)
    if not isinstance(output, str) or not output.strip():
        return _failure("public_live_worker_output_missing")
    try:
        return _sanitize_worker_evidence(json.loads(output))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("public_live_worker_output_invalid")


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    now_provider: Callable[[], datetime] = _now,
    day_kind_resolver: Callable[..., Any] = get_calendar_day_kind,
    live_runner: Callable[
        [Tuple[str, ...], datetime], Mapping[str, Any]
    ] = _run_bounded_worker,
    output_writer: Callable[[str], Any] = print,
) -> int:
    parser = argparse.ArgumentParser(
        description="执行一次不落盘的免费公开源可交易性字段POC",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="1至8只沪深A股六位证券代码",
    )
    parser.add_argument(
        "--confirm-live-poc",
        action="store_true",
        help="确认本次会只读调用真实公开来源",
    )
    args = parser.parse_args(argv)
    if not args.confirm_live_poc:
        _write(
            output_writer,
            _failure(
                "public_live_confirmation_missing",
                execution_status="not_run",
            ),
        )
        return 2
    symbols = validate_public_live_symbols(args.symbols)
    if symbols is None:
        _write(output_writer, _failure("public_live_query_invalid"))
        return 1
    try:
        now = now_provider()
    except Exception:
        now = None
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        _write(
            output_writer,
            _failure("public_live_observation_time_unverified"),
        )
        return 1
    now = now.astimezone(SHANGHAI_TZ)
    try:
        calendar = day_kind_resolver("cn", now.date())
    except Exception:
        _write(
            output_writer,
            _failure(
                "public_live_calendar_failed",
                execution_status="not_run",
            ),
        )
        return 2
    calendar_kind = getattr(calendar, "kind", None)
    if calendar_kind != "full":
        reason_kind = (
            calendar_kind
            if calendar_kind in {"closed", "half", "unknown"}
            else "unverified"
        )
        _write(
            output_writer,
            _failure(
                f"public_live_calendar_{reason_kind}",
                execution_status="not_run",
            ),
            calendar=calendar,
        )
        return 2
    market_status = calculate_market_status("cn", now, calendar_kind)
    if market_status.code != "trading":
        _write(
            output_writer,
            _failure(
                f"public_live_market_{market_status.code}",
                execution_status="not_run",
            ),
            calendar=calendar,
        )
        return 2
    try:
        live_evidence = _sanitize_worker_evidence(
            dict(live_runner(symbols, now))
        )
    except Exception:
        live_evidence = _failure("public_live_execution_failed")
    try:
        completed_at = now_provider()
    except Exception:
        completed_at = None
    if (
        not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or completed_at.utcoffset() is None
    ):
        _write(
            output_writer,
            _failure("public_live_completion_time_unverified"),
            calendar=calendar,
        )
        return 1
    completed_at = completed_at.astimezone(SHANGHAI_TZ)
    completed_status = calculate_market_status(
        "cn",
        completed_at,
        calendar_kind,
    )
    if (
        completed_at < now
        or completed_at.date() != now.date()
        or completed_status.code != "trading"
    ):
        _write(
            output_writer,
            _failure(
                "public_live_market_window_closed_during_execution"
            ),
            calendar=calendar,
        )
        return 1
    _write(output_writer, live_evidence, calendar=calendar)
    if live_evidence["executionStatus"] != "completed":
        return 1
    if live_evidence["resolutionStatus"] == "field_candidate":
        return 0
    return 2


def _worker_main(argv: Sequence[str]) -> int:
    del argv
    payload = _failure(
        "public_live_worker_unauthorized",
        execution_status="not_run",
    )
    print(json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ))
    return 2


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        raise SystemExit(_worker_main(sys.argv[1:]))
    raise SystemExit(main())
