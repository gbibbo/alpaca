#!/usr/bin/env python3
"""
apps/risk_manager/main.py
Enhanced Risk Manager with ChatGPT's recommended improvements
- Timezone-aware validation using US/Eastern market time
- Monotonic time-based rate limiting 
- Persistent deduplication with Redis
- Multi-layer validation with detailed logging
- Circuit breakers and kill switches
- Market hours validation using Clock API
"""

import os
import sys
import errno
import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from decimal import ROUND_DOWN
from lib.models import Signal, OrderIntent, SignalSide, OrderType
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings
from lib.strategy_base import registered_names
from lib.time_utils import (
    TimeUtils, MonotonicTimer, RateLimitWindow, TimingContext,
    ORDER_RATE_LIMITER, SIGNAL_RATE_LIMITER, check_alpaca_rate_limit,
    record_alpaca_call
)
from lib.deduplication import get_deduplication_service
from lib.metrics_helpers import (
    RiskManagerMetrics, BusMetrics, time_bus_processing,
    start_metrics_server, find_available_port
)

# Import enhanced market hours validator
from apps.risk_manager.market_hours import MarketHoursValidator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker for emergency stops and error rate monitoring"""
    
    def __init__(self, name: str, error_threshold: int = 5, time_window: int = 300,
                 cooldown_seconds: Optional[int] = None):
        self.name = name
        self.error_threshold = error_threshold
        self.time_window = time_window  # 5 minutes default
        # After this long in the open state the breaker auto-closes (half-open) so that a
        # transient burst of errors cannot block trading until a manual restart.
        self.cooldown_seconds = cooldown_seconds if cooldown_seconds is not None else time_window

        # Error tracking using monotonic time
        self.errors = []  # List of (monotonic_time, error_info)
        self.is_open = False
        self.opened_at = None
        self.manual_override = False
        
    def record_error(self, error_info: str):
        """Record an error occurrence"""
        current_time = MonotonicTimer.current()
        self.errors.append((current_time, error_info))
        
        # Clean old errors
        self._cleanup_old_errors()
        
        # Check if threshold exceeded
        if len(self.errors) >= self.error_threshold:
            self._open_circuit(f"Error threshold exceeded: {len(self.errors)} errors")
    
    def record_success(self):
        """Record a successful operation"""
        if self.is_open and not self.manual_override:
            # If circuit is open due to errors (not manual), consider closing
            if len(self.errors) < self.error_threshold // 2:
                self._close_circuit("Error rate improved")
    
    def _cleanup_old_errors(self):
        """Remove errors outside the time window"""
        current_time = MonotonicTimer.current()
        cutoff_time = current_time - self.time_window
        
        self.errors = [err for err in self.errors if err[0] > cutoff_time]
    
    def _open_circuit(self, reason: str):
        """Open the circuit breaker"""
        if not self.is_open:
            self.is_open = True
            self.opened_at = MonotonicTimer.current()
            logger.error(f"🚨 Circuit breaker '{self.name}' OPENED: {reason}")
    
    def _close_circuit(self, reason: str):
        """Close the circuit breaker"""
        if self.is_open and not self.manual_override:
            self.is_open = False
            self.opened_at = None
            logger.info(f"✅ Circuit breaker '{self.name}' CLOSED: {reason}")
    
    def is_blocked(self) -> Tuple[bool, str]:
        """Check if operations should be blocked"""
        if self.manual_override:
            return True, f"Manual override active"

        if self.is_open:
            # Half-open: once the cooldown has elapsed, let traffic through again.
            if self.opened_at is not None and MonotonicTimer.since(self.opened_at) >= self.cooldown_seconds:
                self.errors = []
                self._close_circuit(f"Cooldown of {self.cooldown_seconds}s elapsed")
                return False, "Circuit breaker closed (cooldown elapsed)"
            return True, f"Circuit breaker open - {len(self.errors)} errors in last {self.time_window}s"

        return False, "Circuit breaker closed"
    
    def manual_open(self, reason: str = "Manual emergency stop"):
        """Manually open circuit breaker"""
        self.manual_override = True
        self._open_circuit(reason)
    
    def manual_close(self, reason: str = "Manual reset"):
        """Manually close circuit breaker"""
        self.manual_override = False
        self._close_circuit(reason)
    
    def get_stats(self) -> Dict:
        """Get circuit breaker statistics"""
        self._cleanup_old_errors()
        
        return {
            "name": self.name,
            "is_open": self.is_open,
            "manual_override": self.manual_override,
            "error_count": len(self.errors),
            "error_threshold": self.error_threshold,
            "time_window": self.time_window,
            "opened_at": self.opened_at,
            "recent_errors": [err[1] for err in self.errors[-5:]]  # Last 5 errors
        }


class EnhancedRiskManager:
    """
    Enhanced Risk Manager implementing ChatGPT's recommendations
    - Multi-layer validation with detailed logging
    - Timezone-aware market hours checking
    - Monotonic time-based rate limiting
    - Persistent deduplication with Redis
    - Circuit breakers and emergency controls
    """
    
    def __init__(self):
        os.environ["SERVICE_NAME"] = "risk_manager"
        self.settings = get_settings()
        self.bus = get_bus()
        self.running = False

        # Initialize enhanced services
        self.deduplication = get_deduplication_service()

        # Initialize market hours validator with optional Alpaca Clock API
        # Try to get Alpaca trading client if available
        alpaca_client = None
        if self.settings.has_alpaca_credentials:
            try:
                from alpaca.trading.client import TradingClient
                alpaca_client = TradingClient(
                    api_key=self.settings.apca_api_key_id,
                    secret_key=self.settings.apca_api_secret_key,
                    paper=self.settings.is_paper_trading
                )
                logger.info("Connected to Alpaca Clock API for market hours validation")
            except Exception as e:
                logger.warning(f"Could not initialize Alpaca Clock API: {e}")

        self.market_validator = MarketHoursValidator(alpaca_client)
        self.alpaca_client = alpaca_client
        self._portfolio_value_cache: Optional[Decimal] = None
        self._portfolio_value_cached_at: float = 0.0
        self._positions_cache: Dict[str, Dict[str, Decimal]] = {}
        self._positions_cached_at: float = 0.0

        # Signal sources we accept: every registered strategy + manual API, or an explicit override
        override = [s.strip().lower() for s in os.getenv("ALLOWED_SIGNAL_SOURCES", "").split(",") if s.strip()]
        self.allowed_sources = set(override) if override else set(registered_names()) | {"manual_api"}

        # Initialize metrics
        self.metrics = RiskManagerMetrics()

        # Start metrics server
        try:
            metrics_port = int(os.getenv("RISK_METRICS_PORT", "8011"))
            start_metrics_server(metrics_port)
            logger.info(f"📊 Risk Manager metrics available at http://localhost:{metrics_port}/metrics")
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
        
        # Rate limiters using monotonic time
        self.order_rate_limiter = RateLimitWindow(
            window_seconds=60, 
            max_requests=self.settings.max_orders_per_minute
        )
        self.signal_rate_limiter = RateLimitWindow(
            window_seconds=300,  # 5 minutes 
            max_requests=self.settings.max_signals_per_5min
        )
        
        # Circuit breakers for different failure modes
        self.circuit_breakers = {
            "order_execution": CircuitBreaker("OrderExecution", error_threshold=3, time_window=300),
            "market_data": CircuitBreaker("MarketData", error_threshold=5, time_window=600),
            "risk_validation": CircuitBreaker("RiskValidation", error_threshold=10, time_window=300)
        }
        
        # Emergency stop state
        self.emergency_stop = False
        self.emergency_reason = None
        self.emergency_activated_at = None
        
        # Performance tracking
        self.validation_timer = MonotonicTimer()
        self.signals_processed = 0
        self.signals_approved = 0
        self.signals_rejected = 0
        self.orders_created = 0
        self.validation_errors = 0
        
        # Daily loss tracking (reset at market open)
        self.daily_pnl = Decimal('0.0')
        self.daily_reset_time = None
        
        logger.info("Enhanced Risk Manager initialized with:")
        logger.info(f"  Order rate limit: {self.settings.max_orders_per_minute}/minute")
        logger.info(f"  Signal rate limit: {self.settings.max_signals_per_5min}/5min")
        logger.info(f"  Market timezone: {self.settings.market_timezone}")
        logger.info(f"  Max daily loss: {self.settings.max_daily_loss:.1%}")
        logger.info(f"  Max position size: {self.settings.max_position_size:.1%}")
    
    def activate_emergency_stop(self, reason: str = "Manual emergency stop"):
        """Activate emergency stop"""
        self.emergency_stop = True
        self.emergency_reason = reason
        self.emergency_activated_at = MonotonicTimer.current()
        
        # Open all circuit breakers
        for breaker in self.circuit_breakers.values():
            breaker.manual_open(f"Emergency stop: {reason}")
        
        # Publish emergency event
        self.bus.publish_system_event(
            event_type="emergency_stop_activated",
            source="risk_manager",
            data={
                "reason": reason,
                "activated_at": TimeUtils.utc_now().isoformat(),
                "market_time": TimeUtils.market_now().isoformat()
            }
        )
        
        logger.error(f"🚨 EMERGENCY STOP ACTIVATED: {reason}")
    
    def deactivate_emergency_stop(self, reason: str = "Manual reset"):
        """Deactivate emergency stop"""
        self.emergency_stop = False
        self.emergency_reason = None
        self.emergency_activated_at = None
        
        # Close circuit breakers (if not manually overridden)
        for breaker in self.circuit_breakers.values():
            breaker.manual_close(f"Emergency stop lifted: {reason}")
        
        # Publish recovery event
        self.bus.publish_system_event(
            event_type="emergency_stop_deactivated", 
            source="risk_manager",
            data={
                "reason": reason,
                "deactivated_at": TimeUtils.utc_now().isoformat(),
                "market_time": TimeUtils.market_now().isoformat()
            }
        )
        
        logger.info(f"✅ Emergency stop deactivated: {reason}")
    
    def validate_signal_comprehensive(self, signal: Signal) -> Tuple[bool, str]:
        """
        Comprehensive signal validation with all ChatGPT recommended checks
        Returns (is_valid, detailed_reason)
        """
        with TimingContext("signal_validation") as timer:
            
            # 1. Emergency Stop Check (highest priority)
            if self.emergency_stop or self.bus.redis_client.get("trading:emergency_stop"):
                return False, f"Emergency stop active: {self.emergency_reason}"
            
            # 2. Circuit Breaker Check
            for breaker_name, breaker in self.circuit_breakers.items():
                is_blocked, reason = breaker.is_blocked()
                if is_blocked:
                    return False, f"Circuit breaker {breaker_name}: {reason}"
            
            # 3. Market Hours Validation (timezone-aware)
            market_open, market_reason = self.market_validator.validate_trading_hours()
            if not market_open and signal.timeframe.value != "1d":
                return False, f"Market hours: {market_reason}"
            
            # 4. Deduplication Check (persistent)
            if self.deduplication.is_signal_processed(signal):
                return False, "Signal already processed (persistent deduplication)"
            
            # 5. Rate Limiting Check (monotonic time-based)
            if not self.signal_rate_limiter.can_make_request():
                stats = self.signal_rate_limiter.get_stats()
                time_until_next = self.signal_rate_limiter.time_until_next_slot()
                return False, f"Signal rate limit exceeded ({stats['current_requests']}/{stats['max_requests']}), wait {time_until_next:.1f}s"
            
            # 6. Signal Quality Validation
            if signal.confidence < Decimal('0.5'):
                return False, f"Confidence too low: {signal.confidence} < 0.5"
            
            if hasattr(signal, 'expire_seconds') and signal.expire_seconds and signal.timestamp:
                signal_ts = signal.timestamp
                if signal_ts.tzinfo is None:
                    signal_ts = signal_ts.replace(tzinfo=timezone.utc)
                if TimeUtils.utc_now() > (signal_ts + timedelta(seconds=signal.expire_seconds)):
                    return False, "Signal expired"
            
            # 7. Symbol Validation
            if signal.symbol not in self.settings.symbols_list:
                return False, f"Symbol {signal.symbol} not in allowed list: {self.settings.symbols_list}"
            
            # 8. Source Validation
            if signal.source not in self.allowed_sources:
                return False, f"Unknown signal source: {signal.source} (allowed: {sorted(self.allowed_sources)})"
            
            # All validations passed
            return True, "Signal validation successful"
    
    def get_portfolio_value(self, max_age_seconds: int = 60) -> Decimal:
        """Account equity from Alpaca (cached), falling back to a nominal 100k when unavailable."""
        now = MonotonicTimer.current()
        if self._portfolio_value_cache is not None and now - self._portfolio_value_cached_at < max_age_seconds:
            return self._portfolio_value_cache
        if self.alpaca_client is not None:
            try:
                account = self.alpaca_client.get_account()
                equity = Decimal(str(account.equity or account.portfolio_value or 0))
                if equity > 0:
                    self._portfolio_value_cache = equity
                    self._portfolio_value_cached_at = now
                    return equity
            except Exception as e:
                logger.warning(f"Could not fetch account equity, using fallback: {e}")
        raise RuntimeError('Account equity unavailable; refusing to increase risk')

    def get_positions(self, max_age_seconds: int = 30) -> Dict[str, Dict[str, Decimal]]:
        """Open positions from Alpaca as {symbol: {qty, market_value}} (cached)."""
        now = MonotonicTimer.current()
        if self._positions_cache and now - self._positions_cached_at < max_age_seconds:
            return self._positions_cache
        if self.alpaca_client is None:
            raise RuntimeError('Position source unavailable')
        try:
            positions = {}
            for p in self.alpaca_client.get_all_positions():
                qty = Decimal(str(p.qty))
                side = str(getattr(p, "side", "long")).lower()
                if side.endswith("short"):
                    qty = -abs(qty)
                positions[str(p.symbol).upper()] = {
                    "qty": qty,
                    "market_value": Decimal(str(p.market_value or 0)),
                }
            self._positions_cache = positions
            self._positions_cached_at = now
        except Exception as e:
            raise RuntimeError("Positions unavailable; refusing to trade") from e
        return self._positions_cache

    def calculate_position_size(self, signal: Signal, portfolio_value: Optional[Decimal] = None,
                                positions: Optional[Dict[str, Dict[str, Decimal]]] = None) -> Tuple[Decimal, str]:
        """
        Decide HOW MUCH to trade from live portfolio state.
        - BUY: risk_pct * equity scaled by confidence, capped so that the symbol's total exposure
          never exceeds max_position_size of equity (existing holdings count).
        - SELL: never more than the shares actually held (no accidental shorting).
        Returns (quantity, reasoning); quantity 0 means "do not trade".
        """
        if portfolio_value is None:
            portfolio_value = self.get_portfolio_value()
        if positions is None:
            positions = self.get_positions()
        try:
            price = Decimal(str(signal.price)) if signal.price and signal.price > 0 else None
            held = positions.get(signal.symbol.upper(), {})
            held_qty = held.get("qty", Decimal('0'))
            held_value = held.get("market_value")
            if held_value is None:
                held_value = held_qty * price if price else Decimal('0')

            max_position_value = portfolio_value * Decimal(str(self.settings.max_position_size))

            if signal.side == SignalSide.SELL:
                if held_qty <= 0:
                    return Decimal('0'), f"No long position in {signal.symbol} to sell (held: {held_qty})"
                # Sell the whole position (exit signal); strategies can later request partial exits via metadata
                quantity = held_qty
                reasoning = f"Exit: selling {quantity} of {held_qty} held (${held_value:.2f})"
                return quantity.quantize(Decimal('1'), rounding=ROUND_DOWN), reasoning

            # BUY
            base_risk_amount = portfolio_value * Decimal(str(self.settings.risk_pct))
            confidence_adjusted_risk = base_risk_amount / Decimal(str(self.settings.stop_loss_pct))
            room = max_position_value - held_value
            if room <= 0:
                return Decimal('0'), (f"Position limit reached for {signal.symbol}: holding ${held_value:.2f} "
                                      f"of max ${max_position_value:.2f} ({float(self.settings.max_position_size):.0%} of equity)")
            aggregate = sum((abs(p.get('market_value', Decimal(0))) for p in positions.values()), Decimal(0))
            aggregate_room = portfolio_value * Decimal(str(self.settings.max_portfolio_risk)) - aggregate
            position_value = min(confidence_adjusted_risk, room, aggregate_room)

            if price is None:
                return Decimal('0'), "Signal has no price; cannot size position"

            quantity = (position_value / price).quantize(Decimal('1'), rounding=ROUND_DOWN)
            if quantity < 1:
                return Decimal('0'), (f"Sized to ${position_value:.2f} at ${price:.2f} = less than 1 share")

            reasoning = (f"Risk budget ${confidence_adjusted_risk:.2f}, room ${room:.2f} "
                         f"(held ${held_value:.2f} / max ${max_position_value:.2f}), price ${price:.2f} -> {quantity} shares")
            return quantity, reasoning

        except Exception as e:
            logger.error(f"Position sizing error: {e}")
            return Decimal('0'), f"Error in calculation: {e}"
    
    def create_order_intent(self, signal: Signal, quantity: Optional[Decimal] = None) -> OrderIntent:
        """Create order intent from validated signal (quantity from calculate_position_size unless given)"""
        if quantity is None:
            quantity, size_reasoning = self.calculate_position_size(signal)
            if quantity <= 0:
                raise ValueError(f"Position sizing yielded no trade: {size_reasoning}")
        else:
            size_reasoning = "quantity supplied by caller"

        # Generate unique client order ID with risk manager prefix
        client_order_id = f"risk_{signal.signal_id.hex}"
        
        # Create order intent
        order_intent = OrderIntent(
            symbol=signal.symbol,
            timestamp=TimeUtils.utc_now(),
            side=signal.side,
            quantity=quantity,
            order_type=OrderType.MARKET,  # Default to market orders
            price=signal.price,
            client_order_id=client_order_id,
            signal_source=signal.source,
            stop_loss=signal.price * (1 - Decimal(str(self.settings.stop_loss_pct))) if signal.price and signal.side == SignalSide.BUY else None,
            take_profit=signal.price * (1 + Decimal(str(self.settings.take_profit_pct))) if signal.price and signal.side == SignalSide.BUY else None,
            risk_adjusted=True,
            max_slippage_bps=50,  # 0.5% max slippage
            valid_until=signal.timestamp + timedelta(seconds=signal.expire_seconds or 300)  # 5 minute expiry
        )
        
        logger.info(f"Created order intent: {order_intent.side} {order_intent.quantity} {order_intent.symbol}")
        logger.debug(f"Position sizing: {size_reasoning}")
        
        return order_intent
    
    async def process_signal(self, signal: Signal):
        """Process incoming signal with comprehensive validation"""
        self.signals_processed += 1

        try:
            logger.info(f"Processing signal: {signal.side} {signal.symbol} (confidence: {signal.confidence:.1%}) from {signal.source}")

            # Comprehensive validation
            is_valid, reason = self.validate_signal_comprehensive(signal)

            if not is_valid:
                self.signals_rejected += 1
                logger.warning(f"Signal rejected: {reason}")

                # Record rejection metrics
                self.metrics.signal_processed(signal.symbol, "rejected")
                self.metrics.risk_check_failed("signal_validation", reason)

                # Publish rejection event
                self.bus.publish_system_event(
                    event_type="signal_rejected",
                    source="risk_manager",
                    data={
                        "symbol": signal.symbol,
                        "side": signal.side,
                        "reason": reason,
                        "source": signal.source,
                        "confidence": float(signal.confidence)
                    }
                )

                # NOTE: a rejected signal is a normal outcome, not a fault. Feeding rejections
                # into the circuit breaker made "Circuit breaker ..." rejections re-trip the
                # breaker forever. Only genuine processing exceptions (below) count as errors.
                return

            # Order rate limit: check BEFORE marking the signal processed, otherwise the
            # signal is silently lost (it can never be re-evaluated once marked).
            if not self.order_rate_limiter.can_make_request():
                wait = self.order_rate_limiter.time_until_next_slot()
                reason = f"Order rate limit exceeded, retry in {wait:.1f}s"
                self.signals_rejected += 1
                logger.warning(f"Signal rejected: {reason}")
                self.metrics.signal_processed(signal.symbol, "rejected")
                self.metrics.risk_check_failed("order_rate_limit", reason)
                self.bus.publish_system_event(
                    event_type="signal_rejected",
                    source="risk_manager",
                    data={
                        "symbol": signal.symbol,
                        "side": signal.side,
                        "reason": reason,
                        "source": signal.source,
                        "confidence": float(signal.confidence)
                    }
                )
                return

            # Record successful validation
            self.circuit_breakers["risk_validation"].record_success()
            
            # Record rate limit usage
            self.signal_rate_limiter.record_request(f"{signal.symbol}_{signal.source}")
            
            # Size from live portfolio state; zero means the portfolio layer vetoed the trade
            quantity, size_reasoning = self.calculate_position_size(signal)
            if quantity <= 0:
                self.signals_rejected += 1
                logger.warning(f"Signal rejected by portfolio sizing: {size_reasoning}")
                self.metrics.signal_processed(signal.symbol, "rejected")
                self.metrics.risk_check_failed("position_sizing", size_reasoning)
                self.bus.publish_system_event(
                    event_type="signal_rejected",
                    source="risk_manager",
                    data={
                        "symbol": signal.symbol,
                        "side": signal.side,
                        "reason": size_reasoning,
                        "source": signal.source,
                        "confidence": float(signal.confidence)
                    }
                )
                return
            logger.info(f"Position sizing: {size_reasoning}")

            # Create order intent
            order_intent = self.create_order_intent(signal, quantity=quantity)

            # Record order rate limit usage
            self.order_rate_limiter.record_request(f"order_{order_intent.symbol}")
            
            # Publish order intent
            self.bus.publish_order_intent(order_intent)
            self.deduplication.mark_signal_processed(signal)
            self.deduplication.mark_order_processed(order_intent)

            self.signals_approved += 1
            self.orders_created += 1

            # Record approval metrics
            self.metrics.signal_processed(signal.symbol, "approved")

            logger.info(f"✅ Signal approved and order created: {order_intent.client_order_id}")

            # Publish approval event
            self.bus.publish_system_event(
                event_type="signal_approved",
                source="risk_manager",
                data={
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "quantity": float(order_intent.quantity),
                    "client_order_id": order_intent.client_order_id,
                    "signal_source": signal.source,
                    "confidence": float(signal.confidence)
                }
            )
            
        except Exception as e:
            self.validation_errors += 1
            logger.error(f"Error processing signal: {e}")
            
            # Record error in circuit breaker
            self.circuit_breakers["risk_validation"].record_error(str(e))
            
            # Publish error event
            self.bus.publish_system_event(
                event_type="signal_processing_error",
                source="risk_manager",
                data={
                    "error": str(e),
                    "symbol": signal.symbol,
                    "source": signal.source
                }
            )
            raise
    
    async def consume_signals(self):
        """Consume signals from message bus with Streams-optimized processing"""
        logger.info("Starting enhanced signal processing...")

        # Check if we're using Streams backend for optimized consumption
        if hasattr(self.bus.backend, 'consume_with_handler') and self.bus.get_stats().get('backend') == 'streams':
            logger.info("Using Redis Streams optimized consumption with safe ACK pattern")

            async def signal_handler(msg_data: dict) -> bool:
                """Handler for Streams-based signal processing"""
                try:
                    if not self.running:
                        return False

                    # Parse signal from message data
                    if msg_data.get("type") != "signal":
                        return True  # ACK non-signal messages

                    signal_data = json.loads(msg_data["data"])
                    signal = Signal.model_validate(signal_data)

                    # Process the signal
                    await self.process_signal(signal)

                    # Return True to ACK the message (only after successful processing)
                    return True

                except Exception as e:
                    logger.error(f"Error processing signal in handler: {e}")
                    # Return False to NOT ACK the message (it will remain pending for retry)
                    return False

            # Use the safe Streams consumption pattern
            await self.bus.backend.consume_with_handler("signals", signal_handler)

        else:
            # Fallback to Pub/Sub pattern for backward compatibility
            logger.info("Using Pub/Sub consumption pattern")
            async for signal in self.bus.subscribe_signals():
                if not self.running:
                    break

                try:
                    await self.process_signal(signal)
                except Exception as e:
                    logger.error(f"Error in signal consumption loop: {e}")
                    await asyncio.sleep(1)  # Brief pause on error
    
    def get_comprehensive_stats(self) -> Dict:
        """Get comprehensive risk manager statistics"""
        return {
            "performance": {
                "signals_processed": self.signals_processed,
                "signals_approved": self.signals_approved,
                "signals_rejected": self.signals_rejected,
                "orders_created": self.orders_created,
                "validation_errors": self.validation_errors,
                "approval_rate": self.signals_approved / max(1, self.signals_processed),
                "uptime_seconds": self.validation_timer.elapsed_seconds()
            },
            "rate_limits": {
                "orders": self.order_rate_limiter.get_stats(),
                "signals": self.signal_rate_limiter.get_stats()
            },
            "circuit_breakers": {
                name: breaker.get_stats() 
                for name, breaker in self.circuit_breakers.items()
            },
            "emergency_stop": {
                "active": self.emergency_stop,
                "reason": self.emergency_reason,
                "activated_at": self.emergency_activated_at
            },
            "deduplication": self.deduplication.get_comprehensive_stats(),
            "market": {
                "is_open": self.market_validator.is_market_open(),
                "current_time": TimeUtils.market_now().isoformat(),
                "next_open": TimeUtils.next_market_open().isoformat()
            },
            "last_updated": TimeUtils.utc_now().isoformat()
        }
    
    async def start(self):
        """Start enhanced risk manager"""
        logger.info("Starting Enhanced Risk Manager...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False
        
        # Mark service start in metrics
        self.metrics.mark_service_start()

        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="risk_manager",
            data={
                "enhanced_features": [
                    "timezone_aware_validation",
                    "monotonic_rate_limiting",
                    "persistent_deduplication",
                    "circuit_breakers",
                    "comprehensive_logging",
                    "prometheus_metrics"
                ],
                "rate_limits": {
                    "orders_per_minute": self.settings.max_orders_per_minute,
                    "signals_per_5min": self.settings.max_signals_per_5min
                },
                "metrics_port": os.getenv("RISK_METRICS_PORT", "8011")
            }
        )

        self.running = True
        
        try:
            # Start consuming signals
            await self.consume_signals()
            
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop enhanced risk manager"""
        logger.info("Stopping Enhanced Risk Manager...")
        self.running = False

        # Mark service stop in metrics
        self.metrics.mark_service_stop()

        # Get final stats
        final_stats = self.get_comprehensive_stats()

        logger.info("Final Statistics:")
        logger.info(f"  Signals processed: {self.signals_processed}")
        logger.info(f"  Signals approved: {self.signals_approved}")
        logger.info(f"  Approval rate: {final_stats['performance']['approval_rate']:.1%}")
        logger.info(f"  Orders created: {self.orders_created}")

        # Publish service stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="risk_manager",
                data={
                    "reason": "graceful_shutdown",
                    "final_stats": final_stats
                }
            )
            self.bus.disconnect()


async def main():
    """Main entry point"""
    try:
        manager = EnhancedRiskManager()
        await manager.start()
        
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
# --- Compat alias for tests ---
RiskManager = EnhancedRiskManager
__all__ = ['EnhancedRiskManager','RiskManager']
