#!/usr/bin/env python3
"""
tests/test_bracket_legs_paper.py
Bracket-leg tracking against the real Alpaca PAPER account. Submits a safe bracket that cannot
fill (BUY limit far below market), verifies the legs are registered, persisted and recovered
after a restart, then cancels it. Skipped without working paper credentials.
"""
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def _client():
    from lib.settings import get_settings
    s = get_settings()
    if not s.has_alpaca_credentials:
        pytest.skip("no Alpaca credentials")
    from alpaca.trading.client import TradingClient
    tc = TradingClient(s.apca_api_key_id, s.apca_api_secret_key, paper=True)
    try:
        tc.get_account()
    except Exception as e:
        pytest.skip(f"paper account unreachable: {e}")
    return tc


def test_bracket_legs_tracked_persisted_recovered(tmp_path):
    from apps.executor.main import OrderTracker, TERMINAL_STATUSES
    from lib.models import OrderIntent, SignalSide
    from decimal import Decimal
    from alpaca.trading.requests import (LimitOrderRequest, TakeProfitRequest, StopLossRequest)
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    tc = _client()
    journal = str(tmp_path / "orders.sqlite")
    tracker = OrderTracker(journal)
    coid = f"legtest_{uuid.uuid4().hex[:8]}"
    order = None
    try:
        req = LimitOrderRequest(symbol="AAPL", qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                                limit_price=50, order_class=OrderClass.BRACKET, client_order_id=coid,
                                take_profit=TakeProfitRequest(limit_price=60),
                                stop_loss=StopLossRequest(stop_price=45))
        order = tc.submit_order(req)
        parent = str(order.id)
        intent = OrderIntent(symbol="AAPL", side=SignalSide.BUY, quantity=Decimal("1"),
                             client_order_id=coid, signal_source="legtest",
                             price=Decimal("50"), stop_loss=Decimal("45"), take_profit=Decimal("60"))
        tracker.add_pending_order(intent, parent)
        tracker.register_legs(parent, "AAPL", order.legs)

        # Two protective legs, tracked and linked to the parent.
        assert len(tracker.legs_by_parent[parent]) == 2
        assert len(tracker.active_leg_ids()) == 2
        assert set(tracker.open_leg_ids_for_symbol("AAPL")) == set(tracker.active_leg_ids())
        assert all(tracker.legs_by_id[l]["side"] == "sell" for l in tracker.active_leg_ids())

        # Persisted to the journal.
        rows = list(tracker.db.execute("SELECT leg_id,parent FROM legs"))
        assert len(rows) == 2 and all(r[1] == parent for r in rows)
        tracker.db.close()

        # Restart: a fresh tracker recovers the legs from the journal.
        t2 = OrderTracker(journal)
        assert len(t2.active_leg_ids()) == 2
        assert set(t2.legs_by_parent[parent]) == set(tracker.legs_by_id.keys())

        # A still-open leg reconciles to no fill.
        leg_id = t2.active_leg_ids()[0]
        leg_order = tc.get_order_by_id(leg_id)
        assert t2.update_leg(leg_order) is None      # HELD -> no protective exit yet
        t2.db.close()
    finally:
        if order is not None:
            try:
                tc.cancel_order_by_id(str(order.id))
            except Exception:
                pass


def test_cancel_frees_legs(tmp_path):
    from apps.executor.main import OrderTracker
    from alpaca.trading.requests import (LimitOrderRequest, TakeProfitRequest, StopLossRequest)
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    tc = _client()
    tracker = OrderTracker(str(tmp_path / "o.sqlite"))
    coid = f"legcancel_{uuid.uuid4().hex[:8]}"
    order = tc.submit_order(LimitOrderRequest(
        symbol="AAPL", qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.GTC, limit_price=50,
        order_class=OrderClass.BRACKET, client_order_id=coid,
        take_profit=TakeProfitRequest(limit_price=60), stop_loss=StopLossRequest(stop_price=45)))
    try:
        tracker.register_legs(str(order.id), "AAPL", order.legs)
        for lid in tracker.open_leg_ids_for_symbol("AAPL"):
            tracker.mark_leg_canceled(lid)
        assert tracker.open_leg_ids_for_symbol("AAPL") == []   # freed locally
    finally:
        try:
            tc.cancel_order_by_id(str(order.id))
        except Exception:
            pass
        tracker.db.close()
