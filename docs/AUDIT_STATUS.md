# Backlog status vs. HANDOVER_2026-09-10

Tracks the handover's pending list. Status: **done** / **partial** / **pending**. Not certified
for real-money trading. The user's goal is a measurable end-to-end simulator before investing;
broker-execution items are deliberately deprioritized until paper/live.

Baseline commits: handover integration `21fa8d3`; engine correctness `77338f0`; API cleanup `94fea7d`.

## Verified (not a backlog item, but the handover left it unverified)
- **done** — Full suite green in the real environment: 183 passed / 21 skipped (WSL, real Redis+streams). The 12 Windows-only failures were environmental (no Redis reachable, empty creds, tmp perms).
- **done** — Real uvicorn **lifespan** research flow end-to-end over HTTP: login → create job → subprocess CLI → CSV → engine → results → download, with measurable per-account metrics. `/portfolio` returns 503 in backtest mode.

## P0 — broker execution / durability / stop (mostly PENDING; not needed until paper/live)
- **done** — Legacy `HistoricalSimulator` bus replay disabled in backtest mode (`77338f0`).
- **done** — `OrderTracker` now rehydrates from the journal **without writing** (`_rehydrate`): accumulated fills survive a crash mid-restore, the FSM is restored to the journalled status, and `orders_submitted` is not re-inflated on restart. Tested (`tests/test_executor_durability.py`). **Pending**: injected fault before/after checkpoint/publish as a stress test.
- **done** — Bracket legs are registered as own orders (`register_legs`), persisted in a `legs` journal table, recovered on restart, and reconciled by the monitor so a protective stop/take-profit exit emits an OrderFill (never missed in PnL). Verified against the real **paper** account (`tests/test_bracket_legs_paper.py`). **Pending**: cross-check the two-leg OCO cancellation on a real fill (needs an open market to fill the entry).
- **done** — A strategic SELL first cancels the symbol's open protective legs (`cancel_bracket_legs`) so the exit is not rejected for shares reserved by the bracket. **Pending**: replace (not just cancel) when only resizing.
- **pending** — Stop: authorized resume endpoint, per-instance/order fresh ACK, race avoidance; don't report "stopped" on a stale ACK.
- **pending** — Submission lock expiry/multi-executor/ambiguous-broker-response tests.
- **done** — `get_existing_order` is now fail-closed: it returns None only on a definitive 404, and raises `IdempotencyCheckError` on any ambiguous failure (network/timeout/5xx after retry) so the intent is left pending instead of risking a duplicate submit. Tested (`tests/test_executor_durability.py`). **Pending**: reconcile a terminal existing order's fills before ACK (currently returns None when it exists-but-unfilled).
- **pending** — Require Streams/real Redis for durable operation (Pub/Sub still selectable).
- **done** — Settings validates risk parameters fail-fast at construction (finite, in range) so a misconfigured `.env` cannot reach the live risk manager/executor. Tested (`tests/test_settings_validation.py`).

## P1 — economic correctness of the historical engine
- **done** — Resting stop persists across volume-capped bars (completes at later bar opens) instead of vanishing or being re-tested (`77338f0`).
- **done** — `available_at` uses a **computed** session calendar (`lib/market_calendar.py`) for any year, ET session close, early-close half-days; non-session dates roll forward (`77338f0`).
- **done** — Daily date contract: date-only daily bars at 00:00 UTC belong to that session (no UTC→NY back-shift) (`77338f0`).
- **done** — Future-independence tested across multiple symbols and symbol ordering; hash invariant to symbol order (`77338f0`, `tests/test_backtest_economics.py`).
- **done** — Metrics added: drawdown duration, avg/max exposure, avg win/loss; per-account `first_fill_timestamp`/`warmup_bars`; explicit warmup-comparability and no-OOS notes (`77338f0`).
- **partial** — Intrabar realism (gaps, simultaneous stop/target, partial fills): stop-first tie-break, entry-bar risk and resting stops modeled; full tick-accurate sequencing is not derivable from OHLC and stays declared as a limitation.
- **pending** — Warmup vs. evaluation window: disclosed via `first_fill_timestamp`, but a shared warmup/evaluation split for like-for-like benchmark comparison is not implemented.
- **partial** — Walk-forward **consistency report** implemented: `walk_forward_folds` scores the same fixed-parameter strategy over N contiguous sub-periods (per-window return/drawdown + aggregate mean/stdev/min/max/fraction-positive). **Pending**: walk-forward *optimization* (there is no fitting step), confidence intervals, dividends/splits/tax ledger, point-in-time universe / survivorship.

## P1 — services / API / auth / UI
- **done** — Retired legacy `/backtest/quick`; download route requires `READ_BACKTEST` (`94fea7d`).
- **partial** — Server lifespan verified over HTTP; **browser/visual** verification of the research UI still not done (cannot run a browser here).
- **pending** — Periodic real reconciliation of the operational snapshot (currently published on order validation; 60s staleness → 503).
- **partial** — Login **rate limiting** done (per-username lockout, 429 + Retry-After; env-tunable); access/refresh token-type separation tested; **user/API-key persistence** done (opt-in via `AUTH_DB_PATH`, atomic write, survives restart, hashes only). **Pending**: secret (AUTH_SECRET_KEY) rotation with a grace window.
- **pending** — Persist PnL marks/config across restarts; per-account/mode journal.
- **done** — Streams `_subscribe_model` adapters now recover a consumer's own un-acked backlog on restart (phase-1 replay from the pending list, then new messages) and drop poison messages instead of redelivering forever; tested against real Redis (`tests/test_streams_recovery.py`). The `consume_with_handler` path (used by the services) already had xautoclaim-based reclaim. **Pending**: cross-consumer (dead-consumer) reclaim inside `_subscribe_model`, and a dead-letter stream rather than log-and-drop.

## P2 — tests / docs / delivery
- **done** — README/QUICK_START/STRATEGIES updated for the isolated engine, CLI and `TRADING_MODE` (this batch).
- **done** — This status document.
- **pending** — Separate demo scripts (that return False without asserting) from real tests; mark external-dependency tests explicitly.
