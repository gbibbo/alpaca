#!/usr/bin/env python3
"""
tests/test_hourly_intraday_ablation.py
The `hourly_trend_intraday` ablation (spec block 3): same signal rule and parameters as
`hourly_trend`, run under the intraday no-overnight contract. Confirms the signal is identical to
the baseline, that positions never go overnight (forced flat at each session close), and that the
new engine signal-driven exit closes a position on an opposing signal at the next bar's open.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.backtest import run_backtest, ResearchConfig
from lib.strategy_base import Strategy, create_strategies, register
from lib.market_calendar import is_trading_day, session_bounds
from lib.session_filter import expected_bar_starts

DAY1 = date(2024, 1, 3)


def _trading_days(start, n):
    days, d = [], start
    while len(days) < n:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _hourly_uptrend(days, sym="SPY"):
    bars, g = [], 0
    for d in days:
        for t in expected_bar_starts(d, TimeFrame.HOUR):
            px = 100.0 + 0.5 * g                     # strictly rising -> BUY regime, never a SELL
            bars.append(Bar(symbol=sym, timestamp=t, timeframe=TimeFrame.HOUR,
                            open=px, high=px + 0.2, low=px - 0.2, close=px + 0.05, volume=1_000_000))
            g += 1
    return bars


def _cfg(name):
    return ResearchConfig(strategies=[name], initial_cash=1_000_000, max_volume_participation=1.0,
                          commission_bps=0, slippage_bps=0, spread_bps=0,
                          stop_loss_pct=None, take_profit_pct=None)


def test_signal_identical_to_baseline():
    # analyze() is inherited unchanged; assert it over many prefixes of a synthetic 1h series.
    closes = [100.0 + (i % 7) * 0.3 + i * 0.05 for i in range(70)]
    bars = [Bar(symbol="SPY", timestamp=session_bounds(DAY1)[0] + timedelta(hours=i),
                timeframe=TimeFrame.HOUR, open=c, high=c + 0.1, low=c - 0.1, close=c, volume=1000)
            for i, c in enumerate(closes)]
    base = create_strategies(["hourly_trend"])[0]
    abl = create_strategies(["hourly_trend_intraday"])[0]
    for i in range(60, len(bars)):
        a = base.analyze("SPY", bars[:i])
        b = abl.analyze("SPY", bars[:i])
        assert (a is None) == (b is None)
        if a is not None:
            assert a.side == b.side and float(a.confidence) == float(b.confidence)


def test_no_overnight_under_intraday_contract():
    days = _trading_days(DAY1, 14)
    bars = _hourly_uptrend(days)
    acc = run_backtest(bars, _cfg("hourly_trend_intraday"))["accounts"]["hourly_trend_intraday"]
    assert acc["kind"] == "intraday"
    assert acc["final_portfolio"]["positions"] == {}
    assert acc["metrics"]["intraday"]["overnight_positions"] == 0
    assert acc["metrics"]["intraday"]["sessions"] == len(days)
    assert acc["metrics"]["intraday"]["trades"] > 0
    # Pure uptrend -> no opposing SELL crossover, so every exit is the forced session close.
    assert all(t["exit_reason"] == "session_close" for t in acc["trades"])


@register
class _BuyThenSell(Strategy):
    """BUY on the 5th bar, SELL on the 9th bar; with signal_driven_exit the SELL must close the
    long at the next bar's open (reason 'signal'), not wait for the session close."""
    name = "_probe_buy_then_sell"
    timeframe = TimeFrame.MINUTE
    intraday = True
    signal_driven_exit = True
    max_holding_bars = None
    lookback_bars = 1
    max_history = 500

    def analyze(self, symbol, bars):
        o, _ = session_bounds(bars[-1].timestamp.date())
        off = int((bars[-1].timestamp - o).total_seconds() // 60)
        if off == 4:
            return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)
        if off == 8:
            return self.make_signal(symbol, SignalSide.SELL, 1.0, bars)
        return None


def test_signal_driven_exit_closes_long_at_next_open():
    o, c = session_bounds(DAY1)
    bars, t, px = [], o, 100.0
    while t < c:
        bars.append(Bar(symbol="SPY", timestamp=t, timeframe=TimeFrame.MINUTE,
                        open=px, high=px + 0.1, low=px - 0.1, close=px + 0.02, volume=100000))
        t += timedelta(minutes=1); px += 0.001
    acc = run_backtest(bars, _cfg("_probe_buy_then_sell"))["accounts"]["_probe_buy_then_sell"]
    assert len(acc["trades"]) == 1
    t0 = acc["trades"][0]
    assert t0["exit_reason"] == "signal"
    # BUY decided at offset 4 -> fill offset 5; SELL decided at offset 8 -> exit fill offset 9 open.
    assert t0["exit"] == (o + timedelta(minutes=9)).isoformat()
    assert acc["metrics"]["intraday"]["overnight_positions"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
