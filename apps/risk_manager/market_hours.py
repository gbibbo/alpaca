#!/usr/bin/env python3
"""
apps/risk_manager/market_hours.py
Market Hours and Calendar Validation
Integrates with Alpaca Clock API for accurate trading hours validation
Includes holiday calendar and early close detection
"""

import logging
from datetime import datetime, time, timedelta
from typing import Dict, Optional, Tuple
from decimal import Decimal
import pytz

logger = logging.getLogger(__name__)


class MarketCalendar:
    """
    Market Calendar with holiday and early close detection
    Uses Alpaca Clock API for real-time market status
    """

    # US Market holidays for 2024-2025 (NYSE/NASDAQ)
    MARKET_HOLIDAYS = {
        # 2024
        "2024-01-01": "New Year's Day",
        "2024-01-15": "Martin Luther King Jr. Day",
        "2024-02-19": "Presidents' Day",
        "2024-03-29": "Good Friday",
        "2024-05-27": "Memorial Day",
        "2024-06-19": "Juneteenth",
        "2024-07-04": "Independence Day",
        "2024-09-02": "Labor Day",
        "2024-11-28": "Thanksgiving Day",
        "2024-12-25": "Christmas Day",

        # 2025
        "2025-01-01": "New Year's Day",
        "2025-01-20": "Martin Luther King Jr. Day",
        "2025-02-17": "Presidents' Day",
        "2025-04-18": "Good Friday",
        "2025-05-26": "Memorial Day",
        "2025-06-19": "Juneteenth",
        "2025-07-04": "Independence Day",
        "2025-09-01": "Labor Day",
        "2025-11-27": "Thanksgiving Day",
        "2025-12-25": "Christmas Day",
    }

    # Early close days (1:00 PM ET close)
    EARLY_CLOSE_DAYS = {
        # Day before Independence Day (if weekday)
        "2024-07-03": "Day before Independence Day",
        "2025-07-03": "Day before Independence Day",

        # Black Friday (day after Thanksgiving)
        "2024-11-29": "Black Friday",
        "2025-11-28": "Black Friday",

        # Christmas Eve (if weekday)
        "2024-12-24": "Christmas Eve",
        "2025-12-24": "Christmas Eve",
    }

    def __init__(self, timezone: str = "US/Eastern"):
        self.timezone = pytz.timezone(timezone)
        self.clock_api = None
        self._clock_cache = {}
        self._cache_expiry = None
        logger.info(f"Market calendar initialized (timezone: {timezone})")

    def set_clock_api(self, clock_api):
        """Set Alpaca Clock API client for real-time validation"""
        self.clock_api = clock_api
        logger.info("Alpaca Clock API connected")

    def is_holiday(self, date: datetime) -> Tuple[bool, Optional[str]]:
        """Check if date is a market holiday"""
        date_key = date.strftime("%Y-%m-%d")

        if date_key in self.MARKET_HOLIDAYS:
            holiday_name = self.MARKET_HOLIDAYS[date_key]
            return True, holiday_name

        return False, None

    def is_early_close(self, date: datetime) -> Tuple[bool, Optional[str]]:
        """Check if date is an early close day (1:00 PM ET)"""
        date_key = date.strftime("%Y-%m-%d")

        if date_key in self.EARLY_CLOSE_DAYS:
            reason = self.EARLY_CLOSE_DAYS[date_key]
            return True, reason

        return False, None

    def get_market_hours(self, date: datetime) -> Tuple[time, time]:
        """
        Get market open and close times for a given date
        Returns (open_time, close_time) as time objects
        """
        # Check for early close
        is_early, _ = self.is_early_close(date)

        if is_early:
            # Early close: 9:30 AM - 1:00 PM ET
            return time(9, 30), time(13, 0)
        else:
            # Normal hours: 9:30 AM - 4:00 PM ET
            return time(9, 30), time(16, 0)

    def get_clock_from_api(self) -> Optional[Dict]:
        """
        Get current market clock from Alpaca API
        Caches result for 60 seconds to avoid excessive API calls
        """
        if not self.clock_api:
            return None

        # Check cache
        now = datetime.utcnow()
        if self._cache_expiry and now < self._cache_expiry:
            return self._clock_cache

        try:
            clock = self.clock_api.get_clock()

            self._clock_cache = {
                "timestamp": clock.timestamp.isoformat(),
                "is_open": clock.is_open,
                "next_open": clock.next_open.isoformat(),
                "next_close": clock.next_close.isoformat(),
            }

            # Cache for 60 seconds
            self._cache_expiry = now + timedelta(seconds=60)

            return self._clock_cache

        except Exception as e:
            logger.error(f"Error fetching clock from Alpaca API: {e}")
            return None

    def is_market_open_now(self) -> Tuple[bool, str]:
        """
        Check if market is currently open
        Uses Alpaca Clock API if available, otherwise falls back to local calculation
        """
        # Try API first
        clock_data = self.get_clock_from_api()
        if clock_data:
            is_open = clock_data["is_open"]
            if is_open:
                return True, "Market is open (Alpaca Clock API)"
            else:
                next_open = datetime.fromisoformat(clock_data["next_open"])
                return False, f"Market closed - Next open: {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')}"

        # Fallback to local calculation
        now = datetime.now(self.timezone)

        # Check if weekend
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False, f"Market closed - Weekend ({now.strftime('%A')})"

        # Check if holiday
        is_holiday, holiday_name = self.is_holiday(now)
        if is_holiday:
            return False, f"Market closed - Holiday: {holiday_name}"

        # Get market hours for today
        open_time, close_time = self.get_market_hours(now)

        current_time = now.time()

        # Check if within market hours
        if current_time < open_time:
            return False, f"Market closed - Pre-market (opens at {open_time.strftime('%H:%M')} ET)"
        elif current_time >= close_time:
            return False, f"Market closed - After-hours (closed at {close_time.strftime('%H:%M')} ET)"

        # Check if early close and close to closing time
        is_early, early_reason = self.is_early_close(now)
        if is_early:
            return True, f"Market open - Early close day ({early_reason}), closes at 1:00 PM ET"

        return True, "Market open - Normal hours"

    def time_until_market_open(self) -> timedelta:
        """Calculate time until next market open"""
        now = datetime.now(self.timezone)

        # Try API first
        clock_data = self.get_clock_from_api()
        if clock_data:
            next_open = datetime.fromisoformat(clock_data["next_open"])
            next_open_et = next_open.astimezone(self.timezone)
            return next_open_et - now

        # Fallback to local calculation
        # If currently within market hours, return 0
        is_open, _ = self.is_market_open_now()
        if is_open:
            return timedelta(0)

        # Find next market open
        search_date = now
        max_days_ahead = 10  # Prevent infinite loop

        for _ in range(max_days_ahead):
            # Move to next day if past market hours
            if search_date.time() >= time(16, 0):
                search_date = (search_date + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
            else:
                search_date = search_date.replace(hour=9, minute=30, second=0, microsecond=0)

            # Skip weekends
            if search_date.weekday() >= 5:
                search_date = search_date + timedelta(days=(7 - search_date.weekday()))
                continue

            # Skip holidays
            is_holiday, _ = self.is_holiday(search_date)
            if is_holiday:
                search_date = search_date + timedelta(days=1)
                continue

            # Found next market open
            return search_date - now

        # Fallback if we couldn't find next open
        logger.warning("Could not determine next market open within 10 days")
        return timedelta(days=1)

    def validate_trading_time(self, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Validate if trading is allowed at given time (or now)
        Returns (is_valid, detailed_reason)
        """
        if dt is None:
            return self.is_market_open_now()

        # Convert to market timezone if needed
        if dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)

        # Check if weekend
        if dt.weekday() >= 5:
            return False, f"Market closed - Weekend ({dt.strftime('%A')})"

        # Check if holiday
        is_holiday, holiday_name = self.is_holiday(dt)
        if is_holiday:
            return False, f"Market closed - Holiday: {holiday_name}"

        # Get market hours for that day
        open_time, close_time = self.get_market_hours(dt)

        check_time = dt.time()

        # Check if within market hours
        if check_time < open_time:
            return False, f"Market closed - Pre-market (opens at {open_time.strftime('%H:%M')} ET)"
        elif check_time >= close_time:
            return False, f"Market closed - After-hours (closed at {close_time.strftime('%H:%M')} ET)"

        return True, "Valid trading time"

    def get_stats(self) -> Dict:
        """Get calendar statistics"""
        is_open, status = self.is_market_open_now()
        time_until_open = self.time_until_market_open() if not is_open else timedelta(0)

        now = datetime.now(self.timezone)
        is_holiday, holiday_name = self.is_holiday(now)
        is_early, early_reason = self.is_early_close(now)

        return {
            "current_time": now.isoformat(),
            "timezone": str(self.timezone),
            "is_open": is_open,
            "status": status,
            "is_holiday": is_holiday,
            "holiday_name": holiday_name,
            "is_early_close": is_early,
            "early_close_reason": early_reason,
            "time_until_open_seconds": int(time_until_open.total_seconds()),
            "has_clock_api": self.clock_api is not None,
            "clock_cache_valid": self._cache_expiry is not None and datetime.utcnow() < self._cache_expiry,
        }


class MarketHoursValidator:
    """
    Enhanced market hours validator with Calendar integration
    Drop-in replacement for the simple validator in risk_manager
    """

    def __init__(self, alpaca_trading_client=None):
        self.calendar = MarketCalendar()

        # Try to connect to Alpaca Clock API if trading client provided
        if alpaca_trading_client:
            try:
                self.calendar.set_clock_api(alpaca_trading_client)
                logger.info("Market hours validator connected to Alpaca Clock API")
            except Exception as e:
                logger.warning(f"Could not connect to Alpaca Clock API: {e}")

        logger.info("Market hours validator initialized with calendar support")

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if market is open at given time (or now)"""
        is_valid, _ = self.calendar.validate_trading_time(dt)
        return is_valid

    def validate_trading_hours(self) -> Tuple[bool, str]:
        """Validate current trading hours with detailed reason"""
        return self.calendar.is_market_open_now()

    def time_until_market_open(self) -> timedelta:
        """Get time until next market open"""
        return self.calendar.time_until_market_open()

    def get_stats(self) -> Dict:
        """Get validator statistics"""
        return self.calendar.get_stats()


# Convenience function for getting a configured validator
_validator_instance = None

def get_market_hours_validator(alpaca_trading_client=None) -> MarketHoursValidator:
    """Get or create market hours validator singleton"""
    global _validator_instance

    if _validator_instance is None:
        _validator_instance = MarketHoursValidator(alpaca_trading_client)

    return _validator_instance
