#!/usr/bin/env python3
"""
apps/strategies/library.py
Built-in strategies, one per horizon. They are deliberately simple baselines; the point is that
each one declares its timeframe/lookback and the engine routes the right bars to it.

    random_50_50          1m   infrastructure test only (random signals)
    smart_technical       1m   SMA20/50 + RSI14 + MACD scoring (original strategy)
    intraday_momentum_5m  5m   RSI + SMA20 momentum
    hourly_trend          1h   SMA20/SMA50 crossover
    daily_trend           1d   SMA50/SMA200 regime + RSI filter

Select at runtime with ENABLED_STRATEGIES="hourly_trend,daily_trend" (default: all).
"""

import logging
from typing import List, Optional

import numpy as np

from lib.models import Bar, Signal, SignalSide, TimeFrame
from lib.strategy_base import Strategy, register

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- indicators
class TechnicalIndicators:
    """Plain-float indicator helpers (Bar prices are Decimal; convert with float() first)."""

    @staticmethod
    def sma(prices: List[float], period: int) -> Optional[float]:
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> Optional[float]:
        if len(prices) < period + 1:
            return None
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0.0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def ema(prices: List[float], period: int) -> Optional[float]:
        if len(prices) < period:
            return None
        alpha = 2.0 / (period + 1)
        value = sum(prices[:period]) / period
        for p in prices[period:]:
            value = alpha * p + (1 - alpha) * value
        return value

    @staticmethod
    def macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """Returns (macd_line, signal_line, histogram); (None, None, None) if not enough data."""
        if len(prices) < slow + signal:
            return None, None, None
        macd_series = []
        for i in range(slow, len(prices) + 1):
            window = prices[:i]
            macd_series.append(TechnicalIndicators.ema(window, fast) - TechnicalIndicators.ema(window, slow))
        macd_line = macd_series[-1]
        signal_line = TechnicalIndicators.ema(macd_series, signal) if len(macd_series) >= signal else None
        hist = macd_line - signal_line if signal_line is not None else None
        return macd_line, signal_line, hist

    @staticmethod
    def volatility(prices: List[float], window: int = 20) -> float:
        recent = prices[-window:]
        return float(np.std(recent) / np.mean(recent)) if recent else 0.0


# ---------------------------------------------------------------- 1m
@register
class Random50Strategy(Strategy):
    """Random 50/50 signals. Exercises the pipeline; never use for real decisions."""
    name = "random_50_50"
    timeframe = TimeFrame.MINUTE
    lookback_bars = 10
    cooldown_seconds = 300
    signal_expiry_seconds = 300
    description = "Random BUY/SELL, ~5% of bars, for infrastructure testing"

    def __init__(self, seed: Optional[int] = 42, signal_probability: float = 0.05, **params):
        super().__init__(seed=seed, signal_probability=signal_probability, **params)
        self.seed = seed
        self.signal_probability = signal_probability
        self.rng = np.random.default_rng(seed)

    def set_seed(self, seed: int) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        logger.info(f"{self.name} seed updated to {seed}")

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        if self.rng.random() >= self.signal_probability:
            return None
        side = SignalSide.BUY if self.rng.random() > 0.5 else SignalSide.SELL
        confidence = float(self.rng.uniform(0.4, 0.8))
        return self.make_signal(symbol, side, confidence, bars, {"strategy_type": "random"})


@register
class SmartTechnicalStrategy(Strategy):
    """Score-based: trend (price > SMA20 > SMA50), RSI extremes, MACD sign. 2 of 3 agree -> signal."""
    name = "smart_technical"
    timeframe = TimeFrame.MINUTE
    lookback_bars = 50
    cooldown_seconds = 300
    signal_expiry_seconds = 300
    description = "SMA20/50 + RSI14 + MACD scoring on 1m bars"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        closes = [float(b.close) for b in bars]
        sma_20 = TechnicalIndicators.sma(closes, 20)
        sma_50 = TechnicalIndicators.sma(closes, 50)
        rsi = TechnicalIndicators.rsi(closes, 14)
        macd, _, _ = TechnicalIndicators.macd(closes)
        if None in (sma_20, sma_50, rsi):
            return None

        price = closes[-1]
        buy_votes = sell_votes = 0
        if price > sma_20 > sma_50:
            buy_votes += 1
        elif price < sma_20 < sma_50:
            sell_votes += 1
        if rsi < 30:
            buy_votes += 1
        elif rsi > 70:
            sell_votes += 1
        if macd is not None and macd > 0:
            buy_votes += 1
        elif macd is not None and macd < 0:
            sell_votes += 1

        vol = TechnicalIndicators.volatility(closes, 20)
        multiplier = 0.5 if vol > 0.05 else 1.0

        if buy_votes >= 2:
            side, votes = SignalSide.BUY, buy_votes
        elif sell_votes >= 2:
            side, votes = SignalSide.SELL, sell_votes
        else:
            return None

        confidence = min(0.9, (votes / 3) * 0.8 * multiplier)
        return self.make_signal(symbol, side, confidence, bars, {
            "buy_votes": buy_votes, "sell_votes": sell_votes,
            "sma_20": sma_20, "sma_50": sma_50, "rsi": rsi, "macd": macd, "volatility": vol,
        })


# ---------------------------------------------------------------- 5m
@register
class IntradayMomentum5m(Strategy):
    """Momentum on 5-minute bars: price above SMA20 with rising RSI in the 50-70 band -> BUY;
    the mirror image (RSI 30-50, falling, below SMA20) -> SELL."""
    name = "intraday_momentum_5m"
    timeframe = TimeFrame.FIVE_MINUTE
    lookback_bars = 30
    cooldown_seconds = 30 * 60
    signal_expiry_seconds = 10 * 60
    description = "RSI14 + SMA20 momentum on 5m bars"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        closes = [float(b.close) for b in bars]
        sma_20 = TechnicalIndicators.sma(closes, 20)
        rsi_now = TechnicalIndicators.rsi(closes, 14)
        rsi_prev = TechnicalIndicators.rsi(closes[:-1], 14)
        if None in (sma_20, rsi_now, rsi_prev):
            return None
        price = closes[-1]
        rising = price > closes[-4]
        if price > sma_20 and 50 <= rsi_now <= 70 and rsi_now > rsi_prev and rising:
            confidence = 0.55 + min(0.25, (rsi_now - 50) / 80)
            side = SignalSide.BUY
        elif price < sma_20 and 30 <= rsi_now <= 50 and rsi_now < rsi_prev and not rising:
            confidence = 0.55 + min(0.25, (50 - rsi_now) / 80)
            side = SignalSide.SELL
        else:
            return None
        return self.make_signal(symbol, side, confidence, bars, {"sma_20": sma_20, "rsi": rsi_now})


# ---------------------------------------------------------------- 1h
@register
class HourlyTrendStrategy(Strategy):
    """Trend following on hourly bars: SMA20 above SMA50 and price above SMA20 -> BUY; mirror -> SELL."""
    name = "hourly_trend"
    timeframe = TimeFrame.HOUR
    lookback_bars = 60
    cooldown_seconds = 4 * 3600
    signal_expiry_seconds = 3600
    description = "SMA20/SMA50 crossover on 1h bars"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        closes = [float(b.close) for b in bars]
        sma_20 = TechnicalIndicators.sma(closes, 20)
        sma_50 = TechnicalIndicators.sma(closes, 50)
        if None in (sma_20, sma_50):
            return None
        price = closes[-1]
        spread = (sma_20 - sma_50) / sma_50
        if sma_20 > sma_50 and price > sma_20:
            side = SignalSide.BUY
        elif sma_20 < sma_50 and price < sma_20:
            side = SignalSide.SELL
        else:
            return None
        confidence = min(0.85, 0.55 + abs(spread) * 10)  # 1% spread -> 0.65, 3% -> 0.85
        return self.make_signal(symbol, side, confidence, bars, {"sma_20": sma_20, "sma_50": sma_50, "spread": spread})


# ---------------------------------------------------------------- 1d
@register
class DailyTrendStrategy(Strategy):
    """Swing regime filter on daily bars: SMA50 above SMA200 and price above SMA50 with RSI < 70 -> BUY;
    price below SMA50 and SMA50 below SMA200 -> SELL."""
    name = "daily_trend"
    timeframe = TimeFrame.DAY
    lookback_bars = 200
    max_history = 400
    cooldown_seconds = 5 * 86400
    signal_expiry_seconds = 4 * 86400
    description = "SMA50/SMA200 regime + RSI14 filter on daily bars"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        closes = [float(b.close) for b in bars]
        sma_50 = TechnicalIndicators.sma(closes, 50)
        sma_200 = TechnicalIndicators.sma(closes, 200)
        rsi = TechnicalIndicators.rsi(closes, 14)
        if None in (sma_50, sma_200, rsi):
            return None
        price = closes[-1]
        if sma_50 > sma_200 and price > sma_50 and rsi < 70:
            side = SignalSide.BUY
            confidence = min(0.85, 0.6 + (price - sma_50) / sma_50 * 5)
        elif sma_50 < sma_200 and price < sma_50:
            side = SignalSide.SELL
            confidence = min(0.85, 0.6 + (sma_50 - price) / sma_50 * 5)
        else:
            return None
        return self.make_signal(symbol, side, confidence, bars, {"sma_50": sma_50, "sma_200": sma_200, "rsi": rsi})


# ---------------------------------------------------------------- 1d (breakout)
@register
class TurtleBreakout(Strategy):
    """Donchian channel breakout (classic Turtle-style), long-only and easy to audit.

    Entry: today's close is strictly above the highest HIGH of the prior `entry_channel` bars
           (a new N-day high).
    Exit:  today's close is strictly below the lowest LOW of the prior `exit_channel` bars
           (a new M-day low); the engine also attaches the configured protective stop/target.

    Every decision reduces to one comparison against a rolling extreme of *prior* bars (the
    current bar is excluded, so the rule can never be trivially true). Parameters are explicit.
    """
    name = "turtle_breakout"
    timeframe = TimeFrame.DAY
    entry_channel = 20
    exit_channel = 10
    lookback_bars = 21          # entry_channel prior bars + the current bar
    max_history = 60
    cooldown_seconds = 86400    # at most one signal per session per symbol
    signal_expiry_seconds = 4 * 86400
    description = "Donchian 20-day high entry / 10-day low exit on daily bars"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        prior_highs = [float(b.high) for b in bars[:-1]]
        prior_lows = [float(b.low) for b in bars[:-1]]
        if len(prior_highs) < self.entry_channel:
            return None
        close = float(bars[-1].close)
        entry_level = max(prior_highs[-self.entry_channel:])
        exit_level = min(prior_lows[-self.exit_channel:]) if len(prior_lows) >= self.exit_channel else None

        if close > entry_level:
            # Confidence grows with how decisively the breakout clears the channel.
            confidence = min(0.85, 0.6 + (close / entry_level - 1) * 10)
            return self.make_signal(symbol, SignalSide.BUY, confidence, bars,
                                    {"rule": "close>20d_high", "entry_level": entry_level, "close": close})
        if exit_level is not None and close < exit_level:
            confidence = min(0.85, 0.6 + (1 - close / exit_level) * 10)
            return self.make_signal(symbol, SignalSide.SELL, confidence, bars,
                                    {"rule": "close<10d_low", "exit_level": exit_level, "close": close})
        return None
