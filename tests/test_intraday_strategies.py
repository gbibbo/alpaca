#!/usr/bin/env python3
"""
tests/test_intraday_strategies.py
Behavioural tests for the three preregistered intraday strategies, driven through the real
intraday engine (run_backtest -> run_intraday_account) with synthetic, fully deterministic
session-aligned bars. Each test asserts the ex-ante rule fires where and only where it should,
that entries fill at the next bar's open, and that positions never go overnight.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame
from lib.backtest import run_backtest, ResearchConfig
from lib.market_calendar import session_bounds

DAY1, DAY2 = date(2024, 1, 3), date(2024, 1, 4)   # two ordinary winter sessions (14:30-21:00 UTC)


def _bars(d, tf, minutes, closes, highs=None, sym="SPY"):
    """Build session-aligned bars for date d: one bar every `minutes` from the 09:30 ET open."""
    o, _ = session_bounds(d)
    out = []
    for i, c in enumerate(closes):
        t = o + timedelta(minutes=minutes * i)
        op = closes[i - 1] if i else c
        hi = (highs[i] if highs else max(c, op)) + 0.05
        lo = min(c, op) - 0.05
        out.append(Bar(symbol=sym, timestamp=t, timeframe=tf,
                       open=op, high=max(hi, op, c), low=min(lo, op, c), close=c, volume=1_000_000))
    return out


def _cfg(name):
    return ResearchConfig(strategies=[name], initial_cash=1_000_000, max_volume_participation=1.0,
                          commission_bps=0, slippage_bps=0, spread_bps=0,
                          stop_loss_pct=None, take_profit_pct=None)


class TestExtremeReversal1m:
    def _series(self):
        # Two full 390-bar sessions of a tiny alternating wiggle (std > 0) around 100, with a single
        # -2% outlier minute injected mid-session-2. That one bar is the only ~1/1000 event.
        closes1 = [round(100.0 + (0.02 if i % 2 else -0.02), 4) for i in range(390)]
        closes2 = []
        for i in range(390):
            if i == 200:
                p = round(closes2[-1] * 0.98, 4)            # extreme downside minute
            else:
                base = closes2[-1] if i else 100.0
                p = round(base + (0.02 if i % 2 else -0.02), 4)
            closes2.append(p)
        return closes1, closes2

    def test_fires_once_on_the_outlier_and_holds_five_bars(self):
        c1, c2 = self._series()
        bars = _bars(DAY1, TimeFrame.MINUTE, 1, c1) + _bars(DAY2, TimeFrame.MINUTE, 1, c2)
        acc = run_backtest(bars, _cfg("extreme_reversal_1m"))["accounts"]["extreme_reversal_1m"]
        assert acc["kind"] == "intraday"
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        assert len(buys) == 1                                   # only the injected outlier triggers
        o2, _ = session_bounds(DAY2)
        # Decision on bar 200 -> entry fills at bar 201's open.
        assert buys[0]["timestamp"] == (o2 + timedelta(minutes=201)).isoformat()
        assert len(acc["trades"]) == 1
        assert acc["trades"][0]["exit_reason"] == "time"
        assert acc["trades"][0]["holding_seconds"] == 5 * 60    # 5-bar hold
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0

    def test_no_signal_without_an_outlier(self):
        c1, _ = self._series()
        c2 = c1[:]                                              # calm second session, no outlier
        bars = _bars(DAY1, TimeFrame.MINUTE, 1, c1) + _bars(DAY2, TimeFrame.MINUTE, 1, c2)
        acc = run_backtest(bars, _cfg("extreme_reversal_1m"))["accounts"]["extreme_reversal_1m"]
        assert acc["trades"] == []


class TestOpeningRangeBreakout5m:
    def test_breakout_above_or_high_then_hold_to_close(self):
        # 78 five-minute bars. OR (first 6 bars, 09:30-10:00) peaks at high 101.0; after 10:00 the
        # market drifts under 101 until bar 30 closes 102 (> OR high) -> one long, held to close.
        closes, highs = [], []
        for i in range(78):
            if i < 6:
                c = 100.0
                h = 100.0 + i * 0.2            # OR high climbs to 101.0 at i=5
            elif i == 30:
                c = 102.0; h = 102.0           # breakout bar
            else:
                c = 100.3; h = 100.6           # below OR high, no breakout
            closes.append(c); highs.append(h)
        bars = _bars(DAY1, TimeFrame.FIVE_MINUTE, 5, closes, highs)
        acc = run_backtest(bars, _cfg("opening_range_breakout_5m"))["accounts"]["opening_range_breakout_5m"]
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        assert len(buys) == 1                                   # exactly one entry per session
        o, _ = session_bounds(DAY1)
        assert buys[0]["timestamp"] == (o + timedelta(minutes=5 * 31)).isoformat()  # bar 30 -> fill bar 31 open
        assert len(acc["trades"]) == 1
        assert acc["trades"][0]["exit_reason"] == "session_close"
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0

    def test_no_breakout_no_trade(self):
        closes, highs = [], []
        for i in range(78):
            if i < 6:
                c = 100.0; h = 100.0 + i * 0.2      # OR high 101.0
            else:
                c = 100.3; h = 100.6                # never exceeds OR high
            closes.append(c); highs.append(h)
        bars = _bars(DAY1, TimeFrame.FIVE_MINUTE, 5, closes, highs)
        acc = run_backtest(bars, _cfg("opening_range_breakout_5m"))["accounts"]["opening_range_breakout_5m"]
        assert acc["trades"] == []


class TestMarketIntradayMomentum30m:
    def _two_sessions(self, first30_close_day2):
        c1 = [100.0] * 13                            # session 1: flat, last close = 100 (prior close)
        c2 = [100.0] * 13
        c2[0] = first30_close_day2                   # session 2 first-half-hour close
        return _bars(DAY1, TimeFrame.THIRTY_MINUTE, 30, c1) + \
               _bars(DAY2, TimeFrame.THIRTY_MINUTE, 30, c2)

    def test_positive_morning_goes_long_last_half_hour(self):
        bars = self._two_sessions(first30_close_day2=101.0)     # +1% morning return
        acc = run_backtest(bars, _cfg("market_intraday_momentum_30m"))["accounts"]["market_intraday_momentum_30m"]
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        assert len(buys) == 1
        o2, close2 = session_bounds(DAY2)
        # Entry fills at the LAST bar's open (close - 30 min); forced exit at the close.
        assert buys[0]["timestamp"] == (close2 - timedelta(minutes=30)).isoformat()
        assert len(acc["trades"]) == 1
        t = acc["trades"][0]
        assert t["exit_reason"] == "session_close"
        assert t["holding_seconds"] == 30 * 60                  # exactly the final half hour
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0

    def test_negative_morning_no_trade(self):
        bars = self._two_sessions(first30_close_day2=99.0)      # -1% morning return -> long-only skips
        acc = run_backtest(bars, _cfg("market_intraday_momentum_30m"))["accounts"]["market_intraday_momentum_30m"]
        assert acc["trades"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
