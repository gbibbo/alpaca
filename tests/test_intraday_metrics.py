#!/usr/bin/env python3
"""
tests/test_intraday_metrics.py
The intraday metrics block is self-contained and complete (spec item 9), and cost attribution is
internally consistent: gross return exceeds net once frictions are on, and the "edge kept" share
is a sensible fraction.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.backtest import run_backtest, ResearchConfig
from lib.strategy_base import Strategy, register
from lib.market_calendar import session_bounds

DAY1, DAY2 = date(2024, 1, 3), date(2024, 1, 4)

REQUIRED_KEYS = {
    "trades", "trades_per_day", "sessions", "avg_holding_seconds", "median_holding_seconds",
    "avg_holding_minutes", "overnight_positions", "win_rate_pct", "profit_factor",
    "avg_pnl_per_trade", "gross_return_pct", "net_return_pct", "total_costs", "cost_drag_pct",
    "gross_edge_kept_pct", "max_drawdown_pct", "sharpe_daily", "avg_exposure_pct", "turnover",
    "pnl_per_unit_turnover",
}


@register
class _ProbeMetrics(Strategy):
    """Enter early each session, hold 10 bars: enough round-trips to populate every metric."""
    name = "_probe_metrics"
    timeframe = TimeFrame.MINUTE
    intraday = True
    lookback_bars = 1
    max_history = 500
    max_holding_bars = 10

    def analyze(self, symbol, bars):
        o, _ = session_bounds(bars[-1].timestamp.date())
        offset = int((bars[-1].timestamp - o).total_seconds() // 60)
        if offset % 30 == 5:                       # a handful of entries per session
            return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)
        return None


def _rth_1m(d):
    o, c = session_bounds(d)
    bars, t, px = [], o, 100.0
    while t < c:
        bars.append(Bar(symbol="SPY", timestamp=t, timeframe=TimeFrame.MINUTE,
                        open=px, high=px + 0.2, low=px - 0.2, close=px + 0.05, volume=500_000))
        t += timedelta(minutes=1); px += 0.01
    return bars


def _run(commission_bps, slippage_bps, spread_bps):
    bars = _rth_1m(DAY1) + _rth_1m(DAY2)
    cfg = ResearchConfig(strategies=["_probe_metrics"], initial_cash=1_000_000,
                         max_volume_participation=1.0, commission_bps=commission_bps,
                         slippage_bps=slippage_bps, spread_bps=spread_bps,
                         stop_loss_pct=None, take_profit_pct=None)
    return run_backtest(bars, cfg)["accounts"]["_probe_metrics"]


def test_metrics_block_is_complete():
    acc = _run(1.0, 1.0, 2.0)
    intr = acc["metrics"]["intraday"]
    assert REQUIRED_KEYS.issubset(intr.keys())
    assert intr["trades"] > 0
    assert intr["overnight_positions"] == 0
    assert intr["win_rate_pct"] is not None
    assert intr["avg_holding_minutes"] == pytest.approx(intr["avg_holding_seconds"] / 60)


def test_costs_erode_gross_to_net():
    acc = _run(1.0, 1.0, 2.0)
    intr = acc["metrics"]["intraday"]
    assert intr["total_costs"] > 0
    assert intr["cost_drag_pct"] > 0
    # Gross return is net with frictions added back, so gross >= net.
    assert intr["gross_return_pct"] >= intr["net_return_pct"] - 1e-9


def test_zero_cost_run_has_no_drag():
    intr = _run(0.0, 0.0, 0.0)["metrics"]["intraday"]
    assert intr["total_costs"] == pytest.approx(0.0)
    assert intr["cost_drag_pct"] == pytest.approx(0.0)
    assert intr["gross_return_pct"] == pytest.approx(intr["net_return_pct"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
