#!/usr/bin/env python3
"""
Test Fixed Bus - Verify pattern subscription fix
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.settings import get_settings
from lib.bus import connect_bus, get_bus
from lib.models import Bar

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_fixed_pattern_subscription():
    """Test the fixed pattern subscription"""
    logger.info("🔧 Testing fixed pattern subscription...")
    
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    messages_received = []
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]
    
    async def pattern_subscriber():
        """Pattern subscriber using fixed implementation"""
        logger.info("📡 Starting pattern subscriber...")
        async for bar in bus.subscribe_bars("*"):  # This should now work
            logger.info(f"📥 Received: {bar.symbol} @ ${bar.close}")
            messages_received.append(bar)
            if len(messages_received) >= len(symbols):
                break
    
    # Start subscriber
    sub_task = asyncio.create_task(pattern_subscriber())
    
    # Give subscriber time to start and subscribe to all channels
    await asyncio.sleep(2)
    
    # Publish messages for each symbol
    logger.info(f"📤 Publishing bars for {len(symbols)} symbols...")
    for i, symbol in enumerate(symbols):
        test_bar = Bar(
            symbol=symbol,
            timestamp=asyncio.get_event_loop().time() + i,
            open=100.0 + i,
            high=102.0 + i,
            low=99.0 + i,
            close=101.0 + i,
            volume=1000 + i
        )
        
        logger.info(f"📤 Publishing {symbol}: ${test_bar.close}")
        bus.publish_bar(test_bar)
        await asyncio.sleep(0.2)  # Small delay
    
    # Wait for subscriber
    try:
        await asyncio.wait_for(sub_task, timeout=15)
        logger.info(f"✅ Fixed pattern test passed! Received {len(messages_received)}/{len(symbols)} messages")
        return len(messages_received) == len(symbols)
    except asyncio.TimeoutError:
        logger.error(f"❌ Fixed pattern test failed - received {len(messages_received)}/{len(symbols)} messages")
        return False

async def test_integration_with_fix():
    """Test integration with the fix"""
    logger.info("🔧 Testing integration with fixed bus...")
    
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    # Simulate strategy processing
    bars_processed = []
    
    async def strategy_processor():
        """Process bars like a real strategy"""
        logger.info("📊 Strategy processor starting...")
        
        async for bar in bus.subscribe_bars("*"):
            bars_processed.append(bar)
            logger.info(f"📊 Processed bar #{len(bars_processed)}: {bar.symbol} @ ${bar.close}")
            
            if len(bars_processed) >= 10:  # Process 10 bars
                break
    
    # Start processor
    proc_task = asyncio.create_task(strategy_processor())
    
    # Give processor time to subscribe
    await asyncio.sleep(2)
    
    # Simulate data ingestor publishing
    logger.info("📈 Simulating data ingestor...")
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]
    
    for i in range(10):
        symbol = symbols[i % len(symbols)]
        
        test_bar = Bar(
            symbol=symbol,
            timestamp=asyncio.get_event_loop().time() + i,
            open=100.0,
            high=102.0,
            low=99.0,
            close=100.0 + (i * 0.5),  # Changing price
            volume=1000 + i
        )
        
        logger.info(f"📈 Data ingestor publishing: {symbol} @ ${test_bar.close}")
        bus.publish_bar(test_bar)
        await asyncio.sleep(0.1)
    
    # Wait for processing
    try:
        await asyncio.wait_for(proc_task, timeout=15)
        logger.info(f"✅ Integration test passed! Processed {len(bars_processed)} bars")
        return len(bars_processed) == 10
    except asyncio.TimeoutError:
        logger.error(f"❌ Integration test failed - processed {len(bars_processed)} bars")
        return False

async def main():
    """Test the fix"""
    logger.info("🔧 Testing Fixed Message Bus")
    logger.info("=" * 50)
    
    # Test 1: Fixed Pattern Subscription
    logger.info("\nTEST 1: Fixed Pattern Subscription")
    logger.info("-" * 40)
    result1 = await test_fixed_pattern_subscription()
    
    # Test 2: Integration Test
    logger.info("\nTEST 2: Integration with Fixed Bus")
    logger.info("-" * 40)
    result2 = await test_integration_with_fix()
    
    # Summary
    logger.info("\n" + "=" * 50)
    logger.info("SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Pattern Subscription: {'✅ FIXED' if result1 else '❌ STILL BROKEN'}")
    logger.info(f"Integration Test: {'✅ WORKING' if result2 else '❌ STILL BROKEN'}")
    
    if result1 and result2:
        logger.info("🎉 Bus fix successful! Ready to test full integration.")
    else:
        logger.info("🔧 Bus still needs work.")

if __name__ == "__main__":
    asyncio.run(main())