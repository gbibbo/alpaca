import os, sys, os.path, asyncio, uuid, pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("BUS_BACKEND", "streams")

from lib.bus import connect_bus, get_bus

def _get_sync_redis(backend):
    try:
        return backend.redis_client
    except Exception:
        import redis
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return redis.from_url(url)

@pytest.mark.asyncio
async def test_subscribe_system_events_with_isolated_group(monkeypatch):
    connect_bus()
    bus = get_bus()
    backend = bus.backend

    stream = backend.streams.get("system", "system")
    temp_group = f"pytest_system_{uuid.uuid4().hex[:8]}"
    original_group = backend.consumer_groups["system"]
    monkeypatch.setitem(backend.consumer_groups, "system", temp_group)

    r = _get_sync_redis(backend)
    try:
        r.xgroup_create(stream, temp_group, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    WANT_SEED = 20240926

    agen = bus.subscribe_system_events(event_type="strategy_config")

    async def consume():
        evt = await asyncio.wait_for(agen.__anext__(), timeout=5)
        got_seed = (evt.data or {}).get("data", {}).get("random_seed")
        return evt.event_type, got_seed

    async def publish():
        await asyncio.sleep(0.2)
        bus.publish_system_event(
            event_type="strategy_config",
            source="pytest-isolated",
            data={"config_type": "reproducible_mode", "random_seed": WANT_SEED},
        )

    try:
        (ev_type, got_seed), _ = await asyncio.gather(consume(), publish())
        assert ev_type == "strategy_config"
        assert got_seed == WANT_SEED
    finally:
        try:
            await agen.aclose()  # evita “Task was destroyed but it is pending!”
        finally:
            monkeypatch.setitem(backend.consumer_groups, "system", original_group)
