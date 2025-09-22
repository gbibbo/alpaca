#!/usr/bin/env python3
"""
lib/time_utils.py
Enhanced Time Utilities with US/Eastern timezone and monotonic time support
Addresses ChatGPT's recommendations for robust time handling in trading systems
"""

import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import pytz
import logging

logger = logging.getLogger(__name__)

# US Eastern timezone for market hours
US_EASTERN = pytz.timezone('US/Eastern')
UTC = pytz.UTC

class TimeUtils:
    """Enhanced time utilities for trading systems"""
    
    @staticmethod
    def utc_now() -> datetime:
        """Get current UTC time with timezone awareness"""
        return datetime.now(timezone.utc)
    
    @staticmethod
    def eastern_now() -> datetime:
        """Get current US/Eastern time"""
        return datetime.now(US_EASTERN)
    
    @staticmethod
    def to_eastern(dt: datetime) -> datetime:
        """Convert datetime to US/Eastern timezone"""
        if dt.tzinfo is None:
            # Assume UTC for naive datetime
            dt = UTC.localize(dt)
        return dt.astimezone(US_EASTERN)
    
    @staticmethod
    def to_utc(dt: datetime) -> datetime:
        """Convert datetime to UTC"""
        if dt.tzinfo is None:
            # Assume US/Eastern for naive datetime in trading context
            dt = US_EASTERN.localize(dt)
        return dt.astimezone(UTC)
    
    @staticmethod
    def market_now() -> datetime:
        """Get current time in market timezone (US/Eastern)"""
        return TimeUtils.eastern_now()
    
    @staticmethod
    def is_market_hours(dt: Optional[datetime] = None) -> bool:
        """Check if given time (or now) is during market hours"""
        if dt is None:
            dt = TimeUtils.eastern_now()
        elif dt.tzinfo != US_EASTERN:
            dt = TimeUtils.to_eastern(dt)
        
        # Market hours: 9:30 AM - 4:00 PM ET, Monday-Friday
        if dt.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False
        
        market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = dt.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return market_open <= dt < market_close
    
    @staticmethod
    def next_market_open(dt: Optional[datetime] = None) -> datetime:
        """Get next market open time"""
        if dt is None:
            dt = TimeUtils.eastern_now()
        elif dt.tzinfo != US_EASTERN:
            dt = TimeUtils.to_eastern(dt)
        
        # Start with today's market open
        next_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        
        # If market is already open or closed today, move to next trading day
        if dt.time() >= next_open.time() or dt.weekday() >= 5:
            next_open += timedelta(days=1)
            
            # Skip weekends
            while next_open.weekday() >= 5:
                next_open += timedelta(days=1)
        
        return next_open
    
    @staticmethod
    def market_close_today(dt: Optional[datetime] = None) -> datetime:
        """Get today's market close time"""
        if dt is None:
            dt = TimeUtils.eastern_now()
        elif dt.tzinfo != US_EASTERN:
            dt = TimeUtils.to_eastern(dt)
        
        return dt.replace(hour=16, minute=0, second=0, microsecond=0)


class MonotonicTimer:
    """
    Monotonic time-based utilities for rate limiting and performance measurement
    Uses time.monotonic() which is unaffected by system clock adjustments
    """
    
    def __init__(self):
        self._start_time = time.monotonic()
    
    def elapsed_seconds(self) -> float:
        """Get elapsed seconds since timer creation"""
        return time.monotonic() - self._start_time
    
    def reset(self):
        """Reset the timer"""
        self._start_time = time.monotonic()
    
    @staticmethod
    def current() -> float:
        """Get current monotonic time"""
        return time.monotonic()
    
    @staticmethod
    def since(start_time: float) -> float:
        """Get seconds elapsed since start_time"""
        return time.monotonic() - start_time


class RateLimitWindow:
    """
    Rate limiting window using monotonic time
    Robust against system clock changes
    """
    
    def __init__(self, window_seconds: int = 60, max_requests: int = 10):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.requests = []  # List of (monotonic_time, request_info)
    
    def _cleanup_old_requests(self):
        """Remove requests outside the current window"""
        current_time = MonotonicTimer.current()
        cutoff_time = current_time - self.window_seconds
        
        self.requests = [
            req for req in self.requests 
            if req[0] > cutoff_time
        ]
    
    def can_make_request(self) -> bool:
        """Check if a request can be made without exceeding rate limit"""
        self._cleanup_old_requests()
        return len(self.requests) < self.max_requests
    
    def record_request(self, info: str = "request") -> bool:
        """
        Record a request attempt
        Returns True if request is allowed, False if rate limited
        """
        if not self.can_make_request():
            return False
        
        current_time = MonotonicTimer.current()
        self.requests.append((current_time, info))
        return True
    
    def get_stats(self) -> dict:
        """Get current rate limiting statistics"""
        self._cleanup_old_requests()
        
        return {
            "current_requests": len(self.requests),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "requests_remaining": self.max_requests - len(self.requests),
            "window_utilization": len(self.requests) / self.max_requests
        }
    
    def time_until_next_slot(self) -> float:
        """Get seconds until next request slot is available"""
        if self.can_make_request():
            return 0.0
        
        if not self.requests:
            return 0.0
        
        # Find the oldest request
        oldest_time = min(req[0] for req in self.requests)
        time_until_expire = self.window_seconds - (MonotonicTimer.current() - oldest_time)
        
        return max(0.0, time_until_expire)


class TimingContext:
    """Context manager for measuring execution time"""
    
    def __init__(self, name: str = "operation"):
        self.name = name
        self.timer = MonotonicTimer()
        self.duration = 0.0
    
    def __enter__(self):
        self.timer.reset()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration = self.timer.elapsed_seconds()
        logger.debug(f"⏱️ {self.name} took {self.duration:.3f}s")


# Convenience functions for backward compatibility
def get_utc_now():
    """Get current UTC time - backward compatible"""
    return TimeUtils.utc_now()

def get_market_now():
    """Get current market time (US/Eastern)"""
    return TimeUtils.market_now()

def is_market_open():
    """Check if market is currently open"""
    return TimeUtils.is_market_hours()

# Global rate limiters for common operations
ORDER_RATE_LIMITER = RateLimitWindow(window_seconds=60, max_requests=10)  # 10 orders/minute
SIGNAL_RATE_LIMITER = RateLimitWindow(window_seconds=300, max_requests=50)  # 50 signals/5min
API_RATE_LIMITER = RateLimitWindow(window_seconds=60, max_requests=200)  # 200 API calls/minute


def check_alpaca_rate_limit() -> bool:
    """Check if we can make an Alpaca API call (200/minute limit)"""
    return API_RATE_LIMITER.can_make_request()

def record_alpaca_call(endpoint: str = "unknown") -> bool:
    """Record an Alpaca API call"""
    return API_RATE_LIMITER.record_request(f"alpaca_{endpoint}")

def get_rate_limit_stats() -> dict:
    """Get stats for all rate limiters"""
    return {
        "orders": ORDER_RATE_LIMITER.get_stats(),
        "signals": SIGNAL_RATE_LIMITER.get_stats(),
        "api_calls": API_RATE_LIMITER.get_stats()
    }