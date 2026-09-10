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

### TRADING_MODE

| Mode | Meaning |
|---|---|
| `backtest` (default) | Isolated engine only. API starts without Redis; operational routes (`/portfolio`, …) return 503; `lib/execution_safety.verify_mode` and the legacy `HistoricalSimulator` bus replay refuse to run. |
| `paper` | Requires a paper Alpaca account. |
| `live` | Requires `enable_live_trading` and a non-paper account. Not certified. |

### Legacy live-bus replay

`HistoricalSimulator` (the old `apps/simulator/main.py` class that published bars to the shared
bus) is disabled in backtest mode. It is not the measurement path; use the isolated engine above.
