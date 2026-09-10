"""Isolated deterministic long-only research engine. No message bus or trading API.

OHLC timestamps are interval starts. Decisions use closed bars; fills occur at
the next bar open, with adverse costs and volume limits. Simultaneous stop/target
touches resolve to the stop. Each strategy owns an independent account.
"""
import csv
import hashlib
import json
import math
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
    max_volume_participation: float = Field(default=.01, gt=0, le=1)
    risk_free_rate: float = Field(default=0, ge=-.1, le=1)
    walk_forward_folds: int = Field(default=1, ge=1, le=52)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("Duplicate strategies")
        unknown = set(self.strategies) - {"cash", "buy_and_hold"}
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
        if not symbol.isalnum():
            raise ValueError("CSV symbols must be alphanumeric")
        path = root / f"{symbol}.csv"
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
    return {"return_pct": (ratio - 1) * 100, "max_drawdown_pct": drawdown * 100,
            "max_drawdown_duration_days": dd_seconds / 86400,
            "avg_exposure_pct": mean(exposures) * 100 if exposures else None,
            "max_exposure_pct": max(exposures) * 100 if exposures else None,
            "cagr_pct": (ratio ** (365.25 / days) - 1) * 100 if days >= 365 and ratio > 0 else None,
            "volatility_annualized": vol * math.sqrt(252) if vol else None,
            "sharpe": mean(excess) / vol * math.sqrt(252) if vol else None,
            "sortino": mean(excess) / downside * math.sqrt(252) if downside else None,
            "daily_observations": len(returns), "costs": float(ledger.fees),
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


def run_backtest(bars, config):
    config = ResearchConfig.model_validate(config) if isinstance(config, dict) else config
    validate_data(bars)
    ordered = sorted(bars, key=lambda b: (b.timestamp, b.symbol))
    data_hash = hashlib.sha256(json.dumps([b.model_dump(mode="json") for b in ordered], sort_keys=True).encode()).hexdigest()
    names = list(dict.fromkeys(config.strategies + ["cash", "buy_and_hold"]))
    accounts = {}
    for name in names:
        strategy = create_strategies([name])[0] if name not in ("cash", "buy_and_hold") else None
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
                        price = bar.open * (1 + D(config.slippage_bps / 10000) * (1 if side == "BUY" else -1))
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
                            fee = D(qty) * price * D(config.commission_bps / 10000)
                            ledger.fill(f"{name}-{len(ledger.fills)}", sym, side, qty, price, fee, ts.isoformat())
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
                            price = basis * (1 - D(config.slippage_bps / 10000))
                            ledger.fill(f"{name}-{len(ledger.fills)}", sym, "SELL", qty, price,
                                        D(qty) * price * D(config.commission_bps / 10000), available_at(bar).isoformat())
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
    payload["result_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return payload
