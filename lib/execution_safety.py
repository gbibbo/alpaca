"""Fail-closed account checks at the final broker boundary."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus


def verify_mode(settings):
    if settings.trading_mode == "backtest":
        raise RuntimeError("Broker execution disabled in backtest mode")
    if settings.trading_mode == "live" and (not settings.enable_live_trading or settings.is_paper_trading):
        raise RuntimeError("Live mode requires explicit enable_live_trading and live account configuration")
    if settings.trading_mode == "paper" and not settings.is_paper_trading:
        raise RuntimeError("Paper mode requires a paper account")


def check_order(client, redis, settings, intent):
    """Called under a shared account submission lock. Reserve all open buy orders.

    Pending sells conservatively block another exit; bracket legs reserve shares.
    Unknown state, market order reservations, shorts or non-finite values block entry.
    """
    verify_mode(settings)
    if redis.get("trading:emergency_stop"):
        raise RuntimeError("Emergency stop is active")
    account = client.get_account()
    positions = client.get_all_positions()
    orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False))
    equity, cash = Decimal(str(account.equity)), Decimal(str(account.cash))
    buying_power = Decimal(str(account.buying_power))
    if not all(x.is_finite() for x in (equity, cash, buying_power)) or equity <= 0:
        raise RuntimeError("Invalid account snapshot")
    snapshot = {"timestamp": datetime.now(timezone.utc).isoformat(), "total_value": float(equity),
                "cash": float(cash), "buying_power": float(buying_power),
                "positions": [{"symbol": p.symbol, "quantity": float(p.qty), "market_value": float(p.market_value)} for p in positions]}
    redis.set("trading:portfolio", json.dumps(snapshot))
    side = intent.side.value.upper()
    held = next((Decimal(str(p.qty)) for p in positions if p.symbol == intent.symbol), Decimal(0))
    if side == "SELL":
        pending_sell = sum((Decimal(str(o.qty)) - Decimal(str(o.filled_qty or 0)) for o in orders
                            if o.symbol == intent.symbol and str(getattr(o.side, "value", o.side)).lower() == "sell"), Decimal(0))
        if intent.quantity > max(Decimal(0), held - pending_sell):
            raise RuntimeError("Insufficient unreserved shares; pending exits must settle first")
        return
    previous_equity = Decimal(str(account.last_equity))
    if previous_equity <= 0 or equity <= previous_equity * (1 - Decimal(str(settings.max_daily_loss))):
        raise RuntimeError("Daily loss limit or unavailable day-start equity")
    if not intent.price or not intent.stop_loss or not intent.take_profit:
        raise RuntimeError("Entry requires a price and protective exits")
    if not intent.stop_loss < intent.price < intent.take_profit:
        raise RuntimeError("Invalid protective exit prices")
    pending_value = Decimal(0)
    pending_symbol = Decimal(0)
    for order in orders:
        if str(getattr(order.side, "value", order.side)).lower() == "buy":
            if not order.limit_price:
                raise RuntimeError("Cannot reserve an unbounded open buy order")
            value = (Decimal(str(order.qty)) - Decimal(str(order.filled_qty or 0))) * Decimal(str(order.limit_price))
            pending_value += value
            if order.symbol == intent.symbol:
                pending_symbol += value
    exposure = sum((abs(Decimal(str(p.market_value))) for p in positions), Decimal(0))
    symbol_value = sum((abs(Decimal(str(p.market_value))) for p in positions if p.symbol == intent.symbol), Decimal(0))
    value = intent.quantity * intent.price * (1 + Decimal(str(intent.max_slippage_bps or 0)) / 10000)
    if value > min(cash - pending_value, buying_power, equity * Decimal(str(settings.max_position_size)) - symbol_value - pending_symbol,
                   equity * Decimal(str(settings.max_portfolio_risk)) - exposure - pending_value):
        raise RuntimeError("Insufficient cash or portfolio/symbol exposure budget including pending orders")
