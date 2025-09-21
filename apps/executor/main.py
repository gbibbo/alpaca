#!/usr/bin/env python3
"""
Executor - Executes orders with Alpaca broker (Fixed Version)
Consumes order intents, submits to Alpaca, publishes fill results
"""

import os
import asyncio
import logging
import sys
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import OrderIntent, OrderFill, SignalSide, OrderStatus, OrderType
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide as AlpacaOrderSide, TimeInForce

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class AlpacaExecutor:
    """Executes orders using Alpaca broker API with unified configuration"""
    
    def __init__(self):
        self.settings = get_settings()
        self.bus = get_bus()
        self.running = False
        
        # Initialize Alpaca trading client
        if not self.settings.has_alpaca_credentials:
            raise ValueError("Missing Alpaca API credentials in configuration")
        
        self.trading_client = TradingClient(
            api_key=self.settings.apca_api_key_id,
            secret_key=self.settings.apca_api_secret_key,
            paper=self.settings.is_paper_trading
        )
        
        # Track pending orders
        self.pending_orders: Dict[str, OrderIntent] = {}
        
        # Execution statistics
        self.orders_executed = 0
        self.orders_failed = 0
        self.total_volume = 0.0
        
        logger.info(f"Initialized Alpaca Executor")
        logger.info(f"  Paper Trading: {self.settings.is_paper_trading}")
        logger.info(f"  Base URL: {self.settings.apca_api_base_url}")
    
    def convert_side(self, side: SignalSide) -> AlpacaOrderSide:
        """Convert our SignalSide to Alpaca OrderSide"""
        if side == SignalSide.BUY:
            return AlpacaOrderSide.BUY
        elif side == SignalSide.SELL:
            return AlpacaOrderSide.SELL
        else:
            raise ValueError(f"Invalid order side: {side}")
    
    async def verify_account_status(self) -> bool:
        """Verify Alpaca account is ready for trading"""
        try:
            account = self.trading_client.get_account()
            
            if account.status.value != "ACTIVE":
                logger.error(f"Account not active: {account.status}")
                return False
            
            if account.trading_blocked:
                logger.error("Trading is blocked on account")
                return False
            
            logger.info(f"Account verified:")
            logger.info(f"  Status: {account.status}")
            logger.info(f"  Buying Power: ${float(account.buying_power):,.2f}")
            logger.info(f"  Cash: ${float(account.cash):,.2f}")
            logger.info(f"  Portfolio Value: ${float(account.portfolio_value):,.2f}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to verify account: {e}")
            return False
    
    def check_position_exists(self, symbol: str) -> tuple[bool, float]:
        """Check if position exists and return quantity"""
        try:
            position = self.trading_client.get_open_position(symbol)
            quantity = float(position.qty)
            logger.debug(f"Current position in {symbol}: {quantity} shares")
            return True, quantity
        except:
            logger.debug(f"No position found for {symbol}")
            return False, 0.0
    
    async def execute_order(self, order_intent: OrderIntent) -> Optional[OrderFill]:
        """Execute order with Alpaca"""
        try:
            logger.info(
                f"Executing order: {order_intent.side} {order_intent.quantity:.0f} "
                f"{order_intent.symbol} @ ${order_intent.price:.2f}"
            )
            
            # Convert side
            alpaca_side = self.convert_side(order_intent.side)
            
            # For sell orders, verify we have the position
            if order_intent.side == SignalSide.SELL:
                has_position, current_qty = self.check_position_exists(order_intent.symbol)
                
                if not has_position or current_qty < order_intent.quantity:
                    logger.warning(
                        f"Insufficient position to sell {order_intent.symbol}: "
                        f"need {order_intent.quantity}, have {current_qty}"
                    )
                    
                    # Publish error event
                    self.bus.publish_system_event(
                        event_type="order_error",
                        source="executor",
                        data={
                            "symbol": order_intent.symbol,
                            "side": order_intent.side,
                            "quantity": order_intent.quantity,
                            "error": "insufficient_position",
                            "current_position": current_qty,
                            "client_order_id": order_intent.client_order_id
                        }
                    )
                    return None
            
            # Create order request based on type
            if order_intent.order_type == OrderType.MARKET:
                order_request = MarketOrderRequest(
                    symbol=order_intent.symbol,
                    qty=order_intent.quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=order_intent.client_order_id
                )
            
            elif order_intent.order_type == OrderType.LIMIT:
                if not order_intent.price:
                    logger.error("Limit order requires price")
                    return None
                
                order_request = LimitOrderRequest(
                    symbol=order_intent.symbol,
                    qty=order_intent.quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=order_intent.price,
                    client_order_id=order_intent.client_order_id
                )
            
            else:
                logger.error(f"Unsupported order type: {order_intent.order_type}")
                return None
            
            # Submit order to Alpaca
            logger.info(f"Submitting order to Alpaca...")
            order_response = self.trading_client.submit_order(order_request)
            
            # Track pending order
            self.pending_orders[order_response.id] = order_intent
            
            logger.info(
                f"Order submitted successfully:"
                f"  Alpaca Order ID: {order_response.id}"
                f"  Status: {order_response.status}"
                f"  Symbol: {order_response.symbol}"
                f"  Quantity: {order_response.qty}"
                f"  Side: {order_response.side}"
            )
            
            # Update statistics
            self.orders_executed += 1
            self.total_volume += float(order_intent.quantity or 0) * float(order_intent.price or 0)
            
            # Create fill response for market orders (immediate execution in paper trading)
            if order_intent.order_type == OrderType.MARKET:
                fill_price = order_intent.price or 0.0
                
                # Alpaca is commission-free for stocks
                commission = 0.0
                
                order_fill = OrderFill(
                    symbol=order_intent.symbol,
                    timestamp=datetime.utcnow(),
                    side=order_intent.side,
                    quantity=order_intent.quantity,
                    fill_price=fill_price,
                    fill_quantity=order_intent.quantity,
                    broker_order_id=order_response.id,
                    client_order_id=order_intent.client_order_id,
                    status=OrderStatus.FILLED,
                    commission=commission,
                    total_value=fill_price * order_intent.quantity
                )
                
                logger.info(f"Order filled: {order_fill.symbol} "
                           f"{order_fill.fill_quantity}@${order_fill.fill_price:.2f} "
                           f"(Total: ${order_fill.total_value:,.2f})")
                
                return order_fill
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to execute order for {order_intent.symbol}: {e}")
            self.orders_failed += 1
            
            # Publish error event
            self.bus.publish_system_event(
                event_type="order_error",
                source="executor",
                data={
                    "symbol": order_intent.symbol,
                    "side": order_intent.side,
                    "quantity": order_intent.quantity,
                    "error": str(e),
                    "client_order_id": order_intent.client_order_id
                }
            )
            
            return None
    
    async def monitor_orders(self):
        """Monitor order status updates (simplified implementation)"""
        logger.info("Starting order monitoring...")
        
        while self.running:
            try:
                # Check pending orders periodically
                if self.pending_orders:
                    logger.debug(f"Monitoring {len(self.pending_orders)} pending orders")
                    
                    orders = self.trading_client.get_orders()
                    
                    for order in orders:
                        if order.id in self.pending_orders:
                            original_intent = self.pending_orders[order.id]
                            
                            logger.debug(f"Order {order.id} status: {order.status}")
                            
                            if order.status.value in ['filled', 'partially_filled']:
                                # Create fill notification
                                fill_qty = float(order.filled_qty) if order.filled_qty else 0
                                fill_price = float(order.filled_avg_price) if order.filled_avg_price else 0
                                
                                if fill_qty > 0:
                                    order_fill = OrderFill(
                                        symbol=order.symbol,
                                        timestamp=datetime.utcnow(),
                                        side=original_intent.side,
                                        quantity=fill_qty,
                                        fill_price=fill_price,
                                        fill_quantity=fill_qty,
                                        broker_order_id=order.id,
                                        client_order_id=original_intent.client_order_id,
                                        status=OrderStatus.FILLED,
                                        commission=0.0,
                                        total_value=fill_price * fill_qty
                                    )
                                    
                                    # Publish fill
                                    self.bus.publish_order_fill(order_fill)
                                    
                                    logger.info(
                                        f"Order status update: {order.symbol} "
                                        f"{fill_qty}@${fill_price:.2f} - {order.status}"
                                    )
                            
                            # Remove completed orders
                            if order.status.value in ['filled', 'canceled', 'rejected']:
                                logger.debug(f"Removing completed order {order.id}")
                                del self.pending_orders[order.id]
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error monitoring orders: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def consume_order_intents(self):
        """Consume order intents from message bus"""
        logger.info("Starting to consume order intents...")
        
        async for order_intent in self.bus.subscribe_order_intents():
            if not self.running:
                break
            
            try:
                logger.info(f"Received order intent: {order_intent.side} {order_intent.quantity:.0f} "
                           f"{order_intent.symbol} @ ${order_intent.price:.2f}")
                
                # Execute order
                order_fill = await self.execute_order(order_intent)
                
                if order_fill:
                    # Publish fill result
                    self.bus.publish_order_fill(order_fill)
                    
                    logger.info(
                        f"Order executed and published: {order_fill.symbol} "
                        f"{order_fill.quantity}@${order_fill.fill_price:.2f}"
                    )
                    
                    # Publish execution event
                    self.bus.publish_system_event(
                        event_type="order_executed",
                        source="executor",
                        data={
                            "symbol": order_fill.symbol,
                            "side": order_fill.side,
                            "quantity": order_fill.quantity,
                            "fill_price": order_fill.fill_price,
                            "total_value": order_fill.total_value,
                            "broker_order_id": order_fill.broker_order_id
                        }
                    )
                
            except Exception as e:
                logger.error(f"Error processing order intent: {e}")
    
    async def start(self):
        """Start the executor"""
        logger.info("Starting Alpaca Executor...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False
        
        # Verify account status
        if not await self.verify_account_status():
            logger.error("Account verification failed")
            return False
        
        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="executor",
            data={
                "broker": "alpaca",
                "paper_trading": self.settings.is_paper_trading,
                "account_verified": True,
                "symbols": self.settings.symbols_list
            }
        )
        
        self.running = True
        
        try:
            # Start order monitoring task
            monitor_task = asyncio.create_task(self.monitor_orders())
            
            # Start consuming order intents
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
        """Stop the executor"""
        logger.info("Stopping executor...")
        self.running = False
        
        # Show execution statistics
        logger.info("Execution Statistics:")
        logger.info(f"  Orders Executed: {self.orders_executed}")
        logger.info(f"  Orders Failed: {self.orders_failed}")
        logger.info(f"  Total Volume: ${self.total_volume:,.2f}")
        
        # Cancel any pending orders if needed
        if self.pending_orders:
            logger.info(f"Canceling {len(self.pending_orders)} pending orders...")
            try:
                self.trading_client.cancel_orders()
            except Exception as e:
                logger.error(f"Error canceling orders: {e}")
        
        # Publish service stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="executor",
                data={
                    "reason": "graceful_shutdown",
                    "orders_executed": self.orders_executed,
                    "orders_failed": self.orders_failed,
                    "total_volume": self.total_volume
                }
            )
            self.bus.disconnect()

async def main():
    """Main entry point"""
    try:
        executor = AlpacaExecutor()
        await executor.start()
        
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())