#!/usr/bin/env python3
"""
tests/test_xsmom_portfolio.py
Portfolio strategy layer: PortfolioTarget contract, RebalancePlanner atomicity/scaling/turnover,
12-1 cross-sectional momentum selection, the equal-weight benchmark, symbol-order invariance,
and absence of lookahead in the portfolio path of the backtester.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from lib.models import Bar, TimeFrame
from lib.portfolio_strategy import PortfolioTarget, portfolio_names
from lib.rebalance import plan_rebalance, one_way_turnover
from lib.backtest import run_backtest, ResearchConfig
from apps.strategies.portfolio_library import CrossSectionalMomentum12_1


def daily_series(sym, closes, start=datetime(2022, 1, 3, tzinfo=timezone.utc), volume=50_000_000):
    bars, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        bars.append(Bar(symbol=sym, timestamp=d, timeframe=TimeFrame.DAY,
                        open=c, high=c * 1.005, low=c * 0.995, close=c, volume=volume))
        d += timedelta(days=1)
    return bars


def universe(n_days=330, drifts=None):
    """Symbols with distinct constant daily drifts so the 12-1 ranking is known in advance."""
    drifts = drifts or {"WIN1": 0.0020, "WIN2": 0.0015, "MID1": 0.0005, "MID2": 0.0002,
                        "MID3": 0.0000, "LOS1": -0.0005, "LOS2": -0.0010, "LOS3": -0.0015,
                        "LOS4": -0.0020, "LOS5": -0.0025}
    bars = []
    for sym, g in drifts.items():
        bars += daily_series(sym, [100 * (1 + g) ** i for i in range(n_days)])
    return bars


class TestContracts:
    def test_registered(self):
        assert {"xsmom_12_1_long_only", "equal_weight_universe"} <= set(portfolio_names())

    def test_target_rejects_short_and_overweight(self):
        with pytest.raises(ValueError):
            PortfolioTarget(timestamp=datetime.now(timezone.utc), strategy="x", weights={"A": Decimal("-0.1")})
        with pytest.raises(ValueError):
            PortfolioTarget(timestamp=datetime.now(timezone.utc), strategy="x", weights={"A": Decimal("0.6"), "B": Decimal("0.6")})
        t = PortfolioTarget(timestamp=datetime.now(timezone.utc), strategy="x", weights={"A": "0.5"})
        assert t.gross_exposure == Decimal("0.5")


class TestRebalancePlanner:
    def test_deltas_sells_first_and_turnover(self):
        prices = {"A": Decimal(100), "B": Decimal(50), "C": Decimal(20)}
        positions = {"A": Decimal(27), "B": Decimal(0)}       # A=2700 (27%), rest cash
        target = {"A": Decimal("0.20"), "B": Decimal("0.20"), "C": Decimal("0.00")}
        plan = plan_rebalance(Decimal(10000), Decimal(7300), prices, positions, target)
        sides = [t.side for t in plan.trades]
        assert sides == sorted(sides, key=lambda s: 0 if s == "SELL" else 1)   # sells before buys
        sell_a = next(t for t in plan.sells if t.symbol == "A")
        buy_b = next(t for t in plan.buys if t.symbol == "B")
        assert sell_a.quantity == 7 and buy_b.quantity == 40       # 27->20 shares; 2000/50
        assert plan.turnover_one_way == one_way_turnover(target, {"A": Decimal("0.27"), "B": Decimal(0)})
        assert plan.turnover_one_way == pytest.approx(Decimal("0.135"))   # 0.5*(0.07+0.20)

    def test_buys_scaled_proportionally_when_cash_short(self):
        prices = {"A": Decimal(10), "B": Decimal(10)}
        plan = plan_rebalance(Decimal(1000), Decimal(100), prices, {}, {"A": Decimal("0.5"), "B": Decimal("0.5")})
        assert plan.scaled_buys
        qa = next(t.quantity for t in plan.buys if t.symbol == "A")
        qb = next(t.quantity for t in plan.buys if t.symbol == "B")
        assert qa == qb == 5                        # 50 each planned, only 100 cash -> 5 each

    def test_no_trade_when_already_on_target(self):
        prices = {"A": Decimal(100)}
        plan = plan_rebalance(Decimal(10000), Decimal(8000), prices, {"A": Decimal(20)}, {"A": Decimal("0.20")})
        assert plan.trades == [] and plan.turnover_one_way == 0


class TestXsmomSelection:
    def test_top_decile_equal_weight_known_ranking(self):
        s = CrossSectionalMomentum12_1()
        bars = universe()
        hist = {}
        for b in bars:
            hist.setdefault(b.symbol, []).append(b)
        target = s.target(bars[-1].timestamp, hist, sorted(hist))
        # 10 symbols -> top decile = 1 winner: the highest 12-1 drift.
        assert list(target.weights) == ["WIN1"]
        assert target.weights["WIN1"] == Decimal(1)
        assert target.metadata["n_eligible"] == 10 and target.metadata["k"] == 1
        # Scores are 12-1 (skip the last 21 sessions), computed only from provided history.
        closes = [float(b.close) for b in hist["WIN1"]]
        assert target.metadata["scores"]["WIN1"] == pytest.approx(closes[-22] / closes[-253] - 1)

    def test_skips_symbols_without_full_history(self):
        s = CrossSectionalMomentum12_1()
        bars = universe(n_days=330) + daily_series("NEW", [100] * 100)
        hist = {}
        for b in bars:
            hist.setdefault(b.symbol, []).append(b)
        target = s.target(bars[-1].timestamp, hist, sorted(hist))
        assert "NEW" not in target.metadata["scores"]


class TestEngineIntegration:
    def _cfg(self, **kw):
        base = dict(strategies=["xsmom_12_1_long_only", "equal_weight_universe"], initial_cash=1_000_000,
                    max_volume_participation=1.0, commission_bps=0, slippage_bps=0)
        base.update(kw)
        return ResearchConfig(**base)

    def test_runs_rebalances_monthly_and_reports(self):
        r = run_backtest(universe(), self._cfg())
        x = r["accounts"]["xsmom_12_1_long_only"]
        assert x["kind"] == "portfolio"
        assert x["rebalances"], "expected at least one month-end rebalance after warmup"
        assert all(rb["n_holdings"] == 1 and rb["gross_exposure"] == pytest.approx(1.0) for rb in x["rebalances"])
        assert "turnover" in x and x["turnover"]["rebalances"] == len(x["rebalances"])
        assert "decile_returns" in x
        assert "xsmom_12_1_long_only" in r["comparisons"]
        ew = r["accounts"]["equal_weight_universe"]
        assert ew["rebalances"] and ew["rebalances"][0]["n_holdings"] == 10   # 1/N over all eligible
        # Same eligibility window: both accounts start on the same date.
        assert x["rebalances"][0]["timestamp"] == ew["rebalances"][0]["timestamp"]

    def test_winner_is_held_and_no_protective_exits(self):
        x = run_backtest(universe(), self._cfg())["accounts"]["xsmom_12_1_long_only"]
        buys = [f for f in x["fills"] if f["side"] == "BUY"]
        assert buys and all(f["symbol"] == "WIN1" for f in buys)
        assert x["final_portfolio"]["positions"].get("WIN1", 0) > 0

    def test_symbol_order_invariance(self):
        cfg = self._cfg()
        bars = universe()
        a = run_backtest(bars, cfg)
        b = run_backtest(list(reversed(bars)), cfg)
        assert a["result_sha256"] == b["result_sha256"]

    def test_symbol_order_invariance_when_cash_is_scarce(self):
        # Force scaling: tiny volume cap and equal-weight over 10 names with limited cash.
        cfg = self._cfg(strategies=["equal_weight_universe"], initial_cash=5_000, max_volume_participation=0.01)
        bars = universe()
        a = run_backtest(bars, cfg)
        b = run_backtest(list(reversed(bars)), cfg)
        assert a["result_sha256"] == b["result_sha256"]

    def test_no_lookahead(self):
        cfg = self._cfg()
        base = run_backtest(universe(), cfg)["accounts"]["xsmom_12_1_long_only"]["fills"]
        # Change only the LAST 5 sessions of the winner (a crash): earlier fills must be identical.
        bars = universe()
        cutoff = sorted({b.timestamp for b in bars})[-5]
        changed = [b if b.timestamp < cutoff else b.model_copy(update={"open": b.open / 2, "high": b.high / 2,
                                                                       "low": b.low / 2, "close": b.close / 2})
                   for b in bars]
        other = run_backtest(changed, cfg)["accounts"]["xsmom_12_1_long_only"]["fills"]
        cut = cutoff.isoformat()
        assert [f for f in base if f["timestamp"] < cut] == [f for f in other if f["timestamp"] < cut]

    def test_deterministic(self):
        cfg = self._cfg()
        assert run_backtest(universe(), cfg) == run_backtest(universe(), cfg)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
