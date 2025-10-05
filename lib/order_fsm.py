#!/usr/bin/env python3
"""
lib/order_fsm.py
Order Finite State Machine (FSM) - Epic 5
Manages order lifecycle with safe state transitions, timeouts, and cancel/replace logic
"""

from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from decimal import Decimal
import logging
from lib.models import OrderStatus, OrderIntent
from lib.time_utils import TimeUtils, MonotonicTimer

logger = logging.getLogger(__name__)


class OrderState(str, Enum):
    """
    Complete FSM states for order lifecycle (Epic 5)
    Covers all possible states from creation to terminal states
    """
    # Initial states
    NEW = "new"
    SUBMITTED = "submitted"
    PENDING_NEW = "pending_new"

    # Active states
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"

    # Cancellation states
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"

    # Terminal states (no further transitions)
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REPLACED = "replaced"
    SUSPENDED = "suspended"  # Halted by exchange


class OrderEvent(str, Enum):
    """Events that trigger state transitions"""
    SUBMIT = "submit"
    ACCEPT = "accept"
    PARTIAL_FILL = "partial_fill"
    FILL = "fill"
    CANCEL = "cancel"
    REPLACE = "replace"
    REJECT = "reject"
    EXPIRE = "expire"
    TIMEOUT = "timeout"
    SUSPEND = "suspend"


# Valid state transitions: {current_state: {event: new_state}}
VALID_TRANSITIONS = {
    OrderState.NEW: {
        OrderEvent.SUBMIT: OrderState.SUBMITTED,
        OrderEvent.REJECT: OrderState.REJECTED
    },
    OrderState.SUBMITTED: {
        OrderEvent.ACCEPT: OrderState.ACCEPTED,
        OrderEvent.REJECT: OrderState.REJECTED,
        OrderEvent.TIMEOUT: OrderState.EXPIRED
    },
    OrderState.PENDING_NEW: {
        OrderEvent.ACCEPT: OrderState.ACCEPTED,
        OrderEvent.REJECT: OrderState.REJECTED,
        OrderEvent.TIMEOUT: OrderState.EXPIRED
    },
    OrderState.ACCEPTED: {
        OrderEvent.PARTIAL_FILL: OrderState.PARTIALLY_FILLED,
        OrderEvent.FILL: OrderState.FILLED,
        OrderEvent.CANCEL: OrderState.PENDING_CANCEL,
        OrderEvent.REPLACE: OrderState.PENDING_REPLACE,
        OrderEvent.REJECT: OrderState.REJECTED,
        OrderEvent.TIMEOUT: OrderState.EXPIRED,
        OrderEvent.SUSPEND: OrderState.SUSPENDED
    },
    OrderState.PARTIALLY_FILLED: {
        OrderEvent.FILL: OrderState.FILLED,
        OrderEvent.PARTIAL_FILL: OrderState.PARTIALLY_FILLED,  # More partial fills
        OrderEvent.CANCEL: OrderState.CANCELED,
        OrderEvent.TIMEOUT: OrderState.EXPIRED,
        OrderEvent.SUSPEND: OrderState.SUSPENDED
    },
    OrderState.PENDING_CANCEL: {
        OrderEvent.CANCEL: OrderState.CANCELED,
        OrderEvent.FILL: OrderState.FILLED,  # Can fill before cancel completes
        OrderEvent.PARTIAL_FILL: OrderState.PARTIALLY_FILLED,
        OrderEvent.REJECT: OrderState.ACCEPTED  # Cancel rejected, back to accepted
    },
    OrderState.PENDING_REPLACE: {
        OrderEvent.REPLACE: OrderState.REPLACED,
        OrderEvent.REJECT: OrderState.ACCEPTED,  # Replace rejected, back to accepted
        OrderEvent.FILL: OrderState.FILLED
    },
    # Terminal states have no outgoing transitions
    OrderState.FILLED: {},
    OrderState.CANCELED: {},
    OrderState.REJECTED: {},
    OrderState.EXPIRED: {},
    OrderState.REPLACED: {},
    OrderState.SUSPENDED: {
        OrderEvent.CANCEL: OrderState.CANCELED  # Can cancel suspended orders
    }
}


class OrderFSM:
    """
    Finite State Machine for individual order lifecycle management
    Implements Epic 5 requirements with timeouts and safe transitions
    """

    def __init__(
        self,
        order_intent: OrderIntent,
        broker_order_id: str,
        new_timeout_seconds: int = 30,
        partial_timeout_seconds: int = 300
    ):
        self.order_intent = order_intent
        self.broker_order_id = broker_order_id
        self.current_state = OrderState.NEW
        self.state_history: List[Tuple[OrderState, datetime]] = [
            (OrderState.NEW, TimeUtils.utc_now())
        ]

        # Timeout configuration (Epic 5 T5.1)
        self.new_timeout_seconds = new_timeout_seconds  # 30s for NEW → ACCEPTED
        self.partial_timeout_seconds = partial_timeout_seconds  # 5min for PARTIALLY_FILLED

        # Fill tracking
        self.filled_quantity = Decimal('0')
        self.remaining_quantity = order_intent.quantity
        self.avg_fill_price = Decimal('0')
        self.fills: List[Dict[str, Any]] = []

        # Timing
        self.created_at = MonotonicTimer.current()
        self.last_update = self.created_at
        self.timeout_at: Optional[float] = None

        # Metadata
        self.rejection_reason: Optional[str] = None
        self.cancel_reason: Optional[str] = None

        self._set_timeout()

    def _set_timeout(self):
        """Set timeout based on current state (Epic 5 requirement)"""
        current = MonotonicTimer.current()

        if self.current_state == OrderState.SUBMITTED:
            self.timeout_at = current + self.new_timeout_seconds
            logger.debug(f"Order {self.broker_order_id}: Set NEW timeout to {self.new_timeout_seconds}s")

        elif self.current_state == OrderState.PARTIALLY_FILLED:
            self.timeout_at = current + self.partial_timeout_seconds
            logger.debug(f"Order {self.broker_order_id}: Set PARTIAL timeout to {self.partial_timeout_seconds}s")

        else:
            self.timeout_at = None

    def check_timeout(self) -> bool:
        """
        Check if order has timed out (Epic 5 T5.1)
        Returns True if timeout occurred and state transitioned
        """
        if not self.timeout_at:
            return False

        if MonotonicTimer.current() >= self.timeout_at:
            logger.warning(
                f"⏰ Order {self.broker_order_id} timed out in state {self.current_state} "
                f"after {self.get_state_duration():.1f}s"
            )

            # Transition to EXPIRED
            success = self.transition(OrderEvent.TIMEOUT)
            return success

        return False

    def transition(self, event: OrderEvent, **kwargs) -> bool:
        """
        Execute state transition with validation (Epic 5 core)

        Args:
            event: Event triggering the transition
            **kwargs: Additional data (fill_quantity, fill_price, reason, etc.)

        Returns:
            True if transition successful, False if invalid
        """
        valid_events = VALID_TRANSITIONS.get(self.current_state, {})

        if event not in valid_events:
            logger.error(
                f"❌ Invalid transition for order {self.broker_order_id}: "
                f"{self.current_state} --[{event}]--> ?? "
                f"(valid events: {list(valid_events.keys())})"
            )
            return False

        new_state = valid_events[event]
        old_state = self.current_state

        # Execute state transition
        self.current_state = new_state
        self.state_history.append((new_state, TimeUtils.utc_now()))
        self.last_update = MonotonicTimer.current()

        # Update timeouts
        self._set_timeout()

        # Handle event-specific logic
        if event == OrderEvent.PARTIAL_FILL:
            fill_qty = kwargs.get('fill_quantity', Decimal('0'))
            fill_price = kwargs.get('fill_price', Decimal('0'))
            self._update_fills(fill_qty, fill_price)
            logger.info(
                f"📊 Order {self.broker_order_id}: Partial fill "
                f"{fill_qty} @ ${fill_price} (total: {self.filled_quantity}/{self.order_intent.quantity})"
            )

        elif event == OrderEvent.FILL:
            fill_qty = kwargs.get('fill_quantity', self.remaining_quantity)
            fill_price = kwargs.get('fill_price', Decimal('0'))
            self._update_fills(fill_qty, fill_price)
            logger.info(
                f"✅ Order {self.broker_order_id}: Fully filled "
                f"{self.filled_quantity} @ avg ${self.avg_fill_price:.2f}"
            )

        elif event == OrderEvent.REJECT:
            self.rejection_reason = kwargs.get('reason', 'Unknown')
            logger.warning(f"❌ Order {self.broker_order_id}: Rejected - {self.rejection_reason}")

        elif event == OrderEvent.CANCEL:
            self.cancel_reason = kwargs.get('reason', 'Manual cancel')
            logger.info(f"🚫 Order {self.broker_order_id}: Canceled - {self.cancel_reason}")

        elif event == OrderEvent.TIMEOUT:
            logger.error(f"⏰ Order {self.broker_order_id}: Expired due to timeout")

        # Log transition
        logger.info(f"🔄 Order {self.broker_order_id}: {old_state} --[{event}]--> {new_state}")

        return True

    def _update_fills(self, fill_qty: Decimal, fill_price: Decimal):
        """Update fill quantities and average price"""
        if fill_qty <= 0:
            return

        # Record fill
        self.fills.append({
            "quantity": float(fill_qty),
            "price": float(fill_price),
            "timestamp": TimeUtils.utc_now().isoformat()
        })

        # Update totals
        prev_filled = self.filled_quantity
        self.filled_quantity += fill_qty
        self.remaining_quantity = self.order_intent.quantity - self.filled_quantity

        # Calculate weighted average price
        if self.filled_quantity > 0:
            total_value = (self.avg_fill_price * prev_filled) + (fill_price * fill_qty)
            self.avg_fill_price = total_value / self.filled_quantity

    def is_terminal(self) -> bool:
        """Check if current state is terminal (no more transitions possible)"""
        return self.current_state in [
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.REPLACED
        ]

    def is_active(self) -> bool:
        """Check if order is active (can still be filled)"""
        return self.current_state in [
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.PENDING_CANCEL,
            OrderState.PENDING_REPLACE
        ]

    def can_cancel(self) -> bool:
        """Check if order can be canceled from current state"""
        return OrderEvent.CANCEL in VALID_TRANSITIONS.get(self.current_state, {})

    def get_state_duration(self) -> float:
        """Get time spent in current state (seconds)"""
        return MonotonicTimer.current() - self.last_update

    def get_total_duration(self) -> float:
        """Get total time since order creation (seconds)"""
        return MonotonicTimer.current() - self.created_at

    def get_fill_percentage(self) -> float:
        """Get fill percentage (0.0 to 1.0)"""
        if self.order_intent.quantity <= 0:
            return 0.0
        return float(self.filled_quantity / self.order_intent.quantity)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize FSM state to dictionary for logging/metrics"""
        return {
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.order_intent.client_order_id,
            "symbol": self.order_intent.symbol,
            "side": self.order_intent.side.value,
            "current_state": self.current_state.value,
            "is_terminal": self.is_terminal(),
            "is_active": self.is_active(),
            "can_cancel": self.can_cancel(),
            "fills": {
                "filled_quantity": float(self.filled_quantity),
                "remaining_quantity": float(self.remaining_quantity),
                "fill_percentage": self.get_fill_percentage(),
                "avg_fill_price": float(self.avg_fill_price),
                "num_fills": len(self.fills)
            },
            "timing": {
                "state_duration_seconds": self.get_state_duration(),
                "total_duration_seconds": self.get_total_duration(),
                "timeout_at": self.timeout_at,
                "has_timeout": self.timeout_at is not None
            },
            "state_history": [
                {"state": state.value, "timestamp": ts.isoformat()}
                for state, ts in self.state_history
            ],
            "rejection_reason": self.rejection_reason,
            "cancel_reason": self.cancel_reason
        }

    def __repr__(self) -> str:
        return (
            f"OrderFSM(broker_id={self.broker_order_id}, "
            f"state={self.current_state.value}, "
            f"filled={self.get_fill_percentage():.1%})"
        )


# Helper functions for common FSM operations

def map_alpaca_status_to_event(alpaca_status: str) -> Optional[OrderEvent]:
    """
    Map Alpaca order status to FSM event
    Used by OrderTracker to integrate with FSM
    """
    status_map = {
        "new": OrderEvent.ACCEPT,
        "accepted": OrderEvent.ACCEPT,
        "partially_filled": OrderEvent.PARTIAL_FILL,
        "filled": OrderEvent.FILL,
        "canceled": OrderEvent.CANCEL,
        "rejected": OrderEvent.REJECT,
        "expired": OrderEvent.EXPIRE,
        "suspended": OrderEvent.SUSPEND,
        "pending_cancel": OrderEvent.CANCEL,
        "pending_replace": OrderEvent.REPLACE
    }

    return status_map.get(alpaca_status.lower())


def create_fsm_from_order_intent(
    order_intent: OrderIntent,
    broker_order_id: str,
    new_timeout_seconds: int = 30,
    partial_timeout_seconds: int = 300
) -> OrderFSM:
    """
    Factory function to create FSM from OrderIntent
    Automatically transitions to SUBMITTED state
    """
    fsm = OrderFSM(
        order_intent=order_intent,
        broker_order_id=broker_order_id,
        new_timeout_seconds=new_timeout_seconds,
        partial_timeout_seconds=partial_timeout_seconds
    )

    # Immediately transition to SUBMITTED
    fsm.transition(OrderEvent.SUBMIT)

    return fsm
