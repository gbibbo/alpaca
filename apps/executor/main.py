#!/usr/bin/env python3
"""
apps/executor/main.py
Enhanced Executor with ChatGPT's recommended improvements
- Exponential backoff + jitter retry logic for Alpaca API (200 req/min limit)
- Comprehensive partial fills and order status management
- Intelligent rate limiting with priority queuing
- Enhanced error handling and recovery
- Performance monitoring and metrics
"""

import os
import sys
import errno
import asyncio
import logging
import json
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from decimal import Decimal
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import OrderIntent, OrderFill, SignalSide, OrderStatus, OrderType
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings
from lib.time_utils import (
    TimeUtils, MonotonicTimer, RateLimitWindow, TimingContext,
    check_alpaca_rate_limit, record_alpaca_call
)
from lib.deduplication import get_deduplication_service
from lib.metrics_helpers import (
    ExecutorMetrics, start_metrics_server, find_available_port,
    time_order_execution, time_api_request, time_bus_processing
)

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide as AlpacaOrderSide, TimeInForce, OrderClass, OrderStatus as AlpacaOrderStatus
try:
    from alpaca.common.exceptions import APIError
except Exception:  # pragma: no cover - keeps module importable if alpaca layout changes
    APIError = Exception

# Alpaca order statuses (lower-case) grouped by lifecycle stage
OPEN_STATUSES = {"new", "accepted", "pending_new", "accepted_for_bidding", "held", "partially_filled",
                 "pending_cancel", "pending_replace", "calculated", "stopped", "suspended", "done_for_day"}
TERMINAL_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired", "replaced"}


def order_status_str(order) -> str:
    """Normalize an alpaca-py Order.status (Enum or str) to a lower-case string."""
    status = getattr(order, "status", None)
    status = getattr(status, "value", status)
    return str(status).lower() if status is not None else ""

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for retry logic with exponential backoff"""
    
    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 60.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff + jitter"""
        if attempt <= 0:
            return 0.0
        
        # Exponential backoff: delay = base_delay * (2 ** (attempt - 1))
        delay = self.base_delay * (2 ** (attempt - 1))
        delay = min(delay, self.max_delay)
        
        # Add jitter (random factor between 0.5 and 1.5)
        jitter = random.uniform(0.5, 1.5)
        final_delay = delay * jitter
        
        logger.debug(f"Retry delay for attempt {attempt}: {final_delay:.2f}s (base: {delay:.2f}s)")
        return final_delay


class AlpacaRateManager:
    """Intelligent rate manager for Alpaca API calls"""
    
    def __init__(self):
        os.environ["SERVICE_NAME"] = "executor"
        self.settings = get_settings()
        
        # Alpaca Trading API limit: 200 requests/minute
        self.trading_limiter = RateLimitWindow(window_seconds=60, max_requests=190)  # Leave buffer
        
        # Data API has different limits, but we focus on trading
        self.data_limiter = RateLimitWindow(window_seconds=60, max_requests=1000)
        
        # Priority queue for important operations
        self.priority_queue: List[Tuple[int, str, callable]] = []  # (priority, timestamp, operation)
        
        # Metrics
        self.total_calls = 0
        self.rate_limited_calls = 0
        self.failed_calls = 0
        
    def can_make_trading_call(self) -> bool:
        """Check if we can make a trading API call"""
        return self.trading_limiter.can_make_request()
    
    def record_trading_call(self, endpoint: str = "unknown") -> bool:
        """Record a trading API call"""
        success = self.trading_limiter.record_request(endpoint)
        self.total_calls += 1
        
        if not success:
            self.rate_limited_calls += 1
            
        return success
    
    def get_wait_time(self) -> float:
        """Get time to wait until next API call slot"""
        return self.trading_limiter.time_until_next_slot()
    
    def get_stats(self) -> Dict:
        """Get rate manager statistics"""
        trading_stats = self.trading_limiter.get_stats()
        
        return {
            "trading_api": trading_stats,
            "total_calls": self.total_calls,
            "rate_limited_calls": self.rate_limited_calls,
            "failed_calls": self.failed_calls,
            "success_rate": (self.total_calls - self.failed_calls) / max(1, self.total_calls),
            "rate_limit_hit_rate": self.rate_limited_calls / max(1, self.total_calls)
        }


class OrderTracker:
    """Enhanced order tracking with FSM and partial fills support (Epic 5)"""

    def __init__(self, journal_path=None):
        import sqlite3
        self.db = sqlite3.connect(journal_path or ':memory:')
        self.db.execute('CREATE TABLE IF NOT EXISTS orders (broker_id TEXT PRIMARY KEY, intent TEXT, qty TEXT, notional TEXT, status TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT)')
        # Track orders by various IDs
        self.orders_by_client_id: Dict[str, Dict] = {}
        self.orders_by_broker_id: Dict[str, Dict] = {}
        self.pending_orders: Dict[str, OrderIntent] = {}

        # Partial fill tracking
        self.partial_fills: Dict[str, List[Dict]] = {}  # broker_id -> list of fills

        # Epic 5: FSM tracking
        from lib.order_fsm import OrderFSM
        self.order_fsms: Dict[str, OrderFSM] = {}  # broker_id -> FSM
        self.timeout_monitors: Dict[str, float] = {}  # broker_id -> last_check_time

        # Performance metrics
        self.orders_submitted = 0
        self.orders_filled = 0
        self.orders_partial = 0
        self.orders_failed = 0
        rows = list(self.db.execute('SELECT broker_id,intent,qty,notional,status FROM orders'))
        for broker_id, payload, qty, notional, status in rows:
            self._rehydrate(broker_id, OrderIntent.model_validate_json(payload),
                            Decimal(qty), Decimal(notional), status)

    def _rehydrate(self, broker_id, order_intent, total_filled, total_notional, status):
        """Rebuild in-memory state from the journal WITHOUT writing back.

        The journal already holds the accumulated truth. The previous restore path called
        add_pending_order first, which re-persisted reset state (total_filled=0) before restoring
        the accumulators and also re-incremented orders_submitted; a crash in that window could
        overwrite real fills with zero. This reads only, restores the FSM to the journalled
        status, and never re-persists.
        """
        from lib.order_fsm import create_fsm_from_order_intent, OrderState
        status = (status or "submitted").lower()
        fsm = create_fsm_from_order_intent(order_intent, broker_id)
        try:
            fsm.current_state = OrderState(status)
        except ValueError:
            pass  # unknown status -> leave the freshly-created SUBMITTED state
        fsm.filled_quantity = total_filled
        fsm.remaining_quantity = order_intent.quantity - total_filled
        fsm._set_timeout()
        self.order_fsms[broker_id] = fsm

        order_data = {
            "intent": order_intent, "broker_id": broker_id,
            "submitted_at": MonotonicTimer.current(), "status": status, "fills": [],
            "total_filled": total_filled, "total_notional": total_notional,
            "remaining_quantity": order_intent.quantity - total_filled, "fsm": fsm,
        }
        self.orders_by_client_id[order_intent.client_order_id] = order_data
        self.orders_by_broker_id[broker_id] = order_data
        if status in TERMINAL_STATUSES or order_data["remaining_quantity"] <= 0:
            self.pending_orders.pop(broker_id, None)
        else:
            self.pending_orders[broker_id] = order_intent

    def checkpoint(self, broker_id, fill=None):
        order = self.orders_by_broker_id[broker_id]
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?)',
                            (broker_id, order['intent'].model_dump_json(), str(order['total_filled']),
                             str(order['total_notional']), order['status']))
            if fill:
                self.db.execute('INSERT OR IGNORE INTO outbox VALUES (?,?)', (str(fill.fill_id), fill.model_dump_json()))

    def drain_outbox(self, bus):
        for fill_id, payload in list(self.db.execute('SELECT id,payload FROM outbox ORDER BY rowid')):
            bus.publish_order_fill(OrderFill.model_validate_json(payload))
            with self.db:
                self.db.execute('DELETE FROM outbox WHERE id=?', (fill_id,))
        
    def add_pending_order(self, order_intent: OrderIntent, broker_order_id: str):
        """Add order to tracking with FSM (Epic 5)"""
        # Create FSM for order lifecycle management
        from lib.order_fsm import create_fsm_from_order_intent
        fsm = create_fsm_from_order_intent(order_intent, broker_order_id)
        self.order_fsms[broker_order_id] = fsm

        order_data = {
            "intent": order_intent,
            "broker_id": broker_order_id,
            "submitted_at": MonotonicTimer.current(),
            "status": "submitted",
            "fills": [],
            "total_filled": Decimal('0'),
            "total_notional": Decimal('0'),
            "remaining_quantity": order_intent.quantity,
            "fsm": fsm  # Reference to FSM
        }

        self.orders_by_client_id[order_intent.client_order_id] = order_data
        self.orders_by_broker_id[broker_order_id] = order_data
        self.pending_orders[broker_order_id] = order_intent

        self.orders_submitted += 1
        self.checkpoint(broker_order_id)
        logger.debug(f"Tracking order with FSM: {order_intent.client_order_id} -> {broker_order_id} (state: {fsm.current_state})")
    
    def update_order_status(self, broker_order_id: str, status: str, filled_qty: Decimal = None,
                          fill_price: Decimal = None) -> Optional[OrderFill]:
        """Update order status using FSM (Epic 5)"""
        if broker_order_id not in self.orders_by_broker_id:
            logger.warning(f"Unknown order for status update: {broker_order_id}")
            return None

        order_data = self.orders_by_broker_id[broker_order_id]
        order_intent = order_data["intent"]

        status = (status or "").lower()

        # Alpaca reports filled_qty as a CUMULATIVE total. Only the delta since the last
        # update is a new fill; repeated polls must not double-count.
        new_fill_qty = Decimal('0')
        if filled_qty is not None and filled_qty > 0 and fill_price:
            new_fill_qty = Decimal(str(filled_qty)) - order_data["total_filled"]

        cumulative_notional = Decimal(str(filled_qty or 0)) * Decimal(str(fill_price or 0))
        if new_fill_qty > 0:
            fill_price = (cumulative_notional - order_data.get("total_notional", Decimal(0))) / new_fill_qty
            if fill_price <= 0:
                raise ValueError("Invalid cumulative broker fill notional")
            order_data["total_notional"] = cumulative_notional

        # Epic 5: Update FSM state
        fsm = self.order_fsms.get(broker_order_id)
        if fsm:
            from lib.order_fsm import map_alpaca_status_to_event
            event = map_alpaca_status_to_event(status)

            if event and fsm.is_terminal():
                logger.debug(f"FSM for {broker_order_id} already terminal ({fsm.current_state}); ignoring '{status}'")
            elif event:
                if new_fill_qty > 0:
                    fsm.transition(event, fill_quantity=new_fill_qty, fill_price=fill_price)
                else:
                    fsm.transition(event)
                logger.debug(f"FSM updated for {broker_order_id}: {fsm.current_state}")

        order_data["status"] = status
        order_data["last_updated"] = MonotonicTimer.current()

        # Handle fills
        if new_fill_qty > 0:
            fill_data = {
                "quantity": new_fill_qty,
                "price": fill_price,
                "timestamp": TimeUtils.utc_now(),
                "value": new_fill_qty * fill_price
            }

            order_data["fills"].append(fill_data)
            order_data["total_filled"] += new_fill_qty
            order_data["remaining_quantity"] = order_intent.quantity - order_data["total_filled"]

            # Create OrderFill object
            from uuid import uuid5, NAMESPACE_URL
            order_fill = OrderFill(
                fill_id=uuid5(NAMESPACE_URL, f"{broker_order_id}:{order_data['total_filled']}"),
                symbol=order_intent.symbol,
                timestamp=TimeUtils.utc_now(),
                side=order_intent.side,
                quantity=order_intent.quantity,
                fill_price=fill_price,
                fill_quantity=new_fill_qty,
                broker_order_id=str(broker_order_id),
                client_order_id=order_intent.client_order_id,
                status=OrderStatus.PARTIALLY_FILLED if order_data["remaining_quantity"] > 0 else OrderStatus.FILLED,
                commission=Decimal('0.0'),  # Alpaca is commission-free
                total_value=fill_data["value"]
            )

            # Update metrics
            if order_data["remaining_quantity"] <= 0:
                self.orders_filled += 1
                # Remove from pending if completely filled
                self.pending_orders.pop(broker_order_id, None)
                logger.info(f"Order completely filled: {order_intent.client_order_id}")
            else:
                self.orders_partial += 1
                logger.info(f"Partial fill: {order_intent.client_order_id} - {order_data['total_filled']}/{order_intent.quantity}")

            self.checkpoint(broker_order_id, order_fill)
            return order_fill

        # Handle failed/cancelled orders (Alpaca spells it 'canceled')
        if status in ('canceled', 'cancelled', 'rejected', 'expired', 'replaced'):
            self.orders_failed += 1
            self.pending_orders.pop(broker_order_id, None)
            logger.warning(f"Order {status}: {order_intent.client_order_id}")
        elif status == 'filled' and order_data["remaining_quantity"] <= 0:
            # Already accounted for (repeated poll)
            self.pending_orders.pop(broker_order_id, None)

        self.checkpoint(broker_order_id)
        return None
    
    def get_pending_orders(self) -> List[str]:
        """Get list of pending order broker IDs"""
        return list(self.pending_orders.keys())

    async def check_timeouts(self) -> List[str]:
        """
        Check and process order timeouts (Epic 5)
        Returns list of broker_order_ids that timed out
        """
        timed_out_orders = []

        for broker_id, fsm in list(self.order_fsms.items()):
            if not fsm.is_terminal() and fsm.check_timeout():
                timed_out_orders.append(broker_id)
                logger.warning(
                    f"⏰ Order {broker_id} timed out in state {fsm.current_state} "
                    f"after {fsm.get_state_duration():.1f}s"
                )

                # Update order data
                if broker_id in self.orders_by_broker_id:
                    self.orders_by_broker_id[broker_id]["status"] = "expired"
                    self.orders_by_broker_id[broker_id]["last_updated"] = MonotonicTimer.current()

                # Remove from pending
                self.pending_orders.pop(broker_id, None)
                self.orders_failed += 1

        return timed_out_orders

    def get_stats(self) -> Dict:
        """Get order tracking statistics"""
        return {
            "orders_submitted": self.orders_submitted,
            "orders_filled": self.orders_filled,
            "orders_partial": self.orders_partial,
            "orders_failed": self.orders_failed,
            "pending_count": len(self.pending_orders),
            "fill_rate": self.orders_filled / max(1, self.orders_submitted),
            "partial_fill_rate": self.orders_partial / max(1, self.orders_submitted)
        }


class EnhancedAlpacaExecutor:
    """
    Enhanced Executor implementing ChatGPT's recommendations
    - Retry logic with exponential backoff + jitter
    - Intelligent rate limiting for Alpaca API
    - Comprehensive partial fills handling
    - Enhanced error handling and recovery
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.bus = get_bus()
        self.running = False
        
        # Enhanced services
        self.deduplication = get_deduplication_service()
        self.rate_manager = AlpacaRateManager()
        Path('data').mkdir(exist_ok=True)
        self.order_tracker = OrderTracker(os.getenv('EXECUTOR_JOURNAL', 'data/executor.sqlite'))

        # Initialize metrics
        self.metrics = ExecutorMetrics()

        # Start metrics server
        try:
            metrics_port = int(os.getenv("EXECUTOR_METRICS_PORT", "8012"))
            start_metrics_server(metrics_port)
            logger.info(f"📊 Executor metrics available at http://localhost:{metrics_port}/metrics")
        except OSError as e:
            if getattr(e, "errno", None) in (errno.EADDRINUSE, 98, 10048):  # Address already in use (Linux/Windows)
                try:
                    metrics_port = find_available_port(metrics_port + 1)
                    start_metrics_server(metrics_port)
                    logger.warning(f"Metrics port busy. Using fallback http://localhost:{metrics_port}/metrics")
                except Exception as fallback_error:
                    logger.warning(f"Failed to start metrics server on fallback port: {fallback_error}")
            else:
                logger.warning(f"Failed to start metrics server: {e}")
        except Exception as e:
            logger.warning(f"Failed to start metrics server: {e}")
        
        # Retry configurations for different operations
        self.retry_configs = {
            "submit_order": RetryConfig(max_attempts=3, base_delay=1.0, max_delay=30.0),
            "get_orders": RetryConfig(max_attempts=2, base_delay=0.5, max_delay=10.0),
            "cancel_order": RetryConfig(max_attempts=2, base_delay=1.0, max_delay=15.0)
        }

        # How long to wait for the broker to report a fill right after submission before
        # handing the order over to the background monitor (paper market orders fill in ~1s).
        self.fill_confirm_seconds = float(os.getenv("EXECUTOR_FILL_CONFIRM_SECONDS", "5"))
        self.fill_poll_interval = float(os.getenv("EXECUTOR_FILL_POLL_INTERVAL", "1"))
        
        # Initialize Alpaca trading client
        if not self.settings.has_alpaca_credentials:
            raise ValueError("Missing Alpaca API credentials in configuration")
        
        self.trading_client = TradingClient(
            api_key=self.settings.apca_api_key_id,
            secret_key=self.settings.apca_api_secret_key,
            paper=self.settings.is_paper_trading
        )
        
        # Performance tracking
        self.execution_timer = MonotonicTimer()
        self.total_volume = Decimal('0.0')
        self.successful_executions = 0
        self.failed_executions = 0
        
        logger.info(f"Enhanced Alpaca Executor initialized:")
        logger.info(f"  Paper Trading: {self.settings.is_paper_trading}")
        logger.info(f"  Base URL: {self.settings.apca_api_base_url}")
        logger.info(f"  Rate Limit: 190/minute (with buffer)")
        logger.info(f"  Retry Logic: Exponential backoff + jitter")
    
    async def verify_account_status(self) -> bool:
        """Verify Alpaca account with retry logic"""
        return await self._execute_with_retry(
            operation="get_account",
            func=lambda: self.trading_client.get_account(),
            description="Account verification"
        )
    
    async def _execute_with_retry(self, operation: str, func, description: str = None) -> any:
        """Execute function with exponential backoff retry logic"""
        retry_config = self.retry_configs.get(operation, self.retry_configs["submit_order"])
        
        for attempt in range(1, retry_config.max_attempts + 1):
            try:
                # Check rate limits before making API call
                if not self.rate_manager.can_make_trading_call():
                    wait_time = self.rate_manager.get_wait_time()
                    logger.warning(f"Rate limit reached, waiting {wait_time:.1f}s for {description}")
                    await asyncio.sleep(wait_time)
                
                # Execute the function
                with TimingContext(f"{operation}_attempt_{attempt}") as timer:
                    result = func()
                
                # Record successful API call
                self.rate_manager.record_trading_call(operation)

                # Epic 4: Record successful retry after 429 (if this was a retry)
                if attempt > 1:
                    logger.info(f"✅ {description} succeeded on attempt {attempt}")
                    self.metrics.broker_429_retry(operation, success=True)

                return result
                
            except Exception as e:
                error_msg = str(e).lower()
                # alpaca-py APIError exposes the HTTP status; str(e) is only the JSON body
                status_code = getattr(e, "status_code", None)
                if not isinstance(status_code, int):
                    status_code = None
                is_rate_limit = status_code == 429 or "429" in error_msg or "rate limit" in error_msg
                is_server_error = status_code is not None and 500 <= status_code < 600
                is_client_error = status_code is not None and 400 <= status_code < 500 and not is_rate_limit

                # Handle specific error types
                if is_rate_limit:
                    # Rate limit hit - wait longer
                    wait_time = retry_config.calculate_delay(attempt) * 2  # Double wait for rate limits
                    logger.warning(f"Rate limit error on attempt {attempt}/{retry_config.max_attempts} for {description}: {e}")

                    # Epic 4: Record 429 retry metric
                    self.metrics.broker_429_retry(operation, success=False)

                    if attempt < retry_config.max_attempts:
                        logger.info(f"Waiting {wait_time:.1f}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # All retries failed
                        self.metrics.broker_429_retry(operation, success=False)
                
                elif is_server_error:  # 5xx server errors
                    # Server error - retry with exponential backoff
                    wait_time = retry_config.calculate_delay(attempt)
                    logger.warning(f"Server error on attempt {attempt}/{retry_config.max_attempts} for {description}: {e}")
                    
                    if attempt < retry_config.max_attempts:
                        logger.info(f"Waiting {wait_time:.1f}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue
                
                elif is_client_error:
                    # 4xx client errors (except 429) - don't retry
                    logger.error(f"Client error for {description}: {e}")
                    self.rate_manager.failed_calls += 1
                    raise e
                
                else:
                    # Other errors - retry with normal backoff
                    wait_time = retry_config.calculate_delay(attempt)
                    logger.warning(f"Error on attempt {attempt}/{retry_config.max_attempts} for {description}: {e}")
                    
                    if attempt < retry_config.max_attempts:
                        logger.info(f"Waiting {wait_time:.1f}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue
                
                # If we reach here, all retries failed
                logger.error(f"All {retry_config.max_attempts} attempts failed for {description}: {e}")
                self.rate_manager.failed_calls += 1
                raise e
    
    async def get_existing_order(self, client_order_id: str):
        """Look up an order by client_order_id. Returns None when it does not exist (404)."""
        try:
            if not self.rate_manager.can_make_trading_call():
                await asyncio.sleep(self.rate_manager.get_wait_time())
            order = self.trading_client.get_order_by_client_id(client_order_id)
            self.rate_manager.record_trading_call("get_order_by_client_id")
            return order
        except Exception as e:
            status_code = getattr(e, "status_code", None)
            msg = str(e).lower()
            if status_code == 404 or "not found" in msg or "does not exist" in msg:
                return None
            if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
                # Transient broker problem: use the retry path once
                try:
                    return await self._execute_with_retry(
                        operation="get_orders",
                        func=lambda: self.trading_client.get_order_by_client_id(client_order_id),
                        description=f"Check existing order {client_order_id}"
                    )
                except Exception as retry_error:
                    logger.warning(f"Error checking existing order (continuing with submit): {retry_error}")
                    return None
            logger.warning(f"Error checking existing order (continuing with submit): {e}")
            return None

    def is_intent_expired(self, order_intent: OrderIntent) -> bool:
        """Intents carry a valid_until set by the risk manager; never act on stale redeliveries."""
        if not order_intent.valid_until:
            return False
        valid_until = order_intent.valid_until
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=TimeUtils.utc_now().tzinfo)
        return TimeUtils.utc_now() > valid_until

    def _handle_expired_intent(self, order_intent: OrderIntent):
        logger.warning(
            f"Order intent expired (valid_until={order_intent.valid_until}), skipping: {order_intent.client_order_id}"
        )
        self.bus.publish_system_event(
            event_type="order_intent_expired",
            source="executor",
            data={
                "symbol": order_intent.symbol,
                "side": order_intent.side,
                "quantity": float(order_intent.quantity),
                "client_order_id": order_intent.client_order_id,
                "valid_until": order_intent.valid_until.isoformat() if order_intent.valid_until else None
            }
        )

    async def confirm_fill(self, broker_id: str, order) -> Optional[OrderFill]:
        """Poll the broker briefly after submission and report the REAL fill (price/qty).
        Returns None if the order is still open; the background monitor takes over then."""
        latest = order
        deadline = MonotonicTimer.current() + self.fill_confirm_seconds
        while True:
            status = order_status_str(latest)
            if status in TERMINAL_STATUSES or status == "partially_filled":
                break
            if status not in OPEN_STATUSES:
                logger.debug(f"Unknown broker status '{status}' for {broker_id}; leaving to monitor")
                return None
            if MonotonicTimer.current() >= deadline:
                logger.info(f"Order {broker_id} still '{status}' after {self.fill_confirm_seconds}s; monitor will track it")
                return None
            await asyncio.sleep(self.fill_poll_interval)
            try:
                latest = await self._execute_with_retry(
                    operation="get_orders",
                    func=lambda: self.trading_client.get_order_by_id(broker_id),
                    description=f"Poll order {broker_id}"
                )
            except Exception as e:
                logger.warning(f"Could not poll order {broker_id}: {e}")
                return None
        return self.apply_broker_order_state(latest)

    def apply_broker_order_state(self, alpaca_order) -> Optional[OrderFill]:
        """Feed a broker order snapshot into the tracker; returns a new OrderFill if any."""
        broker_id = str(alpaca_order.id)
        status = order_status_str(alpaca_order)
        filled_qty = Decimal(str(alpaca_order.filled_qty or 0))
        fill_price = Decimal(str(alpaca_order.filled_avg_price)) if alpaca_order.filled_avg_price else None
        logger.debug(f"Order update: {broker_id} status={status} filled_qty={filled_qty} avg_price={fill_price}")
        return self.order_tracker.update_order_status(broker_id, status, filled_qty, fill_price)

    def convert_side(self, side: SignalSide) -> AlpacaOrderSide:
        """Convert our SignalSide to Alpaca OrderSide"""
        if side == SignalSide.BUY:
            return AlpacaOrderSide.BUY
        elif side == SignalSide.SELL:
            return AlpacaOrderSide.SELL
        else:
            raise ValueError(f"Invalid order side: {side}")
    
    async def check_position_for_sell(self, symbol: str, required_quantity: Decimal) -> Tuple[bool, Decimal]:
        """Check if we have sufficient position for sell order"""
        try:
            position_data = await self._execute_with_retry(
                operation="get_position",
                func=lambda: self.trading_client.get_open_position(symbol),
                description=f"Position check for {symbol}"
            )
            
            current_qty = Decimal(str(position_data.qty))
            has_sufficient = current_qty >= required_quantity
            
            logger.debug(f"Position check {symbol}: have {current_qty}, need {required_quantity}, sufficient: {has_sufficient}")
            return has_sufficient, current_qty
            
        except Exception as e:
            if "position does not exist" in str(e).lower():
                logger.debug(f"No position found for {symbol}")
                return False, Decimal('0')
            else:
                logger.error(f"Error checking position for {symbol}: {e}")
                return False, Decimal('0')
    
    async def execute_order_with_validation(self, order_intent):
        from lib.execution_safety import verify_mode
        verify_mode(self.settings)
        from uuid import uuid4
        redis = self.bus.redis_client
        token = str(uuid4())
        # One final validation/submission at a time across executors.
        if not redis.set('trading:submission_lock', token, nx=True, ex=120):
            raise RuntimeError('Another executor is validating an order')
        self._submission_token = token
        try:
            return await self._execute_order_unlocked(order_intent)
        finally:
            # WATCH prevents deleting a newer owner's lock.
            with redis.pipeline() as pipe:
                pipe.watch('trading:submission_lock')
                if pipe.get('trading:submission_lock') == token:
                    pipe.multi()
                    pipe.delete('trading:submission_lock')
                    pipe.execute()

    def _submit_guarded(self, intent, request):
        from lib.execution_safety import check_order
        redis = self.bus.redis_client
        if redis.get('trading:submission_lock') != self._submission_token:
            raise RuntimeError('Submission lease expired')
        check_order(self.trading_client, redis, self.settings, intent)
        if redis.get('trading:emergency_stop'):
            raise RuntimeError('Emergency stop is active')
        return self.trading_client.submit_order(request)

    async def _execute_order_unlocked(self, order_intent: OrderIntent) -> Optional[OrderFill]:
        """Execute order with comprehensive validation and error handling"""
        # Start order execution timing
        with time_order_execution(order_intent.symbol, order_intent.order_type.value):
            try:
                logger.info(f"Executing order: {order_intent.side} {order_intent.quantity} {order_intent.symbol} @ ${order_intent.price or 'MARKET'}")

                # Record order submission attempt
                self.metrics.order_submitted(
                    symbol=order_intent.symbol,
                    side=order_intent.side.value,
                    order_type=order_intent.order_type.value
                )

                # Validate sell orders have sufficient position
                if order_intent.side == SignalSide.SELL:
                    has_position, current_qty = await self.check_position_for_sell(order_intent.symbol, order_intent.quantity)

                    if not has_position:
                        logger.warning(f"Insufficient position for sell: need {order_intent.quantity}, have {current_qty}")

                        # Publish error event
                        self.bus.publish_system_event(
                            event_type="order_validation_failed",
                            source="executor",
                            data={
                                "symbol": order_intent.symbol,
                                "reason": "insufficient_position",
                                "required_quantity": float(order_intent.quantity),
                                "current_position": float(current_qty),
                                "client_order_id": order_intent.client_order_id
                            }
                        )
                        return None

                # Epic 4: Check if order already exists by client_order_id (idempotency)
                existing_order = await self.get_existing_order(order_intent.client_order_id)
                if existing_order:
                    existing_id = str(existing_order.id)
                    existing_status = order_status_str(existing_order)
                    logger.warning(f"💡 Order already exists (idempotency): {order_intent.client_order_id} -> {existing_id}")

                    # Record duplicate blocked metric
                    self.metrics.duplicate_order_blocked(
                        symbol=order_intent.symbol,
                        client_order_id=order_intent.client_order_id
                    )

                    # Track it (if not already tracked) and report any fill the broker has for it
                    if existing_id not in self.order_tracker.orders_by_broker_id:
                        self.order_tracker.add_pending_order(order_intent, existing_id)

                    if existing_status in ('filled', 'partially_filled'):
                        order_fill = self.apply_broker_order_state(existing_order)
                        if order_fill:
                            logger.info(f"✅ Existing order already filled: {order_intent.client_order_id}")
                            return order_fill

                    # Order exists but not filled yet (or fill already reported)
                    logger.info(f"Order exists in state: {existing_status}")
                    return None

                # Convert order side
                alpaca_side = self.convert_side(order_intent.side)

                if not order_intent.price:
                    raise ValueError('Reference price required for bounded execution')
                collar = Decimal(order_intent.max_slippage_bps or 0) / Decimal(10000)
                limit = order_intent.price * (1 + collar if order_intent.side == SignalSide.BUY else 1 - collar)
                if order_intent.order_type == OrderType.LIMIT:
                    limit = order_intent.price
                extras = {}
                if order_intent.side == SignalSide.BUY:
                    if not order_intent.stop_loss or not order_intent.take_profit:
                        raise ValueError('Buy orders require protective exits')
                    extras = dict(order_class=OrderClass.BRACKET,
                                  stop_loss=StopLossRequest(stop_price=float(order_intent.stop_loss)),
                                  take_profit=TakeProfitRequest(limit_price=float(order_intent.take_profit)))
                order_request = LimitOrderRequest(symbol=order_intent.symbol, qty=float(order_intent.quantity),
                    side=alpaca_side, time_in_force=TimeInForce.GTC, limit_price=float(limit.quantize(Decimal('.01'))),
                    client_order_id=order_intent.client_order_id, **extras)

                # Submit order with retry logic
                order_response = await self._execute_with_retry(
                    operation="submit_order",
                    func=lambda: self._submit_guarded(order_intent, order_request),
                    description=f"Order submission for {order_intent.symbol}"
                )

                # Add to order tracker (broker ids are UUIDs in alpaca-py; our models want str)
                broker_id = str(order_response.id)
                self.order_tracker.add_pending_order(order_intent, broker_id)

                logger.info(f"✅ Order submitted successfully:")
                logger.info(f"  Alpaca Order ID: {broker_id}")
                logger.info(f"  Status: {order_status_str(order_response)}")
                logger.info(f"  Symbol: {order_response.symbol}")
                logger.info(f"  Quantity: {order_response.qty}")
                logger.info(f"  Side: {order_response.side}")

                # Update metrics
                self.successful_executions += 1
                self.total_volume += Decimal(str(order_intent.quantity)) * Decimal(str(order_intent.price or 0))

                # Report the REAL fill from the broker (never fabricate one at the signal price).
                # If it has not filled within fill_confirm_seconds, monitor_pending_orders takes over.
                order_fill = await self.confirm_fill(broker_id, order_response)
                if order_fill:
                    logger.info(f"Order filled: {order_fill.symbol} {order_fill.fill_quantity}@${order_fill.fill_price:.2f}")
                return order_fill

            except Exception as e:
                self.failed_executions += 1
                logger.error(f"Failed to execute order for {order_intent.symbol}: {e}")

                # Record failure metrics
                error_type = "alpaca_error" if "alpaca" in str(e).lower() else "execution_error"
                self.metrics.order_failed(
                    symbol=order_intent.symbol,
                    side=order_intent.side.value,
                    error_type=error_type
                )

                # Publish error event
                self.bus.publish_system_event(
                    event_type="order_execution_failed",
                    source="executor",
                    data={
                        "symbol": order_intent.symbol,
                        "side": order_intent.side,
                        "quantity": float(order_intent.quantity),
                        "error": str(e),
                        "client_order_id": order_intent.client_order_id
                    }
                )

                raise
    
    async def monitor_pending_orders(self):
        """Monitor pending orders for fills, status updates and timeouts (Epic 5)"""
        logger.info("Starting enhanced order monitoring with FSM timeouts...")

        while self.running:
            try:
                self.order_tracker.drain_outbox(self.bus)
                if self.bus.redis_client.get('trading:emergency_stop'):
                    for broker_id in list(self.order_tracker.get_pending_orders()):
                        intent = self.order_tracker.orders_by_broker_id[broker_id]['intent']
                        if intent.side == SignalSide.BUY:
                            self.trading_client.cancel_order_by_id(broker_id)
                    self.bus.redis_client.set('trading:stop_ack', TimeUtils.utc_now().isoformat())
                # Epic 5: Check for order timeouts FIRST
                timed_out = await self.order_tracker.check_timeouts()

                for broker_id in timed_out:
                    # Attempt to cancel timed out order
                    try:
                        logger.warning(f"⏰ Canceling timed out order: {broker_id}")

                        await self._execute_with_retry(
                            operation="cancel_order",
                            func=lambda: self.trading_client.cancel_order_by_id(broker_id),
                            description=f"Cancel timed out order {broker_id}"
                        )

                        logger.info(f"✅ Successfully canceled timed out order: {broker_id}")

                        # Publish timeout event to system stream
                        fsm = self.order_tracker.order_fsms.get(broker_id)
                        self.bus.publish_system_event(
                            event_type="order_timeout",
                            source="executor",
                            data={
                                "broker_order_id": broker_id,
                                "reason": "timeout",
                                "fsm_state": fsm.to_dict() if fsm else {}
                            }
                        )

                    except Exception as e:
                        logger.error(f"Failed to cancel timed out order {broker_id}: {e}")

                # Monitor pending orders. get_orders() without a filter only returns OPEN
                # orders, so filled ones would never show up; fetch each pending id directly.
                pending_orders = self.order_tracker.get_pending_orders()

                if pending_orders:
                    logger.debug(f"Monitoring {len(pending_orders)} pending orders")

                    for broker_id in list(pending_orders):
                        try:
                            order = await self._execute_with_retry(
                                operation="get_orders",
                                func=lambda bid=broker_id: self.trading_client.get_order_by_id(bid),
                                description=f"Get order status {broker_id}"
                            )
                            await self._process_order_update(order)
                        except Exception as e:
                            logger.error(f"Error fetching order status for {broker_id}: {e}")

                await asyncio.sleep(15)  # Check every 15 seconds

            except Exception as e:
                logger.error(f"Error in order monitoring loop: {e}")
                await asyncio.sleep(30)  # Wait longer on error
    
    async def _process_order_update(self, alpaca_order):
        """Process order status update from Alpaca"""
        try:
            order_fill = self.apply_broker_order_state(alpaca_order)

            if order_fill:
                # Mark fill as processed to prevent duplicates
                if not self.deduplication.is_fill_processed(order_fill):
                    # Record fill metrics
                    fill_type = "full" if order_fill.fill_quantity == order_fill.quantity else "partial"
                    self.metrics.order_filled(
                        symbol=order_fill.symbol,
                        side=order_fill.side.value,
                        fill_type=fill_type,
                        value=float(order_fill.total_value)
                    )

                    # Publish fill event
                    self.order_tracker.drain_outbox(self.bus)

                    logger.info(f"✅ Order fill published: {order_fill.symbol} {order_fill.fill_quantity}@${order_fill.fill_price:.2f}")
                    
                    # Publish execution event
                    self.bus.publish_system_event(
                        event_type="order_filled",
                        source="executor",
                        data={
                            "symbol": order_fill.symbol,
                            "side": order_fill.side,
                            "quantity": float(order_fill.quantity),
                            "fill_price": float(order_fill.fill_price),
                            "fill_quantity": float(order_fill.fill_quantity),
                            "total_value": float(order_fill.total_value),
                            "broker_order_id": order_fill.broker_order_id,
                            "status": order_fill.status
                        }
                    )
                else:
                    logger.debug(f"Fill already processed: {order_fill.broker_order_id}")
            
        except Exception as e:
            logger.error(f"Error processing order update: {e}")
    
    async def consume_order_intents(self):
        """Consume order intents from message bus with Streams-optimized processing"""
        logger.info("Starting enhanced order intent processing...")

        # Check if we're using Streams backend for optimized consumption
        if hasattr(self.bus.backend, 'consume_with_handler') and self.bus.get_stats().get('backend') == 'streams':
            logger.info("Using Redis Streams optimized consumption with safe ACK pattern")

            async def order_handler(msg_data: dict) -> bool:
                """Handler for Streams-based order intent processing"""
                try:
                    if not self.running:
                        return False

                    # Parse order intent from message data
                    if msg_data.get("type") != "order_intent":
                        return True  # ACK non-order messages

                    order_data = json.loads(msg_data["data"])
                    order_intent = OrderIntent.model_validate(order_data)

                    logger.info(f"Received order intent: {order_intent.side} {order_intent.quantity} {order_intent.symbol} @ ${order_intent.price or 'MARKET'}")

                    # Check for duplicate processing (executor-side key; the risk manager's
                    # dedup:order key is set before publishing and must not be consulted here)
                    if self.deduplication.is_order_executed(order_intent):
                        logger.debug(f"Order intent already executed: {order_intent.client_order_id}")
                        return True  # ACK duplicate orders

                    if self.is_intent_expired(order_intent):
                        self._handle_expired_intent(order_intent)
                        return True  # ACK stale intents, never submit them

                    # Execute order
                    order_fill = await self.execute_order_with_validation(order_intent)
                    if order_intent.client_order_id not in self.order_tracker.orders_by_client_id:
                        raise RuntimeError('Intent not submitted; retry required')
                    self.order_tracker.drain_outbox(self.bus)
                    self.deduplication.mark_order_executed(
                        order_intent, order_fill.broker_order_id if order_fill else None
                    )

                    if order_fill:
                        # Mark fill as processed and publish
                        if not self.deduplication.is_fill_processed(order_fill):
                            self.order_tracker.drain_outbox(self.bus)
                            logger.info(f"✅ Order executed and published: {order_fill.symbol} {order_fill.fill_quantity}@${order_fill.fill_price:.2f}")
                        else:
                            logger.debug(f"Fill already processed: {order_fill.broker_order_id}")

                    # Return True to ACK the message (only after successful processing)
                    return True

                except Exception as e:
                    logger.error(f"Error processing order intent in handler: {e}")
                    # Return False to NOT ACK the message (it will remain pending for retry)
                    return False

            # Use the safe Streams consumption pattern
            await self.bus.backend.consume_with_handler("orders", order_handler)

        else:
            # Fallback to Pub/Sub pattern for backward compatibility
            logger.info("Using Pub/Sub consumption pattern")
            async for order_intent in self.bus.subscribe_order_intents():
                if not self.running:
                    break

                try:
                    logger.info(f"Received order intent: {order_intent.side} {order_intent.quantity} {order_intent.symbol} @ ${order_intent.price or 'MARKET'}")

                    # Check for duplicate processing (executor-side key)
                    if self.deduplication.is_order_executed(order_intent):
                        logger.debug(f"Order intent already executed: {order_intent.client_order_id}")
                        continue

                    if self.is_intent_expired(order_intent):
                        self._handle_expired_intent(order_intent)
                        continue

                    # Execute order
                    order_fill = await self.execute_order_with_validation(order_intent)
                    if order_intent.client_order_id not in self.order_tracker.orders_by_client_id:
                        raise RuntimeError('Intent not submitted; retry required')
                    self.order_tracker.drain_outbox(self.bus)
                    self.deduplication.mark_order_executed(
                        order_intent, order_fill.broker_order_id if order_fill else None
                    )

                    if order_fill:
                        # Mark fill as processed and publish
                        if not self.deduplication.is_fill_processed(order_fill):
                            self.order_tracker.drain_outbox(self.bus)
                            logger.info(f"✅ Order executed and published: {order_fill.symbol} {order_fill.fill_quantity}@${order_fill.fill_price:.2f}")
                        else:
                            logger.debug(f"Fill already processed: {order_fill.broker_order_id}")

                except Exception as e:
                    logger.error(f"Error processing order intent: {e}")
                    await asyncio.sleep(1)  # Brief pause on error
    
    def get_comprehensive_stats(self) -> Dict:
        """Get comprehensive executor statistics"""
        rate_stats = self.rate_manager.get_stats()
        order_stats = self.order_tracker.get_stats()
        
        return {
            "performance": {
                "successful_executions": self.successful_executions,
                "failed_executions": self.failed_executions,
                "total_volume": float(self.total_volume),
                "success_rate": self.successful_executions / max(1, self.successful_executions + self.failed_executions),
                "uptime_seconds": self.execution_timer.elapsed_seconds()
            },
            "order_tracking": order_stats,
            "rate_management": rate_stats,
            "deduplication": self.deduplication.get_comprehensive_stats(),
            "alpaca_connection": {
                "paper_trading": self.settings.is_paper_trading,
                "base_url": self.settings.apca_api_base_url
            },
            "last_updated": TimeUtils.utc_now().isoformat()
        }
    
    async def start(self):
        """Start enhanced executor"""
        logger.info("Starting Enhanced Alpaca Executor...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False
        
        # Mark service start in metrics
        self.metrics.mark_service_start()

        from lib.execution_safety import verify_mode
        verify_mode(self.settings)
        # Verify account status
        try:
            account = await self.verify_account_status()
            logger.info("Account verified:")
            logger.info(f"  Status: {account.status}")
            logger.info(f"  Buying Power: ${float(account.buying_power):,.2f}")
            logger.info(f"  Cash: ${float(account.cash):,.2f}")
            logger.info(f"  Portfolio Value: ${float(account.portfolio_value):,.2f}")
        except Exception as e:
            logger.error(f"Account verification failed: {e}")
            return False
        
        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="executor",
            data={
                "enhanced_features": [
                    "exponential_backoff_retry",
                    "intelligent_rate_limiting",
                    "partial_fills_handling",
                    "comprehensive_error_recovery",
                    "performance_monitoring"
                ],
                "broker": "alpaca",
                "paper_trading": self.settings.is_paper_trading,
                "account_verified": True,
                "rate_limits": {
                    "trading_api": "190/minute"
                }
            }
        )
        
        self.running = True
        
        try:
            # Start monitoring and consuming tasks
            monitor_task = asyncio.create_task(self.monitor_pending_orders())
            consume_task = asyncio.create_task(self.consume_order_intents())
            
            # Wait for both tasks
            await asyncio.gather(monitor_task, consume_task)
            
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop enhanced executor"""
        logger.info("Stopping Enhanced Executor...")
        self.running = False

        # Mark service stop in metrics
        self.metrics.mark_service_stop()

        # Show final statistics
        final_stats = self.get_comprehensive_stats()
        
        logger.info("Final Statistics:")
        logger.info(f"  Successful Executions: {self.successful_executions}")
        logger.info(f"  Failed Executions: {self.failed_executions}")
        logger.info(f"  Success Rate: {final_stats['performance']['success_rate']:.1%}")
        logger.info(f"  Total Volume: ${final_stats['performance']['total_volume']:,.2f}")
        
        # Cancel only the orders THIS executor submitted and still tracks as open.
        # (cancel_orders() would wipe every open order in the account, including manual ones.)
        pending_orders = self.order_tracker.get_pending_orders()
        if pending_orders:
            logger.info(f"Canceling {len(pending_orders)} pending orders...")
            for broker_id in list(pending_orders):
                try:
                    await self._execute_with_retry(
                        operation="cancel_order",
                        func=lambda bid=broker_id: self.trading_client.cancel_order_by_id(bid),
                        description=f"Cancel pending order {broker_id}"
                    )
                except Exception as e:
                    logger.error(f"Error canceling order {broker_id}: {e}")
        
        # Publish service stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="executor",
                data={
                    "reason": "graceful_shutdown",
                    "final_stats": final_stats
                }
            )
            self.bus.disconnect()


async def main():
    """Main entry point"""
    try:
        executor = EnhancedAlpacaExecutor()
        await executor.start()
        
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
# --- Back-compat para tests ---
if 'AlpacaExecutor' not in globals() and 'EnhancedAlpacaExecutor' in globals():
    AlpacaExecutor = EnhancedAlpacaExecutor
