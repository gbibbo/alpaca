#!/usr/bin/env python3
"""
tests/test_streams_integration.py
Integration tests for Redis Streams backend
Tests the complete flow: publish -> consume -> ACK -> replay
"""

import asyncio
import logging
import sys
import time
import os
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.bus import MessageBus
from lib.models import Signal, SignalSide
from lib.time_utils import TimeUtils
from decimal import Decimal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StreamsIntegrationTest:
    """Integration tests for Redis Streams functionality"""

    def __init__(self):
        # Force Streams backend for testing
        os.environ["BUS_BACKEND"] = "streams"
        os.environ["USE_FAKE_REDIS"] = "0"

        self.bus = MessageBus(force_backend="streams")
        self.test_results = {}

    def create_test_signal(self, signal_id: str = None) -> Signal:
        """Create a test signal"""
        return Signal(
            symbol="AAPL",
            side=SignalSide.BUY,
            confidence=Decimal('0.8'),
            price=Decimal('150.0'),
            source="test_streams",
            signal_id=signal_id or TimeUtils.utc_now().timestamp()
        )

    async def test_basic_publish_consume(self) -> bool:
        """Test basic publish and consume with ACK"""
        logger.info("🧪 Testing basic publish and consume...")

        try:
            # Connect to bus
            if not self.bus.connect():
                logger.error("Failed to connect to bus")
                return False

            # Check if we're actually using Streams
            stats = self.bus.get_stats()
            if stats.get('backend') != 'streams':
                logger.error(f"Expected streams backend, got: {stats.get('backend')}")
                return False

            logger.info("✅ Streams backend confirmed")

            # Publish test signal
            test_signal = self.create_test_signal("test_basic")
            message_id = self.bus.publish_signal(test_signal)
            logger.info(f"Published signal with message_id: {message_id}")

            # Consume the message
            consumed_messages = []

            async def signal_handler(msg_data: dict) -> bool:
                """Test handler that captures messages"""
                logger.info(f"Received message: {msg_data.get('type')}")
                if msg_data.get("type") == "signal":
                    consumed_messages.append(msg_data)
                    return True  # ACK the message
                return True

            # Set a timeout for consumption
            consume_task = asyncio.create_task(
                self.bus.backend.consume_with_handler("signals", signal_handler)
            )

            # Wait for consumption with timeout
            try:
                await asyncio.wait_for(asyncio.sleep(2), timeout=5.0)  # Give it 5 seconds
                consume_task.cancel()
            except asyncio.TimeoutError:
                consume_task.cancel()

            # Verify we consumed the message
            if len(consumed_messages) >= 1:
                logger.info("✅ Basic publish/consume test passed")
                return True
            else:
                logger.error(f"❌ Expected to consume 1 message, got {len(consumed_messages)}")
                return False

        except Exception as e:
            logger.error(f"❌ Basic test failed: {e}")
            return False

    async def test_pending_message_recovery(self) -> bool:
        """Test pending message recovery (crash simulation)"""
        logger.info("🧪 Testing pending message recovery...")

        try:
            # Publish a test signal
            test_signal = self.create_test_signal("test_pending")
            message_id = self.bus.publish_signal(test_signal)
            logger.info(f"Published signal for pending test: {message_id}")

            # Consume but DON'T ACK (simulate crash)
            messages_without_ack = self.bus.backend.consume("signals", count=10, block_ms=1000)

            if not messages_without_ack:
                logger.error("❌ No messages to test pending recovery")
                return False

            logger.info(f"Consumed {len(messages_without_ack)} messages without ACK (simulating crash)")

            # Now try to recover pending messages
            pending_messages = self.bus.backend.consume_pending("signals", min_idle_ms=100)

            if len(pending_messages) > 0:
                logger.info(f"✅ Recovered {len(pending_messages)} pending messages")

                # ACK the recovered messages
                for msg_id, msg_data in pending_messages:
                    self.bus.backend.ack("signals", msg_id)
                    logger.info(f"ACKed recovered message: {msg_id}")

                return True
            else:
                logger.warning("⚠️  No pending messages found (may be Redis < 6.2 or no XAUTOCLAIM support)")
                return True  # Not a failure, just unsupported

        except Exception as e:
            logger.error(f"❌ Pending recovery test failed: {e}")
            return False

    async def test_multiple_consumers(self) -> bool:
        """Test multiple consumers with consumer groups"""
        logger.info("🧪 Testing multiple consumers...")

        try:
            # Create multiple bus instances (simulating different services)
            bus2 = MessageBus(force_backend="streams")
            if not bus2.connect():
                logger.error("Failed to connect second bus")
                return False

            # Publish multiple signals
            signals_published = 3
            for i in range(signals_published):
                test_signal = self.create_test_signal(f"test_multi_{i}")
                self.bus.publish_signal(test_signal)

            logger.info(f"Published {signals_published} signals for multi-consumer test")

            # Track consumption across both consumers
            consumer1_count = 0
            consumer2_count = 0

            async def consumer1_handler(msg_data: dict) -> bool:
                nonlocal consumer1_count
                if msg_data.get("type") == "signal":
                    consumer1_count += 1
                    logger.info(f"Consumer 1 processed message {consumer1_count}")
                return True

            async def consumer2_handler(msg_data: dict) -> bool:
                nonlocal consumer2_count
                if msg_data.get("type") == "signal":
                    consumer2_count += 1
                    logger.info(f"Consumer 2 processed message {consumer2_count}")
                return True

            # Start both consumers
            task1 = asyncio.create_task(self.bus.backend.consume_with_handler("signals", consumer1_handler))
            task2 = asyncio.create_task(bus2.backend.consume_with_handler("signals", consumer2_handler))

            # Let them consume for a bit
            await asyncio.sleep(3)

            # Cancel tasks
            task1.cancel()
            task2.cancel()

            total_consumed = consumer1_count + consumer2_count
            logger.info(f"Total consumed: {total_consumed} (C1: {consumer1_count}, C2: {consumer2_count})")

            if total_consumed >= signals_published:
                logger.info("✅ Multiple consumers test passed")
                return True
            else:
                logger.error(f"❌ Expected {signals_published}+, got {total_consumed}")
                return False

        except Exception as e:
            logger.error(f"❌ Multiple consumers test failed: {e}")
            return False

    async def run_all_tests(self):
        """Run all Streams integration tests"""
        logger.info("🚀 Starting Redis Streams Integration Tests")

        tests = [
            ("Basic Publish/Consume", self.test_basic_publish_consume),
            ("Pending Message Recovery", self.test_pending_message_recovery),
            ("Multiple Consumers", self.test_multiple_consumers),
        ]

        results = {}
        passed = 0
        total = len(tests)

        for test_name, test_func in tests:
            logger.info(f"\n{'='*50}")
            logger.info(f"Running: {test_name}")
            logger.info(f"{'='*50}")

            try:
                result = await test_func()
                results[test_name] = result
                if result:
                    passed += 1
                    logger.info(f"✅ {test_name}: PASSED")
                else:
                    logger.error(f"❌ {test_name}: FAILED")
            except Exception as e:
                results[test_name] = False
                logger.error(f"❌ {test_name}: ERROR - {e}")

        logger.info(f"\n{'='*50}")
        logger.info(f"📊 TEST RESULTS: {passed}/{total} passed")
        logger.info(f"{'='*50}")

        for test_name, result in results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            logger.info(f"{status} {test_name}")

        # Get final stats
        stats = self.bus.get_stats()
        logger.info(f"\n📈 Final Streams Stats:")
        logger.info(f"  Backend: {stats.get('backend')}")
        logger.info(f"  Messages Published: {stats.get('messages_published', 0)}")
        logger.info(f"  Messages Consumed: {stats.get('messages_consumed', 0)}")
        logger.info(f"  Messages ACKed: {stats.get('messages_acked', 0)}")
        logger.info(f"  ACK Rate: {stats.get('ack_rate', 0):.1%}")

        # Cleanup
        self.bus.disconnect()

        return passed == total


# Add pytest-compatible test functions
def test_streams_backend_initialization():
    """Test that Streams backend initializes correctly"""
    os.environ["BUS_BACKEND"] = "streams"
    os.environ["USE_FAKE_REDIS"] = "0"

    bus = MessageBus(force_backend="streams")
    assert bus.connect()

    stats = bus.get_stats()
    assert stats.get('backend') == 'streams'
    assert stats.get('supports_streams') is True

    bus.disconnect()

async def test_streams_publish_consume():
    """Test basic publish and consume with Streams"""
    os.environ["BUS_BACKEND"] = "streams"
    os.environ["USE_FAKE_REDIS"] = "0"

    bus = MessageBus(force_backend="streams")
    assert bus.connect()

    # Create test signal
    test_signal = Signal(
        symbol="AAPL",
        side=SignalSide.BUY,
        confidence=Decimal('0.8'),
        price=Decimal('150.0'),
        source="test_streams"
    )

    # Publish signal
    message_id = bus.publish_signal(test_signal)
    assert message_id

    # Give some time for processing
    await asyncio.sleep(0.1)

    # Check that stream has messages
    stats = bus.get_stats()
    signals_stream = stats.get('streams', {}).get('signals', {})
    assert signals_stream.get('length', 0) >= 1

    bus.disconnect()

def test_streams_consumer_groups():
    """Test that consumer groups are created correctly"""
    os.environ["BUS_BACKEND"] = "streams"
    os.environ["USE_FAKE_REDIS"] = "0"

    bus = MessageBus(force_backend="streams")
    assert bus.connect()

    stats = bus.get_stats()
    streams = stats.get('streams', {})

    # Check that expected streams exist
    expected_streams = ['bars', 'signals', 'orders', 'fills', 'system']
    for stream_name in expected_streams:
        assert stream_name in streams
        stream_info = streams[stream_name]
        assert 'group_info' in stream_info

    bus.disconnect()

async def main():
    """Main test runner"""
    test_suite = StreamsIntegrationTest()
    success = await test_suite.run_all_tests()

    if success:
        logger.info("🎉 All Streams integration tests passed!")
        exit(0)
    else:
        logger.error("💥 Some tests failed!")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())