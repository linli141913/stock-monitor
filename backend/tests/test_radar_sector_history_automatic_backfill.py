import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from radar.sector_history_automatic_backfill import (
    SectorHistoryAutomaticBackfillRequest,
    _request_identity,
    run_sector_history_automatic_backfill,
)
from radar.sector_history_backfill_collector import (
    SectorHistoryMinuteFrozenBatch,
)
from radar.sector_history_trading_presence import (
    HistoricalTradingPresenceBatch,
)
from tests.test_radar_sector_history_backfill import query


def request():
    value = query()
    return SectorHistoryAutomaticBackfillRequest(
        radar_run_id=value.radar_run_id,
        as_of=value.as_of,
        comparable_time=value.comparable_time,
        expected_trade_dates=value.expected_trade_dates,
        classification_release=value.classification_release,
        memberships_by_division=value.memberships_by_division,
        total_shares_by_symbol=value.total_shares_by_symbol,
        source_batch_ids=value.source_batch_ids,
    ), value.series_by_symbol


class SectorHistoryAutomaticBackfillTests(unittest.TestCase):
    def test_checkpoint_identity_is_stable_across_rolling_date_windows(self):
        value, _ = request()
        shifted_dates = tuple(
            day + timedelta(days=1)
            for day in value.expected_trade_dates
        )
        shifted = replace(
            value,
            expected_trade_dates=shifted_dates,
            comparable_time=value.comparable_time.replace(minute=35),
        )

        self.assertEqual(
            _request_identity(value),
            _request_identity(shifted),
        )

    def test_fresh_collection_uses_completion_time_not_bootstrap_start_time(self):
        value, series = request()
        collected_at = value.as_of + timedelta(seconds=30)
        fresh_series = {
            symbol: replace(item, fetched_at=collected_at)
            for symbol, item in series.items()
        }

        def loader(symbols, dates):
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={
                    symbol: fresh_series[symbol] for symbol in symbols
                },
                source_status="ready",
                failure_count=0,
                requested_count=len(symbols),
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = run_sector_history_automatic_backfill(
                value,
                artifact_dir=Path(directory),
                minute_loader=loader,
                clock=lambda: value.as_of + timedelta(seconds=60),
            )
            payload = json.loads(result.evidence_path.read_text("utf-8"))

        self.assertEqual(result.status, "ready", result.reasons)
        self.assertEqual(
            payload["asOf"],
            (value.as_of + timedelta(seconds=60)).isoformat(),
        )

    def test_daily_presence_resolves_minute_gap_without_waiting_new_day(self):
        value, series = request()
        symbol = next(iter(series))
        value = replace(
            value,
            terminal_non_trading_symbols=(symbol,),
        )
        target_day = value.expected_trade_dates[5]
        incomplete = replace(
            series[symbol],
            bars=tuple(
                bar for bar in series[symbol].bars
                if bar.occurred_at.date() != target_day
            ),
        )

        def minute_loader(symbols, dates):
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={
                    item: incomplete if item == symbol else series[item]
                    for item in symbols
                },
                source_status="ready",
                failure_count=0,
                requested_count=len(symbols),
            )

        presence_calls = []

        def presence_loader(
            targets,
            *,
            expected_trade_dates,
            terminal_non_trading_symbols,
        ):
            presence_calls.append((targets, terminal_non_trading_symbols))
            return HistoricalTradingPresenceBatch(
                verified_trading_dates_by_symbol={symbol: ()},
                verified_non_trading_dates_by_symbol={
                    symbol: (target_day,),
                },
                source_hashes_by_symbol={symbol: "sha256:" + "a" * 64},
                source_status="ready",
                requested_count=1,
                failure_count=0,
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = run_sector_history_automatic_backfill(
                value,
                artifact_dir=Path(directory),
                minute_loader=minute_loader,
                presence_loader=presence_loader,
            )
            payload = json.loads(
                result.evidence_path.read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "ready", result.reasons)
        self.assertEqual(
            presence_calls,
            [({symbol: (target_day,)}, (symbol,))],
        )
        self.assertEqual(
            payload["tradingPresence"]["requestedCount"],
            1,
        )
        self.assertNotIn("sourceHashesBySymbol", payload)

    def test_complete_batch_checkpoints_and_second_run_reuses_every_symbol(self):
        value, series = request()
        calls = []

        def loader(symbols, dates):
            calls.append(symbols)
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={symbol: series[symbol] for symbol in symbols},
                source_status="ready",
                failure_count=0,
                requested_count=len(symbols),
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            first = run_sector_history_automatic_backfill(
                value,
                artifact_dir=Path(directory),
                minute_loader=loader,
            )
            second = run_sector_history_automatic_backfill(
                value,
                artifact_dir=Path(directory),
                minute_loader=loader,
            )

            self.assertEqual(first.status, "ready", first.reasons)
            self.assertEqual(first.fetched_count, 40)
            self.assertEqual(first.reused_count, 0)
            self.assertEqual(second.status, "ready", second.reasons)
            self.assertEqual(second.fetched_count, 0)
            self.assertEqual(second.reused_count, 40)
            self.assertEqual(len(calls), 1)
            self.assertTrue(first.evidence_path.is_file())
            self.assertFalse(first.formal_gate_ready)

    def test_existing_published_checkpoint_root_is_reused_after_identity_upgrade(self):
        value, series = request()
        calls = []

        def loader(symbols, dates):
            calls.append(symbols)
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={symbol: series[symbol] for symbol in symbols},
                source_status="ready",
                failure_count=0,
                requested_count=len(symbols),
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            first = run_sector_history_automatic_backfill(
                value,
                artifact_dir=root,
                minute_loader=loader,
            )
            original_run_root = first.evidence_path.parent
            legacy_root = root / "sector-history-legacy-identity"
            original_run_root.rename(legacy_root)
            moved_evidence = legacy_root / first.evidence_path.name
            (root / "latest.json").write_text(json.dumps({
                "contractId": "radar-sector-history-store-manifest-v1",
                "evidenceRelativePath": str(moved_evidence.relative_to(root)),
            }), encoding="utf-8")

            second = run_sector_history_automatic_backfill(
                value,
                artifact_dir=root,
                minute_loader=loader,
            )

        self.assertEqual(second.status, "ready", second.reasons)
        self.assertEqual(second.reused_count, 40)
        self.assertEqual(second.fetched_count, 0)
        self.assertEqual(len(calls), 1)

    def test_corrupt_checkpoint_refetches_only_that_symbol(self):
        value, series = request()
        calls = []

        def loader(symbols, dates):
            calls.append(symbols)
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={symbol: series[symbol] for symbol in symbols},
                source_status="ready",
                failure_count=0,
                requested_count=len(symbols),
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            first = run_sector_history_automatic_backfill(
                value,
                artifact_dir=root,
                minute_loader=loader,
            )
            checkpoint = next(root.rglob("series-*.json"))
            checkpoint.write_text("not-json", encoding="utf-8")

            second = run_sector_history_automatic_backfill(
                value,
                artifact_dir=root,
                minute_loader=loader,
            )

            self.assertEqual(first.status, "ready")
            self.assertEqual(second.status, "ready")
            self.assertEqual(second.fetched_count, 1)
            self.assertEqual(second.reused_count, 39)
            self.assertEqual(len(calls[-1]), 1)

    def test_checkpoint_missing_latest_completed_day_refetches_only_that_symbol(self):
        value, series = request()
        calls = []

        def loader(symbols, dates):
            calls.append(symbols)
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={symbol: series[symbol] for symbol in symbols},
                source_status="ready",
                failure_count=0,
                requested_count=len(symbols),
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            first = run_sector_history_automatic_backfill(
                value,
                artifact_dir=root,
                minute_loader=loader,
            )
            checkpoint = next(root.rglob("series-*.json"))
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            latest = value.expected_trade_dates[-1].isoformat()
            payload["bars"] = [
                row for row in payload["bars"]
                if not row[0].startswith(latest)
            ]
            checkpoint.write_text(json.dumps(payload), encoding="utf-8")

            second = run_sector_history_automatic_backfill(
                value,
                artifact_dir=root,
                minute_loader=loader,
            )

        self.assertEqual(first.status, "ready")
        self.assertEqual(second.status, "ready", second.reasons)
        self.assertEqual(second.fetched_count, 1)
        self.assertEqual(second.reused_count, 39)
        self.assertEqual(len(calls[-1]), 1)

    def test_failed_source_keeps_success_checkpoints_and_never_emits_gate_packet(self):
        value, series = request()
        failed_symbol = next(iter(series))

        def loader(symbols, dates):
            kept = tuple(symbol for symbol in symbols if symbol != failed_symbol)
            return SectorHistoryMinuteFrozenBatch(
                expected_trade_dates=dates,
                series_by_symbol={symbol: series[symbol] for symbol in kept},
                source_status="source_failed",
                failure_count=1,
                requested_count=len(symbols),
            )

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            result = run_sector_history_automatic_backfill(
                value,
                artifact_dir=Path(directory),
                minute_loader=loader,
            )
            payload = json.loads(result.evidence_path.read_text("utf-8"))

            self.assertEqual(result.status, "partial")
            self.assertEqual(result.fetched_count, 39)
            self.assertEqual(result.failure_count, 1)
            self.assertFalse(payload["gate"]["formalGateReady"])
            self.assertNotIn("seriesBySymbol", payload)


if __name__ == "__main__":
    unittest.main()
