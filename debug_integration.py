#!/usr/bin/env python3
"""
Debug Integration Test - Identify message bus communication issues
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.settings import get_settings
from lib.bus import connect_bus, get_bus
from lib.models import Bar, Signal, SignalSide

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_basic_pubsub():
    """Test basic publish/subscribe functionality"""
    logger.info("🔍 Testing basic pub/sub functionality...")
    
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    # Test data
    test_bar = Bar(
        symbol="TEST",
        timestamp=asyncio.get_event_loop().time(),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1000
    )
    
    messages_received = []
    
    async def subscriber():
        """Simple subscriber"""
        logger.info("📡 Subscriber starting...")
        async for bar in bus.subscribe_bars("TEST"):
            logger.info(f"📥 Received bar: {bar.symbol} @ ${bar.close}")
            messages_received.append(bar)
            break  # Only receive one message
    
    # Start subscriber
    sub_task = asyncio.create_task(subscriber())
    
    # Give subscriber time to start
    await asyncio.sleep(1)
    
    # Publish test message
    logger.info(f"📤 Publishing test bar: {test_bar.symbol} @ ${test_bar.close}")
    bus.publish_bar(test_bar)
    
    # Wait for subscriber to receive message
    try:
        await asyncio.wait_for(sub_task, timeout=5)
        logger.info(f"✅ Basic pub/sub test passed! Received {len(messages_received)} messages")
        return True
    except asyncio.TimeoutError:
        logger.error("❌ Basic pub/sub test failed - timeout")
        return False

async def test_pattern_subscription():
    """Test pattern-based subscription (bars.*)"""
    logger.info("🔍 Testing pattern subscription...")
    
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    messages_received = []
    symbols = ["AAPL", "MSFT", "GOOGL"]
    
    async def pattern_subscriber():
        """Pattern subscriber"""
        logger.info("📡 Pattern subscriber starting (bars.*)...")
        async for bar in bus.subscribe_bars("*"):  # Subscribe to all symbols
            logger.info(f"📥 Received bar via pattern: {bar.symbol} @ ${bar.close}")
            messages_received.append(bar)
            if len(messages_received) >= len(symbols):
                break
    
    # Start subscriber
    sub_task = asyncio.create_task(pattern_subscriber())
    
    # Give subscriber time to start
    await asyncio.sleep(1)
    
    # Publish test messages for different symbols
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
        
        logger.info(f"📤 Publishing bar for {symbol}: ${test_bar.close}")
        bus.publish_bar(test_bar)
        await asyncio.sleep(0.1)  # Small delay between publishes
    
    # Wait for subscriber to receive messages
    try:
        await asyncio.wait_for(sub_task, timeout=10)
        logger.info(f"✅ Pattern subscription test passed! Received {len(messages_received)} messages")
        return True
    except asyncio.TimeoutError:
        logger.error(f"❌ Pattern subscription test failed - received {len(messages_received)}/{len(symbols)} messages")
        return False

async def test_rapid_publishing():
    """Test rapid publishing like the data ingestor does"""
    logger.info("🔍 Testing rapid publishing scenario...")
    
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    messages_received = []
    total_to_publish = 100
    
    async def rapid_subscriber():
        """Subscriber for rapid messages"""
        logger.info("📡 Rapid subscriber starting...")
        async for bar in bus.subscribe_bars("*"):
            messages_received.append(bar)
            if len(messages_received) % 10 == 0:
                logger.info(f"📥 Received {len(messages_received)} bars so far...")
            
            if len(messages_received) >= total_to_publish:
                break
    
    # Start subscriber
    sub_task = asyncio.create_task(rapid_subscriber())
    
    # Give subscriber time to start
    await asyncio.sleep(1)
    
    # Rapid publishing
    logger.info(f"📤 Publishing {total_to_publish} bars rapidly...")
    for i in range(total_to_publish):
        test_bar = Bar(
            symbol="RAPID",
            timestamp=asyncio.get_event_loop().time() + i,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0 + (i * 0.01),
            volume=1000
        )
        
        bus.publish_bar(test_bar)
        
        # No delay - publish as fast as possible
    
    logger.info("📤 Finished publishing, waiting for subscriber...")
    
    # Wait for subscriber to receive messages
    try:
        await asyncio.wait_for(sub_task, timeout=15)
        logger.info(f"✅ Rapid publishing test passed! Received {len(messages_received)}/{total_to_publish} messages")
        return len(messages_received) == total_to_publish
    except asyncio.TimeoutError:
        logger.error(f"❌ Rapid publishing test failed - received {len(messages_received)}/{total_to_publish} messages")
        return False

async def test_real_strategy_integration():
    """Test with actual strategy code"""
    logger.info("🔍 Testing with real strategy integration...")
    
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    # Simple strategy simulation
    bars_processed = []
    signals_generated = []
    
    async def strategy_subscriber():
        """Simulate strategy processing"""
        logger.info("📡 Strategy subscriber starting...")
        bar_count = 0
        
        async for bar in bus.subscribe_bars("*"):
            bars_processed.append(bar)
            bar_count += 1
            
            logger.debug(f"📊 Processing bar #{bar_count}: {bar.symbol} @ ${bar.close}")
            
            # Simple signal generation (every 50 bars)
            if bar_count % 50 == 0:
                signal = Signal(
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    side=SignalSide.BUY,
                    confidence=0.7,
                    price=bar.close,
                    source="debug_strategy"
                )
                
                signals_generated.append(signal)
                bus.publish_signal(signal)
                logger.info(f"🎯 Generated signal #{len(signals_generated)}: {signal.side} {signal.symbol}")
            
            if bar_count >= 200:  # Process 200 bars
                break
    
    # Start strategy subscriber
    strategy_task = asyncio.create_task(strategy_subscriber())
    
    # Give subscriber time to start
    await asyncio.sleep(1)
    
    # Publish test data
    logger.info("📤 Publishing test data for strategy...")
    symbols = ["AAPL", "MSFT", "GOOGL"]
    
    for i in range(200):  # Publish 200 bars total
        symbol = symbols[i % len(symbols)]
        
        test_bar = Bar(
            symbol=symbol,
            timestamp=asyncio.get_event_loop().time() + i,
            open=100.0,
            high=102.0,
            low=99.0,
            close=100.0 + (i * 0.1),  # Gradually increasing price
            volume=1000 + i
        )
        
        bus.publish_bar(test_bar)
        
        if i % 50 == 0:
            logger.info(f"📤 Published {i+1} bars...")
    
    logger.info("📤 Finished publishing, waiting for strategy...")
    
    # Wait for strategy to process
    try:
        await asyncio.wait_for(strategy_task, timeout=20)
        logger.info(f"✅ Strategy integration test completed!")
        logger.info(f"   Bars processed: {len(bars_processed)}")
        logger.info(f"   Signals generated: {len(signals_generated)}")
        return len(bars_processed) > 0
        
    except asyncio.TimeoutError:
        logger.error(f"❌ Strategy integration test failed - timeout")
        logger.info(f"   Bars processed: {len(bars_processed)}")
        logger.info(f"   Signals generated: {len(signals_generated)}")
        return False

async def main():
    """Run all debug tests"""
    logger.info("🐛 Running Message Bus Debug Tests")
    logger.info("=" * 60)
    
    tests = [
        ("Basic Pub/Sub", test_basic_pubsub),
        ("Pattern Subscription", test_pattern_subscription),
        ("Rapid Publishing", test_rapid_publishing),
        ("Strategy Integration", test_real_strategy_integration)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        logger.info(f"\n{'='*60}")
        logger.info(f"TEST: {test_name}")
        logger.info('='*60)
        
        try:
            result = await test_func()
            results.append((test_name, result))
            status = "✅ PASS" if result else "❌ FAIL"
            logger.info(f"Result: {status}")
            
        except Exception as e:
            logger.error(f"❌ Test '{test_name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
        
        # Small delay between tests
        await asyncio.sleep(1)
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("DEBUG TEST SUMMARY")
    logger.info('='*60)
    
    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{test_name}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\nOverall: {passed}/{len(results)} tests passed")
    
    if passed == len(results):
        logger.info("🎉 All debug tests passed! Message bus is working correctly.")
    else:
        logger.info("🔧 Some tests failed. Need to investigate message bus implementation.")

if __name__ == "__main__":
    asyncio.run(main())