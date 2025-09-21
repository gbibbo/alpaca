#!/usr/bin/env python3
"""
Forced Signal Test - Guaranteed signal generation for pipeline validation
"""

import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.settings import get_settings
from lib.bus import connect_bus, get_bus
from lib.models import Signal, SignalSide

# Import services (but we'll manually create signals)
sys.path.insert(0, str(Path(__file__).parent / "apps" / "risk_manager"))
sys.path.insert(0, str(Path(__file__).parent / "apps" / "executor"))

from apps.risk_manager.main import RiskManager
from apps.executor.main import AlpacaExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_forced_signal_pipeline():
    """Test pipeline with manually created signals to validate execution"""
    logger.info("🎯 FORCED SIGNAL PIPELINE TEST")
    logger.info("Manual Signals → Risk Manager → Executor")
    logger.info("=" * 60)
    
    # Connect to shared message bus
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    # Initialize services (skip data ingestor and strategies)
    risk_manager = RiskManager()
    executor = AlpacaExecutor()
    
    # Track results
    order_intents_created = []
    order_fills_executed = []
    
    async def order_intent_monitor():
        """Monitor order intents created by risk manager"""
        async for order_intent in bus.subscribe_order_intents():
            order_intents_created.append(order_intent)
            logger.info(f"💼 ORDER INTENT: {order_intent.side} {order_intent.quantity:.0f} "
                       f"{order_intent.symbol} @ ${order_intent.price:.2f}")
    
    async def order_fill_monitor():
        """Monitor order fills from executor"""
        async for order_fill in bus.subscribe_order_fills():
            order_fills_executed.append(order_fill)
            logger.info(f"✅ ORDER FILLED: {order_fill.side} {order_fill.fill_quantity:.0f} "
                       f"{order_fill.symbol} @ ${order_fill.fill_price:.2f} "
                       f"(Total: ${order_fill.total_value:,.2f})")
    
    try:
        # Start monitoring tasks
        order_intent_task = asyncio.create_task(order_intent_monitor())
        order_fill_task = asyncio.create_task(order_fill_monitor())
        
        # Start services
        logger.info("💼 Starting risk manager...")
        risk_manager.running = True
        risk_task = asyncio.create_task(risk_manager.consume_signals())
        
        logger.info("⚡ Starting executor...")
        if not await executor.verify_account_status():
            logger.error("Executor account verification failed")
            return False
        
        executor.running = True
        executor_task = asyncio.create_task(executor.consume_order_intents())
        
        # Give services time to start
        await asyncio.sleep(2)
        
        # Create and publish manual signals
        logger.info("📊 Creating manual signals for testing...")
        
        test_signals = [
            Signal(
                symbol="AAPL",
                timestamp=datetime.utcnow(),
                side=SignalSide.BUY,
                confidence=0.8,
                price=238.50,
                source="manual_test",
                metadata={"test": True}
            ),
            Signal(
                symbol="MSFT",
                timestamp=datetime.utcnow(),
                side=SignalSide.BUY,
                confidence=0.75,
                price=412.30,
                source="manual_test",
                metadata={"test": True}
            ),
            Signal(
                symbol="GOOGL",
                timestamp=datetime.utcnow(),
                side=SignalSide.BUY,
                confidence=0.7,
                price=161.25,
                source="manual_test",
                metadata={"test": True}
            )
        ]
        
        # Publish signals one by one
        for i, signal in enumerate(test_signals):
            logger.info(f"📊 Publishing signal {i+1}: {signal.side} {signal.symbol} @ ${signal.price}")
            bus.publish_signal(signal)
            await asyncio.sleep(2)  # Small delay between signals
        
        # Wait for processing
        logger.info("⏳ Waiting for signal processing and order execution...")
        await asyncio.sleep(15)  # Give time for processing and execution
        
        # Cancel tasks
        for task in [order_intent_task, order_fill_task, risk_task, executor_task]:
            task.cancel()
        
        # Results
        logger.info("\n" + "=" * 60)
        logger.info("FORCED SIGNAL TEST RESULTS")
        logger.info("=" * 60)
        
        logger.info(f"📊 Manual Signals Created: {len(test_signals)}")
        logger.info(f"💼 Order Intents Created: {len(order_intents_created)}")
        logger.info(f"✅ Orders Executed: {len(order_fills_executed)}")
        
        if order_intents_created:
            logger.info("\n💼 ORDER INTENTS CREATED:")
            for i, order in enumerate(order_intents_created):
                logger.info(f"  {i+1}. {order.side} {order.quantity:.0f} {order.symbol} "
                           f"@ ${order.price:.2f}")
        
        if order_fills_executed:
            logger.info("\n✅ ORDERS EXECUTED:")
            total_value = 0
            for i, fill in enumerate(order_fills_executed):
                logger.info(f"  {i+1}. {fill.side} {fill.fill_quantity:.0f} {fill.symbol} "
                           f"@ ${fill.fill_price:.2f} = ${fill.total_value:,.2f}")
                total_value += fill.total_value
            
            logger.info(f"\n💰 TOTAL TRADING VOLUME: ${total_value:,.2f}")
        
        # Success criteria
        pipeline_success = len(order_intents_created) > 0 and len(order_fills_executed) > 0
        
        logger.info("\n" + "=" * 60)
        
        if pipeline_success:
            logger.info("🎉 FORCED SIGNAL TEST PASSED!")
            logger.info("✅ Risk Manager → Executor pipeline working correctly")
            logger.info("✅ Real orders executed in Alpaca paper trading")
            logger.info("🚀 CORE TRADING EXECUTION IS FUNCTIONAL!")
        else:
            logger.info("❌ Pipeline execution failed")
            if len(order_intents_created) == 0:
                logger.info("   Risk manager not creating order intents")
            elif len(order_fills_executed) == 0:
                logger.info("   Executor not executing orders")
        
        return pipeline_success
        
    except Exception as e:
        logger.error(f"❌ Forced signal test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup
        risk_manager.running = False
        executor.running = False

async def main():
    """Run the forced signal test"""
    settings = get_settings()
    
    logger.info("🧪 FORCED SIGNAL PIPELINE TEST")
    logger.info("Testing Risk Manager → Executor with guaranteed signals")
    logger.info("=" * 60)
    logger.info(f"Paper Trading: {settings.is_paper_trading}")
    logger.info(f"Max Position Size: {settings.max_position_size:.1%}")
    logger.info("")
    
    success = await test_forced_signal_pipeline()
    
    logger.info("\n" + "=" * 60)
    if success:
        logger.info("🏆 EXECUTION PIPELINE VALIDATED!")
        logger.info("The core trading execution is working correctly.")
        logger.info("Issue: Strategies need tuning to generate more signals.")
    else:
        logger.info("🔧 Execution pipeline needs debugging.")

if __name__ == "__main__":
    asyncio.run(main())