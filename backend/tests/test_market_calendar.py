import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import market_calendar
import database


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class MarketCalendarParsingTests(unittest.TestCase):
    def test_fetch_text_honours_utf8_html_when_http_encoding_is_wrong(self):
        class FakeResponse:
            encoding = "ISO-8859-1"
            apparent_encoding = "utf-8"
            content = '<meta charset="utf-8"><strong>2027年休市安排</strong>'.encode("utf-8")

            @property
            def text(self):
                return self.content.decode(self.encoding)

            def raise_for_status(self):
                return None

        with patch.object(market_calendar.requests, "get", return_value=FakeResponse()):
            text = market_calendar._fetch_text(market_calendar.SSE_CALENDAR_URL)

        self.assertIn("2027年休市安排", text)

    def test_sse_calendar_parses_official_closed_ranges(self):
        html = """
        <strong>2027年休市安排</strong>
        <table><tbody>
          <tr><td>元旦：</td><td>1月1日（星期五）至1月3日（星期日）休市，1月4日起照常开市。</td></tr>
          <tr><td>春节：</td><td>2月8日（星期一）至2月12日（星期五）休市，2月15日起照常开市。</td></tr>
        </tbody></table>
        """

        snapshot = market_calendar.parse_sse_calendar(html, 2027)

        self.assertIn("2027-01-01", snapshot.closed_days)
        self.assertIn("2027-01-03", snapshot.closed_days)
        self.assertIn("2027-02-12", snapshot.closed_days)
        self.assertNotIn("2027-01-04", snapshot.closed_days)
        self.assertEqual(snapshot.half_days, frozenset())

    def test_hkex_calendar_parses_closed_and_half_days(self):
        html = r'''
        <script>
        var calendarDataSource = '{"monthly":[
          {"name":"National Day","description":"Hong Kong Market is closed","startdate":"2027-10-01","holidayIcon":"HongKongPublicHolidays","activityIcon":""},
          {"name":"Half-Day Trading Day - Afternoon Session is Closed on New Year\u0027s Eve","description":"","startdate":"2027-12-31","holidayIcon":"","activityIcon":"SecuritiesandDerivatives"},
          {"name":"Northbound - Trading is Closed","description":"","startdate":"2027-10-02","holidayIcon":"","activityIcon":"StockConnect"}
        ]}';
        </script>
        '''

        snapshot = market_calendar.parse_hkex_calendar(html, 2027)

        self.assertEqual(snapshot.closed_days, frozenset({"2027-10-01"}))
        self.assertEqual(snapshot.half_days, frozenset({"2027-12-31"}))

    def test_calendar_fetch_failure_returns_unknown_for_weekday(self):
        def failing_fetcher(_url):
            raise RuntimeError("upstream unavailable")

        result = market_calendar.get_calendar_day_kind(
            "cn",
            datetime(2027, 3, 1, tzinfo=SHANGHAI_TZ).date(),
            fetcher=failing_fetcher,
        )

        self.assertEqual(result.kind, "unknown")
        self.assertIn("暂不可用", result.error)


class MarketSessionTests(unittest.TestCase):
    def assert_status(self, market, timestamp, day_kind, expected):
        result = market_calendar.calculate_market_status(
            market,
            datetime.fromisoformat(timestamp).replace(tzinfo=SHANGHAI_TZ),
            day_kind,
        )
        self.assertEqual(result.code, expected)

    def test_a_share_sessions_include_lunch_break(self):
        self.assert_status("cn", "2027-03-01T10:00:00", "full", "trading")
        self.assert_status("cn", "2027-03-01T12:00:00", "full", "lunch_break")
        self.assert_status("cn", "2027-03-01T14:00:00", "full", "trading")
        self.assert_status("cn", "2027-03-01T15:01:00", "full", "closed")

    def test_hk_sessions_use_hk_close_time(self):
        self.assert_status("hk", "2027-03-01T10:00:00", "full", "trading")
        self.assert_status("hk", "2027-03-01T12:30:00", "full", "lunch_break")
        self.assert_status("hk", "2027-03-01T15:30:00", "full", "trading")
        self.assert_status("hk", "2027-03-01T16:01:00", "full", "closed")

    def test_hk_half_day_closes_after_noon(self):
        self.assert_status("hk", "2027-12-31T11:00:00", "half", "trading")
        self.assert_status("hk", "2027-12-31T12:01:00", "half", "closed")

    def test_holiday_and_unknown_are_not_treated_as_trading(self):
        self.assert_status("cn", "2027-03-01T10:00:00", "closed", "holiday")
        self.assert_status("hk", "2027-03-01T10:00:00", "unknown", "unknown")


class AnalysisHistoryBoundaryTests(unittest.TestCase):
    @staticmethod
    def full_weekday_resolver(_market, day):
        return "closed" if day.weekday() >= 5 else "full"

    def test_a_share_history_cycle_uses_1530_boundary(self):
        bounds = database.get_trading_session_bounds_for_symbol(
            "000725",
            now=datetime(2027, 3, 1, 14, 0, tzinfo=SHANGHAI_TZ),
            day_kind_resolver=self.full_weekday_resolver,
        )

        self.assertEqual(bounds[0], "2027-02-26T15:30:00+08:00")
        self.assertEqual(bounds[1], "2027-03-01T15:30:00+08:00")

    def test_hk_history_cycle_uses_1630_boundary(self):
        bounds = database.get_trading_session_bounds_for_symbol(
            "hk00700",
            now=datetime(2027, 3, 1, 14, 0, tzinfo=SHANGHAI_TZ),
            day_kind_resolver=self.full_weekday_resolver,
        )

        self.assertEqual(bounds[0], "2027-02-26T16:30:00+08:00")
        self.assertEqual(bounds[1], "2027-03-01T16:30:00+08:00")

    def test_hk_half_day_history_cycle_uses_1230_boundary(self):
        def resolver(_market, day):
            if day.isoformat() == "2027-12-31":
                return "half"
            return "closed" if day.weekday() >= 5 else "full"

        bounds = database.get_trading_session_bounds_for_symbol(
            "hk00700",
            now=datetime(2027, 12, 31, 11, 0, tzinfo=SHANGHAI_TZ),
            day_kind_resolver=resolver,
        )

        self.assertEqual(bounds[1], "2027-12-31T12:30:00+08:00")

    def test_unknown_calendar_returns_empty_bounds(self):
        bounds = database.get_trading_session_bounds_for_symbol(
            "000725",
            now=datetime(2027, 3, 1, 10, 0, tzinfo=SHANGHAI_TZ),
            day_kind_resolver=lambda _market, _day: "unknown",
        )

        self.assertEqual(bounds, (None, None))

    def test_historical_target_date_exposes_its_real_trading_cycle(self):
        get_bounds = getattr(
            database,
            "get_trading_session_bounds_for_target_date",
            lambda *_args, **_kwargs: (None, None),
        )

        bounds = get_bounds(
            "000725",
            "2027-03-01",
            day_kind_resolver=self.full_weekday_resolver,
        )

        self.assertEqual(bounds[0], "2027-02-26T15:30:00+08:00")
        self.assertEqual(bounds[1], "2027-03-01T15:30:00+08:00")


if __name__ == "__main__":
    unittest.main()
