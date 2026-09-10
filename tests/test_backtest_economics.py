#!/usr/bin/env python3
"""
tests/test_backtest_economics.py
Economic-correctness invariants of the isolated research engine (lib/backtest.py):
session-date contract, year-agnostic session calendar, resting stops under volume caps,
future independence across multiple symbols and symbol ordering, and the added metrics.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.backtest import available_at, session_date_of, run_backtest, ResearchConfig
from lib.strategy_base import Strategy, register

NY = ZoneInfo("America/New_York")


@register
class BuyOnceMinute(Strategy):
    name = "buy_once_minute"
    timeframe = TimeFrame.MINUTE
    lookback_bars = 1
    cooldown_seconds = 10 ** 9  # buy once, never again
    signal_expiry_seconds = 3600

    def analyze(self, symbol, bars):
        return self.make_signal(symbol, SignalSide.BUY, 1, bars)


def daily_bar(sym, d, o, h, l, c, v=100000):
    return Bar(symbol=sym, timestamp=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
               timeframe=TimeFrame.DAY, open=o, high=h, low=l, close=c, volume=v)


class TestSessionContract:
    def test_daily_bar_keeps_its_utc_date(self):
        # A date-only daily bar at midnight UTC must belong to THAT session, not the prior day.
        b = daily_bar(None or "TEST", datetime(2024, 1, 2), 100, 101, 99, 100)
        assert session_date_of(b) == datetime(2024, 1, 2).date()
        at = available_at(b)
        # Jan 2 2024 regular close 16:00 ET == 21:00 UTC (EST)
        assert at == datetime(2024, 1, 2, 16, 0, tzinfo=NY).astimezone(timezone.utc)
        assert at.date() == datetime(2024, 1, 2).date()

    def test_available_at_year_agnostic(self):
        # Works well beyond the old hardcoded 2024-2026 table.
        b = daily_bar("TEST", datetime(2030, 3, 15), 100, 101, 99, 100)
        at = available_at(b)
        assert at.astimezone(NY).hour == 16  # regular close
        # Good Friday 2030 is Apr 19; a bar dated then rolls forward to the next trading day.
        gf = daily_bar("TEST", datetime(2030, 4, 19), 100, 101, 99, 100)
        assert available_at(gf).astimezone(NY).date() > datetime(2030, 4, 19).date()

    def test_early_close_half_day(self):
        b = daily_bar("TEST", datetime(2024, 11, 29), 100, 101, 99, 100)  # Black Friday
        assert available_at(b).astimezone(NY).hour == 13


class TestRestingStop:
    def test_stop_completes_across_volume_capped_bars(self):
        # Buy on bar 1 (fills bar 2 open). Price then collapses through the stop but each bar's
        # volume only lets a slice out; the exit must keep completing on later bars, not vanish.
        start = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
        prices = [(100, 100, 100), (100, 100, 100), (80, 60, 60), (55, 50, 50), (45, 40, 40), (42, 38, 38)]
        bars = []
        for i, (o, h_hi, c) in enumerate(prices):
            low = min(o, c)
            bars.append(Bar(symbol="TEST", timestamp=start + timedelta(minutes=i), timeframe=TimeFrame.MINUTE,
                            open=o, high=max(o, h_hi, c), low=low, close=c, volume=1000))
        cfg = ResearchConfig(strategies=["buy_once_minute"], initial_cash=100000,
                             max_position_size=1.0, max_portfolio_risk=1.0, risk_pct=1.0,
                             stop_loss_pct=0.05, max_volume_participation=0.05, commission_bps=0, slippage_bps=0)
        acc = run_backtest(bars, cfg)["accounts"]["buy_once_minute"]
        sells = [f for f in acc["fills"] if f["side"] == "SELL"]
        assert len(sells) >= 2, acc["fills"]                 # volume forced a multi-bar exit
        assert acc["final_portfolio"]["positions"].get("TEST", 0) == 0  # fully exited
        # A later slice fills at a later bar's open (gap-through), i.e. below the stop level.
        assert any(f["price"] <= 55 for f in sells)


class TestFutureIndependence:
    def _bars(self, last_close):
        start = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
        out = []
        for sym in ("AAA", "BBB"):
            for i in range(6):
                c = 100 if i < 5 else (last_close if sym == "AAA" else 100)
                out.append(Bar(symbol=sym, timestamp=start + timedelta(minutes=i), timeframe=TimeFrame.MINUTE,
                               open=c, high=c + 1, low=c - 1, close=c, volume=100000))
        return out

    def test_future_change_does_not_alter_past_fills(self):
        cfg = ResearchConfig(strategies=["buy_once_minute"], initial_cash=100000,
                             max_position_size=0.5, max_portfolio_risk=1.0)
        base = run_backtest(self._bars(100), cfg)["accounts"]["buy_once_minute"]["fills"]
        changed = run_backtest(self._bars(200), cfg)["accounts"]["buy_once_minute"]["fills"]
        cutoff = (datetime(2024, 1, 2, 15, 5, tzinfo=timezone.utc)).isoformat()
        assert [f for f in base if f["timestamp"] < cutoff] == [f for f in changed if f["timestamp"] < cutoff]

    def test_symbol_order_is_irrelevant(self):
        cfg = ResearchConfig(strategies=["buy_once_minute"], initial_cash=100000)
        forward = run_backtest(self._bars(100), cfg)
        reversed_input = run_backtest(list(reversed(self._bars(100))), cfg)
        assert forward["data_sha256"] == reversed_input["data_sha256"]
        assert forward["result_sha256"] == reversed_input["result_sha256"]


class TestWalkForward:
    def _series(self, n=40):
        start = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
        return [Bar(symbol="TEST", timestamp=start + timedelta(minutes=i), timeframe=TimeFrame.MINUTE,
                    open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=100000) for i in range(n)]

    def test_folds_report_shape(self):
        cfg = ResearchConfig(strategies=["buy_and_hold"], initial_cash=100000, walk_forward_folds=4)
        acc = run_backtest(self._series(), cfg)["accounts"]["buy_and_hold"]
        wf = acc["walk_forward"]
        assert wf is not None
        assert len(wf["windows"]) == 4
        assert wf["aggregate"]["folds"] == 4
        for key in ("mean_return_pct", "stdev_return_pct", "min_return_pct", "fraction_positive"):
            assert key in wf["aggregate"]
        # windows are contiguous and ordered
        tss = [w["from"] for w in wf["windows"]]
        assert tss == sorted(tss)

    def test_single_fold_is_none(self):
        cfg = ResearchConfig(strategies=["buy_and_hold"], initial_cash=100000)  # folds default 1
        acc = run_backtest(self._series(), cfg)["accounts"]["buy_and_hold"]
        assert acc["walk_forward"] is None

    def test_determinism_with_folds(self):
        cfg = ResearchConfig(strategies=["buy_and_hold"], initial_cash=100000, walk_forward_folds=5)
        assert run_backtest(self._series(), cfg) == run_backtest(self._series(), cfg)


class TestMetrics:
    def test_new_metrics_present(self):
        start = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
        bars = [Bar(symbol="TEST", timestamp=start + timedelta(minutes=i), timeframe=TimeFrame.MINUTE,
                    open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=100000) for i in range(6)]
        m = run_backtest(bars, ResearchConfig(strategies=["buy_and_hold"], initial_cash=100000))["accounts"]["buy_and_hold"]["metrics"]
        for key in ("max_drawdown_duration_days", "avg_exposure_pct", "max_exposure_pct"):
            assert key in m
        assert "avg_win" in m["trades"] and "avg_loss" in m["trades"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
