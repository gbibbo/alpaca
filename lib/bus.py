#!/usr/bin/env python3
"""
lib/bus.py
Message Bus using Redis Pub/Sub with fakeredis fallback
Handles communication between microservices
"""

import json
import asyncio
import logging
from typing import Any, Callable, Dict, Optional, AsyncGenerator
from datetime import datetime
from lib.models import Bar, Signal, OrderIntent, OrderFill, MessageEvent
from lib.settings import get_settings

logger = logging.getLogger(__name__)

def _connect_redis():
    """Connect to Redis with fallback to fakeredis"""
    settings = get_settings()
    
    # Force fake redis if USE_FAKE_REDIS=1 or if settings say so
    if settings.use_fake_redis:
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
        
        # Silent fallback to fake for tests/development
        try:
            import fakeredis
            return fakeredis.FakeRedis(decode_responses=True)
        except ImportError:
            logger.error("fakeredis not available - install with: pip install fakeredis")
            raise

class MessageBus:
    """Redis-based message bus for microservice communication with fakeredis fallback"""
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client or _connect_redis()
        self.pubsub = None
        self.subscribers: Dict[str, Callable] = {}
        
    def connect(self):
        """Connect to Redis (already done in __init__)"""
        try:
            if not self.redis_client:
                self.redis_client = _connect_redis()
            
            # Test connection
            self.redis_client.ping()
            logger.info("Message bus connected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect message bus: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from Redis"""
        if self.pubsub:
            try:
                self.pubsub.close()
            except:
                pass
        if self.redis_client:
            try:
                self.redis_client.close()
            except:
                pass
        logger.info("Disconnected from message bus")
    
    # Publishing Methods
    def publish_bar(self, bar: Bar):
        """Publish market bar data"""
        channel = f"bars.{bar.symbol}"
        message = bar.model_dump_json()
        self._publish(channel, message, "bar")
    
    def publish_signal(self, signal: Signal):
        """Publish trading signal"""
        channel = f"signals.{signal.symbol}"
        message = signal.model_dump_json()
        self._publish(channel, message, "signal")
    
    def publish_order_intent(self, order: OrderIntent):
        """Publish order intention"""
        channel = "orders.intent"
        message = order.model_dump_json()
        self._publish(channel, message, "order_intent")
    
    def publish_order_fill(self, fill: OrderFill):
        """Publish order execution result"""
        channel = f"orders.fill.{fill.symbol}"
        message = fill.model_dump_json()
        self._publish(channel, message, "order_fill")
    
    def publish_system_event(self, event_type: str, source: str, data: dict):
        """Publish system event"""
        event = MessageEvent(
            event_type=event_type,
            source=source,
            data=data
        )
        channel = f"system.{event_type}"
        message = event.model_dump_json()
        self._publish(channel, message, "system_event")
    
    def _publish(self, channel: str, message: str, msg_type: str):
        """Internal publish method"""
        try:
            if not self.redis_client:
                raise Exception("Not connected to Redis")
            
            result = self.redis_client.publish(channel, message)
            logger.debug(f"Published {msg_type} to {channel}: {result} subscribers")
            
        except Exception as e:
            logger.error(f"Failed to publish {msg_type} to {channel}: {e}")
    
    # Subscription Methods
    async def subscribe_bars(self, symbol: str = "*") -> AsyncGenerator[Bar, None]:
        """Subscribe to bar data for symbol(s)"""
        pattern = f"bars.{symbol}"
        async for bar in self._subscribe_with_parser(pattern, Bar):
            yield bar
    
    async def subscribe_signals(self, symbol: str = "*") -> AsyncGenerator[Signal, None]:
        """Subscribe to signals for symbol(s)"""
        pattern = f"signals.{symbol}"
        async for signal in self._subscribe_with_parser(pattern, Signal):
            yield signal
    
    async def subscribe_order_intents(self) -> AsyncGenerator[OrderIntent, None]:
        """Subscribe to order intentions"""
        async for order in self._subscribe_with_parser("orders.intent", OrderIntent):
            yield order
    
    async def subscribe_order_fills(self, symbol: str = "*") -> AsyncGenerator[OrderFill, None]:
        """Subscribe to order fills"""
        pattern = f"orders.fill.{symbol}"
        async for fill in self._subscribe_with_parser(pattern, OrderFill):
            yield fill
    
    async def subscribe_system_events(self, event_type: str = "*") -> AsyncGenerator[MessageEvent, None]:
        """Subscribe to system events"""
        pattern = f"system.{event_type}"
        async for event in self._subscribe_with_parser(pattern, MessageEvent):
            yield event
    
    async def _subscribe_with_parser(self, pattern: str, model_class):
        """Generic subscription with automatic parsing"""
        if not self.redis_client:
            raise Exception("Not connected to Redis")
        
        pubsub = self.redis_client.pubsub()
        
        try:
            # Handle pattern vs specific subscription
            if "*" in pattern:
                # For fakeredis compatibility, expand wildcard patterns to specific channels
                if pattern.startswith("bars."):
                    # Subscribe to specific bar channels for known symbols
                    from lib.settings import get_settings
                    settings = get_settings()
                    channels = [f"bars.{symbol}" for symbol in settings.symbols_list]
                    logger.info(f"Expanding pattern {pattern} to specific channels: {channels}")
                    for channel in channels:
                        pubsub.subscribe(channel)
                elif pattern.startswith("signals."):
                    # Subscribe to specific signal channels
                    from lib.settings import get_settings
                    settings = get_settings()
                    channels = [f"signals.{symbol}" for symbol in settings.symbols_list]
                    logger.info(f"Expanding pattern {pattern} to specific channels: {channels}")
                    for channel in channels:
                        pubsub.subscribe(channel)
                elif pattern.startswith("system."):
                    # Subscribe to common system events
                    system_events = [
                        "system.service_start", "system.service_stop", "system.service_error",
                        "system.signal_generated", "system.signal_rejected", "system.signal_approved",
                        "system.historical_data_complete", "system.order_error", "system.emergency_stop"
                    ]
                    logger.info(f"Expanding pattern {pattern} to system events: {system_events}")
                    for channel in system_events:
                        pubsub.subscribe(channel)
                else:
                    # Fallback to pattern subscription for other cases
                    logger.warning(f"Using pattern subscription for {pattern} - may not work with fakeredis")
                    pubsub.psubscribe(pattern)
            else:
                pubsub.subscribe(pattern)
            
            logger.info(f"Subscribed to {pattern}")
            
            while True:
                try:
                    message = pubsub.get_message(timeout=1.0)
                    if message and message['type'] in ['message', 'pmessage']:
                        try:
                            # Parse JSON and create model instance
                            data = json.loads(message['data'])
                            instance = model_class.model_validate(data)
                            yield instance
                            
                        except Exception as parse_error:
                            logger.error(f"Failed to parse message: {parse_error}")
                            continue
                    
                    # Allow other coroutines to run
                    await asyncio.sleep(0.001)
                    
                except Exception as msg_error:
                    logger.error(f"Error receiving message: {msg_error}")
                    await asyncio.sleep(0.1)
                    
        except Exception as e:
            logger.error(f"Subscription error for {pattern}: {e}")
        finally:
            try:
                pubsub.close()
            except:
                pass
    
    # Synchronous Helper Methods
    def publish_bar_sync(self, symbol: str, timestamp: datetime, open_p: float, 
                         high: float, low: float, close: float, volume: int):
        """Synchronous bar publishing helper"""
        bar = Bar(
            symbol=symbol,
            timestamp=timestamp,
            open=open_p,
            high=high,
            low=low,
            close=close,
            volume=volume
        )
        self.publish_bar(bar)
    
    def publish_signal_sync(self, symbol: str, side: str, confidence: float, 
                           source: str, metadata: dict = None):
        """Synchronous signal publishing helper"""
        signal = Signal(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            side=side,
            confidence=confidence,
            source=source,
            metadata=metadata or {}
        )
        self.publish_signal(signal)
    
    # Health & Monitoring
    def health_check(self) -> dict:
        """Check Redis connection health"""
        try:
            if not self.redis_client:
                return {"status": "disconnected", "error": "No Redis client"}
            
            start_time = datetime.utcnow()
            self.redis_client.ping()
            latency = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            # Try to get info (might not work with fakeredis)
            try:
                info = self.redis_client.info()
                connected_clients = info.get('connected_clients', 0)
                used_memory = info.get('used_memory_human', 'unknown')
                redis_version = info.get('redis_version', 'unknown')
            except:
                # Fallback for fakeredis
                connected_clients = 1
                used_memory = 'fake'
                redis_version = 'fakeredis'
            
            return {
                "status": "healthy",
                "latency_ms": round(latency, 2),
                "connected_clients": connected_clients,
                "used_memory": used_memory,
                "redis_version": redis_version
            }
            
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def get_stats(self) -> dict:
        """Get message bus statistics"""
        try:
            if not self.redis_client:
                return {"error": "Not connected"}
            
            # Try to get pubsub info (might not work with fakeredis)
            try:
                pubsub_info = self.redis_client.execute_command('PUBSUB', 'NUMSUB')
                channels = {}
                
                # Parse channel subscriber counts
                for i in range(0, len(pubsub_info), 2):
                    channel = pubsub_info[i]
                    subscribers = pubsub_info[i + 1]
                    channels[channel] = subscribers
                
                return {
                    "channels": channels,
                    "total_channels": len(channels),
                    "total_subscribers": sum(channels.values())
                }
            except:
                # Fallback for fakeredis
                return {
                    "channels": {"fake": 1},
                    "total_channels": 1,
                    "total_subscribers": 1,
                    "note": "Using fakeredis - limited stats available"
                }
            
        except Exception as e:
            return {"error": str(e)}

# Global message bus instance
message_bus = None

# Convenience functions
def connect_bus(redis_url: str = None) -> bool:
    """Connect to message bus"""
    global message_bus
    
    if redis_url:
        # Create custom Redis client for specific URL
        try:
            import redis
            redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
            message_bus = MessageBus(redis_client)
        except:
            message_bus = MessageBus()  # Use default connection
    else:
        message_bus = MessageBus()
    
    return message_bus.connect()

def get_bus() -> MessageBus:
    """Get global message bus instance"""
    global message_bus
    if message_bus is None:
        message_bus = MessageBus()
    return message_bus