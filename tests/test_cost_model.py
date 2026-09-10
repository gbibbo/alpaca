#!/usr/bin/env python3
"""
tests/test_cost_model.py
Cost model: explicit spread, gross/net/commission/slippage/spread breakdown, and a descriptive
cost-sensitivity sweep. Costs never change the strategy — only what is subtracted.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame
from lib.backtest import run_backtest, cost_sensitivity, ResearchConfig, COST_SCENARIOS


def rising(n=40):
    start = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
    return [Bar(symbol="TEST", timestamp=start + timedelta(minutes=i), timeframe=TimeFrame.MINUTE,
                open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=100000) for i in range(n)]


def _acc(bars, **kw):
    cfg = ResearchConfig(strategies=["buy_and_hold"], initial_cash=100000, max_volume_participation=1.0, **kw)
    return run_backtest(bars, cfg)["accounts"]["buy_and_hold"]["metrics"]


class TestBreakdown:
    def test_frictionless_gross_equals_net(self):
        m = _acc(rising(), slippage_bps=0, commission_bps=0, spread_bps=0)
        assert m["costs"]["total"] == pytest.approx(0)
        assert m["gross_return_pct"] == pytest.approx(m["net_return_pct"])

    def test_components_present_and_sum(self):
        m = _acc(rising(), slippage_bps=5, commission_bps=1, spread_bps=4)
        c = m["costs"]
        assert c["commission"] > 0 and c["slippage"] > 0 and c["spread"] > 0
        assert c["total"] == pytest.approx(c["commission"] + c["slippage"] + c["spread"])
        # gross adds the costs back; net is lower by exactly the drag.
        assert m["gross_return_pct"] - m["net_return_pct"] == pytest.approx(c["cost_drag_pct"])

    def test_spread_reduces_net(self):
        no_spread = _acc(rising(), slippage_bps=5, commission_bps=1, spread_bps=0)
        wide = _acc(rising(), slippage_bps=5, commission_bps=1, spread_bps=20)
        assert wide["costs"]["spread"] > 0 and no_spread["costs"]["spread"] == pytest.approx(0)
        assert wide["net_return_pct"] < no_spread["net_return_pct"]

    def test_default_spread_zero_but_slippage_counted(self):
        m = _acc(rising())  # defaults: slippage 5, commission 1, spread 0
        assert m["costs"]["spread"] == pytest.approx(0)
        assert m["costs"]["slippage"] > 0                # slippage is now attributed, not hidden
        assert m["gross_return_pct"] > m["net_return_pct"]


class TestSensitivity:
    def test_more_cost_never_improves_net(self):
        sweep = cost_sensitivity(rising(), ResearchConfig(strategies=["buy_and_hold"],
                                                          initial_cash=100000, max_volume_participation=1.0))
        nets = [s["accounts"]["buy_and_hold"]["net_return_pct"] for s in sweep]
        labels = [s["scenario"]["label"] for s in sweep]
        assert labels == [x["label"] for x in COST_SCENARIOS]
        assert nets == sorted(nets, reverse=True)        # frictionless best, stress worst
        assert sweep[0]["scenario"]["label"] == "frictionless"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
