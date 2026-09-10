#!/usr/bin/env python3
"""
tests/test_session_completeness.py
Session-completeness detection (spec block 1). The session close is derived from the market
calendar, never inferred from the last bar present, so partial pulls (mid-morning start, pre-close
end) are recognised and discarded. Covers a normal session, an early close, a partial start, a
partial end, internal data gaps, the common-across-symbols set, and the end-to-end engine drop
(a partial session must not be traded and must never leak overnight).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

import pytest

from lib.models import Bar, TimeFrame, SignalSide
from lib.market_calendar import session_bounds
from lib.strategy_base import Strategy, register
from lib.backtest import run_backtest, ResearchConfig
from lib.session_filter import (expected_bar_starts, expected_last_bar_start,
                                classify_sessions, complete_session_dates, filter_complete_sessions)

NORMAL = date(2024, 1, 3)     # ordinary winter session 09:30-16:00 ET (14:30-21:00 UTC)
EARLY = date(2024, 11, 29)    # Black Friday early close 13:00 ET (14:30-18:00 UTC)


def _minutes(d, sym="SPY", first_offset=0, drop_last=0, skip=()):
    """Full 1m RTH session on `d`, optionally starting `first_offset` minutes late, dropping the
    last `drop_last` minutes, or skipping interior minute offsets in `skip`."""
    o, c = session_bounds(d)
    bars, t, px = [], o, 100.0
    idx = 0
    while t < c:
        off = int((t - o).total_seconds() // 60)
        keep = off >= first_offset and off not in skip
        total = int((c - o).total_seconds() // 60)
        if off >= total - drop_last:
            keep = False
        if keep:
            bars.append(Bar(symbol=sym, timestamp=t, timeframe=TimeFrame.MINUTE,
                            open=px, high=px + 0.1, low=px - 0.1, close=px + 0.02, volume=100000))
        t += timedelta(minutes=1); px += 0.001
    return bars


class TestExpectedGrid:
    def test_normal_grid_1m_30m(self):
        assert len(expected_bar_starts(NORMAL, TimeFrame.MINUTE)) == 390
        o, c = session_bounds(NORMAL)
        assert expected_last_bar_start(NORMAL, TimeFrame.MINUTE) == c - timedelta(minutes=1)
        m30 = expected_bar_starts(NORMAL, TimeFrame.THIRTY_MINUTE)
        assert len(m30) == 13 and m30[-1] == c - timedelta(minutes=30)          # 15:30 ET
        # 1h: 7 buckets, the last one (15:30) short by design
        assert expected_last_bar_start(NORMAL, TimeFrame.HOUR) == c - timedelta(minutes=30)

    def test_early_close_grid(self):
        assert len(expected_bar_starts(EARLY, TimeFrame.MINUTE)) == 210            # 3.5h
        assert len(expected_bar_starts(EARLY, TimeFrame.THIRTY_MINUTE)) == 7
        o, c = session_bounds(EARLY)
        assert expected_last_bar_start(EARLY, TimeFrame.THIRTY_MINUTE) == c - timedelta(minutes=30)


class TestClassify:
    def test_full_session_complete(self):
        rep = classify_sessions(_minutes(NORMAL), TimeFrame.MINUTE)
        assert ("SPY", NORMAL) in rep["complete_keys"]
        assert rep["totals"] == {"total": 1, "complete": 1, "incomplete": 0}
        assert rep["sessions"][("SPY", NORMAL)]["internal_gaps"] == 0

    def test_early_close_full_is_complete(self):
        rep = classify_sessions(_minutes(EARLY), TimeFrame.MINUTE)
        assert ("SPY", EARLY) in rep["complete_keys"]
        assert rep["sessions"][("SPY", EARLY)]["reason"] == "complete"

    def test_partial_start_incomplete(self):
        rep = classify_sessions(_minutes(NORMAL, first_offset=60), TimeFrame.MINUTE)
        assert ("SPY", NORMAL) not in rep["complete_keys"]
        assert rep["sessions"][("SPY", NORMAL)]["reason"] == "partial_start"

    def test_partial_end_incomplete(self):
        rep = classify_sessions(_minutes(NORMAL, drop_last=60), TimeFrame.MINUTE)
        assert ("SPY", NORMAL) not in rep["complete_keys"]
        assert rep["sessions"][("SPY", NORMAL)]["reason"] == "partial_end"

    def test_internal_gap_detected_but_still_complete(self):
        # Missing a few interior minutes: boundaries intact -> complete, but gaps are counted.
        rep = classify_sessions(_minutes(NORMAL, skip=(100, 101, 202)), TimeFrame.MINUTE)
        s = rep["sessions"][("SPY", NORMAL)]
        assert ("SPY", NORMAL) in rep["complete_keys"]
        assert s["reason"] == "complete" and s["internal_gaps"] == 3

    def test_common_across_symbols(self):
        # SPY has NORMAL + EARLY complete; QQQ only NORMAL complete (its EARLY is truncated).
        bars = (_minutes(NORMAL, sym="SPY") + _minutes(EARLY, sym="SPY")
                + _minutes(NORMAL, sym="QQQ") + _minutes(EARLY, sym="QQQ", drop_last=30))
        common = complete_session_dates(bars, TimeFrame.MINUTE)
        assert common == {NORMAL}                       # EARLY excluded: not complete for QQQ

    def test_filter_keeps_only_complete(self):
        bars = _minutes(NORMAL) + _minutes(EARLY, drop_last=30)
        kept, rep = filter_complete_sessions(bars, TimeFrame.MINUTE)
        assert all(b.timestamp < session_bounds(EARLY)[0] for b in kept)   # no EARLY bars survive
        assert rep["totals"]["incomplete"] == 1


@register
class _ProbeHoldAllSess(Strategy):
    """Enter once early each session and hold; only the forced session-close can flatten it."""
    name = "_probe_holdall_sess"
    timeframe = TimeFrame.MINUTE
    intraday = True
    lookback_bars = 1
    max_history = 500
    max_holding_bars = None

    def analyze(self, symbol, bars):
        o, _ = session_bounds(bars[-1].timestamp.date())
        if int((bars[-1].timestamp - o).total_seconds() // 60) == 4:
            return self.make_signal(symbol, SignalSide.BUY, 1.0, bars)
        return None


def _cfg():
    return ResearchConfig(strategies=["_probe_holdall_sess"], initial_cash=1_000_000,
                          max_volume_participation=1.0, commission_bps=0, slippage_bps=0,
                          spread_bps=0, stop_loss_pct=None, take_profit_pct=None)


class TestEngineDiscardsPartialSessions:
    def test_partial_last_session_is_dropped_and_no_overnight(self):
        # A full first session followed by a TRUNCATED second session (ends 60 min early). The
        # second must be discarded, not mistaken for a full day, and nothing may go overnight.
        bars = _minutes(NORMAL) + _minutes(EARLY, drop_last=60)
        acc = run_backtest(bars, _cfg())["accounts"]["_probe_holdall_sess"]
        assert acc["final_portfolio"]["positions"] == {}
        assert acc["metrics"]["intraday"]["overnight_positions"] == 0
        assert acc["metrics"]["intraday"]["sessions"] == 1          # only the complete session traded
        assert acc["session_audit"]["totals"]["incomplete"] == 1
        assert acc["metrics"]["intraday"]["incomplete_sessions_discarded"] == 1
        # The single trade is the complete session, closed by the forced session-close flatten.
        assert len(acc["trades"]) == 1
        assert acc["trades"][0]["exit_reason"] == "session_close"
        assert acc["trades"][0]["exit"] == session_bounds(NORMAL)[1].isoformat()

    def test_partial_first_session_is_dropped(self):
        bars = _minutes(NORMAL, first_offset=90) + _minutes(EARLY)
        acc = run_backtest(bars, _cfg())["accounts"]["_probe_holdall_sess"]
        assert acc["metrics"]["intraday"]["sessions"] == 1
        assert acc["session_audit"]["per_symbol"]["SPY"]["discarded"] == [[NORMAL.isoformat(), "partial_start"]] \
            or acc["session_audit"]["per_symbol"]["SPY"]["discarded"] == [(NORMAL.isoformat(), "partial_start")]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
