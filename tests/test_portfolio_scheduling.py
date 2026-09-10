#!/usr/bin/env python3
"""
tests/test_portfolio_scheduling.py
Generalised PortfolioStrategy rebalance scheduling (spec item 10). Confirms the "monthly" cadence
is byte-for-byte the original month-boundary rule (so XSMOM 12-1 is unchanged) and that the new
daily / every_n_bars / session_time cadences pick the right decision batches.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, datetime, timedelta, timezone

import pytest

from lib.backtest import _rebalance_indices, NY


def _daily_batches(start: date, n: int):
    """n consecutive daily batches (UTC midday), returned as (batches, sessions)."""
    batches, sessions = [], []
    for k in range(n):
        d = start + timedelta(days=k)
        ts = datetime(d.year, d.month, d.day, 17, 0, tzinfo=timezone.utc)  # ~midday ET
        batches.append((ts, [None]))
        sessions.append(d)
    return batches, sessions


class TestMonthly:
    def test_matches_original_month_boundary_rule(self):
        batches, sessions = _daily_batches(date(2024, 1, 1), 120)   # spans Jan..Apr
        got = _rebalance_indices(batches, sessions, "monthly")
        # Original inline formula, reproduced here as the oracle.
        expected = {i for i in range(len(batches) - 1)
                    if (sessions[i].year, sessions[i].month) != (sessions[i + 1].year, sessions[i + 1].month)}
        assert got == expected
        assert len(got) == 3                                        # Jan/Feb/Mar month-ends

    def test_default_when_unset(self):
        batches, sessions = _daily_batches(date(2024, 1, 1), 40)
        assert _rebalance_indices(batches, sessions, None) == _rebalance_indices(batches, sessions, "monthly")


class TestDaily:
    def test_decides_on_each_session_change(self):
        batches, sessions = _daily_batches(date(2024, 1, 1), 5)     # every batch is its own session
        # Last batch never decides (nothing to execute into); the other four do.
        assert _rebalance_indices(batches, sessions, "daily") == {0, 1, 2, 3}


class TestEveryNBars:
    def test_every_third_batch(self):
        batches, sessions = _daily_batches(date(2024, 1, 1), 10)
        assert _rebalance_indices(batches, sessions, "every_n_bars:3") == {2, 5, 8}

    def test_alias_and_bad_step(self):
        batches, sessions = _daily_batches(date(2024, 1, 1), 6)
        assert _rebalance_indices(batches, sessions, "every:2") == {1, 3}
        with pytest.raises(ValueError):
            _rebalance_indices(batches, sessions, "every_n_bars:0")


class TestSessionTime:
    def test_picks_the_batch_at_the_given_eastern_time(self):
        # One session of 30m bars 09:30..16:00 ET on 2024-01-03 (winter -> 14:30..21:00 UTC).
        open_utc = datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc)
        batches, sessions = [], []
        for k in range(13):
            ts = open_utc + timedelta(minutes=30 * k)
            batches.append((ts, [None]))
            sessions.append(date(2024, 1, 3))
        got = _rebalance_indices(batches, sessions, "session_time:15:30")
        # 15:30 ET == 20:30 UTC == open + 6h == the k=12 bar, but that is the final batch and is
        # excluded; so the 15:00 bar (k=11) is the last decidable one. Verify 15:00 instead.
        got_1500 = _rebalance_indices(batches, sessions, "session_time:15:00")
        assert got == set()                     # 15:30 is the final batch -> excluded
        assert got_1500 == {11}
        assert batches[11][0].astimezone(NY).strftime("%H:%M") == "15:00"


def test_unknown_spec_raises():
    batches, sessions = _daily_batches(date(2024, 1, 1), 5)
    with pytest.raises(ValueError):
        _rebalance_indices(batches, sessions, "fortnightly")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
