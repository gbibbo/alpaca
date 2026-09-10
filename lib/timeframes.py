#!/usr/bin/env python3
"""
lib/timeframes.py
Single place for timeframe parsing, bucket alignment and Alpaca mapping.

Canonical sources are 1m and 1d bars from Alpaca; 5m and 1h are derived from 1m
by lib.resampler.BarResampler.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict

from lib.models import TimeFrame

# Minutes covered by one bar of each timeframe
TIMEFRAME_MINUTES: Dict[TimeFrame, int] = {
    TimeFrame.MINUTE: 1,
    TimeFrame.FIVE_MINUTE: 5,
    TimeFrame.HOUR: 60,
    TimeFrame.DAY: 1440,
}

# Accepted spellings (lower-case) -> TimeFrame
_ALIASES = {
    "1m": TimeFrame.MINUTE, "1min": TimeFrame.MINUTE, "minute": TimeFrame.MINUTE, "min": TimeFrame.MINUTE,
    "5m": TimeFrame.FIVE_MINUTE, "5min": TimeFrame.FIVE_MINUTE,
    "1h": TimeFrame.HOUR, "1hour": TimeFrame.HOUR, "60m": TimeFrame.HOUR, "60min": TimeFrame.HOUR, "hour": TimeFrame.HOUR,
    "1d": TimeFrame.DAY, "1day": TimeFrame.DAY, "day": TimeFrame.DAY, "daily": TimeFrame.DAY,
}


def parse_timeframe(value) -> TimeFrame:
    """Parse '1Min', '5Min', '1Hour', '1Day', '1m', '5m', '1h', '1d' (case-insensitive) or a TimeFrame."""
    if isinstance(value, TimeFrame):
        return value
    key = str(value).strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(f"Unsupported timeframe '{value}'. Use one of: 1Min/1m, 5Min/5m, 1Hour/1h, 1Day/1d")


def timeframe_minutes(tf: TimeFrame) -> int:
    return TIMEFRAME_MINUTES[parse_timeframe(tf)]


def timeframe_seconds(tf: TimeFrame) -> int:
    return timeframe_minutes(tf) * 60


def to_alpaca_timeframe(tf):
    """Map our TimeFrame to alpaca-py's TimeFrame (imported lazily so this module has no hard alpaca dependency)."""
    from alpaca.data.timeframe import TimeFrame as ATF, TimeFrameUnit
    tf = parse_timeframe(tf)
    if tf == TimeFrame.MINUTE:
        return ATF.Minute
    if tf == TimeFrame.FIVE_MINUTE:
        return ATF(5, TimeFrameUnit.Minute)
    if tf == TimeFrame.HOUR:
        return ATF.Hour
    return ATF.Day


def bucket_start(ts: datetime, tf: TimeFrame) -> datetime:
    """Floor a timestamp to the start of its bucket for `tf` (clock-aligned, UTC).

    5m buckets start at :00, :05, ...; 1h buckets at the top of the hour; 1d at midnight UTC.
    Note: the first regular-session hour bar (09:30-10:00 ET) is therefore a half-hour bucket,
    which matches how Alpaca aligns its own hourly bars.
    """
    tf = parse_timeframe(tf)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if tf == TimeFrame.DAY:
        return ts.replace(hour=0, minute=0)
    minutes = TIMEFRAME_MINUTES[tf]
    minute_of_day = ts.hour * 60 + ts.minute
    floored = minute_of_day - (minute_of_day % minutes)
    return ts.replace(hour=floored // 60, minute=floored % 60)


def bucket_end(ts: datetime, tf: TimeFrame) -> datetime:
    return bucket_start(ts, tf) + timedelta(minutes=timeframe_minutes(tf))
