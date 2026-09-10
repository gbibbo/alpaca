#!/usr/bin/env python3
"""
tests/test_tsmom.py
Preregistered 12-month time-series momentum (long or cash) and the engine features it needs:
optional protective exits and allocation sizing without a stop (no monthly accumulation).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.backtest import run_backtest, ResearchConfig
from apps.strategies.library import TimeSeriesMomentum12M


def daily_series(closes, sym="TEST", start=datetime(2022, 1, 3, tzinfo=timezone.utc), volume=10_000_000):
    bars, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        bars.append(Bar(symbol=sym, timestamp=d, timeframe=TimeFrame.DAY,
                        open=c, high=c * 1.01, low=c * 0.99, close=c, volume=volume))
        d += timedelta(days=1)
    return bars


class TestRules:
    def test_preregistered_parameters(self):
        s = TimeSeriesMomentum12M()
        assert (s.lookback_sessions, s.threshold, s.cooldown_seconds) == (252, 0.0, 30 * 86400)
        assert s.lookback_bars == 253

    def test_positive_momentum_is_buy(self):
        s = TimeSeriesMomentum12M()
        bars = daily_series([100 + i * 0.1 for i in range(253)])   # steadily rising
        sig = s.analyze("TEST", bars)
        assert sig.side == SignalSide.BUY
        assert sig.metadata["momentum_12m"] == pytest.approx(float(bars[-1].close) / float(bars[-253].close) - 1)

    def test_non_positive_momentum_is_sell(self):
        s = TimeSeriesMomentum12M()
        bars = daily_series([100 - i * 0.1 for i in range(253)])   # steadily falling
        assert s.analyze("TEST", bars).side == SignalSide.SELL
        flat = daily_series([100.0] * 253)                          # exactly zero -> not > 0 -> SELL
        assert s.analyze("TEST", flat).side == SignalSide.SELL

    def test_needs_full_year(self):
        s = TimeSeriesMomentum12M()
        assert s.analyze("TEST", daily_series([100 + i for i in range(200)])) is None


class TestOptionalBrackets:
    def _cfg(self, **kw):
        base = dict(strategies=["tsmom_12m_long_only"], initial_cash=100_000,
                    max_position_size=1.0, max_portfolio_risk=1.0, max_volume_participation=1.0,
                    commission_bps=0, slippage_bps=0)
        base.update(kw)
        return ResearchConfig(**base)

    def _uptrend(self, n=330):
        return daily_series([100 * (1 + 0.001) ** i for i in range(n)])  # ~+39% over the run

    def test_pure_run_has_no_protective_exits_and_rides_trend(self):
        cfg = self._cfg(stop_loss_pct=None, take_profit_pct=None)
        acc = run_backtest(self._uptrend(), cfg)["accounts"]["tsmom_12m_long_only"]
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        sells = [f for f in acc["fills"] if f["side"] == "SELL"]
        assert buys and not sells                     # no take-profit/stop ever fired
        assert acc["final_portfolio"]["positions"]["TEST"] > 0

    def test_take_profit_caps_the_same_trend(self):
        # Ablation: identical data/strategy, but a 6% take-profit exits early in a +39% trend.
        capped = run_backtest(self._uptrend(), self._cfg(stop_loss_pct=0.02, take_profit_pct=0.06))
        sells = [f for f in capped["accounts"]["tsmom_12m_long_only"]["fills"] if f["side"] == "SELL"]
        assert sells                                  # the cap fired

    def test_no_stop_sizes_to_allocation_and_does_not_accumulate(self):
        cfg = self._cfg(stop_loss_pct=None, take_profit_pct=None, max_position_size=0.5)
        acc = run_backtest(self._uptrend(), cfg)["accounts"]["tsmom_12m_long_only"]
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        assert len(buys) >= 1
        # First entry is sized to the 50% allocation cap (not risk/stop-distance).
        first_value = buys[0]["quantity"] * buys[0]["price"]
        assert 0.4 * 100_000 <= first_value <= 0.5 * 100_000
        # Later monthly BUY signals must not keep adding beyond the cap.
        curve_max_exposure = acc["metrics"]["max_exposure_pct"]
        assert curve_max_exposure <= 60             # ~50% cap plus price drift, never 100%

    def test_deterministic(self):
        cfg = self._cfg(stop_loss_pct=None, take_profit_pct=None)
        assert run_backtest(self._uptrend(), cfg) == run_backtest(self._uptrend(), cfg)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
