#!/usr/bin/env python3
"""
lib/bus.py
Enhanced Message Bus with Redis Streams support - REFACTORED VERSION
Implements ChatGPT's factory pattern for clean backend separation
"""

import json
import asyncio
import logging
import os
import time
import socket
from typing import Any, Callable, Dict, Optional, AsyncGenerator, List, Tuple, Awaitable
from datetime import datetime, timezone
from lib.models import Bar, Signal, OrderIntent, OrderFill, MessageEvent
from lib.settings import get_settings
from lib.time_utils import TimeUtils, MonotonicTimer

logger = logging.getLogger(__name__)

# Configuration management
def _get_bus_config():
    """Get current bus configuration from environment"""
    return {
        "backend": os.getenv("BUS_BACKEND", "pubsub").lower(),  # Default to pubsub for compatibility
        "use_fake": bool(int(os.getenv("USE_FAKE_REDIS", "0")))
    }

def _connect_redis():
    """Connect to Redis with automatic fallback to fakeredis"""
    settings = get_settings()
    config = _get_bus_config()  # Read fresh from environment

    # Force fake redis if USE_FAKE_REDIS=1 or if settings say so
    if settings.use_fake_redis or config["use_fake"]:
        logger.info("Using fakeredis (forced by configuration)")
        import fakeredis
        return fakeredis.FakeRedis(decode_responses=True)

    # Try real Redis first
    try:
        import redis
        r = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        # Test connection
        r.ping()
        logger.info(f"Connected to real Redis: {settings.redis_url}")
        return r
    except Exception as e:
        logger.warning(f"Real Redis connection failed: {e}")
        logger.info("Falling back to fakeredis for testing")

        try:
            import fakeredis
            return fakeredis.FakeRedis(decode_responses=True)
        except ImportError:
            logger.error("fakeredis not available - install with: pip install fakeredis")
            raise


class PubSubBus:
    """
    Legacy Redis Pub/Sub backend implementation
    Maintains compatibility with existing code
    """

    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.pubsub = None
        self.subscribers: Dict[str, Callable] = {}

        # Performance tracking
        self.messages_published = 0
        self.messages_consumed = 0
        self.messages_acked = 0

    def publish_bar(self, bar: Bar):
        """Publish market bar data"""
        message_data = {
            "type": "bar",
            "data": bar.model_dump_json(),
            "symbol": bar.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        channel = f"bars.{bar.symbol}"
        result = self.redis_client.publish(channel, json.dumps(message_data))
        self.messages_published += 1
        logger.debug(f"Published bar to {channel}: {result} subscribers")

    def publish_signal(self, signal: Signal):
        """Publish trading signal"""
        message_data = {
            "type": "signal",
            "data": signal.model_dump_json(),
            "symbol": signal.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        channel = f"signals.{signal.symbol}"
        result = self.redis_client.publish(channel, json.dumps(message_data))
        self.messages_published += 1
        logger.debug(f"Published signal to {channel}: {result} subscribers")

    def publish_order_intent(self, order: OrderIntent):
        """Publish order intention"""
        message_data = {
            "type": "order_intent",
            "data": order.model_dump_json(),
            "symbol": order.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        channel = "orders.intent"
        result = self.redis_client.publish(channel, json.dumps(message_data))
        self.messages_published += 1
        logger.debug(f"Published order intent to {channel}: {result} subscribers")

    def publish_order_fill(self, fill: OrderFill):
        """Publish order execution result"""
        message_data = {
            "type": "order_fill",
            "data": fill.model_dump_json(),
            "symbol": fill.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        channel = f"orders.fill.{fill.symbol}"
        result = self.redis_client.publish(channel, json.dumps(message_data))
        self.messages_published += 1
        logger.debug(f"Published order fill to {channel}: {result} subscribers")

    def publish_system_event(self, event_type: str, source: str, data: dict):
        """Publish system event"""
        event = MessageEvent(
            event_type=event_type,
            source=source,
            data=data
        )
        message_data = {
            "type": "system_event",
            "data": event.model_dump_json(),
            "event_type": event_type,
            "source": source,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        channel = f"system.{event_type}"
        result = self.redis_client.publish(channel, json.dumps(message_data))
        self.messages_published += 1
        logger.debug(f"Published system event to {channel}: {result} subscribers")

    async def subscribe_bars(self, symbol: str = "*") -> AsyncGenerator[Bar, None]:
        """Subscribe to bar data for symbol(s)"""
        if symbol == "*":
            settings = get_settings()
            channels = [f"bars.{s}" for s in settings.symbols_list]
        else:
            channels = [f"bars.{symbol}"]

        async for bar in self._subscribe_with_parser(channels, Bar):
            yield bar

    async def subscribe_signals(self, symbol: str = "*") -> AsyncGenerator[Signal, None]:
        """Subscribe to signals for symbol(s)"""
        if symbol == "*":
            settings = get_settings()
            channels = [f"signals.{s}" for s in settings.symbols_list]
        else:
            channels = [f"signals.{symbol}"]

        async for signal in self._subscribe_with_parser(channels, Signal):
            yield signal

    async def subscribe_order_intents(self) -> AsyncGenerator[OrderIntent, None]:
        """Subscribe to order intentions"""
        async for order in self._subscribe_with_parser(["orders.intent"], OrderIntent):
            yield order

    async def subscribe_order_fills(self, symbol: str = "*") -> AsyncGenerator[OrderFill, None]:
        """Subscribe to order fills"""
        if symbol == "*":
            settings = get_settings()
            channels = [f"orders.fill.{s}" for s in settings.symbols_list]
        else:
            channels = [f"orders.fill.{symbol}"]

        async for fill in self._subscribe_with_parser(channels, OrderFill):
            yield fill

    async def subscribe_system_events(self, event_type: str = "*") -> AsyncGenerator[MessageEvent, None]:
        """Subscribe to system events"""
        if event_type == "*":
            channels = [
                "system.service_start", "system.service_stop", "system.service_error",
                "system.signal_generated", "system.signal_rejected", "system.signal_approved",
                "system.historical_data_complete", "system.order_error", "system.emergency_stop"
            ]
        else:
            channels = [f"system.{event_type}"]

        async for event in self._subscribe_with_parser(channels, MessageEvent):
            yield event

    async def _subscribe_with_parser(self, channels: List[str], model_class):
        """Generic subscription with automatic parsing"""
        if not self.redis_client:
            raise Exception("Not connected to Redis")

        pubsub = self.redis_client.pubsub()

        try:
            # Subscribe to all channels
            for channel in channels:
                pubsub.subscribe(channel)

            logger.debug(f"Subscribed to channels: {channels}")

            consecutive_errors = 0
            max_consecutive_errors = 5

            while consecutive_errors < max_consecutive_errors:
                try:
                    message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)

                    if message and message['type'] == 'message':
                        try:
                            # Parse message data
                            msg_data = json.loads(message['data'])

                            # Extract the actual data
                            if 'data' in msg_data:
                                actual_data = json.loads(msg_data['data'])
                            else:
                                actual_data = msg_data

                            instance = model_class.model_validate(actual_data)
                            consecutive_errors = 0
                            self.messages_consumed += 1
                            self.messages_acked += 1  # Pub/Sub auto-acks
                            yield instance

                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error: {e}")
                            consecutive_errors += 1
                        except Exception as parse_error:
                            logger.error(f"Failed to parse message: {parse_error}")
                            consecutive_errors += 1

                    await asyncio.sleep(0.001)

                except Exception as msg_error:
                    consecutive_errors += 1
                    logger.error(f"Error receiving message (#{consecutive_errors}): {msg_error}")
                    await asyncio.sleep(0.1)

            logger.error(f"Too many consecutive errors, stopping subscription to {channels}")

        except Exception as e:
            logger.error(f"Subscription error for {channels}: {e}")
        finally:
            try:
                pubsub.close()
            except:
                pass

    def connect(self):
        """Connect to Redis"""
        try:
            self.redis_client.ping()
            logger.info("PubSub backend connected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect PubSub backend: {e}")
            return False

    def disconnect(self):
        """Disconnect from Redis"""
        if self.pubsub:
            try:
                self.pubsub.close()
            except:
                pass
        logger.info("PubSub backend disconnected")

    def health_check(self) -> dict:
        """Health check for Pub/Sub backend"""
        try:
            start_time = time.time()
            self.redis_client.ping()
            latency = (time.time() - start_time) * 1000

            return {
                "status": "healthy",
                "backend": "pubsub",
                "latency_ms": round(latency, 2),
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed,
                "messages_acked": self.messages_acked
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_stats(self) -> dict:
        """Get comprehensive statistics"""
        return {
            "backend": "pubsub",
            "mode": "pubsub",
            "messages_published": self.messages_published,
            "messages_consumed": self.messages_consumed,
            "messages_acked": self.messages_acked,
            "supports_streams": False
        }


class MessageBus:
    """
    Factory-based Message Bus with pluggable backends
    Implements ChatGPT's recommended architecture for clean separation
    """

    def __init__(self, redis_client=None, force_backend=None):
        self.redis_client = redis_client or _connect_redis()

        # Store Redis connection details for Streams backend
        self.redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
        self.redis_db = int(os.getenv("REDIS_DB", "0"))

        # Determine backend
        config = _get_bus_config()
        self.backend_type = force_backend or config["backend"]

        # Initialize appropriate backend
        if self.backend_type == "streams":
            self.backend = self._create_streams_backend()
        else:
            self.backend = self._create_pubsub_backend()

        logger.info(f"MessageBus initialized with {self.backend_type} backend")

    def _create_streams_backend(self):
        """Create Redis Streams backend"""
        try:
            # Check if Redis supports Streams
            if self._check_streams_support():
                from lib.bus_streams import RedisStreamsBus
                # Pass the Redis URL instead of the client object
                return RedisStreamsBus(self.redis_url, self.redis_db)
            else:
                logger.warning("Redis Streams not supported, falling back to Pub/Sub")
                return self._create_pubsub_backend()
        except Exception as e:
            logger.error(f"Failed to create Streams backend: {e}")
            logger.info("Falling back to Pub/Sub backend")
            return self._create_pubsub_backend()

    def _create_pubsub_backend(self):
        """Create Redis Pub/Sub backend (legacy)"""
        return PubSubBus(self.redis_client)

    def _check_streams_support(self) -> bool:
        """Check if Redis supports Streams (Redis 5.0+)"""
        try:
            # First check if this is fakeredis
            if "fakeredis" in str(type(self.redis_client)).lower():
                logger.debug("FakeRedis detected - Streams not supported")
                return False

            # For real Redis, test with a simple XINFO command
            try:
                # Try to get info about a non-existent stream
                result = self.redis_client.execute_command("XINFO", "GROUPS", "test:stream:nonexistent")
                logger.debug("Redis Streams support confirmed")
                return True
            except Exception as cmd_error:
                if "no such key" in str(cmd_error).lower() or "ERR no such key" in str(cmd_error):
                    # This is expected for non-existent stream, means Streams are supported
                    logger.debug("Redis Streams support confirmed (key not found is expected)")
                    return True
                else:
                    logger.debug(f"Redis Streams not supported: {cmd_error}")
                    return False

        except Exception as e:
            logger.debug(f"Redis Streams not supported: {e}")
            return False

    # Delegate all methods to backend
    def publish_bar(self, bar: Bar):
        """Publish market bar data"""
        return self.backend.publish_bar(bar)

    def publish_signal(self, signal: Signal):
        """Publish trading signal"""
        return self.backend.publish_signal(signal)

    def publish_order_intent(self, order: OrderIntent):
        """Publish order intention"""
        return self.backend.publish_order_intent(order)

    def publish_order_fill(self, fill: OrderFill):
        """Publish order execution result"""
        return self.backend.publish_order_fill(fill)

    def publish_system_event(self, event_type: str, source: str, data: dict):
        """Publish system event"""
        return self.backend.publish_system_event(event_type, source, data)

    async def subscribe_bars(self, symbol: str = "*") -> AsyncGenerator[Bar, None]:
        """Subscribe to bar data for symbol(s)"""
        async for bar in self.backend.subscribe_bars(symbol):
            yield bar

    async def subscribe_signals(self, symbol: str = "*") -> AsyncGenerator[Signal, None]:
        """Subscribe to signals for symbol(s)"""
        async for signal in self.backend.subscribe_signals(symbol):
            yield signal

    async def subscribe_order_intents(self) -> AsyncGenerator[OrderIntent, None]:
        """Subscribe to order intentions"""
        async for order in self.backend.subscribe_order_intents():
            yield order

    async def subscribe_order_fills(self, symbol: str = "*") -> AsyncGenerator[OrderFill, None]:
        """Subscribe to order fills"""
        async for fill in self.backend.subscribe_order_fills(symbol):
            yield fill

    async def subscribe_system_events(self, event_type: str = "*") -> AsyncGenerator[MessageEvent, None]:
        """Subscribe to system events"""
        async for event in self.backend.subscribe_system_events(event_type):
            yield event

    def connect(self):
        """Connect to Redis"""
        return self.backend.connect()

    def disconnect(self):
        """Disconnect from Redis"""
        self.backend.disconnect()

    def health_check(self) -> dict:
        """Health check"""
        return self.backend.health_check()

    def get_stats(self) -> dict:
        """Get comprehensive statistics"""
        return self.backend.get_stats()


# Global message bus instance
_message_bus: Optional[MessageBus] = None

# Convenience functions
def connect_bus(redis_url: str = None, force_backend: str = None) -> bool:
    """Connect to message bus with automatic fallback"""
    global _message_bus

    if redis_url:
        # Create custom Redis client for specific URL
        try:
            import redis
            redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
            _message_bus = MessageBus(redis_client, force_backend)
        except:
            _message_bus = MessageBus(force_backend=force_backend)
    else:
        _message_bus = MessageBus(force_backend=force_backend)

    return _message_bus.connect()

def get_bus() -> MessageBus:
    """Get global message bus instance"""
    global _message_bus
    if _message_bus is None:
        _message_bus = MessageBus()
        _message_bus.connect()
    return _message_bus