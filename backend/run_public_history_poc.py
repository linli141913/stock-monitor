"""阶段6L-B1免费历史输入真实POC单次入口。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as datetime_time, timedelta
import json
import multiprocessing
import os
from queue import Empty
import re
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import requests

from market_calendar import (
    SHANGHAI_TZ,
    SSE_CALENDAR_URL,
    parse_sse_calendar,
)
from radar.contracts import (
    IndustryIdentityStatus,
    IndustryRecordStatus,
    SourceStatus,
)
from radar.leader_history_features import HistoryAdjustmentBasis
from radar.sources.industry_classification import (
    fetch_industry_classification,
)
from radar.sources.leader_history_public_poc import (
    MAXIMUM_INDUSTRY_MEMBER_COUNT,
    PUBLIC_HISTORY_POC_CONTRACT_ID,
    PointInTimeIndustryMembership,
    PublicHistoryPocQuery,
    TENCENT_HISTORY_URL,
    parse_tencent_history_payload,
    run_public_history_input_poc,
)
from radar.sources.security_master import fetch_security_master


PUBLIC_HISTORY_CLI_CONTRACT_ID = (
    "radar-leader-public-history-poc-cli-v1"
)
CAPCO_PUBLICATION_PAGE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/20260403/"
    "j_2026040315001700017751997384265508.html"
)
WORKER_TIMEOUT_SECONDS = 120
WORKER_TERMINATION_GRACE_SECONDS = 1
HISTORY_REQUEST_TIMEOUT_SECONDS = 8
HISTORY_FETCH_WORKERS = 8
_SAFE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:+-]+$")


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _failure(
    reason: str,
    *,
    execution_status: str = "failed",
    real_poc_status: str = "failed",
) -> dict:
    return {
        "contractId": PUBLIC_HISTORY_POC_CONTRACT_ID,
        "executionStatus": execution_status,
        "realPocStatus": real_poc_status,
        "resolutionStatus": None,
        "reason": reason,
        "reasons": [],
        "memberCount": 0,
        "excludedOutOfScopeCount": 0,
        "expectedSeriesCount": 0,
        "completeSeriesCount": 0,
        "seriesCoverage": 0.0,
        "historyInputReady": False,
        "sourceStatuses": {},
        "sourceFailures": [],
        "historyEndDate": None,
        "asOf": None,
        "elapsedMs": None,
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


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


def _safe_count(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError("invalid count")
    return value


def _sanitize_evidence(value: Any) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("contractId") != PUBLIC_HISTORY_POC_CONTRACT_ID
        or value.get("executionStatus") not in {
            "completed", "failed", "blocked", "not_run"
        }
        or value.get("realPocStatus") not in {
            "completed", "partial", "failed", "not_run"
        }
        or value.get("resolutionStatus") not in {
            "ready", "partial", "failed", None
        }
    ):
        raise ValueError("invalid evidence")
    reasons = value.get("reasons", [])
    failures = value.get("sourceFailures", [])
    statuses = value.get("sourceStatuses", {})
    if (
        not isinstance(reasons, list)
        or not isinstance(failures, list)
        or not isinstance(statuses, dict)
    ):
        raise ValueError("invalid evidence collection")
    coverage = value.get("seriesCoverage", 0.0)
    if (
        not isinstance(coverage, (int, float))
        or isinstance(coverage, bool)
        or not 0 <= float(coverage) <= 1
    ):
        raise ValueError("invalid coverage")
    elapsed = value.get("elapsedMs")
    if elapsed is not None and (
        not isinstance(elapsed, int)
        or isinstance(elapsed, bool)
        or elapsed < 0
    ):
        raise ValueError("invalid elapsed")
    return {
        "contractId": PUBLIC_HISTORY_POC_CONTRACT_ID,
        "executionStatus": value["executionStatus"],
        "realPocStatus": value["realPocStatus"],
        "resolutionStatus": value.get("resolutionStatus"),
        "reason": (
            _safe_code(value["reason"])
            if value.get("reason") is not None
            else None
        ),
        "reasons": [_safe_code(item) for item in reasons[:30]],
        "memberCount": _safe_count(value.get("memberCount", 0)),
        "excludedOutOfScopeCount": _safe_count(
            value.get("excludedOutOfScopeCount", 0)
        ),
        "expectedSeriesCount": _safe_count(
            value.get("expectedSeriesCount", 0)
        ),
        "completeSeriesCount": _safe_count(
            value.get("completeSeriesCount", 0)
        ),
        "seriesCoverage": float(coverage),
        "historyInputReady": value.get("historyInputReady") is True,
        "sourceStatuses": {
            _safe_code(key, 40): _safe_code(status, 40)
            for key, status in statuses.items()
        },
        "sourceFailures": [_safe_code(item) for item in failures[:30]],
        "historyEndDate": _safe_string(value.get("historyEndDate"), 20),
        "asOf": _safe_string(value.get("asOf"), 50),
        "elapsedMs": elapsed,
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def _write(
    output_writer: Callable[[str], Any],
    payload: Mapping[str, Any],
) -> None:
    value = dict(payload)
    value["cliContractId"] = PUBLIC_HISTORY_CLI_CONTRACT_ID
    output_writer(json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ))


def _fetch_calendar_text() -> str:
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        SSE_CALENDAR_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=5,
    )
    response.raise_for_status()
    encoding = response.apparent_encoding or response.encoding or "utf-8"
    return response.content.decode(encoding, errors="replace")


def _completed_trade_dates(
    as_of: datetime,
    page_text: str,
) -> tuple[date, ...]:
    local = as_of.astimezone(SHANGHAI_TZ)
    current = local.date()
    if local.time() < datetime_time(15, 30):
        current -= timedelta(days=1)
    snapshots = {}
    values = []
    for _ in range(80):
        if current.weekday() < 5:
            snapshot = snapshots.get(current.year)
            if snapshot is None:
                snapshot = parse_sse_calendar(page_text, current.year)
                snapshots[current.year] = snapshot
            if current.isoformat() not in snapshot.closed_days:
                values.append(current)
                if len(values) == 21:
                    return tuple(reversed(values))
        current -= timedelta(days=1)
    raise ValueError("official_trade_dates_unavailable")


def _query_symbol(symbol: str) -> str:
    if symbol.startswith("6"):
        return f"sh{symbol}"
    if symbol.startswith(("0", "2", "3")):
        return f"sz{symbol}"
    raise ValueError("history_symbol_exchange_unknown")


def _select_sh_sz_industry_members(
    accepted_records: Sequence[Any],
    master_records: Sequence[Any],
    industry_code: str,
) -> tuple[tuple[str, ...], int]:
    exchange_by_symbol = {
        item.symbol: item.exchange
        for item in master_records
    }
    all_members = tuple(sorted({
        item.security_identity
        for item in accepted_records
        if item.division_code == industry_code
        and item.security_identity is not None
    }))
    members = tuple(
        item for item in all_members
        if exchange_by_symbol.get(item) in {"sse", "szse"}
    )
    return members, len(all_members) - len(members)


def _fetch_tencent_series(
    symbol: str,
    expected_dates: tuple[date, ...],
    *,
    board_index: bool = False,
):
    query_symbol = symbol if board_index else _query_symbol(symbol)
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        TENCENT_HISTORY_URL,
        params={"param": f"{query_symbol},day,,,40,qfq"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=HISTORY_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return parse_tencent_history_payload(
        symbol=symbol,
        query_symbol=query_symbol,
        payload=response.json(),
        expected_trade_dates=expected_dates,
        fetched_at=_now(),
        adjustment_basis=(
            HistoryAdjustmentBasis.CONTINUOUS_INDEX
            if board_index
            else HistoryAdjustmentBasis.FORWARD_ADJUSTED
        ),
    )


def _history_exception_code(exc: Exception) -> str:
    known = {
        "tencent_history_payload_invalid": "payload_invalid",
        "tencent_history_symbol_missing": "symbol_missing",
        "tencent_history_rows_missing": "rows_missing",
        "tencent_history_adjustment_unverified": "adjustment_unverified",
    }
    if isinstance(exc, ValueError):
        return known.get(str(exc), "payload_invalid")
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.RequestException):
        return "transport_failed"
    return "request_failed"


def _history_series_issue_code(
    series: Any,
    expected_dates: tuple[date, ...],
) -> Optional[str]:
    points = getattr(series, "points", None)
    if not isinstance(points, tuple):
        return "payload_invalid"
    if tuple(getattr(item, "trade_date", None) for item in points) != (
        expected_dates
    ):
        return "dates_incomplete"
    return None


def _source_status(status: SourceStatus, has_issues: bool) -> str:
    if status == SourceStatus.FAILED:
        return "failed"
    return "degraded" if has_issues else "completed"


def _collect_default_public_history_evidence(
    symbol: str,
    as_of: datetime,
) -> dict:
    import akshare as ak

    started = time.monotonic()
    calendar_text = _fetch_calendar_text()
    expected_dates = _completed_trade_dates(as_of, calendar_text)
    run_id = f"public-history-{as_of:%Y%m%dT%H%M%S}"
    master = fetch_security_master(
        radar_run_id=run_id,
        batch_id=f"{run_id}-security-master",
        as_of=as_of,
    )
    observed_at = _now()
    classification = fetch_industry_classification(
        radar_run_id=run_id,
        batch_id=f"{run_id}-industry-classification",
        as_of=as_of,
        publication_page_url=CAPCO_PUBLICATION_PAGE_URL,
        current_security_master=tuple(master.items),
        first_observed_at=observed_at,
    )
    release = classification.release
    if release is None:
        evidence = _failure("industry_classification_unavailable")
        evidence["sourceStatuses"] = {
            "tradingCalendar": "completed",
            "securityMaster": "degraded" if master.meta.issues else "completed",
            "industryClassification": "failed",
            "tencentHistory": "not_run",
        }
        evidence["sourceFailures"] = [
            f"industryClassification:{issue.code}"
            for issue in classification.issues
        ]
        return evidence

    accepted = tuple(
        item for item in classification.records
        if item.record_status == IndustryRecordStatus.ACCEPTED
        and item.identity_status in {
            IndustryIdentityStatus.EXACT,
            IndustryIdentityStatus.VERIFIED_ALIAS,
        }
        and item.security_identity is not None
    )
    candidate_records = tuple(
        item for item in accepted
        if item.security_identity == symbol
    )
    if len(candidate_records) != 1:
        evidence = _failure(
            "candidate_industry_membership_unavailable",
            execution_status="completed",
            real_poc_status="partial",
        )
        evidence["resolutionStatus"] = "partial"
        evidence["historyEndDate"] = expected_dates[-1].isoformat()
        return evidence
    industry_code = candidate_records[0].division_code
    members, excluded_out_of_scope_count = (
        _select_sh_sz_industry_members(
            accepted,
            tuple(master.items),
            industry_code,
        )
    )
    board_index = "sh000001" if symbol.startswith("6") else "sz399001"
    expected_series_count = len(members) + 1
    source_statuses = {
        "tradingCalendar": "completed",
        "securityMaster": "degraded" if master.meta.issues else "completed",
        "industryClassification": _source_status(
            classification.status,
            bool(classification.issues),
        ),
        "tencentHistory": "not_run",
    }
    source_failures = [
        f"securityMaster:{issue.code}" for issue in master.meta.issues
    ] + [
        f"industryClassification:{issue.code}"
        for issue in classification.issues
    ]
    if len(members) > MAXIMUM_INDUSTRY_MEMBER_COUNT:
        evidence = _failure(
            "industry_member_budget_exceeded",
            execution_status="completed",
            real_poc_status="partial",
        )
        evidence.update({
            "resolutionStatus": "partial",
            "reasons": ["industry_member_budget_exceeded"],
            "memberCount": len(members),
            "excludedOutOfScopeCount": excluded_out_of_scope_count,
            "expectedSeriesCount": expected_series_count,
            "sourceStatuses": source_statuses,
            "sourceFailures": source_failures,
            "historyEndDate": expected_dates[-1].isoformat(),
            "asOf": as_of.isoformat(),
        })
        return evidence

    series_by_symbol = {}
    failures = []
    targets = tuple((item, False) for item in members) + (
        (board_index, True),
    )
    with ThreadPoolExecutor(max_workers=HISTORY_FETCH_WORKERS) as executor:
        futures = {
            executor.submit(
                _fetch_tencent_series,
                item,
                expected_dates,
                board_index=is_board,
            ): item
            for item, is_board in targets
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                series_by_symbol[item] = future.result()
            except Exception as exc:
                failures.append(
                    f"tencentHistory:{item}:{_history_exception_code(exc)}"
                )
    for item, series in series_by_symbol.items():
        issue_code = _history_series_issue_code(series, expected_dates)
        if issue_code is not None:
            failures.append(f"tencentHistory:{item}:{issue_code}")

    completed_at = _now()
    membership = PointInTimeIndustryMembership(
        release_id=f"capco-{release.release_period}",
        source_contract_id="capco-industry-classification-v1",
        industry_code=industry_code,
        candidate_symbol=symbol,
        member_symbols=members,
        published_date=release.published_date,
        classification_start_date=release.classification_start_date,
        first_observed_at=release.first_observed_at,
        fetched_at=release.fetched_at,
        document_sha256=f"sha256:{release.document_sha256}",
        excluded_out_of_scope_count=excluded_out_of_scope_count,
    )
    result = run_public_history_input_poc(PublicHistoryPocQuery(
        as_of=completed_at,
        expected_trade_dates=expected_dates,
        candidate_symbol=symbol,
        board_index_symbol=board_index,
        membership=membership,
        series_by_symbol=series_by_symbol,
    ))
    evidence = result.to_evidence()
    evidence.update({
        "executionStatus": "completed",
        "realPocStatus": (
            "completed"
            if result.resolution_status == "ready"
            else "partial"
        ),
        "reason": (
            None if result.resolution_status == "ready"
            else result.reasons[0]
        ),
        "sourceStatuses": {
            **source_statuses,
            "tencentHistory": (
                "completed" if not failures else "degraded"
            ),
        },
        "sourceFailures": source_failures + failures,
        "historyEndDate": expected_dates[-1].isoformat(),
        "asOf": completed_at.isoformat(),
        "elapsedMs": int((time.monotonic() - started) * 1000),
    })
    return evidence


def _collect_worker_payload(
    symbol: str,
    as_of: datetime,
    output_queue: Any,
) -> None:
    try:
        payload = _collect_default_public_history_evidence(symbol, as_of)
    except Exception:
        payload = _failure("public_history_worker_execution_failed")
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
    symbol: str,
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
                "public_history_worker_output_isolation_failed"
            )))
        except Exception:
            pass
        return
    finally:
        if null_fd is not None:
            os.close(null_fd)
    worker_target(symbol, as_of, output_queue)


def _close_queue(output_queue: Any) -> None:
    try:
        output_queue.close()
        output_queue.cancel_join_thread()
    except Exception:
        return


def _run_bounded_worker(
    symbol: str,
    as_of: datetime,
    *,
    worker_target: Callable[..., Any] = _collect_worker_payload,
    timeout_seconds: float = WORKER_TIMEOUT_SECONDS,
) -> dict:
    started = time.monotonic()
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        return _failure("public_history_worker_timeout_invalid")
    deadline = started + float(timeout_seconds)
    grace = min(
        WORKER_TERMINATION_GRACE_SECONDS,
        float(timeout_seconds) / 4,
    )
    context = multiprocessing.get_context("spawn")
    output_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_worker_process_entry,
        args=(worker_target, symbol, as_of, output_queue),
        daemon=True,
    )
    try:
        process.start()
    except Exception:
        _close_queue(output_queue)
        return _failure("public_history_worker_start_failed")
    process.join(max(0.0, deadline - grace - time.monotonic()))
    if process.is_alive():
        try:
            process.kill()
        except Exception:
            process.terminate()
        process.join(max(0.0, deadline - time.monotonic()))
        _close_queue(output_queue)
        return _failure("public_history_worker_timeout")
    try:
        raw = output_queue.get(timeout=max(0.05, deadline - time.monotonic()))
        payload = json.loads(raw)
        result = _sanitize_evidence(payload)
    except (Empty, json.JSONDecodeError, ValueError, TypeError):
        result = _failure("public_history_worker_result_invalid")
    finally:
        _close_queue(output_queue)
    result["elapsedMs"] = int((time.monotonic() - started) * 1000)
    return result


def _valid_cli_symbol(symbol: str) -> bool:
    return (
        len(symbol) == 6
        and symbol.isdigit()
        and symbol.startswith(("0", "3", "6"))
    )


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    now_provider: Callable[[], datetime] = _now,
    live_runner: Callable[[str, datetime], Mapping[str, Any]] = (
        _run_bounded_worker
    ),
    output_writer: Callable[[str], Any] = print,
) -> int:
    parser = argparse.ArgumentParser(
        description="执行一次不落盘的免费历史输入POC",
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--confirm-live-poc", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_poc:
        evidence = _failure(
            "public_history_confirmation_missing",
            execution_status="not_run",
            real_poc_status="not_run",
        )
        _write(output_writer, evidence)
        return 2
    symbol = str(args.symbol or "").strip()
    if not _valid_cli_symbol(symbol):
        evidence = _failure(
            "public_history_symbol_invalid",
            execution_status="blocked",
            real_poc_status="not_run",
        )
        _write(output_writer, evidence)
        return 1
    try:
        as_of = now_provider()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("timezone missing")
        evidence = _sanitize_evidence(dict(live_runner(symbol, as_of)))
    except Exception:
        evidence = _failure("public_history_execution_failed")
    _write(output_writer, evidence)
    if (
        evidence["realPocStatus"] == "completed"
        and evidence["resolutionStatus"] == "ready"
    ):
        return 0
    if evidence["realPocStatus"] in {"partial", "not_run"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
