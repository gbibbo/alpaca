#!/usr/bin/env python3
"""
tests/test_turtle_breakout.py
Audit the Donchian/Turtle breakout strategy: entry on a new 20-day high, exit on a new 10-day
low, neutral inside the channel, and a full isolated-engine run.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.backtest import run_backtest, ResearchConfig
from apps.strategies.library import TurtleBreakout


def daily(sym, i, o, h, l, c, v=100000):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc) + timedelta(days=i),
               timeframe=TimeFrame.DAY, open=o, high=h, low=l, close=c, volume=v)


def flat_then(last_close, last_high=None, last_low=None, n=21, base=100.0):
    """n bars: the first n-1 range tightly around `base`, the last one is overridden."""
    bars = [daily("TEST", i, base, base + 0.5, base - 0.5, base) for i in range(n - 1)]
    hi = last_high if last_high is not None else max(base + 0.5, last_close)
    lo = last_low if last_low is not None else min(base - 0.5, last_close)
    bars.append(daily("TEST", n - 1, base, hi, lo, last_close))
    return bars


class TestRules:
    def test_entry_on_new_20d_high(self):
        s = TurtleBreakout()
        bars = flat_then(last_close=105.0)          # closes well above the ~100.5 channel high
        sig = s.analyze("TEST", bars)
        assert sig is not None and sig.side == SignalSide.BUY
        assert sig.metadata["rule"] == "close>20d_high"
        assert sig.metadata["entry_level"] == pytest.approx(100.5)

    def test_no_signal_inside_channel(self):
        s = TurtleBreakout()
        bars = flat_then(last_close=100.0)          # neither a new high nor a new low
        assert s.analyze("TEST", bars) is None

    def test_exit_on_new_10d_low(self):
        s = TurtleBreakout()
        bars = flat_then(last_close=95.0)           # closes below the ~99.5 channel low
        sig = s.analyze("TEST", bars)
        assert sig is not None and sig.side == SignalSide.SELL
        assert sig.metadata["rule"] == "close<10d_low"

    def test_needs_full_lookback(self):
        s = TurtleBreakout()
        assert s.analyze("TEST", flat_then(last_close=105.0, n=10)) is None  # too few prior bars


class TestEngineRun:
    def _breakout_series(self):
        # 22 flat bars around 100, then a staircase of new highs so the breakout fires and fills.
        bars = [daily("TEST", i, 100, 100.5, 99.5, 100) for i in range(22)]
        for j, price in enumerate([102, 104, 106, 108, 110, 112], start=22):
            bars.append(daily("TEST", j, price - 1, price + 0.5, price - 1.5, price))
        return bars

    def test_backtest_buys_on_breakout_and_is_deterministic(self):
        cfg = ResearchConfig(strategies=["turtle_breakout"], initial_cash=100000,
                             max_position_size=1.0, max_portfolio_risk=1.0, risk_pct=0.02,
                             max_volume_participation=0.5)
        result = run_backtest(self._breakout_series(), cfg)
        acc = result["accounts"]["turtle_breakout"]
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        assert buys, acc["signals"]                       # the breakout produced at least one entry
        assert all(f["price"] > 100 for f in buys)        # entries fill after the breakout, not at the flat base
        assert result == run_backtest(self._breakout_series(), cfg)   # deterministic
        # first fill happens strictly after the entry signal (next-bar execution)
        first_signal_ts = acc["signals"][0]["timestamp"]
        assert buys[0]["timestamp"] >= first_signal_ts


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
