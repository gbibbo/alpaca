#!/usr/bin/env python3
"""
tests/test_15m_end_to_end.py
Demonstrates full 15-minute support end to end, even though no 15m-specific production strategy
exists yet (spec item 6): 1m ingestion -> session-aligned resampling to 15m -> backtest ->
intraday execution. The chain is exercised on synthetic 1m RTH bars over two sessions.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.resampler import BarResampler
from lib.backtest import run_backtest, ResearchConfig
from lib.strategy_base import Strategy, register
from lib.market_calendar import session_bounds
from lib.timeframes import session_bucket_start

DAY1, DAY2 = date(2024, 1, 3), date(2024, 1, 4)


def _one_minute_session(d, sym="SPY", base=100.0):
    o, c = session_bounds(d)
    bars, t, px = [], o, base
    while t < c:
        bars.append(Bar(symbol=sym, timestamp=t, timeframe=TimeFrame.MINUTE,
                        open=px, high=px + 0.05, low=px - 0.05, close=px + 0.01, volume=100_000))
        t += timedelta(minutes=1); px += 0.001
    return bars


def _resample_to_15m(one_min_bars):
    r = BarResampler(targets=[TimeFrame.FIFTEEN_MINUTE], session_aligned=True)
    out = []
    for b in one_min_bars:
        out.extend(r.add(b))
    out.extend(r.flush())
    return out, r.get_stats()


@register
class _Probe15m(Strategy):
    """Minimal 15m intraday strategy: BUY on the fourth 15m bar of each session, 2-bar hold.
    Exists only to prove the engine executes a 15m strategy end to end."""
    name = "_probe_15m"
    timeframe = TimeFrame.FIFTEEN_MINUTE
    intraday = True
    lookback_bars = 1
    max_history = 200
    max_holding_bars = 2

    def analyze(self, symbol, bars):
        o, _ = session_bounds(bars[-1].timestamp.date())
        offset = int((bars[-1].timestamp - o).total_seconds() // 60)
        if offset == 45:                          # the 09:30->10:15 boundary, i.e. the 4th 15m bar
            return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)
        return None


def test_1m_resamples_to_session_aligned_15m():
    one_min = _one_minute_session(DAY1)
    bars15, stats = _resample_to_15m(one_min)
    # A 6.5h session = 390 minutes = 26 fifteen-minute buckets, all starting on the 09:30 grid.
    assert len(bars15) == 26
    o, _ = session_bounds(DAY1)
    assert bars15[0].timestamp == o
    for b in bars15:
        assert b.timeframe == TimeFrame.FIFTEEN_MINUTE
        assert session_bucket_start(b.timestamp, TimeFrame.FIFTEEN_MINUTE) == b.timestamp
    # Every bucket but the last was closed by its successor and is complete.
    assert all(b.is_complete for b in bars15[:-1])
    assert stats["bars_in"] == 390


def test_15m_runs_through_intraday_engine():
    one_min = _one_minute_session(DAY1) + _one_minute_session(DAY2)
    bars15, _ = _resample_to_15m(one_min)
    cfg = ResearchConfig(strategies=["_probe_15m"], initial_cash=1_000_000,
                         max_volume_participation=1.0, commission_bps=0, slippage_bps=0,
                         spread_bps=0, stop_loss_pct=None, take_profit_pct=None)
    acc = run_backtest(bars15, cfg)["accounts"]["_probe_15m"]
    assert acc["kind"] == "intraday"
    assert acc["metrics"]["intraday"]["sessions"] == 2
    buys = [f for f in acc["fills"] if f["side"] == "BUY"]
    assert len(buys) == 2                                   # one entry per session
    # Decision on the 4th bar (offset 45) -> entry fills at the 5th bar's open (offset 60).
    for d, f in zip((DAY1, DAY2), sorted(b["timestamp"] for b in buys)):
        o, _ = session_bounds(d)
        assert f == (o + timedelta(minutes=60)).isoformat()
    assert acc["metrics"]["intraday"]["overnight_positions"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
