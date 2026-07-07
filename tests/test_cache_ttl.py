"""Tests for src.cache TTL policy functions."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from src.cache import ET, market_is_open, market_overview_ttl, seconds_until_next_open


class TestMarketOverviewTtl:
    def test_short_ttl_on_partial_failure(self):
        assert market_overview_ttl(has_partial_failure=True) == 60

    def test_delegates_to_screener_ttl_on_success(self):
        with patch("src.cache.screener_ttl", return_value=12345):
            assert market_overview_ttl(has_partial_failure=False) == 12345

    def test_partial_failure_short_ttl_beats_a_long_screener_ttl(self):
        """Even if screener_ttl() would return hours, a partial failure must
        still get the short retry window — this is the whole point of the fix."""
        with patch("src.cache.screener_ttl", return_value=4 * 3600):
            assert market_overview_ttl(has_partial_failure=True) == 60


class TestMarketIsOpenHolidayAware:
    def test_closed_during_market_hours_on_a_weekday_nyse_holiday(self):
        """New Year's Day 2026 falls on a Thursday — a normal trading weekday
        by the weekday-only check, but the market is actually closed."""
        holiday_at_market_hours = datetime(2026, 1, 1, 10, 0, tzinfo=ET)
        with patch("src.cache.datetime") as mock_dt:
            mock_dt.now.return_value = holiday_at_market_hours
            assert market_is_open() is False

    def test_open_during_market_hours_on_a_regular_weekday(self):
        regular_weekday = datetime(2026, 7, 6, 10, 0, tzinfo=ET)  # Monday
        with patch("src.cache.datetime") as mock_dt:
            mock_dt.now.return_value = regular_weekday
            assert market_is_open() is True


class TestSecondsUntilNextOpenHolidayAware:
    def test_skips_a_weekday_nyse_holiday(self):
        """Dec 24 2026 (Thu) evening: the next weekday is Fri Dec 25 (Christmas,
        a NYSE holiday), so next open must skip to Mon Dec 28, not Dec 25."""
        thursday_evening = datetime(2026, 12, 24, 17, 0, tzinfo=ET)
        expected_open = datetime(2026, 12, 28, 9, 30, tzinfo=ET)
        with patch("src.cache.datetime") as mock_dt:
            mock_dt.now.return_value = thursday_evening
            seconds = seconds_until_next_open()
        assert seconds == int((expected_open - thursday_evening).total_seconds())
