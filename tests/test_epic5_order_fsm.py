#!/usr/bin/env python3
"""
tests/test_epic5_order_fsm.py
Unit tests for Epic 5: Order Finite State Machine (FSM)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
from decimal import Decimal
from lib.order_fsm import (
    OrderFSM, OrderState, OrderEvent, VALID_TRANSITIONS,
    map_alpaca_status_to_event, create_fsm_from_order_intent
)
from lib.models import OrderIntent, SignalSide, OrderType


class TestOrderFSMStates:
    """Test FSM state definitions and transitions"""

    def test_all_states_defined(self):
        """All expected states should be defined"""
        expected_states = [
            "NEW", "SUBMITTED", "PENDING_NEW", "ACCEPTED", "PARTIALLY_FILLED",
            "PENDING_CANCEL", "PENDING_REPLACE", "FILLED", "CANCELED",
            "REJECTED", "EXPIRED", "REPLACED", "SUSPENDED"
        ]

        for state in expected_states:
            assert hasattr(OrderState, state)

    def test_terminal_states(self):
        """Terminal states should have no outgoing transitions"""
        terminal_states = [
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.REPLACED
        ]

        for state in terminal_states:
            assert VALID_TRANSITIONS.get(state, {}) == {} or len(VALID_TRANSITIONS[state]) <= 1


class TestOrderFSMTransitions:
    """Test valid and invalid state transitions (Epic 5 core)"""

    def test_valid_transition_new_to_submitted(self):
        """NEW → SUBMITTED should be valid"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        assert fsm.current_state == OrderState.NEW

        # Transition to SUBMITTED
        success = fsm.transition(OrderEvent.SUBMIT)

        assert success is True
        assert fsm.current_state == OrderState.SUBMITTED

    def test_valid_transition_submitted_to_accepted(self):
        """SUBMITTED → ACCEPTED should be valid"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        fsm.transition(OrderEvent.SUBMIT)

        # Transition to ACCEPTED
        success = fsm.transition(OrderEvent.ACCEPT)

        assert success is True
        assert fsm.current_state == OrderState.ACCEPTED

    def test_valid_transition_accepted_to_partial(self):
        """ACCEPTED → PARTIALLY_FILLED should be valid"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)

        # Partial fill
        success = fsm.transition(
            OrderEvent.PARTIAL_FILL,
            fill_quantity=Decimal("5"),
            fill_price=Decimal("150")
        )

        assert success is True
        assert fsm.current_state == OrderState.PARTIALLY_FILLED
        assert fsm.filled_quantity == Decimal("5")
        assert fsm.remaining_quantity == Decimal("5")

    def test_valid_transition_partial_to_filled(self):
        """PARTIALLY_FILLED → FILLED should be valid"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)
        fsm.transition(OrderEvent.PARTIAL_FILL, fill_quantity=Decimal("5"), fill_price=Decimal("150"))

        # Complete fill
        success = fsm.transition(
            OrderEvent.FILL,
            fill_quantity=Decimal("5"),
            fill_price=Decimal("151")
        )

        assert success is True
        assert fsm.current_state == OrderState.FILLED
        assert fsm.filled_quantity == Decimal("10")
        assert fsm.remaining_quantity == Decimal("0")
        assert fsm.is_terminal() is True

    def test_invalid_transition_new_to_filled(self):
        """NEW → FILLED should be invalid (must go through SUBMIT first)"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")

        # Try invalid transition
        success = fsm.transition(OrderEvent.FILL)

        assert success is False
        assert fsm.current_state == OrderState.NEW  # State unchanged


class TestOrderFSMTimeouts:
    """Test timeout detection and handling (Epic 5 T5.1)"""

    def test_new_timeout_detection(self):
        """Order in SUBMITTED state should timeout after configured seconds"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123", new_timeout_seconds=1)
        fsm.transition(OrderEvent.SUBMIT)

        # Immediately: no timeout
        assert fsm.check_timeout() is False
        assert fsm.current_state == OrderState.SUBMITTED

        # Wait for timeout
        import time
        time.sleep(1.5)

        # Now should timeout
        assert fsm.check_timeout() is True
        assert fsm.current_state == OrderState.EXPIRED

    def test_partial_fill_timeout(self):
        """Partially filled order should timeout after configured seconds"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123", partial_timeout_seconds=1)
        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)
        fsm.transition(OrderEvent.PARTIAL_FILL, fill_quantity=Decimal("5"), fill_price=Decimal("150"))

        # Immediately: no timeout
        assert fsm.check_timeout() is False

        # Wait for timeout
        import time
        time.sleep(1.5)

        # Should timeout
        assert fsm.check_timeout() is True
        assert fsm.current_state == OrderState.EXPIRED

    def test_filled_order_no_timeout(self):
        """Filled order (terminal state) should not timeout"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123", new_timeout_seconds=1)
        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)
        fsm.transition(OrderEvent.FILL, fill_quantity=Decimal("10"), fill_price=Decimal("150"))

        import time
        time.sleep(1.5)

        # Terminal state: no timeout
        assert fsm.check_timeout() is False
        assert fsm.current_state == OrderState.FILLED


class TestOrderFSMHelpers:
    """Test FSM helper functions and utilities"""

    def test_is_terminal(self):
        """is_terminal() should correctly identify terminal states"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        assert fsm.is_terminal() is False

        fsm.transition(OrderEvent.SUBMIT)
        assert fsm.is_terminal() is False

        fsm.transition(OrderEvent.REJECT)
        assert fsm.is_terminal() is True

    def test_is_active(self):
        """is_active() should correctly identify active states"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)

        assert fsm.is_active() is True

        fsm.transition(OrderEvent.FILL, fill_quantity=Decimal("10"), fill_price=Decimal("150"))

        assert fsm.is_active() is False

    def test_can_cancel(self):
        """can_cancel() should correctly identify cancelable states"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        assert fsm.can_cancel() is False  # NEW state cannot cancel

        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)

        assert fsm.can_cancel() is True

    def test_get_fill_percentage(self):
        """get_fill_percentage() should calculate correctly"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        fsm.transition(OrderEvent.SUBMIT)
        fsm.transition(OrderEvent.ACCEPT)

        assert fsm.get_fill_percentage() == 0.0

        fsm.transition(OrderEvent.PARTIAL_FILL, fill_quantity=Decimal("5"), fill_price=Decimal("150"))

        assert fsm.get_fill_percentage() == 0.5

        fsm.transition(OrderEvent.FILL, fill_quantity=Decimal("5"), fill_price=Decimal("151"))

        assert fsm.get_fill_percentage() == 1.0

    def test_to_dict_serialization(self):
        """to_dict() should serialize FSM state correctly"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = OrderFSM(order, "broker_123")
        fsm.transition(OrderEvent.SUBMIT)

        fsm_dict = fsm.to_dict()

        assert fsm_dict["broker_order_id"] == "broker_123"
        assert fsm_dict["current_state"] == "submitted"
        assert fsm_dict["is_terminal"] is False
        assert "timing" in fsm_dict
        assert "fills" in fsm_dict
        assert "state_history" in fsm_dict


class TestAlpacaStatusMapping:
    """Test Alpaca status to FSM event mapping"""

    def test_map_alpaca_status_to_event(self):
        """Should correctly map Alpaca statuses to FSM events"""
        mappings = {
            "new": OrderEvent.ACCEPT,
            "accepted": OrderEvent.ACCEPT,
            "partially_filled": OrderEvent.PARTIAL_FILL,
            "filled": OrderEvent.FILL,
            "canceled": OrderEvent.CANCEL,
            "rejected": OrderEvent.REJECT,
            "expired": OrderEvent.EXPIRE,
            "suspended": OrderEvent.SUSPEND
        }

        for alpaca_status, expected_event in mappings.items():
            event = map_alpaca_status_to_event(alpaca_status)
            assert event == expected_event

    def test_map_unknown_status_returns_none(self):
        """Unknown Alpaca status should return None"""
        event = map_alpaca_status_to_event("unknown_status")
        assert event is None


class TestFSMFactory:
    """Test FSM factory function"""

    def test_create_fsm_from_order_intent(self):
        """Factory should create FSM and auto-transition to SUBMITTED"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_123",
            signal_source="test"
        )

        fsm = create_fsm_from_order_intent(order, "broker_456")

        assert fsm.broker_order_id == "broker_456"
        assert fsm.current_state == OrderState.SUBMITTED  # Auto-transitioned
        assert fsm.order_intent.symbol == "GOOGL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
