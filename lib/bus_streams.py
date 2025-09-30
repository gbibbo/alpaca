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
import random
import re
from typing import Any, Callable, Dict, Optional, List, Tuple, Awaitable, AsyncGenerator, Iterator
from datetime import datetime, timezone
from lib.models import Bar, Signal, OrderIntent, OrderFill, MessageEvent
from lib.time_utils import TimeUtils
from lib.metrics_helpers import BusMetrics

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

        # Generate unique consumer ID with microsecond precision
        hostname = socket.gethostname()
        pid = os.getpid()
        import uuid
        self.consumer_id = f"{hostname}_{pid}_{int(time.time() * 1000000)}_{uuid.uuid4().hex[:8]}"

        # Configuration
        self.max_stream_length = 10000
        self.consumer_timeout = 2000  # 2 seconds
        self.ack_timeout = 300000     # 5 minutes

        # Reclaim configuration
        self.reclaim_batch_size = 200
        self.reclaim_max_iterations = 10
        self.production_min_idle_ms = 10000  # 10 seconds for production

        # Reclaim scheduling
        self.last_reclaim_time = {}  # Per stream type
        self.reclaim_interval = 10  # Reclaim every 10 seconds

        # Performance metrics
        self.messages_published = 0
        self.messages_consumed = 0
        self.messages_acked = 0
        self.stream_errors = 0
        self.redeliveries_total = 0
        self.reclaim_loops_total = 0
        self.reclaim_no_progress_total = 0
        self.batch_acks_total = 0

        logger.info(f"RedisStreamsBus initialized with consumer_id: {self.consumer_id}")

    def _validate_stream_id(self, stream_id: str) -> bool:
        """Validate Redis stream ID format (timestamp-sequence)"""
        if not isinstance(stream_id, str):
            return False
        return bool(re.match(r'^\d+-\d+$', stream_id))

    def _normalize_ids(self, ids: List[Any]) -> List[str]:
        """Normalize message IDs to string list, filtering invalid ones"""
        normalized = []
        for msg_id in ids:
            if isinstance(msg_id, (tuple, list)):
                # Extract ID from tuple/list (msg_id, data)
                id_str = str(msg_id[0]) if msg_id else ""
            else:
                id_str = str(msg_id)

            if self._validate_stream_id(id_str):
                normalized.append(id_str)
            else:
                logger.warning(f"Invalid stream ID format: {id_str}")

        return normalized

    def _get_min_idle_with_jitter(self, base_min_idle_ms: int) -> int:
        """Get min_idle_ms with jitter to avoid thundering herd"""
        if base_min_idle_ms <= 0:
            return 0

        # Add ±10% jitter
        jitter = random.uniform(0.9, 1.1)
        return int(base_min_idle_ms * jitter)

    def _should_reclaim_now(self, stream_type: str) -> bool:
        """Check if it's time to run reclaim for this stream type"""
        now = time.time()
        last_reclaim = self.last_reclaim_time.get(stream_type, 0)
        return (now - last_reclaim) >= self.reclaim_interval

    def _mark_reclaim_time(self, stream_type: str):
        """Mark the last reclaim time for this stream type"""
        self.last_reclaim_time[stream_type] = time.time()

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

            # Record metrics
            BusMetrics.message_published(stream_name, payload.get('type', 'unknown'), 'streams_bus')

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

                        # Record metrics
                        BusMetrics.message_consumed(stream_name, msg_data.get('type', 'unknown'), 'streams_consumer')

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
        return self.ack_many(stream_type, [message_id]) > 0

    def ack_many(self, stream_type: str, message_ids: List[str]) -> int:
        """
        Acknowledge multiple messages in batch (reduces RTT)
        Returns number of successfully ACKed messages
        """
        stream_name = self.streams.get(stream_type)
        consumer_group = self.consumer_groups.get(stream_type)

        if not stream_name or not consumer_group:
            raise ValueError(f"Invalid stream configuration for: {stream_type}")

        # Normalize and validate IDs
        valid_ids = self._normalize_ids(message_ids)
        if not valid_ids:
            logger.warning(f"No valid message IDs to ACK for {stream_type}")
            return 0

        try:
            # Use XACK with multiple IDs: XACK stream group id1 id2 id3...
            result = self.redis_client.xack(stream_name, consumer_group, *valid_ids)

            if result > 0:
                self.messages_acked += result
                self.batch_acks_total += 1

                # Record metrics
                for _ in range(result):
                    BusMetrics.message_acked(stream_name, 'streams_consumer')

                logger.debug(f"Batch ACKed {result}/{len(valid_ids)} messages in {stream_name}")

            return result

        except Exception as e:
            logger.error(f"Error batch ACKing {len(valid_ids)} messages in {stream_name}: {e}")
            return 0

    def xautoclaim_compat(self, stream_name: str, consumer_group: str, min_idle_ms: int,
                          start_id: str = "0-0", count: int = 200) -> Tuple[str, List[str]]:
        """
        Compatible XAUTOCLAIM wrapper that normalizes different response formats
        Returns (next_start_id, claimed_ids)
        """
        try:
            # Note: JUSTID flag has compatibility issues with some Redis clients
            # Skip JUSTID optimization and use regular XAUTOCLAIM directly
            result = self.redis_client.xautoclaim(
                stream_name, consumer_group, self.consumer_id,
                min_idle_time=min_idle_ms, start_id=start_id, count=count
            )

            # Handle different return formats
            if len(result) >= 2:
                next_start = str(result[0])
                claimed_entries = result[1]

                # Extract IDs from entries
                claimed_ids = []
                if claimed_entries:
                    for entry in claimed_entries:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                            claimed_ids.append(str(entry[0]))

                return next_start, self._normalize_ids(claimed_ids)

            return "0-0", []

        except Exception as e:
            if "unknown command" in str(e).lower():
                raise  # Re-raise to trigger fallback
            logger.error(f"Error in XAUTOCLAIM: {e}")
            return "0-0", []

    def xpending_ids_paginated(self, stream_name: str, consumer_group: str,
                              min_idle_ms: int, batch_size: int = 100) -> Iterator[List[str]]:
        """
        Paginated XPENDING + filter by idle time
        Yields batches of message IDs ready for claiming
        """
        try:
            start = "-"
            iterations = 0
            max_iterations = self.reclaim_max_iterations

            while iterations < max_iterations:
                pending_info = self.redis_client.xpending_range(
                    stream_name, consumer_group, min=start, max="+", count=batch_size
                )

                if not pending_info:
                    break

                old_message_ids = []
                last_id = None

                for msg_info in pending_info:
                    # Handle both namedtuple and dict formats
                    if hasattr(msg_info, 'time_since_delivered'):
                        idle_time = msg_info.time_since_delivered
                        msg_id = msg_info.message_id
                    elif isinstance(msg_info, dict):
                        idle_time = msg_info.get('time_since_delivered', 0)
                        msg_id = msg_info.get('message_id')
                    else:
                        continue

                    last_id = msg_id
                    if idle_time and idle_time >= min_idle_ms and msg_id:
                        old_message_ids.append(str(msg_id))

                if old_message_ids:
                    yield self._normalize_ids(old_message_ids)

                # Set next start point
                if last_id:
                    start = last_id
                else:
                    break

                iterations += 1

        except Exception as e:
            logger.error(f"Error in paginated XPENDING: {e}")
            return

    def consume_pending(self, stream_type: str, min_idle_ms: int = None, count: int = 200) -> List[Tuple[str, dict]]:
        """
        Robust consume pending with XAUTOCLAIM and fallback to XPENDING+XCLAIM
        """
        stream_name = self.streams.get(stream_type)
        consumer_group = self.consumer_groups.get(stream_type)

        if not stream_name or not consumer_group:
            raise ValueError(f"Invalid stream configuration for: {stream_type}")

        # Use production min_idle with jitter if not specified
        if min_idle_ms is None:
            min_idle_ms = self._get_min_idle_with_jitter(self.production_min_idle_ms)

        total_reclaimed = []
        iterations = 0
        max_iterations = self.reclaim_max_iterations

        try:
            # Check XAUTOCLAIM support
            if not hasattr(self, '_supports_xautoclaim'):
                try:
                    info = self.redis_client.info()
                    ver = info.get("redis_version", "0.0.0")
                    maj, minr, *_ = [int(p) for p in ver.split(".")]
                    self._supports_xautoclaim = (maj > 6) or (maj == 6 and minr >= 2)
                    logger.debug(f"Redis {ver} - XAUTOCLAIM support: {self._supports_xautoclaim}")
                except Exception:
                    self._supports_xautoclaim = False

            if self._supports_xautoclaim:
                # Use XAUTOCLAIM with safeguards
                start_id = "0-0"
                seen_ids = set()

                while iterations < max_iterations and len(total_reclaimed) < count:
                    next_start, claimed_ids = self.xautoclaim_compat(
                        stream_name, consumer_group, min_idle_ms, start_id,
                        min(self.reclaim_batch_size, count - len(total_reclaimed))
                    )

                    if not claimed_ids:
                        break

                    # Check for progress (avoid infinite loops)
                    new_ids = [id for id in claimed_ids if id not in seen_ids]
                    if not new_ids and next_start == start_id:
                        self.reclaim_no_progress_total += 1
                        logger.debug(f"No progress in XAUTOCLAIM for {stream_name}, stopping")
                        break

                    seen_ids.update(claimed_ids)

                    # Get message data for claimed IDs
                    if new_ids:
                        try:
                            messages = self.redis_client.xrange(stream_name, min=min(new_ids), max=max(new_ids))
                            id_to_data = {msg_id: msg_data for msg_id, msg_data in messages}

                            for claimed_id in new_ids:
                                if claimed_id in id_to_data:
                                    total_reclaimed.append((claimed_id, id_to_data[claimed_id]))
                                    self.redeliveries_total += 1
                                    BusMetrics.bus_error(stream_name, 'redelivery', 'streams_consumer')

                        except Exception as e:
                            logger.error(f"Error getting message data for claimed IDs: {e}")

                    start_id = next_start
                    iterations += 1

            else:
                # Fallback to manual XPENDING + XCLAIM
                logger.debug(f"Using manual reclaim for {stream_name} (XAUTOCLAIM not available)")

                for batch_ids in self.xpending_ids_paginated(stream_name, consumer_group, min_idle_ms):
                    if not batch_ids or len(total_reclaimed) >= count:
                        break

                    try:
                        # Claim batch of IDs
                        claimed = self.redis_client.xclaim(
                            stream_name, consumer_group, self.consumer_id,
                            min_idle_time=min_idle_ms, message_ids=batch_ids
                        )

                        for msg_id, msg_data in claimed:
                            total_reclaimed.append((str(msg_id), msg_data))
                            self.redeliveries_total += 1
                            BusMetrics.bus_error(stream_name, 'redelivery', 'streams_consumer')

                        iterations += 1

                    except Exception as e:
                        logger.error(f"Error in manual XCLAIM: {e}")
                        break

            if total_reclaimed:
                self.reclaim_loops_total += 1
                logger.info(f"Reclaimed {len(total_reclaimed)} pending messages from {stream_name} (idle >= {min_idle_ms}ms)")

            return total_reclaimed

        except Exception as e:
            if "unknown command" in str(e).lower():
                self._supports_xautoclaim = False
                logger.warning(f"XAUTOCLAIM not supported, will use manual reclaim for {stream_name}")
                return []
            logger.error(f"Error consuming pending from {stream_name}: {e}")
            return []

    async def consume_with_handler(self, stream_type: str, handler: Callable[[dict], Awaitable[bool]]) -> None:
        """
        Safe consumption loop with handler-controlled ACK and scheduled reclaim
        Only ACKs messages after successful processing
        Implements the reliable consumer pattern from T1.1 with production optimizations
        """
        logger.info(f"Starting safe consumption of {stream_type} stream with scheduled reclaim (every {self.reclaim_interval}s)")

        consecutive_errors = 0
        max_consecutive_errors = 5
        pending_ids_to_ack = []  # Batch ACK buffer

        while consecutive_errors < max_consecutive_errors:
            try:
                # Scheduled reclaim: only run periodically, not every iteration
                if self._should_reclaim_now(stream_type):
                    self._mark_reclaim_time(stream_type)

                    # Batch ACK any pending successful messages first
                    if pending_ids_to_ack:
                        acked_count = self.ack_many(stream_type, pending_ids_to_ack)
                        logger.debug(f"Batch ACKed {acked_count} successful messages before reclaim")
                        pending_ids_to_ack.clear()

                    # Process pending messages with production min_idle_ms + jitter
                    pending_messages = self.consume_pending(stream_type)

                    for msg_id, msg_data in pending_messages:
                        try:
                            logger.debug(f"Processing reclaimed pending message {msg_id}")
                            success = await handler(msg_data)
                            if success:
                                pending_ids_to_ack.append(msg_id)
                                logger.debug(f"Marked reclaimed message {msg_id} for ACK")
                        except Exception as e:
                            logger.error(f"Error processing pending message {msg_id}: {e}")

                # Consume new messages (XREADGROUP with BLOCK)
                new_messages = self.consume(stream_type, count=100, block_ms=2000)

                if new_messages:
                    consecutive_errors = 0

                    for msg_id, msg_data in new_messages:
                        try:
                            success = await handler(msg_data)
                            if success:
                                pending_ids_to_ack.append(msg_id)
                                logger.debug(f"Marked new message {msg_id} for ACK")
                            else:
                                logger.warning(f"Handler failed for {msg_id}, message remains pending")
                        except Exception as e:
                            logger.error(f"Error in handler for message {msg_id}: {e}")

                # Batch ACK successful messages periodically (every 50 messages or 5 seconds)
                if len(pending_ids_to_ack) >= 50 or (
                    pending_ids_to_ack and
                    time.time() - self.last_reclaim_time.get(f"{stream_type}_ack", 0) >= 5
                ):
                    acked_count = self.ack_many(stream_type, pending_ids_to_ack)
                    logger.debug(f"Batch ACKed {acked_count} successful messages")
                    pending_ids_to_ack.clear()
                    self.last_reclaim_time[f"{stream_type}_ack"] = time.time()

                await asyncio.sleep(0.001)  # Small yield

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Stream consumption error #{consecutive_errors}: {e}")

                if consecutive_errors < max_consecutive_errors:
                    await asyncio.sleep(min(1.0 * consecutive_errors, 10.0))
                else:
                    logger.error(f"Too many consecutive errors, stopping consumption of {stream_type}")
                    break

        # Final batch ACK of any remaining successful messages
        if pending_ids_to_ack:
            acked_count = self.ack_many(stream_type, pending_ids_to_ack)
            logger.info(f"Final batch ACKed {acked_count} successful messages")

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
        """Get comprehensive statistics including T1.2 metrics"""
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

            # Get stream-specific stats with T1.2 metrics
            stream_info = {}
            for stream_type, stream_name in self.streams.items():
                try:
                    length = self.redis_client.xlen(stream_name)
                    consumer_group = self.consumer_groups[stream_type]

                    # Get consumer group info including pending count
                    group_info = {}
                    pending_count = 0
                    try:
                        groups = self.redis_client.xinfo_groups(stream_name)
                        for group in groups:
                            if group['name'] == consumer_group:
                                pending_count = group['pending']
                                group_info = {
                                    'pending': pending_count,
                                    'consumers': group['consumers'],
                                    'last_delivered_id': group['last-delivered-id']
                                }

                                # Update Prometheus metrics for T1.2
                                BusMetrics.update_pending_messages(stream_name, consumer_group, pending_count)
                                break
                    except Exception as e:
                        logger.debug(f"Error getting group info for {stream_name}: {e}")

                    # Calculate lag (approximation based on stream length vs last delivered)
                    lag = 0
                    try:
                        if group_info.get('last_delivered_id'):
                            # Simple lag calculation - this is an approximation
                            last_id = group_info['last_delivered_id']
                            if '-' in last_id:
                                last_seq = int(last_id.split('-')[1])
                                # Get latest message ID to calculate lag
                                latest_info = self.redis_client.xinfo_stream(stream_name)
                                if latest_info.get('last-generated-id'):
                                    latest_id = latest_info['last-generated-id']
                                    if '-' in latest_id:
                                        latest_seq = int(latest_id.split('-')[1])
                                        lag = max(0, latest_seq - last_seq)
                    except Exception as e:
                        logger.debug(f"Error calculating lag for {stream_name}: {e}")

                    stream_info[stream_type] = {
                        "name": stream_name,
                        "length": length,
                        "lag": lag,
                        "pending": pending_count,
                        "group_info": group_info
                    }
                except Exception as e:
                    logger.debug(f"Error getting stats for {stream_name}: {e}")
                    stream_info[stream_type] = {"name": stream_name, "length": 0, "lag": 0, "pending": 0}

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