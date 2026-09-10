#!/usr/bin/env python3
"""
lib/market_calendar.py
Computed US equity (NYSE/NASDAQ) session calendar for ANY year, so backtests are not limited
to a hardcoded 2024-2026 table.

Covers the ten standard holidays with their observed-date rules, Good Friday (computed from
Easter), and the three routine early-close days (1:00 PM ET). This is a rules-based
approximation: it does NOT model one-off closures (e.g. national days of mourning) or ad-hoc
schedule changes. For live trading the Alpaca Clock/Calendar API remains the source of truth;
this module exists so historical replays have a deterministic, year-agnostic session model.
"""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Optional, Tuple

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of month; n negative counts from the end (-1 = last)."""
    if n > 0:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + 7 * (n - 1))
    d = date(year, month, 28) + timedelta(days=4)
    last = d - timedelta(days=d.day)  # last day of month
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset + 7 * (-n - 1))


def _observed(d: date) -> date:
    """Federal observed-date rule: Saturday -> Friday, Sunday -> Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset:
    """Set of full-closure NYSE holiday dates for `year` (observed)."""
    h = {
        _observed(date(year, 1, 1)),                    # New Year's Day
        _nth_weekday(year, 1, 0, 3),                    # MLK (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),                    # Presidents' (3rd Mon Feb)
        _easter(year) - timedelta(days=2),              # Good Friday
        _nth_weekday(year, 5, 0, -1),                   # Memorial (last Mon May)
        _nth_weekday(year, 9, 0, 1),                    # Labor (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),                   # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),                  # Christmas
    }
    if year >= 2021:  # Juneteenth became a market holiday in 2022 (first observed 2022)
        h.add(_observed(date(year, 6, 19)))
    h.add(_observed(date(year, 7, 4)))                  # Independence Day
    return frozenset(h)


def is_holiday(d) -> bool:
    d = _as_date(d)
    return d in holidays(d.year)


def is_trading_day(d) -> bool:
    d = _as_date(d)
    return d.weekday() < 5 and not is_holiday(d)


def is_early_close(d) -> bool:
    """Routine half-days: Jul 3 (if a weekday and Jul 4 is a trading day), the Friday after
    Thanksgiving, and Dec 24 (if a weekday)."""
    d = _as_date(d)
    if not is_trading_day(d):
        return False
    # Day after Thanksgiving
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    # July 3 half-day when it is itself a weekday trading day
    if d.month == 7 and d.day == 3 and d.weekday() < 5:
        return True
    # Christmas Eve half-day when a weekday
    if d.month == 12 and d.day == 24 and d.weekday() < 5:
        return True
    return False


def session_hours(d) -> Tuple[time, time]:
    """(open, close) ET for a trading day; raises if `d` is not a trading day."""
    d = _as_date(d)
    if not is_trading_day(d):
        raise ValueError(f"{d} is not a trading day")
    return (REGULAR_OPEN, EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE)


def session_close(d) -> time:
    return session_hours(d)[1]


def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    return d
