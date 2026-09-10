"""Isolated deterministic long-only research engine. No message bus or trading API.

OHLC timestamps are interval starts. Decisions use closed bars; fills occur at
the next bar open, with adverse costs and volume limits. Simultaneous stop/target
touches resolve to the stop. Each strategy owns an independent account.
"""
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from statistics import mean, stdev
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.models import Bar, TimeFrame
from lib.portfolio import Portfolio, D
from lib.strategy_base import create_strategies
from lib.timeframes import parse_timeframe, timeframe_seconds
from lib.market_calendar import session_close, is_trading_day

NY = ZoneInfo("America/New_York")


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    initial_cash: float = Field(default=100000, gt=0)
    strategies: list[str] = Field(default_factory=lambda: ["daily_trend"], min_length=1)
    seed: int = 42
    max_position_size: float = Field(default=.1, gt=0, le=1)
    max_portfolio_risk: float = Field(default=.8, gt=0, le=1)
    risk_pct: float = Field(default=.01, gt=0, le=1)
    max_daily_loss: float = Field(default=.05, gt=0, le=1)
    # Protective exits are OPTIONAL (null disables). A tight take-profit is incompatible with a
    # trend-following thesis (it caps the very trends the strategy is trying to ride), so
    # measure a strategy pure first, then ablate by adding protection.
    stop_loss_pct: float | None = Field(default=.02, gt=0, lt=1)
    take_profit_pct: float | None = Field(default=.06, gt=0, lt=10)
    slippage_bps: float = Field(default=5, ge=0, le=1000)
    commission_bps: float = Field(default=1, ge=0, le=1000)
    # Explicit half-spread cost paid on each side (bps of notional). Default 0 preserves prior
    # results; set it for 1m/5m research where the spread dominates the cost budget.
    spread_bps: float = Field(default=0, ge=0, le=1000)
    max_volume_participation: float = Field(default=.01, gt=0, le=1)
    risk_free_rate: float = Field(default=0, ge=-.1, le=1)
    walk_forward_folds: int = Field(default=1, ge=1, le=52)
    # Portfolio strategies: eligible symbols (None -> every symbol in the dataset). A static list
    # is survivorship-biased; use it for architectural smoke tests, not as historical evidence.
    universe: list[str] | None = None
    # Point-in-time index membership CSV (date,symbol or date,symbols). When set, the portfolio
    # path uses actual constituents per date instead of a static list (fixes survivorship bias).
    universe_csv: str | None = None

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("Duplicate strategies")
        from lib.portfolio_strategy import is_portfolio_strategy
        unknown = {s for s in self.strategies if s not in ("cash", "buy_and_hold") and not is_portfolio_strategy(s)}
        if unknown:
            create_strategies(sorted(unknown))
        return self


def session_date_of(bar):
    """Trading-session date a bar belongs to (year-agnostic, one clear contract).

    Daily bars stamped at 00:00 UTC (date-only CSV) denote that calendar session directly, so
    they are NOT shifted back a day by a UTC->NY conversion. Any bar carrying an intraday time
    is placed by its New York local date.
    """
    ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
    if bar.timeframe == TimeFrame.DAY and ts.hour == 0 and ts.minute == 0:
        return ts.date()
    return ts.astimezone(NY).date()


def available_at(bar):
    """When a decision made on this (closed) bar becomes actionable: the bar's own close.

    Daily bars resolve to the session close in New York (13:00 on early-close days), rolled to
    the next trading day if the dataset carries a non-session date. Intraday bars resolve to the
    bar-interval end.
    """
    ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
    if bar.timeframe == TimeFrame.DAY:
        d = session_date_of(bar)
        for _ in range(7):
            if is_trading_day(d):
                break
            d = d + timedelta(days=1)
        return datetime.combine(d, session_close(d), tzinfo=NY).astimezone(timezone.utc)
    return ts + timedelta(seconds=timeframe_seconds(bar.timeframe))


def load_csv(directory, symbols, timeframe, start=None, end=None):
    root = Path(directory).resolve()
    start = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
    end = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
    start = start.replace(tzinfo=start.tzinfo or timezone.utc) if start else None
    end = end.replace(tzinfo=end.tzinfo or timezone.utc) if end else None
    if start and end and start >= end:
        raise ValueError("start must precede end (end is exclusive)")
    bars = []
    for symbol in symbols:
        # Accept the same ticker scheme as Bar (letters, digits, dots -> e.g. BRK.B, BF.B),
        # but never anything that could escape the data directory via the filename.
        sym = str(symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9.]+", sym) or ".." in sym:
            raise ValueError(f"Invalid CSV symbol '{symbol}'")
        path = (root / f"{sym}.csv").resolve()
        if root not in path.parents:
            raise ValueError(f"CSV path for '{symbol}' escapes the data directory")
        with path.open(newline="", encoding="utf-8-sig") as source:
            selected = []
            for row in csv.DictReader(source):
                bar = Bar(symbol=symbol, timestamp=row["timestamp"], timeframe=parse_timeframe(timeframe),
                          **{key: row[key] for key in ("open", "high", "low", "close", "volume")})
                if (start is None or bar.timestamp >= start) and (end is None or bar.timestamp < end):
                    selected.append(bar)
            if not selected:
                raise ValueError(f"No data in requested range for {symbol}")
            bars.extend(selected)
    validate_data(bars)
    return bars


def validate_data(bars):
    if not bars:
        raise ValueError("Empty dataset")
    seen = set()
    for bar in bars:
        key = (bar.symbol, bar.timeframe, bar.timestamp)
        if key in seen:
            raise ValueError(f"Duplicate bar: {key}")
        seen.add(key)
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError(f"Invalid OHLC: {key}")
    if len({bar.timeframe for bar in bars}) != 1:
        raise ValueError("A run requires one base timeframe to avoid double execution")


def _fill_price(ref, side, config):
    """Actual fill price: frictionless reference moved adversely by slippage + half the spread."""
    sign = 1 if str(getattr(side, "value", side)).upper() == "BUY" else -1
    friction = D(config.slippage_bps) / 10000 + D(config.spread_bps) / 20000
    return D(ref) * (1 + sign * friction)


def _fill_costs(ref, side, qty, price, config):
    """(commission, slippage_cost, spread_cost) for a fill, all in cash. Slippage and spread are
    attributed against the frictionless reference so gross vs net can be separated."""
    ref, qty, price = D(ref), D(qty), D(price)
    commission = qty * price * D(config.commission_bps) / 10000
    slippage_cost = qty * ref * D(config.slippage_bps) / 10000
    spread_cost = qty * ref * D(config.spread_bps) / 20000
    return commission, slippage_cost, spread_cost


def performance(ledger, curve, config):
    values = [row["equity"] for row in curve]
    peak, drawdown = config.initial_cash, 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    # Longest stretch below a prior equity peak (underwater duration), and exposure profile.
    dd_seconds, peak_dur, peak_ts = 0.0, config.initial_cash, curve[0]["timestamp"]
    for row in curve:
        if row["equity"] >= peak_dur:
            peak_dur, peak_ts = row["equity"], row["timestamp"]
        else:
            dd_seconds = max(dd_seconds, (datetime.fromisoformat(row["timestamp"]) - datetime.fromisoformat(peak_ts)).total_seconds())
    exposures = [row["positions_value"] / row["equity"] for row in curve if row["equity"] > 0]
    daily = {}
    for row in curve:
        daily[row["timestamp"][:10]] = row["equity"]
    previous = config.initial_cash
    returns = []
    for value in daily.values():
        returns.append(value / previous - 1)
        previous = value
    vol = stdev(returns) if len(returns) > 1 else None
    excess = [r - ((1 + config.risk_free_rate) ** (1 / 252) - 1) for r in returns]
    downside = math.sqrt(mean([min(r, 0) ** 2 for r in excess])) if excess else 0
    days = (datetime.fromisoformat(curve[-1]["timestamp"]) - datetime.fromisoformat(curve[0]["timestamp"])).total_seconds() / 86400
    ratio = float(ledger.equity / ledger.initial_cash)
    net_return_pct = (ratio - 1) * 100
    commission = float(ledger.fees)
    slippage = float(ledger.slippage_cost)
    spread = float(ledger.spread_cost)
    total_costs = commission + slippage + spread
    cost_drag_pct = total_costs / config.initial_cash * 100
    # Gross = net with the frictional costs added back (an attribution, not a re-simulation):
    # net_equity = gross_equity - costs, so gross_return - net_return = costs / initial_cash.
    gross_return_pct = net_return_pct + cost_drag_pct
    return {"return_pct": net_return_pct, "net_return_pct": net_return_pct,
            "gross_return_pct": gross_return_pct, "max_drawdown_pct": drawdown * 100,
            "max_drawdown_duration_days": dd_seconds / 86400,
            "avg_exposure_pct": mean(exposures) * 100 if exposures else None,
            "max_exposure_pct": max(exposures) * 100 if exposures else None,
            "cagr_pct": (ratio ** (365.25 / days) - 1) * 100 if days >= 365 and ratio > 0 else None,
            "volatility_annualized": vol * math.sqrt(252) if vol else None,
            "sharpe": mean(excess) / vol * math.sqrt(252) if vol else None,
            "sortino": mean(excess) / downside * math.sqrt(252) if downside else None,
            "daily_observations": len(returns),
            "costs": {"commission": commission, "slippage": slippage, "spread": spread,
                      "total": total_costs, "cost_drag_pct": cost_drag_pct,
                      "gross_edge_kept_pct": (net_return_pct / gross_return_pct * 100)
                      if gross_return_pct not in (0, None) else None},
            "turnover": sum(f["quantity"] * f["price"] for f in ledger.fills) / config.initial_cash,
            "trades": ledger.trade_stats(), "annualization": "252 sessions; daily close returns",
            "undefined_metrics": "null for insufficient history or zero denominator; CAGR requires >=365 days"}


def walk_forward_report(curve, folds):
    """Out-of-sample consistency: the same fixed-parameter strategy scored over `folds`
    contiguous, equal-length sub-periods of the equity curve. Strategies here take no fitting
    step, so this is not walk-forward *optimization*; it shows whether results hold across time
    or hinge on one window. Returns None when there is too little history to split.
    """
    n = len(curve)
    if folds < 2 or n < folds * 2:
        return None
    size = n // folds
    windows, rets = [], []
    for k in range(folds):
        lo, hi = k * size, ((k + 1) * size if k < folds - 1 else n)
        seg = curve[lo:hi]
        start_eq, end_eq = seg[0]["equity"], seg[-1]["equity"]
        r = (end_eq / start_eq - 1) if start_eq > 0 else None
        peak, dd = start_eq, 0.0
        for row in seg:
            peak = max(peak, row["equity"])
            dd = max(dd, 1 - row["equity"] / peak) if peak > 0 else dd
        windows.append({"from": seg[0]["timestamp"], "to": seg[-1]["timestamp"],
                        "return_pct": r * 100 if r is not None else None,
                        "max_drawdown_pct": dd * 100})
        if r is not None:
            rets.append(r)
    aggregate = {"folds": folds,
                 "mean_return_pct": mean(rets) * 100 if rets else None,
                 "stdev_return_pct": stdev(rets) * 100 if len(rets) > 1 else None,
                 "min_return_pct": min(rets) * 100 if rets else None,
                 "max_return_pct": max(rets) * 100 if rets else None,
                 "fraction_positive": sum(r > 0 for r in rets) / len(rets) if rets else None}
    return {"windows": windows, "aggregate": aggregate,
            "note": "same fixed-parameter strategy over contiguous sub-periods; consistency "
                    "across windows matters more than any single window, and this is still "
                    "in-sample selection unless the strategy was chosen before seeing this data"}


def run_portfolio_account(name, ordered, config):
    """Second decision path: a PortfolioStrategy decides ONCE per batch (all symbols of a
    timestamp), only on the last session of each month, from closed bars; the resulting
    rebalance executes at the NEXT bar open, sells first, buys scaled proportionally to the cash
    actually available. Everything is derived from one equity snapshot, so results do not depend
    on the order symbols are listed in.
    """
    from lib.portfolio_strategy import create_portfolio_strategy, StaticUniverse, CsvPointInTimeUniverse
    from lib.rebalance import plan_rebalance, one_way_turnover
    strategy = create_portfolio_strategy(name)
    if strategy.timeframe != ordered[0].timeframe:
        raise ValueError(f"{name} requires {strategy.timeframe.value}; supply matching closed bars")
    symbols = sorted({b.symbol for b in ordered})
    universe = CsvPointInTimeUniverse(config.universe_csv) if config.universe_csv \
        else StaticUniverse(config.universe or symbols)
    ledger = Portfolio(config.initial_cash)
    slip, fee_rate = D(config.slippage_bps / 10000), D(config.commission_bps / 10000)
    buy_cost_factor = (1 + slip + D(config.spread_bps) / 20000) * (1 + fee_rate)

    batches = [(ts, list(batch)) for ts, batch in groupby(ordered, key=lambda b: b.timestamp)]
    sessions = [session_date_of(batch[0]) for _, batch in batches]
    decide_at = {i for i in range(len(batches) - 1)
                 if (sessions[i].year, sessions[i].month) != (sessions[i + 1].year, sessions[i + 1].month)}

    history = defaultdict(list)
    pending = {}          # symbol -> {"side","quantity","available","expires"}
    curve, rebalances, rejections = [], [], []
    decile_buckets = defaultdict(list)   # decile -> realized next-period returns
    prior_deciles = {}                   # symbol -> (decile, price at decision)

    for i, (ts, batch) in enumerate(batches):
        for bar in batch:
            ledger.mark(bar.symbol, bar.open)
        open_px = {b.symbol: b.open for b in batch}
        vol_left = {b.symbol: int(b.volume * config.max_volume_participation) for b in batch}

        # ---- execute last decision at this bar's OPEN: sells first, then scaled buys ----
        def _fill(sym, side, qty, bar_open):
            price = _fill_price(bar_open, side, config)
            fee, slc, spc = _fill_costs(bar_open, side, qty, price, config)
            ledger.fill(f"{name}-{len(ledger.fills)}", sym, side, qty, price, fee, ts.isoformat(), slc, spc)
            vol_left[sym] -= qty

        due = {s: o for s, o in pending.items() if s in open_px and ts >= o["available"]}
        for s, o in list(due.items()):
            if ts > o["expires"]:
                rejections.append({"symbol": s, "timestamp": ts.isoformat(), "reason": "expired"})
                pending.pop(s); due.pop(s)
        for s in sorted(k for k, o in due.items() if o["side"] == "SELL"):
            held = int(ledger.positions.get(s, D(0)))
            qty = min(due[s]["quantity"], held, vol_left[s])
            if qty > 0:
                _fill(s, "SELL", qty, open_px[s])
            remaining = due[s]["quantity"] - qty
            if remaining > 0 and held - qty > 0:
                pending[s] = {**due[s], "quantity": remaining}
            else:
                pending.pop(s, None)
        buys = {s: min(o["quantity"], vol_left[s]) for s, o in due.items() if o["side"] == "BUY"}
        need = sum((D(q) * open_px[s] * buy_cost_factor for s, q in buys.items()), D(0))
        ratio = (max(D(0), ledger.cash) / need) if need > ledger.cash and need > 0 else D(1)
        for s in sorted(buys):
            qty = int((D(buys[s]) * ratio).to_integral_value(rounding="ROUND_DOWN"))
            if qty > 0:
                _fill(s, "BUY", qty, open_px[s])
            remaining = due[s]["quantity"] - qty
            if remaining > 0 and ratio == 1 and vol_left[s] <= 0:
                pending[s] = {**due[s], "quantity": remaining}   # only volume-capped remainder carries
            else:
                pending.pop(s, None)

        for bar in batch:
            ledger.mark(bar.symbol, bar.close)
            history[bar.symbol].append(bar)
            if len(history[bar.symbol]) > strategy.max_history:
                history[bar.symbol] = history[bar.symbol][-strategy.max_history:]

        # ---- decide (last session of the month) from CLOSED bars; executes next open ----
        members = set(universe.members(ts)) if i in decide_at else set()
        eligible = sorted(s for s in symbols if s in members and len(history[s]) >= strategy.lookback_bars)
        # A month-end with nothing eligible (still in warmup) is not a decision: no plan, no record.
        if i in decide_at and eligible:
            at = available_at(batch[0])
            close_px = {s: history[s][-1].close for s in eligible}
            # Realize the previous decision's decile returns (diagnostic, ex post).
            for s, (dec, p0) in list(prior_deciles.items()):
                if s in close_px and p0 > 0:
                    decile_buckets[dec].append(float(close_px[s] / p0 - 1))
            prior_deciles = {}
            target = strategy.target(ts, {s: history[s] for s in eligible}, eligible)
            scores = target.metadata.get("scores") or {}
            if scores:
                ranked = sorted(scores, key=lambda s: (-scores[s], s))
                n = len(ranked)
                for r, s in enumerate(ranked):
                    dec = 10 - min(9, (r * 10) // n)          # D10 = winners ... D1 = losers
                    prior_deciles[s] = (dec, close_px[s])
            plan = plan_rebalance(ledger.equity, ledger.cash, close_px, ledger.positions,
                                  target.weights, buy_cost_factor)
            for t in plan.trades:
                pending[t.symbol] = {"side": t.side, "quantity": t.quantity, "available": at,
                                     "expires": at + timedelta(seconds=4 * 86400)}
            rebalances.append({"timestamp": at.isoformat(), "n_eligible": len(eligible),
                               "n_holdings": len(target.weights), "gross_exposure": float(target.gross_exposure),
                               "turnover_one_way": float(plan.turnover_one_way), "scaled_buys": plan.scaled_buys,
                               "holdings": {s: float(w) for s, w in sorted(target.weights.items())}})
        curve.append(ledger.snapshot(max(available_at(b) for b in batch).isoformat()))

    turnovers = [r["turnover_one_way"] for r in rebalances]
    return {"metrics": performance(ledger, curve, config), "equity_curve": curve,
            "fills": ledger.fills, "rejections": rejections, "rebalances": rebalances,
            "turnover": {"rebalances": len(turnovers),
                         "mean_one_way": mean(turnovers) if turnovers else None,
                         "total_one_way": sum(turnovers) if turnovers else 0.0},
            "decile_returns": {f"D{d}": {"mean_next_period_return_pct": mean(v) * 100, "n": len(v)}
                               for d, v in sorted(decile_buckets.items()) if v},
            "final_portfolio": ledger.snapshot(curve[-1]["timestamp"]),
            "warmup_bars": strategy.lookback_bars,
            "first_fill_timestamp": ledger.fills[0]["timestamp"] if ledger.fills else None,
            "walk_forward": walk_forward_report(curve, config.walk_forward_folds),
            "unfilled_at_end": len(pending), "kind": "portfolio"}


def _intraday_fill(ledger, name, sym, side, qty, ref, config, ts):
    price = _fill_price(ref, side, config)
    fee, slc, spc = _fill_costs(ref, side, qty, price, config)
    ledger.fill(f"{name}-{len(ledger.fills)}", sym, side, qty, price, fee, ts.isoformat(), slc, spc)
    return price


def run_intraday_account(name, ordered, config, strategy):
    """Dedicated intraday execution path with a first-class time-based / session-close exit
    contract. Regular trading hours only (the input bars should already be RTH). Rules:
      - decisions use only CLOSED, COMPLETE bars; never the same bar before its close;
      - an entry signal fills at the NEXT bar's open (never the signalling bar);
      - a position exits after Signal.hold_bars bars (at that bar's open), and is force-flattened
        at the session's last bar CLOSE (no overnight);
      - per-session strategy state is reset via strategy.on_session_start at each session's open.
    Long-only; whole shares; sized to max_position_size at decision time.
    """
    if strategy.timeframe != ordered[0].timeframe:
        raise ValueError(f"{name} requires {strategy.timeframe.value}; supply matching closed bars")
    ledger = Portfolio(config.initial_cash)
    symbols = sorted({b.symbol for b in ordered})
    buy_cost_factor = (1 + D(config.slippage_bps) / 10000 + D(config.spread_bps) / 20000) * (1 + D(config.commission_bps) / 10000)

    # last bar timestamp of each (symbol, session) -> where a forced close must happen
    last_of_session = defaultdict(dict)
    for b in ordered:
        sd = session_date_of(b)
        if b.timestamp > last_of_session[b.symbol].get(sd, b.timestamp - timedelta(seconds=1)):
            last_of_session[b.symbol][sd] = b.timestamp

    hist = defaultdict(list)
    st = {s: {"session": None, "seq": 0, "entry_seq": None, "hold": None, "pending": None,
              "entry_ts": None, "entry_px": None} for s in symbols}
    curve, signals, rejections, trades = [], [], [], []

    def _open_trade(sym, price, ts):
        st[sym]["entry_ts"], st[sym]["entry_px"] = ts, float(price)

    def _close_trade(sym, price, ts, reason, exit_ts=None):
        # exit_ts lets a forced session-close record the exit at the bar's CLOSE (bar start + one
        # timeframe) rather than its start, so a position held for the final bar counts as one bar
        # of holding instead of zero. Time-based exits fill at the next bar's OPEN, so they keep
        # ts (the bar start) unchanged.
        s = st[sym]
        xt = exit_ts if exit_ts is not None else ts
        if s["entry_ts"] is not None:
            trades.append({"symbol": sym, "entry": s["entry_ts"].isoformat(), "exit": xt.isoformat(),
                           "holding_seconds": (xt - s["entry_ts"]).total_seconds(),
                           "entry_price": s["entry_px"], "exit_price": float(price), "exit_reason": reason})
        s["entry_ts"] = s["entry_px"] = s["entry_seq"] = s["hold"] = None

    for ts, batch in groupby(ordered, key=lambda b: b.timestamp):
        batch = list(batch)
        for bar in batch:
            ledger.mark(bar.symbol, bar.open)
        for bar in batch:
            sym = bar.symbol
            s = st[sym]
            sd = session_date_of(bar)
            is_last = last_of_session[sym][sd] == bar.timestamp
            vol_cap = int(bar.volume * config.max_volume_participation)

            if s["session"] != sd:                      # new session: reset per-session state
                s.update(session=sd, seq=0, entry_seq=None, hold=None, pending=None)
                strategy.on_session_start(sym)
            else:
                s["seq"] += 1
            hist[sym].append(bar)
            if len(hist[sym]) > strategy.max_history:
                hist[sym] = hist[sym][-strategy.max_history:]

            held = int(ledger.positions.get(sym, D(0)))
            # (1) time-based exit at THIS bar's open
            if held > 0 and s["entry_seq"] is not None and s["hold"] is not None and (s["seq"] - s["entry_seq"]) >= s["hold"]:
                qty = min(held, vol_cap)
                if qty > 0:
                    px = _intraday_fill(ledger, name, sym, "SELL", qty, bar.open, config, ts)
                    vol_cap -= qty
                    if int(ledger.positions.get(sym, D(0))) <= 0:
                        _close_trade(sym, px, ts, "time")
                held = int(ledger.positions.get(sym, D(0)))
            # (2) pending entry at THIS bar's open (only if flat and not the last bar of the
            #     session, unless the strategy explicitly allows a one-bar last-bar hold)
            allow_last = getattr(strategy, "allow_last_bar_entry", False)
            if s["pending"] is not None:
                if held <= 0 and (not is_last or allow_last):
                    qty = min(s["pending"]["qty"], vol_cap)
                    if qty > 0:
                        px = _intraday_fill(ledger, name, sym, "BUY", qty, bar.open, config, ts)
                        vol_cap -= qty
                        s["entry_seq"], s["hold"] = s["seq"], s["pending"]["hold"]
                        _open_trade(sym, px, ts)
                        held = int(ledger.positions.get(sym, D(0)))
                s["pending"] = None
            # (3) forced session-close flatten at THIS bar's CLOSE (no overnight; full size)
            if held > 0 and is_last and strategy.exit_at_session_close:
                px = _intraday_fill(ledger, name, sym, "SELL", held, bar.close, config, ts)
                _close_trade(sym, px, ts, "session_close",
                             exit_ts=ts + timedelta(seconds=timeframe_seconds(strategy.timeframe)))
                held = 0
            # (4) decision on this CLOSED, COMPLETE bar (flat, not the last bar)
            if getattr(bar, "is_complete", True) and held <= 0 and s["pending"] is None and not is_last:
                signal = strategy.analyze(sym, hist[sym])
                if signal is not None and str(getattr(signal.side, "value", signal.side)).upper() == "BUY" \
                        and float(signal.confidence) >= strategy.min_confidence:
                    budget = min(ledger.equity * D(config.max_position_size), max(D(0), ledger.cash))
                    qty = int(budget / (bar.close * buy_cost_factor)) if bar.close > 0 else 0
                    if qty > 0:
                        s["pending"] = {"qty": qty, "hold": signal.hold_bars}
                        at = available_at(bar)
                        signals.append({"symbol": sym, "side": "BUY", "timestamp": at.isoformat(),
                                        "quantity_requested": qty, "hold_bars": signal.hold_bars})
                    else:
                        rejections.append({"symbol": sym, "timestamp": ts.isoformat(), "reason": "size_zero"})
            ledger.mark(sym, bar.close)
        curve.append(ledger.snapshot(max(b.timestamp for b in batch).isoformat()))

    metrics = performance(ledger, curve, config)
    holds = [t["holding_seconds"] for t in trades]
    n_sessions = len({session_date_of(b) for b in ordered})
    metrics["intraday"] = {
        "trades": len(trades), "trades_per_day": len(trades) / n_sessions if n_sessions else None,
        "sessions": n_sessions,
        "avg_holding_seconds": mean(holds) if holds else None,
        "median_holding_seconds": (sorted(holds)[len(holds) // 2]) if holds else None,
        "overnight_positions": 0,   # forced-close guarantees flat at each session end
        "pnl_per_unit_turnover": (metrics["net_return_pct"] / (metrics["turnover"] * 100))
        if metrics["turnover"] else None,
    }
    return {"metrics": metrics, "equity_curve": curve, "fills": ledger.fills, "signals": signals,
            "rejections": rejections, "trades": trades,
            "final_portfolio": ledger.snapshot(curve[-1]["timestamp"]),
            "warmup_bars": strategy.lookback_bars,
            "first_fill_timestamp": ledger.fills[0]["timestamp"] if ledger.fills else None,
            "walk_forward": walk_forward_report(curve, config.walk_forward_folds),
            "kind": "intraday"}


def run_backtest(bars, config):
    config = ResearchConfig.model_validate(config) if isinstance(config, dict) else config
    validate_data(bars)
    ordered = sorted(bars, key=lambda b: (b.timestamp, b.symbol))
    data_hash = hashlib.sha256(json.dumps([b.model_dump(mode="json") for b in ordered], sort_keys=True).encode()).hexdigest()
    names = list(dict.fromkeys(config.strategies + ["cash", "buy_and_hold"]))
    accounts = {}
    from lib.portfolio_strategy import is_portfolio_strategy
    for name in names:
        if is_portfolio_strategy(name):
            accounts[name] = run_portfolio_account(name, ordered, config)
            continue
        strategy = create_strategies([name])[0] if name not in ("cash", "buy_and_hold") else None
        if strategy is not None and getattr(strategy, "intraday", False):
            strategy.set_seed(config.seed)
            accounts[name] = run_intraday_account(name, ordered, config, strategy)
            continue
        if strategy:
            strategy.set_seed(config.seed)
            if strategy.timeframe != ordered[0].timeframe:
                raise ValueError(f"{name} requires {strategy.timeframe.value}; supply matching closed bars")
        ledger = Portfolio(config.initial_cash)
        history, cooldown, pending, brackets, armed_exit = defaultdict(list), {}, {}, {}, {}
        curve, signals, rejections = [], [], []
        day, day_start, day_halted = None, ledger.equity, False
        symbols = sorted({b.symbol for b in bars})
        purchased = set()
        for ts, batch in groupby(ordered, key=lambda b: b.timestamp):
            batch = list(batch)
            session = session_date_of(batch[0])
            if session != day:
                day, day_start, day_halted = session, ledger.equity, False
            for bar in batch:
                ledger.mark(bar.symbol, bar.open)
            day_halted |= ledger.equity <= day_start * (1 - D(config.max_daily_loss))
            for bar in batch:
                sym = bar.symbol
                volume_left = int(bar.volume * config.max_volume_participation)
                order = pending.pop(sym, None)
                if order and ts >= order["available"]:
                    side, wanted = order["side"], order["quantity"]
                    if ts > order["expires"]:
                        rejections.append({"symbol": sym, "timestamp": ts.isoformat(), "reason": "expired"})
                    elif side == "BUY" and day_halted:
                        rejections.append({"symbol": sym, "timestamp": ts.isoformat(), "reason": "daily_loss"})
                    else:
                        price = _fill_price(bar.open, side, config)
                        held = ledger.positions.get(sym, D(0))
                        qty = min(wanted, volume_left)
                        if side == "BUY":
                            equity = ledger.equity
                            cap = config.max_position_size if strategy else 1 / len(symbols)
                            room = min(equity * D(cap) - held * price,
                                       equity * D(config.max_portfolio_risk if strategy else 1) - (equity - ledger.cash))
                            qty = min(qty, int(max(D(0), room) / price),
                                      int(max(D(0), ledger.cash) / (price * (1 + D(config.commission_bps / 10000)))))
                        else:
                            qty = min(qty, int(held))
                        if qty > 0:
                            fee, slc, spc = _fill_costs(bar.open, side, qty, price, config)
                            ledger.fill(f"{name}-{len(ledger.fills)}", sym, side, qty, price, fee, ts.isoformat(), slc, spc)
                            volume_left -= qty
                            if side == "BUY" and strategy and (config.stop_loss_pct or config.take_profit_pct):
                                cost = ledger.avg_cost[sym]
                                brackets[sym] = (
                                    cost * (1 - D(config.stop_loss_pct)) if config.stop_loss_pct else None,
                                    cost * (1 + D(config.take_profit_pct)) if config.take_profit_pct else None)
                            if side == "BUY":
                                purchased.add(sym)
                            if qty < wanted:
                                pending[sym] = {**order, "quantity": wanted - qty}
                elif order:
                    pending[sym] = order
                # Protective exits, including entry-bar risk. A triggered stop/target becomes a
                # resting market exit: if volume caps the fill the remainder stays armed and
                # completes on later bars at their open (gap-through), rather than being re-tested
                # against a later bar that may no longer touch the level.
                held = ledger.positions.get(sym, D(0))
                basis = None
                if held > 0:
                    if sym in armed_exit:
                        basis = bar.open
                    elif sym in brackets:
                        stop, target = brackets[sym]
                        if stop is not None and bar.low <= stop:
                            basis, armed_exit[sym] = min(bar.open, stop), "stop"
                        elif target is not None and bar.high >= target:
                            basis, armed_exit[sym] = max(bar.open, target), "target"
                    if basis is not None:
                        qty = min(int(held), volume_left)
                        if qty:
                            price = _fill_price(basis, "SELL", config)
                            fee, slc, spc = _fill_costs(basis, "SELL", qty, price, config)
                            ledger.fill(f"{name}-{len(ledger.fills)}", sym, "SELL", qty, price, fee,
                                        available_at(bar).isoformat(), slc, spc)
                            volume_left -= qty
                        if ledger.positions.get(sym, D(0)) <= 0:
                            armed_exit.pop(sym, None)
                            brackets.pop(sym, None)
                        pending.pop(sym, None)
                ledger.mark(sym, bar.close)
            day_halted |= ledger.equity <= day_start * (1 - D(config.max_daily_loss))
            for bar in batch:
                sym = bar.symbol
                history[sym].append(bar)
                signal = None
                at = available_at(bar)
                if strategy:
                    history[sym] = history[sym][-max(strategy.lookback_bars, strategy.max_history):]
                    if len(history[sym]) >= strategy.lookback_bars and (sym not in cooldown or (at - cooldown[sym]).total_seconds() >= strategy.cooldown_seconds):
                        signal = strategy.analyze(sym, history[sym])
                        if signal and float(signal.confidence) < strategy.min_confidence:
                            signal = None
                elif name == "buy_and_hold" and sym not in purchased and sym not in pending:
                    signal = type("Buy", (), {"side": "BUY"})()
                if signal and sym not in pending:
                    side = str(getattr(signal.side, "value", signal.side)).upper()
                    if side == "BUY":
                        if not strategy:
                            qty = int(ledger.equity / len(symbols) / bar.close)
                        elif config.stop_loss_pct:
                            # Risk-per-trade sizing: risk budget / distance to the stop.
                            qty = int(ledger.equity * D(config.risk_pct) / (bar.close * D(config.stop_loss_pct)))
                        else:
                            # No stop -> "long or cash": size to the target allocation cap. A repeated
                            # BUY while already at the cap finds no room and does not accumulate.
                            qty = int(ledger.equity * D(config.max_position_size) / bar.close)
                    else:
                        qty = int(ledger.positions.get(sym, D(0)))
                    if qty > 0:
                        expiry = max(86400 * 4, strategy.signal_expiry_seconds or 0) if strategy and strategy.timeframe == TimeFrame.DAY else (strategy.signal_expiry_seconds if strategy else 86400 * 4)
                        pending[sym] = {"side": side, "quantity": qty, "available": at, "expires": at + timedelta(seconds=expiry)}
                        cooldown[sym] = at
                        signals.append({"symbol": sym, "side": side, "timestamp": at.isoformat(), "quantity_requested": qty})
            curve.append(ledger.snapshot(max(available_at(b) for b in batch).isoformat()))
        accounts[name] = {"metrics": performance(ledger, curve, config), "equity_curve": curve,
                          "fills": ledger.fills, "signals": signals, "rejections": rejections,
                          "final_portfolio": ledger.snapshot(curve[-1]["timestamp"]),
                          "warmup_bars": strategy.lookback_bars if strategy else 0,
                          "first_fill_timestamp": ledger.fills[0]["timestamp"] if ledger.fills else None,
                          "walk_forward": walk_forward_report(curve, config.walk_forward_folds),
                          "unfilled_at_end": len(pending)}
    payload = {"schema_version": 1, "mode": "backtest", "config": config.model_dump(),
               "data_sha256": data_hash, "bars_count": len(ordered), "accounts": accounts,
               "coverage": {s: sum(b.symbol == s for b in bars) for s in sorted({b.symbol for b in bars})},
               "assumptions": ["Independent long-only accounts; whole shares; no leverage", "Next bar open with adverse slippage and proportional fees",
                               "Volume participation caps; stop first if both stop/target touched; no forced final liquidation",
                               "Prices supplied by dataset; no separate dividends/taxes or corporate action ledger",
                               "No survivorship correction; validate the historical universe externally",
                               "Benchmarks (cash, buy_and_hold) act from the first bar while strategies wait for "
                               "their lookback, so per-account returns may span different windows; compare "
                               "first_fill_timestamp before ranking",
                               "Optional walk_forward_folds reports per-sub-period consistency, but strategies take "
                               "no fitting step and selection is still in-sample unless chosen before seeing this data",
                               "OHLC cannot establish intrabar execution sequence; results are hypothetical"]}
    # Excess of each portfolio strategy over the equal-weight universe benchmark (same stocks,
    # same dates): isolates the value of the ranking from size-weighting and concentration.
    if "equal_weight_universe" in accounts:
        ew = accounts["equal_weight_universe"]["metrics"]["return_pct"]
        payload["comparisons"] = {n: {"excess_return_pct_over_equal_weight_universe": a["metrics"]["return_pct"] - ew}
                                  for n, a in accounts.items() if a.get("kind") == "portfolio" and n != "equal_weight_universe"}
    # Freeze the point-in-time membership too, so a portfolio result is fully reproducible.
    if config.universe_csv:
        payload["universe_sha256"] = hashlib.sha256(Path(config.universe_csv).read_bytes()).hexdigest()
    payload["result_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return payload


COST_SCENARIOS = [
    {"label": "frictionless", "slippage_bps": 0, "commission_bps": 0, "spread_bps": 0},
    {"label": "low", "slippage_bps": 1, "commission_bps": 0.5, "spread_bps": 1},
    {"label": "moderate", "slippage_bps": 3, "commission_bps": 1, "spread_bps": 3},
    {"label": "high", "slippage_bps": 5, "commission_bps": 1, "spread_bps": 8},
    {"label": "stress", "slippage_bps": 10, "commission_bps": 2, "spread_bps": 15},
]


def cost_sensitivity(bars, config, scenarios=None):
    """Descriptive cost robustness: re-run the SAME strategy/data under predefined cost scenarios
    and report net/gross return and cost drag per account. This is analysis, NOT optimization —
    the strategy is never changed to fit the costs. Especially important for 1m/5m horizons where
    the spread dominates and can turn a positive gross edge negative net."""
    config = ResearchConfig.model_validate(config) if isinstance(config, dict) else config
    scenarios = scenarios or COST_SCENARIOS
    out = []
    for sc in scenarios:
        cfg = config.model_copy(update={k: sc[k] for k in ("slippage_bps", "commission_bps", "spread_bps") if k in sc})
        result = run_backtest(bars, cfg)
        accounts = {name: {"net_return_pct": a["metrics"]["net_return_pct"],
                           "gross_return_pct": a["metrics"]["gross_return_pct"],
                           "cost_drag_pct": a["metrics"]["costs"]["cost_drag_pct"],
                           "trades": a["metrics"]["trades"]["total"]}
                    for name, a in result["accounts"].items()}
        out.append({"scenario": sc, "accounts": accounts})
    return out
