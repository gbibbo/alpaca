#!/usr/bin/env python3
"""
tests/test_executor_durability.py
OrderTracker must rehydrate from its journal without rewriting it (crash-safe restore):
accumulated fills survive, the FSM is restored to the journalled status, and orders_submitted
is not re-incremented on restart.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from decimal import Decimal

from lib.models import OrderIntent, SignalSide
from lib.order_fsm import OrderState


def _intent():
    return OrderIntent(symbol="TEST", side=SignalSide.BUY, quantity=Decimal("10"),
                       client_order_id="dur-test", signal_source="audit")


def test_rehydrate_preserves_accumulators_and_fsm(tmp_path):
    from apps.executor.main import OrderTracker
    journal = str(tmp_path / "orders.sqlite")

    t1 = OrderTracker(journal)
    t1.add_pending_order(_intent(), "broker1")
    assert t1.orders_submitted == 1
    first = t1.update_order_status("broker1", "partially_filled", Decimal("5"), Decimal("100"))
    assert first is not None and first.fill_quantity == 5
    t1.db.close()

    # Simulated restart: a fresh tracker over the same journal.
    t2 = OrderTracker(journal)
    od = t2.orders_by_broker_id["broker1"]
    assert od["total_filled"] == Decimal("5")            # accumulator survived
    assert od["remaining_quantity"] == Decimal("5")
    assert t2.order_fsms["broker1"].current_state == OrderState.PARTIALLY_FILLED  # FSM restored
    assert "broker1" in t2.pending_orders                # still open
    assert t2.orders_submitted == 0                      # restore does NOT re-count submissions

    # The next cumulative update computes the delta off the restored accumulator.
    second = t2.update_order_status("broker1", "filled", Decimal("10"), Decimal("110"))
    assert second.fill_price == 120                      # (1100-500)/5, so accumulators were intact
    assert t2.orders_by_broker_id["broker1"]["remaining_quantity"] == 0
    t2.db.close()


def test_terminal_order_not_pending_after_restart(tmp_path):
    from apps.executor.main import OrderTracker
    journal = str(tmp_path / "orders.sqlite")
    t1 = OrderTracker(journal)
    t1.add_pending_order(_intent(), "broker2")
    t1.update_order_status("broker2", "filled", Decimal("10"), Decimal("100"))
    t1.db.close()

    t2 = OrderTracker(journal)
    assert "broker2" not in t2.pending_orders
    assert t2.order_fsms["broker2"].current_state == OrderState.FILLED
    t2.db.close()


import pytest
from unittest.mock import Mock, patch
from lib.models import OrderType


@pytest.mark.asyncio
async def test_get_existing_order_fails_closed_on_network_error(monkeypatch, tmp_path):
    """A network/ambiguous failure while checking idempotency must NOT lead to a submit."""
    monkeypatch.setenv("EXECUTOR_JOURNAL", str(tmp_path / "j.sqlite"))
    monkeypatch.setenv("EXECUTOR_METRICS_PORT", "0")
    from apps.executor.main import EnhancedAlpacaExecutor, IdempotencyCheckError

    client = Mock()
    client.get_account = Mock(return_value=Mock(status="ACTIVE", buying_power=100000,
                                                cash=100000, portfolio_value=100000))
    client.get_order_by_client_id = Mock(side_effect=ConnectionError("connection reset by peer"))
    client.submit_order = Mock()

    with patch("apps.executor.main.TradingClient", return_value=client):
        ex = EnhancedAlpacaExecutor()
        ex.trading_client = client
        intent = OrderIntent(symbol="TEST", side=SignalSide.BUY, quantity=Decimal("1"),
                             order_type=OrderType.MARKET, client_order_id="cid-net",
                             signal_source="t", price=Decimal("100"))

        with pytest.raises(IdempotencyCheckError):
            await ex.get_existing_order("cid-net")

        # The whole execution path must propagate (fail closed) and never call submit_order.
        with pytest.raises(Exception):
            await ex.execute_order_with_validation(intent)
        assert client.submit_order.call_count == 0


@pytest.mark.asyncio
async def test_get_existing_order_returns_none_on_404(monkeypatch, tmp_path):
    """A definitive 'does not exist' is safe to proceed from (returns None)."""
    monkeypatch.setenv("EXECUTOR_JOURNAL", str(tmp_path / "j2.sqlite"))
    monkeypatch.setenv("EXECUTOR_METRICS_PORT", "0")
    from apps.executor.main import EnhancedAlpacaExecutor

    client = Mock()
    client.get_account = Mock(return_value=Mock(status="ACTIVE", buying_power=100000,
                                                cash=100000, portfolio_value=100000))
    client.get_order_by_client_id = Mock(side_effect=Exception("order not found"))

    with patch("apps.executor.main.TradingClient", return_value=client):
        ex = EnhancedAlpacaExecutor()
        ex.trading_client = client
        assert await ex.get_existing_order("missing") is None
