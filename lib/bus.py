#!/usr/bin/env python3
"""
lib/bus.py
Enhanced Message Bus with Redis Streams support
Implements ChatGPT's recommendations for better reliability:
- Redis Streams with consumer groups for at-least-once delivery
- Automatic fallback from Streams to Pub/Sub for compatibility
- Message replay capability for debugging and recovery
- Improved error handling and connection resilience
"""

import json
import asyncio
import logging
import os
import time
from typing import Any, Callable, Dict, Optional, AsyncGenerator, List
from datetime import datetime
from lib.models import Bar, Signal, OrderIntent, OrderFill, MessageEvent
from lib.settings import get_settings
from lib.time_utils import TimeUtils, MonotonicTimer

logger = logging.getLogger(__name__)

def _connect_redis():
    """Connect to Redis with automatic fallback to fakeredis"""
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


class StreamsConfig:
    """Configuration for Redis Streams"""
    
    def __init__(self):
        # Stream names for different message types
        self.streams = {
            "bars": "trading:bars",
            "signals": "trading:signals", 
            "orders": "trading:orders",
            "fills": "trading:fills",
            "system": "trading:system"
        }
        
        # Consumer group names
        self.consumer_groups = {
            "bars": "bars_processors",
            "signals": "signal_processors", 
            "orders": "order_processors",
            "fills": "fill_processors",
            "system": "system_processors"
        }
        
        # Consumer names (unique per service instance)
        import socket
        hostname = socket.gethostname()
        pid = os.getpid()
        self.consumer_id = f"{hostname}_{pid}_{int(time.time())}"
        
        # Stream settings
        self.max_stream_length = 10000  # Keep last 10k messages per stream
        self.consumer_timeout = 1000    # 1 second timeout for stream reads
        self.ack_timeout = 300000       # 5 minutes to process message before retry


class MessageBus:
    """
    Enhanced Redis-based message bus with Streams support
    Falls back to Pub/Sub for compatibility with fakeredis
    """
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client or _connect_redis()
        self.pubsub = None
        self.subscribers: Dict[str, Callable] = {}
        
        # Determine Redis capabilities
        self.supports_streams = self._check_streams_support()
        self.streams_config = StreamsConfig()
        
        # Performance tracking
        self.messages_published = 0
        self.messages_consumed = 0
        self.stream_errors = 0
        
        # Log which Redis and mode we're using
        redis_type = "FakeRedis" if "fakeredis" in str(type(self.redis_client)) else "Real Redis"
        mode = "Streams" if self.supports_streams else "Pub/Sub"
        logger.info(f"MessageBus initialized with {redis_type} using {mode} mode")
        
        if self.supports_streams:
            self._initialize_streams()
        
    def _check_streams_support(self) -> bool:
        """Check if Redis supports Streams (Redis 5.0+)"""
        try:
            # Try to execute a streams command
            self.redis_client.xinfo_consumers("test_stream", "test_group")
            return True
        except Exception:
            # Fakeredis or older Redis - use Pub/Sub fallback
            return False
    
    def _initialize_streams(self):
        """Initialize Redis Streams and consumer groups"""
        try:
            for stream_type, stream_name in self.streams_config.streams.items():
                consumer_group = self.streams_config.consumer_groups[stream_type]
                
                try:
                    # Create consumer group (will fail if already exists)
                    self.redis_client.xgroup_create(
                        stream_name, 
                        consumer_group, 
                        id='0', 
                        mkstream=True
                    )
                    logger.debug(f"Created consumer group {consumer_group} for stream {stream_name}")
                except Exception as e:
                    if "BUSYGROUP" in str(e):
                        logger.debug(f"Consumer group {consumer_group} already exists")
                    else:
                        logger.warning(f"Error creating consumer group {consumer_group}: {e}")
                        
        except Exception as e:
            logger.error(f"Error initializing streams: {e}")
            self.supports_streams = False
    
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
    
    # Publishing Methods (Enhanced with Streams support)
    def publish_bar(self, bar: Bar):
        """Publish market bar data"""
        message_data = {
            "type": "bar",
            "data": bar.model_dump_json(),
            "symbol": bar.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        self._publish_message("bars", message_data)
    
    def publish_signal(self, signal: Signal):
        """Publish trading signal"""
        message_data = {
            "type": "signal",
            "data": signal.model_dump_json(),
            "symbol": signal.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        self._publish_message("signals", message_data)
    
    def publish_order_intent(self, order: OrderIntent):
        """Publish order intention"""
        message_data = {
            "type": "order_intent",
            "data": order.model_dump_json(),
            "symbol": order.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        self._publish_message("orders", message_data)
    
    def publish_order_fill(self, fill: OrderFill):
        """Publish order execution result"""
        message_data = {
            "type": "order_fill",
            "data": fill.model_dump_json(),
            "symbol": fill.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        self._publish_message("fills", message_data)
    
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
        self._publish_message("system", message_data)
    
    def _publish_message(self, message_type: str, message_data: dict):
        """Internal publish method supporting both Streams and Pub/Sub"""
        try:
            if self.supports_streams:
                self._publish_to_stream(message_type, message_data)
            else:
                self._publish_to_pubsub(message_type, message_data)
            
            self.messages_published += 1
            
        except Exception as e:
            logger.error(f"Failed to publish {message_type}: {e}")
    
    def _publish_to_stream(self, message_type: str, message_data: dict):
        """Publish message to Redis Stream"""
        stream_name = self.streams_config.streams.get(message_type)
        if not stream_name:
            logger.error(f"Unknown message type for streams: {message_type}")
            return
        
        try:
            # Add message to stream
            message_id = self.redis_client.xadd(
                stream_name,
                message_data,
                maxlen=self.streams_config.max_stream_length
            )
            
            logger.debug(f"Published to stream {stream_name}: {message_id}")
            
        except Exception as e:
            logger.error(f"Error publishing to stream {stream_name}: {e}")
            self.stream_errors += 1
    
    def _publish_to_pubsub(self, message_type: str, message_data: dict):
        """Publish message to Redis Pub/Sub (fallback mode)"""
        # Use the original channel naming for backward compatibility
        if message_type == "bars":
            channel = f"bars.{message_data.get('symbol', 'unknown')}"
        elif message_type == "signals":
            channel = f"signals.{message_data.get('symbol', 'unknown')}"
        elif message_type == "orders":
            channel = "orders.intent"
        elif message_type == "fills":
            channel = f"orders.fill.{message_data.get('symbol', 'unknown')}"
        elif message_type == "system":
            channel = f"system.{message_data.get('event_type', 'unknown')}"
        else:
            channel = f"trading.{message_type}"
        
        # Publish to Pub/Sub channel
        result = self.redis_client.publish(channel, json.dumps(message_data))
        logger.debug(f"Published to channel {channel}: {result} subscribers")
    
    # Subscription Methods (Enhanced with Streams support)
    async def subscribe_bars(self, symbol: str = "*") -> AsyncGenerator[Bar, None]:
        """Subscribe to bar data for symbol(s)"""
        if self.supports_streams:
            async for message in self._subscribe_from_stream("bars"):
                try:
                    data = json.loads(message["data"])
                    bar = Bar.model_validate(data)
                    if symbol == "*" or bar.symbol == symbol:
                        yield bar
                except Exception as e:
                    logger.error(f"Error parsing bar message: {e}")
        else:
            # Fallback to Pub/Sub
            pattern = f"bars.{symbol}"
            async for bar in self._subscribe_with_parser(pattern, Bar):
                yield bar
    
    async def subscribe_signals(self, symbol: str = "*") -> AsyncGenerator[Signal, None]:
        """Subscribe to signals for symbol(s)"""
        if self.supports_streams:
            async for message in self._subscribe_from_stream("signals"):
                try:
                    data = json.loads(message["data"])
                    signal = Signal.model_validate(data)
                    if symbol == "*" or signal.symbol == symbol:
                        yield signal
                except Exception as e:
                    logger.error(f"Error parsing signal message: {e}")
        else:
            # Fallback to Pub/Sub
            pattern = f"signals.{symbol}"
            async for signal in self._subscribe_with_parser(pattern, Signal):
                yield signal
    
    async def subscribe_order_intents(self) -> AsyncGenerator[OrderIntent, None]:
        """Subscribe to order intentions"""
        if self.supports_streams:
            async for message in self._subscribe_from_stream("orders"):
                try:
                    if message.get("type") == "order_intent":
                        data = json.loads(message["data"])
                        order = OrderIntent.model_validate(data)
                        yield order
                except Exception as e:
                    logger.error(f"Error parsing order intent message: {e}")
        else:
            # Fallback to Pub/Sub
            async for order in self._subscribe_with_parser("orders.intent", OrderIntent):
                yield order
    
    async def subscribe_order_fills(self, symbol: str = "*") -> AsyncGenerator[OrderFill, None]:
        """Subscribe to order fills"""
        if self.supports_streams:
            async for message in self._subscribe_from_stream("fills"):
                try:
                    if message.get("type") == "order_fill":
                        data = json.loads(message["data"])
                        fill = OrderFill.model_validate(data)
                        if symbol == "*" or fill.symbol == symbol:
                            yield fill
                except Exception as e:
                    logger.error(f"Error parsing order fill message: {e}")
        else:
            # Fallback to Pub/Sub
            pattern = f"orders.fill.{symbol}"
            async for fill in self._subscribe_with_parser(pattern, OrderFill):
                yield fill
    
    async def subscribe_system_events(self, event_type: str = "*") -> AsyncGenerator[MessageEvent, None]:
        """Subscribe to system events"""
        if self.supports_streams:
            async for message in self._subscribe_from_stream("system"):
                try:
                    if message.get("type") == "system_event":
                        data = json.loads(message["data"])
                        event = MessageEvent.model_validate(data)
                        if event_type == "*" or event.event_type == event_type:
                            yield event
                except Exception as e:
                    logger.error(f"Error parsing system event message: {e}")
        else:
            # Fallback to Pub/Sub
            pattern = f"system.{event_type}"
            async for event in self._subscribe_with_parser(pattern, MessageEvent):
                yield event
    
    async def _subscribe_from_stream(self, stream_type: str) -> AsyncGenerator[Dict, None]:
        """Subscribe to messages from Redis Stream with consumer group"""
        stream_name = self.streams_config.streams.get(stream_type)
        consumer_group = self.streams_config.consumer_groups.get(stream_type)
        consumer_id = self.streams_config.consumer_id
        
        if not stream_name or not consumer_group:
            logger.error(f"Invalid stream configuration for type: {stream_type}")
            return
        
        logger.info(f"Subscribing to stream {stream_name} as {consumer_id} in group {consumer_group}")
        
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while consecutive_errors < max_consecutive_errors:
            try:
                # Read new messages from the stream
                messages = self.redis_client.xreadgroup(
                    consumer_group,
                    consumer_id,
                    {stream_name: '>'},
                    count=10,  # Read up to 10 messages at once
                    block=self.streams_config.consumer_timeout
                )
                
                if messages:
                    consecutive_errors = 0  # Reset error counter
                    
                    for stream, msgs in messages:
                        for msg_id, msg_data in msgs:
                            try:
                                self.messages_consumed += 1
                                yield msg_data
                                
                                # Acknowledge message processing
                                self.redis_client.xack(stream_name, consumer_group, msg_id)
                                logger.debug(f"Processed and acked message {msg_id}")
                                
                            except Exception as e:
                                logger.error(f"Error processing stream message {msg_id}: {e}")
                
                # Also process any pending messages that weren't acked
                await self._process_pending_messages(stream_name, consumer_group, consumer_id)
                
                # Brief pause between reads
                await asyncio.sleep(0.001)
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Stream read error #{consecutive_errors}: {e}")
                
                if consecutive_errors < max_consecutive_errors:
                    await asyncio.sleep(min(1.0 * consecutive_errors, 10.0))  # Progressive backoff
                else:
                    logger.error(f"Too many consecutive stream errors, stopping subscription to {stream_name}")
                    break
    
    async def _process_pending_messages(self, stream_name: str, consumer_group: str, consumer_id: str):
        """Process any pending (unacknowledged) messages"""
        try:
            # Get pending messages for this consumer
            pending = self.redis_client.xpending_range(
                stream_name, 
                consumer_group, 
                min='-', 
                max='+', 
                count=10,
                consumer=consumer_id
            )
            
            if pending:
                logger.debug(f"Processing {len(pending)} pending messages")
                
                for msg_info in pending:
                    msg_id = msg_info['message_id']
                    
                    # Check if message is too old (timeout exceeded)
                    if msg_info['time_since_delivered'] > self.streams_config.ack_timeout:
                        logger.warning(f"Message {msg_id} timed out, will be redelivered")
                        continue
                    
                    # Try to re-process the message
                    try:
                        msg_data = self.redis_client.xrange(stream_name, min=msg_id, max=msg_id, count=1)
                        if msg_data:
                            _, data = msg_data[0]
                            yield data
                            
                            # Acknowledge if successful
                            self.redis_client.xack(stream_name, consumer_group, msg_id)
                            logger.debug(f"Re-processed pending message {msg_id}")
                            
                    except Exception as e:
                        logger.error(f"Error re-processing pending message {msg_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error checking pending messages: {e}")
    
    async def _subscribe_with_parser(self, pattern: str, model_class):
        """Generic subscription with automatic parsing (Pub/Sub fallback)"""
        if not self.redis_client:
            raise Exception("Not connected to Redis")
        
        pubsub = self.redis_client.pubsub()
        
        try:
            # Handle pattern vs specific subscription - improved for fakeredis
            if "*" in pattern:
                # For fakeredis compatibility, expand wildcard patterns to specific channels
                if pattern.startswith("bars."):
                    settings = get_settings()
                    channels = [f"bars.{symbol}" for symbol in settings.symbols_list]
                    for channel in channels:
                        pubsub.subscribe(channel)
                elif pattern.startswith("signals."):
                    settings = get_settings()
                    channels = [f"signals.{symbol}" for symbol in settings.symbols_list]
                    for channel in channels:
                        pubsub.subscribe(channel)
                elif pattern.startswith("orders.fill."):
                    settings = get_settings()
                    channels = [f"orders.fill.{symbol}" for symbol in settings.symbols_list]
                    for channel in channels:
                        pubsub.subscribe(channel)
                elif pattern.startswith("system."):
                    system_events = [
                        "system.service_start", "system.service_stop", "system.service_error",
                        "system.signal_generated", "system.signal_rejected", "system.signal_approved",
                        "system.historical_data_complete", "system.order_error", "system.emergency_stop"
                    ]
                    for channel in system_events:
                        pubsub.subscribe(channel)
                else:
                    pubsub.psubscribe(pattern)
            else:
                pubsub.subscribe(pattern)
            
            logger.info(f"Subscribed to {pattern} (Pub/Sub fallback)")
            
            # Message processing loop
            consecutive_errors = 0
            max_consecutive_errors = 5
            
            while consecutive_errors < max_consecutive_errors:
                try:
                    message = pubsub.get_message(timeout=1.0)
                    if message and message['type'] in ['message', 'pmessage']:
                        try:
                            # Parse message data
                            if isinstance(message['data'], str):
                                msg_data = json.loads(message['data'])
                            else:
                                msg_data = json.loads(message['data'])
                            
                            # Extract the actual data
                            if 'data' in msg_data:
                                actual_data = json.loads(msg_data['data'])
                            else:
                                actual_data = msg_data
                            
                            instance = model_class.model_validate(actual_data)
                            consecutive_errors = 0
                            self.messages_consumed += 1
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
            
            logger.error(f"Too many consecutive errors, stopping subscription to {pattern}")
                    
        except Exception as e:
            logger.error(f"Subscription error for {pattern}: {e}")
        finally:
            try:
                pubsub.close()
            except:
                pass
    
    # Replay and Recovery Methods (Streams only)
    async def replay_messages(self, stream_type: str, start_time: datetime, end_time: datetime = None) -> AsyncGenerator[Dict, None]:
        """Replay messages from stream within time range (Streams only)"""
        if not self.supports_streams:
            logger.warning("Message replay requires Redis Streams support")
            return
        
        stream_name = self.streams_config.streams.get(stream_type)
        if not stream_name:
            logger.error(f"Unknown stream type: {stream_type}")
            return
        
        # Convert timestamps to Redis stream IDs
        start_id = f"{int(start_time.timestamp() * 1000)}-0"
        end_id = f"{int(end_time.timestamp() * 1000)}-0" if end_time else "+"
        
        try:
            logger.info(f"Replaying messages from {stream_name} between {start_time} and {end_time}")
            
            messages = self.redis_client.xrange(stream_name, min=start_id, max=end_id)
            
            for msg_id, msg_data in messages:
                yield msg_data
                
        except Exception as e:
            logger.error(f"Error during message replay: {e}")
    
    # Health & Monitoring (Enhanced)
    def health_check(self) -> dict:
        """Enhanced health check with Streams information"""
        try:
            if not self.redis_client:
                return {"status": "disconnected", "error": "No Redis client"}
            
            start_time = datetime.utcnow()
            self.redis_client.ping()
            latency = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            health_info = {
                "status": "healthy",
                "latency_ms": round(latency, 2),
                "supports_streams": self.supports_streams,
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed
            }
            
            # Add Redis info if available
            try:
                info = self.redis_client.info()
                health_info.update({
                    "type": "Real Redis" if info else "FakeRedis",
                    "connected_clients": info.get('connected_clients', 1),
                    "used_memory": info.get('used_memory_human', 'unknown'),
                    "redis_version": info.get('redis_version', 'unknown')
                })
            except:
                health_info.update({
                    "type": "FakeRedis",
                    "connected_clients": 1,
                    "used_memory": 'fake',
                    "redis_version": 'fakeredis'
                })
            
            # Add Streams info if supported
            if self.supports_streams:
                health_info["stream_errors"] = self.stream_errors
                health_info["consumer_id"] = self.streams_config.consumer_id
            
            return health_info
            
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def get_stats(self) -> dict:
        """Get comprehensive message bus statistics"""
        try:
            base_stats = {
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed,
                "supports_streams": self.supports_streams,
                "mode": "streams" if self.supports_streams else "pubsub"
            }
            
            if self.supports_streams:
                base_stats["stream_errors"] = self.stream_errors
                base_stats["consumer_id"] = self.streams_config.consumer_id
                
                # Get stream lengths
                try:
                    stream_info = {}
                    for stream_type, stream_name in self.streams_config.streams.items():
                        try:
                            length = self.redis_client.xlen(stream_name)
                            stream_info[stream_type] = {"name": stream_name, "length": length}
                        except:
                            stream_info[stream_type] = {"name": stream_name, "length": 0}
                    
                    base_stats["streams"] = stream_info
                except:
                    pass
            
            return base_stats
            
        except Exception as e:
            return {"error": str(e)}

# Global message bus instance
message_bus = None

# Convenience functions
def connect_bus(redis_url: str = None) -> bool:
    """Connect to message bus with automatic fallback"""
    global message_bus
    
    if redis_url:
        # Create custom Redis client for specific URL
        try:
            import redis
            redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
            message_bus = MessageBus(redis_client)
        except:
            message_bus = MessageBus()  # Use default connection with fallback
    else:
        message_bus = MessageBus()
    
    return message_bus.connect()

def get_bus() -> MessageBus:
    """Get global message bus instance"""
    global message_bus
    if message_bus is None:
        message_bus = MessageBus()
    return message_bus