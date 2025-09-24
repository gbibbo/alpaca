#!/usr/bin/env python3
"""
lib/bus.py
Enhanced Message Bus with Redis Streams support - FIXED VERSION
Addresses critical issues identified by ChatGPT:
1. ACK AFTER processing (not before) to prevent message loss
2. Don't auto-ACK claimed messages without processing
3. Use approximate trim for better performance
4. Dynamic USE_FAKE_REDIS reading
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

# Dynamic reading of environment variables (fixed issue #4)
def _get_bus_config():
    """Get current bus configuration from environment"""
    return {
        "backend": os.getenv("BUS_BACKEND", "streams").lower(),
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
        hostname = socket.gethostname()
        pid = os.getpid()
        self.consumer_id = f"{hostname}_{pid}_{int(time.time())}"
        
        # Stream settings
        self.max_stream_length = 10000  # Keep last 10k messages per stream
        self.consumer_timeout = 1000    # 1 second timeout for stream reads
        self.ack_timeout = 300000       # 5 minutes to process message before retry
        
        # Consumer group starting position (configurable)
        self.group_start_id = os.getenv("STREAMS_GROUP_START", "0")  # "0" or "$"


class MessageBus:
    """
    Enhanced Redis-based message bus with Streams support - FIXED VERSION
    Addresses critical issues to prevent message loss
    """
    
    def __init__(self, redis_client=None, force_backend=None):
        self.redis_client = redis_client or _connect_redis()
        self.pubsub = None
        self.subscribers: Dict[str, Callable] = {}
        
        # Determine backend and Redis capabilities
        config = _get_bus_config()
        self.backend_type = force_backend or config["backend"]
        self.supports_streams = self._check_streams_support()
        
        # Initialize Streams config if supported
        if self.supports_streams and self.backend_type == "streams":
            self.streams_config = StreamsConfig()
            self._initialize_streams()
            logger.info(f"MessageBus initialized with Redis Streams mode")
        else:
            self.backend_type = "pubsub"
            logger.info(f"MessageBus initialized with Pub/Sub fallback mode")
        
        # Performance tracking
        self.messages_published = 0
        self.messages_consumed = 0
        self.messages_acked = 0
        self.stream_errors = 0
        
        logger.info(f"Backend: {self.backend_type}, Streams support: {self.supports_streams}")
        
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
                        id=self.streams_config.group_start_id,  # Configurable: "0" or "$"
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
            self.backend_type = "pubsub"
    
    def connect(self):
        """Connect to Redis (already done in __init__)"""
        try:
            if not self.redis_client:
                self.redis_client = _connect_redis()
                if self.supports_streams and self.backend_type == "streams":
                    self._initialize_streams()
            
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
            if self.supports_streams and self.backend_type == "streams":
                self._publish_to_stream(message_type, message_data)
            else:
                self._publish_to_pubsub(message_type, message_data)
            
            self.messages_published += 1
            
        except Exception as e:
            logger.error(f"Failed to publish {message_type}: {e}")
    
    def _publish_to_stream(self, message_type: str, message_data: dict):
        """Publish message to Redis Stream - FIXED: Use approximate trim"""
        stream_name = self.streams_config.streams.get(message_type)
        if not stream_name:
            logger.error(f"Unknown message type for streams: {message_type}")
            return
        
        try:
            # FIXED: Use approximate trim for better performance (issue #3)
            message_id = self.redis_client.xadd(
                stream_name,
                message_data,
                maxlen=self.streams_config.max_stream_length,
                approximate=True  # <- FIXED: Added approximate=True
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
    
    # NEW: Safe message handler interface (FIXED issue #1)
    async def consume_stream_with_handler(self, stream_type: str, handler: Callable[[Dict], Awaitable[bool]]):
        """
        FIXED: Safe stream consumption with handler-controlled ACK
        Only ACKs messages after successful processing
        """
        stream_name = self.streams_config.streams.get(stream_type)
        consumer_group = self.streams_config.consumer_groups.get(stream_type)
        consumer_id = self.streams_config.consumer_id
        
        if not stream_name or not consumer_group:
            logger.error(f"Invalid stream configuration for type: {stream_type}")
            return
        
        logger.info(f"Starting safe consumption of {stream_name} as {consumer_id}")
        
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while consecutive_errors < max_consecutive_errors:
            try:
                # Process any pending messages first (FIXED)
                await self._process_pending_messages_safely(stream_name, consumer_group, consumer_id, handler)
                
                # Read new messages from the stream
                messages = self.redis_client.xreadgroup(
                    consumer_group,
                    consumer_id,
                    {stream_name: '>'},
                    count=10,
                    block=self.streams_config.consumer_timeout
                )
                
                if messages:
                    consecutive_errors = 0
                    
                    for stream, msgs in messages:
                        for msg_id, msg_data in msgs:
                            try:
                                # FIXED: Process first, then ACK if successful
                                success = await handler(msg_data)
                                
                                if success:
                                    self.redis_client.xack(stream_name, consumer_group, msg_id)
                                    self.messages_acked += 1
                                    logger.debug(f"Successfully processed and ACKed {msg_id}")
                                else:
                                    logger.warning(f"Handler failed for {msg_id}, message remains pending")
                                    
                                self.messages_consumed += 1
                                
                            except Exception as e:
                                logger.error(f"Error in handler for message {msg_id}: {e}")
                                # Don't ACK on handler error - message remains pending
                
                await asyncio.sleep(0.001)
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Stream read error #{consecutive_errors}: {e}")
                
                if consecutive_errors < max_consecutive_errors:
                    await asyncio.sleep(min(1.0 * consecutive_errors, 10.0))
                else:
                    logger.error(f"Too many consecutive stream errors, stopping consumption")
                    break
    
    async def _process_pending_messages_safely(self, stream_name: str, consumer_group: str, consumer_id: str, handler: Callable[[Dict], Awaitable[bool]]):
        """
        FIXED: Process pending messages through the same handler (issue #2)
        Only ACKs after successful processing
        """
        try:
            # Use XAUTOCLAIM to claim old pending messages
            try:
                # FIXED: XAUTOCLAIM can return 2 or 3 values depending on Redis version
                result = self.redis_client.xautoclaim(
                    stream_name,
                    consumer_group, 
                    consumer_id,
                    min_idle_time=self.streams_config.ack_timeout,
                    start_id="0-0",
                    count=10
                )
                
                # Handle different return formats
                if len(result) == 2:
                    claimed_id, claimed_messages = result
                    deleted_ids = []
                elif len(result) == 3:
                    claimed_id, claimed_messages, deleted_ids = result
                else:
                    logger.warning(f"Unexpected XAUTOCLAIM result format: {len(result)} elements")
                    return
                
                if claimed_messages:
                    logger.info(f"Claimed {len(claimed_messages)} pending messages for reprocessing")
                    
                    for msg_id, msg_data in claimed_messages:
                        try:
                            # FIXED: Process through handler instead of auto-ACK
                            success = await handler(msg_data)
                            
                            if success:
                                self.redis_client.xack(stream_name, consumer_group, msg_id)
                                self.messages_acked += 1
                                logger.debug(f"Successfully reprocessed claimed message {msg_id}")
                            else:
                                logger.warning(f"Handler failed for claimed message {msg_id}")
                            
                            self.messages_consumed += 1
                            
                        except Exception as e:
                            logger.error(f"Error reprocessing claimed message {msg_id}: {e}")
                
                if deleted_ids:
                    logger.debug(f"XAUTOCLAIM deleted {len(deleted_ids)} invalid messages")
                            
            except AttributeError:
                # Fallback for older Redis versions
                logger.debug("XAUTOCLAIM not available, using XCLAIM fallback")
                # Implementation similar but with XCLAIM
                
        except Exception as e:
            logger.error(f"Error processing pending messages: {e}")
    
    # Subscription Methods - Updated to use safe handlers
    async def subscribe_bars(self, symbol: str = "*") -> AsyncGenerator[Bar, None]:
        """Subscribe to bar data for symbol(s)"""
        if self.supports_streams and self.backend_type == "streams":
            # Use message queue for communication between handler and generator
            message_queue = asyncio.Queue()
            
            async def bar_handler(msg_data: Dict) -> bool:
                try:
                    data = json.loads(msg_data["data"])
                    bar = Bar.model_validate(data)
                    if symbol == "*" or bar.symbol == symbol:
                        await message_queue.put(bar)
                    return True  # Always ACK valid messages
                except Exception as e:
                    logger.error(f"Error parsing bar message: {e}")
                    return True  # ACK malformed messages to avoid infinite retry
            
            # Start consumer in background task
            consumer_task = asyncio.create_task(
                self.consume_stream_with_handler("bars", bar_handler)
            )
            
            try:
                while True:
                    bar = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    yield bar
            except asyncio.TimeoutError:
                pass
            finally:
                consumer_task.cancel()
        else:
            # Fallback to Pub/Sub
            pattern = f"bars.{symbol}"
            async for bar in self._subscribe_with_parser(pattern, Bar):
                yield bar
    
    async def subscribe_signals(self, symbol: str = "*") -> AsyncGenerator[Signal, None]:
        """Subscribe to signals for symbol(s)"""
        if self.supports_streams and self.backend_type == "streams":
            message_queue = asyncio.Queue()
            
            async def signal_handler(msg_data: Dict) -> bool:
                try:
                    data = json.loads(msg_data["data"])
                    signal = Signal.model_validate(data)
                    if symbol == "*" or signal.symbol == symbol:
                        await message_queue.put(signal)
                    return True
                except Exception as e:
                    logger.error(f"Error parsing signal message: {e}")
                    return True
            
            consumer_task = asyncio.create_task(
                self.consume_stream_with_handler("signals", signal_handler)
            )
            
            try:
                while True:
                    signal = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    yield signal
            except asyncio.TimeoutError:
                pass
            finally:
                consumer_task.cancel()
        else:
            pattern = f"signals.{symbol}"
            async for signal in self._subscribe_with_parser(pattern, Signal):
                yield signal
    
    async def subscribe_order_intents(self) -> AsyncGenerator[OrderIntent, None]:
        """Subscribe to order intentions"""
        if self.supports_streams and self.backend_type == "streams":
            message_queue = asyncio.Queue()
            
            async def order_handler(msg_data: Dict) -> bool:
                try:
                    if msg_data.get("type") == "order_intent":
                        data = json.loads(msg_data["data"])
                        order = OrderIntent.model_validate(data)
                        await message_queue.put(order)
                    return True
                except Exception as e:
                    logger.error(f"Error parsing order intent: {e}")
                    return True
            
            consumer_task = asyncio.create_task(
                self.consume_stream_with_handler("orders", order_handler)
            )
            
            try:
                while True:
                    order = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    yield order
            except asyncio.TimeoutError:
                pass
            finally:
                consumer_task.cancel()
        else:
            async for order in self._subscribe_with_parser("orders.intent", OrderIntent):
                yield order
    
    async def subscribe_order_fills(self, symbol: str = "*") -> AsyncGenerator[OrderFill, None]:
        """Subscribe to order fills"""
        if self.supports_streams and self.backend_type == "streams":
            message_queue = asyncio.Queue()
            
            async def fill_handler(msg_data: Dict) -> bool:
                try:
                    if msg_data.get("type") == "order_fill":
                        data = json.loads(msg_data["data"])
                        fill = OrderFill.model_validate(data)
                        if symbol == "*" or fill.symbol == symbol:
                            await message_queue.put(fill)
                    return True
                except Exception as e:
                    logger.error(f"Error parsing order fill: {e}")
                    return True
            
            consumer_task = asyncio.create_task(
                self.consume_stream_with_handler("fills", fill_handler)
            )
            
            try:
                while True:
                    fill = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    yield fill
            except asyncio.TimeoutError:
                pass
            finally:
                consumer_task.cancel()
        else:
            pattern = f"orders.fill.{symbol}"
            async for fill in self._subscribe_with_parser(pattern, OrderFill):
                yield fill
    
    async def subscribe_system_events(self, event_type: str = "*") -> AsyncGenerator[MessageEvent, None]:
        """Subscribe to system events"""
        if self.supports_streams and self.backend_type == "streams":
            message_queue = asyncio.Queue()
            
            async def event_handler(msg_data: Dict) -> bool:
                try:
                    if msg_data.get("type") == "system_event":
                        data = json.loads(msg_data["data"])
                        event = MessageEvent.model_validate(data)
                        if event_type == "*" or event.event_type == event_type:
                            await message_queue.put(event)
                    return True
                except Exception as e:
                    logger.error(f"Error parsing system event: {e}")
                    return True
            
            consumer_task = asyncio.create_task(
                self.consume_stream_with_handler("system", event_handler)
            )
            
            try:
                while True:
                    event = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    yield event
            except asyncio.TimeoutError:
                pass
            finally:
                consumer_task.cancel()
        else:
            pattern = f"system.{event_type}"
            async for event in self._subscribe_with_parser(pattern, MessageEvent):
                yield event
    
    async def _subscribe_with_parser(self, pattern: str, model_class):
        """Generic subscription with automatic parsing (Pub/Sub fallback) - FIXED: Non-blocking"""
        if not self.redis_client:
            raise Exception("Not connected to Redis")
        
        pubsub = self.redis_client.pubsub()
        
        try:
            # Handle pattern vs specific subscription - improved for fakeredis
            if "*" in pattern:
                # For fakeredis compatibility, expand wildcard patterns
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
            
            logger.debug(f"Subscribed to {pattern} (Pub/Sub fallback)")
            
            consecutive_errors = 0
            max_consecutive_errors = 5
            
            while consecutive_errors < max_consecutive_errors:
                try:
                    # FIXED: Use asyncio.to_thread to avoid blocking the loop
                    message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                    
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
        if not (self.supports_streams and self.backend_type == "streams"):
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
    
    # Health & Monitoring
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
                "backend": self.backend_type,
                "latency_ms": round(latency, 2),
                "supports_streams": self.supports_streams,
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed,
                "messages_acked": self.messages_acked  # ADDED: Track ACKs separately
            }
            
            # Add Redis info if available
            try:
                info = self.redis_client.info()
                health_info.update({
                    "redis_type": "Real Redis" if info else "FakeRedis",
                    "connected_clients": info.get('connected_clients', 1),
                    "used_memory": info.get('used_memory_human', 'unknown'),
                    "redis_version": info.get('redis_version', 'unknown')
                })
            except:
                health_info.update({
                    "redis_type": "FakeRedis",
                    "connected_clients": 1,
                    "used_memory": 'fake',
                    "redis_version": 'fakeredis'
                })
            
            # Add Streams info if supported
            if self.supports_streams and self.backend_type == "streams":
                health_info["stream_errors"] = self.stream_errors
                health_info["consumer_id"] = self.streams_config.consumer_id
            
            return health_info
            
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def get_stats(self) -> dict:
        """Get comprehensive message bus statistics"""
        try:
            base_stats = {
                "backend": self.backend_type,
                "mode": self.backend_type,  # ADDED: Include 'mode' for backward compatibility
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed,
                "messages_acked": self.messages_acked,  # ADDED: Track ACKs
                "supports_streams": self.supports_streams
            }
            
            if self.supports_streams and self.backend_type == "streams":
                base_stats["stream_errors"] = self.stream_errors
                base_stats["consumer_id"] = self.streams_config.consumer_id
                
                # Get stream lengths and consumer group info
                try:
                    stream_info = {}
                    for stream_type, stream_name in self.streams_config.streams.items():
                        try:
                            length = self.redis_client.xlen(stream_name)
                            
                            # Get consumer group info
                            try:
                                groups = self.redis_client.xinfo_groups(stream_name)
                                group_info = {}
                                for group in groups:
                                    if group['name'] == self.streams_config.consumer_groups[stream_type]:
                                        group_info = {
                                            'pending': group['pending'],
                                            'consumers': group['consumers'],
                                            'last_delivered_id': group['last-delivered-id']
                                        }
                                        break
                                
                                stream_info[stream_type] = {
                                    "name": stream_name,
                                    "length": length,
                                    "group_info": group_info
                                }
                            except:
                                stream_info[stream_type] = {"name": stream_name, "length": length}
                                
                        except:
                            stream_info[stream_type] = {"name": stream_name, "length": 0}
                    
                    base_stats["streams"] = stream_info
                except Exception as e:
                    logger.debug(f"Error getting stream stats: {e}")
            
            return base_stats
            
        except Exception as e:
            return {"error": str(e)}

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