"""阶段6L-C4B-2 RQData可交易性真实POC单次入口。"""

from __future__ import annotations

import argparse
from datetime import datetime
import getpass
import json
from typing import Any, Callable, Optional, Sequence

from market_calendar import (
    SHANGHAI_TZ,
    calculate_market_status,
    get_calendar_day_kind,
)
from radar.sources.leader_tradability_rqdata_poc import (
    RqdataPocStatus,
    RqdataPocTransportError,
    RqdataTradabilityPocQuery,
    RqdataTradabilityPocReport,
    build_rqdata_http_transport,
    run_rqdata_tradability_poc,
)


RQDATA_LIVE_POC_CLI_CONTRACT_ID = (
    "radar-leader-tradability-rqdata-live-poc-v1"
)


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _evidence(
    *,
    execution_status: str,
    reason: Optional[str],
    calendar: Optional[Any] = None,
    report: Optional[RqdataTradabilityPocReport] = None,
) -> dict:
    return {
        "contractId": RQDATA_LIVE_POC_CLI_CONTRACT_ID,
        "executionStatus": execution_status,
        "reason": reason,
        "calendar": (
            {
                "kind": calendar.kind,
                "sourceUrl": calendar.source_url,
                "checkedAt": calendar.checked_at,
            }
            if calendar is not None
            else None
        ),
        "report": report.to_evidence() if report is not None else None,
    }


def _write_evidence(
    output_writer: Callable[[str], Any],
    **values: Any,
) -> None:
    output_writer(json.dumps(
        _evidence(**values),
        ensure_ascii=False,
        sort_keys=True,
    ))


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    now_provider: Callable[[], datetime] = _now,
    day_kind_resolver: Callable[..., Any] = get_calendar_day_kind,
    token_reader: Callable[[str], str] = getpass.getpass,
    transport_builder: Callable[[Any], Any] = (
        build_rqdata_http_transport
    ),
    poc_runner: Callable[..., Any] = run_rqdata_tradability_poc,
    output_writer: Callable[[str], Any] = print,
) -> int:
    parser = argparse.ArgumentParser(
        description="执行一次不落盘的RQData可交易性字段POC",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="1至8只RQData沪深A股order_book_id",
    )
    parser.add_argument(
        "--confirm-live-poc",
        action="store_true",
        help="确认本次会调用真实RQData HTTP API",
    )
    args = parser.parse_args(argv)

    if not args.confirm_live_poc:
        _write_evidence(
            output_writer,
            execution_status="not_run",
            reason="rqdata_live_confirmation_missing",
        )
        return 2

    try:
        now = now_provider()
    except Exception:
        now = None
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason="rqdata_live_observation_time_unverified",
        )
        return 1
    now = now.astimezone(SHANGHAI_TZ)

    try:
        calendar = day_kind_resolver("cn", now.date())
    except Exception:
        _write_evidence(
            output_writer,
            execution_status="not_run",
            reason="rqdata_live_calendar_failed",
        )
        return 2
    calendar_kind = getattr(calendar, "kind", None)
    if calendar_kind != "full":
        reason_kind = (
            calendar_kind
            if calendar_kind in ("closed", "half", "unknown")
            else "unverified"
        )
        _write_evidence(
            output_writer,
            execution_status="not_run",
            reason=f"rqdata_live_calendar_{reason_kind}",
            calendar=calendar,
        )
        return 2

    market_status = calculate_market_status("cn", now, calendar_kind)
    if market_status.code != "trading":
        _write_evidence(
            output_writer,
            execution_status="not_run",
            reason=f"rqdata_live_market_{market_status.code}",
            calendar=calendar,
        )
        return 2

    query = RqdataTradabilityPocQuery(
        as_of=now,
        trading_date=now.date(),
        expected_order_book_ids=tuple(args.symbols),
        include_dynamic_snapshot=True,
    )
    preflight = run_rqdata_tradability_poc(
        query,
        fetched_at=now,
        transport=None,
    )
    if preflight.status == RqdataPocStatus.BLOCKED:
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason="rqdata_live_query_blocked",
            calendar=calendar,
            report=preflight,
        )
        return 1

    try:
        token = token_reader("RQData Token（输入不回显）: ")
    except Exception:
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason="rqdata_live_token_input_failed",
            calendar=calendar,
        )
        return 1

    try:
        transport = transport_builder(token)
    except RqdataPocTransportError as exc:
        token = None
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason=exc.reason_code,
            calendar=calendar,
        )
        return 1
    except Exception:
        token = None
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason="rqdata_live_transport_build_failed",
            calendar=calendar,
        )
        return 1
    token = None

    try:
        live_report = poc_runner(
            query,
            fetched_at=now,
            transport=transport,
        )
    except RqdataPocTransportError as exc:
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason=exc.reason_code,
            calendar=calendar,
        )
        return 1
    except Exception:
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason="rqdata_live_execution_failed",
            calendar=calendar,
        )
        return 1
    finally:
        transport = None

    if not isinstance(live_report, RqdataTradabilityPocReport):
        _write_evidence(
            output_writer,
            execution_status="blocked",
            reason="rqdata_live_report_contract_unverified",
            calendar=calendar,
        )
        return 1

    execution_status = (
        "not_run"
        if live_report.status == RqdataPocStatus.NOT_RUN
        else "blocked"
        if live_report.status == RqdataPocStatus.BLOCKED
        else "completed"
    )
    _write_evidence(
        output_writer,
        execution_status=execution_status,
        reason=None,
        calendar=calendar,
        report=live_report,
    )
    if live_report.status == RqdataPocStatus.FIELD_CANDIDATE:
        return 0
    if live_report.status == RqdataPocStatus.BLOCKED:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
