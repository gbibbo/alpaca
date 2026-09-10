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

Intraday research line (intraday=True -> run_intraday_account; RTH only, no overnight):

    extreme_reversal_1m            1m   long-only ~1/1000 downside-outlier reversal, 5-bar hold
    opening_range_breakout_5m      5m   breakout above the 09:30-10:00 range, held to the close
    market_intraday_momentum_30m   30m  Gao/Han/Zhou 2018: first-half-hour sign -> last-half-hour long

Select at runtime with ENABLED_STRATEGIES="hourly_trend,daily_trend" (default: all).
"""

import logging
from datetime import timedelta
from typing import List, Optional

import numpy as np

from lib.models import Bar, Signal, SignalSide, TimeFrame
from lib.strategy_base import Strategy, register
from lib.timeframes import NY
from lib.market_calendar import session_bounds

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


# ---------------------------------------------------------------- 1d (time-series momentum)
@register
class TimeSeriesMomentum12M(Strategy):
    """12-month time-series (absolute) momentum, long or cash. PREREGISTERED baseline.

        R_12m = P_t / P_{t-252} - 1        (252 sessions ~ one year)
        R_12m > 0  -> BUY (be long)        R_12m <= 0 -> SELL (be flat; never short)

    Evaluated roughly monthly (cooldown 30 days). One hypothesis, one number, one decision:
    no filters, no indicator stack, and the three parameters below (252 sessions, threshold 0,
    monthly review) are fixed in advance and must not be tuned by looking at results.
    Literature: Moskowitz/Ooi/Pedersen 2012 (time-series momentum); Lim/Wang/Yao 2018 (single
    US stocks, 1927-2017); critiques in Goyal/Jegadeesh 2018 and Huang et al. 2020.

    Measure it PURE first (stop_loss_pct/take_profit_pct = null in the research config): a
    tight take-profit caps exactly the multi-month trends this thesis relies on.
    """
    name = "tsmom_12m_long_only"
    timeframe = TimeFrame.DAY
    lookback_sessions = 252          # preregistered
    threshold = 0.0                  # preregistered
    lookback_bars = 253              # 252 sessions of return needs 253 closes
    max_history = 300
    cooldown_seconds = 30 * 86400    # preregistered: ~monthly review
    signal_expiry_seconds = 4 * 86400
    description = "12-month absolute momentum (252 sessions), long or cash, monthly review"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        closes = [float(b.close) for b in bars]
        if len(closes) < self.lookback_sessions + 1:
            return None
        momentum_12m = closes[-1] / closes[-(self.lookback_sessions + 1)] - 1.0
        side = SignalSide.BUY if momentum_12m > self.threshold else SignalSide.SELL
        return self.make_signal(symbol, side, 1.0, bars, {
            "momentum_12m": momentum_12m,
            "lookback_sessions": self.lookback_sessions,
            "threshold": self.threshold,
        })


# ================================================================ INTRADAY RESEARCH LINE
# The three strategies below are PREREGISTERED, exploratory, and long-only. They declare
# intraday=True, so lib.backtest routes them to run_intraday_account: decisions on CLOSED bars,
# entries fill at the NEXT bar's open, forced flatten at the session close, no overnight. Their
# parameters are fixed in advance (no grid search); results are to be reported gross AND net of
# costs, and negative results kept. Feed them session-aligned RTH bars of their own timeframe.

def _et_date(bar: Bar):
    """Regular-session (Eastern) calendar date of a bar; RTH never crosses midnight ET, so this
    groups bars by trading session without a separate calendar lookup."""
    return bar.timestamp.astimezone(NY).date()


@register
class ExtremeReversal1m(Strategy):
    """PREREGISTERED. Long-only very-short-horizon reversal on 1-minute bars.

    Hypothesis: an extreme one-minute DOWN move (a ~1/1000 tail event) is partly liquidity-driven
    and mean-reverts over the next few minutes.

    Rule (ex-ante, reproducible):
      - Work in intraday 1m log-returns only; overnight gaps (a return whose two bars fall on
        different ET sessions) are excluded from both the estimate and the trigger.
      - Over the trailing `window` intraday returns (excluding the current one), compute mean mu
        and sample std sd. Standardise the current return: z = (r_t - mu) / sd.
      - BUY when z <= -`z_threshold`. z_threshold = 3.09 is the one-sided 0.1% (~1/1000) quantile
        of a standard normal, i.e. the "extreme quantile" expressed parametrically so it is stable
        with a few hundred observations (an empirical 1/1000 quantile would need ~1000 points and
        be dominated by a single value).
      - Long-only: an extreme UP move produces no signal (no shorting in this engine).
      - Entry fills at the NEXT bar's open (engine contract); hold exactly `max_holding_bars` bars,
        then exit at that bar's open, or force-flatten at the session close, whichever comes first.

    All four parameters (window, z_threshold, hold, 1m timeframe) are fixed here and must not be
    tuned against results. Test on SPY (primary) and QQQ (replication).
    """
    name = "extreme_reversal_1m"
    timeframe = TimeFrame.MINUTE
    intraday = True
    window = 390                 # trailing intraday 1m returns (~one RTH session), preregistered
    z_threshold = 3.09           # ~1/1000 one-sided Gaussian tail, preregistered
    max_holding_bars = 5         # very short explicit holding period (5 minutes), preregistered
    lookback_bars = 60           # analyze may run early; it self-guards on `window`
    max_history = 900
    cooldown_seconds = 60
    signal_expiry_seconds = 120
    description = "Long-only 1m reversal: BUY on a ~1/1000 downside-outlier minute, 5-bar hold"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        if len(bars) < 2:
            return None
        # Only the last `window`+1 intraday returns matter; bound the slice so per-bar cost stays
        # O(window), not O(history) -- required to run on years of 1m bars. `math.log` avoids the
        # numpy per-scalar overhead. The rule (trailing-window standardised return) is unchanged.
        import math
        recent = bars[-(self.window + 60):]      # a little slack to absorb overnight-gap drops
        closes = [float(b.close) for b in recent]
        dates = [_et_date(b) for b in recent]
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes)) if dates[i] == dates[i - 1]]
        if len(rets) < self.window + 1:
            return None
        # The current return must itself be intraday (current bar not the first of its session).
        if dates[-1] != dates[-2]:
            return None
        last = rets[-1]
        hist = rets[-(self.window + 1):-1]        # exclude current -> trigger can't be trivially true
        n = len(hist)
        mu = sum(hist) / n
        var = sum((x - mu) ** 2 for x in hist) / (n - 1)   # sample variance (ddof=1)
        sd = math.sqrt(var)
        if not (sd > 0) or not math.isfinite(sd):
            return None
        z = (last - mu) / sd
        if z <= -self.z_threshold:
            confidence = float(min(0.9, 0.5 + (abs(z) - self.z_threshold) * 0.1))
            return self.make_signal(symbol, SignalSide.BUY, confidence, bars,
                                    {"rule": "intraday_logret_z<=-3.09 (~1/1000)",
                                     "z": round(z, 3), "window": self.window},
                                    hold_bars=self.max_holding_bars)
        return None


@register
class OpeningRangeBreakout5m(Strategy):
    """PREREGISTERED. Long-only opening-range breakout on 5-minute bars.

    Opening range (OR) = the 09:30-10:00 ET window (the first six 5m bars). After 10:00, the first
    time a bar's CLOSE is strictly above the OR high, go long; hold to the session close (no time
    exit, no overnight). At most one entry per session. Mirror (short below OR low) omitted:
    long-only engine.

    No lookahead: the OR is only considered complete once a bar at/after 10:00 is seen, and the
    breakout is judged on a closed bar. Fixed parameters (OR = first 30 min, one entry/session);
    no grid search. Test SPY (primary), QQQ (replication).
    """
    name = "opening_range_breakout_5m"
    timeframe = TimeFrame.FIVE_MINUTE
    intraday = True
    exit_at_session_close = True
    max_holding_bars = None      # no time exit; the session close is the exit
    or_minutes = 30              # preregistered opening-range length
    lookback_bars = 1
    max_history = 200
    cooldown_seconds = 5 * 60
    signal_expiry_seconds = 10 * 60
    description = "Breakout above the 09:30-10:00 range on 5m bars, held to the close"

    def __init__(self, **params):
        super().__init__(**params)
        self._or_high = {}       # symbol -> running OR high (None until the OR forms)
        self._entered = {}       # symbol -> already took the session's one entry?

    def on_session_start(self, symbol: str) -> None:
        self._or_high[symbol] = None
        self._entered[symbol] = False

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        bar = bars[-1]
        open_utc, _ = session_bounds(_et_date(bar))
        minutes_in = (bar.timestamp - open_utc).total_seconds() / 60.0
        if minutes_in < 0:
            return None
        high = float(bar.high)
        if minutes_in < self.or_minutes:
            prev = self._or_high.get(symbol)
            self._or_high[symbol] = high if prev is None else max(prev, high)
            return None
        or_high = self._or_high.get(symbol)
        if or_high is None or self._entered.get(symbol):
            return None
        if float(bar.close) > or_high:
            self._entered[symbol] = True
            confidence = float(min(0.85, 0.55 + (float(bar.close) / or_high - 1.0) * 20))
            return self.make_signal(symbol, SignalSide.BUY, confidence, bars,
                                    {"rule": "close>opening_range_high", "or_high": or_high,
                                     "or_minutes": self.or_minutes})
        return None


@register
class MarketIntradayMomentum30m(Strategy):
    """PREREGISTERED. Replicates Gao, Han, Li & Zhou (2018), "Market Intraday Momentum".

    Signal: the return from the PRIOR session's close to the end of the FIRST half hour
    (r_first30 = close(09:30-10:00) / prior_close - 1). If r_first30 > 0, be LONG during the LAST
    half hour of the session; close at the session end. Sign-based, no threshold optimization.

    Mechanics on 30m session-aligned bars: the decision is taken on the SECOND-TO-LAST bar
    (15:00-15:30 on a full day); the engine then fills the entry at the LAST bar's open
    (15:30, enabled by allow_last_bar_entry) and force-closes at that bar's close (16:00) -- a
    one-bar hold covering exactly the final half hour. The last-half-hour timing is derived from
    session_bounds, so early-close days (last bar 12:30-13:00) are handled automatically.

    Long-only: Gao et al.'s SHORT leg (r_first30 < 0 -> short the last half hour) is NOT tradeable
    in this long-only engine and is skipped; this is a documented deviation, not a result. SPY is
    primary, QQQ secondary.
    """
    name = "market_intraday_momentum_30m"
    timeframe = TimeFrame.THIRTY_MINUTE
    intraday = True
    allow_last_bar_entry = True   # enter at the last bar's open, exit at its close (Gao et al.)
    exit_at_session_close = True
    max_holding_bars = None
    lookback_bars = 1
    max_history = 60
    cooldown_seconds = 20 * 60
    signal_expiry_seconds = 40 * 60
    description = "Gao et al. 2018 market intraday momentum: first-half-hour sign -> last-half-hour long"

    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        bar = bars[-1]
        cur_date = _et_date(bar)
        open_utc, close_utc = session_bounds(cur_date)
        # Act only on the second-to-last bar of the session (its close = 60 min before the close).
        decision_start = close_utc - timedelta(minutes=60)
        if bar.timestamp != decision_start:
            return None
        # Locate this session's first 30m bar and the prior session's last close.
        dates = [_et_date(b) for b in bars]
        try:
            idx_first = dates.index(cur_date)
        except ValueError:
            return None
        if idx_first == 0:
            return None                          # no prior-session close in history
        first30_close = float(bars[idx_first].close)
        prior_close = float(bars[idx_first - 1].close)
        if prior_close <= 0:
            return None
        r_first30 = first30_close / prior_close - 1.0
        if r_first30 > 0:                        # long-only: only the positive-morning leg trades
            confidence = float(min(0.85, 0.55 + abs(r_first30) * 20))
            return self.make_signal(symbol, SignalSide.BUY, confidence, bars,
                                    {"rule": "r_first30>0 -> long last half hour",
                                     "r_first30": round(r_first30, 5)})
        return None
