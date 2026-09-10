#!/usr/bin/env python3
"""
tests/test_smart_technical_equiv.py
The O(len) MACD optimisation (spec block 4) must not change the maths. This pins the optimised
TechnicalIndicators.macd against the original O(len^2) brute-force implementation (kept here as the
oracle) bit-for-bit, and confirms the full smart_technical strategy emits identical signals whether
it uses the optimised or the reference MACD. The optimisation removes the O(history^2) cost that had
forced smart_technical onto a truncated window, so it can now run the same ~3-year sample as the
other strategies without changing its output.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import random
from datetime import datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame
from lib.strategy_base import create_strategies
from apps.strategies import library
from apps.strategies.library import TechnicalIndicators as TI


def _ref_macd(prices, fast=12, slow=26, signal=9):
    """The ORIGINAL O(len^2) MACD, verbatim, used only as the equivalence oracle."""
    if len(prices) < slow + signal:
        return None, None, None
    macd_series = []
    for i in range(slow, len(prices) + 1):
        window = prices[:i]
        macd_series.append(TI.ema(window, fast) - TI.ema(window, slow))
    macd_line = macd_series[-1]
    signal_line = TI.ema(macd_series, signal) if len(macd_series) >= signal else None
    hist = macd_line - signal_line if signal_line is not None else None
    return macd_line, signal_line, hist


def _series(n, seed):
    rng = random.Random(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(round(prices[-1] * (1 + rng.uniform(-0.01, 0.01)), 4))
    return prices


def test_macd_matches_reference_bit_for_bit():
    for seed, n in enumerate((35, 36, 50, 100, 300, 501, 900)):
        prices = _series(n, seed)
        assert TI.macd(prices) == _ref_macd(prices), f"n={n}"


def test_macd_below_minimum_returns_none():
    assert TI.macd(_series(34, 1)) == (None, None, None)   # < slow + signal


def test_smart_technical_signals_identical_with_reference_macd(monkeypatch):
    start = datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc)
    prices = _series(400, 123)
    bars = [Bar(symbol="SPY", timestamp=start + timedelta(minutes=i), timeframe=TimeFrame.MINUTE,
                open=p, high=p + 0.05, low=p - 0.05, close=p, volume=100000)
            for i, p in enumerate(prices)]

    strat = create_strategies(["smart_technical"])[0]
    optimised = [strat.analyze("SPY", bars[:i]) for i in range(60, len(bars))]

    monkeypatch.setattr(library.TechnicalIndicators, "macd", staticmethod(_ref_macd))
    strat_ref = create_strategies(["smart_technical"])[0]
    reference = [strat_ref.analyze("SPY", bars[:i]) for i in range(60, len(bars))]

    def _key(sig):
        return None if sig is None else (sig.side, float(sig.confidence),
                                         round(sig.metadata.get("macd") or 0.0, 10))
    assert [_key(s) for s in optimised] == [_key(s) for s in reference]
    assert any(s is not None for s in optimised)          # the sample actually exercises signals


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
