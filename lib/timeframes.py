#!/usr/bin/env python3
"""
lib/timeframes.py
Single place for timeframe parsing, bucket alignment and Alpaca mapping.

Canonical sources are 1m and 1d bars from Alpaca; 5m and 1h are derived from 1m
by lib.resampler.BarResampler.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from lib.models import TimeFrame

NY = ZoneInfo("America/New_York")

# Minutes covered by one bar of each timeframe
TIMEFRAME_MINUTES: Dict[TimeFrame, int] = {
    TimeFrame.MINUTE: 1,
    TimeFrame.FIVE_MINUTE: 5,
    TimeFrame.FIFTEEN_MINUTE: 15,
    TimeFrame.THIRTY_MINUTE: 30,
    TimeFrame.HOUR: 60,
    TimeFrame.DAY: 1440,
}

# Intraday timeframes derivable from 1m (excludes 1m itself and 1d)
INTRADAY_DERIVED = (TimeFrame.FIVE_MINUTE, TimeFrame.FIFTEEN_MINUTE, TimeFrame.THIRTY_MINUTE, TimeFrame.HOUR)

# Accepted spellings (lower-case) -> TimeFrame
_ALIASES = {
    "1m": TimeFrame.MINUTE, "1min": TimeFrame.MINUTE, "minute": TimeFrame.MINUTE, "min": TimeFrame.MINUTE,
    "5m": TimeFrame.FIVE_MINUTE, "5min": TimeFrame.FIVE_MINUTE,
    "15m": TimeFrame.FIFTEEN_MINUTE, "15min": TimeFrame.FIFTEEN_MINUTE,
    "30m": TimeFrame.THIRTY_MINUTE, "30min": TimeFrame.THIRTY_MINUTE,
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
    if tf in (TimeFrame.FIVE_MINUTE, TimeFrame.FIFTEEN_MINUTE, TimeFrame.THIRTY_MINUTE):
        return ATF(TIMEFRAME_MINUTES[tf], TimeFrameUnit.Minute)
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


def in_regular_session(ts: datetime) -> bool:
    """True if `ts` falls within the regular trading session (09:30-16:00 ET, 13:00 early close)
    of its own trading day. Pre/post-market and non-trading days are excluded."""
    from lib.market_calendar import session_bounds
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)
    ny_date = ts.astimezone(NY).date()
    b = session_bounds(ny_date)
    return b is not None and b[0] <= ts < b[1]


def session_bucket_start(ts: datetime, tf: TimeFrame) -> Optional[datetime]:
    """Session-ALIGNED intraday bucket start (UTC) for a regular-session bar, or None if `ts`
    is outside the regular session. Buckets are measured from the session open (09:30 ET), so
    09:30 is always a boundary and buckets never straddle two sessions or mix pre-market. The
    final bucket of the day may be shorter than `tf` (e.g. the last 1h bucket of a 6.5h session
    is 30 min, and a 13:00 early close ends mid-bucket)."""
    from lib.market_calendar import session_bounds
    tf = parse_timeframe(tf)
    if tf == TimeFrame.DAY:
        raise ValueError("session_bucket_start is for intraday timeframes; daily bars come from Alpaca")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    ny_date = ts.astimezone(NY).date()
    b = session_bounds(ny_date)
    if b is None:
        return None
    open_utc, close_utc = b
    if ts < open_utc or ts >= close_utc:
        return None
    minutes = TIMEFRAME_MINUTES[tf]
    idx = int((ts - open_utc).total_seconds() // 60) // minutes
    return open_utc + timedelta(minutes=idx * minutes)
