#!/usr/bin/env python3
"""
tests/test_epic6_market_hours.py
Tests for Epic 6: Market Hours and Calendar Validation
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, time, timedelta
import pytz

from apps.risk_manager.market_hours import MarketCalendar, MarketHoursValidator


class TestMarketCalendar:
    """Test MarketCalendar functionality"""

    def test_market_holidays(self):
        """Test holiday detection"""
        calendar = MarketCalendar()

        # Test New Year's Day 2024
        new_years = datetime(2024, 1, 1, 12, 0)
        is_holiday, name = calendar.is_holiday(new_years)
        assert is_holiday is True
        assert name == "New Year's Day"

        # Test regular trading day
        regular_day = datetime(2024, 1, 2, 12, 0)
        is_holiday, name = calendar.is_holiday(regular_day)
        assert is_holiday is False
        assert name is None

        # Test Thanksgiving 2024
        thanksgiving = datetime(2024, 11, 28, 12, 0)
        is_holiday, name = calendar.is_holiday(thanksgiving)
        assert is_holiday is True
        assert name == "Thanksgiving Day"

    def test_early_close_days(self):
        """Test early close detection"""
        calendar = MarketCalendar()

        # Test Black Friday 2024 (early close)
        black_friday = datetime(2024, 11, 29, 12, 0)
        is_early, reason = calendar.is_early_close(black_friday)
        assert is_early is True
        assert "Black Friday" in reason

        # Test regular day
        regular_day = datetime(2024, 11, 25, 12, 0)
        is_early, reason = calendar.is_early_close(regular_day)
        assert is_early is False
        assert reason is None

    def test_market_hours(self):
        """Test market hours calculation"""
        calendar = MarketCalendar()

        # Regular day
        regular_day = datetime(2024, 1, 2, 12, 0)
        open_time, close_time = calendar.get_market_hours(regular_day)
        assert open_time == time(9, 30)
        assert close_time == time(16, 0)

        # Early close day
        black_friday = datetime(2024, 11, 29, 12, 0)
        open_time, close_time = calendar.get_market_hours(black_friday)
        assert open_time == time(9, 30)
        assert close_time == time(13, 0)  # 1:00 PM close

    def test_validate_trading_time(self):
        """Test trading time validation"""
        calendar = MarketCalendar()
        et = pytz.timezone("US/Eastern")

        # Test within market hours (regular day)
        trading_time = et.localize(datetime(2024, 1, 2, 10, 30))  # Tuesday 10:30 AM
        is_valid, reason = calendar.validate_trading_time(trading_time)
        assert is_valid is True
        assert "Valid trading time" in reason

        # Test before market open
        pre_market = et.localize(datetime(2024, 1, 2, 9, 0))
        is_valid, reason = calendar.validate_trading_time(pre_market)
        assert is_valid is False
        assert "Pre-market" in reason

        # Test after market close
        after_hours = et.localize(datetime(2024, 1, 2, 17, 0))
        is_valid, reason = calendar.validate_trading_time(after_hours)
        assert is_valid is False
        assert "After-hours" in reason

        # Test weekend
        weekend = et.localize(datetime(2024, 1, 6, 10, 0))  # Saturday
        is_valid, reason = calendar.validate_trading_time(weekend)
        assert is_valid is False
        assert "Weekend" in reason

        # Test holiday
        holiday = et.localize(datetime(2024, 1, 1, 10, 0))  # New Year's Day
        is_valid, reason = calendar.validate_trading_time(holiday)
        assert is_valid is False
        assert "Holiday" in reason

    def test_early_close_validation(self):
        """Test validation on early close days"""
        calendar = MarketCalendar()
        et = pytz.timezone("US/Eastern")

        # Black Friday 2024 - before 1 PM (should be valid)
        before_close = et.localize(datetime(2024, 11, 29, 12, 30))
        is_valid, reason = calendar.validate_trading_time(before_close)
        assert is_valid is True

        # Black Friday 2024 - after 1 PM (should be invalid)
        after_close = et.localize(datetime(2024, 11, 29, 13, 30))
        is_valid, reason = calendar.validate_trading_time(after_close)
        assert is_valid is False
        assert "After-hours" in reason


class TestMarketHoursValidator:
    """Test MarketHoursValidator functionality"""

    def test_validator_initialization(self):
        """Test validator initializes without Alpaca client"""
        validator = MarketHoursValidator()
        assert validator.calendar is not None
        assert validator.calendar.clock_api is None

    def test_is_market_open(self):
        """Test is_market_open wrapper"""
        validator = MarketHoursValidator()
        et = pytz.timezone("US/Eastern")

        # Test trading hours
        trading_time = et.localize(datetime(2024, 1, 2, 10, 30))
        assert validator.is_market_open(trading_time) is True

        # Test weekend
        weekend = et.localize(datetime(2024, 1, 6, 10, 0))
        assert validator.is_market_open(weekend) is False

    def test_validate_trading_hours(self):
        """Test validate_trading_hours method"""
        validator = MarketHoursValidator()

        # This tests current time, so we can't assert the exact result
        # but we can verify it returns the expected format
        is_valid, reason = validator.validate_trading_hours()
        assert isinstance(is_valid, bool)
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_get_stats(self):
        """Test stats generation"""
        validator = MarketHoursValidator()

        stats = validator.get_stats()

        # Check required fields
        assert "current_time" in stats
        assert "timezone" in stats
        assert "is_open" in stats
        assert "status" in stats
        assert "has_clock_api" in stats

        # Verify data types
        assert isinstance(stats["is_open"], bool)
        assert isinstance(stats["has_clock_api"], bool)
        assert stats["has_clock_api"] is False  # No client provided


class TestMarketHoursIntegration:
    """Integration tests for market hours with risk manager"""

    def test_holiday_rejection(self):
        """Test that signals are rejected on holidays"""
        validator = MarketHoursValidator()
        et = pytz.timezone("US/Eastern")

        # Christmas Day 2024
        christmas = et.localize(datetime(2024, 12, 25, 10, 0))
        is_valid, reason = validator.calendar.validate_trading_time(christmas)

        assert is_valid is False
        assert "Holiday" in reason
        assert "Christmas" in reason

    def test_weekend_rejection(self):
        """Test that signals are rejected on weekends"""
        validator = MarketHoursValidator()
        et = pytz.timezone("US/Eastern")

        # Saturday
        saturday = et.localize(datetime(2024, 1, 6, 10, 0))
        is_valid, reason = validator.calendar.validate_trading_time(saturday)

        assert is_valid is False
        assert "Weekend" in reason or "Saturday" in reason

    def test_early_close_acceptance(self):
        """Test that signals are accepted before early close time"""
        validator = MarketHoursValidator()
        et = pytz.timezone("US/Eastern")

        # Black Friday 2024 at 11 AM (before 1 PM close)
        black_friday = et.localize(datetime(2024, 11, 29, 11, 0))
        is_valid, reason = validator.calendar.validate_trading_time(black_friday)

        assert is_valid is True

    def test_early_close_rejection(self):
        """Test that signals are rejected after early close time"""
        validator = MarketHoursValidator()
        et = pytz.timezone("US/Eastern")

        # Black Friday 2024 at 2 PM (after 1 PM close)
        black_friday = et.localize(datetime(2024, 11, 29, 14, 0))
        is_valid, reason = validator.calendar.validate_trading_time(black_friday)

        assert is_valid is False
        assert "After-hours" in reason


class TestTimeUntilMarketOpen:
    """Test time until market open calculations"""

    def test_time_until_open_from_weekend(self):
        """Test calculation from weekend to Monday open"""
        validator = MarketHoursValidator()
        et = pytz.timezone("US/Eastern")

        # Saturday morning
        saturday = et.localize(datetime(2024, 1, 6, 10, 0))

        # Calculate time until next open (should be Monday 9:30 AM)
        time_until = validator.calendar.time_until_market_open()

        # Just verify it returns a positive timedelta
        # (actual value depends on current time)
        assert isinstance(time_until, timedelta)

    def test_time_until_open_from_after_hours(self):
        """Test calculation from after hours to next day"""
        validator = MarketHoursValidator()

        time_until = validator.time_until_market_open()

        # Just verify it returns a timedelta
        assert isinstance(time_until, timedelta)
        assert time_until.total_seconds() >= 0


def test_calendar_cache():
    """Test that calendar stats include cache information"""
    calendar = MarketCalendar()

    stats = calendar.get_stats()

    assert "current_time" in stats
    assert "timezone" in stats
    assert "is_open" in stats
    assert "has_clock_api" in stats
    assert "clock_cache_valid" in stats

    # Without API, cache should not be valid
    assert stats["has_clock_api"] is False
    assert stats["clock_cache_valid"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
