#!/usr/bin/env python3
"""
lib/bus_streams.py
Redis Streams Backend Implementation - Dedicated Module
Following ChatGPT's architectural recommendations for separation of concerns
"""

import json
import asyncio
import logging
import os
import time
import socket
from typing import Any, Callable, Dict, Optional, List, Tuple, Awaitable, AsyncGenerator
from datetime import datetime, timezone
from lib.models import Bar, Signal, OrderIntent, OrderFill, MessageEvent
from lib.time_utils import TimeUtils

logger = logging.getLogger(__name__)


class RedisStreamsBus:
    """
    Dedicated Redis Streams implementation with at-least-once delivery and replay capabilities
    Implements ChatGPT's recommended patterns for reliable message processing
    """

    def __init__(self, redis_url: str = None, db: int = 0, group_start_id="0"):
        import redis

        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
        self.db = db
        self.redis_client = None
        self.group_start_id = group_start_id
        self._connected = False

        # Stream names for different message types
        self.streams = {
            "bars": "bars",
            "signals": "signals",
            "orders": "orders.intent",  # Use orders.intent for compatibility
            "fills": "orders.fill",
            "system": "system"
        }

        # Consumer group names
        self.consumer_groups = {
            "bars": "bars_processors",
            "signals": "signal_processors",
            "orders": "order_processors",
            "fills": "fill_processors",
            "system": "system_processors"
        }

        # Generate unique consumer ID
        hostname = socket.gethostname()
        pid = os.getpid()
        self.consumer_id = f"{hostname}_{pid}_{int(time.time())}"

        # Configuration
        self.max_stream_length = 10000
        self.consumer_timeout = 2000  # 2 seconds
        self.ack_timeout = 300000     # 5 minutes

        # Performance metrics
        self.messages_published = 0
        self.messages_consumed = 0
        self.messages_acked = 0
        self.stream_errors = 0

        logger.info(f"RedisStreamsBus initialized with consumer_id: {self.consumer_id}")

    def connect(self) -> bool:
        """Connect to Redis and initialize streams"""
        try:
            import redis
            self.redis_client = redis.from_url(self.redis_url, db=self.db, decode_responses=True)

            # Test connection
            if not self.redis_client.ping():
                return False

            # Initialize streams and consumer groups
            self._initialize_streams()
            self._connected = True
            logger.info("RedisStreamsBus connected successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect from Redis"""
        try:
            if self.redis_client:
                self.redis_client.close()
                self.redis_client = None
            self._connected = False
            logger.info("RedisStreamsBus disconnected")
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    def _initialize_streams(self):
        """Initialize Redis Streams and consumer groups"""
        if not self.redis_client:
            raise RuntimeError("Redis client not connected")

        try:
            for stream_type, stream_name in self.streams.items():
                consumer_group = self.consumer_groups[stream_type]

                try:
                    # Create consumer group (will fail if already exists)
                    self.redis_client.xgroup_create(
                        stream_name,
                        consumer_group,
                        id=self.group_start_id,
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
            raise

    def publish(self, stream_type: str, payload: dict) -> str:
        """
        Publish message to Redis Stream with automatic trimming
        Returns message ID
        """
        stream_name = self.streams.get(stream_type)
        if not stream_name:
            raise ValueError(f"Unknown stream type: {stream_type}")

        try:
            # Add timestamp if not present
            if "timestamp" not in payload:
                payload["timestamp"] = TimeUtils.utc_now().isoformat()

            # Publish with approximate trim for performance
            message_id = self.redis_client.xadd(
                stream_name,
                payload,
                maxlen=self.max_stream_length,
                approximate=True
            )

            self.messages_published += 1
            logger.debug(f"Published to {stream_name}: {message_id}")
            return message_id

        except Exception as e:
            self.stream_errors += 1
            logger.error(f"Error publishing to {stream_name}: {e}")
            raise

    def consume(self, stream_type: str, count: int = 10, block_ms: int = None) -> List[Tuple[str, dict]]:
        """
        Consume messages from stream (new messages only)
        Returns list of (message_id, message_data) tuples
        """
        stream_name = self.streams.get(stream_type)
        consumer_group = self.consumer_groups.get(stream_type)

        if not stream_name or not consumer_group:
            raise ValueError(f"Invalid stream configuration for: {stream_type}")

        try:
            block_time = block_ms or self.consumer_timeout

            messages = self.redis_client.xreadgroup(
                consumer_group,
                self.consumer_id,
                {stream_name: '>'},
                count=count,
                block=block_time
            )

            result = []
            if messages:
                for stream, msgs in messages:
                    for msg_id, msg_data in msgs:
                        result.append((msg_id, msg_data))
                        self.messages_consumed += 1

            return result

        except Exception as e:
            self.stream_errors += 1
            logger.error(f"Error consuming from {stream_name}: {e}")
            return []

    def ack(self, stream_type: str, message_id: str) -> bool:
        """
        Acknowledge message processing
        Should only be called after successful processing
        """
        stream_name = self.streams.get(stream_type)
        consumer_group = self.consumer_groups.get(stream_type)

        if not stream_name or not consumer_group:
            raise ValueError(f"Invalid stream configuration for: {stream_type}")

        try:
            result = self.redis_client.xack(stream_name, consumer_group, message_id)
            if result:
                self.messages_acked += 1
                logger.debug(f"ACKed message {message_id} in {stream_name}")
            return bool(result)

        except Exception as e:
            logger.error(f"Error ACKing message {message_id} in {stream_name}: {e}")
            return False

    def consume_pending(self, stream_type: str, min_idle_ms: int = None) -> List[Tuple[str, dict]]:
        """
        Consume pending messages that have been claimed but not acknowledged
        Used for recovering from failures
        """
        stream_name = self.streams.get(stream_type)
        consumer_group = self.consumer_groups.get(stream_type)

        if not stream_name or not consumer_group:
            raise ValueError(f"Invalid stream configuration for: {stream_type}")

        try:
            min_idle = min_idle_ms or self.ack_timeout

            # Check Redis version for XAUTOCLAIM support
            if not hasattr(self, '_supports_xautoclaim'):
                try:
                    info = self.redis_client.info()
                    ver = info.get("redis_version", "0.0.0")
                    maj, minr, *_ = [int(p) for p in ver.split(".")]
                    self._supports_xautoclaim = (maj > 6) or (maj == 6 and minr >= 2)
                    logger.info(f"Redis {ver} - XAUTOCLAIM support: {self._supports_xautoclaim}")
                except Exception:
                    self._supports_xautoclaim = False

            if not self._supports_xautoclaim:
                # For Redis < 6.2, implement manual reclaim with XPENDING + XCLAIM
                logger.debug(f"Using manual reclaim for {stream_name} (Redis < 6.2)")
                try:
                    # Get pending messages
                    pending_info = self.redis_client.xpending_range(
                        stream_name, consumer_group,
                        min='-', max='+', count=10
                    )

                    if not pending_info:
                        return []

                    # Filter by idle time
                    old_messages = []
                    for msg_info in pending_info:
                        if msg_info.get('time_since_delivered', 0) > min_idle:
                            old_messages.append(msg_info['message_id'])

                    if not old_messages:
                        return []

                    # Claim old messages to current consumer
                    claimed = self.redis_client.xclaim(
                        stream_name, consumer_group, self.consumer_id,
                        min_idle_time=min_idle, message_ids=old_messages
                    )

                    logger.info(f"Manually reclaimed {len(claimed)} pending messages from {stream_name}")
                    return [(msg_id, msg_data) for msg_id, msg_data in claimed]

                except Exception as e:
                    logger.error(f"Error in manual reclaim for {stream_name}: {e}")
                    return []

            # Use XAUTOCLAIM to reclaim old pending messages
            result = self.redis_client.xautoclaim(
                stream_name,
                consumer_group,
                self.consumer_id,
                min_idle_time=min_idle,
                start_id="0-0",
                count=10
            )

            # Handle different return formats based on Redis version
            if len(result) >= 2:
                claimed_messages = result[1]

                if claimed_messages:
                    logger.info(f"Reclaimed {len(claimed_messages)} pending messages from {stream_name}")
                    return [(msg_id, msg_data) for msg_id, msg_data in claimed_messages]

            return []

        except Exception as e:
            if "unknown command `XAUTOCLAIM`" in str(e):
                self._supports_xautoclaim = False
                logger.warning(f"XAUTOCLAIM not supported, disabling pending recovery for {stream_name}")
                return []
            logger.error(f"Error consuming pending from {stream_name}: {e}")
            return []

    async def consume_with_handler(self, stream_type: str, handler: Callable[[dict], Awaitable[bool]]) -> None:
        """
        Safe consumption loop with handler-controlled ACK
        Only ACKs messages after successful processing
        """
        logger.info(f"Starting safe consumption of {stream_type} stream")

        consecutive_errors = 0
        max_consecutive_errors = 5

        while consecutive_errors < max_consecutive_errors:
            try:
                # First, process any pending messages
                pending_messages = self.consume_pending(stream_type)
                for msg_id, msg_data in pending_messages:
                    try:
                        success = await handler(msg_data)
                        if success:
                            self.ack(stream_type, msg_id)
                    except Exception as e:
                        logger.error(f"Error processing pending message {msg_id}: {e}")

                # Then consume new messages
                new_messages = self.consume(stream_type, count=10)

                if new_messages:
                    consecutive_errors = 0

                    for msg_id, msg_data in new_messages:
                        try:
                            success = await handler(msg_data)
                            if success:
                                self.ack(stream_type, msg_id)
                            else:
                                logger.warning(f"Handler failed for {msg_id}, message remains pending")
                        except Exception as e:
                            logger.error(f"Error in handler for message {msg_id}: {e}")

                await asyncio.sleep(0.001)  # Small yield

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Stream consumption error #{consecutive_errors}: {e}")

                if consecutive_errors < max_consecutive_errors:
                    await asyncio.sleep(min(1.0 * consecutive_errors, 10.0))
                else:
                    logger.error(f"Too many consecutive errors, stopping consumption of {stream_type}")
                    break

    async def _ensure_group(self, stream: str, group: str) -> None:
        """Ensure a consumer group exists for a stream"""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.redis_client.xgroup_create(stream, group, id="$", mkstream=True)
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.debug(f"Error ensuring group {group} for stream {stream}: {e}")

    def _b2s(self, x):
        """Convert bytes to string if needed"""
        return x.decode() if isinstance(x, (bytes, bytearray)) else x

    def _parse_system_event(self, msg_id, fields: Dict[Any, Any]) -> MessageEvent:
        """Parse system event from Redis stream message"""
        f = {self._b2s(k): self._b2s(v) for k, v in dict(fields).items()}

        # Try to parse data field as JSON
        payload = {}
        try:
            data_str = f.get("data", "{}")
            payload = json.loads(data_str) if data_str != "{}" else {}
        except Exception:
            payload = {}

        # Extract event type (check field-level first, then payload)
        evt_type = f.get("event_type") or payload.get("event_type") or "unknown"

        # Extract source (check field-level first, then payload)
        source = f.get("source") or payload.get("source") or "unknown"

        # Extract timestamp (check field-level first, then payload)
        ts = f.get("timestamp") or payload.get("timestamp")

        return MessageEvent(
            id=str(msg_id),
            event_type=evt_type,
            source=source,
            data=payload,
            timestamp=TimeUtils.parse_timestamp(ts)
        )

    async def _reclaim_pending_system(self, min_idle_ms: int = 60_000, max_claim: int = 100) -> None:
        """Reclaim pending system events using Redis 6.0 compatible method"""
        stream_name = self.streams["system"]
        group_name = self.consumer_groups["system"]

        try:
            # Redis 6.0: use XPENDING + XCLAIM
            pending_info = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.redis_client.xpending_range(
                    stream_name, group_name, min="-", max="+", count=max_claim
                )
            )

            if not pending_info:
                return

            # Filter by idle time and claim old messages
            old_message_ids = []
            for msg_info in pending_info:
                # Handle both namedtuple and dict formats from redis-py
                if hasattr(msg_info, 'time_since_delivered'):
                    idle_time = msg_info.time_since_delivered
                    msg_id = msg_info.message_id
                elif isinstance(msg_info, dict):
                    idle_time = msg_info.get('time_since_delivered', 0)
                    msg_id = msg_info.get('message_id')
                else:
                    continue

                if idle_time and idle_time >= min_idle_ms and msg_id:
                    old_message_ids.append(msg_id)

            if old_message_ids:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.redis_client.xclaim(
                        stream_name, group_name, self.consumer_id,
                        min_idle_time=min_idle_ms, message_ids=old_message_ids
                    )
                )
                logger.info(f"Reclaimed {len(old_message_ids)} pending system events")

        except Exception as e:
            logger.debug(f"Error reclaiming pending system events: {e}")

    async def subscribe_system_events(self, event_type: str = "*") -> AsyncGenerator[MessageEvent, None]:
        """
        Subscribe to system events using Redis Streams with consumer groups.
        Implements safe ACK pattern: ACK previous message when yielding next one.
        """
        stream_name = self.streams["system"]
        group_name = self.consumer_groups["system"]

        # Ensure consumer group exists
        await self._ensure_group(stream_name, group_name)

        # Reclaim any pending messages
        await self._reclaim_pending_system()

        last_to_ack = None

        try:
            while True:
                # Read from the stream using consumer group
                messages = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.redis_client.xreadgroup(
                        groupname=group_name,
                        consumername=self.consumer_id,
                        streams={stream_name: ">"},
                        count=16,
                        block=1000  # 1 second timeout
                    )
                )

                if not messages:
                    continue

                # Process messages: List[(stream, [(id, {fields}), ...])]
                for _stream, entries in messages:
                    for msg_id, fields in entries:
                        try:
                            # Parse the system event
                            evt = self._parse_system_event(msg_id, fields)

                            # Filter by event type if specified
                            if event_type != "*" and evt.event_type != event_type:
                                # ACK filtered events immediately
                                await asyncio.get_event_loop().run_in_executor(
                                    None,
                                    lambda: self.redis_client.xack(stream_name, group_name, msg_id)
                                )
                                continue

                            # Safe ACK pattern: ACK the previous message before yielding new one
                            if last_to_ack:
                                await asyncio.get_event_loop().run_in_executor(
                                    None,
                                    lambda: self.redis_client.xack(stream_name, group_name, last_to_ack)
                                )

                            last_to_ack = msg_id
                            yield evt

                        except Exception as e:
                            logger.error(f"Error processing system event {msg_id}: {e}")
                            # ACK poison pill messages to avoid loops
                            await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: self.redis_client.xack(stream_name, group_name, msg_id)
                            )

        except asyncio.CancelledError:
            logger.info("System events subscription cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in system events subscription: {e}")
            raise
        finally:
            # Best effort ACK of the last message
            if last_to_ack:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.redis_client.xack(stream_name, group_name, last_to_ack)
                    )
                except Exception as e:
                    logger.debug(f"Error in final ACK: {e}")

    # High-level publishing methods
    def publish_bar(self, bar: Bar) -> str:
        """Publish market bar data"""
        payload = {
            "type": "bar",
            "data": bar.model_dump_json(),
            "symbol": bar.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        return self.publish("bars", payload)

    def publish_signal(self, signal: Signal) -> str:
        """Publish trading signal"""
        payload = {
            "type": "signal",
            "data": signal.model_dump_json(),
            "symbol": signal.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        return self.publish("signals", payload)

    def publish_order_intent(self, order: OrderIntent) -> str:
        """Publish order intention"""
        payload = {
            "type": "order_intent",
            "data": order.model_dump_json(),
            "symbol": order.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        return self.publish("orders", payload)

    def publish_order_fill(self, fill: OrderFill) -> str:
        """Publish order execution result"""
        payload = {
            "type": "order_fill",
            "data": fill.model_dump_json(),
            "symbol": fill.symbol,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        return self.publish("fills", payload)

    def publish_system_event(self, event_type: str, source: str, data: dict) -> str:
        """Publish system event"""
        event = MessageEvent(
            event_type=event_type,
            source=source,
            data=data
        )
        payload = {
            "type": "system_event",
            "data": event.model_dump_json(),
            "event_type": event_type,
            "source": source,
            "timestamp": TimeUtils.utc_now().isoformat()
        }
        return self.publish("system", payload)

    # Statistics and monitoring
    def get_stats(self) -> dict:
        """Get comprehensive statistics"""
        try:
            base_stats = {
                "backend": "streams",
                "supports_streams": True,
                "consumer_id": self.consumer_id,
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed,
                "messages_acked": self.messages_acked,
                "stream_errors": self.stream_errors,
                "ack_rate": self.messages_acked / max(1, self.messages_consumed)
            }

            # Get stream-specific stats
            stream_info = {}
            for stream_type, stream_name in self.streams.items():
                try:
                    length = self.redis_client.xlen(stream_name)

                    # Get consumer group info
                    group_info = {}
                    try:
                        groups = self.redis_client.xinfo_groups(stream_name)
                        for group in groups:
                            if group['name'] == self.consumer_groups[stream_type]:
                                group_info = {
                                    'pending': group['pending'],
                                    'consumers': group['consumers'],
                                    'last_delivered_id': group['last-delivered-id']
                                }
                                break
                    except:
                        pass

                    stream_info[stream_type] = {
                        "name": stream_name,
                        "length": length,
                        "group_info": group_info
                    }
                except:
                    stream_info[stream_type] = {"name": stream_name, "length": 0}

            base_stats["streams"] = stream_info
            return base_stats

        except Exception as e:
            return {"error": str(e)}

    def health_check(self) -> dict:
        """Health check for streams backend"""
        try:
            if not self.redis_client:
                return {"status": "not_connected", "backend": "streams"}

            # Test Redis connection
            start_time = time.time()
            self.redis_client.ping()
            latency = (time.time() - start_time) * 1000

            return {
                "status": "healthy",
                "backend": "streams",
                "latency_ms": round(latency, 2),
                "consumer_id": self.consumer_id,
                "messages_published": self.messages_published,
                "messages_consumed": self.messages_consumed,
                "messages_acked": self.messages_acked,
                "stream_errors": self.stream_errors
            }

        except Exception as e:
            return {"status": "error", "backend": "streams", "error": str(e)}