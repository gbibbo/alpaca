#!/usr/bin/env python3
"""
tests/test_gao_long_short.py
Faithful Gao et al. (2018) replication (spec block 2): `market_intraday_momentum_30m_long_short`
takes the SIGN of the first-half-hour return -- LONG if positive, SHORT if negative -- and closes
at the session end, using the SAME preregistered signal as the long-only sibling. The short leg is
executed first-class in the research engine with symmetric costs; the long-only variant is left
unchanged and produces no trade on a negative morning.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame
from lib.backtest import run_backtest, ResearchConfig
from lib.market_calendar import session_bounds

DAY1, DAY2 = date(2024, 1, 3), date(2024, 1, 4)


def _bars(d, closes, sym="SPY"):
    o, _ = session_bounds(d)
    out = []
    for i, c in enumerate(closes):
        t = o + timedelta(minutes=30 * i)
        op = closes[i - 1] if i else c
        out.append(Bar(symbol=sym, timestamp=t, timeframe=TimeFrame.THIRTY_MINUTE,
                       open=op, high=max(op, c) + 0.05, low=min(op, c) - 0.05, close=c, volume=1_000_000))
    return out


def _cfg(name, **kw):
    base = dict(strategies=[name], initial_cash=1_000_000, max_volume_participation=1.0,
                commission_bps=0, slippage_bps=0, spread_bps=0,
                stop_loss_pct=None, take_profit_pct=None)
    base.update(kw)
    return ResearchConfig(**base)


def _two_sessions(first30_day2, last_close_day2):
    c1 = [100.0] * 13                                   # prior-session close = 100
    c2 = [100.0] * 13
    c2[0] = first30_day2                                # first-half-hour close (the signal input)
    c2[11] = 100.0                                      # open of the last bar (= closes[11])
    c2[12] = last_close_day2                            # close of the last bar (final half hour)
    return _bars(DAY1, c1) + _bars(DAY2, c2)


class TestShortLeg:
    def test_negative_morning_goes_short_and_covers_at_close(self):
        # -1% morning -> SHORT the last half hour; last bar falls 100 -> 98, so the short profits.
        bars = _two_sessions(first30_day2=99.0, last_close_day2=98.0)
        acc = run_backtest(bars, _cfg("market_intraday_momentum_30m_long_short"))["accounts"]["market_intraday_momentum_30m_long_short"]
        assert acc["kind"] == "intraday"
        # First fill is a SELL (short entry) at the last bar's open; then a BUY to cover at the close.
        sells = [f for f in acc["fills"] if f["side"] == "SELL"]
        buys = [f for f in acc["fills"] if f["side"] == "BUY"]
        assert len(sells) == 1 and len(buys) == 1
        o2, close2 = session_bounds(DAY2)
        assert sells[0]["timestamp"] == (close2 - timedelta(minutes=30)).isoformat()   # short entry 15:30
        assert len(acc["trades"]) == 1
        t = acc["trades"][0]
        assert t["direction"] == "short"
        assert t["exit_reason"] == "session_close"
        assert t["holding_seconds"] == 30 * 60
        assert t["exit"] == close2.isoformat()
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0
        assert acc["metrics"]["intraday"]["short_trades"] == 1
        # Short into a falling last half hour -> positive PnL.
        assert acc["metrics"]["net_return_pct"] > 0

    def test_positive_morning_goes_long(self):
        bars = _two_sessions(first30_day2=101.0, last_close_day2=102.0)   # +1% morning -> long
        acc = run_backtest(bars, _cfg("market_intraday_momentum_30m_long_short"))["accounts"]["market_intraday_momentum_30m_long_short"]
        assert acc["trades"][0]["direction"] == "long"
        assert acc["metrics"]["intraday"]["long_trades"] == 1
        assert acc["metrics"]["intraday"]["short_trades"] == 0


class TestParityWithLongOnly:
    def test_long_only_skips_negative_morning(self):
        bars = _two_sessions(first30_day2=99.0, last_close_day2=98.0)
        acc = run_backtest(bars, _cfg("market_intraday_momentum_30m"))["accounts"]["market_intraday_momentum_30m"]
        assert acc["trades"] == []                       # the whole point: long-only misses this leg

    def test_positive_morning_identical_direction_in_both(self):
        bars = _two_sessions(first30_day2=101.0, last_close_day2=102.0)
        lo = run_backtest(bars, _cfg("market_intraday_momentum_30m"))["accounts"]["market_intraday_momentum_30m"]
        ls = run_backtest(bars, _cfg("market_intraday_momentum_30m_long_short"))["accounts"]["market_intraday_momentum_30m_long_short"]
        assert len(lo["trades"]) == len(ls["trades"]) == 1
        assert lo["trades"][0]["direction"] == ls["trades"][0]["direction"] == "long"


class TestSymmetricCosts:
    def test_costs_apply_to_the_short_leg(self):
        bars = _two_sessions(first30_day2=99.0, last_close_day2=98.0)
        free = run_backtest(bars, _cfg("market_intraday_momentum_30m_long_short", spread_bps=0, slippage_bps=0))["accounts"]["market_intraday_momentum_30m_long_short"]
        costly = run_backtest(bars, _cfg("market_intraday_momentum_30m_long_short", spread_bps=10, slippage_bps=5))["accounts"]["market_intraday_momentum_30m_long_short"]
        assert costly["metrics"]["costs"]["total"] > 0
        # Frictions reduce the short's net just as they would a long's (symmetric adverse fills).
        assert costly["metrics"]["net_return_pct"] < free["metrics"]["net_return_pct"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
