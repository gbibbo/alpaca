#!/usr/bin/env python3
"""
tests/test_streams_recovery.py
RedisStreamsBus._subscribe_model must recover a consumer's own un-acked backlog after a restart
(phase 1) and must not redeliver a poison message forever. Needs a real Redis (skipped otherwise).
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from lib.models import Signal, SignalSide

pytestmark = pytest.mark.asyncio


def _make_bus():
    import redis as _redis
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        _redis.Redis.from_url(url, socket_connect_timeout=1).ping()
    except Exception:
        pytest.skip("real Redis not available")
    from lib.bus_streams import RedisStreamsBus
    bus = RedisStreamsBus(url)
    bus.connect()
    # Isolate on unique stream + group names so the test never touches shared data.
    tag = uuid.uuid4().hex[:8]
    bus.streams["signals"] = f"test:signals:{tag}"
    bus.consumer_groups["signals"] = f"test:grp:{tag}"
    bus.consumer_id = f"consumer-{tag}"
    return bus


def _signal(sym="AAPL"):
    return Signal(symbol=sym, side=SignalSide.BUY, confidence=0.7, source="test", price=100)


async def _anext(gen, timeout=5):
    return await asyncio.wait_for(gen.__anext__(), timeout=timeout)


async def test_recovers_unacked_backlog_after_restart():
    bus = _make_bus()
    stream = bus.streams["signals"]
    group = bus.consumer_groups["signals"]
    try:
        # Group must exist before publishing (a consumer group starts at '$' = new messages only).
        await bus._ensure_group(stream, group)
        bus.publish_signal(_signal())
        bus.publish_signal(_signal())

        # Read one, then drop the generator WITHOUT acking (simulates a crash): both delivered
        # entries stay in this consumer's pending list.
        gen = bus.subscribe_signals()
        first = await _anext(gen)
        assert first.symbol == "AAPL"
        await gen.aclose()

        pending_before = bus.redis_client.xpending(stream, group)["pending"]
        assert pending_before >= 1

        # A new generator with the SAME consumer id replays the pending backlog first (phase 1).
        gen2 = bus.subscribe_signals()
        recovered = [await _anext(gen2) for _ in range(pending_before)]
        await gen2.aclose()
        assert len(recovered) == pending_before
        assert all(s.source == "test" for s in recovered)
    finally:
        bus.redis_client.delete(stream)


async def test_poison_message_is_dropped_not_looped():
    bus = _make_bus()
    stream = bus.streams["signals"]
    group = bus.consumer_groups["signals"]
    try:
        await bus._ensure_group(stream, group)
        bus.redis_client.xadd(stream, {"type": "signal", "data": "{not valid json"})
        bus.publish_signal(_signal("MSFT"))

        gen = bus.subscribe_signals()
        got = await _anext(gen)              # poison is skipped+acked; the valid one comes through
        assert got.symbol == "MSFT"
        # Requesting the next item resumes the generator past the ACK of MSFT, then blocks (no
        # more messages) -> by the timeout both poison and MSFT are acknowledged.
        with pytest.raises(asyncio.TimeoutError):
            await _anext(gen, timeout=3)
        await gen.aclose()
        assert bus.redis_client.xpending(stream, group)["pending"] == 0
    finally:
        bus.redis_client.delete(stream)
