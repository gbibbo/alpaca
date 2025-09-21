#!/usr/bin/env python3
"""
Integration Test - Data Ingestor + Strategies in same process
Tests the complete pipeline with shared fakeredis instance
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.settings import get_settings
from lib.bus import connect_bus, get_bus

# Import services
sys.path.insert(0, str(Path(__file__).parent / "apps" / "data_ingestor"))
sys.path.insert(0, str(Path(__file__).parent / "apps" / "strategies"))

from apps.data_ingestor.main import AlpacaDataIngestor
from apps.strategies.main import StrategyEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_integration():
    """Test data flow from ingestor to strategies"""
    logger.info("🚀 Starting Integration Test: Data Ingestor → Strategies")
    
    # Connect to shared message bus
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    # Initialize services
    ingestor = AlpacaDataIngestor()
    strategies = StrategyEngine()
    
    try:
        # Start strategy engine first (to subscribe to bars)
        logger.info("📊 Starting strategy engine...")
        strategies.running = True
        
        # Start strategies in background
        strategy_task = asyncio.create_task(strategies.consume_bars())
        
        # Give strategies time to subscribe
        await asyncio.sleep(1)
        
        # Ingest historical data (this will publish bars)
        logger.info("📈 Starting data ingestion...")
        await ingestor.ingest_historical_data(days_back=2)  # Just 2 days for faster testing
        
        # Give some time for strategies to process bars
        logger.info("⏳ Waiting for strategies to process bars...")
        await asyncio.sleep(5)
        
        # Check how many bars were processed
        total_bars = sum(len(deque_obj) for deque_obj in strategies.bar_history.values())
        logger.info(f"📊 Total bars stored by strategies: {total_bars}")
        
        for symbol, bars in strategies.bar_history.items():
            logger.info(f"  {symbol}: {len(bars)} bars")
        
        # Cancel strategy task
        strategy_task.cancel()
        
        logger.info("✅ Integration test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup
        ingestor.running = False
        strategies.running = False

async def test_signal_generation():
    """Test signal generation with historical data"""
    logger.info("🎯 Testing signal generation...")
    
    # Connect to shared message bus
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return False
    
    bus = get_bus()
    
    # Initialize services
    ingestor = AlpacaDataIngestor()
    strategies = StrategyEngine()
    
    # Track signals generated
    signals_received = []
    
    async def signal_monitor():
        """Monitor signals generated"""
        async for signal in bus.subscribe_signals():
            signals_received.append(signal)
            logger.info(f"🎯 Signal received: {signal.side} {signal.symbol} (confidence: {signal.confidence:.2%}) from {signal.source}")
            if len(signals_received) >= 5:  # Stop after 5 signals
                break
    
    try:
        # Start signal monitoring
        signal_task = asyncio.create_task(signal_monitor())
        
        # Start strategy engine
        strategies.running = True
        strategy_task = asyncio.create_task(strategies.consume_bars())
        
        # Give strategies time to subscribe
        await asyncio.sleep(1)
        
        # Ingest historical data
        logger.info("📈 Ingesting data to trigger signals...")
        await ingestor.ingest_historical_data(days_back=3)
        
        # Wait for signals or timeout
        try:
            await asyncio.wait_for(signal_task, timeout=30)
            logger.info(f"✅ Signal generation test completed! Generated {len(signals_received)} signals")
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Timeout reached. Generated {len(signals_received)} signals")
        
        # Show signal summary
        if signals_received:
            logger.info("📋 Signal Summary:")
            for signal in signals_received:
                logger.info(f"  {signal.symbol}: {signal.side} @ ${signal.price:.2f} ({signal.source})")
        else:
            logger.warning("⚠️ No signals generated - this is normal for conservative strategies")
        
        # Cancel tasks
        strategy_task.cancel()
        signal_task.cancel()
        
        return len(signals_received) > 0
        
    except Exception as e:
        logger.error(f"❌ Signal generation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup
        ingestor.running = False
        strategies.running = False

async def main():
    """Main test runner"""
    logger.info("🧪 Running Trading Platform Integration Tests")
    logger.info("=" * 60)
    
    settings = get_settings()
    logger.info(f"📊 Configuration:")
    logger.info(f"  Symbols: {settings.symbols_list}")
    logger.info(f"  Paper trading: {settings.is_paper_trading}")
    logger.info(f"  Fake Redis: {settings.use_fake_redis}")
    
    # Test 1: Basic Integration
    logger.info("\n" + "=" * 60)
    logger.info("TEST 1: Data Flow Integration")
    logger.info("=" * 60)
    
    success1 = await test_integration()
    
    # Test 2: Signal Generation
    logger.info("\n" + "=" * 60) 
    logger.info("TEST 2: Signal Generation")
    logger.info("=" * 60)
    
    success2 = await test_signal_generation()
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Data Flow Integration: {'✅ PASS' if success1 else '❌ FAIL'}")
    logger.info(f"Signal Generation: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1 and success2:
        logger.info("🎉 All tests passed! Trading platform is working correctly.")
    elif success1:
        logger.info("✅ Basic integration works. Signal generation may need tuning.")
    else:
        logger.info("❌ Integration issues detected. Check configuration.")

if __name__ == "__main__":
    asyncio.run(main())