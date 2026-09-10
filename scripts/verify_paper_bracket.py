#!/usr/bin/env python3
"""
scripts/verify_paper_bracket.py
Manual, real-fill verification of the bracket lifecycle against the Alpaca PAPER account.

It takes a tiny real position, so it is NOT a pytest (pytest must never place orders by accident)
and it refuses to run without an explicit opt-in flag.

    python scripts/verify_paper_bracket.py --yes-paper [--symbol NVDA]

What it checks end to end, with cleanup in a finally block:
  1. submit a 1-share MARKET bracket -> the entry fills (real position)
  2. the two protective legs (take-profit + stop) become active and are tracked
  3. our OrderTracker reconciles the legs (no exit while they are open)
  4. cancel_bracket_legs frees the shares (the strategic-exit path)
  5. flatten the position; assert no dangling position or open orders remain

Requires paper credentials in .env and an open market for the entry to fill.
"""
import argparse
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.settings import get_settings
from apps.executor.main import OrderTracker, order_status_str


def _flatten_and_clean(tc, sym):
    """Best-effort: cancel any open orders for `sym`, then flatten any position."""
    from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
    from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce
    for o in tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
        if o.symbol == sym:
            try:
                tc.cancel_order_by_id(o.id)
            except Exception as e:
                print(f"  (cancel {str(o.id)[:8]} note: {str(e)[:50]})")
    time.sleep(1)
    qty = next((Decimal(str(p.qty)) for p in tc.get_all_positions() if p.symbol == sym), Decimal(0))
    if qty > 0:
        tc.submit_order(MarketOrderRequest(symbol=sym, qty=int(qty), side=OrderSide.SELL,
                                           time_in_force=TimeInForce.DAY))
        for _ in range(30):
            time.sleep(0.5)
            if next((Decimal(str(p.qty)) for p in tc.get_all_positions() if p.symbol == sym), Decimal(0)) == 0:
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes-paper", action="store_true", help="required opt-in; places real paper orders")
    ap.add_argument("--symbol", default="NVDA")
    args = ap.parse_args()
    if not args.yes_paper:
        print("Refusing to run without --yes-paper (this places real paper orders)."); return 2

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (MarketOrderRequest, TakeProfitRequest, StopLossRequest)
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest

    s = get_settings()
    if not s.has_alpaca_credentials:
        print("No Alpaca credentials."); return 2
    tc = TradingClient(s.apca_api_key_id, s.apca_api_secret_key, paper=True)
    if not tc.get_clock().is_open:
        print("Market is closed; the entry cannot fill. Try during regular hours."); return 2
    sym = args.symbol
    if any(p.symbol == sym for p in tc.get_all_positions()):
        print(f"{sym} already held; pick a symbol you do not hold to avoid interference."); return 2

    dc = StockHistoricalDataClient(s.apca_api_key_id, s.apca_api_secret_key)
    px = float(dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sym, feed="iex"))[sym].price)
    tp, sl = round(px * 1.05, 2), round(px * 0.95, 2)
    tracker = OrderTracker(":memory:")
    ok = False
    parent_id = None
    try:
        o = tc.submit_order(MarketOrderRequest(
            symbol=sym, qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET, client_order_id=f"verify_{uuid.uuid4().hex[:8]}",
            take_profit=TakeProfitRequest(limit_price=tp), stop_loss=StopLossRequest(stop_price=sl)))
        parent_id = str(o.id)
        tracker.register_legs(parent_id, sym, o.legs)
        assert len(tracker.active_leg_ids()) == 2, "expected 2 protective legs"
        print(f"1) bracket submitted, {len(tracker.active_leg_ids())} legs tracked")

        for _ in range(40):
            full = tc.get_order_by_id(parent_id)
            if order_status_str(full) == "filled":
                break
            time.sleep(0.5)
        assert order_status_str(full) == "filled", f"entry not filled: {order_status_str(full)}"
        print(f"2) entry filled: {full.filled_qty}@{full.filled_avg_price}; legs now active")

        time.sleep(1)
        for lid in list(tracker.active_leg_ids()):
            fill = tracker.update_leg(tc.get_order_by_id(lid))
            assert fill is None, "no protective exit expected while legs are open"
        print("3) legs reconciled (no exit while open)")

        for lid in tracker.open_leg_ids_for_symbol(sym):
            try:
                tc.cancel_order_by_id(lid)
            except Exception as e:
                print(f"   (leg cancel note: {str(e)[:50]})")
            tracker.mark_leg_canceled(lid)
        assert tracker.open_leg_ids_for_symbol(sym) == [], "legs not freed"
        print("4) protective legs canceled (shares freed for strategic exit)")

        _flatten_and_clean(tc, sym)
        pos = next((Decimal(str(p.qty)) for p in tc.get_all_positions() if p.symbol == sym), Decimal(0))
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        strays = [x for x in tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)) if x.symbol == sym]
        assert pos == 0 and not strays, f"not clean: pos={pos} strays={len(strays)}"
        print(f"5) flat: {sym} position 0, no open orders")
        ok = True
        print("\nVERIFIED: bracket lifecycle on a real fill is clean")
    finally:
        try:
            if parent_id:
                tc.cancel_order_by_id(parent_id)
        except Exception:
            pass
        _flatten_and_clean(tc, sym)  # safety net
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
