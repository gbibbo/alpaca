#!/usr/bin/env python3
"""
lib/portfolio_strategy.py
Second strategy contract for JOINT decisions over a universe (cross-sectional strategies).

`Strategy` (lib/strategy_base.py) answers "is SYMBOL attractive?" one symbol at a time and stays
untouched. A `PortfolioStrategy` answers "given everything eligible today, what should the whole
portfolio look like?" and returns a `PortfolioTarget`: target weights that sum to at most 1
(the remainder is cash). A RebalancePlanner (lib/rebalance.py) turns the target into trades.

Universe membership is a first-class, point-in-time question (`members(as_of)`): picking today's
index constituents and backtesting them into the past is survivorship bias. `StaticUniverse` is
provided for architectural smoke tests only; its results are not historical evidence.
"""

import bisect
import csv as _csv
import re as _re
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Type

from pydantic import BaseModel, Field, field_validator

from lib.models import Bar, TimeFrame


class PortfolioTarget(BaseModel):
    """A joint allocation decision: symbol -> target weight of equity. Sum <= 1; rest is cash."""
    timestamp: datetime
    strategy: str
    weights: Dict[str, Decimal] = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    @field_validator("weights")
    @classmethod
    def _valid_weights(cls, w):
        total = Decimal(0)
        for sym, x in w.items():
            x = Decimal(str(x))
            if x < 0:
                raise ValueError(f"negative weight for {sym} (long-only contract)")
            total += x
        if total > Decimal("1.0000001"):
            raise ValueError(f"weights sum to {total} > 1")
        return {s: Decimal(str(x)) for s, x in w.items()}

    @property
    def gross_exposure(self) -> Decimal:
        return sum(self.weights.values(), Decimal(0))


class Universe(ABC):
    """Who is eligible at a point in time."""
    @abstractmethod
    def members(self, as_of: datetime) -> List[str]: ...


class StaticUniverse(Universe):
    """Fixed list, identical at every date. Architectural smoke tests only (survivorship-biased)."""
    def __init__(self, symbols: Iterable[str]):
        self.symbols = sorted({s.upper() for s in symbols})

    def members(self, as_of: datetime) -> List[str]:
        return list(self.symbols)


def _as_date(s: str) -> date:
    s = s.strip()
    if "T" in s or " " in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    return datetime.strptime(s, "%Y-%m-%d").date()


class CsvPointInTimeUniverse(Universe):
    """Point-in-time index membership from a CSV, so a backtest only ever holds names that were
    actually in the index on each date (the fix for survivorship bias).

    Accepts either shape (header row required):
      * long  : columns `date,symbol`   -> one row per member per effective date
      * wide  : columns `date,symbols`  -> `symbols` is a list separated by ; , space or tab
    members(as_of) returns the constituents effective on the most recent date <= as_of
    (forward-filled), so both a daily snapshot and a sparse change-log work.
    """
    def __init__(self, path):
        rows: Dict[date, set] = defaultdict(set)
        with Path(path).open(newline="", encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
            date_col = cols.get("date")
            if not date_col:
                raise ValueError("point-in-time universe CSV needs a 'date' column")
            sym_col = cols.get("symbol") or cols.get("ticker")
            list_col = cols.get("symbols") or cols.get("constituents") or cols.get("tickers")
            if not sym_col and not list_col:
                raise ValueError("universe CSV needs a 'symbol' (long) or 'symbols' (wide) column")
            for row in reader:
                d = _as_date(row[date_col])
                if sym_col:
                    s = (row[sym_col] or "").strip().upper()
                    if s:
                        rows[d].add(s)
                else:
                    for s in _re.split(r"[;,\s]+", (row[list_col] or "").strip()):
                        if s:
                            rows[d].add(s.upper())
        if not rows:
            raise ValueError("point-in-time universe CSV is empty")
        self._dates: List[date] = sorted(rows)
        self._members: Dict[date, List[str]] = {d: sorted(rows[d]) for d in self._dates}

    def members(self, as_of: datetime) -> List[str]:
        d = as_of.date() if isinstance(as_of, datetime) else as_of
        i = bisect.bisect_right(self._dates, d) - 1
        return list(self._members[self._dates[i]]) if i >= 0 else []

    @property
    def all_symbols(self) -> List[str]:
        return sorted({s for members in self._members.values() for s in members})


class PortfolioStrategy(ABC):
    name: str = "portfolio_base"
    timeframe: TimeFrame = TimeFrame.DAY
    lookback_bars: int = 253           # histories shorter than this are not eligible
    max_history: int = 400
    rebalance: str = "monthly"         # decide on the last session of each month
    description: str = ""

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def target(self, as_of: datetime, histories: Dict[str, List[Bar]], universe: List[str]) -> PortfolioTarget:
        """histories: symbol -> bars (oldest -> newest, all <= as_of); universe: eligible symbols
        (each has >= lookback_bars of history). Must use only information in `histories`."""

    def describe(self) -> dict:
        return {"name": self.name, "kind": "portfolio", "timeframe": self.timeframe.value,
                "lookback_bars": self.lookback_bars, "rebalance": self.rebalance, "params": self.params}


# ---------------------------------------------------------------- registry
PORTFOLIO_REGISTRY: Dict[str, Type[PortfolioStrategy]] = {}


def register_portfolio(cls: Type[PortfolioStrategy]) -> Type[PortfolioStrategy]:
    if not cls.name or cls.name == "portfolio_base":
        raise ValueError(f"{cls.__name__} must define a unique `name`")
    if cls.name in PORTFOLIO_REGISTRY and PORTFOLIO_REGISTRY[cls.name] is not cls:
        raise ValueError(f"Portfolio strategy '{cls.name}' already registered")
    PORTFOLIO_REGISTRY[cls.name] = cls
    return cls


def portfolio_names() -> List[str]:
    _ensure_loaded()
    return sorted(PORTFOLIO_REGISTRY)


def is_portfolio_strategy(name: str) -> bool:
    _ensure_loaded()
    return name in PORTFOLIO_REGISTRY


def create_portfolio_strategy(name: str) -> PortfolioStrategy:
    _ensure_loaded()
    if name not in PORTFOLIO_REGISTRY:
        raise ValueError(f"Unknown portfolio strategy '{name}'. Registered: {portfolio_names()}")
    return PORTFOLIO_REGISTRY[name]()


def _ensure_loaded() -> None:
    try:
        import apps.strategies.portfolio_library  # noqa: F401
    except ImportError:
        pass
