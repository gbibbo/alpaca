#!/usr/bin/env python3
"""
tests/test_intraday_engine.py
Intraday execution contract: entry fills at the NEXT bar open (never the signalling bar), a
time-based exit after hold_bars, a forced session-close flatten (no overnight), per-session state
reset, and no entry on a session's last bar. Uses synthetic 1m RTH bars over two sessions.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.backtest import run_backtest, ResearchConfig
from lib.strategy_base import Strategy, register
from lib.market_calendar import session_bounds

DAY1, DAY2 = date(2024, 1, 3), date(2024, 1, 4)   # winter, 14:30-21:00 UTC


def _rth(d, n=None):
    o, c = session_bounds(d)
    bars, t, px = [], o, 100.0
    while t < c and (n is None or len(bars) < n):
        bars.append(Bar(symbol="SPY", timestamp=t, timeframe=TimeFrame.MINUTE,
                        open=px, high=px + 0.1, low=px - 0.1, close=px + 0.02, volume=100000))
        t += timedelta(minutes=1); px += 0.001
    return bars


@register
class _EnterAt5thMinute(Strategy):
    """BUY once per session, decided on the bar closing 4 min after the open, hold 3 bars."""
    name = "_probe_enter5"
    timeframe = TimeFrame.MINUTE
    intraday = True
    lookback_bars = 1
    max_history = 500
    max_holding_bars = 3

    def analyze(self, symbol, bars):
        o, _ = session_bounds(bars[-1].timestamp.date())
        offset = int((bars[-1].timestamp - o).total_seconds() // 60)
        if offset == 4:
            return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)
        return None


@register
class _AlwaysBuy(Strategy):
    name = "_probe_always"
    timeframe = TimeFrame.MINUTE
    intraday = True
    lookback_bars = 1
    max_history = 500
    max_holding_bars = 3

    def analyze(self, symbol, bars):
        return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)


@register
class _EnterOnceHoldAll(Strategy):
    """BUY once early each session and never time-exit (hold_bars=None), so the ONLY way out is
    the forced session-close flatten. Proves no-overnight and the session_close exit path."""
    name = "_probe_holdall"
    timeframe = TimeFrame.MINUTE
    intraday = True
    lookback_bars = 1
    max_history = 500
    max_holding_bars = None            # no time-based exit

    def analyze(self, symbol, bars):
        o, _ = session_bounds(bars[-1].timestamp.date())
        if int((bars[-1].timestamp - o).total_seconds() // 60) == 4:
            return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)
        return None


def _cfg(name):
    return ResearchConfig(strategies=[name], initial_cash=1_000_000, max_volume_participation=1.0,
                          commission_bps=0, slippage_bps=0, spread_bps=0,
                          stop_loss_pct=None, take_profit_pct=None)


class TestNextOpenAndTimeExit:
    def test_entry_next_open_and_time_exit(self):
        bars = _rth(DAY1)
        acc = run_backtest(bars, _cfg("_probe_enter5"))["accounts"]["_probe_enter5"]
        assert acc["kind"] == "intraday"
        o, _ = session_bounds(DAY1)
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        sells = [f for f in acc["fills"] if f["side"] == "SELL"]
        assert len(buys) == 1 and len(sells) == 1
        # Decision on the bar at open+4min -> entry fills at the NEXT bar open (open+5min).
        assert buys[0]["timestamp"] == (o + timedelta(minutes=5)).isoformat()
        # hold_bars=3 -> exit at open+8min.
        assert sells[0]["timestamp"] == (o + timedelta(minutes=8)).isoformat()
        assert acc["trades"][0]["exit_reason"] == "time"
        assert acc["trades"][0]["holding_seconds"] == 180


class TestNoOvernightAndForcedClose:
    def test_forced_close_each_session_no_overnight(self):
        bars = _rth(DAY1) + _rth(DAY2)
        acc = run_backtest(bars, _cfg("_probe_holdall"))["accounts"]["_probe_holdall"]
        assert acc["final_portfolio"]["positions"] == {}          # flat at the very end
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0
        # Exactly one trade per session, each closed by the forced session-close flatten.
        assert len(acc["trades"]) == 2
        assert all(t["exit_reason"] == "session_close" for t in acc["trades"])
        # The forced exit fills at each session's LAST bar (its close), never after.
        last_ts = {(session_bounds(d)[1] - timedelta(minutes=1)).isoformat() for d in (DAY1, DAY2)}
        assert {t["exit"] for t in acc["trades"]} == last_ts
        # BUY and SELL quantities net to flat.
        assert sum(f["quantity"] for f in acc["fills"] if f["side"] == "BUY") == \
               sum(f["quantity"] for f in acc["fills"] if f["side"] == "SELL")

    def test_no_entry_on_last_bar_and_flat_end(self):
        bars = _rth(DAY1) + _rth(DAY2)
        acc = run_backtest(bars, _cfg("_probe_always"))["accounts"]["_probe_always"]
        assert acc["final_portfolio"]["positions"] == {}          # flat at the very end
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0
        # No BUY ever fills on a session's last bar (entry is blocked there).
        last_ts = {(session_bounds(d)[1] - timedelta(minutes=1)).isoformat() for d in (DAY1, DAY2)}
        assert not any(f["timestamp"] in last_ts for f in acc["fills"] if f["side"] == "BUY")
        assert sum(f["quantity"] for f in acc["fills"] if f["side"] == "BUY") == \
               sum(f["quantity"] for f in acc["fills"] if f["side"] == "SELL")

    def test_session_reset_seen(self):
        bars = _rth(DAY1) + _rth(DAY2)
        acc = run_backtest(bars, _cfg("_probe_always"))["accounts"]["_probe_always"]
        assert acc["metrics"]["intraday"]["sessions"] == 2

    def test_deterministic(self):
        bars = _rth(DAY1) + _rth(DAY2)
        assert run_backtest(bars, _cfg("_probe_always")) == run_backtest(bars, _cfg("_probe_always"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
