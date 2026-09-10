#!/usr/bin/env python3
"""
tests/test_xsmom_abs_filter.py
xsmom_12_1_abs_filter: same top-decile ranking, but a winner is held only if its absolute 12-1
momentum is positive; dropped slices become cash (gross exposure falls in broad downturns).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from lib.models import Bar, TimeFrame
from lib.backtest import run_backtest, ResearchConfig
from apps.strategies.portfolio_library import CrossSectionalMomentum12_1AbsFilter


def series(sym, closes, start=datetime(2022, 1, 3, tzinfo=timezone.utc)):
    bars, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        bars.append(Bar(symbol=sym, timestamp=d, timeframe=TimeFrame.DAY,
                        open=c, high=c * 1.005, low=c * 0.995, close=c, volume=50_000_000))
        d += timedelta(days=1)
    return bars


def _hist(bars):
    h = {}
    for b in bars:
        h.setdefault(b.symbol, []).append(b)
    return h


class TestFilter:
    def test_all_positive_is_full_decile(self):
        s = CrossSectionalMomentum12_1AbsFilter()
        bars = []
        for i, sym in enumerate(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]):
            bars += series(sym, [100 * (1 + 0.001 * (i + 1)) ** n for n in range(300)])  # all rising
        t = s.target(bars[-1].timestamp, _hist(bars), sorted(_hist(bars)))
        assert t.metadata["k"] == 1 and t.metadata["n_passing_abs"] == 1
        assert t.gross_exposure == Decimal(1)          # the single winner has positive abs momentum

    def test_negative_winner_goes_to_cash(self):
        s = CrossSectionalMomentum12_1AbsFilter()
        # Everything falls; the "winner" is merely the least-bad and has NEGATIVE absolute momentum.
        bars = []
        for i, sym in enumerate(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]):
            bars += series(sym, [100 * (1 - 0.001 * (i + 1)) ** n for n in range(300)])
        t = s.target(bars[-1].timestamp, _hist(bars), sorted(_hist(bars)))
        assert t.metadata["n_passing_abs"] == 0
        assert t.weights == {} and t.gross_exposure == Decimal(0)   # de-risked to cash


class TestEngine:
    def _universe(self):
        # 8 rise, 2 fall; over the last month (the skip window is 21d) some names roll over.
        bars = []
        drifts = {"U1": 0.0020, "U2": 0.0016, "U3": 0.0012, "U4": 0.0008, "U5": 0.0004,
                  "U6": 0.0002, "U7": 0.0001, "U8": 0.00005, "D1": -0.0010, "D2": -0.0020}
        for sym, g in drifts.items():
            bars += series(sym, [100 * (1 + g) ** n for n in range(330)])
        return bars

    def test_abs_filter_never_holds_negative_momentum_name(self):
        cfg = ResearchConfig(strategies=["xsmom_12_1_abs_filter"], initial_cash=1_000_000,
                             max_volume_participation=1.0, commission_bps=0, slippage_bps=0)
        acc = run_backtest(self._universe(), cfg)["accounts"]["xsmom_12_1_abs_filter"]
        assert acc["kind"] == "portfolio" and acc["rebalances"]
        for rb in acc["rebalances"]:
            assert rb["gross_exposure"] <= 1.0 + 1e-9
            assert set(rb["holdings"]) <= {"U1", "U2", "U3", "U4", "U5", "U6", "U7", "U8"}  # never D1/D2

    def test_deterministic(self):
        cfg = ResearchConfig(strategies=["xsmom_12_1_abs_filter"], initial_cash=1_000_000,
                             max_volume_participation=1.0)
        assert run_backtest(self._universe(), cfg) == run_backtest(self._universe(), cfg)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
