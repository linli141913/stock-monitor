import hashlib
import json
import unittest
from datetime import date, datetime, timezone


SSE_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"


def _html(year=2026):
    return (
        f"<html><strong>{year}年休市安排</strong><table>"
        "<tr><td>元旦</td><td>1月1日至1月3日休市</td></tr>"
        "<tr><td>国庆</td><td>10月1日至10月7日休市</td></tr>"
        "</table></html>"
    ).encode("utf-8")


def _real_format_html(year, rows):
    return (
        f"<html><strong>{year}年休市安排</strong><table>"
        "<tr><th>节日</th><th>休市安排</th></tr>"
        + "".join(f"<tr><td>{name}</td><td>{text}</td></tr>" for name, text in rows)
        + "</table></html>"
    ).encode("utf-8")


def _envelope(raw, *, year=2026, observed_through="2026-09-04", fetched_at="2026-09-04T15:05:00+08:00"):
    return json.dumps({
        "contractVersion": "radar-formal-shadow-calendar-input-v1",
        "market": "cn",
        "sourceName": "上海证券交易所",
        "sourceUrl": SSE_URL,
        "year": year,
        "fetchedAt": fetched_at,
        "observedThrough": observed_through,
        "sourceDocumentSha256": hashlib.sha256(raw).hexdigest(),
    }, ensure_ascii=False).encode("utf-8")


class FormalShadowCalendarTests(unittest.TestCase):
    def test_same_document_same_cutoff_later_fetch_is_idempotent(self):
        from radar.formal_shadow_calendar import (
            load_official_sse_calendar_evidence,
            merge_calendar_evidence,
        )

        raw = _html()
        first = load_official_sse_calendar_evidence(
            _envelope(
                raw,
                observed_through="2026-09-07",
                fetched_at="2026-09-07T10:00:00+08:00",
            ),
            raw,
        )
        repeated = load_official_sse_calendar_evidence(
            _envelope(
                raw,
                observed_through="2026-09-07",
                fetched_at="2026-09-07T13:00:00+08:00",
            ),
            raw,
        )

        try:
            merged = merge_calendar_evidence((first,), repeated)
        except ValueError as error:
            self.fail(f"identical official calendar was not idempotent: {error}")

        self.assertEqual(merged, (first,))

    def test_official_raw_document_is_hashed_parsed_and_not_self_reported(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        raw = _html()
        evidence = load_official_sse_calendar_evidence(_envelope(raw), raw)

        self.assertEqual(evidence.source_document_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(evidence.closed_days[0], date(2026, 1, 1))
        self.assertEqual(evidence.closed_days[-1], date(2026, 10, 7))
        self.assertEqual(evidence.observed_through, date(2026, 9, 4))
        self.assertFalse(hasattr(evidence, "trading_dates"))

    def test_sha_source_contract_year_and_aware_time_fail_closed(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        raw = _html()
        cases = (
            ({"sourceDocumentSha256": "0" * 64}, "formal_shadow_calendar_document_hash_mismatch"),
            ({"sourceUrl": "https://example.com"}, "formal_shadow_calendar_input_invalid"),
            ({"sourceName": "other"}, "formal_shadow_calendar_input_invalid"),
            ({"contractVersion": "v0"}, "formal_shadow_calendar_input_invalid"),
            ({"year": 2027}, "formal_shadow_calendar_input_invalid"),
            ({"fetchedAt": "2026-09-04T15:05:00"}, "formal_shadow_calendar_input_invalid"),
            ({"observedThrough": "2027-01-01"}, "formal_shadow_calendar_input_invalid"),
        )
        base = json.loads(_envelope(raw))
        for changes, reason in cases:
            with self.subTest(reason=reason):
                payload = {**base, **changes}
                with self.assertRaisesRegex(ValueError, reason):
                    load_official_sse_calendar_evidence(
                        json.dumps(payload).encode(), raw
                    )

    def test_weekend_holiday_unknown_year_and_observed_through_are_explicit(self):
        from radar.formal_shadow_calendar import (
            OfficialSseCalendarProvider,
            load_official_sse_calendar_evidence,
        )

        raw = _html()
        provider = OfficialSseCalendarProvider((
            load_official_sse_calendar_evidence(_envelope(raw), raw),
        ))
        self.assertFalse(provider.is_trading_day(date(2026, 9, 5)))
        self.assertFalse(provider.is_trading_day(date(2026, 10, 1)))
        self.assertTrue(provider.is_trading_day(date(2026, 9, 4)))
        self.assertIsNone(provider.is_trading_day(date(2027, 1, 4)))
        self.assertIsNone(provider.is_trading_day(date(2026, 9, 7)))

    def test_cross_year_provider_uses_each_official_document(self):
        from radar.formal_shadow_calendar import (
            OfficialSseCalendarProvider,
            load_official_sse_calendar_evidence,
        )

        raw_2026 = _html(2026)
        raw_2027 = _html(2027)
        evidence_2026 = load_official_sse_calendar_evidence(
            _envelope(raw_2026, observed_through="2026-12-31", fetched_at="2026-12-31T16:00:00+08:00"),
            raw_2026,
        )
        evidence_2027 = load_official_sse_calendar_evidence(
            _envelope(raw_2027, year=2027, observed_through="2027-01-04", fetched_at="2027-01-04T16:00:00+08:00"),
            raw_2027,
        )
        provider = OfficialSseCalendarProvider((evidence_2026, evidence_2027))
        self.assertTrue(provider.is_trading_day(date(2026, 12, 31)))
        self.assertTrue(provider.is_trading_day(date(2027, 1, 4)))

    def test_real_2024_2025_formats_single_day_explicit_year_and_cross_year(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        raw_2024 = _real_format_html(2024, (
            ("元旦", "2023年12月30日（星期六）至2024年1月1日（星期一）休市。"),
            ("清明节", "4月4日（星期四）至4月6日（星期六）休市。"),
        ))
        raw_2025 = _real_format_html(2025, (
            ("元旦", "1月1日（星期三）休市。"),
            ("春节", "2025年1月28日（星期二）至2月4日（星期二）休市。"),
        ))
        evidence_2024 = load_official_sse_calendar_evidence(
            _envelope(raw_2024, year=2024, observed_through="2024-12-31", fetched_at="2024-12-31T16:00:00+08:00"),
            raw_2024,
        )
        evidence_2025 = load_official_sse_calendar_evidence(
            _envelope(raw_2025, year=2025, observed_through="2025-12-31", fetched_at="2025-12-31T16:00:00+08:00"),
            raw_2025,
        )
        self.assertIn(date(2024, 1, 1), evidence_2024.closed_days)
        self.assertNotIn(date(2023, 12, 31), evidence_2024.closed_days)
        self.assertIn(date(2024, 4, 4), evidence_2024.closed_days)
        self.assertIn(date(2025, 1, 1), evidence_2025.closed_days)
        self.assertIn(date(2025, 2, 4), evidence_2025.closed_days)

    def test_any_unrecognized_official_closure_row_fails_closed(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        raw = _real_format_html(2026, (
            ("元旦", "1月1日休市。"),
            ("异常", "另行通知的日期休市。"),
        ))
        with self.assertRaisesRegex(ValueError, "formal_shadow_calendar_document_invalid"):
            load_official_sse_calendar_evidence(_envelope(raw), raw)

    def test_duplicate_same_year_sections_always_fail_closed(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        first = _real_format_html(2026, (("元旦", "1月1日休市。"),))
        same = _real_format_html(2026, (("元旦", "1月1日休市。"),))
        conflicting = _real_format_html(2026, (("春节", "2月16日至2月23日休市。"),))
        for second in (same, conflicting):
            with self.subTest(second=second):
                raw = first + second
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_shadow_calendar_document_invalid",
                ):
                    load_official_sse_calendar_evidence(_envelope(raw), raw)

    def test_abnormal_or_ambiguous_calendar_intervals_fail_closed(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        cases = (
            "2025年12月31日至2027年1月2日休市。",
            "2025年12月15日至2026年1月20日休市。",
            "2025年1月1日至2025年1月3日休市。",
            "2026年2月2日至2026年1月1日休市。",
        )
        for closure_text in cases:
            with self.subTest(closure_text=closure_text):
                raw = _real_format_html(2026, (("异常", closure_text),))
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_shadow_calendar_document_invalid",
                ):
                    load_official_sse_calendar_evidence(_envelope(raw), raw)

    def test_malicious_non_bytes_and_invalid_json_have_stable_reasons(self):
        from radar.formal_shadow_calendar import load_official_sse_calendar_evidence

        for envelope, raw, reason in (
            ({}, b"x", "formal_shadow_calendar_input_bytes_required"),
            (b"{}", "x", "formal_shadow_calendar_document_bytes_required"),
            (b"{bad", b"x", "formal_shadow_calendar_input_invalid"),
            (b'{"year":2026,"year":2027}', b"x", "formal_shadow_calendar_input_invalid"),
            (b'{"year":NaN}', b"x", "formal_shadow_calendar_input_invalid"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    load_official_sse_calendar_evidence(envelope, raw)


if __name__ == "__main__":
    unittest.main()
