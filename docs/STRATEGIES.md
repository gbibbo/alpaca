# Strategies, timeframes and portfolio sizing

## Concepts

| Concept | Where | What it is |
|---|---|---|
| **Universe** | `SYMBOLS` in `.env` (`settings.symbols_list`), optionally narrowed per strategy via `Strategy.universe` | Symbols a strategy may look at |
| **Strategy** | `apps/strategies/library.py`, contract in `lib/strategy_base.py` | Declares `timeframe`, `lookback_bars`, `cooldown_seconds`, `signal_expiry_seconds`; answers "is SYMBOL attractive now, with what confidence" |
| **Portfolio** | Live Alpaca account (equity + open positions), read by the risk manager | Cash, positions, market value, equity |
| **Risk / sizing** | `apps/risk_manager/main.py::calculate_position_size` | Decides *how much* from live portfolio state; strategies never size |

Flow:

```
Alpaca 1m  ──┬────────────────────────▶ 1m strategies   (random_50_50, smart_technical)
             ├─ resample 5m ──────────▶ 5m strategies   (intraday_momentum_5m)
             └─ resample 1h ──────────▶ 1h strategies   (hourly_trend)
Alpaca 1d  ──────────────────────────▶ 1d strategies   (daily_trend, turtle_breakout)
                          │
                       Signal (symbol, side, confidence, timeframe, source)
                          │
                 Risk manager + portfolio
                 "already 8.5% NVDA, cap is 10% -> buy 1.5% only"
                          │
                     OrderIntent ─▶ Executor ─▶ Alpaca
```

## Timeframes

* Canonical sources: **1m** and **1d** bars from Alpaca (`apps/data_ingestor`).
* Derived: **5m** and **1h** are aggregated from 1m by `lib/resampler.py` (clock-aligned buckets, emitted when the bucket closes). Configure with `RESAMPLE_TIMEFRAMES=5m,1h`.
* Every bar carries `Bar.timeframe`; all timeframes share the `bars` stream. The strategy engine keeps one history per `(symbol, timeframe)`, so series never mix.
* Parse/align helpers: `lib/timeframes.py` (`parse_timeframe("1Hour")`, `bucket_start`, `to_alpaca_timeframe`).

Rough history needed (also what the ingestor backfills):

| Horizon | Bar | Backfill knob | Built-in strategy |
|---|---:|---|---|
| Intraday fast | 1m | `HISTORICAL_DAYS` (default 7) | `smart_technical` |
| Intraday | 5m | derived from 1m | `intraday_momentum_5m` |
| Short term | 1h | derived from 1m | `hourly_trend` |
| Swing | 1d | `DAILY_HISTORY_DAYS` (default 400) | `daily_trend`, `turtle_breakout` |

## Writing a strategy

```python
# apps/strategies/library.py
from lib.strategy_base import Strategy, register
from lib.models import TimeFrame, SignalSide

@register
class MyBreakout(Strategy):
    name = "my_breakout"            # unique; becomes Signal.source and is auto-allowed by the risk manager
    timeframe = TimeFrame.HOUR
    lookback_bars = 60              # analyze() runs only once this many bars exist
    cooldown_seconds = 4 * 3600     # per symbol, measured in bar time (works in replays too)
    signal_expiry_seconds = 3600
    universe = ["NVDA", "AAPL"]     # optional; None = all configured symbols

    def analyze(self, symbol, bars):          # bars: oldest -> newest, all 1h
        closes = [float(b.close) for b in bars]
        if closes[-1] > max(closes[-21:-1]):
            return self.make_signal(symbol, SignalSide.BUY, 0.7, bars, {"reason": "20-bar breakout"})
        return None
```

Select what runs with `ENABLED_STRATEGIES=hourly_trend,daily_trend` (default: all registered).
`confidence` below `min_confidence` (0.5) is dropped by the engine; the risk manager also requires >= 0.5.

## Portfolio-aware sizing (risk manager)

* Equity and open positions are read from Alpaca (cached 60 s / 30 s).
* **BUY**: budget = `RISK_PCT * equity * confidence`, capped so the symbol's total exposure stays under `MAX_POSITION_SIZE * equity` counting what is already held. At the cap the signal is rejected with reason `Position limit reached`.
* **SELL**: exits the held quantity; no position means rejection (no accidental shorting).
* Whole shares only, rounded down. A result of 0 shares rejects the signal (published as `signal_rejected`).

## Live safety knobs

| Env | Default | Effect |
|---|---|---|
| `STRATEGY_MAX_BAR_AGE_SECONDS` | 300 in `.env`, 0 in code | Bars older than this only warm up indicators (no orders from backfill). Use 0 for simulator replays |
| `BUS_BACKEND` | `streams` in `.env` | Durable consumer groups; `pubsub` drops messages published before a consumer is up |
| `ALLOWED_SIGNAL_SOURCES` | all registered strategies + `manual_api` | Override to whitelist explicitly |

## Measuring a strategy: the isolated research engine

This is the supported way to measure a strategy before risking anything. `lib/backtest.py`
(`run_backtest`) is a self-contained long-only engine with **no message bus and no broker**.
It never sends an order. `TRADING_MODE=backtest` (the default, see below) disables every
broker path in the whole system.

How it models execution (declared in the result JSON under `assumptions`):
- Decisions use **closed** bars; a decision on a bar is actionable at that bar's close
  (`available_at`), and fills happen at the **next** bar's open with adverse slippage and
  proportional commission.
- Volume-participation cap per bar; a triggered stop/target becomes a resting market exit that
  completes across later bars if volume caps a fill; stop wins if stop and target are both
  touched in one bar.
- One base timeframe per run (no auto-resampling); each strategy gets an **independent**
  account, plus `cash` and `buy_and_hold` benchmarks.
- Metrics: return, CAGR, max drawdown and its duration, volatility, Sharpe, Sortino, average
  exposure, win rate, expectancy, profit factor, average win/loss. Benchmarks act from bar 1
  while strategies wait for their lookback, so compare each account's `first_fill_timestamp`
  before ranking. A single in-sample pass is **not** predictive evidence (no walk-forward /
  out-of-sample split yet).

CLI (CSV only; `data/sample/TEST.csv` is synthetic):

```
python apps/simulator/main.py --symbols TEST --start 2024-01-01 --end 2026-01-01 \
  --csv data/sample --timeframe 1Day --strategies daily_trend,buy_and_hold \
  --initial-cash 100000 --output out/run.json
```

API (auth-gated research service): `POST /backtest/jobs` (body with `csv_dir`, `symbols`,
`timeframe`, `strategies`, `initial_cash`, optional `risk_params`) then `/start`, poll
`/backtest/jobs/{id}`, read `/results`, download `/download`. The API runs the CLI as a
subprocess; results are validated before being marked complete.

### Research protocol: preregister, measure pure, then ablate

1. **Preregister** the strategy's parameters before looking at results and do not tune them
   afterwards. Trying enough variants will find a great backtest by accident (Bailey et al.,
   "The Probability of Backtest Overfitting").
2. **Measure it pure first**: set `"stop_loss_pct": null, "take_profit_pct": null` in
   `--risk-params`. Protective exits are optional; a tight take-profit is conceptually
   incompatible with a trend-following thesis because it caps the very trends the strategy
   is trying to ride. Without a stop, entries are sized to the `max_position_size` allocation
   ("long or cash"); a repeated BUY at the cap does not accumulate.
3. **Ablate**: re-run the identical data/strategy with protection added, and compare. Report both.
4. **Compare like for like**: benchmarks (`cash`, `buy_and_hold`) act from bar 1 while a
   strategy waits for its lookback. Read each account's `first_fill_timestamp` and re-run the
   benchmark over the strategy's active window before ranking. With `walk_forward_folds`,
   prefer consistency across sub-periods over the headline return.
5. Mind sample size: a monthly-review strategy over three years makes very few independent
   decisions; treat its Sharpe and fold statistics as weak evidence.

Suggested lab order (longest horizon and strongest evidence first):

| # | Strategy | Horizon | Note |
|---|---|---|---|
| 1 | `tsmom_12m_long_only` | months | **Preregistered**: 252 sessions, threshold 0, monthly review. Long or cash, never short. Time-series (absolute) momentum; fits the per-symbol `analyze(symbol, bars)` contract. |
| 2 | `turtle_breakout` | weeks | Donchian 20-day high entry / 10-day low exit. |
| 3 | `daily_trend` | months | SMA50/200 regime + RSI filter. |
| 4+ | hourly, then 5m/1m | hours/minutes | Only after the above; turnover and costs rise steeply. |

Data: 12-month momentum needs 253 daily closes before its first decision, so backfill at
least ~550 calendar days (`DAILY_HISTORY_DAYS`, now the default) and always use split/dividend
**adjusted** bars (`adjustment="all"`); unadjusted data turns a stock split into a fake crash.

### Portfolio (cross-sectional) strategies

`Strategy` answers "is AAPL attractive?" one symbol at a time. A **`PortfolioStrategy`**
(`lib/portfolio_strategy.py`) answers "given everything eligible today, what should the whole
portfolio look like?" and returns a `PortfolioTarget`: symbol → target weight of equity
(sum ≤ 1, remainder cash; long only). The `Strategy` contract is untouched.

```
PortfolioStrategy.target(as_of, histories, universe) -> PortfolioTarget
        ↓  lib/rebalance.py: plan_rebalance (current vs target -> whole-share trades)
   sells first, buys scaled DOWN proportionally to available cash (one atomic decision)
        ↓  executes at the NEXT bar open
```

The backtester runs a portfolio strategy **once per batch** (all symbols of a timestamp), only
on the **last session of each month**, from closed bars. Every target value comes from one
equity snapshot, so results are invariant to the order symbols are listed in (tested, including
when cash is scarce), and there is no lookahead (tested).

Built-ins (`apps/strategies/portfolio_library.py`):

| Name | What | Note |
|---|---|---|
| `xsmom_12_1_long_only` | 12-1 cross-sectional momentum: `P[t-21]/P[t-252] - 1`, rank the universe, hold the **top decile equal-weight**, monthly, long only, no brackets, gross 100% | **Preregistered** (skip 21, lookback 252, top 10%, equal weight, monthly). No absolute-momentum filter yet: that is a different (combined) strategy for a later ablation. |
| `equal_weight_universe` | 1/N over the same eligible universe on the same dates | **Primary benchmark** for xsmom: `R_top_decile − R_equal_weight_universe` isolates the ranking from size-weighting and concentration. SPY is secondary. |

Extra outputs per portfolio account: `rebalances` (holdings, gross exposure, one-way turnover
`½ Σ|w_target − w_pretrade|`), `turnover` summary, `decile_returns` (mean **next-period** return
by momentum decile, D10 = winners), and top-level `comparisons` (excess over the equal-weight
universe). Ask first whether the **decile ladder** has economically sensible shape (high deciles
beating low ones on average); "my portfolio made money" is the weaker question.

**Universe is the real problem.** A fixed list (`ResearchConfig.universe`, `StaticUniverse`) is
survivorship-biased — fine for a smoke test only. For a serious experiment set
`ResearchConfig.universe_csv` (also via `--risk-params '{"universe_csv":"data/sp500/composition.csv"}'`)
to a **point-in-time membership** file; the backtest then only holds names that were actually in
the index on each date, and freezes the file's SHA256 (`universe_sha256`) into the result.

Composition CSV format (`lib/portfolio_strategy.CsvPointInTimeUniverse`), header required:

```
date,symbol            # long  : one row per member per effective date
2016-01-04,AAPL
2016-01-04,MSFT
...
# or wide: date,symbols   with a ; , space or tab separated list per date
```

`members(as_of)` returns the constituents effective on the most recent date ≤ `as_of`
(forward-filled), so a daily snapshot and a sparse change-log both work; dotted tickers like
`BRK.B` load correctly. Getting the data is the remaining step (needs an external source, e.g.
a free Wikipedia+Tiingo reconstruction such as `K0D1Z/sp500-quantitative-dataset` for a first
pass, then a commercial source like EODHD as an independent cross-check). Five symbols do not
test the factor; hundreds do (top decile ≈ 50 names at ≈2% each, so `max_position_size` stops
mattering). Keep long only: momentum crashes come mostly from the short leg.

### TRADING_MODE

| Mode | Meaning |
|---|---|
| `backtest` (default) | Isolated engine only. API starts without Redis; operational routes (`/portfolio`, …) return 503; `lib/execution_safety.verify_mode` and the legacy `HistoricalSimulator` bus replay refuse to run. |
| `paper` | Requires a paper Alpaca account. |
| `live` | Requires `enable_live_trading` and a non-paper account. Not certified. |

### Legacy live-bus replay

`HistoricalSimulator` (the old `apps/simulator/main.py` class that published bars to the shared
bus) is disabled in backtest mode. It is not the measurement path; use the isolated engine above.
