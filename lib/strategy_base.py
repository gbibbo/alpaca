#!/usr/bin/env python3
"""
lib/strategy_base.py
Base class + registry for trading strategies.

A strategy declares WHAT it looks at (universe, timeframe, lookback) and WHEN it speaks
(cooldown, signal expiry). It answers only "is SYMBOL attractive right now, and how sure am I".
HOW MUCH to buy is decided downstream by the risk manager from live portfolio state.

To add a strategy:

    from lib.strategy_base import Strategy, register
    from lib.models import TimeFrame, SignalSide

    @register
    class MyStrategy(Strategy):
        name = "my_strategy"          # must be unique; becomes Signal.source
        timeframe = TimeFrame.HOUR    # bars it consumes (1m, 5m, 1h, 1d)
        lookback_bars = 60            # analyze() is not called before this many bars exist
        cooldown_seconds = 3600       # min gap between two signals for the same symbol

        def analyze(self, symbol, bars):
            closes = [float(b.close) for b in bars]
            if closes[-1] > max(closes[-20:-1]):
                return self.make_signal(symbol, SignalSide.BUY, 0.7, bars, {"reason": "breakout"})
            return None

Strategies live in apps/strategies/library.py and are picked up automatically.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional, Type

from lib.models import Bar, Signal, SignalSide, TimeFrame
from lib.timeframes import timeframe_seconds


class Strategy(ABC):
    # --- declaration (override in subclasses) ---
    name: str = "base"
    timeframe: TimeFrame = TimeFrame.MINUTE
    lookback_bars: int = 50            # minimum bars before analyze() runs
    max_history: int = 500             # bars retained per symbol
    universe: Optional[List[str]] = None  # None -> every symbol configured in settings
    signal_expiry_seconds: Optional[int] = None  # None -> 2 bars worth of time
    cooldown_seconds: Optional[int] = None       # None -> 5 bars worth of time
    min_confidence: float = 0.5        # signals below this are discarded by the engine
    description: str = ""
    # --- intraday contract (used by the engine's dedicated intraday path) ---
    intraday: bool = False             # True routes to run_intraday_account (RTH, no overnight)
    exit_at_session_close: bool = True # force-flatten at the session's last bar close
    max_holding_bars: Optional[int] = None  # default time-based exit when a signal omits hold_bars
    allow_last_bar_entry: bool = False # if True, a pending entry may fill on the session's LAST
                                       # bar (open) and be force-closed at that same bar's close --
                                       # a legitimate one-bar hold (e.g. the Gao et al. last-half-hour
                                       # position). Default False: never open a doomed last-bar entry.
    allow_short: bool = False          # if True, a SELL signal while flat opens a SHORT (research
                                       # engine only; symmetric costs). Default False keeps every
                                       # existing strategy long-only and byte-for-byte unchanged.
    signal_driven_exit: bool = False   # if True, an OPPOSING signal (SELL while long / BUY while
                                       # short) closes the position at the next bar's open, in
                                       # addition to the time / session-close exits. Default False:
                                       # positions leave only via hold_bars or the session close.

    def __init__(self, **params):
        self.params = params
        if self.signal_expiry_seconds is None:
            self.signal_expiry_seconds = max(60, 2 * timeframe_seconds(self.timeframe))
        if self.cooldown_seconds is None:
            self.cooldown_seconds = 5 * timeframe_seconds(self.timeframe)

    # --- contract ---
    @abstractmethod
    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        """bars: oldest -> newest, all of self.timeframe, len >= lookback_bars."""

    def applies_to(self, symbol: str) -> bool:
        return self.universe is None or symbol.upper() in {s.upper() for s in self.universe}

    def set_seed(self, seed: int) -> None:  # optional, for reproducible runs
        pass

    # --- helpers ---
    def on_session_start(self, symbol: str) -> None:
        """Hook called by the intraday engine at the first bar of each regular session, so a
        strategy can reset per-session state (e.g. an opening range). No-op by default."""

    def make_signal(self, symbol: str, side: SignalSide, confidence: float, bars: List[Bar],
                    metadata: Optional[dict] = None, hold_bars: Optional[int] = None) -> Signal:
        latest = bars[-1]
        meta = {
            "strategy": self.name,
            "timeframe": self.timeframe.value,
            "bar_timestamp": latest.timestamp.isoformat(),
            "bar_count": len(bars),
        }
        if metadata:
            meta.update(metadata)
        from lib.backtest import available_at
        from uuid import uuid5, NAMESPACE_URL
        return Signal(
            signal_id=uuid5(NAMESPACE_URL, f"{self.name}:{symbol}:{latest.timestamp.isoformat()}:{side.value}"),
            symbol=symbol,
            timestamp=available_at(latest),
            side=side,
            confidence=round(float(confidence), 3),
            price=latest.close,
            expire_seconds=self.signal_expiry_seconds,
            source=self.name,
            timeframe=self.timeframe,
            hold_bars=hold_bars if hold_bars is not None else self.max_holding_bars,
            metadata=meta,
        )

    def describe(self) -> dict:
        return {
            "name": self.name,
            "timeframe": self.timeframe.value,
            "lookback_bars": self.lookback_bars,
            "universe": self.universe,
            "cooldown_seconds": self.cooldown_seconds,
            "signal_expiry_seconds": self.signal_expiry_seconds,
            "min_confidence": self.min_confidence,
            "params": self.params,
        }


# ---------------------------------------------------------------- registry
STRATEGY_REGISTRY: Dict[str, Type[Strategy]] = {}


def register(cls: Type[Strategy]) -> Type[Strategy]:
    if not cls.name or cls.name == "base":
        raise ValueError(f"{cls.__name__} must define a unique `name`")
    if cls.name in STRATEGY_REGISTRY and STRATEGY_REGISTRY[cls.name] is not cls:
        raise ValueError(f"Strategy name '{cls.name}' already registered by {STRATEGY_REGISTRY[cls.name].__name__}")
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


def registered_names() -> List[str]:
    _ensure_library_loaded()
    return sorted(STRATEGY_REGISTRY.keys())


def create_strategies(enabled: Optional[List[str]] = None) -> List[Strategy]:
    """Instantiate registered strategies; `enabled` filters by name (None/empty -> all)."""
    _ensure_library_loaded()
    wanted = [n.strip().lower() for n in (enabled or []) if n.strip()]
    unknown = [n for n in wanted if n not in STRATEGY_REGISTRY]
    if unknown:
        raise ValueError(f"Unknown strategies {unknown}. Registered: {registered_names()}")
    names = wanted or sorted(STRATEGY_REGISTRY.keys())
    return [STRATEGY_REGISTRY[n]() for n in names]


def _ensure_library_loaded() -> None:
    """Import the strategy library so @register decorators run (idempotent)."""
    try:
        import apps.strategies.library  # noqa: F401
    except ImportError:
        pass
