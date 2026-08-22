"""阶段6行业历史主动回填的一键只读入口。"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Callable, Optional, Sequence, TextIO
from zoneinfo import ZoneInfo

import requests

from market_calendar import SSE_CALENDAR_URL, parse_sse_calendar
from radar.contracts import (
    IndustryIdentityStatus,
    IndustryRecordStatus,
    UnitVerificationStatus,
)
from radar.sector_history_automatic_backfill import (
    SectorHistoryAutomaticBackfillRequest,
    run_sector_history_automatic_backfill,
)
from radar.sector_history_store import (
    DEFAULT_SECTOR_HISTORY_STORE_DIR,
    publish_sector_history_evidence,
)
from radar.sources.industry_classification import (
    fetch_industry_classification,
)
from radar.sources.security_master import fetch_security_master
from radar.sources.tencent_quotes import fetch_tencent_quotes_concurrent


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CAPCO_PUBLICATION_PAGE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/20260403/"
    "j_2026040315001700017751997384265508.html"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从官方行业版本和公开5分钟行情立即回填行业历史"
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(DEFAULT_SECTOR_HISTORY_STORE_DIR),
    )
    parser.add_argument("--history-days", type=int, default=40)
    parser.add_argument("--comparable-time")
    parser.add_argument("--confirm-live-backfill", action="store_true")
    return parser


def _print(payload: Any, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    stdout.write("\n")


def _parse_time(value: Optional[str], now: datetime) -> time:
    if value:
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("sector_history_comparable_time_invalid") from exc
        return parsed.replace(tzinfo=None)
    local = now.astimezone(SHANGHAI_TZ).time().replace(tzinfo=None)
    if local < time(9, 35):
        return time(9, 35)
    if time(11, 30) < local < time(13, 5):
        return time(11, 30)
    if local >= time(15, 0):
        return time(15, 0)
    minute = local.minute - local.minute % 5
    return time(local.hour, minute)


def _fetch_sse_calendar_text() -> str:
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            SSE_CALENDAR_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text


def _completed_trade_dates(
    *,
    as_of: datetime,
    count: int,
    calendar_text: str,
) -> tuple[date, ...]:
    if not 21 <= count <= 120:
        raise ValueError("sector_history_days_out_of_range")
    snapshots = {}
    values = []
    current = as_of.astimezone(SHANGHAI_TZ).date() - timedelta(days=1)
    while len(values) < count:
        if current.year not in snapshots:
            snapshots[current.year] = parse_sse_calendar(
                calendar_text,
                current.year,
            )
        if (
            current.weekday() < 5
            and current.isoformat() not in snapshots[current.year].closed_days
        ):
            values.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(values))


def _default_bootstrap(
    *,
    as_of: datetime,
    history_days: int,
    comparable_time: time,
) -> SectorHistoryAutomaticBackfillRequest:
    run_id = f"sector-history-backfill-{as_of:%Y%m%dT%H%M%S}"
    calendar_text = _fetch_sse_calendar_text()
    trade_dates = _completed_trade_dates(
        as_of=as_of,
        count=history_days,
        calendar_text=calendar_text,
    )
    master = fetch_security_master(
        radar_run_id=run_id,
        batch_id=f"{run_id}-security-master",
        as_of=as_of,
    )
    classification = fetch_industry_classification(
        radar_run_id=run_id,
        batch_id=f"{run_id}-industry-classification",
        as_of=as_of,
        publication_page_url=CAPCO_PUBLICATION_PAGE_URL,
        current_security_master=tuple(master.items),
        verify_official_archive=True,
    )
    if classification.release is None:
        raise ValueError("sector_history_classification_unavailable")
    master_by_symbol = {
        item.symbol: item for item in master.items
        if item.exchange in {"sse", "szse"}
        and item.symbol.startswith(("0", "3", "6"))
    }
    memberships = {}
    for record in classification.records:
        if (
            record.record_status != IndustryRecordStatus.ACCEPTED
            or record.identity_status not in {
                IndustryIdentityStatus.EXACT,
                IndustryIdentityStatus.VERIFIED_ALIAS,
            }
            or record.security_identity not in master_by_symbol
        ):
            continue
        memberships.setdefault(record.division_code, []).append(
            record.security_identity
        )
    memberships = {
        code: tuple(sorted(set(symbols)))
        for code, symbols in memberships.items()
        if len(set(symbols)) > 1
    }
    if len(memberships) < 20:
        raise ValueError("sector_history_comparable_industries_below_20")
    symbols = tuple(sorted({
        symbol for members in memberships.values() for symbol in members
    }))
    quotes = fetch_tencent_quotes_concurrent(
        symbols,
        radar_run_id=run_id,
        batch_id=f"{run_id}-share-basis",
        as_of=as_of,
    )
    total_shares = {
        item.symbol: float(item.total_shares_source)
        for item in quotes.items
        if (
            item.market_cap_unit_status == UnitVerificationStatus.VERIFIED
            and item.total_shares_source is not None
            and item.total_shares_source > 0
        )
    }
    if set(total_shares) != set(symbols):
        raise ValueError("sector_history_total_share_basis_incomplete")
    completed_at = datetime.now(SHANGHAI_TZ)
    return SectorHistoryAutomaticBackfillRequest(
        radar_run_id=run_id,
        as_of=completed_at,
        comparable_time=comparable_time,
        expected_trade_dates=trade_dates,
        classification_release=classification.release,
        memberships_by_division=memberships,
        total_shares_by_symbol=total_shares,
        source_batch_ids=(quotes.meta.batch_id,),
        terminal_non_trading_symbols=tuple(
            item.symbol
            for item in quotes.items
            if item.trading_status is not None
        ),
    )


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    now_provider: Callable[[], datetime] = (
        lambda: datetime.now(SHANGHAI_TZ)
    ),
    bootstrap: Callable[..., SectorHistoryAutomaticBackfillRequest] = (
        _default_bootstrap
    ),
    runner: Callable[..., Any] = run_sector_history_automatic_backfill,
    publisher: Callable[..., Any] = publish_sector_history_evidence,
) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except SystemExit:
        raise
    if not arguments.confirm_live_backfill:
        _print({
            "status": "not_run",
            "reasons": ["sector_history_live_confirmation_missing"],
            "gate": {"formalGateReady": False},
        }, stdout)
        return 2
    try:
        if not 21 <= arguments.history_days <= 120:
            raise ValueError
        now = now_provider()
        if (
            now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError
        comparable = _parse_time(arguments.comparable_time, now)
        request = bootstrap(
            as_of=now,
            history_days=arguments.history_days,
            comparable_time=comparable,
        )
        result = runner(
            request,
            artifact_dir=Path(arguments.artifact_dir),
        )
        if result.status == "ready":
            if not isinstance(result.evidence_path, Path):
                raise ValueError("sector_history_evidence_path_missing")
            publisher(
                result.evidence_path,
                store_dir=Path(arguments.artifact_dir),
                published_at=now,
            )
    except Exception:
        _print({
            "status": "error",
            "reasons": ["sector_history_live_bootstrap_failed"],
            "gate": {"formalGateReady": False},
        }, stdout)
        return 3
    _print(result.to_evidence(), stdout)
    return 0 if result.status == "ready" else 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
