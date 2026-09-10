#!/usr/bin/env python3
"""
tests/test_intraday_timeframes.py
Intraday timeframe support: 15m/30m parsing and Alpaca mapping; session-aligned bucketing and
resampling (09:30 ET boundary, RTH-only, day change, early close, DST); the last 1h bucket of a
6.5h session is 30 minutes.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame
from lib.timeframes import (parse_timeframe, timeframe_minutes, to_alpaca_timeframe,
                            session_bucket_start, in_regular_session)
from lib.market_calendar import session_bounds
from lib.resampler import BarResampler

NY_WINTER = date(2024, 1, 3)   # EST (UTC-5): 09:30 ET = 14:30 UTC
NY_SUMMER = date(2024, 7, 1)   # EDT (UTC-4): 09:30 ET = 13:30 UTC
EARLY = date(2024, 11, 29)     # Black Friday: 13:00 ET early close


class TestParsing:
    def test_15_30_parse_and_minutes(self):
        assert parse_timeframe("15m") == TimeFrame.FIFTEEN_MINUTE
        assert parse_timeframe("30Min") == TimeFrame.THIRTY_MINUTE
        assert timeframe_minutes(TimeFrame.FIFTEEN_MINUTE) == 15
        assert timeframe_minutes(TimeFrame.THIRTY_MINUTE) == 30

    def test_alpaca_mapping(self):
        assert to_alpaca_timeframe(TimeFrame.FIFTEEN_MINUTE).value == "15Min"
        assert to_alpaca_timeframe(TimeFrame.THIRTY_MINUTE).value == "30Min"


class TestSessionBounds:
    def test_dst_winter_vs_summer(self):
        ow, cw = session_bounds(NY_WINTER)
        assert ow == datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc)
        assert cw == datetime(2024, 1, 3, 21, 0, tzinfo=timezone.utc)
        os_, cs = session_bounds(NY_SUMMER)
        assert os_ == datetime(2024, 7, 1, 13, 30, tzinfo=timezone.utc)
        assert cs == datetime(2024, 7, 1, 20, 0, tzinfo=timezone.utc)

    def test_early_close(self):
        o, c = session_bounds(EARLY)
        assert o == datetime(2024, 11, 29, 14, 30, tzinfo=timezone.utc)
        assert c == datetime(2024, 11, 29, 18, 0, tzinfo=timezone.utc)   # 13:00 ET

    def test_non_trading_day_is_none(self):
        assert session_bounds(date(2024, 1, 1)) is None   # New Year's Day


class TestSessionBucketAndRTH:
    def test_regular_session_membership(self):
        assert in_regular_session(datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc))   # open
        assert in_regular_session(datetime(2024, 1, 3, 20, 59, tzinfo=timezone.utc))   # last minute
        assert not in_regular_session(datetime(2024, 1, 3, 14, 29, tzinfo=timezone.utc))  # pre-market
        assert not in_regular_session(datetime(2024, 1, 3, 21, 0, tzinfo=timezone.utc))   # at close

    def test_buckets_align_to_930(self):
        u = lambda h, m: datetime(2024, 1, 3, h, m, tzinfo=timezone.utc)
        # 5m
        assert session_bucket_start(u(14, 34), TimeFrame.FIVE_MINUTE) == u(14, 30)
        assert session_bucket_start(u(14, 35), TimeFrame.FIVE_MINUTE) == u(14, 35)
        # 30m
        assert session_bucket_start(u(14, 59), TimeFrame.THIRTY_MINUTE) == u(14, 30)
        assert session_bucket_start(u(15, 0), TimeFrame.THIRTY_MINUTE) == u(15, 0)
        # 1h aligns to the session open, not the clock hour
        assert session_bucket_start(u(15, 29), TimeFrame.HOUR) == u(14, 30)
        assert session_bucket_start(u(15, 30), TimeFrame.HOUR) == u(15, 30)
        # pre-market -> None
        assert session_bucket_start(u(14, 0), TimeFrame.FIVE_MINUTE) is None


def _rth_minutes(d):
    o, c = session_bounds(d)
    bars, t, px = [], o, 100.0
    while t < c:
        bars.append(Bar(symbol="SPY", timestamp=t, timeframe=TimeFrame.MINUTE,
                        open=px, high=px + 0.1, low=px - 0.1, close=px + 0.05, volume=1000))
        t += timedelta(minutes=1); px += 0.001
    return bars


class TestSessionResampling:
    def test_two_sessions_alignment_and_last_hour(self):
        rs = BarResampler(["5m", "15m", "30m", "1h"], session_aligned=True)
        day1, day2 = date(2024, 1, 3), date(2024, 1, 4)
        premarket = [Bar(symbol="SPY", timestamp=datetime(2024, 1, 3, 14, 0, tzinfo=timezone.utc),
                         timeframe=TimeFrame.MINUTE, open=99, high=99.1, low=98.9, close=99, volume=10)]
        out = []
        for b in premarket + _rth_minutes(day1) + _rth_minutes(day2):
            out.extend(rs.add(b))
        out.extend(rs.flush())

        by = lambda tf: [b for b in out if b.timeframe == tf]
        # Pre-market never produced a bucket: the earliest 5m bucket is at the 14:30 open, not 14:00.
        assert min(b.timestamp for b in by(TimeFrame.FIVE_MINUTE)) == datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc)
        # Day 1 counts: 390 RTH minutes -> 78 x 5m, 26 x 15m, 13 x 30m, 7 x 1h (6 full + one 30-min).
        d1_5m = [b for b in by(TimeFrame.FIVE_MINUTE) if b.timestamp.date() == day1]
        d1_1h = [b for b in by(TimeFrame.HOUR) if b.timestamp.date() == day1]
        assert len(d1_5m) == 78
        assert len(d1_1h) == 7
        # The last day-1 1h bucket starts 20:30 UTC (15:30 ET) and, closed by the next session, is
        # marked complete despite being only 30 minutes long.
        last = max(d1_1h, key=lambda b: b.timestamp)
        assert last.timestamp == datetime(2024, 1, 3, 20, 30, tzinfo=timezone.utc)
        assert last.is_complete is True
        # Day 2 buckets restart at its own open (no bucket straddles the two sessions).
        assert datetime(2024, 1, 4, 14, 30, tzinfo=timezone.utc) in {b.timestamp for b in by(TimeFrame.FIVE_MINUTE)}

    def test_early_close_last_bucket(self):
        rs = BarResampler(["30m"], session_aligned=True)
        out = []
        for b in _rth_minutes(EARLY):
            out.extend(rs.add(b))
        out.extend(rs.flush())
        # 3.5h session = 210 min -> 7 x 30m buckets, last starts 17:30 UTC (12:30 ET).
        m30 = [b for b in out if b.timeframe == TimeFrame.THIRTY_MINUTE]
        assert len(m30) == 7
        assert max(b.timestamp for b in m30) == datetime(2024, 11, 29, 17, 30, tzinfo=timezone.utc)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
