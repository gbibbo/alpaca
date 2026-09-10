#!/usr/bin/env python3
"""
tests/test_intraday_15m_exploratory.py
The EXPLORATORY 15-minute strategy `intraday_momentum_15m` (spec block 5): a straight temporal
translation of the `intraday_momentum_5m` baseline that preserves the same real-time horizons.
This checks the documented 5m->15m period mapping, that the 15m horizon runs end to end through
resampling and the engine, and that the strategy actually fires on 15m data (the empirical point of
item 5 -- demonstrate the system works at 15m, without using the result to claim validation).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import random
from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame
from lib.backtest import run_backtest, ResearchConfig
from lib.strategy_base import create_strategies
from lib.resampler import BarResampler
from lib.market_calendar import session_bounds

DAY1, DAY2 = date(2024, 1, 3), date(2024, 1, 4)


def test_documented_period_mapping():
    s = create_strategies(["intraday_momentum_15m"])[0]
    assert s.timeframe == TimeFrame.FIFTEEN_MINUTE
    # 5m -> 15m: bars * 5/15, floored at 1, keeping wall-clock cooldown/expiry.
    assert s._sma_period == 7          # 20 * 5/15 ~ 7
    assert s._rsi_period == 5          # 14 * 5/15 ~ 5
    assert s._rising_lookback == 1     # ~15 min back
    assert s.lookback_bars == 10       # 30 * 5/15
    assert s.cooldown_seconds == 30 * 60 and s.signal_expiry_seconds == 10 * 60


def _cfg():
    return ResearchConfig(strategies=["intraday_momentum_15m"], initial_cash=1_000_000,
                          max_volume_participation=1.0, commission_bps=0, slippage_bps=0.5,
                          spread_bps=1.0, stop_loss_pct=None, take_profit_pct=None)


def test_runs_end_to_end_via_resampling_from_1m():
    # Build 1m RTH bars for two sessions, resample session-aligned to 15m, run the strategy.
    one_min = []
    for d in (DAY1, DAY2):
        o, c = session_bounds(d)
        t, px = o, 100.0
        while t < c:
            one_min.append(Bar(symbol="SPY", timestamp=t, timeframe=TimeFrame.MINUTE,
                               open=px, high=px + 0.05, low=px - 0.05, close=px + 0.01, volume=100000))
            t += timedelta(minutes=1); px += 0.002
    r = BarResampler(targets=[TimeFrame.FIFTEEN_MINUTE], session_aligned=True)
    bars15 = []
    for b in one_min:
        bars15.extend(r.add(b))
    bars15.extend(r.flush())
    bars15.sort(key=lambda b: b.timestamp)
    assert all(b.timeframe == TimeFrame.FIFTEEN_MINUTE for b in bars15)
    acc = run_backtest(bars15, _cfg())["accounts"]["intraday_momentum_15m"]
    assert "metrics" in acc and acc["warmup_bars"] == 10          # ran without error at 15m


def test_strategy_fires_on_15m_data():
    # A mildly upward, noisy 15m random walk exercises the RSI/SMA momentum rule (some BUYs/SELLs).
    rng = random.Random(11)
    px, bars = 100.0, []
    start = session_bounds(DAY1)[0]
    for i in range(400):
        px = round(px * (1 + rng.uniform(-0.004, 0.005)), 4)     # slight upward drift + noise
        bars.append(Bar(symbol="SPY", timestamp=start + timedelta(minutes=15 * i),
                        timeframe=TimeFrame.FIFTEEN_MINUTE,
                        open=px, high=px + 0.1, low=px - 0.1, close=px, volume=500000))
    acc = run_backtest(bars, _cfg())["accounts"]["intraday_momentum_15m"]
    assert len(acc["signals"]) > 0                                # empirically fires at 15m


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
