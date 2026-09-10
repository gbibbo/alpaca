#!/usr/bin/env python3
"""
lib/session_filter.py
Session completeness for intraday research. The session CLOSE is derived from the market
calendar (lib.market_calendar), never inferred from "the last bar that happens to be present"
-- a partial session (a data pull that starts mid-morning or ends before 16:00 ET) must be
recognised and discarded, not silently treated as a full day.

A complete session, for a given intraday timeframe, is one whose bars span from the session's
FIRST expected bar (the 09:30 ET open bucket) to its LAST expected bar (the bucket whose close is
the session close, 16:00 ET, or 13:00 ET on an early-close day). Early closes are handled because
the expected grid comes from session_bounds(). Internal missing bars (a minute with no trade) are
detected and reported as data-quality gaps but, on their own, do not disqualify a session -- the
disqualifying conditions are a missing open bar (partial start) or a missing close bar (partial
end / truncated pull).

The expected bar-start grid is the SAME one lib.resampler.BarResampler(session_aligned=True)
produces from complete 1m data, so "complete at 1m" implies "complete at 5m/15m/30m/1h": a
session that has its 09:30 open minute and its final minute has, after resampling, every boundary
bucket too.
"""
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from lib.market_calendar import session_bounds, is_trading_day
from lib.timeframes import parse_timeframe, timeframe_minutes
from lib.models import TimeFrame

NY = ZoneInfo("America/New_York")


def session_date(bar):
    """Regular-session (Eastern) date of an intraday bar. RTH never crosses midnight ET."""
    ts = bar.timestamp
    return ts.astimezone(NY).date()


def expected_bar_starts(day, tf):
    """UTC bucket-start timestamps of a COMPLETE regular session on `day` for timeframe `tf`.

    Buckets are measured from the 09:30 ET open, so the final bucket may be short (e.g. the last
    1h bucket of a 6.5h session is 30 min, and an early close ends mid-bucket). Matches
    lib.timeframes.session_bucket_start exactly. Returns [] if `day` is not a trading day.
    """
    tf = parse_timeframe(tf)
    if tf == TimeFrame.DAY:
        raise ValueError("expected_bar_starts is for intraday timeframes")
    b = session_bounds(day)
    if b is None:
        return []
    open_utc, close_utc = b
    m = timeframe_minutes(tf)
    total_min = int((close_utc - open_utc).total_seconds() // 60)
    n = -(-total_min // m)                       # ceil: the last bucket may be short
    return [open_utc + timedelta(minutes=i * m) for i in range(n)]


def expected_last_bar_start(day, tf):
    """UTC start of the LAST bar of a complete session -- the calendar-derived session close bar.
    This is what the intraday engine uses to force-flatten, so the close is never inferred from
    the last available bar. None if `day` is not a trading day."""
    starts = expected_bar_starts(day, tf)
    return starts[-1] if starts else None


def classify_sessions(bars, tf):
    """Classify every (symbol, session-date) present in `bars` for timeframe `tf`.

    Returns a dict:
      complete_keys : set of (symbol, date) that are complete and usable
      sessions      : {(symbol, date): {"status", "reason", "present", "expected",
                                        "internal_gaps", "first", "last"}}
      per_symbol    : {symbol: {"total", "complete", "incomplete", "discarded":[(date,reason)...]}}
      totals        : {"total", "complete", "incomplete"}
    Status is "complete" | "incomplete"; reason is one of complete / partial_start / partial_end /
    not_trading_day / misaligned / empty.
    """
    tf = parse_timeframe(tf)
    groups = defaultdict(list)
    for b in bars:
        groups[(b.symbol, session_date(b))].append(b.timestamp)

    sessions, complete_keys = {}, set()
    per_symbol = defaultdict(lambda: {"total": 0, "complete": 0, "incomplete": 0, "discarded": []})
    for (sym, day), stamps in groups.items():
        present = sorted(set(stamps))
        per_symbol[sym]["total"] += 1
        if not is_trading_day(day):
            status, reason = "incomplete", "not_trading_day"
            expected, internal = [], []
        else:
            expected = expected_bar_starts(day, tf)
            exp_set = set(expected)
            present_set = set(present)
            if not expected:
                status, reason, internal = "incomplete", "not_trading_day", []
            elif present[-1] != expected[-1]:
                status, reason = "incomplete", "partial_end"
                internal = []
            elif present[0] != expected[0]:
                status, reason = "incomplete", "partial_start"
                internal = []
            elif not present_set.issubset(exp_set):
                status, reason = "incomplete", "misaligned"    # off-grid bar (bad resample/feed)
                internal = sorted(present_set - exp_set)
            else:
                status, reason = "complete", "complete"
                internal = [t for t in expected if t not in present_set]  # missing-trade minutes
        sessions[(sym, day)] = {"status": status, "reason": reason,
                                "present": len(present), "expected": len(expected),
                                "internal_gaps": len(internal),
                                "first": present[0].isoformat() if present else None,
                                "last": present[-1].isoformat() if present else None}
        if status == "complete":
            complete_keys.add((sym, day))
            per_symbol[sym]["complete"] += 1
        else:
            per_symbol[sym]["incomplete"] += 1
            per_symbol[sym]["discarded"].append((day.isoformat(), reason))

    totals = {"total": sum(v["total"] for v in per_symbol.values()),
              "complete": sum(v["complete"] for v in per_symbol.values()),
              "incomplete": sum(v["incomplete"] for v in per_symbol.values())}
    return {"complete_keys": complete_keys, "sessions": sessions,
            "per_symbol": {s: dict(v) for s, v in per_symbol.items()}, "totals": totals}


def complete_session_dates(bars, tf):
    """Set of session dates that are complete for EVERY symbol present (the common, aligned set).
    Used to run SPY and QQQ on exactly the same sessions."""
    audit = classify_sessions(bars, tf)
    symbols = {sym for sym, _ in audit["complete_keys"]}
    if not symbols:
        return set()
    per_sym = defaultdict(set)
    for sym, day in audit["complete_keys"]:
        per_sym[sym].add(day)
    common = set.intersection(*(per_sym[s] for s in symbols)) if per_sym else set()
    return common


def filter_complete_sessions(bars, tf, allowed_dates=None):
    """Keep only bars whose (symbol, session-date) is complete for `tf`; optionally restrict to
    `allowed_dates` (a set of dates, e.g. the common SPY/QQQ set). Returns (kept_bars, audit)."""
    audit = classify_sessions(bars, tf)
    complete = audit["complete_keys"]
    kept = [b for b in bars
            if (b.symbol, session_date(b)) in complete
            and (allowed_dates is None or session_date(b) in allowed_dates)]
    return kept, audit
