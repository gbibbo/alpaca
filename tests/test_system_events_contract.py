import os, sys, os.path, asyncio, json, time, uuid
import pytest
from datetime import datetime, timezone

# Asegura que 'lib/' sea importable ejecutando pytest desde la raíz
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("BUS_BACKEND", "streams")

from lib.bus import connect_bus, get_bus
from lib.time_utils import TimeUtils

def _b2s(x):
    return x.decode() if isinstance(x, (bytes, bytearray)) else x

@pytest.mark.asyncio
async def test_backend_exposes_subscribe_system_events():
    connect_bus()
    backend = get_bus().backend
    assert hasattr(backend, "subscribe_system_events"), \
        "RedisStreamsBus debe implementar subscribe_system_events()"

@pytest.mark.asyncio
async def test_bus_subscribe_system_events_consumes_one_event_isolated(monkeypatch):
    """
    Igual que antes pero aislando el consumer group para evitar mensajes viejos.
    """
    connect_bus()
    bus = get_bus()
    backend = bus.backend

    stream = backend.streams.get("system", "system")
    temp_group = f"pytest_system_{uuid.uuid4().hex[:8]}"
    original_group = backend.consumer_groups["system"]
    monkeypatch.setitem(backend.consumer_groups, "system", temp_group)

    # crea el grupo en '$'
    try:
        # mejor vía cliente sync directo si está expuesto; si no, crea otro
        try:
            r = backend.redis_client
        except Exception:
            import redis
            r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        r.xgroup_create(stream, temp_group, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    WANT_SEED = 20240926

    async def consume_one():
        agen = bus.subscribe_system_events(event_type="strategy_config")
        evt = await asyncio.wait_for(agen.__anext__(), timeout=5)
        seed = (evt.data or {}).get("data", {}).get("random_seed")
        return evt.event_type, seed

    async def publish():
        await asyncio.sleep(0.2)
        bus.publish_system_event(
            event_type="strategy_config",
            source="contract-isolated",
            data={"config_type":"reproducible_mode","random_seed":WANT_SEED}
        )

    try:
        (ev_type, got_seed), _ = await asyncio.gather(consume_one(), publish())
        assert ev_type == "strategy_config"
        assert got_seed == WANT_SEED
    finally:
        monkeypatch.setitem(backend.consumer_groups, "system", original_group)

@pytest.mark.asyncio
async def test_publish_and_consume_system_with_xreadgroup():
    """
    Prueba de vida del stream 'system' sin usar subscribe_system_events:
      1) publish_system_event escribe en 'system'
      2) XREADGROUP con ID '>' recupera SOLO mensajes nuevos
    """
    connect_bus()
    bus = get_bus()

    # Cliente redis sync
    try:
        r = bus.backend.redis_client
    except Exception:
        import redis
        r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))

    stream = bus.backend.streams.get("system", "system")
    group = f"pytest_system_{uuid.uuid4().hex[:8]}"
    consumer = f"pytest_consumer_{uuid.uuid4().hex[:6]}"

    # Crea grupo en '$'
    try:
        r.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    WANT_SEED = 20240926

    # Publica
    bus.publish_system_event(
        event_type="strategy_config",
        source="xreadgroup-test",
        data={"config_type":"reproducible_mode","random_seed":WANT_SEED}
    )

    # Lee solo mensajes nuevos
    msgs = r.xreadgroup(groupname=group, consumername=consumer,
                        streams={stream: ">"}, count=1, block=3000)
    assert msgs, "No se recibió ningún evento nuevo del stream 'system'"

    # Extrae campos
    _, entries = msgs[0]
    msg_id, fields = entries[0]
    fields = { _b2s(k): _b2s(v) for k,v in dict(fields).items() }

    # payload principal está en 'data'
    payload = json.loads(fields.get("data","{}")) if "data" in fields else {}
    assert payload.get("event_type") == "strategy_config"
    assert payload.get("data", {}).get("random_seed") == WANT_SEED
