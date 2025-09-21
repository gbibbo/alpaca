#!/usr/bin/env python3
"""
Risk Manager - Validates and filters trading signals
Applies position sizing, risk limits, and portfolio constraints
"""

import os
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
import uuid

# Add lib to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from lib.models import Signal, OrderIntent, PortfolioState, Position, SignalSide, OrderType, RiskConfig
from lib.bus import get_bus, connect_bus
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RiskManager:
    """Risk management service for trading signals"""
    
    def __init__(self):
        self.bus = get_bus()
        self.running = False
        
        # Risk configuration
        self.config = RiskConfig(
            max_daily_loss=float(os.getenv('MAX_DAILY_LOSS', '0.05')),  # 5%
            max_portfolio_risk=float(os.getenv('MAX_PORTFOLIO_RISK', '0.2')),  # 20%
            max_position_size=float(os.getenv('MAX_POSITION_SIZE', '0.1')),  # 10%
            stop_loss_pct=float(os.getenv('STOP_LOSS_PCT', '0.02')),  # 2%
            take_profit_pct=float(os.getenv('TAKE_PROFIT_PCT', '0.06'))  # 6%
        )
        
        # Track portfolio state
        self.portfolio = PortfolioState(
            total_value=100000.0,  # Starting value
            cash=100000.0,
            buying_power=100000.0,
            positions=[],
            last_updated=datetime.utcnow()
        )
        
        # Track daily P&L
        self.daily_pnl = 0.0
        self.daily_reset_time = datetime.now().date()
        
        # Track recent signals to avoid spam
        self.recent_signals: Dict[str, datetime] = {}
        self.signal_cooldown = timedelta(minutes=5)  # 5-minute cooldown per symbol
        
        logger.info(f"Initialized Risk Manager with config: {self.config}")
    
    def reset_daily_metrics(self):
        """Reset daily tracking metrics"""
        current_date = datetime.now().date()
        
        if current_date > self.daily_reset_time:
            logger.info(f"Resetting daily metrics for {current_date}")
            self.daily_pnl = 0.0
            self.daily_reset_time = current_date
    
    def check_signal_cooldown(self, signal: Signal) -> bool:
        """Check if signal is too recent for same symbol"""
        key = f"{signal.symbol}_{signal.source}"
        last_signal_time = self.recent_signals.get(key)
        
        if last_signal_time:
            time_since_last = datetime.utcnow() - last_signal_time
            if time_since_last < self.signal_cooldown:
                logger.debug(f"Signal for {signal.symbol} in cooldown period")
                return False
        
        self.recent_signals[key] = datetime.utcnow()
        return True
    
    def check_daily_loss_limit(self) -> bool:
        """Check if daily loss limit would be exceeded"""
        daily_loss_limit = self.portfolio.total_value * self.config.max_daily_loss
        
        if self.daily_pnl < -daily_loss_limit:
            logger.warning(f"Daily loss limit reached: {self.daily_pnl:.2f}")
            return False
        
        return True
    
    def calculate_position_size(self, signal: Signal) -> float:
        """Calculate appropriate position size based on risk management"""
        # Base position size on risk per trade and portfolio value
        max_risk_amount = self.portfolio.total_value * self.config.max_position_size
        
        if signal.price and signal.price > 0:
            # Calculate shares based on dollar amount
            base_quantity = max_risk_amount / signal.price
            
            # Adjust based on signal confidence
            confidence_multiplier = min(1.0, signal.confidence * 2)  # Scale confidence
            adjusted_quantity = base_quantity * confidence_multiplier
            
            # Ensure we don't exceed available cash for buy orders
            if signal.side == SignalSide.BUY:
                max_affordable = self.portfolio.cash / signal.price
                adjusted_quantity = min(adjusted_quantity, max_affordable)
            
            return max(0, adjusted_quantity)
        
        return 0
    
    def get_current_position(self, symbol: str) -> Optional[Position]:
        """Get current position for symbol"""
        for position in self.portfolio.positions:
            if position.symbol == symbol:
                return position
        return None
    
    def validate_signal(self, signal: Signal) -> tuple[bool, str]:
        """Validate signal against risk rules"""
        
        # Check basic signal requirements
        if signal.confidence < 0.3:
            return False, f"Signal confidence too low: {signal.confidence}"
        
        # Check cooldown period
        if not self.check_signal_cooldown(signal):
            return False, "Signal in cooldown period"
        
        # Check daily loss limits
        if not self.check_daily_loss_limit():
            return False, "Daily loss limit exceeded"
        
        # Check portfolio-specific rules
        current_position = self.get_current_position(signal.symbol)
        
        if signal.side == SignalSide.BUY:
            # Check if we already have a large position
            if current_position:
                position_value = current_position.quantity * signal.price if signal.price else 0
                position_pct = position_value / self.portfolio.total_value
                
                if position_pct > self.config.max_position_size:
                    return False, f"Position size already too large: {position_pct:.2%}"
            
            # Check available cash
            required_cash = self.calculate_position_size(signal) * (signal.price or 0)
            if required_cash > self.portfolio.cash:
                return False, f"Insufficient cash: need ${required_cash:.2f}, have ${self.portfolio.cash:.2f}"
        
        elif signal.side == SignalSide.SELL:
            # Check if we have position to sell
            if not current_position or current_position.quantity <= 0:
                return False, "No position to sell"
        
        return True, "Signal validated"
    
    def create_order_intent(self, signal: Signal) -> OrderIntent:
        """Create order intent from validated signal"""
        
        # Calculate position size
        quantity = self.calculate_position_size(signal)
        
        # For sell signals, use current position quantity
        if signal.side == SignalSide.SELL:
            current_position = self.get_current_position(signal.symbol)
            if current_position:
                quantity = min(quantity, current_position.quantity)
        
        # Calculate stop loss and take profit levels
        stop_loss = None
        take_profit = None
        
        if signal.price and signal.side == SignalSide.BUY:
            stop_loss = signal.price * (1 - self.config.stop_loss_pct)
            take_profit = signal.price * (1 + self.config.take_profit_pct)
        elif signal.price and signal.side == SignalSide.SELL:
            stop_loss = signal.price * (1 + self.config.stop_loss_pct)
            take_profit = signal.price * (1 - self.config.take_profit_pct)
        
        # Generate unique client order ID
        client_order_id = f"{signal.source}_{signal.symbol}_{uuid.uuid4().hex[:8]}"
        
        return OrderIntent(
            symbol=signal.symbol,
            timestamp=datetime.utcnow(),
            side=signal.side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            price=signal.price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
            signal_source=signal.source,
            risk_adjusted=True
        )
    
    async def process_signal(self, signal: Signal):
        """Process incoming signal and apply risk management"""
        try:
            # Validate signal
            is_valid, reason = self.validate_signal(signal)
            
            if not is_valid:
                logger.info(f"Signal rejected for {signal.symbol}: {reason}")
                
                # Publish rejection event
                self.bus.publish_system_event(
                    event_type="signal_rejected",
                    source="risk_manager",
                    data={
                        "symbol": signal.symbol,
                        "side": signal.side,
                        "reason": reason,
                        "confidence": signal.confidence,
                        "source": signal.source
                    }
                )
                return
            
            # Create order intent
            order_intent = self.create_order_intent(signal)
            
            # Publish order intent
            self.bus.publish_order_intent(order_intent)
            
            logger.info(
                f"Created order intent: {order_intent.side} {order_intent.quantity:.2f} "
                f"{order_intent.symbol} @ ${order_intent.price:.2f}"
            )
            
            # Publish approval event
            self.bus.publish_system_event(
                event_type="signal_approved",
                source="risk_manager",
                data={
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "quantity": order_intent.quantity,
                    "price": order_intent.price,
                    "client_order_id": order_intent.client_order_id
                }
            )
            
        except Exception as e:
            logger.error(f"Error processing signal for {signal.symbol}: {e}")
    
    async def consume_signals(self):
        """Consume signals from message bus"""
        logger.info("Starting to consume signals...")
        
        async for signal in self.bus.subscribe_signals():
            if not self.running:
                break
            
            try:
                # Reset daily metrics if needed
                self.reset_daily_metrics()
                
                # Process signal
                await self.process_signal(signal)
                
            except Exception as e:
                logger.error(f"Error consuming signal: {e}")
    
    async def start(self):
        """Start the risk manager"""
        logger.info("Starting Risk Manager...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to Redis")
            return False
        
        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="risk_manager",
            data={
                "config": self.config.model_dump(),
                "portfolio_value": self.portfolio.total_value
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
        """Stop the risk manager"""
        logger.info("Stopping risk manager...")
        self.running = False
        
        # Publish service stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="risk_manager",
                data={"reason": "graceful_shutdown"}
            )
            self.bus.disconnect()

async def main():
    """Main entry point"""
    risk_manager = RiskManager()
    
    try:
        await risk_manager.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")

if __name__ == "__main__":
    asyncio.run(main())