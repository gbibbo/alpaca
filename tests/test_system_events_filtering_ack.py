import os, sys, os.path, asyncio, uuid, pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("BUS_BACKEND", "streams")

from lib.bus import connect_bus, get_bus

def _sync_redis(backend):
    try:
        return backend.redis_client
    except Exception:
        import redis
        return redis.from_url(os.environ.get("REDIS_URL","redis://localhost:6379/0"))

@pytest.mark.asyncio
async def test_filtering_and_ack_clears_pending(monkeypatch):
    connect_bus()
    bus = get_bus()
    backend = bus.backend
    r = _sync_redis(backend)

    stream = backend.streams.get("system","system")
    temp_group = f"pytest_system_{uuid.uuid4().hex[:8]}"
    original_group = backend.consumer_groups["system"]
    monkeypatch.setitem(backend.consumer_groups, "system", temp_group)

    # Empieza desde el final: solo eventos nuevos
    try:
        r.xgroup_create(stream, temp_group, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    WANT_SEED = 424242

    # Pre-publica: 1 filtrado + 2 válidos (el 2º “flushea” el ACK del 1º)
    bus.publish_system_event(
        event_type="service_start",
        source="filter-check",
        data={"foo":"bar"}
    )
    bus.publish_system_event(
        event_type="strategy_config",
        source="filter-check",
        data={"config_type":"reproducible_mode","random_seed":WANT_SEED}
    )
    bus.publish_system_event(
        event_type="strategy_config",
        source="flush",
        data={"config_type":"reproducible_mode","random_seed":-1}
    )

    agen = bus.subscribe_system_events(event_type="strategy_config")

    try:
        # 1º consumo: recibimos el “bueno” (WANT_SEED)
        evt1 = await asyncio.wait_for(agen.__anext__(), timeout=5)
        seed1 = (evt1.data or {}).get("data", {}).get("random_seed")
        assert seed1 == WANT_SEED

        # 2º consumo: entra el “flush”; al llegar aquí, el patrón safe-ack ACKea el 1º
        _ = await asyncio.wait_for(agen.__anext__(), timeout=5)

    finally:
        await agen.aclose()  # ACK final del último mensaje
        monkeypatch.setitem(backend.consumer_groups, "system", original_group)

    # Pequeña espera para que el aclose() termine el ACK final
    await asyncio.sleep(0.3)

    # No deben quedar pendientes en el grupo temporal
    pend = r.xpending_range(stream, temp_group, min="-", max="+", count=10)
    assert not pend, f"Quedaron mensajes pendientes en {temp_group}: {pend}"
