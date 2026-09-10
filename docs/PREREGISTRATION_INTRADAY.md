# Intraday research line — preregistration

This document fixes the hypotheses, exact rules, parameters, data, and success criteria **before**
looking at any result. The strategy code (`apps/strategies/library.py`) and the execution contract
(`lib/backtest.py::run_intraday_account`) were committed on `main` (commits `a1e64d3`, `ad39f60`)
**before** the SPY/QQQ data pull and the experiment run. Parameters below are frozen: they must not
be tuned against results. Negative results are reported, not hidden.

## Data

- **Provider / feed:** Alpaca, **SIP** (consolidated). The account entitlement was detected at
  runtime (`scripts/fetch_intraday_data.py --detect-only`), not assumed. SIP 1-minute depth reaches
  back to ~2016-09; IEX is also available. SIP includes extended hours, so every intraday
  experiment filters to the **regular session (RTH)** before use.
- **Resolution:** one canonical source, **1-minute**. Higher timeframes (5m, 30m, 1h) are derived
  by deterministic **session-aligned** resampling from that 1m source. Resolutions are never mixed
  within an experiment.
- **Symbols:** SPY (primary), QQQ (replication).
- **Window:** the most recent ~3 years of 1m bars (≈2023-09 to 2026-09), ≈292k RTH 1m bars per
  symbol. Exact fetched ranges are recorded in each result's `data_lineage`.
- **Session completeness (data hygiene, added block 1):** a session is used only if it is
  **complete** — its bars span from the 09:30 ET open bar to the calendar close bar (16:00 ET, or
  13:00 ET on an early-close day). The close is taken from the market calendar
  (`lib.market_calendar.session_bounds`), **never** inferred from the last bar that happens to be
  present, so a truncated pull (a first session that starts mid-morning, a last session that ends
  before the close) is **discarded**, not mistaken for a full day. Early closes are handled by the
  calendar. Internal missing minutes are detected and reported as data-quality gaps but do not, on
  their own, disqualify a session. Both symbols are then reduced to the sessions complete for
  **both**, so SPY and QQQ run on exactly the same sessions. Counts of complete / incomplete /
  discarded sessions and the final session range are recorded in each result's
  `session_accounting`.

## Execution contract (all intraday experiments)

Regular session only; long-only; whole shares. Decisions use only **closed, complete** bars; an
entry fills at the **next** bar's open (never the signalling bar); positions are **force-flattened
at the session close** (no overnight). Frictions for the headline net figure: commission 0 bps
(Alpaca US equities), slippage 0.5 bps, half-spread from 1.0 bps spread. A separate 5-point cost
sweep (frictionless → stress) is reported for robustness, not optimization.

## Batch A — `extreme_reversal_1m` (1-minute)

- **Hypothesis:** an extreme one-minute **down** move (a ~1/1000 tail event) partly reverts over
  the next few minutes.
- **Rule:** on intraday 1m log-returns only (overnight gaps excluded), standardise the current
  return against the trailing **390** intraday returns: `z = (r_t − μ) / σ`. **BUY when
  z ≤ −3.09** (the one-sided 0.1% Gaussian tail ≈ 1/1000). Long-only; an extreme up move is ignored.
- **Exit:** time-based, **5 bars** (5 minutes), or the session close, whichever comes first.
- **Frozen parameters:** window 390, z-threshold 3.09, hold 5. No threshold search.

## Batch B — `opening_range_breakout_5m` (5-minute)

- **Hypothesis:** a break above the first 30 minutes' range carries through the session.
- **Rule:** opening range = **09:30–10:00 ET** high (first six 5m bars). After 10:00, the **first**
  bar to **close strictly above the OR high** goes long; **one entry per session**.
- **Exit:** the session close (no time exit, no overnight).
- **Frozen parameters:** OR = first 30 min; one entry/session. No grid search.

## Batch C — `market_intraday_momentum_30m` (30-minute)

- **Hypothesis (Gao, Han, Li & Zhou 2018, "Market Intraday Momentum"):** the sign of the
  prior-close → first-half-hour return predicts the **last** half hour.
- **Rule:** `r_first30 = close(09:30–10:00) / prior_session_close − 1`. If `r_first30 > 0`, be long
  during the last half hour (decision on the second-to-last bar, entry at the last bar's open,
  forced exit at the close). Sign-based; **no threshold**.
- **Two preregistered variants, reported separately:**
  - `market_intraday_momentum_30m` (**long-only**): only the positive-morning leg trades; the
    short leg is skipped. Retained as the earlier experiment, unchanged.
  - `market_intraday_momentum_30m_long_short` (**faithful replication**, added block 2): the
    **same** `_r_first30` signal, but the last-half-hour position takes the **sign** —
    `r_first30 > 0` → long, `r_first30 < 0` → **short**, close at the session end. Shorting is
    enabled in the research engine only (`allow_short`); costs apply symmetrically to both legs.
    This is the more faithful Gao et al. replication. The negative-morning leg is no longer a
    documented deviation but a traded leg, so the long-only vs long-short comparison shows whether
    dropping it destroyed the original effect.

## Batch D — baselines and ablations (measured, not validated)

Pre-existing baselines run for reference on the same SPY/QQQ complete-session data:
`smart_technical` (1m), `intraday_momentum_5m` (5m), `hourly_trend` (1h). They are **not**
intraday-contract strategies (they may hold overnight) and are **not** treated as validated.

- **`smart_technical` now runs the FULL window** (added block 4). Its MACD was reimplemented from
  O(history²) to O(history) in a single forward pass that is mathematically identical
  (proven bit-for-bit against the brute-force oracle in `tests/test_smart_technical_equiv.py`), so
  the earlier truncation to a recent window is removed and it is measured on the same ~3-year
  sample as everything else. The strategy maths is unchanged.
- **`hourly_trend_intraday` (1h, ablation, added block 3):** EXACTLY the `hourly_trend` signal and
  parameters (SMA20/SMA50), but under the intraday no-overnight contract — RTH only, next-bar-open
  execution, an opposing SELL crossover closes the long, forced flatten at the session close. No
  SMA/threshold is re-tuned. Purpose: isolate how much of `hourly_trend`'s result comes from market
  hours versus overnight exposure (`hourly_trend` vs `hourly_trend_intraday`).
- **`intraday_momentum_15m` (15m, EXPLORATORY, added block 5):** a straight temporal translation of
  the `intraday_momentum_5m` baseline that preserves the same real-time horizons
  (SMA 20→7 bars, RSI 14→5 bars, the ~15-min rising reference → 1 bar; cooldown/expiry kept as
  wall-clock minutes). It is **not** a new hypothesis and its result is **not** used to claim
  validation or to pick parameters — it exists to demonstrate the system runs a real strategy end
  to end at 15m.

## Success criteria (item 12), fixed in advance

A strategy is considered promising only if it: (1) shows a signal **before** costs; (2) **survives**
after costs; (3) **persists** across the three contiguous time sub-windows; (4) is not driven by 1–2
extreme days; (5) works on SPY **and** replicates in QQQ; and (6) the return compensates its turnover
and drawdown. Anything short of that is reported as a negative or inconclusive result. No parameter
is tuned to make a strategy pass.
